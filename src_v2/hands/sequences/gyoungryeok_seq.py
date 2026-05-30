"""공력증강 시퀀스 — v1 1:1.

v1: NUMPAD3 single tap. cast 후 hpmp.allow_hp_drop_for(5s) 호출
(HP 60% 소모를 정당화 — drop ratio filter 가 reject 하지 않게).

ctx['allow_hp_drop_sec'] (룰이 주입) 또는 default 5s.
worker_state['_hpmp_adapter'] 가 hpmp adapter 참조.
"""
from __future__ import annotations

from ...core.plugin_registry import sequence
from ...config import v1_defaults as V1
from ._common import sleep_ms, SLOT_GYOUNGRYEOK
from ..key_transport import KeyTransport, send as kt_send


@sequence("gyoungryeok", description="공력증강 (NUMPAD3) + allow_hp_drop_for(5s)")
def gyoungryeok(ctx: dict) -> None:
    dispatcher = ctx["_dispatcher"]
    worker_state = ctx.get("_worker_state") or {}
    allow_sec = float(ctx.get("allow_hp_drop_sec", V1.GYOUNGRYEOK_HP_DROP_ALLOW_SEC))

    # P0-3: KeyTransport.NUMPAD_LOCKED 직호출. v1 press_normal_vk 동치.
    kt_send(SLOT_GYOUNGRYEOK, KeyTransport.NUMPAD_LOCKED,
            dispatcher=dispatcher, hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
    sleep_ms(50)

    # hpmp adapter 의 allow_hp_drop_for(sec) 호출 hook.
    hpmp = worker_state.get("_hpmp_adapter")
    if hpmp is not None and hasattr(hpmp, "allow_hp_drop_for"):
        try:
            hpmp.allow_hp_drop_for(allow_sec)
        except Exception:
            pass
