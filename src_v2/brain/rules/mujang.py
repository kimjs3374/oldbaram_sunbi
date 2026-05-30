"""무장 룰 — v1 1:1 (healer_worker.py:1559-1567).

v1 트리거:
  muj = atk.buff_mujang_sec (UDP)
  - muj == 0 (확정 부재) AND prev_have=True (직전 있음) → cast 요청
  - muj == -1 (미관측) → prev 갱신 보류 (현 상태 유지)
  - muj >= 1 → prev_have=True

mujang_have_prev=True 일 때 0 으로 떨어진 순간만 cast (없어진 edge).

ctx.extras key: attacker_mujang_have_prev (bool)
"""
from __future__ import annotations
from typing import Optional

from ...core.plugin_registry import rule
from ...core.snapshot import Snapshot
from ...core.types import CastRequest, RuleContext


@rule(
    name="mujang",
    priority=50,
    topics=["eye.attacker_state"],
    description="무장 (격수 무장 버프 사라진 edge)",
)
def mujang(snap: Snapshot, ctx: RuleContext) -> Optional[CastRequest]:
    if "mujang" in ctx.in_progress:
        return None
    if not ctx.cfg.get("mujang_enabled", True):
        return None

    muj = int(snap.attacker_mujang_sec)
    have_now = (muj >= 1)
    prev_have = bool(ctx.extras.get("attacker_mujang_have_prev", False))

    if muj == 0 and prev_have:
        # 무장 사라진 순간 — cast.
        ctx.extras["attacker_mujang_have_prev"] = False
        return CastRequest(name="mujang", priority=50)

    # -1 (미관측) 은 prev 갱신 보류.
    if muj != -1:
        ctx.extras["attacker_mujang_have_prev"] = have_now
    return None
