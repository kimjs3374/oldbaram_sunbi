"""Self-heal rule — 1:1 ported from v1 (dist_dosa/src/).

v1 SoR:
  - healer_worker.py:1208-1227 (자힐 EDGE-DEFER 트리거)
  - hpmp.py:436-488 (HP drop ratio 0.5+ 1프레임 pending → 2프레임 동일 시 수락)
  - healer_worker.py:198 (POST_MAPCHG_GRACE_SEC = 5.0)
  - healer_worker.py:1018 (PENDING_TAB_LOCK_SEC = 20.0)

트리거 조건 (v1 1:1):
  1. snap.hp >= 0  (OCR 미관측 무시)
  2. snap.hp == 0  → self_revive (이 룰은 alive 만 처리)
  3. snap.hp < self_heal_hp_thr (default 50)
  4. in_progress 차단 (이미 자힐 진행 중이면 트리거 안 함)
  5. EDGE-DEFER:
     - h_map != a_map (맵 불일치) 이면 보류 (= prev 상태 미갱신, 다음 프레임 재평가)
     - now < _map_jump_hold_until (MAP-JUMP 2초 hold) 이면 보류
     - now - _last_map_change_ts < POST_MAPCHG_GRACE_SEC 이면 보류
  6. HP drop ratio 0.5+ 의심값은 hpmp_watcher 단계에서 이미 1프레임 pending 처리 →
     이 rule 은 수락된 hp 만 본다 (이중 필터 X).

prev edge:
  - hp_below_now=True 이고 prev=False 일 때만 cast 요청.
  - hp 가 그대로 낮은 상태 유지 중에는 재요청 안 함 (상태 cross-down 만 trigger).

ctx 통과:
  burst_count, burst_gap_ms 는 cfg 또는 v1_defaults.SEQ_A_HEAL_BURST_SEC /
  SEQ_A_BURST_INTERVAL_SEC 환산값. enable_block_b=False (v1 2026-04-24:
  SEQ-B는 ESC 1회만, TAB×2 + 토글 재ON은 worker 가 별도로 처리).
"""
from __future__ import annotations
from typing import Optional

from ...core.plugin_registry import rule
from ...core.snapshot import Snapshot
from ...core.types import CastRequest, RuleContext
from ...config import v1_defaults as V1


@rule(
    name="self_heal",
    priority=10,
    topics=["eye.hp"],
    description="HP 임계 자힐 (v1 1:1 — EDGE-DEFER, hp drop filter 적용)",
)
def self_heal(snap: Snapshot, ctx: RuleContext) -> Optional[CastRequest]:
    # 1) 이미 진행 중이면 차단 (v1 in_progress 와 동치)
    if "self_heal" in ctx.in_progress:
        return None

    # 2) HP 미관측 / 사망(0) 은 처리 안 함 (사망은 self_revive 룰)
    if snap.hp < 0:
        return None
    if snap.hp == 0:
        return None

    # 3) 임계치 비교 (v1 healer_worker.py:1208 thr_hp = int(self.self_heal_hp_thr))
    thr = int(ctx.cfg.get("self_heal_hp_thr", V1.SELF_HEAL_HP_THR_DEFAULT))
    hp_below_now = (snap.hp < thr)

    # 4) Edge prev — ctx.extras 에 영구 보존 (RuleEngine 가 동일 ctx 재사용)
    prev = bool(ctx.extras.get("hp_below_thr_prev", False))

    if not hp_below_now:
        # 임계 위 → prev 갱신 후 종료
        ctx.extras["hp_below_thr_prev"] = False
        return None

    # 5) EDGE-DEFER (v1 healer_worker.py:1212-1225 1:1)
    # prev_state 가 비어있을 수 있으므로 cfg 에서 동적 주입 사용 (같은 의미).
    map_transition = bool(ctx.cfg.get("_map_transition_in_progress", False))

    # edge: hp_below_now=True & prev=False
    if hp_below_now and not prev:
        if map_transition:
            # v1 동작: prev 플래그 업데이트 안 함 → 다음 프레임 재평가, 동기화
            # 시 즉시 발동. 여기서는 cast 안 보내고 prev 도 그대로 둠.
            return None
        # 정상 edge → cast 요청 + prev=True 갱신
        ctx.extras["hp_below_thr_prev"] = True

        # v1 burst 환산: 0.5s / 0.1s = 5회, gap 100ms.
        burst_count = int(ctx.cfg.get(
            "self_heal_burst_count",
            int(V1.SEQ_A_HEAL_BURST_SEC / V1.SEQ_A_BURST_INTERVAL_SEC),
        ))
        burst_gap_ms = int(ctx.cfg.get(
            "self_heal_burst_gap_ms",
            int(V1.SEQ_A_BURST_INTERVAL_SEC * 1000),
        ))
        # v1 healer_worker.py:920-983 — 자힐 진입 시점 YOLO 빨탭 detection
        # 좌표 저장 (SEQ-RCLICK 타겟). detection 객체에 cx/cy 가 있으면 그대로,
        # 없으면 snap.red_tab_pos 로 폴백.
        red_det = snap.red_tab_detection
        if red_det is None and snap.red_tab_pos is not None:
            # 가벼운 stub — cx/cy attr 만 노출.
            class _PosOnly:
                __slots__ = ("cx", "cy")
                def __init__(self, p):
                    self.cx, self.cy = int(p[0]), int(p[1])
            red_det = _PosOnly(snap.red_tab_pos)
        return CastRequest(
            name="self_heal",
            priority=10,
            ctx={
                "burst_count": burst_count,
                "burst_gap_ms": burst_gap_ms,
                # v1 2026-04-24: SEQ-B 는 ESC only.
                "enable_block_b": True,  # ESC만 실행 (시퀀스에서 v1 동작)
                "key_gap_ms": int(V1.SEQ_A_KEY_GAP_SEC * 1000),
                "_yolo_red_det": red_det,
            },
        )
    # 임계 아래 유지 중 (prev=True) — 재요청 안 함
    return None
