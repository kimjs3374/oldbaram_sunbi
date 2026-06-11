"""쿨다운 창 OCR + 닉네임 OCR (백그라운드 스레드).

[설계 원칙]
- 메인 루프 블로킹 금지. OCR 은 별도 스레드에서 수행. 메인은
  `submit_frame()`으로 최신 프레임만 원자적으로 전달하고, `latest()`로
  마지막 완료 결과를 비블로킹 조회한다.
- 흰탭 TAB-CONFIRM 3프레임 안정성 판정이 OCR 블로킹 때문에 깨지는 회귀를
  방지한다 (2026-04-17 사용자 보고).
- 비침습. OCR 실패 = -1 반환. region 미지정 시 전체 비활성.
- 쿨다운 + 닉네임을 한 스레드에서 순차 OCR — RapidOCR(rec-only) 공유.
- 인식 결과는 대상 스킬 리스트 기반 fuzzy 매칭(_lev1_same_len)으로 보정 →
  "어검술↔어검습", "이기어검↔미기머검" 같은 한글 오인식 복구.

대상 스킬 (하드코딩):
- 파력무참 : SkillScheduler 180s.
- 백호의희원 : NumLockCycler 토글.

API:
    ocr = CooldownOcr(poll_sec=1.0)
    ocr.set_region(x, y, w, h)          # 쿨다운 영역.
    ocr.set_nick_region(x, y, w, h)     # 닉네임 영역 (선택).
    ocr.start()                          # 백그라운드 스레드.
    ...
    ocr.submit_frame(frame, origin)      # 메인 루프 매 프레임 — 비블로킹.
    r = ocr.latest()                     # 최근 결과 (캐시).
    ocr.stop()
"""
from __future__ import annotations

import logging
import os
import pathlib
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

import cv2
import numpy as np

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]

# 공유 RapidOCR rec. 여러 CooldownOcr 인스턴스(쿨/버프/채팅)가 동일 엔진 공유.
# RapidOCR 는 경량(onnxruntime CPU)이라 동시 predict 안전 → lock 불필요(no-op).
_SHARED_REC = None
_SHARED_REC_LOCK = threading.Lock()
_SHARED_REC_NOTE = ""


class _RapidRec:
    """RapidOCR(korean rec-only) 어댑터. predict(crop) -> [{"rec_text": str}].

    쿨/버프/닉/HP/MP 의 기존 라인 파싱 코드가 dict 리스트를 기대하므로 그
    형태로 감싼다. 경량(onnxruntime CPU, ~12ms). torch/paddle 의존 0."""

    def predict(self, crop):
        from .map_rapidocr import read_text
        return [{"rec_text": read_text(crop)}]


def _get_shared_rec():
    """프로세스 단일 RapidOCR 어댑터. lazy init (쿨/버프/닉/HP/MP 공유)."""
    global _SHARED_REC, _SHARED_REC_NOTE
    if _SHARED_REC is not None:
        return _SHARED_REC, _SHARED_REC_NOTE
    with _SHARED_REC_LOCK:
        if _SHARED_REC is not None:
            return _SHARED_REC, _SHARED_REC_NOTE
        _SHARED_REC = _RapidRec()
        _SHARED_REC_NOTE = "shared RapidOCR(korean) rec-only"
    return _SHARED_REC, _SHARED_REC_NOTE


class _NoopLock:
    """락 획득/해제를 무시하는 더미. predict 병렬 호출 허용."""
    def acquire(self, *a, **kw):
        return True
    def release(self):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False


_NOOP_LOCK = _NoopLock()


def get_shared_rec_lock():
    """shared rec lock → NoopLock (병렬 predict 허용).

    RapidOCR(onnxruntime CPU) predict 는 병렬 호출 안전 → 직렬화 lock 제거로
    OCR 간 블로킹 0. (이전 PaddleOCR 시절 lock 경합으로 fps 드롭하던 문제 해소.)
    """
    return _NOOP_LOCK


