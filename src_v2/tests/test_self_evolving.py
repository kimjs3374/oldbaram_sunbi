"""Phase 7 — self-evolving subsystem unit + scenario tests.

9 tests covering: @learnable registration, set/get_param COW,
fitness eval, MetaLearner scoring, BanditOptimizer UCB1,
HotApply rollback, runner cycle, integration with action log,
mock self-improvement scenario.
"""
from __future__ import annotations
import time
from typing import List

import pytest

from src_v2.core.plugin_registry import (
    PluginRegistry, LearnableSpec, learnable,
)
from src_v2.core.types import ActionRecord
from src_v2.core.event_bus import EventBus
from src_v2.core.snapshot import SnapshotStore
from src_v2.memory.action_log import ActionLog

from src_v2.learning import (
    declare_learnables,
    builtin_learnables,
    MetaLearner,
    Optimizer,
    BanditOptimizer,
    HotApply,
    FitnessRegistry,
    register_builtin_fitness,
    MetaLearnerRunner,
)


# ---------- helpers ----------

def _mk_record(action: str, result: str = "ok", ts: float = 0.0,
               latency: float = 5.0, **snap) -> ActionRecord:
    return ActionRecord(
        ts=ts or time.time(),
        action=action,
        snapshot_at_decision=dict(snap) or {"hp": 80, "mp": 50},
        result=result,
        latency_ms=latency,
        detail="",
    )


# ====================================================================
# Test 1: @learnable decorator + builtin learnables registration
# ====================================================================
def test_01_learnable_decorator_and_builtins():
    # decorator form
    @learnable("rule.test_thing.x", range=(0.0, 100.0),
               fitness="higher_uptime", default=42)
    def _seed():
        return 42

    spec = PluginRegistry.get_learnable("rule.test_thing.x")
    assert spec is not None
    assert spec.range == (0.0, 100.0)
    assert spec.fitness == "higher_uptime"
    assert PluginRegistry.get_param("rule.test_thing.x") == 42

    # builtins via declare_learnables
    n = declare_learnables()
    assert n == len(builtin_learnables())
    assert n >= 5
    assert PluginRegistry.get_learnable("rule.self_heal.hp_thr") is not None
    assert PluginRegistry.get_param("rule.self_heal.hp_thr") == 50


# ====================================================================
# Test 2: copy-on-write set_param + get_param atomicity
# ====================================================================
def test_02_set_param_copy_on_write():
    declare_learnables()
    # capture pre-swap reference
    before_snap = PluginRegistry.snapshot_params()
    assert before_snap["rule.self_heal.hp_thr"] == 50

    # set_param -> new dict, old snap unchanged
    PluginRegistry.set_param("rule.self_heal.hp_thr", 65)
    assert before_snap["rule.self_heal.hp_thr"] == 50  # COW intact
    assert PluginRegistry.get_param("rule.self_heal.hp_thr") == 65

    # unregistered target requires force
    assert PluginRegistry.set_param("unregistered.x", 1) is False
    assert PluginRegistry.set_param("unregistered.x", 1, force=True) is True
    assert PluginRegistry.get_param("unregistered.x") == 1

    # restore
    PluginRegistry.restore_params(before_snap)
    assert PluginRegistry.get_param("rule.self_heal.hp_thr") == 50


# ====================================================================
# Test 3: built-in fitness functions
# ====================================================================
def test_03_fitness_functions():
    reg = FitnessRegistry()
    register_builtin_fitness(reg)
    assert "lower_death_rate" in reg.list_names()

    # all heals, no revives -> high fitness (close to 1)
    records = [_mk_record("self_heal", "ok") for _ in range(10)]
    f = reg.eval("lower_death_rate", records)
    assert f == pytest.approx(1.0)

    # half revives -> 0.5
    records = ([_mk_record("self_heal", "ok") for _ in range(5)] +
               [_mk_record("self_revive", "ok") for _ in range(5)])
    f = reg.eval("lower_death_rate", records)
    assert f == pytest.approx(0.5)

    # higher_uptime: all ok -> 1.0
    records = [_mk_record("anything", "ok") for _ in range(10)]
    assert reg.eval("higher_uptime", records) == pytest.approx(1.0)
    # mixed
    records = ([_mk_record("a", "ok") for _ in range(7)] +
               [_mk_record("a", "failed") for _ in range(3)])
    assert reg.eval("higher_uptime", records) == pytest.approx(0.7)

    # unknown name returns None
    assert reg.eval("unknown_fn", records) is None


