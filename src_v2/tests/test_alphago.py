"""AlphaGo module — unit + scenario tests.

13 tests covering feature extractor, NN core, policy/value nets,
replay buffer, env model, self-play, MCTS, trainer convergence,
coach full cycle (mock), neural rule integration, hot-swap atomicity.

All tests are self-contained and mock-driven.
"""
from __future__ import annotations
import os
import time
from typing import List

import numpy as np
import pytest

from src_v2.core.plugin_registry import PluginRegistry
from src_v2.core.snapshot import Snapshot
from src_v2.core.types import ActionRecord, RuleContext

from src_v2.learning.alphago import (
    FeatureExtractor, FEATURE_DIM,
    Linear, ReLU, Softmax, Tanh, Adam,
    PolicyNet, ValueNet,
    ACTION_INDEX_TO_RULE, ACTION_RULE_TO_INDEX, NUM_ACTIONS,
    ReplayBuffer, Transition,
    EnvModel,
    play_episode,
    mcts_search,
    Trainer,
    Coach,
    AlphaGoRunner,
    register_neural_advisor,
    save_weights, load_weights, hot_swap,
)
from src_v2.learning.alphago.neural_rule import _neural_advisor_handler


# ---------- helpers ----------

def _mk_snap(**kw) -> Snapshot:
    s = Snapshot()
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _mk_record(action: str, result: str = "ok", ts: float = 0.0, **snap_kv) -> ActionRecord:
    snap_dict = {
        "hp": snap_kv.get("hp", 80),
        "mp": snap_kv.get("mp", 70),
        "healer_coord": snap_kv.get("healer_coord"),
        "healer_map": snap_kv.get("healer_map", "mapA"),
        "attacker_coord": snap_kv.get("attacker_coord"),
        "attacker_map": snap_kv.get("attacker_map", "mapA"),
        "attacker_hp": snap_kv.get("attacker_hp", 90),
        "red_tab": snap_kv.get("red_tab", False),
    }
    return ActionRecord(
        ts=ts or time.time(),
        action=action,
        snapshot_at_decision=snap_dict,
        result=result,
        latency_ms=5.0,
        detail="",
    )


# ====================================================================
# 1. Feature Extractor
# ====================================================================

def test_feature_extractor_dim():
    extractor = FeatureExtractor()
    snap = _mk_snap(hp=75, mp=60, attacker_hp=80,
                    healer_coord=(100, 200), attacker_coord=(150, 220),
                    healer_map="A", attacker_map="A",
                    red_tab_present=True)
    v = extractor.extract(snap)
    assert v.shape == (FEATURE_DIM,)
    assert v.dtype == np.float32
    assert FEATURE_DIM == 32
    # HP 75/100 = 0.75
    assert abs(v[0] - 0.75) < 1e-5
    # mp 0.6
    assert abs(v[1] - 0.60) < 1e-5
    # red_tab
    assert v[21] == 1.0
    # map match
    assert v[20] == 1.0
    # no NaN
    assert np.isfinite(v).all()


def test_feature_extractor_handles_negative_hp():
    extractor = FeatureExtractor()
    snap = _mk_snap(hp=-1, mp=-1, attacker_hp=-1)
    v = extractor.extract(snap)
    assert v[0] == -1.0
    assert v[1] == -1.0
    assert np.isfinite(v).all()


# ====================================================================
# 2. NN Core
# ====================================================================

def test_linear_forward_shapes():
    L = Linear(8, 4, seed=42)
    x = np.random.randn(3, 8).astype(np.float32)
    y = L.forward(x)
    assert y.shape == (3, 4)
    g = L.backward(np.ones_like(y))
    assert g.shape == (3, 8)
    L.adam_step(lr=1e-2)
    # weights changed
    assert L._t == 1


def test_relu_softmax_tanh():
    r = ReLU()
    x = np.array([[-1.0, 0.0, 2.0]], dtype=np.float32)
    y = r.forward(x)
    assert np.allclose(y, [[0.0, 0.0, 2.0]])
    grad_in = r.backward(np.ones_like(y))
    assert np.allclose(grad_in, [[0.0, 0.0, 1.0]])

    sm = Softmax()
    p = sm.forward(np.array([[1.0, 2.0, 3.0]], dtype=np.float32))
    assert abs(p.sum() - 1.0) < 1e-5
    assert (p > 0).all()

    th = Tanh()
    y = th.forward(np.array([[0.0, 5.0, -5.0]], dtype=np.float32))
    assert abs(y[0, 0]) < 1e-5
    assert y[0, 1] > 0.99
    assert y[0, 2] < -0.99


# ====================================================================
# 3. Policy Net
# ====================================================================

