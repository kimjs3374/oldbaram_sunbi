"""SelfHealingLoop unit tests."""
import os
import tempfile
import time
import pytest

from src_v2.core.event_bus import EventBus
from src_v2.core.snapshot import SnapshotStore
from src_v2.core.plugin_registry import PluginRegistry, LearnableSpec
from src_v2.core.types import CastRequest, CastResult, CastError, ActionRecord

from src_v2.memory.action_log import ActionLog
from src_v2.memory.self_healing import (
    SelfHealingLoop, HealingPolicy, builtin_policies,
    metric_self_heal_fail_rate, metric_atk_revive_fail_rate,
    metric_overall_fail_rate,
)


def _mk_rec(action: str, result: str, ts: float = None) -> ActionRecord:
    return ActionRecord(
        ts=ts or time.monotonic(),
        action=action,
        snapshot_at_decision={},
        result=result,
        latency_ms=0.0,
    )


def test_metric_self_heal_fail_rate():
    recs = [
        _mk_rec("self_heal", "ok"),
        _mk_rec("self_heal", "ok"),
        _mk_rec("self_heal", "no_effect"),
        _mk_rec("self_heal", "timeout"),
    ]
    rate = metric_self_heal_fail_rate(recs)
    assert abs(rate - 0.5) < 1e-6


def test_metric_overall_fail_rate():
    recs = [
        _mk_rec("a", "ok"),
        _mk_rec("b", "fail"),
    ]
    assert abs(metric_overall_fail_rate(recs) - 0.5) < 1e-6


def test_metric_atk_revive():
    recs = [
        _mk_rec("attacker_revive", "ok"),
        _mk_rec("격수부활", "fail"),
    ]
    assert abs(metric_atk_revive_fail_rate(recs) - 0.5) < 1e-6


def test_self_healing_disabled_does_not_start():
    store = SnapshotStore()
    bus = EventBus()
    log = ActionLog(store, bus)
    sh = SelfHealingLoop(log, enabled=False)
    sh.start()
    assert sh._thread is None or not sh._thread.is_alive()


def test_self_healing_below_threshold_no_apply():
    store = SnapshotStore()
    bus = EventBus()
    log = ActionLog(store, bus)
    log.attach()

    # PluginRegistry 에 param + spec 등록
    PluginRegistry.set_param("rule.self_heal.hp_thr", 50, force=True)
    sh = SelfHealingLoop(log, enabled=True, poll_sec=10.0)
    # 100% ok → 0 fail rate
    for _ in range(20):
        bus.publish("hand.cast_done", CastResult(
            request=CastRequest("self_heal"), status="ok"))
    sh.tick()
    assert sh._counts["applied"] == 0


def test_self_healing_above_threshold_applies_param():
    store = SnapshotStore()
    bus = EventBus()
    log = ActionLog(store, bus)
    log.attach()

    PluginRegistry.set_param("rule.self_heal.hp_thr", 50, force=True)
    sh = SelfHealingLoop(log, enabled=True, poll_sec=10.0)
    # 50% fail rate → threshold 0.10 초과 → apply (-5)
    for _ in range(10):
        bus.publish("hand.cast_done", CastResult(
            request=CastRequest("self_heal"), status="ok"))
    for _ in range(10):
        bus.publish("hand.cast_failed", CastError(
            request=CastRequest("self_heal"), reason="busy"))
    sh.tick()
    new_val = PluginRegistry.get_param("rule.self_heal.hp_thr")
    assert sh._counts["applied"] >= 1
    # direction=-1, step=5 → 50-5=45
    assert float(new_val) == 45.0


def test_self_healing_cooldown_prevents_repeat():
    store = SnapshotStore()
    bus = EventBus()
    log = ActionLog(store, bus)
    log.attach()
    PluginRegistry.set_param("rule.self_heal.hp_thr", 50, force=True)
    policies = [HealingPolicy(
        name="test_p",
        target_id="rule.self_heal.hp_thr",
        metric_fn=metric_overall_fail_rate,
        threshold=0.0,
        direction=-1,
        step=1.0,
        cooldown_sec=600.0,
    )]
    sh = SelfHealingLoop(log, policies=policies, enabled=True, poll_sec=10.0)
    # 100% fail → 항상 threshold 넘김
    for _ in range(5):
        bus.publish("hand.cast_failed", CastError(
            request=CastRequest("foo"), reason="x"))
    sh.tick()
    n1 = sh._counts["applied"]
    sh.tick()  # 즉시 두 번째 — cooldown 으로 차단
    n2 = sh._counts["applied"]
    assert n2 == n1


def test_evolution_log_writes_apply_line(tmp_path):
    store = SnapshotStore()
    bus = EventBus()
    log = ActionLog(store, bus)
    log.attach()
    PluginRegistry.set_param("rule.self_heal.hp_thr", 50, force=True)
    evo_path = str(tmp_path / "evolution_log.jsonl")
    sh = SelfHealingLoop(log, enabled=True, evolution_log_path=evo_path, poll_sec=10.0)
    for _ in range(10):
        bus.publish("hand.cast_failed", CastError(
            request=CastRequest("self_heal"), reason="x"))
    sh.tick()
    sh.stop(timeout=1.0)
    assert os.path.exists(evo_path)
    with open(evo_path, encoding="utf-8") as f:
        lines = f.readlines()
    assert any('"kind": "apply"' in ln for ln in lines)


def test_builtin_policies_count():
    pols = builtin_policies()
    names = [p.name for p in pols]
    assert "self_heal_fail_high" in names
    assert "atk_revive_fail_high" in names


def test_self_healing_handles_non_numeric_param_gracefully():
    """param 이 numeric 아니면 skip — 예외 없이."""
    store = SnapshotStore()
    bus = EventBus()
    log = ActionLog(store, bus)
    log.attach()
    PluginRegistry.set_param("rule.self_heal.hp_thr", "not-a-number", force=True)
    sh = SelfHealingLoop(log, enabled=True, poll_sec=10.0)
    for _ in range(10):
        bus.publish("hand.cast_failed", CastError(
            request=CastRequest("self_heal"), reason="x"))
    # 예외 없이 통과해야 함
    sh.tick()
    assert sh._counts["checks"] == 1
