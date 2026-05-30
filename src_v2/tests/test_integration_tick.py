"""Integration tick — v1 1:1 분기 검증 (TAB-CONFIRM, F1-PEND, MAP-PAUSE,
edge detection, post-self-heal-tab, parlyuk-tol)."""
from __future__ import annotations

import time

from src_v2.brain.integration_tick import (
    IntegrationState, integration_tick, arm_tab_lock_pending,
    POST_MAPCHG_GRACE_SEC, PENDING_TAB_LOCK_SEC, TAB_LOCK_DIST_THR,
    SELF_HEAL_TAB_RETURN_DIST, SELF_HEAL_TAB_RETURN_WINDOW_SEC,
)
from src_v2.core.snapshot import SnapshotStore, Snapshot
from src_v2.core.types import AttackerState
from src_v2.brain.follower import _MinimalFollowerStub


def _new_setup():
    store = SnapshotStore()
    state = IntegrationState()
    fol = _MinimalFollowerStub()
    rule_cfg = {"_map_transition_in_progress": False, "coord_tol": 2}
    extras: dict = {}
    return store, state, fol, rule_cfg, extras


def test_force_exit_mirror_inactive_default():
    store, state, fol, rule_cfg, extras = _new_setup()
    integration_tick(store, state, fol, rule_cfg, extras)
    snap = store.read()
    assert snap.force_exit_active is False
    assert snap.force_exit_dir == "-"


def test_f1_pend_mirror():
    store, state, fol, rule_cfg, extras = _new_setup()
    atk = AttackerState(
        coord=(10, 10), coord_valid=True, map_name="m", map_seq=0,
        last_dir="R", hp=80, mp=80, honma_sec=0,
        mujang_sec=10, boho_sec=10, seq=1, map_change_pending=True,
    )
    store.update(attacker_state=atk)
    integration_tick(store, state, fol, rule_cfg, extras)
    snap = store.read()
    assert snap.f1_pend_active is True


def test_map_neq_sets_transition():
    store, state, fol, rule_cfg, extras = _new_setup()
    store.update(healer_map="A", attacker_map="B")
    integration_tick(store, state, fol, rule_cfg, extras)
    assert rule_cfg["_map_transition_in_progress"] is True


def test_attacker_dead_edge_triggers_revive():
    store, state, fol, rule_cfg, extras = _new_setup()
    casts: list = []
    atk = AttackerState(
        coord=(10, 10), coord_valid=True, map_name="m", map_seq=0,
        last_dir="R", hp=0, mp=80, honma_sec=0,
        mujang_sec=10, boho_sec=10, seq=1,
    )
    store.update(attacker_state=atk, hp=80)
    integration_tick(
        store, state, fol, rule_cfg, extras,
        request_cast=lambda n: casts.append(n),
    )
    assert "격수부활" in casts


def test_attacker_honma_edge_triggers_parhon():
    store, state, fol, rule_cfg, extras = _new_setup()
    casts: list = []
    atk = AttackerState(
        coord=(10, 10), coord_valid=True, map_name="m", map_seq=0,
        last_dir="R", hp=80, mp=80, honma_sec=10,
        mujang_sec=10, boho_sec=10, seq=1,
    )
    store.update(attacker_state=atk, hp=80)
    integration_tick(
        store, state, fol, rule_cfg, extras,
        request_cast=lambda n: casts.append(n),
    )
    assert "파혼술" in casts


def test_parlyuk_active_edge_forces_coord_tol_1():
    store, state, fol, rule_cfg, extras = _new_setup()
    rule_cfg["coord_tol"] = 5
    store.update(buff_parlyuk_active=True)
    integration_tick(store, state, fol, rule_cfg, extras)
    assert rule_cfg["coord_tol"] == 1
    # 만료 → 복원
    store.update(buff_parlyuk_active=False)
    integration_tick(store, state, fol, rule_cfg, extras)
    assert rule_cfg["coord_tol"] == 5


def test_tab_lock_pending_arm_then_fire():
    store, state, fol, rule_cfg, extras = _new_setup()
    # arm
    arm_tab_lock_pending(state)
    assert state._pending_tab_lock_until > time.time()
    # 같은 맵, 거리 ≤ 10, 안정화 0.6s 경과 시 tab_lock_pending=True
    state._last_map_change_ts = time.time() - 1.0
    store.update(
        healer_map="m", attacker_map="m",
        healer_coord=(10, 10),
        attacker_coord=(15, 12),
        attacker_coord_valid=True,
    )
    integration_tick(store, state, fol, rule_cfg, extras)
    snap = store.read()
    assert snap.tab_lock_pending is True


def test_tab_lock_pending_skips_when_far():
    store, state, fol, rule_cfg, extras = _new_setup()
    arm_tab_lock_pending(state)
    state._last_map_change_ts = time.time() - 1.0
    store.update(
        healer_map="m", attacker_map="m",
        healer_coord=(10, 10),
        attacker_coord=(50, 50),  # manhattan = 80 > 10
        attacker_coord_valid=True,
    )
    integration_tick(store, state, fol, rule_cfg, extras)
    snap = store.read()
    assert snap.tab_lock_pending is False


def test_post_self_heal_tab_return_window():
    store, state, fol, rule_cfg, extras = _new_setup()
    casts: list = []
    # 자힐 ts 기록
    ws = {"last_self_heal_ts": time.time()}
    store.update(
        healer_map="m", attacker_map="m",
        healer_coord=(10, 10),
        attacker_coord=(11, 12),  # manhattan = 3 ≤ 5
        attacker_coord_valid=True,
    )
    integration_tick(
        store, state, fol, rule_cfg, extras,
        request_cast=lambda n: casts.append(n),
        worker_state=ws,
    )
    assert "tab_lock" in casts


def test_post_self_heal_tab_skips_when_far():
    store, state, fol, rule_cfg, extras = _new_setup()
    casts: list = []
    ws = {"last_self_heal_ts": time.time()}
    store.update(
        healer_map="m", attacker_map="m",
        healer_coord=(10, 10),
        attacker_coord=(50, 50),  # manhattan = 80
        attacker_coord_valid=True,
    )
    integration_tick(
        store, state, fol, rule_cfg, extras,
        request_cast=lambda n: casts.append(n),
        worker_state=ws,
    )
    assert "tab_lock" not in casts


def test_force_exit_active_mirrored_from_follower():
    store, state, fol, rule_cfg, extras = _new_setup()
    # stub 의 force_exit_active 를 True 로 패치
    fol.force_exit_active = lambda now=None: True
    fol.exit_dir = lambda: "R"
    fol.force_exit_remaining = lambda now=None: 1.5
    integration_tick(store, state, fol, rule_cfg, extras)
    snap = store.read()
    assert snap.force_exit_active is True
    assert snap.force_exit_dir == "R"


def test_map_pause_mirror():
    store, state, fol, rule_cfg, extras = _new_setup()
    fol.is_paused = lambda now=None: True
    fol.pause_remaining = lambda now=None: 2.0
    integration_tick(store, state, fol, rule_cfg, extras)
    snap = store.read()
    assert snap.map_paused is True
    # MAP-PAUSE 가 _map_transition_in_progress 트리거
    assert rule_cfg["_map_transition_in_progress"] is True
