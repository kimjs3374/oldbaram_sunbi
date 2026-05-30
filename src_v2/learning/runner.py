"""MetaLearnerRunner — daemon thread that runs the learning cycle.

Phase 7. Cycle (every poll_sec):
    1. records = action_log.all()
    2. entries = meta_learner.score_targets(records)
    3. filtered = meta_learner.filter_above_threshold(entries)
    4. for each entry with a registered LearnableSpec:
        - proposal = optimizer.propose(spec)
        - token = hot_apply.apply(spec, proposal, records)
    5. for each pending token: hot_apply.maybe_rollback(token, records)
    6. for each rolled-back token: optimizer.update(token.target_id,
                                                   new_value, low reward)
    7. for each settled (kept) token: optimizer.update(value, current fitness)

Thread starts on .start(); stops on .stop(). Daemon=True so process can exit.
"""
from __future__ import annotations
import logging
import threading
import time
from typing import List, Optional

from ..memory.action_log import ActionLog
from ..core.plugin_registry import PluginRegistry
from .meta_learner import MetaLearner
from .optimizer import Optimizer
from .hot_apply import HotApply, RollbackToken
from .fitness import FitnessRegistry, register_builtin_fitness

log = logging.getLogger("src_v2.learning.runner")


class MetaLearnerRunner:
    """Owns thread + composes meta_learner + optimizer + hot_apply."""

    def __init__(self,
                 action_log: ActionLog,
                 *,
                 poll_sec: float = 300.0,
                 min_score_threshold: float = 0.5,
                 rollback_window_sec: float = 300.0,
                 regression_factor: float = 1.1,
                 fitness: Optional[FitnessRegistry] = None,
                 meta: Optional[MetaLearner] = None,
                 optimizer: Optional[Optimizer] = None,
                 hot_apply: Optional[HotApply] = None,
                 max_targets_per_cycle: int = 1) -> None:
        self.action_log = action_log
        self.poll_sec = float(poll_sec)
        self.max_targets_per_cycle = int(max_targets_per_cycle)

        self.fitness = fitness or FitnessRegistry()
        if not self.fitness.list_names():
            register_builtin_fitness(self.fitness)

        self.meta = meta or MetaLearner(
            self.fitness, min_score_threshold=min_score_threshold,
        )
        self.optimizer = optimizer or Optimizer()
        self.hot_apply = hot_apply or HotApply(
            self.fitness,
            rollback_window_sec=rollback_window_sec,
            regression_factor=regression_factor,
        )

        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._cycles = 0
        self._lock = threading.Lock()

    # ---------- thread control ----------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._loop, name="MetaLearnerRunner", daemon=True,
        )
        self._thread.start()
        log.info("MetaLearnerRunner started (poll=%.1fs)", self.poll_sec)

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_evt.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=timeout)
        log.info("MetaLearnerRunner stopped (cycles=%d)", self._cycles)

    # ---------- single cycle (also used by tests) ----------

    def run_once(self) -> dict:
        with self._lock:
            return self._cycle_once()

    def _cycle_once(self) -> dict:
        records = self.action_log.all()
        if not records:
            return {"cycles": self._cycles, "skipped": "no records"}

        entries = self.meta.score_targets(records)
        filtered = self.meta.filter_above_threshold(entries)

        applied = 0
        for entry in filtered[: self.max_targets_per_cycle]:
            if entry.spec is None:
                # auto-discovered without an explicit spec — skip apply
                continue
            try:
                proposal = self.optimizer.propose(entry.spec)
            except Exception:  # noqa: BLE001
                log.exception("optimizer.propose raised for %s", entry.target_id)
                continue
            token = self.hot_apply.apply(entry.spec, proposal, records)
            if token is not None:
                applied += 1

        # rollback check on pending tokens
        rolled = 0
        for token in list(self.hot_apply.pending_tokens()):
            if self.hot_apply.maybe_rollback(token, records):
                rolled += 1
                # bandit: punish the rolled-back arm
                self.optimizer.update(token.target_id, token.new_value, 0.0)
            else:
                # if window passed and not rolled back, reward
                if (time.monotonic() - token.applied_at
                        >= token.rollback_window_sec):
                    score = self.fitness.eval(token.fitness_name, records)
                    if score is not None:
                        self.optimizer.update(token.target_id, token.new_value,
                                              float(score))

        self._cycles += 1
        return {
            "cycles": self._cycles,
            "scored": len(entries),
            "above_threshold": len(filtered),
            "applied": applied,
            "rolled_back": rolled,
        }

    # ---------- thread loop ----------

    def _loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self.run_once()
            except Exception:  # noqa: BLE001
                log.exception("MetaLearnerRunner cycle error")
            # interruptible sleep
            self._stop_evt.wait(self.poll_sec)

    # ---------- inspection ----------

    def stats(self) -> dict:
        return {
            "cycles": self._cycles,
            "hot_apply": self.hot_apply.stats(),
            "fitness_fns": self.fitness.list_names(),
            "learnables": [s.target_id for s in
                           PluginRegistry.list_learnables()],
            "params": PluginRegistry.snapshot_params(),
        }


__all__ = ["MetaLearnerRunner"]
