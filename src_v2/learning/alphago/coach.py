"""Coach — orchestrator that runs in a daemon thread.

Each cycle (every poll_sec seconds):
    1. Snapshot ActionLog -> ReplayBuffer.update_from_log
    2. Self-play to augment buffer (env_model required to be fitted first)
    3. NN training: N batches
    4. Evaluation: compare new vs old fitness on a held-out window
    5. If improved by >= regression_factor: hot_swap weights into the LIVE nets
       Else: revert to backup weights.

The "live" nets used by neural_advisor rule are the same Python objects -
hot_swap mutates their .W / .b in place under a lock.
"""
from __future__ import annotations
import logging
import threading
import time
from typing import Callable, List, Optional

import numpy as np

from ...core.types import ActionRecord
from .env_model import EnvModel
from .policy_net import PolicyNet
from .replay_buffer import ReplayBuffer
from .self_play import play_episode
from .trainer import Trainer
from .value_net import ValueNet
from .weight_io import hot_swap

log = logging.getLogger("src_v2.learning.alphago.coach")


class Coach(threading.Thread):
    """Background trainer.

    Constructor args:
        action_log_provider: callable -> List[ActionRecord]  (e.g. ActionLog.all)
        replay_buffer:       ReplayBuffer
        env_model:           EnvModel  (will be re-fit each cycle)
        policy_net:          PolicyNet — LIVE net used by neural rule
        value_net:           ValueNet  — LIVE net used by neural rule
        trainer:             Trainer (operates on LIVE nets — but we backup weights first)
        poll_sec:            seconds between cycles
        min_records:         skip cycle until buffer has this many transitions
        train_steps:         number of batches per cycle
        batch_size:          batch size
        self_play_episodes:  number of episodes to roll per cycle
        improve_factor:      keep new weights only if new_avg_reward > old_avg_reward * factor
    """

    def __init__(self,
                 action_log_provider: Callable[[], List[ActionRecord]],
                 replay_buffer: ReplayBuffer,
                 env_model: EnvModel,
                 policy_net: PolicyNet,
                 value_net: ValueNet,
                 trainer: Trainer,
                 poll_sec: float = 1800.0,
                 min_records: int = 1000,
                 train_steps: int = 100,
                 batch_size: int = 64,
                 self_play_episodes: int = 50,
                 improve_factor: float = 1.05) -> None:
        super().__init__(daemon=True, name="alphago-coach")
        self.action_log_provider = action_log_provider
        self.replay_buffer = replay_buffer
        self.env_model = env_model
        self.policy_net = policy_net
        self.value_net = value_net
        self.trainer = trainer
        self.poll_sec = float(poll_sec)
        self.min_records = int(min_records)
        self.train_steps = int(train_steps)
        self.batch_size = int(batch_size)
        self.self_play_episodes = int(self_play_episodes)
        self.improve_factor = float(improve_factor)
        self._stop = threading.Event()
        self._cycle_count = 0
        self._swap_count = 0
        self._reject_count = 0
        self._last_loss = None

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self.is_alive():
            self.join(timeout=timeout)

    # ---- the loop ----

    def run(self) -> None:
        log.info("Coach start, poll_sec=%.1f", self.poll_sec)
        while not self._stop.is_set():
            try:
                self.run_cycle()
            except Exception:  # noqa: BLE001
                log.exception("coach cycle crashed")
            # sleep with stop check
            self._stop.wait(self.poll_sec)
        log.info("Coach stop")

    def run_cycle(self) -> dict:
        """Single iteration. Public for tests."""
        self._cycle_count += 1
        # 1. update buffer from action log
        records = self.action_log_provider() or []
        added = self.replay_buffer.update_from_log(records)

        if len(self.replay_buffer) < self.min_records:
            return {
                "cycle": self._cycle_count,
                "skipped": True,
                "reason": "min_records_not_met",
                "buffer_size": len(self.replay_buffer),
                "added_from_log": added,
            }

        # 2. fit env model on buffer
        self.env_model.fit(self.replay_buffer.all())

        # 3. self-play
        for _ in range(self.self_play_episodes):
            ep = play_episode(self.policy_net, self.env_model, max_steps=20)
            for t in ep:
                self.replay_buffer.add(t)

        # 4. backup weights for rollback
        backup_p = self.policy_net.get_weights()
        backup_v = self.value_net.get_weights()
        before_reward = self._eval_avg_reward()

        # 5. train
        last_loss = None
        for _ in range(self.train_steps):
            S, A, R, Sn = self.replay_buffer.sample(self.batch_size)
            last_loss = self.trainer.train_batch(S, A, R, Sn)
        self._last_loss = last_loss

        # 6. eval after
        after_reward = self._eval_avg_reward()

        # 7. accept or reject
        accepted = self._should_accept(before_reward, after_reward)
        if not accepted:
            # rollback by hot_swap to backup
            try:
                hot_swap(self.policy_net, self.value_net, {**backup_p, **backup_v})
            except Exception:
                # fallback: direct set
                self.policy_net.set_weights(backup_p)
                self.value_net.set_weights(backup_v)
            self._reject_count += 1
        else:
            self._swap_count += 1

        return {
            "cycle": self._cycle_count,
            "skipped": False,
            "buffer_size": len(self.replay_buffer),
            "added_from_log": added,
            "before_reward": before_reward,
            "after_reward": after_reward,
            "accepted": accepted,
            "loss": last_loss,
        }

    # ---- helpers ----

    def _eval_avg_reward(self, n_episodes: int = 5) -> float:
        """Average reward of N self-play episodes under current policy."""
        if not self.env_model.fitted:
            return 0.0
        rewards = []
        for _ in range(n_episodes):
            ep = play_episode(self.policy_net, self.env_model, max_steps=20, epsilon=0.0)
            if ep:
                rewards.append(sum(t.r for t in ep))
        if not rewards:
            return 0.0
        return float(np.mean(rewards))

    def _should_accept(self, before: float, after: float) -> bool:
        """Accept new weights if after improves on before by >= improve_factor.
        Uses additive logic when before is small/non-positive.
        """
        # absolute improvement margin = max(|before|, 0.05) * (improve_factor - 1.0)
        margin = max(abs(before), 0.05) * (self.improve_factor - 1.0)
        return after >= before + margin

    def stats(self) -> dict:
        return {
            "cycles": self._cycle_count,
            "swaps": self._swap_count,
            "rejects": self._reject_count,
            "last_loss": self._last_loss,
        }


__all__ = ["Coach"]
