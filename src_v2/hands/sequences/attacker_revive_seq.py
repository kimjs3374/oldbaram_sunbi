"""격수부활 시퀀스 — v1 1:1.

v1 흐름:
  TAB → 격수 빨탭 고정 (이미 격수 자기 빨탭 상태가 default)
  NUMPAD6 burst 0.3s @ 0.1s

자힐과 다른 점: HOME 으로 self-target 하지 않음. 격수가 이미 타겟.
TAB 한 번으로 다음 대상 확정 (Route A self → 격수).
"""
from __future__ import annotations
import time

from ...core.plugin_registry import sequence
from ...config import v1_defaults as V1
from ._common import sleep_ms, tap_vk


@sequence("attacker_revive", description="격수부활 (TAB → NUMPAD6 burst)")
def attacker_revive(ctx: dict) -> None:
    dispatcher = ctx["_dispatcher"]

    tap_vk(dispatcher, V1.VK_TAB, hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
    sleep_ms(int(V1.ATTACKER_REVIVE_KEY_GAP_SEC * 1000))

    t_end = time.time() + V1.ATTACKER_REVIVE_BURST_SEC
    while time.time() < t_end:
        try:
            dispatcher.tap(V1.VK_NUMPAD6, hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
        except Exception:
            pass
        sleep_ms(int(V1.ATTACKER_REVIVE_BURST_INTERVAL_SEC * 1000))
