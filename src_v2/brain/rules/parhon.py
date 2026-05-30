"""파혼술 룰 — v1 1:1 (healer_worker.py:1553-1558).

v1 트리거:
  honma = atk.debuff_honmasul_sec (UDP)
  honma_now = (honma > 0)
  prev=False → cast + prev=True (걸리는 순간 1회 cast)

ctx.extras key: attacker_honma_prev (bool)
"""
from __future__ import annotations
from typing import Optional

from ...core.plugin_registry import rule
from ...core.snapshot import Snapshot
from ...core.types import CastRequest, RuleContext


@rule(
    name="parhon",
    priority=40,
    topics=["eye.attacker_state"],
    description="파혼술 (격수 혼마술 걸리는 edge)",
)
def parhon(snap: Snapshot, ctx: RuleContext) -> Optional[CastRequest]:
    if "parhon" in ctx.in_progress:
        return None
    if not ctx.cfg.get("parhon_enabled", True):
        return None

    honma = int(snap.attacker_honma_sec)
    # 미관측 (-1) — prev 보존.
    if honma < 0:
        return None

    honma_now = (honma > 0)
    prev = bool(ctx.extras.get("attacker_honma_prev", False))

    if not honma_now:
        ctx.extras["attacker_honma_prev"] = False
        return None

    if honma_now and not prev:
        ctx.extras["attacker_honma_prev"] = True
        return CastRequest(name="parhon", priority=40)
    return None