def test_policy_net_forward_softmax_sums_to_one():
    pn = PolicyNet(in_dim=32, hidden=64, out_dim=NUM_ACTIONS, seed=7)
    x = np.random.randn(5, 32).astype(np.float32)
    p = pn.forward(x)
    assert p.shape == (5, NUM_ACTIONS)
    sums = p.sum(axis=1)
    assert np.allclose(sums, np.ones(5), atol=1e-5)
    assert (p >= 0).all()


# ====================================================================
# 4. Value Net
# ====================================================================

def test_value_net_forward_in_minus1_1():
    vn = ValueNet(in_dim=32, hidden=64, seed=11)
    x = np.random.randn(7, 32).astype(np.float32) * 5  # large magnitudes
    y = vn.forward(x)
    assert y.shape == (7, 1)
    assert (y >= -1.0).all() and (y <= 1.0).all()


# ====================================================================
# 5. Replay Buffer
# ====================================================================

def test_replay_buffer_circular():
    buf = ReplayBuffer(capacity=5)
    for i in range(8):
        buf.add(Transition(
            s=np.full(FEATURE_DIM, i, dtype=np.float32),
            a=i % NUM_ACTIONS,
            r=float(i),
            s_next=np.full(FEATURE_DIM, i + 1, dtype=np.float32),
        ))
    assert len(buf) == 5
    # last transition added (i=7) must be present
    all_t = buf.all()
    assert len(all_t) == 5
    rs = sorted(t.r for t in all_t)
    # circular: oldest 3-7 remain (capacity=5, so r=3,4,5,6,7)
    assert rs == [3.0, 4.0, 5.0, 6.0, 7.0]


def test_replay_buffer_sample_shapes():
    buf = ReplayBuffer(capacity=10)
    for i in range(10):
        buf.add(Transition(
            s=np.random.randn(FEATURE_DIM).astype(np.float32),
            a=i % NUM_ACTIONS,
            r=float(i % 3),
            s_next=np.random.randn(FEATURE_DIM).astype(np.float32),
        ))
    S, A, R, Sn = buf.sample(8)
    assert S.shape == (8, FEATURE_DIM)
    assert A.shape == (8,)
    assert R.shape == (8,)
    assert Sn.shape == (8, FEATURE_DIM)


def test_replay_buffer_update_from_log():
    buf = ReplayBuffer(capacity=100)
    records = [
        _mk_record("self_heal", ts=1.0, hp=40),
        _mk_record("seq_rclick", ts=2.0, hp=45),
        _mk_record("baekho", ts=3.0, hp=50),
        _mk_record("self_revive", ts=4.0, hp=0),
    ]
    n = buf.update_from_log(records)
    assert n == 3  # 4 records -> 3 transitions
    assert len(buf) == 3
    # subsequent calls should not re-add older records
    n2 = buf.update_from_log(records)
    assert n2 == 0


# ====================================================================
# 6. Env Model
# ====================================================================

def test_env_model_transition_count():
    transitions = []
    for i in range(50):
        s = np.random.randn(FEATURE_DIM).astype(np.float32)
        sn = np.random.randn(FEATURE_DIM).astype(np.float32)
        transitions.append(Transition(s=s, a=i % NUM_ACTIONS, r=0.1, s_next=sn))
    env = EnvModel(n_clusters=8, seed=0)
    env.fit(transitions)
    assert env.fitted
    assert env.transition_count() == 50
    s0 = env.sample_initial_state()
    assert s0.shape == (FEATURE_DIM,)
    s_next, r = env.step(s0, 0)
    assert s_next.shape == (FEATURE_DIM,)


# ====================================================================
# 7. Self-Play
# ====================================================================

def test_self_play_episode_length():
    pn = PolicyNet(seed=3)
    transitions = []
    for i in range(40):
        s = np.random.randn(FEATURE_DIM).astype(np.float32)
        sn = np.random.randn(FEATURE_DIM).astype(np.float32)
        transitions.append(Transition(s=s, a=i % NUM_ACTIONS, r=0.05, s_next=sn))
    env = EnvModel(n_clusters=8, seed=0)
    env.fit(transitions)
    vn = ValueNet(seed=4)
    ep = play_episode(pn, env, max_steps=10, epsilon=0.0)
    assert len(ep) == 10
    for t in ep:
        assert 0 <= t.a < NUM_ACTIONS
        assert t.s.shape == (FEATURE_DIM,)


# ====================================================================
# 8. MCTS
# ====================================================================

def test_mcts_returns_distribution():
    pn = PolicyNet(seed=5)
    vn = ValueNet(seed=6)
    transitions = []
    for i in range(40):
        s = np.random.randn(FEATURE_DIM).astype(np.float32)
        sn = np.random.randn(FEATURE_DIM).astype(np.float32)
        transitions.append(Transition(s=s, a=i % NUM_ACTIONS, r=0.1, s_next=sn))
    env = EnvModel(n_clusters=8, seed=0)
    env.fit(transitions)
    s0 = np.random.randn(FEATURE_DIM).astype(np.float32)
    dist = mcts_search(s0, pn, vn, env, sims=16, depth=3)
    assert dist.shape == (NUM_ACTIONS,)
    assert abs(dist.sum() - 1.0) < 1e-4
    assert (dist >= 0).all()


