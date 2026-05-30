"""OCR watcher — coord + map name.

Wraps an OcrAdapter (injected). Adapter returns (coord, map_name) per frame.

Design ref: §2.4 + §11.2 (src/vision/ocr.py + map_ocr.py wrap)
"""
from __future__ import annotations
import logging
import time as _time
from typing import Any, Callable, Optional, Protocol, Tuple

from .base_watcher import BaseWatcher
from ..core.event_bus import EventBus
from ..core.snapshot import SnapshotStore

log = logging.getLogger("src_v2.eyes.ocr")


class OcrAdapter(Protocol):
    """OCR adapter.

    `read(frame)` -> (coord_or_None, map_name_or_empty)
    """
    def read(self, frame: Any) -> Tuple[Optional[Tuple[int, int]], str]: ...
    def is_available(self) -> bool: ...


class _NullOcr:
    def read(self, frame): return (None, "")
    def is_available(self): return False


class OcrWatcher(BaseWatcher):
    TOPIC_COORD = "eye.coord"
    TOPIC_MAP_CHANGED = "eye.map_changed"

    def __init__(self,
                 store: SnapshotStore,
                 bus: EventBus,
                 ocr: Optional[OcrAdapter] = None,
                 poll_sec: float = 0.05,
                 log_callback: Optional[Callable[[str], None]] = None) -> None:
        super().__init__("ocr", store, bus, poll_sec=poll_sec, adapter=ocr)
        self.ocr: OcrAdapter = ocr or _NullOcr()
        self._last_coord: Optional[Tuple[int, int]] = None
        self._last_map: str = ""
        # 진단 로그 — v1 healer_worker.py:1466~1505 [OCR-H] 동치.
        self._log_emit = log_callback if callable(log_callback) else None
        self._announced_start: bool = False
        self._first_read_logged: bool = False
        self._first_coord_logged: bool = False
        self._first_map_logged: bool = False
        self._last_pending_warn_ts: float = 0.0
        self._last_heartbeat_ts: float = 0.0
        self._heartbeat_interval_sec: float = 30.0

    def _emit(self, s: str) -> None:
        if self._log_emit:
            try:
                self._log_emit(s)
            except Exception:
                pass

    def _tick(self) -> None:
        # tick alive 진단 — 첫 진입 시 1회 + 30s heartbeat (frame None / read None 도 알림).
        if not self._announced_start:
            self._announced_start = True
            try:
                avail = bool(self.ocr.is_available())
            except Exception:
                avail = False
            self._emit(
                f"[OCR-H] watcher tick 시작 — adapter type={type(self.ocr).__name__} "
                f"is_available={avail}"
            )
        if not self.ocr.is_available():
            return
        frame = self.store.read_field("last_frame")
        if frame is None:
            # 5초/1회 frame 없음 경고.
            now = _time.time()
            if (now - self._last_pending_warn_ts) >= 5.0:
                self._last_pending_warn_ts = now
                self._emit(
                    "[ocr-fail-H] last_frame=None — capture watcher 미가동/미수신"
                )
            return
        try:
            r = self.ocr.read(frame)
            if r is None:
                self._emit("[ocr-fail-H] read 가 None 반환 — adapter 응답 없음")
                return
            coord, map_name = r
        except Exception as e:  # noqa: BLE001
            log.exception("ocr read fail: %s", e)
            self._emit(f"[ocr-fail-H] read 예외: {e}")
            return

        if not self._first_read_logged:
            self._first_read_logged = True
            self._emit(
                f"[OCR-H] 첫 read — coord={coord} map='{map_name}' "
                f"(이후 변경 edge 만 emit)"
            )

        # coord update
        if coord is not None and coord != self._last_coord:
            self.store.update(healer_coord=coord)
            self.bus.publish(self.TOPIC_COORD, coord)
            self._last_coord = coord
            if not self._first_coord_logged:
                self._first_coord_logged = True
                self._emit(f"[OCR-H] 힐러좌표 최초 획득 coord={coord}")

        # map name update
        if map_name and map_name != self._last_map:
            prev_seq = self.store.read_field("healer_map_seq", 0) or 0
            self.store.update(
                healer_map=map_name,
                healer_map_seq=prev_seq + 1,
            )
            self.bus.publish(self.TOPIC_MAP_CHANGED, map_name)
            self._last_map = map_name
            if not self._first_map_logged:
                self._first_map_logged = True
                self._emit(f"[OCR-H] 힐러맵 최초 획득 map='{map_name}'")

        # 5초/1회 pending — coord 도 map 도 아직 못 잡으면 알림.
        if coord is None and not map_name:
            now = _time.time()
            if (now - self._last_pending_warn_ts) >= 5.0:
                self._last_pending_warn_ts = now
                # 진단 정보 추가 (RealOcrAdapter.diag).
                diag_str = ""
                try:
                    diag_fn = getattr(self.ocr, "diag", None)
                    if callable(diag_fn):
                        d = diag_fn()
                        if d:
                            diag_str = (
                                f" reads={d.get('read_call_count', 0)} "
                                f"async_thread={'alive' if d.get('async_thread_alive') else 'DEAD'} "
                                f"last_pred_ms={d.get('async_last_predict_ms', 0):.1f} "
                                f"easy={d.get('easy_device', '?')}"
                            )
                except Exception:
                    pass
                self._emit(
                    "[ocr-fail-H] coord=None map='' — 영역/HUD 가시성/OCR 모델 확인"
                    + diag_str
                )

        # 30s heartbeat — 살아있다는 신호 (frame, coord, map 상태 함께).
        now = _time.time()
        if (now - self._last_heartbeat_ts) >= self._heartbeat_interval_sec:
            self._last_heartbeat_ts = now
            try:
                tc = getattr(self, "_tick_count", 0)
                self._emit(
                    f"[OCR-H] heartbeat ticks={tc} "
                    f"last_coord={self._last_coord} last_map='{self._last_map}'"
                )
            except Exception:
                pass