# ====================================================================
# Test 4: MetaLearner scoring + threshold filtering
# ====================================================================
def test_04_meta_learner_scoring():
    declare_learnables()
    reg = FitnessRegistry()
    register_builtin_fitness(reg)
    meta = MetaLearner(reg, min_score_threshold=0.5)

    # Construct records: many self_heal events, mixed results
    records: List[ActionRecord] = []
    for i in range(20):
        records.append(_mk_record("self_heal",
                                  "ok" if i % 3 != 0 else "failed",
                                  ts=i))
    # parlyuk all-ok (low volatility, low score)
    for i in range(8):
        records.append(_mk_record("parlyuk", "ok", ts=20 + i))
    # unknown auto-discovered action
    for i in range(6):
        records.append(_mk_record("custom_action", "ok", ts=30 + i))

    entries = meta.score_targets(records)
    target_ids = [e.target_id for e in entries]

    # explicit self_heal targets present
    assert "rule.self_heal.hp_thr" in target_ids
    # auto-discovered
    assert "auto.custom_action" in target_ids
    # all-ok parlyuk is low volatility -> low score (likely below threshold)

    filtered = meta.filter_above_threshold(entries)
    # at least one self_heal target should pass since it has volatility
    assert any(e.target_id.startswith("rule.self_heal") for e in filtered)


# ====================================================================
# Test 5: BanditOptimizer UCB1 selection + reward update
# ====================================================================
def test_05_bandit_optimizer_ucb1():
    spec = LearnableSpec(
        target_id="rule.self_heal.hp_thr",
        range=(30.0, 70.0),
        default=50,
    )
    opt = BanditOptimizer(n_arms=5, c=1.4)

    # First 5 proposes should pull each arm once
    proposed = []
    for _ in range(5):
        v = opt.propose(spec)
        proposed.append(v)
        opt.update(spec.target_id, v, reward=0.0)
    # all unique values across the range
    assert len(set(proposed)) == 5

    # Heavily reward arm at value=50 across many pulls
    for _ in range(200):
        opt.update(spec.target_id, 50.0, reward=0.9)
        opt.update(spec.target_id, 30.0, reward=0.0)
        opt.update(spec.target_id, 40.0, reward=0.0)
        opt.update(spec.target_id, 60.0, reward=0.0)
        opt.update(spec.target_id, 70.0, reward=0.0)

    # arm 50 mean should be highest now
    stats = opt.stats(spec.target_id)
    means = {a["value"]: a["mean"] for a in stats["arms"]}
    assert means[50.0] > means[30.0]
    assert means[50.0] > means[70.0]
    assert means[50.0] >= 0.8

    # UCB1 with many pulls: exploration term is small -> exploitation wins
    chosen = [opt.propose(spec) for _ in range(20)]
    near_50 = sum(1 for v in chosen if abs(v - 50.0) < 0.01)
    # Expect majority near 50 (exploration still possible)
    assert near_50 >= 10
    assert len(stats["arms"]) == 5


# ====================================================================
# Test 6: HotApply clamping + rollback
# ====================================================================
def test_06_hot_apply_clamp_and_rollback(monkeypatch):
    declare_learnables()
    reg = FitnessRegistry()
    register_builtin_fitness(reg)
    # Tiny window so rollback test is fast
    ha = HotApply(reg, rollback_window_sec=0.01, regression_factor=1.5)

    spec = PluginRegistry.get_learnable("rule.self_heal.hp_thr")
    # baseline records: all heals ok -> fitness 1.0
    baseline_records = [_mk_record("self_heal", "ok") for _ in range(10)]

    # Apply with proposal way above range — should clamp to safety/range
    token = ha.apply(spec, proposed_value=999.0,
                     records_before=baseline_records)
    assert token is not None
    # range hi=70, safety hi=85 -> final clamp = 70 (range is tighter)
    assert PluginRegistry.get_param("rule.self_heal.hp_thr") == 70
    assert token.baseline_score == pytest.approx(1.0)

    # Wait past rollback window
    time.sleep(0.02)

    # Now records show degradation: lots of revives
    bad_records = ([_mk_record("self_heal", "ok") for _ in range(2)] +
                   [_mk_record("self_revive", "ok") for _ in range(8)])
    rolled = ha.maybe_rollback(token, bad_records)
    assert rolled is True
    assert token.rolled_back is True
    # restored to previous (default 50)
    assert PluginRegistry.get_param("rule.self_heal.hp_thr") == 50

    s = ha.stats()
    assert s["applied"] == 1
    assert s["rollbacks"] == 1


# ====================================================================
# Test 7: HotApply keeps improvement (no rollback)
# ====================================================================
def test_07_hot_apply_keeps_improvement():
    declare_learnables()
    reg = FitnessRegistry()
    register_builtin_fitness(reg)
    ha = HotApply(reg, rollback_window_sec=0.01, regression_factor=1.1)

    spec = PluginRegistry.get_learnable("rule.self_heal.hp_thr")
    # baseline: 50% revive rate -> fitness 0.5
    baseline = ([_mk_record("self_heal", "ok") for _ in range(5)] +
                [_mk_record("self_revive", "ok") for _ in range(5)])
    token = ha.apply(spec, 60.0, baseline)
    assert token is not None
    assert token.baseline_score == pytest.approx(0.5)

    time.sleep(0.02)
    # improved: only 20% revive
    improved = ([_mk_record("self_heal", "ok") for _ in range(8)] +
                [_mk_record("self_revive", "ok") for _ in range(2)])
    rolled = ha.maybe_rollback(token, improved)
    assert rolled is False
    # value retained
    assert PluginRegistry.get_param("rule.self_heal.hp_thr") == 60


