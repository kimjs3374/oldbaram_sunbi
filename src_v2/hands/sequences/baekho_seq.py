"""백호의희원 시퀀스 — v1 1:1 (skill_blueprints).

v1: 백호의희원 (NUMPAD4) → 백호의희원첨 (NUMPAD5) 연속 시전.
"""
from __future__ import annotations

from ...core.plugin_registry import sequence
from ...config import v1_defaults as V1
from ._common import sleep_ms, SLOT_BAEKHO_1, SLOT_BAEKHO_2
from ..key_transport import KeyTransport, send as kt_send


@sequence("baekho", description="백호의희원 (NUMPAD4 → NUMPAD5)")
def baekho(ctx: dict) -> None:
    dispatcher = ctx["_dispatcher"]
    # P0-3 (v1_gap_fix_list): KeyTransport.NUMPAD_LOCKED 직호출. v1 press_normal_vk 동치.
    kt_send(SLOT_BAEKHO_1, KeyTransport.NUMPAD_LOCKED,
            dispatcher=dispatcher, hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
    sleep_ms(V1.BAEKHO_INTER_KEY_GAP_MS)
    kt_send(SLOT_BAEKHO_2, KeyTransport.NUMPAD_LOCKED,
            dispatcher=dispatcher, hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
    sleep_ms(50)
