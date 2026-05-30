"""Fitness functions — derive a scalar score from action_log snapshot.

Phase 7. Single source: ActionLog.all() (no external metrics).

Convention: HIGHER fitness = BETTER.
- For metrics that are naturally "lower-is-better" (e.g. death rate),
  the function returns -value or (1 - rate) so direction is consistent.

All fitness functions take a list of ActionRecord and return float.
"""
from __future__ import annotations
import logging
from typing import Callable, Dict, List, Optional

from ..core.types import ActionRecord

log = logging.getLogger("src_v2.learning.fitness")

FitnessFn = Callable[[List[ActionRecord]], float]


class FitnessRegistry:
    """Name -> fitness function. Shared between MetaLearner and HotApply.

    Instance-level (not class-level) so tests can swap.
    """

    def __init__(self) -> None:
        self._fns: Dict[str, FitnessFn] = {}

    def register(self, name: str, fn: FitnessFn) -> None:
        self._fns[name] = fn

    def get(self, name: str) -> Optional[FitnessFn]:
        return self._fns.get(name)

    def eval(self, name: str, records: List[ActionRecord]) -> Optional[float]:
        fn = self._fns.get(name)
        if fn is None:
            return None
        try:
            return float(fn(records))
        except Exception:  # noqa: BLE001
            log.exception("fitness '%s' raised", name)
            return None

    def list_names(self) -> List[str]:
        return list(self._fns.keys())


# ===== Built-in fitness functions =====

def _count(records: List[ActionRecord], action_prefix: str) -> int:
    return sum(1 for r in records if r.action.startswith(action_prefix))


def _ok_count(records: List[ActionRecord], action_prefix: str) -> int:
    return sum(
        1 for r in records
        if r.action.startswith(action_prefix) and r.result == "ok"
    )


def lower_death_rate(records: List[ActionRecord]) -> float:
    """Fewer self_revive relative to self_heal -> higher fitness.

    fitness = 1.0 - revive/(heal+revive+epsilon).
    """
    revives = _count(records, "self_revive")
    heals = _count(records, "self_heal")
    total = revives + heals
    if total == 0:
        return 1.0  # no events -> max (no deaths happened)
    return 1.0 - (revives / total)


def higher_uptime(records: List[ActionRecord]) -> float:
    """Higher ratio of skill ok-results -> higher fitness."""
    n_total = len(records)
    if n_total == 0:
        return 0.5
    n_ok = sum(1 for r in records if r.result == "ok")
    return n_ok / n_total


def higher_buff_uptime(records: List[ActionRecord]) -> float:
    """Buff-related skills (parhon/baekho/parlyuk) success ratio."""
    buffs = [r for r in records
             if r.action in ("parhon", "baekho", "parlyuk")]
    if not buffs:
        return 0.5
    ok = sum(1 for r in buffs if r.result == "ok")
    return ok / len(buffs)


def higher_xp_rate(records: List[ActionRecord]) -> float:
    """Approximate via seq_rclick ok rate."""
    seqs = [r for r in records if r.action == "seq_rclick"]
    if not seqs:
        return 0.5
    ok = sum(1 for r in seqs if r.result == "ok")
    return ok / len(seqs)


def register_builtin_fitness(reg: FitnessRegistry) -> None:
    reg.register("lower_death_rate", lower_death_rate)
    reg.register("higher_uptime", higher_uptime)
    reg.register("higher_buff_uptime", higher_buff_uptime)
    reg.register("higher_xp_rate", higher_xp_rate)


__all__ = [
    "FitnessRegistry",
    "FitnessFn",
    "register_builtin_fitness",
    "lower_death_rate",
    "higher_uptime",
    "higher_buff_uptime",
    "higher_xp_rate",
]
