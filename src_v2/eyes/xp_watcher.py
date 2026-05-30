"""XP (experience) watcher — for hunt analytics.

Design ref: §2.4 + §11.2 (src/vision/xp_ocr.py wrap)
"""
from __future__ import annotations
import logging
from typing import Any, Optional, Protocol

from .base_watcher import BaseWatcher
from ..core.event_bus import EventBus
from ..core.snapshot import SnapshotStore

log = logging.getLogger("src_v2.eyes.xp")


class XpAdapter(Protocol):
    """XP reader. `read(frame)` -> int (current xp) or -1."""
    def read(self, frame: Any) -> int: ...
    def is_available(self) -> bool: ...


class _NullXp:
    def read(self, frame): return -1
    def is_available(self): return False


class XpWatcher(BaseWatcher):
    TOPIC_XP = "eye.xp"
    # P1-2 (v1_gap_fix_list): freshness 메타 topic.
    # payload = {"source_state": "unconfigured|empty|observed|rejected", "xp": int,
    #            "last_observed_age_sec": float}
    TOPIC_STATE = "eye.xp_state"

    def __init__(self,
                 store: SnapshotStore,
                 bus: EventBus,
                 adapter: Optional[XpAdapter] = None,
                 poll_sec: float = 2.0) -> None:
        super().__init__("xp", store, bus, poll_sec=poll_sec, adapter=adapter)
        self.adapter: XpAdapter = adapter or _NullXp()
        self._last_xp = -1
        self._last_observed_ts = 0.0

    def _publish_state(self, state: str, xp: int = -1) -> None:
        import time as _t
        try:
            age = (_t.monotonic() - self._last_observed_ts) if self._last_observed_ts else -1.0
            self.bus.publish(self.TOPIC_STATE, {
                "source_state": state, "xp": int(xp),
                "last_observed_age_sec": float(age),
            })
        except Exception:
            pass

    def _tick(self) -> None:
        import time as _t
        if not self.adapter.is_available():
            self._publish_state("unconfigured")
            return
        frame = self.store.read_field("last_frame")
        if frame is None:
            self._publish_state("empty")
            return
        # adapter.read 가 int 또는 tuple 반환할 수 있음. 정규화.
        # 2026-04-25 origin 전달 — region 화면 절대 좌표 → frame 변환.
        origin = self.store.read_field("monitor_origin", (0, 0)) or (0, 0)
        try:
            try:
                r = self.adapter.read(frame, origin=origin)
            except TypeError:
                r = self.adapter.read(frame)
        except Exception:
            self._publish_state("rejected", xp=self._last_xp)
            return
        if isinstance(r, tuple):
            xp = int(r[0]) if r else -1
        else:
            try:
                xp = int(r)
            except Exception:
                xp = -1
        if xp >= 0:
            self._last_observed_ts = _t.monotonic()
            self._publish_state("observed", xp=xp)
            if xp != self._last_xp:
                self.bus.publish(self.TOPIC_XP, xp)
                self._last_xp = xp
        else:
            self._publish_state("empty", xp=self._last_xp)
