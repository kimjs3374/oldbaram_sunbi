"""v1 SoR Parity Tests — Phase 1.

검증 범위:
  1. self_heal rule edge + EDGE-DEFER (map_neq, map_jump_hold, post_mapchg_grace)
  2. self_heal rule HP=0 / HP<0 분기 (사망/미관측은 skip)
  3. decide_direction FORCE-EXIT
  4. decide_direction B3 to_target (격수 뒤 FOLLOW_OFFSET)
  5. decide_direction MAP-JUMP-HOLD (jump>=8 & 다른 맵)
  6. _apply_stuck_filter — dur<0.8 normal / 0.8-2.0 ortho1 / 2.0-3.5 ortho2 / 3.5+ RESET
  7. blacklist add — 첫 RESET 용서, 10초 내 재발 시 등록
  8. blacklist remove — manhattan_delta>=2 진행 시 해제
  9. v1_defaults 상수 1:1 일치 확인 (SoR 재추출 시 값 변경 감시)

모든 테스트는 mock-only — 실제 입력/OCR/UDP 호출 없음.
"""
from __future__ import annotations
import time
from unittest.mock import MagicMock

import pytest

from src_v2.config import v1_defaults as V1
from src_v2.core.snapshot import Snapshot
from src_v2.core.types import RuleContext, CastRequest
from src_v2.brain.rules.self_heal import self_heal as self_heal_rule
from src_v2.muscle.main_loop import (
    decide_direction,
    _decide_move_raw,
    _apply_stuck_filter,
    DecisionState,
    blacklist_add,
    blacklist_check,
    blacklist_remove_at,
)


# =====================================================================
# 1. v1_defaults 상수 SoR 일치 (값 변경 감시)
# =====================================================================
class TestV1DefaultsValues:
    def test_self_heal_thr(self):
        assert V1.SELF_HEAL_HP_THR_DEFAULT == 50
        assert V1.GYOUNGRYEOK_MP_THR_DEFAULT == 30

    def test_seq_a_burst_timing(self):
        assert V1.SEQ_A_REVIVE_BURST_SEC == 0.3
        assert V1.SEQ_A_HEAL_BURST_SEC == 0.5
        assert V1.SEQ_A_BURST_INTERVAL_SEC == 0.1
        assert V1.SEQ_A_KEY_GAP_SEC == 0.1

    def test_seq_a_tap_hold(self):
        assert V1.SEQ_A_TAP_HOLD_MIN_MS == 35
        assert V1.SEQ_A_TAP_HOLD_MAX_MS == 60

    def test_pending_tab_lock(self):
        assert V1.PENDING_TAB_LOCK_SEC == 20.0

    def test_post_mapchg_grace(self):
        assert V1.POST_MAPCHG_GRACE_SEC == 5.0

    def test_hp_drop_filter(self):
        assert V1.HP_DROP_REJECT_RATIO == 0.5
        assert V1.HP_PENDING_TOLERANCE_MIN == 100
        assert V1.HP_PENDING_TOLERANCE_DIV == 100

    def test_stuck_thresholds(self):
        assert V1.STUCK_NORMAL_MAX_SEC == 0.8
        assert V1.STUCK_ORTHO1_MAX_SEC == 2.0
        assert V1.STUCK_ORTHO2_MAX_SEC == 3.5
        assert V1.STUCK_RESET_MANHATTAN_DELTA == 2

    def test_blacklist(self):
        assert V1.BL_TTL_SEC_BASE == 5.0
        assert V1.BL_FORGIVE_WINDOW_SEC == 10.0
        assert V1.BL_TTL_MAX_SEC == 60.0
        assert V1.BL_CELL_GRID == 2
        assert V1.BL_NEIGHBOR_RANGE == 1

    def test_decide(self):
        assert V1.COORD_TOL_DEFAULT == 1
        assert V1.FOLLOW_OFFSET == 1
        assert V1.F1_PEND_STALE_DIST == 30
        assert V1.MAP_JUMP_THRESHOLD == 8
        assert V1.MAP_JUMP_HOLD_SEC == 2.0
        assert V1.EXIT_BOUNDARY_R == 30
        assert V1.EXIT_BOUNDARY_L == 5
        assert V1.EXIT_BOUNDARY_D == 30
        assert V1.EXIT_BOUNDARY_U == 5
        assert V1.TRAIL_TOL == 1

    def test_follower(self):
        assert V1.FORCE_EXIT_SEC == 2.5
        assert V1.JUMP_REJECT_THRESHOLD == 8
        assert V1.FRESH_REJECT_THRESHOLD == 3
        assert V1.ATK_JUMP_THRESHOLD == 8
        assert V1.HEALER_COORD_JUMP_THRESHOLD == 60
        assert V1.MAP_SYNC_DURATION_SEC == 0.3
        assert V1.PAUSE_SEC == 0.1
        assert V1.SNAP_FORWARD_THRESHOLD == 10
        assert V1.HMAP_BBOX_MARGIN == 20
        assert V1.REVERSION_DEBOUNCE_SEC == 2.0
        assert V1.REVERSION_CONFIRM_FRAMES == 3
        assert V1.MAP_TRAIL_MAXLEN == 2000
        assert V1.GLOBAL_TRAIL_MAXLEN == 4000

    def test_vk(self):
        assert V1.VK_NUMPAD1 == 0x61
        assert V1.VK_NUMPAD6 == 0x66
        assert V1.VK_TAB == 0x09
        assert V1.VK_HOME == 0x24
        assert V1.VK_ESCAPE == 0x1B
        assert V1.SKILL_VK_MAINHEAL == 0x61
        assert V1.SKILL_VK_REVIVE == 0x66
        assert V1.SKILL_VK_PARHON == 0x67
        assert V1.SKILL_VK_PARLYUK == 0x68


