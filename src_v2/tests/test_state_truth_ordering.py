"""state truth + ordering 통합 테스트 (v1_gap_fix_list P2-1/2/3).

규약:
- Follower: force_exit_active / is_paused 의 truth
- SnapshotStore: read-only mirror (integration_tick 이 갱신)
- Snapshot.coord_tol_override: muscle.main_loop 의 truth (P0-4)

ordering 가정: integration_tick → store 갱신 → muscle.main_loop tick.
"""
from __future__ import annotations
import os, sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


class _MutFol:
    """force_exit / is_paused state 를 인위적으로 흔드는 fake."""
    def __init__(self):
        self._fea = False
        self._paused = False
    def force_exit_active(self, n=None): return self._fea
    def is_paused(self, n=None): return self._paused
    def exit_dir(self): return "U" if self._fea else "-"
    def force_exit_remaining(self, n=None): return 1.5 if self._fea else 0.0
    def pause_remaining(self, n=None): return 0.3 if self._paused else 0.0


def test_integration_tick_mirrors_follower_force_exit():
    """Follower=truth, snap=mirror. integration_tick 이 매 tick 동기."""
    from src_v2.brain.integration_tick import IntegrationState, integration_tick
    from src_v2.core.snapshot import SnapshotStore

    store = SnapshotStore()
    state = IntegrationState()
    fol = _MutFol()

    # 비활성 → snap 미러도 비활성
    integration_tick(store, state, fol, {}, {}, request_cast=lambda *a: None)
    assert store.read().force_exit_active is False

    # 활성 토글 → mirror 갱신
    fol._fea = True
    integration_tick(store, state, fol, {}, {}, request_cast=lambda *a: None)
    snap = store.read()
    assert snap.force_exit_active is True
    assert snap.force_exit_dir == "U"
    assert snap.force_exit_remaining > 0


def test_integration_tick_mirrors_paused():
    from src_v2.brain.integration_tick import IntegrationState, integration_tick
    from src_v2.core.snapshot import SnapshotStore

    store = SnapshotStore()
    state = IntegrationState()
    fol = _MutFol()
    fol._paused = True
    integration_tick(store, state, fol, {}, {}, request_cast=lambda *a: None)
    snap = store.read()
    assert snap.map_paused is True
    assert snap.map_pause_remaining > 0


def test_parlyuk_override_e2e_to_muscle():
    """parlyuk active → integration_tick 이 store override → muscle 가 즉시 read."""
    from src_v2.brain.integration_tick import IntegrationState, integration_tick
    from src_v2.core.snapshot import SnapshotStore
    from src_v2.muscle.main_loop import _decide_move_raw, DecisionState

    store = SnapshotStore()
    state = IntegrationState()

    # 활성 edge
    store.update(buff_parlyuk_active=True, healer_coord=(10, 10),
                 healer_map="M", attacker_coord=(13, 10), attacker_map="M",
                 attacker_coord_valid=True)
    integration_tick(store, state, _MutFol(), {"coord_tol": 5}, {},
                     request_cast=lambda *a: None)
    snap = store.read()
    assert snap.coord_tol_override == 1
    # muscle 가 override 우선 사용 → tol=1 이라 dx=3>1 → 이동
    want, _ = _decide_move_raw(snap, DecisionState(), {"coord_tol": 5},
                                move_hint=None, follower=_MutFol())
    assert want == "R"

    # 만료 → restore (override -1, cfg.coord_tol=5 사용 → dx=3<=5 → 정지)
    store.update(buff_parlyuk_active=False)
    integration_tick(store, state, _MutFol(), {"coord_tol": 5}, {},
                     request_cast=lambda *a: None)
    snap = store.read()
    assert snap.coord_tol_override == -1
    want2, _ = _decide_move_raw(snap, DecisionState(), {"coord_tol": 5},
                                 move_hint=None, follower=_MutFol())
    assert want2 == "-"


if __name__ == "__main__":
    test_integration_tick_mirrors_follower_force_exit()
    test_integration_tick_mirrors_paused()
    test_parlyuk_override_e2e_to_muscle()
    print("ALL PASS")
