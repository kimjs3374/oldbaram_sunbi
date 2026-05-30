"""자가부활 룰 — v1 1:1 (healer_worker.py:1190-1205).

v1 트리거:
  - dead_now = (hp == 0)
  - prev=False & dead_now=True (cross-down edge) 시점에만 cast
  - EDGE-DEFER: 맵 전환 중이면 prev 갱신 없이 보류 (다음 프레임 재평가)

ctx.extras key:
  self_dead_prev (bool) — RuleEngine 단일 ctx 재사용 가정
"""
from __future__ import annotations
from typing import Optional

from ...core.plugin_registry import rule
from ...core.snapshot import Snapshot
from ...core.types import CastRequest, RuleContext


@rule(
    name="self_revive",
    priority=1,
    topics=["eye.hp"],
    description="자가부활 (HP=0 cross-down edge, EDGE-DEFER 적용)",
)
def self_revive(snap: Snapshot, ctx: RuleContext) -> Optional[CastRequest]:
    if "self_revive" in ctx.in_progress:
        return None

    # HP 미관측 (-1) → 무시.
    if snap.hp < 0:
        return None
    dead_now = (snap.hp == 0)

    prev = bool(ctx.extras.get("self_dead_prev", False))

    if not dead_now:
        # alive — prev 갱신 후 종료.
        ctx.extras["self_dead_prev"] = False
        return None

    # dead_now=True. edge: prev=False 일 때만.
    if dead_now and not prev:
        # EDGE-DEFER: 맵 전환 중이면 prev 갱신 안 함 → 다음 프레임 재평가.
        if bool(ctx.cfg.get("_map_transition_in_progress", False)):
            return None
        ctx.extras["self_dead_prev"] = True
        return CastRequest(name="self_revive", priority=1)

    # 사망 상태 유지 중 (재요청 금지).
    return None
