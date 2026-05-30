"""HotApply — atomically apply param + register rollback token.

Phase 7. Steps for each apply():
  1. snapshot current param value (before)
  2. clamp proposed value via spec.range and spec.safety
  3. PluginRegistry.set_param(target_id, clamped) -> copy-on-write swap
  4. capture baseline fitness from records (records ref BEFORE apply)
  5. return RollbackToken

Rollback: if fitness measured later >= regression_factor * baseline degradation,
caller invokes rollback_token.rollback(records_after) which:
  - restores previous param value via set_param
  - logs the rollback
"""
from __future__ import annotations
import logging
import time
from dataclasses import dataclass
from typing import Any, List, Optional

from ..core.plugin_registry import PluginRegistry, LearnableSpec
from ..core.types import ActionRecord
from .fitness import FitnessRegistry

log = logging.getLogger("src_v2.learning.hot_apply")


@dataclass
class RollbackToken:
    """Holds enough state to undo an apply().

    rollback_window_sec: how long after apply() before regression is measured.
    """
    target_id: str
    fitness_name: str
    prev_value: Any
    new_value: Any
    baseline_score: float
    applied_at: float
    rollback_window_sec: float
    regression_factor: float
    rolled_back: bool = False


class HotApply:
    """Apply + measure + rollback orchestrator.

    Intentionally not a thread — runner manages timing.
    """

    def __init__(self,
                 fitness: FitnessRegistry,
                 rollback_window_sec: float = 300.0,
                 regression_factor: float = 1.1) -> None:
        self.fitness = fitness
        self.rollback_window_sec = float(rollback_window_sec)
        self.regression_factor = float(regression_factor)
        self._tokens: List[RollbackToken] = []
        self._applied_count = 0
        self._rollback_count = 0

    # ---------- apply ----------

    def apply(self,
              spec: LearnableSpec,
              proposed_value: Any,
              records_before: List[ActionRecord]) -> Optional[RollbackToken]:
        clamped = self._clamp(spec, proposed_value)
        prev = PluginRegistry.get_param(spec.target_id, default=spec.default)

        baseline = 0.0
        if spec.fitness:
            v = self.fitness.eval(spec.fitness, records_before)
            if v is not None:
                baseline = v

        ok = PluginRegistry.set_param(spec.target_id, clamped)
        if not ok:
            log.warning("hot_apply: set_param failed for %s", spec.target_id)
            return None

        token = RollbackToken(
            target_id=spec.target_id,
            fitness_name=spec.fitness or "",
            prev_value=prev,
            new_value=clamped,
            baseline_score=baseline,
            applied_at=time.monotonic(),
            rollback_window_sec=self.rollback_window_sec,
            regression_factor=self.regression_factor,
        )
        self._tokens.append(token)
        self._applied_count += 1
        log.info("hot_apply %s: %r -> %r (baseline=%.3f)",
                 spec.target_id, prev, clamped, baseline)
        return token

    # ---------- rollback ----------

    def maybe_rollback(self,
                       token: RollbackToken,
                       records_after: List[ActionRecord]) -> bool:
        """Check if regression occurred; rollback if so.

        Returns True if rollback happened.
        """
        if token.rolled_back:
            return False
        if not token.fitness_name:
            return False  # no fitness defined, can't rollback intelligently

        elapsed = time.monotonic() - token.applied_at
        if elapsed < token.rollback_window_sec:
            return False  # too early to judge

        new_score = self.fitness.eval(token.fitness_name, records_after)
        if new_score is None:
            return False

        # higher = better convention. degradation if new < baseline by factor.
        # If baseline is 0 and new is 0 -> no change (skip).
        if token.baseline_score <= 0 and new_score <= 0:
            return False

        # Allow small noise — only rollback if new < baseline / regression_factor.
        threshold = token.baseline_score / token.regression_factor
        if new_score < threshold:
            self._do_rollback(token)
            return True
        return False

    def _do_rollback(self, token: RollbackToken) -> None:
        PluginRegistry.set_param(token.target_id, token.prev_value, force=True)
        token.rolled_back = True
        self._rollback_count += 1
        log.warning("rollback %s: restoring %r (was %r)",
                    token.target_id, token.prev_value, token.new_value)

    # ---------- helpers ----------

    def _clamp(self, spec: LearnableSpec, value: Any) -> Any:
        """Clamp numeric value into safety, then range. Non-numeric pass-through."""
        try:
            v = float(value)
        except (TypeError, ValueError):
            return value
        if spec.safety:
            lo_s, hi_s = spec.safety
            v = max(float(lo_s), min(float(hi_s), v))
        lo, hi = spec.range
        v = max(float(lo), min(float(hi), v))
        # preserve int type if range bounds are int-like
        if isinstance(spec.default, int) and not isinstance(spec.default, bool):
            return int(round(v))
        return v

    def stats(self) -> dict:
        return {
            "applied": self._applied_count,
            "rollbacks": self._rollback_count,
            "pending": sum(1 for t in self._tokens if not t.rolled_back),
        }

    def pending_tokens(self) -> List[RollbackToken]:
        return [t for t in self._tokens if not t.rolled_back]


__all__ = ["HotApply", "RollbackToken"]
