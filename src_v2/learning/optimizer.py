"""Optimizer — propose new value for a target_id.

Phase 7. Pure numpy + stdlib (no scikit-learn).

Two strategies:
- BanditOptimizer (UCB1) — discrete arms within range; good for small finite param sets.
- LinearRegressionOptimizer — fits param->fitness on history, picks gradient direction.

Default Optimizer = UCB1 with 5 arms across the spec.range.
"""
from __future__ import annotations
import logging
import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

try:
    import numpy as np  # type: ignore
    HAS_NUMPY = True
except Exception:  # noqa: BLE001
    HAS_NUMPY = False

from ..core.plugin_registry import LearnableSpec

log = logging.getLogger("src_v2.learning.optimizer")


@dataclass
class _ArmStats:
    pulls: int = 0
    sum_reward: float = 0.0

    @property
    def mean(self) -> float:
        return self.sum_reward / self.pulls if self.pulls > 0 else 0.0


class BanditOptimizer:
    """UCB1 over `n_arms` evenly-spaced points within spec.range.

    Per target_id keeps independent arm stats.
    """

    def __init__(self, n_arms: int = 5, c: float = 1.4,
                 rng: Optional[random.Random] = None) -> None:
        self.n_arms = max(2, int(n_arms))
        self.c = float(c)
        self.rng = rng or random.Random()
        self._arms: Dict[str, List[_ArmStats]] = {}
        self._values: Dict[str, List[float]] = {}

    def _ensure(self, spec: LearnableSpec) -> None:
        if spec.target_id in self._arms:
            return
        lo, hi = spec.range
        if self.n_arms == 1:
            values = [(lo + hi) / 2]
        else:
            step = (hi - lo) / (self.n_arms - 1)
            values = [lo + i * step for i in range(self.n_arms)]
        self._arms[spec.target_id] = [_ArmStats() for _ in range(self.n_arms)]
        self._values[spec.target_id] = values

    def propose(self, spec: LearnableSpec) -> float:
        """UCB1 selection. Falls back to uniform random if no spec."""
        self._ensure(spec)
        arms = self._arms[spec.target_id]
        values = self._values[spec.target_id]

        # initial: pull each arm once
        total_pulls = sum(a.pulls for a in arms)
        for i, a in enumerate(arms):
            if a.pulls == 0:
                return values[i]

        # UCB1
        best_i = 0
        best_score = -math.inf
        for i, a in enumerate(arms):
            ucb = a.mean + self.c * math.sqrt(math.log(total_pulls) / a.pulls)
            if ucb > best_score:
                best_score = ucb
                best_i = i
        return values[best_i]

    def update(self, target_id: str, value: float, reward: float) -> None:
        """Record reward for the closest arm to value."""
        if target_id not in self._arms:
            return
        arms = self._arms[target_id]
        values = self._values[target_id]
        # closest arm
        best_i = min(range(len(values)), key=lambda i: abs(values[i] - value))
        arms[best_i].pulls += 1
        arms[best_i].sum_reward += reward

    def stats(self, target_id: str) -> Dict[str, List[Dict[str, float]]]:
        if target_id not in self._arms:
            return {"arms": []}
        arms = self._arms[target_id]
        values = self._values[target_id]
        return {"arms": [
            {"value": float(values[i]), "pulls": int(a.pulls),
             "mean": float(a.mean)}
            for i, a in enumerate(arms)
        ]}


class LinearRegressionOptimizer:
    """Fit fitness as linear function of parameter value, propose along gradient.

    Pure numpy via np.linalg.lstsq. Falls back to bandit if numpy unavailable.
    """

    def __init__(self, step_frac: float = 0.1,
                 rng: Optional[random.Random] = None) -> None:
        self.step_frac = float(step_frac)
        self.rng = rng or random.Random()
        self._history: Dict[str, List[Tuple[float, float]]] = {}

    def record(self, target_id: str, value: float, reward: float) -> None:
        self._history.setdefault(target_id, []).append((float(value), float(reward)))
        # cap history
        if len(self._history[target_id]) > 200:
            self._history[target_id] = self._history[target_id][-200:]

    def propose(self, spec: LearnableSpec) -> float:
        history = self._history.get(spec.target_id, [])
        lo, hi = spec.range
        center = (lo + hi) / 2

        if not HAS_NUMPY or len(history) < 4:
            # explore: random within range
            return self.rng.uniform(lo, hi)

        xs = np.array([h[0] for h in history], dtype=float)
        ys = np.array([h[1] for h in history], dtype=float)
        # fit y = a*x + b
        A = np.vstack([xs, np.ones_like(xs)]).T
        sol, _res, _rank, _sv = np.linalg.lstsq(A, ys, rcond=None)
        slope = float(sol[0])

        step = (hi - lo) * self.step_frac
        # last best x
        best_idx = int(np.argmax(ys))
        anchor = float(xs[best_idx])
        proposal = anchor + (step if slope >= 0 else -step)

        # add small noise so we don't get stuck
        proposal += self.rng.uniform(-step * 0.1, step * 0.1)
        # clamp to range
        return max(lo, min(hi, proposal))


# Default = bandit (no sklearn deps, faster convergence on small arm count)
class Optimizer(BanditOptimizer):
    """Default optimizer alias — UCB1 bandit."""


__all__ = ["Optimizer", "BanditOptimizer", "LinearRegressionOptimizer"]