def get_shared_rec_note() -> str:
    """현재 shared rec 초기화 모드 문자열. lazy, note가 비면 공문자."""
    return _SHARED_REC_NOTE or ""

# "스킬명 NN초" 패턴.
# 2026-04-23: 이름-숫자 사이 OCR 노이즈 문자(괄호/공백/기호/영문 등) 0~6자 관용.
# `[^\d가-힣]` 로 한정해 다른 한글 스킬명을 건너뛰며 매칭하는 오탐은 차단.
# 예: "어검술' 20초", "어검술| 20초" 도 매칭. "진백호령60초어검술20초" 는
# 첫 매칭이 "진백호령 60초" 로 끊기고, finditer 로 뒷부분 "어검술 20초" 도 잡음.
_COOLDOWN_LINE_RE = re.compile(
    r"([가-힣]{2,})[^\d가-힣]{0,6}(\d{1,3})\s*초"
)


def _lev1_same_len(target: str, cand: str) -> bool:
    """동일 길이 편집거리 fuzzy 매칭. target 길이 < 3 이면 False.

    - 길이 3 : 1 diff 까지 허용 ("어검술"↔"어검습").
    - 길이 4+: 2 diff 까지 허용 ("이기어검"↔"미기머검" 같은 한글 자모
      오인식 복구). 4자 중 2자 차이면 50% 보존 → 실용 안전선.
    길이 다른 경우는 매칭 안 함 → "파력무참"↔"파력무참진" 부분일치 오탐 차단.
    """
    if not target or not cand or len(target) < 3:
        return False
    if len(cand) != len(target):
        return False
    max_diffs = 2 if len(target) >= 4 else 1
    diffs = 0
    for a, b in zip(target, cand):
        if a != b:
            diffs += 1
            if diffs > max_diffs:
                return False
    return 1 <= diffs <= max_diffs

_DEFAULT_TARGETS: Dict[str, List[str]] = {
    # 완전일치 매칭 (partial alias 제거). "파력"/"무참" 같은 짧은 alias를
    # 두면 OCR이 "파력무참진"의 "진"을 누락해 "파력무참"으로 읽힐 때 파력무참
    # 타겟이 오탐. 긴 정답만 kws 로 유지 → `_ocr_cooldown` 이 `==` 비교.
    "파력무참": ["파력무참"],
    "백호의희원": ["백호의희원"],
    # 백호의희원'첨 — '/특수문자는 _normalize_name에서 제거됨.
    "백호의희원첨": ["백호의희원첨"],
}

# 타겟 오인식 방지 — 이 토큰이 라인에 포함된 경우 해당 타겟은 매칭하지 않음.
# 예: "파력무참진 98초" 라인은 파력무참 타겟이 먹으면 안 됨 (더 긴 이름).
_TARGET_EXCLUDES: Dict[str, List[str]] = {
    "파력무참": ["파력무참진"],
    # 백호의희원 은 백호의희원첨 보다 짧아 substring 매칭에서 먹힘 — 배제.
    "백호의희원": ["백호의희원첨"],
}

# 격수 custom_mode 에서 미탐지 스킬을 0 으로 내리기까지 허용할 연속 미탐지 프레임 수.
# OCR은 약 1Hz → 3프레임 ≈ 3초. 쿨 UI에 스킬이 실제로 사라졌다면 3초면 확정.
_MISS_STREAK_TO_ZERO: int = 3


def _normalize_name(s: str) -> str:
    """OCR 노이즈(특수문자/공백) 제거."""
    return re.sub(r"[\s'`·ㆍ]+", "", s or "")