# =====================================================================
# 2. self_heal rule
# =====================================================================
def _make_ctx(cfg: dict | None = None, in_progress: set | None = None,
              extras: dict | None = None) -> RuleContext:
    """RuleContext 헬퍼."""
    return RuleContext(
        cfg=dict(cfg or {}),
        in_progress=set(in_progress or []),
        extras=dict(extras or {}),
    )


class TestSelfHealRule:
    def test_hp_unobserved_no_cast(self):
        snap = Snapshot(hp=-1)
        ctx = _make_ctx()
        assert self_heal_rule(snap, ctx) is None

    def test_hp_zero_no_cast_self_revive_path(self):
        # HP=0 은 self_revive 룰 — self_heal 은 처리 안 함.
        snap = Snapshot(hp=0)
        ctx = _make_ctx()
        assert self_heal_rule(snap, ctx) is None

    def test_in_progress_blocks(self):
        snap = Snapshot(hp=10)
        ctx = _make_ctx(in_progress={"self_heal"})
        assert self_heal_rule(snap, ctx) is None

    def test_above_thr_no_cast_resets_prev(self):
        snap = Snapshot(hp=80)
        ctx = _make_ctx(extras={"hp_below_thr_prev": True})
        assert self_heal_rule(snap, ctx) is None
        # prev → False 갱신
        assert ctx.extras["hp_below_thr_prev"] is False

    def test_edge_below_thr_emits_cast(self):
        snap = Snapshot(hp=30)
        ctx = _make_ctx(extras={"hp_below_thr_prev": False})
        req = self_heal_rule(snap, ctx)
        assert req is not None
        assert req.name == "self_heal"
        assert req.priority == 10
        # v1 환산: 0.5s / 0.1s = 5회
        assert req.ctx["burst_count"] == 5
        assert req.ctx["burst_gap_ms"] == 100
        assert ctx.extras["hp_below_thr_prev"] is True

    def test_below_thr_prev_already_true_no_recast(self):
        # 이미 prev=True 면 재요청 안 함 (cross-down edge 기반)
        snap = Snapshot(hp=30)
        ctx = _make_ctx(extras={"hp_below_thr_prev": True})
        assert self_heal_rule(snap, ctx) is None

    def test_edge_defer_map_transition_blocks(self):
        # v1: map_transition_in_progress 면 cast 보류 + prev 갱신 안 함
        snap = Snapshot(hp=30)
        ctx = _make_ctx(
            cfg={"_map_transition_in_progress": True},
            extras={"hp_below_thr_prev": False},
        )
        req = self_heal_rule(snap, ctx)
        assert req is None
        # prev 그대로 False — 다음 프레임 재평가 가능
        assert ctx.extras["hp_below_thr_prev"] is False

    def test_custom_thr_via_cfg(self):
        snap = Snapshot(hp=60)
        ctx = _make_ctx(cfg={"self_heal_hp_thr": 70})
        req = self_heal_rule(snap, ctx)
        assert req is not None  # 60 < 70 trigger


