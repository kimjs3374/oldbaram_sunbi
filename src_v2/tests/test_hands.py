"""Phase 3 — Hands unit tests."""
import queue
import threading
import time
import pytest

from src_v2.core.event_bus import EventBus
from src_v2.core.plugin_registry import PluginRegistry, sequence
from src_v2.core.types import CastRequest

from src_v2.hands.input_dispatcher import (
    InputDispatcher, NullKeys, DIRECTION_VK,
)
from src_v2.hands.numlock_cycle import NumlockCycler, _NullNumlock
from src_v2.hands.skill_executor import SkillExecutor, HandsAPI


def test_dispatcher_set_direction_press_release():
    keys = NullKeys()
    d = InputDispatcher(keys=keys)
    d.set_direction("R")
    assert keys.is_down(DIRECTION_VK["R"])
    d.set_direction("L")
    # R must be released, L pressed
    assert not keys.is_down(DIRECTION_VK["R"])
    assert keys.is_down(DIRECTION_VK["L"])
    d.set_direction(None)
    assert not keys.is_down(DIRECTION_VK["L"])


def test_dispatcher_idempotent_same_direction():
    keys = NullKeys()
    d = InputDispatcher(keys=keys)
    d.set_direction("U")
    n = len(keys.events)
    d.set_direction("U")
    d.set_direction("U")
    # No new events for repeated same direction
    assert len(keys.events) == n


def test_dispatcher_release_with_dash():
    keys = NullKeys()
    d = InputDispatcher(keys=keys)
    d.set_direction("D")
    d.set_direction("-")
    assert d.held_direction() is None
    assert not keys.is_down(DIRECTION_VK["D"])


def test_dispatcher_unknown_direction():
    keys = NullKeys()
    d = InputDispatcher(keys=keys)
    d.set_direction("R")
    d.set_direction("X")  # unknown — releases R, holds None
    assert d.held_direction() is None


def test_numlock_cycle_disabled_no_toggle():
    # v1 SoR: NumLockCycler 는 주기적 토글이 아니라 skill_lock_vk 기반.
    # tick() 은 v2 호환 no-op. armed=False 일 때 동작 없음.
    null = _NullNumlock()
    c = NumlockCycler(adapter=null, interval_sec=0.01, enabled=False)
    time.sleep(0.05)
    assert c.tick() is False
    assert null.toggle_count == 0


def test_numlock_cycle_enabled_toggles():
    # v1 SoR: tick() 은 no-op. set_armed/set_slots/suspend/resume 등 v1 인터페이스
    # 동작 검증으로 대체.
    c = NumlockCycler(slots=[0x61, 0x62])
    assert c.armed is False
    c.set_armed(True)
    assert c.armed is True
    assert c.is_initial_lock_done() is False
    c.suspend()
    assert c._suspended is True
    c.resume()
    assert c._suspended is False
    c.set_slots([0x63, 0x64])
    assert c.slots == [0x63, 0x64]


def test_skill_executor_runs_sequence():
    PluginRegistry.reset()

    @sequence("test_seq")
    def my_seq(ctx):
        ctx["_invoked"] = True

    bus = EventBus()
    q: queue.PriorityQueue = queue.PriorityQueue()
    keys = NullKeys()
    dispatcher = InputDispatcher(keys=keys)
    api = HandsAPI(q, dispatcher)
    ex = SkillExecutor(q, bus, dispatcher)
    ex.start()

    done = []
    bus.subscribe("hand.cast_done", lambda e: done.append(e.payload))

    api.request_cast(CastRequest("test_seq", priority=10))
    time.sleep(0.2)
    ex.stop()

    assert ex._cast_count == 1
    assert len(done) == 1


def test_skill_executor_unknown_sequence():
    PluginRegistry.reset()
    bus = EventBus()
    q: queue.PriorityQueue = queue.PriorityQueue()
    dispatcher = InputDispatcher()
    api = HandsAPI(q, dispatcher)
    ex = SkillExecutor(q, bus, dispatcher)
    ex.start()

    failed = []
    bus.subscribe("hand.cast_failed", lambda e: failed.append(e.payload))
    api.request_cast(CastRequest("unknown", priority=10))
    time.sleep(0.2)
    ex.stop()

    assert ex._fail_count == 1
    assert failed[0].reason == "no_sequence"


