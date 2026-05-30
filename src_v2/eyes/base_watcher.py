"""Base watcher — polling thread template.

Design ref: §2.4
"""
from __future__ import annotations
import logging
import threading
import time
from typing import Optional, Protocol

from ..core.event_bus import EventBus
from ..core.snapshot import SnapshotStore

log = logging.getLogger("src_v2.eyes")


class WatcherAdapter(Protocol):
    """Adapter interface — concrete watchers can use this to abstract
    external dependencies (OCR libs, YOLO model, frame grabber).

    Adapters are injected at construction so tests can swap with fakes.
    """
    def is_available(self) -> bool: ...


class BaseWatcher(threading.Thread):
    """Base class for all eye watchers.

    Subclass and implement `_tick(self)` — called every `poll_sec` until stopped.
    Update `self.store` (SnapshotStore) and publish to `self.bus` (EventBus).
    Exceptions in _tick are logged and swallowed; thread keeps running.
    """

    def __init__(self,
                 name: str,
                 store: SnapshotStore,
                 bus: EventBus,
                 poll_sec: float = 0.5,
                 adapter: Optional[WatcherAdapter] = None) -> None:
        super().__init__(daemon=True, name=f"eye_{name}")
        self.watcher_name = name
        self.store = store
        self.bus = bus
        self.poll_sec = max(0.001, float(poll_sec))
        self.adapter = adapter
        self._stop_evt = threading.Event()
        self._tick_count: int = 0
        self._err_count: int = 0
        self._last_tick_ts: float = 0.0
        self._last_dur_ms: float = 0.0

    def run(self) -> None:
        log.info("watcher %s start poll=%.3fs", self.watcher_name, self.poll_sec)
        while not self._stop_evt.wait(self.poll_sec):
            t0 = time.perf_counter()
            try:
                self._tick()
                self._tick_count += 1
                self._last_tick_ts = time.monotonic()
            except Exception as e:  # noqa: BLE001
                self._err_count += 1
                log.exception("watcher %s tick err: %s", self.watcher_name, e)
            self._last_dur_ms = (time.perf_counter() - t0) * 1000.0
        log.info("watcher %s stop", self.watcher_name)

    def _tick(self) -> None:
        raise NotImplementedError

    def stop(self, timeout: float = 2.0) -> None:
        # P0-1 fix 2026-04-28: adapter.stop() chain 강제.
        # 이전: 폴링 thread 만 join → 하부 adapter (UdpReceiver 등) 의 socket/thread
        # 가 process 안에 잔존 → 재기동 bind 30회 실패 → adapter=None.
        # blueprint_transport §4.3 stop chain 계약.
        self._stop_evt.set()
        if self.is_alive():
            self.join(timeout=timeout)
        ad = getattr(self, "adapter", None)
        if ad is not None:
            for m in ("stop", "close"):
                fn = getattr(ad, m, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:  # noqa: BLE001
                        log.exception("watcher %s adapter.%s fail",
                                      self.watcher_name, m)
                    break

    def stats(self) -> dict:
        return {
            "name": self.watcher_name,
            "tick_count": self._tick_count,
            "err_count": self._err_count,
            "last_tick_ts": self._last_tick_ts,
            "alive": self.is_alive(),
        }