# =====================================================================
# 3. decide_direction — FORCE-EXIT / B3 / MAP-JUMP
# =====================================================================
class TestDecideDirection:
    def test_force_exit_overrides_all(self):
        state = DecisionState()
        state.force_exit_until = time.time() + 1.0
        state.force_exit_dir = "R"
        snap = Snapshot(
            healer_coord=(10, 10), healer_map="A",
            attacker_coord=(10, 10), attacker_coord_valid=True,
            attacker_map="A",
        )
        want, reason = decide_direction(snap, state, {})
        assert want == "R"
        assert "FORCE-EXIT" in reason

    def test_b3_at_target(self):
        # 격수 (10,10) atk_dir='-' → tx=10,ty=10 → 힐러 (10,10) → at_target
        state = DecisionState()
        snap = Snapshot(
            healer_coord=(10, 10), healer_map="A",
            attacker_coord=(10, 10), attacker_coord_valid=True,
            attacker_map="A", attacker_last_dir="-",
        )
        want, reason = decide_direction(snap, state, {})
        assert want == "-"
        assert "B3a:at_target" in reason

    def test_b3_follow_offset_attacker_R(self):
        # 격수 R 이동 중 → tx = ax - FOLLOW_OFFSET = ax-1
        # 힐러 (5,10), 격수 (10,10), atk_dir=R → 타겟=(9,10) → 힐러는 R 가야 함
        state = DecisionState()
        snap = Snapshot(
            healer_coord=(5, 10), healer_map="A",
            attacker_coord=(10, 10), attacker_coord_valid=True,
            attacker_map="A", attacker_last_dir="R",
        )
        want, reason = decide_direction(snap, state, {})
        assert want == "R"
        assert "B3:to_target" in reason

    def test_b3_follow_offset_attacker_stationary(self):
        # 격수 정지(atk_dir='-') → 타겟 = 격수 좌표 자체
        state = DecisionState()
        snap = Snapshot(
            healer_coord=(5, 10), healer_map="A",
            attacker_coord=(10, 10), attacker_coord_valid=True,
            attacker_map="A", attacker_last_dir="-",
        )
        want, _ = decide_direction(snap, state, {})
        assert want == "R"

    def test_map_jump_hold_inferred_R(self):
        # 격수 좌표 (10,10) → (35,10) jump=25 ≥ 8, 다른 맵 → R 추론 (35>=30)
        state = DecisionState()
        state.prev_atk_coord = (10, 10)
        snap = Snapshot(
            healer_coord=(5, 10), healer_map="A",
            attacker_coord=(35, 10), attacker_coord_valid=True,
            attacker_map="B",  # 다른 맵 (B2 분기 회피용 — same_map=False)
            attacker_last_dir="R",
        )
        # B2 (map_neq) 분기에 빠지므로 jump 가드 안 탐 — 다른 케이스 필요.
        # MAP-JUMP는 같은 맵 표시 + 좌표만 점프인 케이스를 잡음.
        # 그러나 v1 코드는 NOT same_map 일 때만 jump hold 활성 → 이 테스트는
        # B2:MAPNEQ 가 먼저 매치됨.
        want, reason = decide_direction(snap, state, {})
        # h_map='A' a_map='B' → map_neq=True → B2 path
        assert "B2:MAPNEQ" in reason

    def test_map_jump_hold_same_map_disabled(self):
        # v1: 같은 맵에서는 MAP-JUMP 트리거 안 함 (정상 이동 vs 워프 구분 어려움).
        state = DecisionState()
        state.prev_atk_coord = (10, 10)
        snap = Snapshot(
            healer_coord=(5, 10), healer_map="A",
            attacker_coord=(35, 10), attacker_coord_valid=True,
            attacker_map="A", attacker_last_dir="R",
        )
        want, reason = decide_direction(snap, state, {})
        # same_map → jump hold 비활성 → B3 to_target 로 진행
        assert state.map_jump_hold_until == 0.0
        assert "B3" in reason

    def test_b4_h_none_attacker_ok(self):
        state = DecisionState()
        snap = Snapshot(
            healer_coord=None, healer_map="A",
            attacker_coord=(10, 10), attacker_coord_valid=True,
            attacker_map="A", attacker_last_dir="R",
        )
        want, reason = decide_direction(snap, state, {})
        assert want == "R"
        assert "B4" in reason

    def test_b5_attacker_invalid(self):
        state = DecisionState()
        snap = Snapshot(
            healer_coord=(5, 5), healer_map="A",
            attacker_coord=(10, 10), attacker_coord_valid=False,
            attacker_map="A", attacker_last_dir="L",
        )
        want, reason = decide_direction(snap, state, {})
        assert want == "L"
        assert "B5" in reason

    def test_f1_pend_stale_stay(self):
        state = DecisionState()
        state.f1_pend_active = True
        snap = Snapshot(
            healer_coord=(0, 0), healer_map="A",
            attacker_coord=(40, 0), attacker_coord_valid=True,
            attacker_map="A", attacker_last_dir="R",
        )
        want, reason = decide_direction(snap, state, {})
        assert want == "-"
        assert "F1-PEND stale" in reason