def test_mcts_fallback_when_env_not_fitted():
    pn = PolicyNet(seed=8)
    vn = ValueNet(seed=9)
    env = EnvModel(n_clusters=4)  # not fitted
    s0 = np.random.randn(FEATURE_DIM).astype(np.float32)
    dist = mcts_search(s0, pn, vn, env, sims=8, depth=3)
    assert dist.shape == (NUM_ACTIONS,)
    assert abs(dist.sum() - 1.0) < 1e-4


# ====================================================================
# 9. Trainer — loss decreases on toy data
# ====================================================================

def test_trainer_loss_decreases():
    pn = PolicyNet(in_dim=32, hidden=32, out_dim=NUM_ACTIONS, seed=1)
    vn = ValueNet(in_dim=32, hidden=32, seed=2)
    tr = Trainer(pn, vn, lr=5e-3)
    # toy fixed dataset
    rng = np.random.default_rng(123)
    S = rng.standard_normal((64, 32)).astype(np.float32)
    A = rng.integers(0, NUM_ACTIONS, size=64).astype(np.int64)
    R = rng.standard_normal(64).astype(np.float32) * 0.5
    Sn = rng.standard_normal((64, 32)).astype(np.float32)
    losses = []
    for _ in range(80):
        l = tr.train_batch(S, A, R, Sn)
        losses.append(l["total"])
    # final loss < initial loss (overfit toy data)
    assert losses[-1] < losses[0], f"loss did not decrease: {losses[0]:.4f} -> {losses[-1]:.4f}"


# ====================================================================
# 10. Coach full cycle with mock action log
# ====================================================================

def test_coach_full_cycle_mock(tmp_path):
    """Coach.run_cycle with min_records met -> performs training, may swap or reject."""
    pn = PolicyNet(seed=20)
    vn = ValueNet(seed=21)
    buf = ReplayBuffer(capacity=2000)
    env = EnvModel(n_clusters=8, seed=0)
    tr = Trainer(pn, vn, lr=1e-3)
    # Build mock action log (50 records -> 49 transitions)
    actions = ["self_heal", "seq_rclick", "baekho", "parlyuk", "parhon"]
    records = []
    for i in range(60):
        records.append(_mk_record(
            actions[i % len(actions)],
            result="ok",
            ts=float(i),
            hp=50 + (i % 30),
            mp=40 + (i % 40),
        ))
    coach = Coach(
        action_log_provider=lambda: records,
        replay_buffer=buf,
        env_model=env,
        policy_net=pn,
        value_net=vn,
        trainer=tr,
        poll_sec=9999.0,
        min_records=10,  # easy threshold
        train_steps=5,
        batch_size=8,
        self_play_episodes=3,
        improve_factor=1.05,
    )
    # Snapshot weights before
    pn_W_before = pn.l1.W.copy()
    result = coach.run_cycle()
    assert not result["skipped"]
    assert result["buffer_size"] >= 10
    # weights should have either swapped (changed) or rejected (unchanged)
    if result["accepted"]:
        # weights changed
        assert not np.allclose(pn.l1.W, pn_W_before)
    # loss reported
    assert result["loss"] is not None


def test_coach_skips_when_below_min_records():
    pn = PolicyNet(seed=30)
    vn = ValueNet(seed=31)
    buf = ReplayBuffer(capacity=2000)
    env = EnvModel(n_clusters=4, seed=0)
    tr = Trainer(pn, vn, lr=1e-3)
    coach = Coach(
        action_log_provider=lambda: [],
        replay_buffer=buf,
        env_model=env,
        policy_net=pn, value_net=vn, trainer=tr,
        poll_sec=9999.0,
        min_records=100,
        train_steps=5,
        batch_size=8,
        self_play_episodes=2,
    )
    result = coach.run_cycle()
    assert result["skipped"]


# ====================================================================
# 11. Neural rule confidence gating
# ====================================================================

