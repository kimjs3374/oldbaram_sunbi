"""맵 이름 OCR 백그라운드 워커 (RapidOCR korean rec-only).

[목적]
- 맵 OCR 을 메인 루프에서 떼어 별도 스레드에서 돌려 블로킹을 0으로 만든다.
  메인은 `submit_frame()` 으로 최신 프레임만 원자적 전달하고 `latest()` 로
  마지막 완료 결과를 비블로킹 조회.

[정확도]
- crop 로직(_find_map_bar/_crop_map)은 ocr.py 와 동일 규칙 복제. 인식은
  map_rapidocr(RapidOCR korean rec-only) — 노이즈 제거 + X-Y(Z) 구조 정규화
  까지 모듈이 담당. torch/paddle 의존 0.

[동기화]
- 최신 프레임 한 장만 유지 (덮어쓰기). OCR 이 늦으면 중간 프레임은 버림.
  맵 이름은 초 단위 변경이라 frame-skip 허용.
- `interval_s` 스로틀은 워커 루프에도 걸어 과부하 방지.

API:
    worker = MapOcrWorker(map_w=400, map_h=40, map_top_pad=0,
                          map_left_pad=-1, interval_s=2.0)
    worker.start()
    worker.submit_frame(frame)
    r = worker.latest()   # MapOcrReading(raw, ts, cycle_ms)
    worker.stop()
"""
from __future__ import annotations

import pathlib
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]


@dataclass
class MapOcrReading:
    raw: str = ""        # map_rapidocr 노이즈제거+구조정규화 완료 raw. knownmaps 보정은 ocr.py.
    ts: float = 0.0      # OCR 완료 시각 (time.time()).
    cycle_ms: float = 0.0  # 가장 최근 predict + crop 소요.
    crop_h: int = 0
    crop_scale: int = 1


