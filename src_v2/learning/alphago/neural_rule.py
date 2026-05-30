"""Neural rule integration with Brain.

The neural advisor rule queries MCTS via an injected AlphaGoRunner.
Behaviour:
    - If runner is None or not ready: return None (defer to other rules).
    - Else: run MCTS at low budget (sims=16, depth=3).
    - If chosen action is "wait" or below confidence threshold: return None.
    - Otherwise: emit CastRequest with priority based on confidence.

Priority calculation:
    confidence in [0, 1] -> priority = max(1, int(100 - confidence * 80))
    Higher confidence -> lower priority -> earlier execution.

We use a HIGH base priority (999) on the rule itself so existing high-priority
emergency rules (self_heal at priority 10, self_revive at priority 1) still win
when their conditions are met. Neural advisor only fills the gaps.
"""
from __future__ import annotations
import logging
import threading
from typing import Optional

import numpy as np

from ...core.plugin_registry import PluginRegistry, RuleSpec
from ...core.snapshot import Snapshot
from ...core.types import CastRequest, RuleContext
from .env_model import EnvModel
from .feature_extractor import FeatureExtractor
from .mcts import mcts_search, mcts_search_move
from .move_policy_net import MovePolicyNet, MOVE_INDEX_TO_DIR, NUM_MOVE_ACTIONS
from .policy_net import PolicyNet, ACTION_INDEX_TO_RULE, NUM_ACTIONS
from .value_net import ValueNet

log = logging.getLogger("src_v2.learning.alphago.neural_rule")


class AlphaGoRunner:
    """Lightweight facade exposing decide(snap) -> (action_idx, confidence).

    HealerWorkerV2 holds an instance and injects via RuleContext.extras.
    """

    def __init__(self,
                 policy: PolicyNet,
                 value: ValueNet,
                 env: EnvModel,
                 enabled: bool = True,
                 min_confidence: float = 0.7,
                 mcts_sims: int = 16,
                 mcts_depth: int = 3,
                 move_policy: Optional[MovePolicyNet] = None,
                 movement_enabled: bool = False) -> None:
        self.policy = policy
        self.value = value
        self.env = env
        self.move_policy = move_policy
        self.movement_enabled = bool(movement_enabled)
        self.enabled = enabled
        self.min_confidence = float(min_confidence)
        self.mcts_sims = int(mcts_sims)
        self.mcts_depth = int(mcts_depth)
        self._extractor = FeatureExtractor()
        self._lock = threading.Lock()
        self._call_count = 0
        self._move_call_count = 0
        self._suggestion_count = 0
        self._move_suggestion_count = 0

    def ready(self) -> bool:
        return bool(self.enabled) and self.env.fitted

    def decide(self, snap: Snapshot) -> tuple[int, float]:
        """Return (best_action_idx, confidence).

        confidence = max prob of action distribution from MCTS (or policy prior fallback).
        """
        with self._lock:
            self._call_count += 1
            s = self._extractor.extract(snap)
            dist = mcts_search(
                s, self.policy, self.value, self.env,
                sims=self.mcts_sims, depth=self.mcts_depth,
            )
        a = int(np.argmax(dist))
        conf = float(dist[a])
        return a, conf

    def move_ready(self) -> bool:
        return (self.movement_enabled and self.move_policy is not None
                and self.env.fitted)

    def move_decide(self, snap: Snapshot) -> tuple[int, float]:
        """Return (best_move_idx, confidence). Stay/non-confident yields (4, low)."""
        if not self.move_ready():
            return NUM_MOVE_ACTIONS - 1, 0.0
        with self._lock:
            self._move_call_count += 1
            s = self._extractor.extract(snap)
            dist = mcts_search_move(
                s, self.move_policy, self.value, self.env,
                sims=self.mcts_sims, depth=self.mcts_depth,
            )
        a = int(np.argmax(dist))
        conf = float(dist[a])
        return a, conf

    def stats(self) -> dict:
        return {
            "calls": self._call_count,
            "suggestions_emitted": self._suggestion_count,
            "move_calls": self._move_call_count,
            "move_suggestions": self._move_suggestion_count,
            "enabled": self.enabled,
            "movement_enabled": self.movement_enabled,
            "ready": self.ready(),
            "move_ready": self.move_ready(),
            "min_confidence": self.min_confidence,
        }


