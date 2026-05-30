"""공력증강 룰 — v1 1:1 (healer_worker.py:1228-1245).

v1 트리거:
  thr_mp = self.gyoungryeok_mp_thr
  mp_below_now = (0 <= mp < thr_mp)
  prev=False → cast + prev=True
  cast 직후 hpmp.allow_hp_drop_for(5.0) 호출 (HP 60% 감소 허용 윈도우).

ctx.extras key: mp_below_thr_prev (bool)
hpmp drop allow 호출은 worker_state hook 으로 위임 (sequence 가 trigger).
"""
from __future__ import annotations
from typing import Optional

from ...core.plugin_registry import rule
from ...core.snapshot import Snapshot
from ...core.types import CastRequest, RuleContext
from ...config import v1_defaults as V1


def _diag_once(ctx: "RuleContext", reason: str) -> None:
    """첫 차단 원인 1회 emit — 다음 로그에서 root 즉시 확정."""
    seen = ctx.extras.setdefault("_gyoungryeok_diag_reasons", set())
    if reason in seen:
        return
    seen.add(reason)
    import logging
    logging.getLogger("src_v2.brain.rules.gyoungryeok").warning(
        "[GYOUNG-DIAG] block reason=%s (1회만 emit)", reason
    )


@rule(
    name="gyoungryeok",
    priority=20,
    topics=["eye.mp"],
    description="공력증강 (MP 임계 cross-down edge)",
)
def gyoungryeok(snap: Snapshot, ctx: RuleContext) -> Optional[CastRequest]:
    if "gyoungryeok" in ctx.in_progress:
        _diag_once(ctx, "in_progress_stuck")
        return None
    if not ctx.cfg.get("gyoungryeok_enabled", True):
        _diag_once(ctx, "cfg_disabled")
        return None
    if snap.buff_gyoungryeok_active:
        _diag_once(ctx, "buff_active_stuck")
        return None

    mp = int(snap.mp)
    if mp < 0:
        _diag_once(ctx, "mp_negative")
        return None
    thr = int(ctx.cfg.get("gyoungryeok_mp_thr", V1.GYOUNGRYEOK_MP_THR_DEFAULT))
    mp_below_now = (mp < thr)
    prev = bool(ctx.extras.get("mp_below_thr_prev", False))

    if not mp_below_now:
        ctx.extras["mp_below_thr_prev"] = False
        return None

    if mp_below_now and not prev:
        ctx.extras["mp_below_thr_prev"] = True
        return CastRequest(
            name="gyoungryeok",
            priority=20,
            ctx={"allow_hp_drop_sec": V1.GYOUNGRYEOK_HP_DROP_ALLOW_SEC},
        )
    # mp<thr 인데 prev=True 라 차단 — edge 한 번 fire 됐으면 buff active 까지 lock.
    # buff 가 안 풀리면 영원 차단. 실측 신호.
    _diag_once(ctx, "prev_locked_no_edge")
    return None