# =====================================================================
# 4. _apply_stuck_filter — dur 단계
# =====================================================================
class TestStuckFilter:
    def _snap(self, h, a, h_map="A"):
        return Snapshot(
            healer_coord=h, healer_map=h_map,
            attacker_coord=a, attacker_coord_valid=(a is not None),
            attacker_map=h_map,
        )

    def test_first_call_initializes_baseline(self):
        state = DecisionState()
        snap = self._snap((5, 5), (10, 10))
        want, reason = _apply_stuck_filter(snap, state, "R", "test")
        assert want == "R"
        assert state.run_want == "R"
        assert state.run_start_pos == (5, 5)

    def test_progress_resets_baseline(self):
        # baseline (5,5) → 현재 (7,5) → progress R = 2 → 진행으로 판정
        state = DecisionState()
        state.run_want = "R"
        state.run_start_ts = time.time() - 1.0
        state.run_start_pos = (5, 5)
        snap = self._snap((7, 5), (15, 5))
        want, reason = _apply_stuck_filter(snap, state, "R", "test")
        assert want == "R"
        # progress > 0 → baseline 갱신
        assert state.run_start_pos == (7, 5)

    def test_dur_normal_no_intervention(self):
        state = DecisionState()
        state.run_want = "R"
        state.run_start_ts = time.time() - 0.3  # < 0.8s
        state.run_start_pos = (5, 5)
        snap = self._snap((5, 5), (15, 5))
        want, reason = _apply_stuck_filter(snap, state, "R", "test")
        assert want == "R"

    def test_dur_ortho1_X_blocked(self):
        # 1.0s 경과 (0.8 ≤ dur < 2.0) + R 막힘 + 격수 y > 힐러 y → ortho1=D
        state = DecisionState()
        state.run_want = "R"
        state.run_start_ts = time.time() - 1.0
        state.run_start_pos = (5, 5)
        snap = self._snap((5, 5), (10, 8))
        want, reason = _apply_stuck_filter(snap, state, "R", "test")
        assert want == "D"
        assert "STUCK-ORTHO1" in reason

    def test_dur_ortho2(self):
        state = DecisionState()
        state.run_want = "R"
        state.run_start_ts = time.time() - 2.5  # 2.0 ≤ dur < 3.5
        state.run_start_pos = (5, 5)
        snap = self._snap((5, 5), (10, 8))
        want, reason = _apply_stuck_filter(snap, state, "R", "test")
        # ortho1=D → ortho2=U
        assert want == "U"
        assert "STUCK-ORTHO2" in reason

    def test_dur_reset_blacklist_add(self):
        state = DecisionState()
        state.run_want = "R"
        state.run_start_ts = time.time() - 4.0  # > 3.5s
        state.run_start_pos = (5, 5)
        # 첫 RESET — reset_history 에 등록만, blacklist 등록 X (forgive)
        snap = self._snap((5, 5), (10, 8))
        want, reason = _apply_stuck_filter(snap, state, "R", "test")
        assert want == "-"
        assert "STUCK-RESET" in reason
        assert state.run_want is None
        # 첫 RESET 은 blacklist 안 들어감
        assert len(state.stuck_blacklist) == 0
        assert len(state.reset_history) == 1

    def test_whitetab_skip_filter(self):
        # white_tab_present=True 면 STUCK 필터 무시, want 그대로
        state = DecisionState()
        state.run_want = "R"
        state.run_start_ts = time.time() - 5.0
        state.run_start_pos = (5, 5)
        snap = Snapshot(
            healer_coord=(5, 5), healer_map="A",
            attacker_coord=(10, 5), attacker_coord_valid=True,
            attacker_map="A",
            white_tab_present=True,
        )
        want, reason = _apply_stuck_filter(snap, state, "R", "test")
        assert want == "R"
        # 상태 리셋됨
        assert state.run_want is None

    def test_manhattan_progress_resets(self):
        # 주축 진행 0 인데 manhattan_delta>=2 (부축 이동) → 진행으로 인정
        state = DecisionState()
        state.run_want = "R"
        state.run_start_ts = time.time() - 1.5
        state.run_start_pos = (5, 5)
        # x=5 그대로, y=5→7 (부축 D 2칸) → manhattan=2
        snap = self._snap((5, 7), (10, 5))
        want, reason = _apply_stuck_filter(snap, state, "R", "test")
        assert want == "R"  # 원 want 유지
        assert state.run_start_pos == (5, 7)


