"""MetaLearner — score targets + auto-discover candidates.

Phase 7. Decides *what* to tune; Optimizer decides *how*.

Two sources of targets:
1. @learnable explicit registrations (PluginRegistry.list_learnables) — preferred.
2. action_log auto-discovery — fills in unknown patterns.

Score per target = (frequency * volatility) / (1 + recent_ok_ratio).

Higher score = better candidate to tune (frequent + volatile + struggling).
"""
from __future__ import annotations
import logging
import math
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..core.plugin_registry import PluginRegistry, LearnableSpec
from ..core.types import ActionRecord
from .fitness import FitnessRegistry

log = logging.getLogger("src_v2.learning.meta")


@dataclass
class ScoreEntry:
    target_id: str
    score: float
    frequency: int
    volatility: float
    recent_ok_ratio: float
    spec: Optional[LearnableSpec] = None


class MetaLearner:
    """Pure logic — no threads. Run from MetaLearnerRunner."""

    def __init__(self,
                 fitness: FitnessRegistry,
                 min_score_threshold: float = 0.5) -> None:
        self.fitness = fitness
        self.min_score_threshold = float(min_score_threshold)

    # ---------- public ----------

    def score_targets(self, records: List[ActionRecord]) -> List[ScoreEntry]:
        """Compute scores for all known + auto-discovered targets.

        Returns list sorted by score DESC.
        """
        # 1. action -> records mapping
        per_action: Dict[str, List[ActionRecord]] = {}
        for r in records:
            per_action.setdefault(r.action, []).append(r)

        # 2. explicit learnables -> map action -> target_id
        explicit = PluginRegistry.list_learnables()
        explicit_by_action: Dict[str, List[LearnableSpec]] = {}
        for spec in explicit:
            # target_id like "rule.self_heal.hp_thr" -> action "self_heal"
            parts = spec.target_id.split(".")
            if len(parts) >= 2:
                action = parts[1]
                explicit_by_action.setdefault(action, []).append(spec)

        entries: List[ScoreEntry] = []
        seen_targets: set = set()

        # 3. score explicit targets
        for action, action_records in per_action.items():
            specs = explicit_by_action.get(action, [])
            for spec in specs:
                e = self._score_one(spec.target_id, action_records, spec=spec)
                entries.append(e)
                seen_targets.add(spec.target_id)

        # 4. auto-discovery: synthetic target_id "auto.<action>"
        for action, action_records in per_action.items():
            if action not in explicit_by_action and len(action_records) >= 5:
                target_id = f"auto.{action}"
                if target_id in seen_targets:
                    continue
                e = self._score_one(target_id, action_records, spec=None)
                entries.append(e)

        entries.sort(key=lambda x: x.score, reverse=True)
        return entries

    def filter_above_threshold(self,
                               entries: List[ScoreEntry]) -> List[ScoreEntry]:
        return [e for e in entries if e.score >= self.min_score_threshold]

    # ---------- internal ----------

    def _score_one(self,
                   target_id: str,
                   records: List[ActionRecord],
                   spec: Optional[LearnableSpec]) -> ScoreEntry:
        n = len(records)
        if n == 0:
            return ScoreEntry(target_id=target_id, score=0.0, frequency=0,
                              volatility=0.0, recent_ok_ratio=1.0, spec=spec)

        # frequency: log-scaled count
        frequency = n
        freq_term = math.log1p(n)

        # volatility: result diversity (Shannon entropy of result strings)
        result_counts = Counter(r.result for r in records)
        total = sum(result_counts.values())
        vol = 0.0
        for c in result_counts.values():
            p = c / total
            if p > 0:
                vol -= p * math.log(p)

        # recent_ok_ratio: last 30
        recent = records[-30:]
        ok = sum(1 for r in recent if r.result == "ok")
        ok_ratio = ok / len(recent)

        # combined
        score = (freq_term * (vol + 0.1)) / (1.0 + ok_ratio)

        return ScoreEntry(
            target_id=target_id,
            score=score,
            frequency=frequency,
            volatility=vol,
            recent_ok_ratio=ok_ratio,
            spec=spec,
        )


__all__ = ["MetaLearner", "ScoreEntry"]
