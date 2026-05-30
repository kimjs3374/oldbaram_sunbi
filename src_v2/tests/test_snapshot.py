"""Phase 1 — SnapshotStore unit tests."""
import threading
import time
import pytest

from src_v2.core.snapshot import Snapshot, SnapshotStore


def test_default_snapshot_values():
    s = Snapshot()
    assert s.healer_coord is None
    assert s.hp == -1
    assert s.healer_map == ""
    assert s.update_count == 0


def test_update_setattr():
    store = SnapshotStore()
    store.update(hp=80, mp=90, healer_coord=(100, 200))
    snap = store.read()
    assert snap.hp == 80
    assert snap.mp == 90
    assert snap.healer_coord == (100, 200)
    assert snap.update_count == 1
    assert snap.last_eye_update_ts > 0


def test_read_field():
    store = SnapshotStore()
    store.update(hp=42)
    assert store.read_field("hp") == 42
    assert store.read_field("nonexistent", "default") == "default"


def test_update_count_increments():
    store = SnapshotStore()
    for i in range(10):
        store.update(hp=i)
    assert store.read().update_count == 10


def test_atomic_field_write():
    """Writers update fields, readers see consistent values (GIL atomic)."""
    store = SnapshotStore()
    stop = threading.Event()
    seen_partials = []

    def writer():
        i = 0
        while not stop.is_set():
            store.update(healer_coord=(i, i + 1))
            i += 1

    def reader():
        for _ in range(2000):
            c = store.read_field("healer_coord")
            if c is not None:
                x, y = c
                if y != x + 1:
                    seen_partials.append((x, y))

    t1 = threading.Thread(target=writer)
    t2 = threading.Thread(target=reader)
    t1.start(); t2.start()
    time.sleep(0.05)
    stop.set()
    t1.join(); t2.join()
    # Tuple is set in single setattr — readers must never see partial.
    assert seen_partials == [], f"saw partial coord: {seen_partials[:5]}"


def test_replace_all():
    store = SnapshotStore()
    s2 = Snapshot(hp=99)
    store.replace_all(s2)
    assert store.read().hp == 99


def test_to_dict_excludes_frame():
    store = SnapshotStore()
    store.update(hp=10, last_frame=object())
    d = store.to_dict()
    assert d["hp"] == 10
    assert d["last_frame"] is True


def test_read_returns_live_ref():
    """read() returns ref — subsequent updates visible."""
    store = SnapshotStore()
    snap = store.read()
    assert snap.hp == -1
    store.update(hp=50)
    assert snap.hp == 50  # same ref


def test_lock_free_read_perf():
    """read() must be cheap (no lock overhead)."""
    store = SnapshotStore()
    store.update(hp=50, mp=60)
    N = 100_000
    t0 = time.perf_counter()
    for _ in range(N):
        s = store.read()
        _ = s.hp
    elapsed = time.perf_counter() - t0
    per_us = (elapsed / N) * 1e6
    assert per_us < 5.0, f"read too slow: {per_us:.2f}us"
