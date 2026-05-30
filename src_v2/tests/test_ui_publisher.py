"""Phase 6 — UI publisher unit tests."""
import time
import pytest

from src_v2.core.snapshot import SnapshotStore
from src_v2.ui.publisher import UiPublisher


def test_publisher_emits_at_rate():
    received = []
    store = SnapshotStore()
    store.update(hp=80, mp=70, healer_coord=(10, 20))
    pub = UiPublisher(store, emit=lambda p: received.append(p), hz=50)
    pub.start()
    time.sleep(0.15)
    pub.stop()
    # ~50hz * 0.15s = 7+ emits, allow 3+
    assert len(received) >= 3
    assert received[-1]["hp"] == 80
    assert received[-1]["mp"] == 70
    assert received[-1]["healer_coord"] == (10, 20)


def test_publisher_emit_exception_isolated():
    """Emit raising must not stop the publisher."""
    store = SnapshotStore()
    store.update(hp=10)
    state = {"calls": 0}

    def bad_emit(p):
        state["calls"] += 1
        raise RuntimeError("fail")

    pub = UiPublisher(store, emit=bad_emit, hz=50)
    pub.start()
    time.sleep(0.1)
    pub.stop()
    assert state["calls"] >= 2
    assert pub._err_count >= 2


def test_publisher_uses_custom_build():
    received = []
    store = SnapshotStore()
    store.update(hp=99)
    pub = UiPublisher(
        store,
        emit=lambda p: received.append(p),
        hz=100,
        build_payload=lambda snap: {"only": snap.hp},
    )
    pub.start()
    time.sleep(0.05)
    pub.stop()
    assert all("only" in p for p in received)


def test_publisher_stops_clean():
    store = SnapshotStore()
    pub = UiPublisher(store, emit=lambda p: None, hz=10)
    pub.start()
    time.sleep(0.02)
    pub.stop()
    assert not pub.is_alive()
