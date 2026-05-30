"""@learnable target declarations + builtin tuning targets.

Phase 7. Targets here mirror the rule_cfg keys used in HealerConfig.rule_cfg.
target_id convention: ``rule.<rule_name>.<param>``.

Why: explicit declaration is the primary source of truth for what may be tuned;
meta_learner.auto_propose_candidates only fills in the gaps for unknown
patterns it discovers in action_log.

Range / safety: range is the *normal* tuning band; safety is the *hard* clamp
that even an extreme proposal cannot violate.
"""
from __future__ import annotations
import logging
from typing import List

from ..core.plugin_registry import PluginRegistry, LearnableSpec, learnable

log = logging.getLogger("src_v2.learning.learnable")


def builtin_learnables() -> List[LearnableSpec]:
    """All factory-shipped tuning targets.

    Defaults match HealerConfig.rule_cfg defaults so first registration is a
    no-op behaviorally.
    """
    return [
        LearnableSpec(
            target_id="rule.self_heal.hp_thr",
            range=(30.0, 70.0),
            safety=(20.0, 85.0),
            fitness="lower_death_rate",
            default=50,
            description="HP% threshold below which self-heal triggers.",
        ),
        LearnableSpec(
            target_id="rule.self_heal.burst_count",
            range=(1, 5),
            safety=(1, 8),
            fitness="lower_death_rate",
            default=3,
            description="Number of self-heal presses in a burst.",
        ),
        LearnableSpec(
            target_id="rule.self_heal.burst_gap_ms",
            range=(40, 200),
            safety=(20, 400),
            fitness="lower_death_rate",
            default=80,
            description="Gap between self-heal burst presses in ms.",
        ),
        LearnableSpec(
            target_id="rule.gyoungryeok.mp_thr",
            range=(15.0, 50.0),
            safety=(10.0, 70.0),
            fitness="higher_uptime",
            default=30,
            description="MP% threshold above which gyoungryeok casts.",
        ),
        LearnableSpec(
            target_id="rule.parhon.edge_sec",
            range=(1.0, 6.0),
            safety=(0.5, 10.0),
            fitness="higher_buff_uptime",
            default=3,
            description="Seconds-before-expiry to re-cast parhon.",
        ),
        LearnableSpec(
            target_id="rule.seq_rclick.duration_ms",
            range=(800, 2400),
            safety=(400, 4000),
            fitness="higher_xp_rate",
            default=1500,
            description="Duration of seq_rclick action in ms.",
        ),
        LearnableSpec(
            target_id="rule.seq_rclick.interval_ms",
            range=(300, 1200),
            safety=(150, 2000),
            fitness="higher_xp_rate",
            default=500,
            description="Idle gap between seq_rclick actions in ms.",
        ),
    ]


def declare_learnables() -> int:
    """Register all builtin targets. Idempotent.

    Returns the number of targets registered.
    """
    n = 0
    for spec in builtin_learnables():
        PluginRegistry.register_learnable(spec)
        n += 1
    log.info("declared %d builtin learnable targets", n)
    return n


# --- Convenience: re-export decorator for user-defined targets ---
__all__ = ["declare_learnables", "builtin_learnables", "learnable"]
