"""XP OCR adapter — wraps src/vision/xp_ocr.py.

Returns (xp_abs, xp_per_hour) from current frame. v2 XpWatcher uses
.read(frame) -> (int xp, int xph) signature.
"""
from __future__ import annotations
import logging
from typing import Any, Tuple

log = logging.getLogger("src_v2.adapters.xp")


class SrcXpAdapter:
    def __init__(self, src_xp: Any) -> None:
        self._x = src_xp

    def set_region(self, x: int, y: int, w: int, h: int) -> None:
        if self._x is None:
            return
        try:
            fn = getattr(self._x, "set_region", None)
            if callable(fn):
                fn(int(x), int(y), int(w), int(h))
        except Exception:  # noqa: BLE001
            log.exception("xp set_region fail")

    def xp_per_hour(self) -> int:
        if self._x is None:
            return 0
        try:
            fn = getattr(self._x, "xp_per_hour", None)
            if callable(fn):
                return int(fn() or 0)
        except Exception:  # noqa: BLE001
            pass
        return 0

    def read(self, frame: Any, origin=(0, 0)) -> Tuple[int, int]:
        if self._x is None or frame is None:
            return (0, 0)
        try:
            # 2026-04-25 origin 인자 받도록 — frame 외 영역 crop 차단.
            try:
                if getattr(self._x, "ready", lambda: False)():
                    self._x.submit_frame(frame, origin)
            except Exception:  # noqa: BLE001
                pass
            xp_abs = int(getattr(self._x, "_last_xp", 0) or 0)
            xph = int(self._x.xp_per_hour() or 0) if hasattr(self._x, "xp_per_hour") else 0
            return (xp_abs, xph)
        except Exception:  # noqa: BLE001
            log.exception("xp read fail")
            return (0, 0)

    def is_available(self) -> bool:
        return self._x is not None

    def stop(self) -> None:
        try:
            fn = getattr(self._x, "stop", None)
            if callable(fn):
                fn()
        except Exception:  # noqa: BLE001
            pass


class RealXpAdapter(SrcXpAdapter):
    """Production adapter — instantiates src.vision.xp_ocr.XpOcr."""

    def __init__(self, log_cb=None) -> None:
        from src.vision.xp_ocr import XpOcr  # lazy
        x = XpOcr(log_cb=log_cb)
        super().__init__(x)

    @property
    def reader(self):
        return self._x
