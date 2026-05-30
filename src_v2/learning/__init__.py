"""Self-evolving subsystem (Phase 7).

Pipeline (per cycle, every poll_sec):
    ActionLog.snapshot()
        |
        v
    MetaLearner.score_targets()       -> list of (target_id, score)
        |  (filter min_score_threshold)
        v
    Optimizer.propose(target_id)       -> proposed value (numpy-only)
        |
        v
    HotApply.apply(target_id, value)   -> registers params via PluginRegistry.set_param
        |  (snapshot before)
        v
    [wait rollback_window_sec]
        |
        v
    Fitness.eval(target_id) compared to baseline
        |
        +- worse by >= regression_factor: HotApply.rollback()
        +- equal/better: keep new value

External entry: MetaLearnerRunner thread.

Design ref: §7
"""

from .learnable import declare_learnables, builtin_learnables
from .meta_learner import MetaLearner, ScoreEntry
from .optimizer import Optimizer, BanditOptimizer
from .hot_apply import HotApply, RollbackToken
from .fitness import FitnessRegistry, register_builtin_fitness
from .runner import MetaLearnerRunner

__all__ = [
    "declare_learnables",
    "builtin_learnables",
    "MetaLearner",
    "ScoreEntry",
    "Optimizer",
    "BanditOptimizer",
    "HotApply",
    "RollbackToken",
    "FitnessRegistry",
    "register_builtin_fitness",
    "MetaLearnerRunner",
]
