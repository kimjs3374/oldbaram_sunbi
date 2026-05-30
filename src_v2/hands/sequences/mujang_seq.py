"""무장 시퀀스 — v1 1:1.

v1 (skill_blueprints.cast_mujang_hook):
  Shift hold + Z + C → Shift release.
  cast 후 채팅 OCR poll 로 ESC 체크 (시전 도중 채팅창 열린 거 닫기).

본 시퀀스는 InputDispatcher.shift_combo 가 있으면 그걸 사용, 없으면 hold/release
저수준 호출 fallback.
"""
from __future__ import annotations

from ...core.plugin_registry import sequence
from ...config import v1_defaults as V1
from ._common import sleep_ms, VK_DIGIT, VK_ESC

VK_SHIFT = 0xA0  # LSHIFT
VK_Z = 0x5A
VK_C = 0x43


@sequence("mujang", description="무장 (Shift+Z+C) + 채팅 ESC 체크")
def mujang(ctx: dict) -> None:
    dispatcher = ctx["_dispatcher"]
    worker_state = ctx.get("_worker_state") or {}

    try:
        if hasattr(dispatcher, "key_down"):
            dispatcher.key_down(VK_SHIFT)
            sleep_ms(int(V1.MUJANG_KEY_GAP_SEC * 1000))
            dispatcher.tap(VK_Z, hold_ms=V1.MUJANG_SHIFT_HOLD_MS)
            sleep_ms(int(V1.MUJANG_KEY_GAP_SEC * 1000))
            dispatcher.tap(VK_C, hold_ms=V1.MUJANG_SHIFT_HOLD_MS)
            sleep_ms(int(V1.MUJANG_KEY_GAP_SEC * 1000))
            dispatcher.key_up(VK_SHIFT)
        else:
            # fallback — single tap (Shift 미지원).
            dispatcher.tap(VK_Z, hold_ms=V1.MUJANG_SHIFT_HOLD_MS)
            sleep_ms(int(V1.MUJANG_KEY_GAP_SEC * 1000))
            dispatcher.tap(VK_C, hold_ms=V1.MUJANG_SHIFT_HOLD_MS)
    except Exception:
        # release shift for safety.
        try:
            if hasattr(dispatcher, "key_up"):
                dispatcher.key_up(VK_SHIFT)
        except Exception:
            pass

    # 채팅 OCR ESC poll — chat adapter 가 있으면 활용.
    chat = worker_state.get("_chat_adapter")
    if chat is not None and hasattr(chat, "is_chat_open"):
        import time as _t
        deadline = _t.time() + V1.MUJANG_CHAT_ESC_TIMEOUT_SEC
        while _t.time() < deadline:
            try:
                if chat.is_chat_open():
                    dispatcher.tap(VK_ESC, hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
                    sleep_ms(50)
            except Exception:
                pass
            sleep_ms(int(V1.MUJANG_CHAT_ESC_POLL_SEC * 1000))