# ====================================================================
# Test 8: Runner cycle integration with ActionLog
# ====================================================================
def test_08_runner_cycle_integration():
    declare_learnables()

    bus = EventBus()
    store = SnapshotStore()
    alog = ActionLog(store, bus, capacity=1000)

    # Seed action log directly with skewed data: many self_heal events
    # half failed -> high volatility, low recent ok ratio -> high score
    for i in range(40):
        rec = _mk_record(
            "self_heal",
            "ok" if i % 2 == 0 else "failed",
            ts=time.time() + i * 0.01,
        )
        alog._buf.append(rec)  # bypass bus for unit test

    runner = MetaLearnerRunner(
        alog,
        poll_sec=999.0,  # never auto
        min_score_threshold=0.3,
        rollback_window_sec=0.01,
        regression_factor=1.1,
        max_targets_per_cycle=2,
    )

    pre_param = PluginRegistry.get_param("rule.self_heal.hp_thr")
    result = runner.run_once()

    assert result["scored"] >= 1
    assert result["above_threshold"] >= 1
    assert result["applied"] >= 1
    # Param should now have been changed by hot_apply
    post_param = PluginRegistry.get_param("rule.self_heal.hp_thr")
    # (not necessarily different, but at least within range)
    spec = PluginRegistry.get_learnable("rule.self_heal.hp_thr")
    assert spec.range[0] <= post_param <= spec.range[1]

    # learner stats accessible
    s = runner.stats()
    assert "hot_apply" in s
    assert "params" in s


# ====================================================================
# Test 9: Mock self-improvement scenario
#   Scenario: hp_thr=50 causes too many deaths in a "low-survival" world.
#   Optimizer proposes hp_thr=65 (earlier heal), survival improves,
#   value is retained and the new best arm is reinforced.
#   Verifies end-to-end loop without external dependencies.
# ====================================================================
def test_09_mock_self_improvement_scenario():
    declare_learnables()

    bus = EventBus()
    store = SnapshotStore()
    alog = ActionLog(store, bus, capacity=2000)

    fitness = FitnessRegistry()
    register_builtin_fitness(fitness)

    # Force optimizer to propose 65 first by seeding closest arm bias.
    # n_arms=5 across (30, 70) -> arms = [30, 40, 50, 60, 70]
    opt = BanditOptimizer(n_arms=5, c=0.1)  # low explore -> exploit

    # Pre-train opt: high reward at 65 (closest to arm 60)
    spec = PluginRegistry.get_learnable("rule.self_heal.hp_thr")
    opt._ensure(spec)
    # initialise all arms once with low reward, then favor 60
    for v in (30.0, 40.0, 50.0, 60.0, 70.0):
        opt.update(spec.target_id, v, 0.1)
    for _ in range(10):
        opt.update(spec.target_id, 60.0, 0.95)

    ha = HotApply(fitness, rollback_window_sec=0.01, regression_factor=1.05)
    meta = MetaLearner(fitness, min_score_threshold=0.3)

    runner = MetaLearnerRunner(
        alog,
        poll_sec=999.0,
        fitness=fitness,
        meta=meta,
        optimizer=opt,
        hot_apply=ha,
        max_targets_per_cycle=1,
    )

    # ----- Phase A: seed BAD baseline (deaths frequent at hp_thr=50) -----
    # 6 revives + 4 heals -> fitness 0.4
    # Need volatility for meta scoring -> mix some failed heals too
    for i in range(4):
        alog._buf.append(_mk_record("self_heal", "ok", ts=i))
    for i in range(2):
        alog._buf.append(_mk_record("self_heal", "failed", ts=i))
    for i in range(6):
        alog._buf.append(_mk_record("self_revive", "ok", ts=10 + i))

    pre = PluginRegistry.get_param("rule.self_heal.hp_thr")
    assert pre == 50

    cycle1 = runner.run_once()
    assert cycle1["applied"] == 1
    new_val = PluginRegistry.get_param("rule.self_heal.hp_thr")
    # Optimizer should have nudged us toward 60 (best arm)
    assert new_val == 60

    # ----- Phase B: simulate IMPROVED world after hp_thr=60 deployed -----
    time.sleep(0.02)  # past rollback window
    # Wipe and seed improved data
    alog.clear()
    for i in range(8):
        alog._buf.append(_mk_record("self_heal", "ok", ts=10 + i))
    for i in range(2):
        alog._buf.append(_mk_record("self_revive", "ok", ts=18 + i))

    cycle2 = runner.run_once()
    # Pending token should NOT roll back (improvement detected)
    final_val = PluginRegistry.get_param("rule.self_heal.hp_thr")
    assert final_val == 60  # held
    assert cycle2["rolled_back"] == 0

    # Optimizer was given positive reward for arm 60 -> stats reflect
    arm_stats = opt.stats(spec.target_id)
    arm_60 = next(a for a in arm_stats["arms"] if a["value"] == 60.0)
    # mean is positive (got rewards from pre-train + cycle reward)
    assert arm_60["mean"] > 0.5