def test_skill_executor_priority_ordering():
    PluginRegistry.reset()
    order = []

    @sequence("a")
    def a(ctx):
        order.append("a")

    @sequence("b")
    def b(ctx):
        order.append("b")

    @sequence("c")
    def c(ctx):
        order.append("c")

    bus = EventBus()
    q: queue.PriorityQueue = queue.PriorityQueue()
    dispatcher = InputDispatcher()
    api = HandsAPI(q, dispatcher)

    # enqueue out-of-priority order, executor must run by priority
    api.request_cast(CastRequest("c", priority=100))
    api.request_cast(CastRequest("a", priority=1))
    api.request_cast(CastRequest("b", priority=50))

    ex = SkillExecutor(q, bus, dispatcher)
    ex.start()
    time.sleep(0.3)
    ex.stop()
    assert order == ["a", "b", "c"]


def test_skill_executor_fifo_for_equal_priority():
    PluginRegistry.reset()
    order = []

    @sequence("seq")
    def s(ctx):
        order.append(ctx["tag"])

    bus = EventBus()
    q: queue.PriorityQueue = queue.PriorityQueue()
    dispatcher = InputDispatcher()
    api = HandsAPI(q, dispatcher)

    for tag in range(5):
        api.request_cast(CastRequest("seq", priority=50, ctx={"tag": tag}))

    ex = SkillExecutor(q, bus, dispatcher)
    ex.start()
    time.sleep(0.3)
    ex.stop()
    assert order == [0, 1, 2, 3, 4]


def test_skill_executor_handles_seq_exception():
    PluginRegistry.reset()

    @sequence("boom")
    def b(ctx):
        raise ValueError("boom")

    bus = EventBus()
    q: queue.PriorityQueue = queue.PriorityQueue()
    dispatcher = InputDispatcher()
    api = HandsAPI(q, dispatcher)
    ex = SkillExecutor(q, bus, dispatcher)
    ex.start()

    failed = []
    bus.subscribe("hand.cast_failed", lambda e: failed.append(e.payload))
    api.request_cast(CastRequest("boom"))
    time.sleep(0.2)
    ex.stop()

    assert ex._fail_count == 1
    assert "boom" in failed[0].reason


def test_in_progress_tracked():
    PluginRegistry.reset()
    seen_in_progress = []

    @sequence("slow")
    def slow(ctx):
        seen_in_progress.append("slow" in ex.in_progress)
        time.sleep(0.05)

    bus = EventBus()
    q: queue.PriorityQueue = queue.PriorityQueue()
    dispatcher = InputDispatcher()
    api = HandsAPI(q, dispatcher)
    ex = SkillExecutor(q, bus, dispatcher)
    ex.start()
    api.request_cast(CastRequest("slow"))
    time.sleep(0.2)
    ex.stop()
    assert seen_in_progress == [True]
    # After completion, removed
    assert "slow" not in ex.in_progress


def test_sequences_self_register_on_import():
    """Importing src_v2.hands.sequences must register all 9 sequences."""
    PluginRegistry.reset()
    import importlib
    import src_v2.hands.sequences as seq_pkg
    importlib.reload(seq_pkg)  # re-runs decorators after reset
    names = set(PluginRegistry.list_sequences())
    expected = {
        "self_heal", "attacker_revive", "self_revive",
        "parhon", "baekho", "parlyuk", "gyoungryeok",
        "seq_rclick", "tab_lock",
    }
    assert expected.issubset(names), f"missing: {expected - names}"


def test_self_heal_sequence_records_keys():
    """self_heal_seq must press TAB, HOME, TAB, NUMLOCK, heal x N, ESC, TAB, TAB, NUMLOCK."""
    PluginRegistry.reset()
    import importlib
    import src_v2.hands.sequences.self_heal_seq as m
    importlib.reload(m)
    seq_fn = PluginRegistry.get_sequence("self_heal")
    assert seq_fn is not None
    keys = NullKeys()
    dispatcher = InputDispatcher(keys=keys)
    seq_fn({"_dispatcher": dispatcher, "burst_count": 2, "burst_gap_ms": 1, "enable_block_b": True})
    # collect tap VKs
    taps = [e for e in keys.events if e[0] == "tap"]
    vks = [t[1] for t in taps]
    # should contain TAB(0x09), HOME(0x24), NUMLOCK(0x90), '1' digit (0x31)x2, ESC(0x1B)
    assert 0x09 in vks
    assert 0x24 in vks
    assert 0x90 in vks
    assert 0x1B in vks
    assert vks.count(0x31) == 2  # 2 burst heals
