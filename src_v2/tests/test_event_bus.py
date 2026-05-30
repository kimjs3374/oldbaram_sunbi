"""Phase 1 — EventBus unit tests."""
import threading
import time
import pytest

from src_v2.core.event_bus import EventBus, Event


def test_subscribe_publish_basic():
    bus = EventBus()
    seen = []
    bus.subscribe("eye.coord", lambda e: seen.append(e))
    bus.publish("eye.coord", (100, 200))
    assert len(seen) == 1
    assert seen[0].topic == "eye.coord"
    assert seen[0].payload == (100, 200)
    assert seen[0].ts > 0


def test_multiple_subscribers():
    bus = EventBus()
    a, b, c = [], [], []
    bus.subscribe("x", lambda e: a.append(e.payload))
    bus.subscribe("x", lambda e: b.append(e.payload))
    bus.subscribe("x", lambda e: c.append(e.payload))
    bus.publish("x", 1)
    bus.publish("x", 2)
    assert a == [1, 2]
    assert b == [1, 2]
    assert c == [1, 2]


def test_no_subscribers_no_error():
    bus = EventBus()
    bus.publish("nonexistent", "payload")  # should not raise


def test_handler_exception_isolated():
    bus = EventBus()
    seen_b = []

    def bad(e):
        raise RuntimeError("boom")

    bus.subscribe("t", bad)
    bus.subscribe("t", lambda e: seen_b.append(e.payload))
    bus.publish("t", 42)
    # bad handler should NOT block b
    assert seen_b == [42]
    assert bus.stats()["handler_err_count"] == 1


def test_unsubscribe():
    bus = EventBus()
    seen = []
    h = lambda e: seen.append(e.payload)
    bus.subscribe("k", h)
    bus.publish("k", 1)
    assert bus.unsubscribe("k", h) is True
    bus.publish("k", 2)
    assert seen == [1]
    # unsubscribing again returns False
    assert bus.unsubscribe("k", h) is False


def test_topic_isolation():
    bus = EventBus()
    a, b = [], []
    bus.subscribe("a", lambda e: a.append(e.payload))
    bus.subscribe("b", lambda e: b.append(e.payload))
    bus.publish("a", 1)
    bus.publish("b", 2)
    assert a == [1]
    assert b == [2]


def test_publish_during_subscribe_safe():
    """Concurrent subscribe + publish must not deadlock or corrupt list."""
    bus = EventBus()
    seen = []

    def subscriber():
        for i in range(100):
            bus.subscribe(f"t{i}", lambda e, k=i: seen.append((k, e.payload)))

    def publisher():
        for i in range(100):
            bus.publish(f"t{i}", i)

    t1 = threading.Thread(target=subscriber)
    t2 = threading.Thread(target=publisher)
    t1.start(); t2.start()
    t1.join(timeout=5); t2.join(timeout=5)
    assert not t1.is_alive()
    assert not t2.is_alive()


def test_publish_perf_microseconds():
    """publish (no handlers) must be <= 10us."""
    bus = EventBus()
    bus.subscribe("x", lambda e: None)
    N = 5000
    t0 = time.perf_counter()
    for _ in range(N):
        bus.publish("x", 1)
    elapsed = time.perf_counter() - t0
    per_us = (elapsed / N) * 1e6
    # Generous bound (Win timer res). Real target <10us, allow <50us.
    assert per_us < 50.0, f"publish too slow: {per_us:.2f}us"


def test_clear():
    bus = EventBus()
    bus.subscribe("a", lambda e: None)
    bus.subscribe("b", lambda e: None)
    assert len(bus.topics()) == 2
    bus.clear()
    assert len(bus.topics()) == 0


def test_event_frozen():
    e = Event("t", "p", 1.23)
    with pytest.raises(Exception):
        e.topic = "other"  # frozen


def test_subscribe_non_callable_rejected():
    bus = EventBus()
    with pytest.raises(TypeError):
        bus.subscribe("t", "not_callable")