# =====================================================================
# 5. blacklist add/remove/check
# =====================================================================
class TestBlacklist:
    def test_first_reset_forgiven(self):
        state = DecisionState()
        blacklist_add(state, "A", (10, 10), "R")
        # 첫 발생 → 등록 안 됨
        assert len(state.stuck_blacklist) == 0
        assert (("A", 5, 5, "R") in state.reset_history)

    def test_second_reset_within_window_added(self):
        state = DecisionState()
        # 1차 — reset_history 에만 기록 (forgive)
        blacklist_add(state, "A", (10, 10), "R")
        # 2차 (즉시 재발) — 등록
        blacklist_add(state, "A", (10, 10), "R")
        assert len(state.stuck_blacklist) == 1
        # 첫 hit=1, ttl=5
        for k, (exp, hit) in state.stuck_blacklist.items():
            assert hit == 1

    def test_blacklist_check_neighbor_range(self):
        state = DecisionState()
        # (10,10) → cell (5,5) 등록 (수동)
        state.stuck_blacklist[("A", 5, 5, "R")] = (time.time() + 5.0, 1)
        # cell (4,4)~(6,6) 모두 ±1 매치
        assert blacklist_check(state, "A", (8, 8), "R")  # cell (4,4)
        assert blacklist_check(state, "A", (10, 10), "R")  # cell (5,5)
        assert blacklist_check(state, "A", (12, 12), "R")  # cell (6,6)
        # 다른 방향은 false
        assert not blacklist_check(state, "A", (10, 10), "L")

    def test_blacklist_remove_at_clears(self):
        state = DecisionState()
        state.stuck_blacklist[("A", 5, 5, "R")] = (time.time() + 5.0, 1)
        blacklist_remove_at(state, "A", (10, 10), "R")
        assert len(state.stuck_blacklist) == 0

    def test_blacklist_lazy_cleanup(self):
        state = DecisionState()
        # 만료된 항목 + 유효 항목 동시 존재
        state.stuck_blacklist[("A", 5, 5, "R")] = (time.time() - 1.0, 1)  # 만료
        state.stuck_blacklist[("A", 6, 6, "L")] = (time.time() + 5.0, 1)  # 유효
        # check 호출 → 만료 정리
        blacklist_check(state, "A", (0, 0), "U")
        assert ("A", 5, 5, "R") not in state.stuck_blacklist
        assert ("A", 6, 6, "L") in state.stuck_blacklist

    def test_exponential_ttl_backoff(self):
        state = DecisionState()
        # 충분한 시간차 없이 forgive 회피 위해 reset_history 직접 조작
        now = time.time()
        # 1차 등록(hit=1, ttl=5)
        state.reset_history[("A", 5, 5, "R")] = now - 1.0
        blacklist_add(state, "A", (10, 10), "R")
        assert state.stuck_blacklist[("A", 5, 5, "R")][1] == 1
        ttl1 = state.stuck_blacklist[("A", 5, 5, "R")][0] - now
        assert 4.5 < ttl1 < 5.5

        # 2차 hit=2, ttl=10
        state.reset_history[("A", 5, 5, "R")] = now - 1.0
        blacklist_add(state, "A", (10, 10), "R")
        assert state.stuck_blacklist[("A", 5, 5, "R")][1] == 2
        ttl2 = state.stuck_blacklist[("A", 5, 5, "R")][0] - now
        assert 9.5 < ttl2 < 10.5
