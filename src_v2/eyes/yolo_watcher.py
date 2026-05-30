"""YOLO watcher — red_tab / white_tab detection.

Wraps a YoloAdapter (injected) so unit tests can use fakes.

Design ref: §2.4 + §11.2 (src/vision/yolo.py wrap)
"""
from __future__ import annotations
import logging
import time as _time
from typing import Any, Callable, List, Optional, Protocol, Tuple

from .base_watcher import BaseWatcher
from ..core.event_bus import EventBus
from ..core.snapshot import SnapshotStore
from ..core.types import Detection

log = logging.getLogger("src_v2.eyes.yolo")


class YoloAdapter(Protocol):
    """YOLO inference adapter.

    `predict(frame)` -> list of (cls_name, x1, y1, x2, y2, conf)
    """
    def predict(self, frame: Any) -> List[Tuple[str, int, int, int, int, float]]: ...
    def is_available(self) -> bool: ...


class _NullYolo:
    def predict(self, frame): return []
    def is_available(self): return False


class YoloWatcher(BaseWatcher):
    TOPIC_RED = "eye.red_tab"
    TOPIC_WHITE = "eye.white_tab"

    # white_tab 1회 검출 후 유효 유지 시간 (ms).
    # poll 0.09s 기준 confirm=3 충족 최소 window = 270ms → 250ms 시작값.
    WHITE_CACHE_TTL_MS: float = 250.0
    DEFAULT_POLL_SEC: float = 0.09

    def __init__(self,
                 store: SnapshotStore,
                 bus: EventBus,
                 yolo: Optional[YoloAdapter] = None,
                 poll_sec: float = 0.09,  # 11 Hz (↓ from 20 Hz, 2026-05-02)
                 conf_threshold: float = 0.45,
                 log_callback: Optional[Callable[[str], None]] = None) -> None:
        super().__init__("yolo", store, bus, poll_sec=poll_sec, adapter=yolo)
        self.yolo: YoloAdapter = yolo or _NullYolo()
        self.conf_threshold = float(conf_threshold)
        # 진단 로그.
        self._log_emit = log_callback if callable(log_callback) else None
        self._announced_start: bool = False
        self._first_red_logged: bool = False
        self._first_white_logged: bool = False
        self._last_white_warn_ts: float = 0.0
        self._white_seen_count: int = 0
        self._red_seen_count: int = 0
        # white cache — poll 완화 시 순간 흰탭 누락 방지 (2026-05-02).
        self._white_pending_ts: Optional[float] = None
        self._white_cached_det: Optional[Detection] = None

    def _emit(self, s: str) -> None:
        if self._log_emit:
            try:
                self._log_emit(s)
            except Exception:
                pass

    def _tick(self) -> None:
        if not self.yolo.is_available():
            if not self._announced_start:
                self._announced_start = True
                self._emit("[YOLO-H] adapter is_available=False — YOLO 비활성")
            return
        if not self._announced_start:
            self._announced_start = True
            self._emit("[YOLO-H] watcher 시작 — adapter is_available=True")
        # 2026-04-25 v1 healer_worker.py:1391 동일: yolo predict 입력은 crop_frame.
        # last_crop 우선, 없으면 last_frame fallback. detection 좌표에 crop_origin
        # offset 더해서 mss-relative 좌표로 변환 (v1 yolo.py:394~401 동일).
        # numpy ndarray 는 truth value 불명 → is None 체크.
        frame = self.store.read_field("last_crop")
        if frame is None:
            frame = self.store.read_field("last_frame")
        if frame is None:
            return
        crop_origin = self.store.read_field("last_crop_origin", (0, 0)) or (0, 0)
        ox, oy = int(crop_origin[0]), int(crop_origin[1])

        try:
            preds = self.yolo.predict(frame)
        except Exception as e:  # noqa: BLE001
            log.warning("yolo predict err: %s", e)
            return

        red_best: Optional[Detection] = None
        white_best: Optional[Detection] = None
        all_dets: List[Detection] = []
        for cls, x1, y1, x2, y2, conf in preds:
            if conf < self.conf_threshold:
                continue
            # offset 적용 — mss 프레임 좌표.
            d = Detection(cls=cls,
                          bbox=(x1 + ox, y1 + oy, x2 + ox, y2 + oy),
                          conf=conf)
            all_dets.append(d)
            if cls == "red_tab" and (red_best is None or conf > red_best.conf):
                red_best = d
            elif cls == "white_tab" and (white_best is None or conf > white_best.conf):
                white_best = d

        red_now = red_best is not None
        white_now = white_best is not None
        now = _time.time()

        # red 강검출 시 white cache 즉시 무효화 — arm 오작동 방지.
        if red_now and self._white_pending_ts is not None:
            self._white_pending_ts = None
            self._white_cached_det = None
            self._emit("[YOLO-WHITE-CACHE] invalidated by red_tab")

        # white cache 처리.
        if white_now:
            # 실제 검출 → 캐시 갱신
            self._white_pending_ts = now
            self._white_cached_det = white_best
        elif self._white_pending_ts is not None:
            elapsed_ms = (now - self._white_pending_ts) * 1000.0
            if elapsed_ms < self.WHITE_CACHE_TTL_MS:
                # TTL 내 → 캐시 유지
                white_now = True
                white_best = self._white_cached_det
                log.debug("YOLO-WHITE-CACHE hit elapsed_ms=%.0f", elapsed_ms)
            else:
                # TTL 만료
                self._emit(
                    f"[YOLO-WHITE-CACHE] expired elapsed_ms={elapsed_ms:.0f}"
                )
                self._white_pending_ts = None
                self._white_cached_det = None

        # Update snapshot atomically (best detections only)
        prev_red = self.store.read_field("red_tab_present")
        prev_white = self.store.read_field("white_tab_present")

        self.store.update(
            red_tab_present=red_now,
            red_tab_pos=(red_best.center if red_best else None),
            red_tab_detection=red_best,
            white_tab_present=white_now,
            white_tab_detection=white_best,
            all_detections=all_dets,
        )

        # Publish only on edge transitions OR on every detection (configurable)
        if red_now and (not prev_red or red_best is not None):
            self.bus.publish(self.TOPIC_RED, red_best)
        if white_now and (not prev_white or white_best is not None):
            self.bus.publish(self.TOPIC_WHITE, white_best)

        # 진단 로그.
        if red_now:
            self._red_seen_count += 1
            if not self._first_red_logged:
                self._first_red_logged = True
                self._emit(
                    f"[YOLO-H] red_tab 최초 검출 bbox={red_best.bbox} "
                    f"conf={red_best.conf:.2f}"
                )
        if white_now:
            self._white_seen_count += 1
            if not self._first_white_logged:
                self._first_white_logged = True
                self._emit(
                    f"[YOLO-H] white_tab 최초 검출 bbox={white_best.bbox} "
                    f"conf={white_best.conf:.2f}"
                )

        # 30초/1회 — white_tab 0건 pending warn.
        if (now - self._last_white_warn_ts) >= 30.0:
            self._last_white_warn_ts = now
            if not self._first_white_logged:
                self._emit(
                    f"[YOLO-H] white_tab 0건 30s — n_dets_total={len(all_dets)} "
                    f"red_count={self._red_seen_count} conf_thr={self.conf_threshold}"
                )
