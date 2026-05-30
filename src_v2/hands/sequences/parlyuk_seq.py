"""파력무참 시퀀스 — NUMPAD8 single tap.

cast 시 worker_state['parlyuk_buff_active']=True 표시 (worker 가 coord_tol=1
강제 적용). 룰에서 force_coord_tol=1 ctx 로 전달돼 worker_state 에 기록.
"""
from __future__ import annotations

from ...core.plugin_registry import sequence
from ...config import v1_defaults as V1
from ._common import sleep_ms, SLOT_PARLYUK
from ..key_transport import KeyTransport, send as kt_send


@sequence("parlyuk", description="파력무참 (NUMPAD8) + coord_tol=1 emit")
def parlyuk(ctx: dict) -> None:
    dispatcher = ctx["_dispatcher"]
    worker_state = ctx.get("_worker_state") or {}
    force_tol = ctx.get("force_coord_tol")

    # P0-3: KeyTransport.NUMPAD_LOCKED 직호출. v1 press_normal_vk 동치.
    kt_send(SLOT_PARLYUK, KeyTransport.NUMPAD_LOCKED,
            dispatcher=dispatcher, hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
    sleep_ms(50)

    # buff active 시 coord_tol=1 강제 적용 신호. worker 가 muscle cfg 갱신.
    if force_tol is not None and worker_state is not None:
        worker_state["parlyuk_force_coord_tol"] = int(force_tol)
        worker_state["parlyuk_cast_at"] = __import__("time").time()
