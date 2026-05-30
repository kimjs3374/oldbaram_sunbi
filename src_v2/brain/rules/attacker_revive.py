"""격수부활 룰 — v1 1:1 (healer_worker.py:1545-1551).

v1 트리거:
  atk_dead_now = (atk_hp == 0 and self_hp_now > 0)
  prev=False → cast + prev=True (edge cross-down)

힐러가 살아있어야 함 (자기 죽으면 자가부활 우선).
attacker_hp -1 (UDP 미수신) 인 경우 prev 갱신 보류.

2026-05-05 Cycle 2 (Task 7) — 단발 0 튐 필터:
  메모리 4-25 세션: "격수 HP UDP 단발 0 1프레임 튐으로 격수부활 트리거.
  9회 오시전 → 전부 self-target 에 들어가 효과 없음."
  격수 PC OCR 의 1프레임 자릿수 누락 → atk_hp=0 단발 송신 가능.
  → 2 tick 연속 dead 확인된 경우만 fire. tick 사이 dead 끊기면 count reset.
  UDP 30Hz 기준 두 packet 연속 hp=0 = 약 66ms 동안 사망 확정.

ctx.extras keys:
  attacker_dead_prev (bool)
  attacker_dead_count (int) — 연속 dead tick 수 (2026-05-05 추가)
"""
from __future__ import annotations
from typing import Optional

from ...core.plugin_registry import rule
from ...core.snapshot import Snapshot
from ...core.types import CastRequest, RuleContext


# 2 tick 연속 dead 확인 임계 (단발 0 차단). v1 30Hz 환경 기준 약 66ms.
_DEAD_CONFIRM_TICKS = 2


@rule(
    name="attacker_revive",
    priority=2,
    topics=["eye.attacker_state", "eye.hp"],
    description="격수부활 (격수 HP=0 + 힐러 살아있음 edge, 2-tick 단발 필터)",
)
def attacker_revive(snap: Snapshot, ctx: RuleContext) -> Optional[CastRequest]:
    if "attacker_revive" in ctx.in_progress:
        return None

    atk_hp = int(snap.attacker_hp)
    self_hp = int(snap.hp)

    # 격수 HP 미관측 시 prev/count 갱신 보류 (v1 -1 처리). UDP stall 중에도
    # streak 유지. 회복 시점에 hp=0 다시 들어오면 streak 이어짐.
    if atk_hp < 0:
        return None

    atk_dead_now = (atk_hp == 0 and self_hp > 0)
    prev = bool(ctx.extras.get("attacker_dead_prev", False))
    dead_count = int(ctx.extras.get("attacker_dead_count", 0) or 0)

    if not atk_dead_now:
        # 살아있음 (또는 self_hp=0 자기 사망 케이스 — self_revive 우선 처리).
        # streak 끊김 → count reset, prev=False.
        ctx.extras["attacker_dead_prev"] = False
        ctx.extras["attacker_dead_count"] = 0
        return None

    # atk_dead_now = True. streak count 누적.
    dead_count += 1
    ctx.extras["attacker_dead_count"] = dead_count

    # 단발 0 튐 차단: 2 tick 미만은 fire 보류.
    if dead_count < _DEAD_CONFIRM_TICKS:
        return None

    # edge fire: prev=False & 연속 confirm 통과.
    if not prev:
        # EDGE-DEFER 보호 (맵 전환 중 격수 hp 노이즈 가능)
        if bool(ctx.cfg.get("_map_transition_in_progress", False)):
            return None
        ctx.extras["attacker_dead_prev"] = True
        return CastRequest(name="attacker_revive", priority=2)
    return None
