"""Cooldown OCR adapter — wraps src/vision/cooldown_ocr.py."""
from __future__ import annotations
import logging
from typing import Any, Dict

log = logging.getLogger("src_v2.adapters.cooldown")


class SrcCooldownAdapter:
    def __init__(self, src_cd: Any) -> None:
        self._c = src_cd

    # 2026-04-25 영역 setter wiring (누락이었음 — region 설정 안 돼 OCR 동작 0).
    def set_region(self, x: int, y: int, w: int, h: int) -> None:
        if self._c is None:
            return
        try:
            fn = getattr(self._c, "set_region", None)
            if callable(fn):
                fn(int(x), int(y), int(w), int(h))
                log.info("cooldown set_region %d,%d,%d,%d", x, y, w, h)
        except Exception:  # noqa: BLE001
            log.exception("cooldown set_region fail")

    def set_nick_region(self, x: int, y: int, w: int, h: int) -> None:
        if self._c is None:
            return
        try:
            fn = getattr(self._c, "set_nick_region", None)
            if callable(fn):
                fn(int(x), int(y), int(w), int(h))
                log.info("cooldown set_nick_region %d,%d,%d,%d", x, y, w, h)
        except Exception:  # noqa: BLE001
            log.exception("cooldown set_nick_region fail")

    def set_target_skills(self, names) -> None:
        if self._c is None:
            return
        try:
            fn = getattr(self._c, "set_target_skills", None)
            if callable(fn):
                fn(list(names))
        except Exception:  # noqa: BLE001
            pass

    def read(self, frame: Any) -> Dict[str, int]:
        if self._c is None or frame is None:
            return {}
        try:
            for m in ("read", "infer", "extract"):
                fn = getattr(self._c, m, None)
                if callable(fn):
                    r = fn(frame)
                    if isinstance(r, dict):
                        return {k: int(v) if isinstance(v, (int, float)) else v
                                for k, v in r.items()}
                    return {}
            return {}
        except Exception:  # noqa: BLE001
            log.exception("cooldown read fail")
            return {}

    def is_available(self) -> bool:
        return self._c is not None

    def stop(self) -> None:
        try:
            fn = getattr(self._c, "stop", None)
            if callable(fn):
                fn()
        except Exception:  # noqa: BLE001
            pass


class RealCooldownAdapter(SrcCooldownAdapter):
    """Production adapter — wraps src.vision.cooldown_ocr.CooldownOcr.

    `name` is one of: 'cd' (skill cooldown), 'buff' (buff/debuff), 'chat'.
    Region/target_skills must be set externally via underlying_ocr.set_*().
    """

    def __init__(self, name: str = "cd", poll_sec: float = 1.0,
                 own_rec: bool = False) -> None:
        from src.vision.cooldown_ocr import CooldownOcr  # lazy
        c = CooldownOcr(poll_sec=poll_sec, name=name, own_rec=own_rec)
        c.start()
        super().__init__(c)
        self._name = name

    def read(self, frame, origin=(0, 0)):
        # Cooldown OCR is async-pull style: submit_frame + latest()
        if self._c is None or frame is None:
            return {}
        try:
            # 2026-04-25 origin 인자 받도록. region 화면 절대 좌표 → frame 변환.
            try:
                self._c.submit_frame(frame, origin)
            except Exception:  # noqa: BLE001
                pass
            r = self._c.latest()
            return dict(getattr(r, "skills", {}) or {})
        except Exception:  # noqa: BLE001
            log.exception("real cooldown read fail name=%s", self._name)
            return {}

    @property
    def underlying_ocr(self):
        return self._c