class MapOcrWorker:
    """맵 이름 OCR 백그라운드 워커.

    ocr.py 의 _find_map_bar / _crop_map 와 동일 규칙을 스레드 내에서 실행.
    인식은 map_rapidocr(RapidOCR, 모듈 싱글톤) — torch/paddle 의존 0.
    """

    def __init__(
        self,
        map_w: int = 400,
        map_h: int = 40,
        map_top_pad: int = 0,
        map_left_pad: int = -1,
        interval_s: float = 2.0,
    ):
        self.map_w = int(map_w)
        self.map_h = int(map_h)
        self.map_top_pad = int(map_top_pad)
        self.map_left_pad = int(map_left_pad)
        self.interval_s = max(0.1, float(interval_s))

        self._init_note: str = ""

        self._lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None
        self._last_read = MapOcrReading()
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._started = False

    # -----------------------------------------------------------------
    # 라이프사이클
    # -----------------------------------------------------------------
    def start(self) -> None:
        if self._started:
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="MapOcrWorker"
        )
        self._thread.start()
        self._started = True

    def stop(self) -> None:
        self._stop_evt.set()
        self._started = False

    def init_note(self) -> str:
        return self._init_note

    # -----------------------------------------------------------------
    # 메인 루프에서 호출 (비블로킹)
    # -----------------------------------------------------------------
    def submit_frame(self, frame: np.ndarray) -> None:
        """참조 복사만. 워커 스레드가 snapshot 시점에 .copy() 뜬다."""
        if frame is None:
            return
        with self._lock:
            self._latest_frame = frame

    def latest(self) -> MapOcrReading:
        with self._lock:
            return self._last_read

    # -----------------------------------------------------------------
    # 지연 초기화
    # -----------------------------------------------------------------
    def _ensure_rec(self) -> None:
        # RapidOCR(map_rapidocr) 모듈 싱글톤. 여기선 준비 확인만.
        try:
            from . import map_rapidocr
            self._init_note = ("RapidOCR(korean) rec-only"
                               if map_rapidocr.ready() else "RapidOCR 모델 없음")
        except Exception as e:
            self._init_note = f"fail {type(e).__name__}: {e}"

    # -----------------------------------------------------------------
    # 백그라운드 스레드
    # -----------------------------------------------------------------
    def _worker_loop(self) -> None:
        self._ensure_rec()
        while not self._stop_evt.is_set():
            t0 = time.monotonic()
            try:
                self._one_cycle()
            except Exception:
                pass
            elapsed = time.monotonic() - t0
            wait = max(0.05, self.interval_s - elapsed)
            if self._stop_evt.wait(wait):
                break

    def _one_cycle(self) -> None:
        try:
            from . import map_rapidocr
            _rapid_ok = map_rapidocr.ready()
        except Exception:
            _rapid_ok = False
        if not _rapid_ok:
            return
        with self._lock:
            frame = self._latest_frame
        if frame is None:
            return
        try:
            frame = frame.copy()
        except Exception:
            return

        t_start = time.perf_counter()
        crop, crop_h, crop_scale = self._crop_map(frame)
        if crop is None or crop.size == 0:
            return
        raw = ""
        try:
            raw = map_rapidocr.read_map(crop)
        except Exception:
            raw = ""
        cycle_ms = (time.perf_counter() - t_start) * 1000

        with self._lock:
            self._last_read = MapOcrReading(
                raw=raw,
                ts=time.time(),
                cycle_ms=cycle_ms,
                crop_h=crop_h,
                crop_scale=crop_scale,
            )

    # -----------------------------------------------------------------
    # crop (ocr.py 와 동일 규칙 복제)
    # -----------------------------------------------------------------
    def _find_map_bar(self, img) -> Optional[Tuple[int, int, int, int]]:
        H, W = img.shape[:2]
        search_h = max(40, int(H * 0.1))
        top = img[0:search_h]
        gray = cv2.cvtColor(top, cv2.COLOR_BGR2GRAY) if top.ndim == 3 else top
        dark = gray < 30
        y_probe_bot = max(8, search_h // 3)
        col_ratio = dark[1:y_probe_bot].mean(axis=0)
        mask = col_ratio >= 0.5
        segs = []
        i = 0
        while i < W:
            if not mask[i]:
                i += 1
                continue
            s = i
            while i < W and mask[i]:
                i += 1
            segs.append((s, i))
        max_gap = max(40, int(W * 0.05))
        merged = []
        for s, e in segs:
            if merged and s - merged[-1][1] <= max_gap:
                merged[-1] = (merged[-1][0], e)
            else:
                merged.append((s, e))
        cx_center = W // 2
        cx_lo, cx_hi = int(W * 0.25), int(W * 0.75)
        min_bar_w = max(80, int(W * 0.08))
        max_bar_w = int(W * 0.55)
        candidates = [(s, e) for s, e in merged
                      if cx_lo <= (s + e) // 2 <= cx_hi
                      and min_bar_w <= (e - s) <= max_bar_w]
        if not candidates:
            return None
        candidates.sort(key=lambda se: abs((se[0] + se[1]) // 2 - cx_center))
        x1, x2 = candidates[0]
        y_mean = dark[:, x1:x2].mean(axis=1)
        valid = y_mean >= 0.4
        y_segs = []
        i = 0
        while i < search_h:
            if not valid[i]:
                i += 1
                continue
            s = i
            while i < search_h and valid[i]:
                i += 1
            y_segs.append((s, i))
        if not y_segs:
            return None
        y_segs.sort(key=lambda se: -(se[1] - se[0]))
        y_top, y_bot = y_segs[0]
        y_top = max(0, y_top - 2)
        y_bot = min(H - 1, y_bot + 2)
        if y_bot - y_top < 18:
            y_bot = min(H - 1, y_top + 22)
        return (x1, y_top, x2, y_bot)

    def _crop_map(self, img) -> Tuple[Optional[np.ndarray], int, int]:
        H, W = img.shape[:2]
        bar = self._find_map_bar(img)
        if bar is not None:
            x1, y1, x2, y2 = bar
            c = img[y1:y2, x1:x2]
        else:
            if self.map_left_pad is not None and self.map_left_pad >= 0:
                x1 = self.map_left_pad
            else:
                x1 = (W - self.map_w) // 2
            y1 = self.map_top_pad
            c = img[y1:y1 + self.map_h, x1:x1 + self.map_w]
        if c.size == 0:
            c = img[0:40, 0:400]
        crop_h = int(c.shape[0])
        crop_scale = 1
        try:
            if crop_h < 48:
                scale = max(3, 96 // max(1, crop_h))
                new_w = int(c.shape[1] * scale)
                new_h = int(c.shape[0] * scale)
                c = cv2.resize(c, (new_w, new_h),
                               interpolation=cv2.INTER_LANCZOS4)
                crop_scale = scale
                try:
                    blur = cv2.GaussianBlur(c, (0, 0), 1.0)
                    c = cv2.addWeighted(c, 1.6, blur, -0.6, 0)
                except Exception:
                    pass
        except Exception:
            pass
        return c, crop_h, crop_scale
