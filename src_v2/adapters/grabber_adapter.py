"""Grabber adapter — wraps src/capture/screen.py AsyncGrabber."""
from __future__ import annotations
import logging
from typing import Any, Optional

log = logging.getLogger("src_v2.adapters.grabber")


class SrcGrabberAdapter:
    """Wraps an existing src/capture grabber instance.

    Caller passes the already-constructed grabber (e.g. AsyncGrabber from src).
    """

    def __init__(self, src_grabber: Any) -> None:
        self._g = src_grabber

    def grab(self) -> Any:
        if self._g is None:
            return None
        try:
            # 2026-04-25 v1 healer_worker.py:1336 동일: grab() 호출 우선.
            # AsyncGrabber.grab()은 numpy ndarray 반환. latest()는 다른 의미일
            # 수 있어 우선순위 낮춤.
            for m in ("grab", "latest", "get_frame", "read"):
                fn = getattr(self._g, m, None)
                if callable(fn):
                    return fn()
            return None
        except Exception:  # noqa: BLE001
            log.exception("grab fail")
            return None

    def is_available(self) -> bool:
        return self._g is not None

    @property
    def mon(self):
        return getattr(self._g, "mon", None)

    def stop(self) -> None:
        try:
            fn = getattr(self._g, "stop", None)
            if callable(fn):
                fn()
        except Exception:  # noqa: BLE001
            log.exception("grabber stop fail")


class RealGrabberAdapter(SrcGrabberAdapter):
    """Production adapter — instantiates src.capture.screen.AsyncGrabber.

    Used by entry points (healer_gui_v2.py, attacker_v2.py).
    """

    def __init__(self, monitor_index: int = 1, hwnd: Optional[int] = None,
                 target_interval_s: float = 0.02) -> None:
        from src.capture.screen import AsyncGrabber  # lazy
        g = AsyncGrabber(
            monitor_index=monitor_index,
            hwnd=hwnd,
            target_interval_s=target_interval_s,
        )
        super().__init__(g)
