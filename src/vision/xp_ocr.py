"""경험치 영역 OCR + 시간당 예상 경험치 추정.

HealerWorker에서 xp_region 지정 시에만 활성. CooldownOcr처럼 submit_frame +
백그라운드 스레드 구조. XP 숫자 인식 = digit_cnn 전용(좌표 CNN 재사용).
torch/paddle 의존 0.

추정 모델:
  session_start_ts 고정. 매 OCR마다 xp 값 읽고, 감소하면 "레벨업" 간주해
  _total_delta += (last - anchor), anchor = xp. 증가는 자연스럽게 계속.
  xp_per_hour = (total_delta + (last - anchor)) / elapsed * 3600.
  elapsed < 10s 이면 0 (초기 노이즈 회피).
"""
from __future__ import annotations

import logging
import pathlib
import re
import threading
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np


def fmt_per_hour(n: int) -> str:
    """시간당 경험치 정수 → 'xx.x억' 문자열. 0 이하 '-'."""
    if n is None or n <= 0:
        return "-"
    return f"{n / 100_000_000.0:.1f}억"


class XpOcr:
    """xp_region 기반 주기 OCR → 시간당 예상 경험치."""

    def __init__(self, poll_sec: float = 2.0, log_cb=None):
        self.poll_sec = max(0.5, float(poll_sec))
        self._region: Optional[Tuple[int, int, int, int]] = None
        # 숫자 CNN (2026-06-10 v16): XP도 green_mask 전처리 적용 시 세그 깔끔
        # (raw 14박스 노이즈 → green전처리 11박스 정확, 실측 확인). 좌표
        # digit_cnn 재사용 — XP 자릿수 삽입/오독 해결. fallback 없음(전용).
        self._digit_cnn = None
        try:
            from .digit_cnn import DigitCnn
            _cnn_p = pathlib.Path(__file__).resolve().parent / "digit_cnn.onnx"
            self._digit_cnn = DigitCnn(str(_cnn_p))
        except Exception:
            self._digit_cnn = None
        self._lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_origin: Tuple[int, int] = (0, 0)
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._started = False
        # 추정 상태.
        self._session_start_ts: float = 0.0
        self._anchor_xp: int = -1
        self._last_xp: int = -1
        self._total_delta: int = 0
        self._last_ocr_ts: float = 0.0
        self._last_xp_per_hour: int = 0
        self._init_note: str = ""
        self._last_diag: str = ""
        self.log = logging.getLogger("xp_ocr")
        # 외부 로그 콜백 — Attacker 로거로 OCR 상태 진단 송출.
        self._log_cb = log_cb
        self._emitted_first_ok: bool = False
        self._consecutive_fail: int = 0
        # 2026-04-23 개정: OCR 스파이크 방어.
        # 옛바 XP 최대 999억 (11자리). 1000억(10^11) 이상 = 반드시 OCR 오탐.
        # XP_HARD_CAP = 100_000_000_000 (이 값 포함 이상 리젝트).
        # 점프 필터: 2배 초과 증가 = OCR 앞자리 삽입 튐(예: 23억→231억).
        # Shallow drop: 감소인데 new > last/2 = 레벨업 아님, OCR 글리치.
        # 첫 anchor: 연속 2회 일관(±10%) 확인 후 생성. 단발 스파이크 poisoning 방지.
        # 연속 리젝트 5회 시 anchor 재설정 (세션 오염 자가복구).
        self._consecutive_reject: int = 0
        self._last_raw_log_ts: float = 0.0
        # anchor pending: 첫 읽기 → 바로 anchor 삼지 않고, 다음 읽기와 ±10% 일치해야 수용.
        self._pending_first_xp: int = -1

    def set_region(self, x: int, y: int, w: int, h: int) -> None:
        if w <= 0 or h <= 0 or x < 0 or y < 0:
            with self._lock:
                self._region = None
            return
        with self._lock:
            self._region = (int(x), int(y), int(w), int(h))

    def clear_region(self) -> None:
        with self._lock:
            self._region = None

    def ready(self) -> bool:
        with self._lock:
            return self._region is not None

    def submit_frame(self, frame: np.ndarray,
                     origin: Tuple[int, int]) -> None:
        if frame is None:
            return
        with self._lock:
            self._latest_frame = frame
            self._latest_origin = (int(origin[0]), int(origin[1]))
        if not self._started:
            self._started = True
            self._thread = threading.Thread(
                target=self._loop, name="xp_ocr", daemon=True
            )
            self._stop_evt.clear()
            self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=1.0)
        self._thread = None
        self._started = False

    def xp_per_hour(self) -> int:
        return int(self._last_xp_per_hour)

    def reset(self) -> None:
        """세션 재시작 (예: 영역 바꾸거나 사용자 요청)."""
        self._session_start_ts = 0.0
        self._anchor_xp = -1
        self._last_xp = -1
        self._total_delta = 0
        self._last_xp_per_hour = 0
        self._pending_first_xp = -1
        self._consecutive_reject = 0

    def test_once(self, frame: np.ndarray,
                  origin: Tuple[int, int],
                  save_debug: bool = True) -> dict:
        """동기 일회성 OCR. 상태 변이 없이 결과만 반환.

        UI "OCR 확인" 버튼용. 내부 추정치(_anchor_xp, _total_delta 등)를 건드리지
        않음. region은 사전 set_region으로 지정돼 있어야 함.
        save_debug=True면 crop/upscale PNG를 logs/xp_ocr_debug/*.png 에 저장.
        반환 dict: {ok, xp, raw_text, diag, crop_shape, debug_path}
        """
        if self._digit_cnn is None or not self._digit_cnn.ready():
            return {
                "ok": False, "xp": None, "raw_text": "",
                "diag": "digit_cnn unavailable",
                "crop_shape": None, "debug_path": "",
            }
        if self._region is None:
            return {
                "ok": False, "xp": None, "raw_text": "",
                "diag": "no region set", "crop_shape": None,
                "debug_path": "",
            }
        rx, ry, rw, rh = self._region
        ml, mt = int(origin[0]), int(origin[1])
        fx = max(0, rx - ml)
        fy = max(0, ry - mt)
        fw = min(rw, frame.shape[1] - fx)
        fh = min(rh, frame.shape[0] - fy)
        if fw < 10 or fh < 8:
            return {
                "ok": False, "xp": None, "raw_text": "",
                "diag": f"crop too small fw={fw} fh={fh}",
                "crop_shape": (fh, fw), "debug_path": "",
            }
        crop = frame[fy:fy + fh, fx:fx + fw]
        up = cv2.resize(
            crop, (crop.shape[1] * 3, crop.shape[0] * 3),
            interpolation=cv2.INTER_CUBIC,
        )
        debug_path = ""
        if save_debug:
            try:
                dbg_dir = pathlib.Path.cwd() / "logs" / "xp_ocr_debug"
                dbg_dir.mkdir(parents=True, exist_ok=True)
                ts = time.strftime("%Y%m%d_%H%M%S")
                crop_p = dbg_dir / f"xp_{ts}_crop.png"
                up_p = dbg_dir / f"xp_{ts}_up3x.png"
                cv2.imwrite(str(crop_p), crop)
                cv2.imwrite(str(up_p), up)
                debug_path = str(crop_p)
            except Exception as e:
                debug_path = f"(save fail: {e})"
        from .hpmp import _preprocess_for_ocr
        text = self._cnn_digits(_preprocess_for_ocr(crop, upscale=3, thr=20))
        xp = self._parse_int(text)
        if xp is None:
            return {
                "ok": False, "xp": None, "raw_text": text,
                "diag": "no digits",
                "crop_shape": crop.shape[:2],
                "debug_path": debug_path,
            }
        return {
            "ok": True, "xp": xp, "raw_text": text,
            "diag": "", "crop_shape": crop.shape[:2],
            "debug_path": debug_path,
        }

    # ---------------------------------------------------------------
    def _emit(self, msg: str) -> None:
        cb = self._log_cb
        if cb is None:
            return
        try:
            cb(str(msg))
        except Exception:
            pass

    def _ensure_rec(self) -> None:
        # XP 숫자 = digit_cnn 전용 (PaddleOCR/RapidOCR fallback 없음).
        self._init_note = "digit_cnn only"

    def _loop(self) -> None:
        try:
            self._ensure_rec()
        except Exception as e:
            self._last_diag = f"init err: {e}"
        while not self._stop_evt.wait(self.poll_sec):
            try:
                self._tick()
            except Exception as e:
                self._last_diag = f"tick err: {e}"

    def _tick(self) -> None:
        if self._digit_cnn is None or not self._digit_cnn.ready():
            self._last_diag = "no digit_cnn"
            return
        with self._lock:
            if self._region is None or self._latest_frame is None:
                return
            rx, ry, rw, rh = self._region
            ml, mt = self._latest_origin
            frame = self._latest_frame
        # region을 frame 좌표계로.
        fx = max(0, rx - ml)
        fy = max(0, ry - mt)
        fw = min(rw, frame.shape[1] - fx)
        fh = min(rh, frame.shape[0] - fy)
        if fw < 10 or fh < 8:
            self._last_diag = "crop too small"
            self._consecutive_fail += 1
            if self._consecutive_fail in (1, 5, 20):
                self._emit(f"[XP-OCR] crop too small fw={fw} fh={fh} "
                           f"region={self._region} origin=({ml},{mt}) "
                           f"frame={frame.shape[1]}x{frame.shape[0]}")
            return
        crop = frame[fy:fy + fh, fx:fx + fw]
        # CNN용 green_mask 전처리(초록 글씨만 → 세그 깔끔). HP/MP와 동일 기법.
        from .hpmp import _preprocess_for_ocr
        up = _preprocess_for_ocr(crop, upscale=3, thr=20)
        # 숫자 CNN 세그+분류 전용 (fallback 없음).
        text = self._cnn_digits(up)
        xp = self._parse_int(text)
        # 숫자 CNN 학습 데이터 수집 (env OB_COLLECT_DIGITS=1 일 때만).
        # 파싱 성공한 text 의 숫자열을 라벨 후보로 (박스수 일치 시만 채택).
        try:
            from . import digit_collect
            if digit_collect.enabled():
                digit_collect.collect("xp", up, text if xp is not None else "")
        except Exception:
            pass
        now_mono = time.monotonic()
        # 2026-04-23: throttled raw 로그 (5초당 1회) — 스파이크 패턴 추적용.
        raw_log_due = (now_mono - self._last_raw_log_ts) >= 5.0
        if xp is None:
            self._last_diag = f"no digits text={text!r}"
            self._consecutive_fail += 1
            if self._consecutive_fail in (1, 5, 20) or raw_log_due:
                self._emit(f"[XP-OCR-RAW] REJECT parse text={text!r} "
                           f"(no-digits-or-over-cap)")
                self._last_raw_log_ts = now_mono
            return
        # 2026-04-23 개정: 점프 필터 강화.
        # - 2배 초과 증가 = OCR 앞자리 삽입 튐 (23억→231억 등).
        # - 감소인데 new > last/2 = 레벨업 아님, OCR 글리치 (레벨업은 거의 0).
        reject_reason = ""
        if self._last_xp > 0:
            if xp > self._last_xp * 2:
                reject_reason = f"jump_up x{xp / self._last_xp:.2f} last={self._last_xp} new={xp}"
            elif xp < self._last_xp and xp * 2 > self._last_xp:
                # 감소인데 새 값이 이전의 절반보다 큼 → OCR 글리치 (레벨업 아님).
                reject_reason = f"shallow_drop last={self._last_xp} new={xp}"
        if reject_reason:
            self._consecutive_reject += 1
            self._last_diag = f"reject {reject_reason}"
            if raw_log_due or self._consecutive_reject in (1, 3, 10):
                self._emit(f"[XP-OCR-RAW] REJECT {reject_reason} "
                           f"text={text!r} cf={self._consecutive_reject}")
                self._last_raw_log_ts = now_mono
            # 연속 5회 리젝트 → anchor 전체 리셋 (pending 까지 날려 재수집 시작).
            # 즉시 anchor 로 삼지 않음 — 2회 일관 확인 로직 다시 통과해야 함.
            if self._consecutive_reject >= 5:
                self._emit(f"[XP-OCR] 연속 리젝트 5회 — anchor 리셋, 재수집 시작")
                self._anchor_xp = -1
                self._last_xp = -1
                self._total_delta = 0
                self._session_start_ts = 0.0
                self._last_xp_per_hour = 0
                self._pending_first_xp = -1
                self._consecutive_reject = 0
            return
        self._consecutive_reject = 0
        self._ingest_xp(xp)
        self._last_diag = f"xp={xp} per_hour={self._last_xp_per_hour}"
        self._consecutive_fail = 0
        if not self._emitted_first_ok:
            self._emitted_first_ok = True
            self._emit(f"[XP-OCR] 첫 인식 성공 xp={xp} text={text!r}")
        elif raw_log_due:
            self._emit(f"[XP-OCR-RAW] ok xp={xp} text={text!r} "
                       f"per_hour={self._last_xp_per_hour}")
            self._last_raw_log_ts = now_mono

    def _cnn_digits(self, up) -> str:
        """전처리 이미지 → 숫자 CNN 세그+분류 → 숫자열. 미사용/실패시 ''."""
        if self._digit_cnn is None or not self._digit_cnn.ready():
            return ""
        try:
            from .ocr import _segment_digit_boxes
            boxes = _segment_digit_boxes(up)
            if not boxes:
                return ""
            patches = [up[y:y + h, x:x + w] for (x, y, w, h) in boxes]
            labels = self._digit_cnn.predict(patches)
            return "".join(str(d) for d in labels)
        except Exception:
            return ""

    # 옛바 XP 상한: "100억 단위까지" = 최대 999억 (11자리).
    # 1000억 = 10^11 이 cap — 이 값 포함 이상은 무조건 OCR 오탐.
    XP_HARD_CAP = 100_000_000_000

    @classmethod
    def _parse_int(cls, text: str) -> Optional[int]:
        if not text:
            return None
        # 콤마/공백 제거 후 최장 숫자 추출.
        digits = re.sub(r"[^0-9]", "", text)
        if not digits:
            return None
        try:
            n = int(digits)
        except Exception:
            return None
        if n <= 0:
            return None
        # 2026-04-23: 자릿수 하드캡. 13자리 이상 무조건 리젝트.
        if n >= cls.XP_HARD_CAP:
            return None
        return n

    def _ingest_xp(self, xp: int) -> None:
        now = time.monotonic()
        if self._anchor_xp < 0:
            # 2026-04-23: 첫 anchor 는 연속 2회 일관(±10%) 확인 후 생성.
            # 단발 스파이크(예: 231억) 가 anchor 로 잡혀 세션 전체 오염되는 것 방지.
            if self._pending_first_xp < 0:
                self._pending_first_xp = xp
                self._emit(f"[XP-OCR] anchor pending first={xp}")
                return
            prev = self._pending_first_xp
            # ±10% 이내면 수용, 아니면 새 값으로 pending 교체 (항상 최근 2회 사용).
            tol = max(1, prev // 10)
            if abs(xp - prev) <= tol:
                self._anchor_xp = xp
                self._last_xp = xp
                self._session_start_ts = now
                self._last_ocr_ts = now
                self._pending_first_xp = -1
                self._emit(f"[XP-OCR] anchor 확정 xp={xp} (prev={prev})")
                return
            self._emit(
                f"[XP-OCR] anchor pending mismatch prev={prev} new={xp} "
                f"(±{tol} 허용) — 새 값으로 대기"
            )
            self._pending_first_xp = xp
            return
        # 감소 → 레벨업 가정: delta 누산 후 anchor 갱신.
        if xp < self._anchor_xp:
            gained = max(0, self._last_xp - self._anchor_xp)
            self._total_delta += gained
            self._anchor_xp = xp
        self._last_xp = xp
        self._last_ocr_ts = now
        elapsed = now - self._session_start_ts
        if elapsed < 10.0:
            self._last_xp_per_hour = 0
            return
        cur_delta = self._total_delta + max(0, xp - self._anchor_xp)
        self._last_xp_per_hour = int(cur_delta / elapsed * 3600.0)
