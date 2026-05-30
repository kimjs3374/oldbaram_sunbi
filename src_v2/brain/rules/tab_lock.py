"""TAB-LOCK rule — fire when attacker_map_seq edge AND tab_lock pending flag set."""
from __future__ import annotations
from typing import Optional

from ...core.plugin_registry import rule
from ...core.snapshot import Snapshot
from ...core.types import CastRequest, RuleContext


@rule(name="tab_lock", priority=50, topics=["eye.map_changed", "eye.attacker_state"],
      description="TAB-CONFIRM Route A (단일)")
def tab_lock(snap: Snapshot, ctx: RuleContext) -> Optional[CastRequest]:
    if "tab_lock" in ctx.in_progress:
        return None
    if not ctx.cfg.get("tab_lock_enabled", True):
        return None
    if not snap.tab_lock_pending:
        return None
    return CastRequest(name="tab_lock", priority=50)
