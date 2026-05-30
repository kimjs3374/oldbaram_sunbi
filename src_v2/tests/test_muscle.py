"""Phase 5 — Muscle main loop unit tests."""
import threading
import time
import pytest

from src_v2.core.snapshot import Snapshot, SnapshotStore
from src_v2.hands.input_dispatcher import InputDispatcher, NullKeys
from src_v2.muscle.main_loop import MainLoop, decide_direction, _step_toward


# ===== decide_direction (pure) =====

def test_decide_no_healer_coord_returns_dash():
    snap = Snapshot(healer_coord=None)
    assert decide_direction(snap) == "-"


def test_decide_same_map_to_attacker_right():
    snap = Snapshot(
        healer_coord=(10, 50), healer_map="m",
        attacker_coord=(20, 50), attacker_map="m",
        attacker_coord_valid=True,
    )
    assert decide_direction(snap) == "R"


def test_decide_same_map_to_attacker_left():
    snap = Snapshot(
        healer_coord=(50, 50), healer_map="m",
        attacker_coord=(10, 50), attacker_map="m",
        attacker_coord_valid=True,
    )
    assert decide_direction(snap) == "L"


def test_decide_same_map_to_attacker_up_down():
    snap = Snapshot(
        healer_coord=(50, 50), healer_map="m",
        attacker_coord=(50, 100), attacker_map="m",
        attacker_coord_valid=True,
    )
    assert decide_direction(snap) == "D"
    snap2 = Snapshot(
        healer_coord=(50, 100), healer_map="m",
        attacker_coord=(50, 50), attacker_map="m",
        attacker_coord_valid=True,
    )
    assert decide_direction(snap2) == "U"


def test_decide_combat_band():
    snap = Snapshot(
        healer_coord=(50, 50), healer_map="m",
        attacker_coord=(51, 51), attacker_map="m",
        attacker_coord_valid=True,
    )
    assert decide_direction(snap, {"combat_band": 2}) == "-"


def test_decide_attacker_invalid_uses_last_dir():
    snap = Snapshot(
        healer_coord=(10, 10), healer_map="m",
        attacker_coord=None, attacker_map="m",
        attacker_coord_valid=False,
        attacker_last_dir="L",
    )
    assert decide_direction(snap) == "L"


def test_decide_map_neq_returns_attacker_last_dir():
    snap = Snapshot(
        healer_coord=(10, 10), healer_map="m1",
        attacker_coord=(20, 20), attacker_map="m2",
        attacker_coord_valid=True,
        attacker_last_dir="U",
    )
    assert decide_direction(snap) == "U"


def test_decide_dominant_axis_x():
    """Larger |dx| beats smaller |dy|."""
    snap = Snapshot(
        healer_coord=(0, 0), healer_map="m",
        attacker_coord=(100, 10), attacker_map="m",
        attacker_coord_valid=True,
    )
    assert decide_direction(snap) == "R"


# ===== MainLoop runtime =====

def test_main_loop_runs_and_stops():
    store = SnapshotStore()
    keys = NullKeys()
    dispatcher = InputDispatcher(keys=keys)
    loop = MainLoop(store, dispatcher, hz_cap=500)
    loop.start()
    time.sleep(0.05)
    loop.stop()
    assert not loop.is_alive()
    assert loop._iter_count > 5


def test_main_loop_sets_direction_when_attacker_visible():
    store = SnapshotStore()
    store.update(
        healer_coord=(10, 10), healer_map="m",
        attacker_coord=(50, 10), attacker_map="m",
        attacker_coord_valid=True,
    )
    keys = NullKeys()
    dispatcher = InputDispatcher(keys=keys)
    loop = MainLoop(store, dispatcher, hz_cap=500)
    loop.start()
    time.sleep(0.05)
    loop.stop()
    # Should be holding R
    assert dispatcher.held_direction() == "R"


def test_main_loop_changes_direction_on_snapshot_change():
    store = SnapshotStore()
    store.update(
        healer_coord=(10, 10), healer_map="m",
        attacker_coord=(50, 10), attacker_map="m",
        attacker_coord_valid=True,
    )
    keys = NullKeys()
    dispatcher = InputDispatcher(keys=keys)
    loop = MainLoop(store, dispatcher, hz_cap=500)
    loop.start()
    time.sleep(0.03)
    assert dispatcher.held_direction() == "R"
    # Move attacker to left
    store.update(attacker_coord=(0, 10))
    time.sleep(0.03)
    assert dispatcher.held_direction() == "L"
    loop.stop()


def test_main_loop_perf_under_2ms_avg():
    """Body avg should be well under 2ms (target 1ms). Allow 5ms slack on Win."""
    store = SnapshotStore()
    store.update(
        healer_coord=(10, 10), healer_map="m",
        attacker_coord=(20, 10), attacker_map="m",
        attacker_coord_valid=True,
    )
    keys = NullKeys()
    dispatcher = InputDispatcher(keys=keys)
    loop = MainLoop(store, dispatcher, hz_cap=200)
    loop.start()
    time.sleep(0.5)
    loop.stop()
    s = loop.stats()
    assert s["avg_ms"] < 5.0, f"main loop body too slow: avg={s['avg_ms']}ms"
    assert s["iter_count"] >= 50  # 200hz * 0.5s = 100 ideal


def test_main_loop_no_release_for_idle():
    store = SnapshotStore()
    # No coord -> idle
    keys = NullKeys()
    dispatcher = InputDispatcher(keys=keys)
    loop = MainLoop(store, dispatcher, hz_cap=500)
    loop.start()
    time.sleep(0.03)
    loop.stop()
    assert dispatcher.held_direction() is None