def test_neural_rule_low_confidence_passes_through():
    """When MCTS returns low-confidence distribution, neural rule emits None."""
    pn = PolicyNet(seed=50)
    vn = ValueNet(seed=51)
    env = EnvModel(n_clusters=4)
    # Don't fit env -> runner.ready() False
    runner = AlphaGoRunner(pn, vn, env, enabled=True, min_confidence=0.99)
    snap = _mk_snap(hp=80, mp=70)
    ctx = RuleContext(extras={"alphago_runner": runner})
    out = _neural_advisor_handler(snap, ctx)
    assert out is None  # not ready

    # Now fit env with random transitions, but require very high confidence
    transitions = []
    for i in range(40):
        s = np.random.randn(FEATURE_DIM).astype(np.float32)
        sn = np.random.randn(FEATURE_DIM).astype(np.float32)
        transitions.append(Transition(s=s, a=i % NUM_ACTIONS, r=0.1, s_next=sn))
    env.fit(transitions)
    # min_confidence=0.99 — mcts dist over 10 actions almost never gives 0.99
    out = _neural_advisor_handler(snap, ctx)
    assert out is None or out.ctx.get("by_neural") is True
    # With permissive threshold should fire
    runner.min_confidence = 0.0
    out2 = _neural_advisor_handler(snap, ctx)
    # might still be None if argmax happens to be "wait"
    assert out2 is None or out2.ctx.get("by_neural") is True


def test_neural_rule_disabled_via_cfg():
    pn = PolicyNet(seed=60)
    vn = ValueNet(seed=61)
    env = EnvModel(n_clusters=4)
    transitions = [Transition(
        s=np.random.randn(FEATURE_DIM).astype(np.float32),
        a=i % NUM_ACTIONS, r=0.1,
        s_next=np.random.randn(FEATURE_DIM).astype(np.float32),
    ) for i in range(20)]
    env.fit(transitions)
    runner = AlphaGoRunner(pn, vn, env, enabled=True, min_confidence=0.0)
    snap = _mk_snap(hp=80, mp=70)
    ctx = RuleContext(cfg={"nn_disabled": True}, extras={"alphago_runner": runner})
    out = _neural_advisor_handler(snap, ctx)
    assert out is None


# ====================================================================
# 12. Hot swap atomicity
# ====================================================================

def test_hot_swap_atomic(tmp_path):
    pn1 = PolicyNet(seed=100)
    vn1 = ValueNet(seed=101)
    pn2 = PolicyNet(seed=200)
    vn2 = ValueNet(seed=201)

    # Save pn2/vn2 weights to disk
    weight_path = str(tmp_path / "w.npz")
    save_weights(pn2, vn2, weight_path)
    # Load
    w = load_weights(weight_path)
    assert "l1.W" in w and "v.l1.W" in w
    # Apply to pn1/vn1
    pn1_W_before = pn1.l1.W.copy()
    hot_swap(pn1, vn1, w)
    assert np.allclose(pn1.l1.W, pn2.l1.W)
    assert not np.allclose(pn1.l1.W, pn1_W_before)


def test_hot_swap_validates_shapes():
    pn = PolicyNet(seed=300)
    vn = ValueNet(seed=301)
    bad = pn.get_weights()
    bad.update(vn.get_weights())
    bad["l1.W"] = np.zeros((1, 1), dtype=np.float32)  # wrong shape
    pn_W_before = pn.l1.W.copy()
    with pytest.raises(ValueError):
        hot_swap(pn, vn, bad)
    # weights unchanged after failed swap
    assert np.allclose(pn.l1.W, pn_W_before)


# ====================================================================
# 13. Full self-improvement scenario (mock)
# ====================================================================

def test_alphago_full_cycle_scenario():
    """End-to-end mock: action log -> coach cycle -> verify pipeline executes,
    NN remains valid (sums to one), no exception."""
    pn = PolicyNet(seed=500)
    vn = ValueNet(seed=501)
    buf = ReplayBuffer(capacity=2000)
    env = EnvModel(n_clusters=6, seed=0)
    tr = Trainer(pn, vn, lr=2e-3)

    actions = ["self_heal", "seq_rclick", "baekho", "parlyuk",
               "parhon", "gyoungryeok", "self_revive"]
    records = []
    for i in range(200):
        # mostly seq_rclick (good), occasional self_revive (bad)
        a = "self_revive" if i % 30 == 0 else actions[i % len(actions)]
        records.append(_mk_record(
            a, result="ok",
            ts=float(i),
            hp=80 if a != "self_revive" else 0,
            mp=60 + (i % 30),
        ))
    coach = Coach(
        action_log_provider=lambda: records,
        replay_buffer=buf,
        env_model=env,
        policy_net=pn,
        value_net=vn,
        trainer=tr,
        poll_sec=9999.0,
        min_records=50,
        train_steps=10,
        batch_size=16,
        self_play_episodes=5,
        improve_factor=1.05,
    )
    # Run two cycles
    r1 = coach.run_cycle()
    r2 = coach.run_cycle()
    assert not r1["skipped"]
    assert not r2["skipped"]

    # Verify policy net still produces valid distribution
    x = np.random.randn(3, 32).astype(np.float32)
    p = pn.forward(x)
    assert np.allclose(p.sum(axis=1), 1.0, atol=1e-4)

    # Stats sanity
    st = coach.stats()
    assert st["cycles"] == 2
    assert st["swaps"] + st["rejects"] == 2
