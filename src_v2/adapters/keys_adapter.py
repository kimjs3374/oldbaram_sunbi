"""Key/mouse adapter — wraps src/input/keys.py."""
from __future__ import annotations
import logging
from typing import Any

log = logging.getLogger("src_v2.adapters.keys")


class SrcKeysAdapter:
    """Wraps a src/input.keys module or object exposing key_down/key_up/tap/click."""

    def __init__(self, src_keys: Any) -> None:
        self._k = src_keys

    def key_down(self, vk: int) -> None:
        try:
            fn = getattr(self._k, "key_down", None) or getattr(self._k, "down", None) or getattr(self._k, "press", None)
            if fn:
                fn(vk)
        except Exception:  # noqa: BLE001
            log.exception("key_down fail vk=%s", vk)

    def key_up(self, vk: int) -> None:
        try:
            fn = getattr(self._k, "key_up", None) or getattr(self._k, "up", None) or getattr(self._k, "release", None)
            if fn:
                fn(vk)
        except Exception:  # noqa: BLE001
            log.exception("key_up fail vk=%s", vk)

    def key_tap(self, vk: int, hold_ms: int = 30) -> None:
        try:
            fn = (getattr(self._k, "key_tap", None)
                  or getattr(self._k, "tap", None)
                  or getattr(self._k, "press_release", None))
            if fn:
                # try with hold_ms first
                try:
                    fn(vk, hold_ms)
                except TypeError:
                    fn(vk)
                return
            # fallback: down + up
            self.key_down(vk)
            import time as _t
            _t.sleep(hold_ms / 1000.0)
            self.key_up(vk)
        except Exception:  # noqa: BLE001
            log.exception("key_tap fail vk=%s", vk)

    def mouse_click(self, x: int, y: int, button: str = "left") -> None:
        try:
            fn = getattr(self._k, "mouse_click", None) or getattr(self._k, "click", None)
            if fn:
                try:
                    fn(x, y, button)
                except TypeError:
                    fn(x, y)
        except Exception:  # noqa: BLE001
            log.exception("mouse_click fail x=%s y=%s", x, y)


class RealKeysAdapter:
    """Production adapter — direct VK-level interface to src.input.keys.

    src.input.keys 모듈은 KeyController 클래스 (direction 기반) 와 module-level
    `_send_input(vk, up)` 만 있고, vk 단위 high-level 함수는 없습니다.
    v2 KeyAdapter Protocol 은 `key_down(vk) / key_up(vk) / key_tap(vk, hold_ms)
    / mouse_click(x, y, button)` 를 요구하므로 _send_input + mouse_click_at 으로
    매핑합니다.

    KeyController 의 movement_lock 같은 안전장치가 필요한 경우는 v1 entry
    (src.app.healer_gui) 사용을 권장 — v2 하단 InputDispatcher 는 자체적으로
    held direction 추적/릴리즈 일관성을 보장합니다.
    """

    def __init__(self) -> None:
        from src.input import keys as keys_mod  # lazy
        self._mod = keys_mod
        # hwnd: opt-in. find_window 결과를 외부에서 set 가능 (facade 가 wiring 시 주입).
        self.hwnd: int = 0

    def set_hwnd(self, hwnd: int) -> None:
        try:
            self.hwnd = int(hwnd or 0)
        except Exception:
            self.hwnd = 0

    def send_vk(self, vk: int, up: bool) -> None:
        """v1 startup-s 호환 — _send_input 직호출."""
        try:
            self._mod._send_input(int(vk), up=bool(up))
        except Exception:  # noqa: BLE001
            log.exception("send_vk fail vk=%s up=%s", vk, up)

    def key_down(self, vk: int) -> None:
        try:
            self._mod._send_input(int(vk), up=False)
        except Exception:  # noqa: BLE001
            log.exception("key_down fail vk=%s", vk)

    def key_up(self, vk: int) -> None:
        try:
            self._mod._send_input(int(vk), up=True)
        except Exception:  # noqa: BLE001
            log.exception("key_up fail vk=%s", vk)

    def key_tap(self, vk: int, hold_ms: int = 30) -> None:
        import time as _t
        self.key_down(vk)
        try:
            _t.sleep(max(0, int(hold_ms)) / 1000.0)
        except Exception:  # noqa: BLE001
            pass
        self.key_up(vk)

    def mouse_click(self, x: int, y: int, button: str = "left") -> None:
        try:
            fn = getattr(self._mod, "mouse_click_at", None)
            if callable(fn):
                fn(int(x), int(y), button=button)
            else:
                log.warning("mouse_click_at 미존재 — skip")
        except Exception:  # noqa: BLE001
            log.exception("mouse_click fail x=%s y=%s", x, y)
