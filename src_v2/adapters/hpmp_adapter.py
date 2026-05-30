"""HP/MP adapter — wraps src/vision/hpmp.py."""
from __future__ import annotations
import logging
from typing import Any, Tuple

log = logging.getLogger("src_v2.adapters.hpmp")


class SrcHpMpAdapter:
    def __init__(self, src_hpmp: Any) -> None:
        self._h = src_hpmp

    # ---- region/max setter 위임 (V2MainWindow → adapter → src.HpMpReader) ---- #
    def set_hp_region(self, x: int, y: int, w: int, h: int) -> None:
        if self._h is None:
            return
        try:
            fn = getattr(self._h, "set_hp_region", None)
            if callable(fn):
                fn(int(x), int(y), int(w), int(h))
        except Exception:  # noqa: BLE001
            log.exception("set_hp_region fail")

    def set_mp_region(self, x: int, y: int, w: int, h: int) -> None:
        if self._h is None:
            return
        try:
            fn = getattr(self._h, "set_mp_region", None)
            if callable(fn):
                fn(int(x), int(y), int(w), int(h))
        except Exception:  # noqa: BLE001
            log.exception("set_mp_region fail")

    def set_hp_max(self, n: int) -> None:
        if self._h is None:
            return
        try:
            fn = getattr(self._h, "set_hp_max", None)
            if callable(fn):
                fn(int(n))
        except Exception:  # noqa: BLE001
            log.exception("set_hp_max fail")

    def set_mp_max(self, n: int) -> None:
        if self._h is None:
            return
        try:
            fn = getattr(self._h, "set_mp_max", None)
            if callable(fn):
                fn(int(n))
        except Exception:  # noqa: BLE001
            log.exception("set_mp_max fail")

    def latest(self) -> Any:
        if self._h is None:
            return None
        try:
            fn = getattr(self._h, "latest", None)
            if callable(fn):
                return fn()
        except Exception:  # noqa: BLE001
            pass
        return None

    def read(self, frame: Any) -> Tuple[int, int, int, int, int, int]:
        if self._h is None or frame is None:
            return (-1, -1, -1, -1, -1, -1)
        try:
            for m in ("read", "read_hpmp", "infer"):
                fn = getattr(self._h, m, None)
                if callable(fn):
                    r = fn(frame)
                    return self._normalize(r)
            return (-1, -1, -1, -1, -1, -1)
        except Exception:  # noqa: BLE001
            log.exception("hpmp read fail")
            return (-1, -1, -1, -1, -1, -1)

    def _normalize(self, r: Any) -> Tuple[int, int, int, int, int, int]:
        if r is None:
            return (-1, -1, -1, -1, -1, -1)
        # Tuple of 6
        if isinstance(r, tuple) and len(r) == 6:
            return tuple(int(v) for v in r)  # type: ignore
        if isinstance(r, dict):
            return (
                int(r.get("hp_pct", -1)),
                int(r.get("mp_pct", -1)),
                int(r.get("hp_cur", -1)),
                int(r.get("mp_cur", -1)),
                int(r.get("hp_max", -1)),
                int(r.get("mp_max", -1)),
            )
        # Tuple of (hp, mp)
        if isinstance(r, tuple) and len(r) == 2:
            return (int(r[0]), int(r[1]), -1, -1, -1, -1)
        return (-1, -1, -1, -1, -1, -1)

    def is_available(self) -> bool:
        return self._h is not None


class RealHpMpAdapter(SrcHpMpAdapter):
    """Production adapter — wraps src.vision.hpmp.HpMpReader."""

    def __init__(self, log_cb=None, poll_sec: float = 0.33) -> None:
        from src.vision.hpmp import HpMpReader  # lazy
        h = HpMpReader(poll_sec=poll_sec, log_cb=log_cb)
        super().__init__(h)

    def read(self, frame, origin=(0, 0)):
        if self._h is None or frame is None:
            return (-1, -1, -1, -1, -1, -1)
        try:
            # 2026-04-25 origin 인자 받도록 변경 (default 0,0 호환).
            r = self._h.read(frame, origin)
            return (
                int(getattr(r, "hp", -1)),
                int(getattr(r, "mp", -1)),
                int(getattr(r, "hp_cur", -1)),
                int(getattr(r, "mp_cur", -1)),
                int(getattr(r, "hp_max", -1)),
                int(getattr(r, "mp_max", -1)),
            )
        except Exception:  # noqa: BLE001
            log.exception("real hpmp read fail")
            return (-1, -1, -1, -1, -1, -1)

    @property
    def reader(self):
        return self._h
