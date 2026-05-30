"""보호 시퀀스 — v1 1:1 (skill_blueprints.cast_boho_hook).

v1: Shift hold + Z + X → Shift release. 채팅 OCR ESC 체크.
"""
from __future__ import annotations

from ...core.plugin_registry import sequence
from ...config import v1_defaults as V1
from ._common import sleep_ms, VK_ESC

VK_SHIFT = 0xA0
VK_Z = 0x5A
VK_X = 0x58


@sequence("boho", description="보호 (Shift+Z+X) + 채팅 ESC 체크")
def boho(ctx: dict) -> None:
    dispatcher = ctx["_dispatcher"]
    worker_state = ctx.get("_worker_state") or {}

    try:
        if hasattr(dispatcher, "key_down"):
            dispatcher.key_down(VK_SHIFT)
            sleep_ms(int(V1.BOHO_KEY_GAP_SEC * 1000))
            dispatcher.tap(VK_Z, hold_ms=V1.BOHO_SHIFT_HOLD_MS)
            sleep_ms(int(V1.BOHO_KEY_GAP_SEC * 1000))
            dispatcher.tap(VK_X, hold_ms=V1.BOHO_SHIFT_HOLD_MS)
            sleep_ms(int(V1.BOHO_KEY_GAP_SEC * 1000))
            dispatcher.key_up(VK_SHIFT)
        else:
            dispatcher.tap(VK_Z, hold_ms=V1.BOHO_SHIFT_HOLD_MS)
            sleep_ms(int(V1.BOHO_KEY_GAP_SEC * 1000))
            dispatcher.tap(VK_X, hold_ms=V1.BOHO_SHIFT_HOLD_MS)
    except Exception:
        try:
            if hasattr(dispatcher, "key_up"):
                dispatcher.key_up(VK_SHIFT)
        except Exception:
            pass

    chat = worker_state.get("_chat_adapter")
    if chat is not None and hasattr(chat, "is_chat_open"):
        import time as _t
        deadline = _t.time() + V1.BOHO_CHAT_ESC_TIMEOUT_SEC
        while _t.time() < deadline:
            try:
                if chat.is_chat_open():
                    dispatcher.tap(VK_ESC, hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
                    sleep_ms(50)
            except Exception:
                pass
            sleep_ms(int(V1.BOHO_CHAT_ESC_POLL_SEC * 1000))
