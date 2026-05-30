"""Phase 2 parity tests — v1 1:1 통합 8 룰 + 시퀀스 + Follower.

각 룰의 edge cross-down/up 패턴 + EDGE-DEFER + 미관측(-1) 처리 검증.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

# repo 루트 sys.path 보장 (pytest 작업 디렉토리 환경 차이 흡수).
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src_v2.core.snapshot import Snapshot
from src_v2.core.types import RuleContext
from src_v2.brain.rules import (
    self_revive as r_self_revive,
    attacker_revive as r_atk_revive,
    parhon as r_parhon,
    gyoungryeok as r_gyo,
    baekho as r_baekho,
    parlyuk as r_parlyuk,
    mujang as r_mujang,
    boho as r_boho,
)


def _ctx(**cfg):
    return RuleContext(cfg=dict(cfg), in_progress=set(), extras={})


# ----------------------------------------------------------------------- #
# self_revive — HP=0 cross-down edge + EDGE-DEFER
# ----------------------------------------------------------------------- #
class TestSelfRevive:
    def test_hp_minus1_no_trigger(self):
        snap = Snapshot(hp=-1)
        assert r_self_revive.self_revive(snap, _ctx()) is None

    def test_alive_no_trigger(self):
        snap = Snapshot(hp=80)
        assert r_self_revive.self_revive(snap, _ctx()) is None

    def test_dead_first_edge_triggers(self):
        snap = Snapshot(hp=0)
        ctx = _ctx()
        req = r_self_revive.self_revive(snap, ctx)
        assert req is not None
        assert req.name == "self_revive"
        assert ctx.extras["self_dead_prev"] is True

    def test_dead_second_call_no_retrigger(self):
        snap = Snapshot(hp=0)
        ctx = _ctx()
        ctx.extras["self_dead_prev"] = True
        assert r_self_revive.self_revive(snap, ctx) is None

    def test_edge_defer_during_map_transition(self):
        snap = Snapshot(hp=0)
        ctx = _ctx(_map_transition_in_progress=True)
        # 보류 — prev 갱신 안 함, cast 안 보냄.
        assert r_self_revive.self_revive(snap, ctx) is None
        assert ctx.extras.get("self_dead_prev", False) is False

    def test_in_progress_blocked(self):
        snap = Snapshot(hp=0)
        ctx = RuleContext(in_progress={"self_revive"})
        assert r_self_revive.self_revive(snap, ctx) is None


# ----------------------------------------------------------------------- #
# attacker_revive — atk_hp=0 + self_hp>0 edge
# ----------------------------------------------------------------------- #
class TestAttackerRevive:
    def test_unobserved(self):
        snap = Snapshot(attacker_hp=-1, hp=80)
        assert r_atk_revive.attacker_revive(snap, _ctx()) is None

    def test_atk_alive_no_trigger(self):
        snap = Snapshot(attacker_hp=50, hp=80)
        assert r_atk_revive.attacker_revive(snap, _ctx()) is None

    def test_self_dead_no_trigger(self):
        snap = Snapshot(attacker_hp=0, hp=0)
        assert r_atk_revive.attacker_revive(snap, _ctx()) is None

    def test_atk_dead_self_alive_edge(self):
        snap = Snapshot(attacker_hp=0, hp=80)
        ctx = _ctx()
        req = r_atk_revive.attacker_revive(snap, ctx)
        assert req is not None
        assert req.name == "attacker_revive"

    def test_atk_dead_no_retrigger(self):
        snap = Snapshot(attacker_hp=0, hp=80)
        ctx = _ctx()
        ctx.extras["attacker_dead_prev"] = True
        assert r_atk_revive.attacker_revive(snap, ctx) is None


# ----------------------------------------------------------------------- #
# parhon — debuff_honma_sec edge
# ----------------------------------------------------------------------- #
class TestParhon:
    def test_unobserved(self):
        snap = Snapshot(attacker_honma_sec=-1)
        assert r_parhon.parhon(snap, _ctx()) is None

    def test_no_honma(self):
        snap = Snapshot(attacker_honma_sec=0)
        assert r_parhon.parhon(snap, _ctx()) is None

    def test_honma_first_edge(self):
        snap = Snapshot(attacker_honma_sec=10)
        ctx = _ctx()
        req = r_parhon.parhon(snap, ctx)
        assert req is not None
        assert req.name == "parhon"
        assert ctx.extras["attacker_honma_prev"] is True

    def test_honma_no_retrigger(self):
        snap = Snapshot(attacker_honma_sec=8)
        ctx = _ctx()
        ctx.extras["attacker_honma_prev"] = True
        assert r_parhon.parhon(snap, ctx) is None

    def test_disabled(self):
        snap = Snapshot(attacker_honma_sec=10)
        assert r_parhon.parhon(snap, _ctx(parhon_enabled=False)) is None


# ----------------------------------------------------------------------- #
# gyoungryeok — MP < thr edge + allow_hp_drop_for ctx hint
# ----------------------------------------------------------------------- #
class TestGyoungryeok:
    def test_unobserved(self):
        snap = Snapshot(mp=-1)
        assert r_gyo.gyoungryeok(snap, _ctx()) is None

    def test_buff_active_no_trigger(self):
        snap = Snapshot(mp=10, buff_gyoungryeok_active=True)
        assert r_gyo.gyoungryeok(snap, _ctx()) is None

    def test_above_thr_no_trigger(self):
        snap = Snapshot(mp=80)
        assert r_gyo.gyoungryeok(snap, _ctx(gyoungryeok_mp_thr=30)) is None

    def test_below_thr_first_edge_emits_drop_hint(self):
        snap = Snapshot(mp=20)
        ctx = _ctx(gyoungryeok_mp_thr=30)
        req = r_gyo.gyoungryeok(snap, ctx)
        assert req is not None
        assert req.name == "gyoungryeok"
        assert "allow_hp_drop_sec" in req.ctx
        assert req.ctx["allow_hp_drop_sec"] == 5.0

    def test_below_no_retrigger(self):
        snap = Snapshot(mp=10)
        ctx = _ctx(gyoungryeok_mp_thr=30)
        ctx.extras["mp_below_thr_prev"] = True
        assert r_gyo.gyoungryeok(snap, ctx) is None


# ----------------------------------------------------------------------- #
# baekho — cd_baekho==0 ready edge
# ----------------------------------------------------------------------- #
class TestBaekho:
    def test_unobserved(self):
        snap = Snapshot(cd_baekho=-1)
        assert r_baekho.baekho(snap, _ctx()) is None

    def test_cd_remaining(self):
        snap = Snapshot(cd_baekho=5)
        assert r_baekho.baekho(snap, _ctx()) is None

    def test_buff_active(self):
        snap = Snapshot(cd_baekho=0, buff_baekho_active=True)
        assert r_baekho.baekho(snap, _ctx()) is None

    def test_cd_zero_first_edge(self):
        snap = Snapshot(cd_baekho=0)
        ctx = _ctx()
        req = r_baekho.baekho(snap, ctx)
        assert req is not None
        assert req.name == "baekho"

    def test_no_retrigger(self):
        snap = Snapshot(cd_baekho=0)
        ctx = _ctx()
        ctx.extras["baekho_ready_prev"] = True
        assert r_baekho.baekho(snap, ctx) is None


# ----------------------------------------------------------------------- #
# parlyuk — cd<=offset ready edge + force_coord_tol=1 ctx hint
# ----------------------------------------------------------------------- #
class TestParlyuk:
    def test_buff_active_no_trigger(self):
        snap = Snapshot(cd_parlyuk=0, buff_parlyuk_active=True)
        assert r_parlyuk.parlyuk(snap, _ctx()) is None

    def test_cd_remaining(self):
        snap = Snapshot(cd_parlyuk=10)
        assert r_parlyuk.parlyuk(snap, _ctx(parlyuk_offset_sec=2)) is None

    def test_offset_within_window(self):
        snap = Snapshot(cd_parlyuk=2)
        ctx = _ctx(parlyuk_offset_sec=2)
        req = r_parlyuk.parlyuk(snap, ctx)
        assert req is not None
        assert req.ctx["force_coord_tol"] == 1

    def test_zero_offset_zero_cd_edge(self):
        snap = Snapshot(cd_parlyuk=0)
        req = r_parlyuk.parlyuk(snap, _ctx(parlyuk_offset_sec=0))
        assert req is not None


# ----------------------------------------------------------------------- #
# mujang — buff_mujang_sec==0 (확정) edge
# ----------------------------------------------------------------------- #
class TestMujang:
    def test_unobserved_no_state_change(self):
        # -1 → prev 갱신 보류
        snap = Snapshot(attacker_mujang_sec=-1)
        ctx = _ctx()
        ctx.extras["attacker_mujang_have_prev"] = True
        assert r_mujang.mujang(snap, ctx) is None
        assert ctx.extras["attacker_mujang_have_prev"] is True

    def test_buff_present_updates_prev(self):
        snap = Snapshot(attacker_mujang_sec=30)
        ctx = _ctx()
        assert r_mujang.mujang(snap, ctx) is None
        assert ctx.extras["attacker_mujang_have_prev"] is True

    def test_buff_disappears_triggers(self):
        snap = Snapshot(attacker_mujang_sec=0)
        ctx = _ctx()
        ctx.extras["attacker_mujang_have_prev"] = True
        req = r_mujang.mujang(snap, ctx)
        assert req is not None
        assert req.name == "mujang"
        assert ctx.extras["attacker_mujang_have_prev"] is False

    def test_zero_no_prev_no_trigger(self):
        snap = Snapshot(attacker_mujang_sec=0)
        ctx = _ctx()  # prev=False
        # 0 + prev=False → cast 안 함 (없는 채로 시작 = trigger 아님).
        assert r_mujang.mujang(snap, ctx) is None

    def test_disabled(self):
        snap = Snapshot(attacker_mujang_sec=0)
        ctx = _ctx(mujang_enabled=False)
        ctx.extras["attacker_mujang_have_prev"] = True
        assert r_mujang.mujang(snap, ctx) is None


# ----------------------------------------------------------------------- #
# boho — 무장과 동일 패턴
# ----------------------------------------------------------------------- #
class TestBoho:
    def test_unobserved(self):
        snap = Snapshot(attacker_boho_sec=-1)
        ctx = _ctx()
        ctx.extras["attacker_boho_have_prev"] = True
        assert r_boho.boho(snap, ctx) is None
        assert ctx.extras["attacker_boho_have_prev"] is True

    def test_disappears_triggers(self):
        snap = Snapshot(attacker_boho_sec=0)
        ctx = _ctx()
        ctx.extras["attacker_boho_have_prev"] = True
        req = r_boho.boho(snap, ctx)
        assert req is not None
        assert req.name == "boho"


# ----------------------------------------------------------------------- #
# Sequence import smoke
# ----------------------------------------------------------------------- #
class TestSequencesImport:
    def test_all_sequences_registered(self):
        from src_v2.core.plugin_registry import PluginRegistry
        from src_v2.hands import sequences  # noqa: F401  ensure import
        names = {"self_heal", "self_revive", "attacker_revive",
                 "parhon", "gyoungryeok", "baekho", "parlyuk",
                 "mujang", "boho", "tab_lock", "seq_rclick"}
        registered = {s.name for s in PluginRegistry.get_sequences()}
        missing = names - registered
        assert not missing, f"missing sequences: {missing}"


# ----------------------------------------------------------------------- #
# Follower wrapper import smoke (v1 모듈 사용 가능 시 v1 인스턴스).
# ----------------------------------------------------------------------- #
class TestFollowerWrapper:
    def test_make_follower_returns_object(self):
        from src_v2.brain.follower import make_follower
        f = make_follower()
        assert f is not None
        # 필수 메서드들 호출 가능 여부.
        assert callable(getattr(f, "update", None))
        assert callable(getattr(f, "force_exit_active", None))
        assert callable(getattr(f, "next_waypoint", None))
        assert callable(getattr(f, "is_paused", None))
        assert callable(getattr(f, "tab_confirm_tick", None))

    def test_adapt_state_handles_none(self):
        from src_v2.brain.follower import adapt_state
        s = adapt_state(None)
        assert s is not None


# ----------------------------------------------------------------------- #
# v1_defaults 신규 추가 magic numbers
# ----------------------------------------------------------------------- #
class TestV1DefaultsExtensions:
    def test_phase2_magics_exist(self):
        from src_v2.config import v1_defaults as V1
        # 신규 추가 항목.
        for name in (
            "GYOUNGRYEOK_HP_DROP_ALLOW_SEC",
            "PARHON_BURST_SEC", "PARHON_BURST_INTERVAL_SEC",
            "MUJANG_SHIFT_HOLD_MS", "MUJANG_KEY_GAP_SEC",
            "BOHO_SHIFT_HOLD_MS", "BOHO_KEY_GAP_SEC",
            "BAEKHO_INTER_KEY_GAP_MS",
            "ATTACKER_REVIVE_BURST_SEC",
            "SELF_REVIVE_BURST_SEC",
            "TAB_CONFIRM_HARD_TIMEOUT", "TAB_CONFIRM_REQUIRED_FRAMES",
            "TAB_LOCK_DIST_THR", "TAB_LOCK_STABILIZE_SEC",
            "SKILL_SCHED_POLL_SEC",
        ):
            assert hasattr(V1, name), f"v1_defaults missing {name}"
