"""SEQ-RCLICK rule — self_heal/self_revive 가 시작될 때 우클릭 sub-loop 동시 시전.

v1 SoR (healer_worker.py:920-983):
  자힐 진입 시점에 YOLO 빨탭 detection 좌표를 저장하고, SEQ-A 진행 동안
  그 좌표에 우클릭을 0.5s 간격으로 burst → 격수 자동 회복 트리거.
  v2 에선 self_heal_seq 가 worker_state['_seq_rclick_target'] 에 좌표 저장.

이 룰은 hand.cast_done 토픽을 받아 직전 cast 가 self_heal/self_revive 일 때만
seq_rclick 시퀀스를 큐에 푸시.
"""
from __future__ import annotations
from typing import Optional

from ...core.plugin_registry import rule
from ...core.snapshot import Snapshot
from ...core.types import CastRequest, RuleContext


_TARGET_NAMES = ("self_heal", "self_revive")


@rule(name="seq_rclick", priority=50, topics=["hand.cast_done"],
      description="SEQ-RCLICK — self_heal/self_revive cast_done 후 격수 우클릭")
def seq_rclick(snap: Snapshot, ctx: RuleContext) -> Optional[CastRequest]:
    if "seq_rclick" in ctx.in_progress:
        return None
    if not ctx.cfg.get("seq_rclick_enabled", True):
        return None
    # extras["last_cast_done_name"] 로 직전 cast 이름 게이트 (RuleEngine wiring).
    last_name = str(ctx.extras.get("last_cast_done_name", "") or "")
    if last_name not in _TARGET_NAMES:
        return None
    # 빨탭 detection — self_heal_seq 가 worker_state 에 저장한 좌표 우선.
    pos = ctx.extras.get("last_seq_rclick_target")
    if pos is None:
        # fallback: 현재 snap 의 red_tab_pos.
        if not snap.red_tab_present or snap.red_tab_pos is None:
            return None
        pos = snap.red_tab_pos
    return CastRequest(
        name="seq_rclick",
        priority=50,
        ctx={
            "red_tab_pos": pos,
            "duration_ms": ctx.cfg.get("seq_rclick_duration_ms", 1500),
            "interval_ms": ctx.cfg.get("seq_rclick_interval_ms", 500),
        },
    )
