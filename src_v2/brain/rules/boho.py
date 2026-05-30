"""보호 룰 — v1 1:1 (healer_worker.py:1568-1575).

무장과 동일 패턴 (buff_boho_sec).

ctx.extras key: attacker_boho_have_prev (bool)
"""
from __future__ import annotations
from typing import Optional

from ...core.plugin_registry import rule
from ...core.snapshot import Snapshot
from ...core.types import CastRequest, RuleContext


@rule(
    name="boho",
    priority=51,
    topics=["eye.attacker_state"],
    description="보호 (격수 보호 버프 사라진 edge)",
)
def boho(snap: Snapshot, ctx: RuleContext) -> Optional[CastRequest]:
    if "boho" in ctx.in_progress:
        return None
    if not ctx.cfg.get("boho_enabled", True):
        return None

    boh = int(snap.attacker_boho_sec)
    have_now = (boh >= 1)
    prev_have = bool(ctx.extras.get("attacker_boho_have_prev", False))

    if boh == 0 and prev_have:
        ctx.extras["attacker_boho_have_prev"] = False
        return CastRequest(name="boho", priority=51)

    if boh != -1:
        ctx.extras["attacker_boho_have_prev"] = have_now
    return None