def _neural_advisor_handler(snap: Snapshot, ctx: RuleContext) -> Optional[CastRequest]:
    runner: Optional[AlphaGoRunner] = ctx.extras.get("alphago_runner") if ctx.extras else None
    if runner is None or not runner.ready():
        return None
    if ctx.cfg.get("nn_disabled", False):
        return None
    a_idx, conf = runner.decide(snap)
    min_conf = float(ctx.cfg.get("nn_min_confidence", runner.min_confidence))
    if conf < min_conf:
        return None
    if a_idx >= NUM_ACTIONS:
        return None
    rule_name = ACTION_INDEX_TO_RULE[a_idx]
    if rule_name == "wait":
        return None
    if rule_name in ctx.in_progress:
        return None
    pri = max(1, int(100 - conf * 80))
    runner._suggestion_count += 1
    return CastRequest(name=rule_name, priority=pri, ctx={"by_neural": True, "nn_confidence": conf})


def _neural_movement_advisor_handler(snap: Snapshot, ctx: RuleContext) -> Optional[CastRequest]:
    """When STUCK is detected, consult NN move_policy for a detour direction.

    Triggered by topic 'muscle.stuck_detected' which muscle/main_loop publishes.
    Output is a CastRequest with name='muscle.move_hint' that the muscle layer
    consumes (it is NOT a real cast — hands ignore unknown names; brain reads ctx).

    Conservative gating:
        - movement_enabled flag must be True
        - confidence must exceed nn_min_confidence
        - "stay" suggestions are dropped (no override of deterministic logic)
    """
    runner: Optional[AlphaGoRunner] = ctx.extras.get("alphago_runner") if ctx.extras else None
    if runner is None or not runner.move_ready():
        return None
    if ctx.cfg.get("nn_disabled", False):
        return None
    if ctx.cfg.get("nn_movement_disabled", False):
        return None
    a_idx, conf = runner.move_decide(snap)
    min_conf = float(ctx.cfg.get("nn_min_confidence", runner.min_confidence))
    if conf < min_conf:
        return None
    direction = MOVE_INDEX_TO_DIR[a_idx] if 0 <= a_idx < NUM_MOVE_ACTIONS else "stay"
    if direction == "stay":
        return None
    runner._move_suggestion_count += 1
    return CastRequest(
        name="muscle.move_hint",
        priority=1,
        ctx={"direction": direction, "by_neural": True, "nn_confidence": conf},
    )


def register_neural_advisor(topics: Optional[list[str]] = None) -> None:
    """Register the neural advisor rule with PluginRegistry. Idempotent."""
    if topics is None:
        topics = ["eye.hp", "eye.mp", "eye.cooldown", "eye.attacker_state",
                  "eye.yolo", "eye.frame"]
    spec = RuleSpec(
        name="neural_advisor",
        priority=999,
        topics=list(topics),
        handler=_neural_advisor_handler,
        enabled=True,
        description="AlphaGo-style policy net advisor (NN-driven action suggestion).",
    )
    PluginRegistry.register_rule(spec)


def register_neural_movement_advisor(topics: Optional[list[str]] = None) -> None:
    """Register the movement advisor. Triggered by 'muscle.stuck_detected'."""
    if topics is None:
        topics = ["muscle.stuck_detected"]
    spec = RuleSpec(
        name="neural_movement_advisor",
        priority=998,
        topics=list(topics),
        handler=_neural_movement_advisor_handler,
        enabled=True,
        description="AlphaGo move policy advisor (STUCK detour direction).",
    )
    PluginRegistry.register_rule(spec)


__all__ = ["AlphaGoRunner", "register_neural_advisor", "register_neural_movement_advisor"]