@dataclass
class CooldownReading:
    """한 번 OCR 결과. -1 = 미탐지."""
    cd_parlyuk: int = -1
    cd_baekho: int = -1
    raw_text: str = ""
    nickname: str = ""
    ts: float = 0.0
    # 신규: generic 스킬명→남은초. 힐러는 파력/백호만 들어감, 격수는 서브클래스 스킬 전체.
    # -1 = OCR 실패/미수신, 0 = 사용가능, >0 = 쿨 중.
    skills: Dict[str, int] = field(default_factory=dict)


class CooldownOcr:
    """쿨다운 + 닉네임 OCR (백그라운드 스레드)."""

    def __init__(self, poll_sec: float = 1.0, own_rec: bool = False,
                 name: str = "cd"):
        self.poll_sec = max(0.2, float(poll_sec))
        # 2026-04-23: 인스턴스 식별자 (cd/buff/chat 등). [CD-OCR-MISS] 로그 구분용.
        self._name: str = str(name or "cd")
        self._region: Optional[Tuple[int, int, int, int]] = None
        self._nick_region: Optional[Tuple[int, int, int, int]] = None
        self._rec = None
        # own_rec 플래그(레거시): RapidOCR 는 경량 공유로 충분 → _ensure_rec 가
        # 항상 shared rec 사용. 동시 predict 안전이라 전용 인스턴스 불필요.
        self._own_rec: bool = bool(own_rec)
        self._last_read = CooldownReading()
        self._init_note: str = ""
        self._last_diag: str = ""
        # 2026-04-23: [CD-OCR-MISS] 스로틀 로그 상태 (10초/1회).
        self._last_miss_log_ts: float = 0.0
        self._dump_dir: Optional[pathlib.Path] = None
        self._dump_remaining: int = 0
        # 백그라운드 스레드용.
        self._lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_origin: Tuple[int, int] = (0, 0)
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._started = False
        # 닉네임 캐시 (한 번 읽으면 유지. 빈 결과로 덮어쓰지 않음).
        self._nick_cache: str = ""
        # 타겟 스킬 매핑 — 기본은 힐러 (파력/black호). 격수는 set_target_skills로 교체.
        self._targets: Dict[str, List[str]] = {
            k: list(v) for k, v in _DEFAULT_TARGETS.items()
        }
        # set_target_skills 호출로 교체된 상태 여부. True면 "any_detected → 0 내림"
        # 로직 활성화 (격수 서브클래스 UI 용). 힐러 기본값에서는 기존 동작 유지.
        self._targets_custom: bool = False
        # 스킬별 연속 미탐지 카운터. any_detected 프레임에서 이 값이
        # _MISS_STREAK_TO_ZERO 이상일 때만 0으로 강제 (=쿨 끝). 그 이하면
        # -1 유지 → update_own_cds 로컬 감산이 살아있음. OCR 1~2프레임
        # 오인식으로 anchor 가 0 으로 꽂히는 "쿨 10초 남았는데 준비됨 오탐"
        # 방지 (2026-04-19 사용자 보고).
        self._miss_streak: Dict[str, int] = {}

    # ---------------------------------------------------------------
    # 설정
    # ---------------------------------------------------------------
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

    def set_nick_region(self, x: int, y: int, w: int, h: int) -> None:
        if w <= 0 or h <= 0 or x < 0 or y < 0:
            with self._lock:
                self._nick_region = None
            return
        with self._lock:
            self._nick_region = (int(x), int(y), int(w), int(h))
            self._nick_cache = ""  # 영역 바뀌면 재OCR.

    def clear_nick_region(self) -> None:
        with self._lock:
            self._nick_region = None
            self._nick_cache = ""

    def set_target_skills(
        self,
        names_or_mapping: Union[List[str], Dict[str, List[str]]],
    ) -> None:
        """타겟 스킬을 교체. 힐러(기본=파력/백호) 대신 격수 서브클래스/디버프 등 용도.

        인자:
            List[str] — 스킬명 리스트. 각 이름 자체가 키워드가 됨. 특수문자 제거 버전도 대체로 추가.
            Dict[str, List[str]] — 스킬명→추가 키워드 후보.
        """
        new_map: Dict[str, List[str]] = {}
        if isinstance(names_or_mapping, dict):
            for k, v in names_or_mapping.items():
                k = str(k)
                kws = [k, _normalize_name(k)]
                kws += [str(x) for x in (v or [])]
                # 중복 제거 + 빈 문자열 제거, 순서 유지.
                seen = set()
                uniq: List[str] = []
                for kw in kws:
                    if not kw or kw in seen:
                        continue
                    seen.add(kw)
                    uniq.append(kw)
                new_map[k] = uniq
        else:
            for n in (names_or_mapping or []):
                s = str(n)
                if not s:
                    continue
                kws = [s, _normalize_name(s)]
                seen = set()
                uniq: List[str] = []
                for kw in kws:
                    if not kw or kw in seen:
                        continue
                    seen.add(kw)
                    uniq.append(kw)
                new_map[s] = uniq
        if not new_map:
            new_map = {k: list(v) for k, v in _DEFAULT_TARGETS.items()}
            custom = False
        else:
            custom = True
        with self._lock:
            self._targets = new_map
            self._targets_custom = custom
            # 서브클래스 변경 시 이전 카운터 잔재 제거 (다음 프레임 기준 재시작).
            self._miss_streak = {}

    def ready(self) -> bool:
        """쿨 또는 닉 영역이라도 설정되어 있으면 True."""
        with self._lock:
            return self._region is not None or self._nick_region is not None

    def region(self) -> Optional[Tuple[int, int, int, int]]:
        with self._lock:
            return self._region

    def nick_region(self) -> Optional[Tuple[int, int, int, int]]:
        with self._lock:
            return self._nick_region

    def init_note(self) -> str:
        return self._init_note

    def last_diag(self) -> str:
        return self._last_diag

    def set_dump_dir(self, d: pathlib.Path, n: int = 3) -> None:
        self._dump_dir = d
        self._dump_remaining = int(n)

    # ---------------------------------------------------------------
    # 지연 초기화 (스레드 내에서 호출)
    # ---------------------------------------------------------------
    def _ensure_rec(self) -> None:
        if self._rec is not None:
            return
        # RapidOCR 어댑터(rec-only) shared 공용. 경량이라 전용 인스턴스 불필요.
        try:
            self._rec, note = _get_shared_rec()
            self._init_note = note or "shared RapidOCR rec"
        except Exception as e:
            self._rec = None
            self._init_note = f"fail {type(e).__name__}: {e}"

    # ---------------------------------------------------------------
    # 메인 루프에서 호출 (비블로킹)
    # ---------------------------------------------------------------
    def submit_frame(
        self,
        frame: np.ndarray,
        frame_origin: Tuple[int, int] = (0, 0),
    ) -> None:
        """메인 루프가 매 프레임 호출. 최신 프레임만 lock 밑에서 교체."""
        with self._lock:
            # 참조 복사만 — OCR 스레드가 snapshot 시점에 .copy()를 뜬다.
            self._latest_frame = frame
            self._latest_origin = tuple(frame_origin)

    def start(self) -> None:
        if self._started:
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="CooldownOcr"
        )
        self._thread.start()
        self._started = True

    def stop(self) -> None:
        self._stop_evt.set()
        self._started = False

    def latest(self) -> CooldownReading:
        with self._lock:
            return self._last_read

    # ---------------------------------------------------------------
    # 백그라운드 스레드
    # ---------------------------------------------------------------
    def _worker_loop(self) -> None:
        self._ensure_rec()
        while not self._stop_evt.is_set():
            t0 = time.monotonic()
            try:
                self._one_cycle()
            except Exception as e:
                self._last_diag = f"cycle-err {type(e).__name__}: {e}"
            # poll 간격 보장.
            elapsed = time.monotonic() - t0
            wait = max(0.05, self.poll_sec - elapsed)
            if self._stop_evt.wait(wait):
                break

    def _one_cycle(self) -> None:
        if self._rec is None:
            self._last_diag = f"rec-None ({self._init_note})"
            return
        with self._lock:
            frame = self._latest_frame
            origin = self._latest_origin
            region = self._region
            nick_region = self._nick_region
            nick_cache = self._nick_cache
        if frame is None:
            self._last_diag = "no-frame"
            return
        # 프레임 공유 참조는 메인이 덮어쓸 수 있으므로 .copy()로 스냅샷.
        try:
            frame = frame.copy()
        except Exception:
            return
        now = time.time()
        skills: Dict[str, int] = {}
        raw = ""
        if region is not None:
            skills, raw = self._ocr_cooldown(frame, origin, region)
        nick = nick_cache
        if nick_region is not None:
            new_nick = self._ocr_nickname(frame, origin, nick_region)
            if new_nick:
                nick = new_nick
        # 힐러 legacy 필드 — skills dict에서 추출.
        cd_p = int(skills.get("파력무참", -1))
        cd_b = int(skills.get("백호의희원", -1))
        with self._lock:
            self._last_read = CooldownReading(
                cd_parlyuk=cd_p, cd_baekho=cd_b, raw_text=raw,
                nickname=nick, ts=now, skills=dict(skills),
            )
            if nick:
                self._nick_cache = nick

    # ---------------------------------------------------------------
    # 개별 OCR
    # ---------------------------------------------------------------
    def _crop(
        self,
        frame: np.ndarray,
        origin: Tuple[int, int],
        region: Tuple[int, int, int, int],
    ) -> Optional[np.ndarray]:
        xs, ys, w, h = region
        ox, oy = origin
        x = xs - int(ox); y = ys - int(oy)
        H, W = frame.shape[:2]
        x0 = max(0, x); y0 = max(0, y)
        x2 = min(W, x + w); y2 = min(H, y + h)
        if x0 >= W or y0 >= H or x2 <= x0 or y2 <= y0:
            return None
        return frame[y0:y2, x0:x2].copy()

    def _split_text_bands(
        self, crop: np.ndarray, min_h: int = 8
    ) -> list:
        """수평 projection으로 텍스트 라인 band를 찾아 (y0, y1) 리스트 반환.

        RapidOCR rec-only 는 detection 없는 **단일 라인** 인식기 →
        2줄 이상 crop을 통으로 넣으면 빈 결과. 각 줄별로 분리 필수.

        2026-04-21: threshold 고정 170 은 밝은 글자/어두운 배경 전제.
        격수 버프 영역처럼 배경/글자 색상이 다른 경우 band 실패. OTSU 자동
        threshold fallback 추가.
        """
        try:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        except Exception:
            return []
        H = crop.shape[0]

        def _bands_from(bw):
            row_sum = bw.sum(axis=1)
            if row_sum.max() <= 0:
                return []
            thr = float(row_sum.max()) * 0.08
            out = []
            i = 0
            while i < H:
                if row_sum[i] > thr:
                    j = i
                    while j < H and row_sum[j] > thr:
                        j += 1
                    if j - i >= min_h:
                        out.append((i, j))
                    i = j
                else:
                    i += 1
            return out

        # 두 threshold 로 각각 band 추출 후 **더 많이 찾은 쪽** 채용.
        # 힐러 쿨다운 UI (어두운 배경+밝은 글자): T=170 유리.
        # 격수 버프 UI (밝은 배경+어두운 글자): OTSU+INV 유리.
        _, bw1 = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)
        bands1 = _bands_from(bw1)
        _, bw3 = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
        )
        bands3 = _bands_from(bw3)
        # 더 많은 band 반환. 둘 다 빈 경우 OTSU 단순 시도.
        if len(bands3) > len(bands1):
            return bands3
        if bands1:
            return bands1
        _, bw2 = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
        return _bands_from(bw2)

    def _predict_lines_banded(self, crop: np.ndarray) -> list:
        """crop을 라인별로 자르고 각 라인을 3x 업스케일 → predict.

        검증됨(사용자 쿨다운 이미지):
        - 원본 3x 라인 crop: '파력무참175초', '백호의희원83초' 정확 인식.
        - bin3x는 굵은 글자 획을 깎아서 '83→3' 등 숫자 망가뜨림.
        라인 분리 실패(band=0) 시 fallback으로 원본 3x를 통으로 predict.
        """
        all_lines: list = []
        bands = self._split_text_bands(crop)
        # 2줄 쿨다운 UI가 한 band로 합쳐지면 rec 가 두 줄을 가로로 이어붙여
        # "박무참 36초 | 홍의희원 23초" 같은 단일 라인으로 읽어 숫자 탈락 잦음.
        # band 1개 + 높이가 crop의 30% 이상이면 상/하 강제 2등분.
        if len(bands) == 1:
            y0, y1 = bands[0]
            bh = y1 - y0
            if bh >= max(20, int(crop.shape[0] * 0.30)):
                mid = y0 + bh // 2
                bands = [(y0, mid), (mid, y1)]
        diag_parts = [f"bands={len(bands)}"]
        if bands:
            H = crop.shape[0]
            for idx, (y0, y1) in enumerate(bands):
                y0p = max(0, y0 - 3); y1p = min(H, y1 + 3)
                sub = crop[y0p:y1p, :]
                try:
                    sub3 = cv2.resize(
                        sub, (sub.shape[1] * 3, sub.shape[0] * 3),
                        interpolation=cv2.INTER_CUBIC,
                    )
                    res = self._rec.predict(sub3)
                except Exception as e:
                    diag_parts.append(f"b{idx}:err")
                    continue
                lines = self._extract_lines(res)
                diag_parts.append(f"b{idx}:{len(lines)}")
                for ln in lines:
                    if ln and ln not in all_lines:
                        all_lines.append(ln)
        # 2026-04-22: bands 검출됐어도 각 sub-predict 가 전부 0 라인이면
        # 전체 crop 통으로 fallback predict. 격수 버프 영역(배경/폰트 이질)
        # 에서 band 절단 crop 이 rec 에 인식 안 되는 케이스 대응.
        if not all_lines:
            try:
                up3 = cv2.resize(
                    crop, (crop.shape[1] * 3, crop.shape[0] * 3),
                    interpolation=cv2.INTER_CUBIC,
                )
                res = self._rec.predict(up3)
                lines = self._extract_lines(res)
                diag_parts.append(f"fb:{len(lines)}")
                for ln in lines:
                    if ln and ln not in all_lines:
                        all_lines.append(ln)
            except Exception:
                diag_parts.append("fb:err")
        self._last_diag = (
            f"banded [{' '.join(diag_parts)}] total={len(all_lines)}"
        )
        return all_lines

    def _extract_lines(self, res) -> list:
        lines: list = []
        try:
            for item in res:
                if isinstance(item, dict):
                    t = item.get("rec_text") or item.get("text") or ""
                    if t:
                        lines.append(str(t))
                elif isinstance(item, (list, tuple)) and item:
                    lines.append(str(item[0]))
                else:
                    s = str(item)
                    if s:
                        lines.append(s)
        except Exception:
            pass
        return lines

    def _predict_lines(self, crop_up: np.ndarray) -> list:
        try:
            res = self._rec.predict(crop_up)
        except Exception as e:
            self._last_diag = f"predict-err {type(e).__name__}: {e}"
            return []
        lines: list = []
        try:
            for item in res:
                if isinstance(item, dict):
                    t = item.get("rec_text") or item.get("text") or ""
                    if t:
                        lines.append(str(t))
                elif isinstance(item, (list, tuple)) and item:
                    lines.append(str(item[0]))
                else:
                    lines.append(str(item))
        except Exception:
            pass
        try:
            res_type = type(res).__name__
            res_len = len(res) if hasattr(res, "__len__") else -1
            self._last_diag = (
                f"ok rec={self._init_note!r} "
                f"res={res_type}(len={res_len}) lines={len(lines)}"
            )
        except Exception:
            pass
        return lines

    def _ocr_cooldown(
        self,
        frame: np.ndarray,
        origin: Tuple[int, int],
        region: Tuple[int, int, int, int],
    ) -> Tuple[Dict[str, int], str]:
        """return (skills dict[name→sec], raw_text). dict 미포함 스킬=미탐지."""
        crop = self._crop(frame, origin, region)
        if crop is None:
            xs, ys, w, h = region
            ox, oy = origin
            H, W = frame.shape[:2]
            self._last_diag = (
                f"oob screen=({xs},{ys},{w},{h}) "
                f"origin=({ox},{oy}) frame={W}x{H}"
            )
            return {}, ""
        # 최초 N회 crop 덤프 (원본).
        if self._dump_remaining > 0 and self._dump_dir is not None:
            try:
                self._dump_dir.mkdir(parents=True, exist_ok=True)
                import datetime as _dt
                ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                fp = self._dump_dir / f"cd_crop_{ts}.png"
                cv2.imwrite(str(fp), crop)
                self._dump_remaining -= 1
            except Exception:
                pass
        lines = self._predict_lines_banded(crop)
        with self._lock:
            targets = {k: list(v) for k, v in self._targets.items()}
            custom_mode = bool(self._targets_custom)
        # 타겟 키워드는 대기 상태 표기 위해 -1로 초기화 (스킬 하나도 표에 없으면 0 아닌 -1).
        skills: Dict[str, int] = {name: -1 for name in targets.keys()}
        # 2026-04-23: finditer 로 한 줄에 여러 스킬 파싱 가능
        # (band 분리 실패로 "진백호령60초어검술20초" 처럼 합쳐져도 둘 다 잡음).
        miss_raw: list = []  # [CD-OCR-MISS] 진단용 — 타겟 미탐 라인 수집.
        for line in lines:
            matched_any = False
            for m in _COOLDOWN_LINE_RE.finditer(line):
                name_tok, sec = m.group(1), m.group(2)
                try:
                    sec_i = int(sec)
                except ValueError:
                    continue
                name_norm = _normalize_name(name_tok)
                for skill, kws in targets.items():
                    if skills.get(skill, -1) >= 0:
                        continue  # 이미 이 스킬 매칭됨.
                    # 배제 토큰 체크 — "파력무참" 타겟의 "파력무참진" 라인 방어.
                    excludes = _TARGET_EXCLUDES.get(skill, [])
                    line_excluded = False
                    for ex in excludes:
                        if not ex:
                            continue
                        ex_norm = _normalize_name(ex)
                        if ex in name_tok or (ex_norm and ex_norm in name_norm):
                            line_excluded = True
                            break
                    if line_excluded:
                        continue
                    # 완전일치 → 편집거리1(동일길이, 길이≥3) 순으로 매칭.
                    # "이기어검↔이기이검", "어검술↔어검습" 같은 한글 오인식 복구.
                    hit = False
                    for kw in kws:
                        if not kw:
                            continue
                        if kw == name_tok or kw == name_norm:
                            hit = True
                            break
                        if (_lev1_same_len(kw, name_tok)
                                or _lev1_same_len(kw, name_norm)):
                            hit = True
                            break
                    if hit:
                        skills[skill] = sec_i
                        matched_any = True
                        break
            if not matched_any and line:
                miss_raw.append(line[:40])
        # 쿨 UI에 스킬이 보이지 않을 때 = 쿨 0(사용가능) 이면 OCR에는 안 잡힘.
        # 격수 UI 로직은 "사용가능" 표시 목적이므로 다른 스킬이라도 하나 탐지된
        # 프레임은 "OCR 작동 중"으로 간주해 미탐지 스킬을 0으로 내린다.
        # 단, 1~2프레임 오인식(예: '이기어검' → '이기이검')으로 잠깐 미탐지
        # 된 경우에도 즉시 0 으로 꽂으면 update_own_cds 의 pending 2회 확인이
        # 계속 0 값에 통과되어 쿨 10초 남았는데 "준비됨" 오탐 → _miss_streak
        # 가 _MISS_STREAK_TO_ZERO 이상일 때만 0 으로 확정. 그 전엔 -1 유지.
        # 힐러 기본 모드에서는 기존 동작(-1 유지) 유지 — 수신측 stick 로직 보존.
        if custom_mode:
            # 스킬별 연속 미탐지 카운터 갱신.
            for k in list(skills.keys()):
                if skills[k] >= 0:
                    self._miss_streak[k] = 0
                else:
                    self._miss_streak[k] = self._miss_streak.get(k, 0) + 1
            # 더 이상 타겟이 아닌 (서브클래스 바뀐 후의) 카운터는 정리.
            for k in list(self._miss_streak.keys()):
                if k not in skills:
                    self._miss_streak.pop(k, None)
            # 2026-04-23: any_detected 게이트 제거. 단일 타겟(파력무참 버프 OCR)
            # 에선 해당 스킬이 사라지면 any_detected 가 영영 False → 0 전환 불가
            # → 힐러가 만료를 격수에 못 알림. MISS_STREAK=3(≈3s)이 이미 보수적이라
            # 단발 오탐엔 안전. 다중 타겟 custom 모드도 3초 연속 미탐지면 0 처리가
            # 타당 (HUD 는 "준비됨" 표시이며 트리거 로직엔 영향 없음).
            for k in list(skills.keys()):
                if (
                    skills[k] < 0
                    and self._miss_streak.get(k, 0) >= _MISS_STREAK_TO_ZERO
                ):
                    skills[k] = 0
        # 2026-04-23: 타겟 미탐 라인 throttled 로그 (10s/1회).
        # "어검술/이기어검이 인식 안 된다" 재현 시 실제 OCR 텍스트 추적용.
        try:
            now_l = time.monotonic()
            # skills -1 스킬 존재 AND miss_raw 있을 때만.
            if miss_raw and any(v < 0 for v in skills.values()):
                if now_l - self._last_miss_log_ts >= 10.0:
                    pending = [k for k, v in skills.items() if v < 0]
                    logging.getLogger("attacker").info(
                        f"[CD-OCR-MISS] inst={self._name} pending={pending} "
                        f"raw_lines={miss_raw}"
                    )
                    self._last_miss_log_ts = now_l
        except Exception:
            pass
        return skills, "\n".join(lines)

    def _ocr_nickname(
        self,
        frame: np.ndarray,
        origin: Tuple[int, int],
        region: Tuple[int, int, int, int],
    ) -> str:
        crop = self._crop(frame, origin, region)
        if crop is None:
            return ""
        # 닉네임도 라인 분리 + 원본 업스케일. 닉은 보통 1줄이지만 band 검출
        # 실패 시 fallback으로 원본 4배 업스케일 predict 병행.
        lines = self._predict_lines_banded(crop)
        if not lines:
            try:
                crop_up = cv2.resize(
                    crop, (crop.shape[1] * 4, crop.shape[0] * 4),
                    interpolation=cv2.INTER_CUBIC,
                )
                lines = self._predict_lines(crop_up)
            except Exception:
                lines = []
        if not lines:
            return ""
        # 가장 긴 한글/영숫자 조합을 닉네임으로. 공백 제거.
        best = ""
        for ln in lines:
            s = ln.strip()
            if len(s) > len(best):
                best = s
        # 과도한 기호/공백 제거.
        best = re.sub(r"[^\w가-힣]", "", best)
        if len(best) < 2:
            return ""
        return best[:16]
