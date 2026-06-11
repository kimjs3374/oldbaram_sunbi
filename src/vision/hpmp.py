"""HP/MP 숫자 OCR 리더 (2026-04-20 재작성 v2).

이전 HSV 픽셀 비율 방식은 초록 글씨가 겹쳐 있는 옛바 게이지에서 오인식 →
사용자 지시로 OCR 기반으로 전환.

사용법:
- `set_hp_region/set_mp_region(x,y,w,h)` : 절대 화면 좌표 (숫자 표시 영역).
- `set_hp_max/set_mp_max(n)` : 사용자가 입력한 최대값. pct 환산에 사용.
- `read(frame, origin)` : 매 프레임 호출 — 내부 큐에 submit. 실제 OCR 은
  백그라운드 스레드가 `poll_sec` 주기로 수행. 즉시 반환은 `latest()` 캐시.
- `latest()` : 가장 최근 OCR 결과 (HpMp).
- `test_once(frame, origin)` : 테스트 버튼용 동기 1회 OCR. 원시 텍스트/현재값/
  max/pct 를 dict 로 반환.

인식: 숫자 digit_cnn(onnx) **전용** (좌표/XP 와 동일 모델). fallback 없음.
torch/paddle/rapidocr 의존 0.

전처리:
- G 채널 - max(R, B) > thr 마스크로 초록 글씨 픽셀만 추출.
- 반전(글씨=검정, 배경=흰색) + 3x 업스케일.
- 숫자만 추출 후 max 값 정보로 '현재/최대' 분리:
    raw 가 str(max) 로 끝나면 앞부분 = current. 아니면 raw 전체 = current.
- pct = round(current * 100 / max). max ≤ 0 이면 pct = -1.
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


@dataclass
class HpMp:
    """HP/MP 상태. -1 = 미관측.

    hp/mp : 0~100 정수 % (환산치). 자힐/공력증강/부활 predicate 에서 사용.
    hp_cur/mp_cur : 최근 OCR 원시 현재값 (max 모를 때도 사용 가능).
    hp_max/mp_max : set_*_max 로 주입된 사용자 입력값 (없으면 0).
    """
    hp: int = -1
    mp: int = -1
    hp_cur: int = -1
    mp_cur: int = -1
    hp_max: int = 0
    mp_max: int = 0


def _green_mask(bgr: np.ndarray, thr: int = 20) -> np.ndarray:
    """초록 글씨 픽셀 마스크. G - max(R,B) > thr 면 글씨로 간주."""
    if bgr is None or bgr.size == 0:
        return np.zeros((1, 1), dtype=np.uint8)
    b = bgr[:, :, 0].astype(np.int16)
    g = bgr[:, :, 1].astype(np.int16)
    r = bgr[:, :, 2].astype(np.int16)
    rb = np.maximum(r, b)
    diff = g - rb
    mask = (diff > int(thr)).astype(np.uint8) * 255
    return mask


def _preprocess_for_ocr(crop: np.ndarray,
                        upscale: int = 3,
                        thr: int = 20) -> np.ndarray:
    """초록 글씨 crop → OCR 친화 이진 이미지.

    1) 초록 마스크 (글씨=255, 배경=0)
    2) 반전 (글씨=0 검정, 배경=255 흰)
    3) 3x 업스케일 (INTER_NEAREST 로 얇은 글씨 보존)
    4) BGR 3채널 복원 (OCR 입력 형식).
    """
    if crop is None or crop.size == 0:
        return crop
    mask = _green_mask(crop, thr=thr)
    inv = 255 - mask
    if upscale > 1:
        inv = cv2.resize(
            inv,
            (inv.shape[1] * upscale, inv.shape[0] * upscale),
            interpolation=cv2.INTER_NEAREST,
        )
    bgr3 = cv2.cvtColor(inv, cv2.COLOR_GRAY2BGR)
    return bgr3


def _parse_digits(text: str) -> Optional[int]:
    """텍스트 → 숫자만 추출해 int. 0 포함 OK (부활 시 HP=0)."""
    if not text:
        return None
    digits = re.sub(r"[^0-9]", "", text)
    if not digits:
        return None
    try:
        return int(digits)
    except Exception:
        return None


def _split_cur_max(raw_digits: str, max_val: int) -> Optional[int]:
    """OCR 결합 문자열 'cur+max' 에서 current 분리.

    - max_val <= 0: raw 전체를 current 로.
    - raw 가 str(max_val) 로 끝나면 앞부분 = current.
    - 실패 시 raw 전체를 current (최후 폴백).

    "603541" + max=541 → "603" → 603
    "956466" + max=466 → "956" → 956
    "0100" + max=100 → "0" → 0 (사망)
    """
    if not raw_digits:
        return None
    if max_val <= 0:
        try:
            return int(raw_digits)
        except Exception:
            return None
    mx = str(int(max_val))
    if raw_digits.endswith(mx) and len(raw_digits) > len(mx):
        cur_s = raw_digits[: -len(mx)]
        try:
            return int(cur_s)
        except Exception:
            pass
    # 폴백: 전체를 current 로.
    try:
        return int(raw_digits)
    except Exception:
        return None


class HpMpReader:
    """HP/MP 숫자 OCR 리더. 백그라운드 스레드로 주기 OCR.

    read(frame, origin) 는 매 프레임 호출되지만 실제 OCR 은 poll_sec 마다만
    동작 (비용 ↓). 스킬 스케줄러는 latest() 캐시만 읽으면 충분.
    """

    def __init__(self, poll_sec: float = 0.5, log_cb=None,
                 on_update=None):
        self.poll_sec = max(0.1, float(poll_sec))
        self._hp_region: Optional[Tuple[int, int, int, int]] = None
        self._mp_region: Optional[Tuple[int, int, int, int]] = None
        self._hp_max: int = 0
        self._mp_max: int = 0
        # 숫자 CNN (2026-06-10): 좌표 digit_cnn.onnx 재사용. 실측 HP/MP 99%
        # (자릿수 손실 없음 — 자릿수 누락/삽입 오독 해결). 세그+분류.
        # XP/좌표와 동일하게 digit_cnn **전용** (RapidOCR fallback 제거 2026-06-11).
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
        # 최근 결과 캐시.
        self._last_hp_cur: int = -1
        self._last_mp_cur: int = -1
        self._last_hp_pct: int = -1
        self._last_mp_pct: int = -1
        self._init_note: str = ""
        self._log_cb = log_cb
        self.log = logging.getLogger("hpmp_ocr")
        self._emitted_first_hp: bool = False
        self._emitted_first_mp: bool = False
        # 2026-04-24 공증(공력증강) 시전 시 HP 60% 감소 정당 → 급감 필터 우회
        # allow_hp_drop_until 이전 시각까지 filter 건너뜀.
        self._allow_hp_drop_until: float = 0.0
        self._hp_pending_sus: Optional[int] = None

    def allow_hp_drop_for(self, seconds: float) -> None:
        """공증 시전 직후 등 정당한 HP 급감 예상 시점 호출.
        해당 시간 동안 HP OCR 급감 필터(50% reject)를 우회.
        """
        import time as _t
        self._allow_hp_drop_until = _t.time() + max(0.1, float(seconds))
        # 2026-04-20 Patch 2.15: "값 변할 때마다" 추적 로그용. OCR 이
        # 실제 체력 변화를 따라가는지 확인. 스팸 방지 위해 cur 또는 pct 값이
        # 이전과 다를 때만 한 줄.
        self._prev_logged_hp_cur: int = -999
        self._prev_logged_mp_cur: int = -999
        # _tick 이 HP/MP 값을 새로 갱신하면 호출되는 콜백 (예: scheduler
        # 즉시 깨우기). 예외는 무시. 외부에서 set_on_update 로 늦게 붙여도 됨.
        self._on_update = on_update

    def set_on_update(self, cb) -> None:
        self._on_update = cb

    # ---- 영역 / max API ----
    def set_hp_region(self, x: int, y: int, w: int, h: int) -> None:
        if w <= 0 or h <= 0 or x < 0 or y < 0:
            with self._lock:
                self._hp_region = None
            return
        with self._lock:
            self._hp_region = (int(x), int(y), int(w), int(h))

    def clear_hp_region(self) -> None:
        with self._lock:
            self._hp_region = None
            self._last_hp_cur = -1
            self._last_hp_pct = -1

    def set_mp_region(self, x: int, y: int, w: int, h: int) -> None:
        if w <= 0 or h <= 0 or x < 0 or y < 0:
            with self._lock:
                self._mp_region = None
            return
        with self._lock:
            self._mp_region = (int(x), int(y), int(w), int(h))

    def clear_mp_region(self) -> None:
        with self._lock:
            self._mp_region = None
            self._last_mp_cur = -1
            self._last_mp_pct = -1

    def set_hp_max(self, n: int) -> None:
        with self._lock:
            self._hp_max = max(0, int(n))

    def set_mp_max(self, n: int) -> None:
        with self._lock:
            self._mp_max = max(0, int(n))

    def has_hp_region(self) -> bool:
        with self._lock:
            return self._hp_region is not None

    def has_mp_region(self) -> bool:
        with self._lock:
            return self._mp_region is not None

    # ---- 프레임 제출 / 결과 ----
    def read(self, frame: np.ndarray,
             origin: Tuple[int, int] = (0, 0)) -> HpMp:
        """매 프레임 호출. 내부 큐에 submit + latest() 반환.

        실제 OCR 은 백그라운드 스레드가 수행. 호출측(main 루프)은 비용 걱정 X.
        """
        if frame is not None:
            with self._lock:
                self._latest_frame = frame
                self._latest_origin = (int(origin[0]), int(origin[1]))
            if not self._started:
                self._started = True
                self._thread = threading.Thread(
                    target=self._loop, name="hpmp_ocr", daemon=True
                )
                self._stop_evt.clear()
                self._thread.start()
        return self.latest()

    def latest(self) -> HpMp:
        with self._lock:
            return HpMp(
                hp=self._last_hp_pct,
                mp=self._last_mp_pct,
                hp_cur=self._last_hp_cur,
                mp_cur=self._last_mp_cur,
                hp_max=self._hp_max,
                mp_max=self._mp_max,
            )

    def stop(self) -> None:
        self._stop_evt.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=1.0)
        self._thread = None
        self._started = False

    # ---- 테스트 / 디버그 ----
    def test_once(self, frame: np.ndarray,
                  origin: Tuple[int, int] = (0, 0),
                  save_debug: bool = True) -> dict:
        """동기 1회 OCR. UI '체력/마력 확인' 버튼용.

        반환: {
          "hp": {"ok": bool, "cur": int, "max": int, "pct": int,
                 "raw": str, "region": (x,y,w,h) | None, "diag": str,
                 "debug_path": str},
          "mp": {동일 구조}
        }
        """
        self._ensure_rec()
        out = {"hp": self._test_kind(frame, origin, "hp", save_debug),
               "mp": self._test_kind(frame, origin, "mp", save_debug)}
        return out

    def _test_kind(self, frame, origin, kind: str, save_debug: bool) -> dict:
        with self._lock:
            region = self._hp_region if kind == "hp" else self._mp_region
            max_val = self._hp_max if kind == "hp" else self._mp_max
        base = {"ok": False, "cur": -1, "max": int(max_val), "pct": -1,
                "raw": "", "region": region, "diag": "", "debug_path": ""}
        if self._digit_cnn is None or not self._digit_cnn.ready():
            base["diag"] = f"digit_cnn 미가용: {self._init_note}"
            return base
        if region is None:
            base["diag"] = "영역 미지정"
            return base
        # frame + origin + region 으로 계산되는 crop 박스를 항상 함께 리턴.
        # crop 실패 원인을 숫자로 사용자에게 보여줌.
        try:
            H, W = frame.shape[:2]
        except Exception:
            H, W = -1, -1
        ox, oy = int(origin[0]), int(origin[1])
        ax, ay, aw, ah = region
        lx = int(ax) - ox
        ly = int(ay) - oy
        x1 = max(0, lx); y1 = max(0, ly)
        x2 = min(W, lx + int(aw)) if W > 0 else lx + int(aw)
        y2 = min(H, ly + int(ah)) if H > 0 else ly + int(ah)
        geom_txt = (
            f"frame={W}x{H} origin=({ox},{oy}) "
            f"region=abs({ax},{ay})+{aw}x{ah} "
            f"→ local({lx},{ly})+{aw}x{ah} "
            f"→ crop=({x1},{y1})-({x2},{y2})"
        )
        crop = self._crop_abs(frame, region, origin)
        if crop is None or crop.size == 0:
            base["diag"] = f"crop 실패 [{geom_txt}]"
            return base
        up = _preprocess_for_ocr(crop, upscale=3, thr=20)
        debug_path = ""
        if save_debug:
            try:
                dbg_dir = pathlib.Path.cwd() / "logs" / "hpmp_ocr_debug"
                dbg_dir.mkdir(parents=True, exist_ok=True)
                ts = time.strftime("%Y%m%d_%H%M%S")
                crop_p = dbg_dir / f"{kind}_{ts}_crop.png"
                up_p = dbg_dir / f"{kind}_{ts}_up.png"
                cv2.imwrite(str(crop_p), crop)
                cv2.imwrite(str(up_p), up)
                debug_path = str(crop_p)
            except Exception as e:
                debug_path = f"(save fail: {e})"
        base["debug_path"] = debug_path
        # digit_cnn 세그+분류 전용 (RapidOCR 제거).
        digits = self._cnn_digits(up)
        base["raw"] = digits
        if not digits:
            base["diag"] = "숫자 없음"
            return base
        cur = _split_cur_max(digits, int(max_val))
        if cur is None:
            base["diag"] = f"파싱 실패 raw={digits!r}"
            return base
        base["cur"] = int(cur)
        base["ok"] = True
        if int(max_val) > 0:
            pct = int(round(cur * 100.0 / float(max_val)))
            base["pct"] = max(0, min(100, pct))
        return base

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
        # HP/MP 숫자 = digit_cnn 전용 (좌표/XP 와 동일 모델). RapidOCR/Paddle 없음.
        if self._digit_cnn is not None and self._digit_cnn.ready():
            self._init_note = "digit_cnn only"
        else:
            self._init_note = "digit_cnn unavailable"
        self._emit(f"[HPMP-OCR] {self._init_note}")

    def _crop_abs(self, frame: np.ndarray,
                  region: Tuple[int, int, int, int],
                  origin: Tuple[int, int]) -> Optional[np.ndarray]:
        try:
            H, W = frame.shape[:2]
            ox, oy = int(origin[0]), int(origin[1])
            ax, ay, aw, ah = region
            lx = int(ax) - ox
            ly = int(ay) - oy
            x1 = max(0, lx); y1 = max(0, ly)
            x2 = min(W, lx + int(aw)); y2 = min(H, ly + int(ah))
            if x2 <= x1 or y2 <= y1:
                return None
            return frame[y1:y2, x1:x2]
        except Exception:
            return None

    def _loop(self) -> None:
        try:
            self._ensure_rec()
        except Exception:
            pass
        while not self._stop_evt.wait(self.poll_sec):
            try:
                self._tick()
            except Exception:
                pass

    def _tick(self) -> None:
        if self._digit_cnn is None or not self._digit_cnn.ready():
            return
        with self._lock:
            frame = self._latest_frame
            origin = self._latest_origin
            hp_r = self._hp_region
            mp_r = self._mp_region
            hp_mx = self._hp_max
            mp_mx = self._mp_max
        if frame is None:
            return
        updated = False
        if hp_r is not None:
            cur, pct = self._ocr_region(frame, origin, hp_r, hp_mx, "hp")
            if cur is not None:
                # 2026-04-24 OCR 오탐 필터: HP 숫자 OCR이 6자리 중 일부 자릿수를
                # 놓쳐 값이 급감해 보이는 경우(예: 603541→190315→603541) 자힐
                # 가짜 트리거. 이전 값 대비 50%+ 급감 시 1프레임 pending, 2프레임
                # 연속 같은 급감이면 그때 수락. 실제 몹 데미지는 50ms 안에 50%
                # 불가능.
                cur_i = int(cur)
                prev_cur = self._last_hp_cur
                is_sus = False
                # 공증 시전 직후 허용 윈도우 중이면 급감 필터 우회.
                import time as _t
                _allow_drop = _t.time() < self._allow_hp_drop_until
                if prev_cur > 0 and not _allow_drop:
                    drop_ratio = (prev_cur - cur_i) / prev_cur
                    if drop_ratio >= 0.5 and cur_i < hp_mx:
                        is_sus = True
                accept = True
                if is_sus:
                    pend = getattr(self, '_hp_pending_sus', None)
                    # 2프레임 연속 낮은 값이면 수락 (진짜 대미지)
                    if pend is not None and abs(pend - cur_i) <= max(
                            100, hp_mx // 100):
                        accept = True
                        self._hp_pending_sus = None
                    else:
                        # 첫 의심 — pending, 현재 값 버림
                        self._hp_pending_sus = cur_i
                        accept = False
                        self._emit(
                            f"[HPMP-REJECT] HP cur={cur_i} "
                            f"prev={prev_cur} drop={drop_ratio*100:.0f}% "
                            f"(OCR 의심 1프레임 pending)"
                        )
                else:
                    # 정상 값 → pending 해제
                    if hasattr(self, '_hp_pending_sus'):
                        self._hp_pending_sus = None
                if accept:
                    with self._lock:
                        self._last_hp_cur = cur_i
                        self._last_hp_pct = int(pct) if pct is not None else -1
                    updated = True
                    if not self._emitted_first_hp:
                        self._emitted_first_hp = True
                        self._emit(
                            f"[HPMP-OCR] HP 첫 인식 cur={cur} max={hp_mx} "
                            f"pct={pct if pct is not None else -1}"
                        )
                    elif cur_i != self._prev_logged_hp_cur:
                        self._emit(
                            f"[HPMP] HP cur={cur} pct="
                            f"{pct if pct is not None else -1}"
                        )
                    self._prev_logged_hp_cur = cur_i
        if mp_r is not None:
            cur, pct = self._ocr_region(frame, origin, mp_r, mp_mx, "mp")
            if cur is not None:
                with self._lock:
                    self._last_mp_cur = int(cur)
                    self._last_mp_pct = int(pct) if pct is not None else -1
                updated = True
                if not self._emitted_first_mp:
                    self._emitted_first_mp = True
                    self._emit(
                        f"[HPMP-OCR] MP 첫 인식 cur={cur} max={mp_mx} "
                        f"pct={pct if pct is not None else -1}"
                    )
                elif int(cur) != self._prev_logged_mp_cur:
                    self._emit(
                        f"[HPMP] MP cur={cur} pct="
                        f"{pct if pct is not None else -1}"
                    )
                self._prev_logged_mp_cur = int(cur)
        # latest 캐시가 갱신됐으면 외부 구독자(예: SkillScheduler) 를
        # 즉시 깨움. scheduler 가 sleep 대신 event.wait 하고 있으므로
        # 폴링 간격 지연 없이 predicate 가 최신 MP 를 읽음.
        if updated and self._on_update is not None:
            try:
                self._on_update()
            except Exception:
                pass

    def _cnn_digits(self, up) -> str:
        """전처리 이미지 → 숫자 CNN 세그+분류 → 숫자열. CNN 미사용시 ''.

        좌표와 동일 _segment_digit_boxes + digit_cnn.predict. HP/MP 는 'cur/max'
        결합이라 슬래시는 세그(높이필터)에서 제외되고 숫자 박스만 잡힘 →
        _split_cur_max 가 max 기준 분리(기존 로직 재사용).
        """
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

    def _ocr_region(self, frame, origin,
                    region: Tuple[int, int, int, int],
                    max_val: int, kind: str = ""):
        crop = self._crop_abs(frame, region, origin)
        if crop is None or crop.size == 0:
            return None, None
        up = _preprocess_for_ocr(crop, upscale=3, thr=20)
        # 숫자 digit_cnn 세그+분류 전용 (좌표/XP 동일 모델). fallback 없음.
        digits = self._cnn_digits(up)
        # 숫자 CNN 학습 데이터 수집 (env OB_COLLECT_DIGITS=1 일 때만).
        try:
            from . import digit_collect
            if digit_collect.enabled():
                digit_collect.collect(kind or "hpmp", up, digits)
        except Exception:
            pass
        if not digits:
            return None, None
        cur = _split_cur_max(digits, int(max_val))
        if cur is None:
            return None, None
        pct = None
        if int(max_val) > 0:
            p = int(round(cur * 100.0 / float(max_val)))
            pct = max(0, min(100, p))
        return int(cur), pct
