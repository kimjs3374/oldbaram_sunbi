"""coord_tol single source of truth 계약 테스트 (v1_gap_fix_list P0-4).

Snap.coord_tol_override 가 truth. -1 이면 cfg.coord_tol 사용, >=0 이면 강제.
integration_tick 이 set, muscle.main_loop._decide_move_raw 가 read.

회귀 가드: rule_cfg dict mutation 의존 폐기 — 격수/룰/muscle 가 다른 ref 보던
race 차단.
"""
from __future__ import annotations
import os, sys, types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


def _make_snap(coord_tol_override: int = -1, h=(10, 10), a=(20, 10), maps=("M", "M")):
    from src_v2.core.snapshot import Snapshot
    s = Snapshot()
    s.coord_tol_override = coord_tol_override
    s.healer_coord = h
    s.healer_map = maps[0]
    s.attacker_coord = a
    s.attacker_map = maps[1]
    s.attacker_coord_valid = True
    return s


def _DecState():
    from src_v2.muscle.main_loop import DecisionState
    return DecisionState()


class _Follower:
    def force_exit_active(self, n=None): return False
    def is_paused(self, n=None): return False
    def exit_dir(self): return "-"
    def force_exit_remaining(self, n=None): return 0.0
    def pause_remaining(self, n=None): return 0.0


def test_override_minus1_uses_cfg():
    """override=-1 → cfg.coord_tol 사용 (기본 동작)."""
    from src_v2.muscle.main_loop import _decide_move_raw
    snap = _make_snap(coord_tol_override=-1, h=(10, 10), a=(13, 10))
    cfg = {"coord_tol": 5}  # tol=5, dx=3 → 정지
    want, reason = _decide_move_raw(snap, _DecState(), cfg, move_hint=None, follower=_Follower())
    assert want == "-", f"tol=5 dx=3 정지 기대, got {want} reason={reason}"


def test_override_forces_value():
    """override=1 → cfg 무시, 강제값 사용."""
    from src_v2.muscle.main_loop import _decide_move_raw
    snap = _make_snap(coord_tol_override=1, h=(10, 10), a=(13, 10))
    cfg = {"coord_tol": 5}  # cfg 만 보면 정지인데 override=1 이라 dx=3>1 → 이동
    want, reason = _decide_move_raw(snap, _DecState(), cfg, move_hint=None, follower=_Follower())
    assert want == "R", f"override=1 dx=3 우→이동 기대, got {want} reason={reason}"


def test_override_zero_treated_as_strict():
    """override=0 → 동좌표만 정지, 1칸 차이도 이동."""
    from src_v2.muscle.main_loop import _decide_move_raw
    snap = _make_snap(coord_tol_override=0, h=(10, 10), a=(11, 10))
    cfg = {"coord_tol": 99}
    want, reason = _decide_move_raw(snap, _DecState(), cfg, move_hint=None, follower=_Follower())
    assert want == "R", f"override=0 dx=1 → 이동 기대, got {want}"


def test_integration_tick_writes_override():
    """parlyuk 활성 edge → store.coord_tol_override=1, 만료 → -1."""
    from src_v2.brain.integration_tick import IntegrationState, integration_tick
    from src_v2.core.snapshot import SnapshotStore

    store = SnapshotStore()
    state = IntegrationState()
    rule_cfg = {"coord_tol": 5}
    extras = {}

    # parlyuk 활성
    store.update(buff_parlyuk_active=True)
    integration_tick(store, state, _Follower(), rule_cfg, extras, request_cast=lambda *a: None)
    snap = store.read()
    assert snap.coord_tol_override == 1, f"활성 시 override=1 기대, got {snap.coord_tol_override}"

    # parlyuk 만료
    store.update(buff_parlyuk_active=False)
    integration_tick(store, state, _Follower(), rule_cfg, extras, request_cast=lambda *a: None)
    snap = store.read()
    assert snap.coord_tol_override == -1, f"만료 시 override=-1 기대, got {snap.coord_tol_override}"


if __name__ == "__main__":
    test_override_minus1_uses_cfg()
    test_override_forces_value()
    test_override_zero_treated_as_strict()
    test_integration_tick_writes_override()
    print("ALL PASS")
