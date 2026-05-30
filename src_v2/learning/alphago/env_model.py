"""EnvModel — Markov transition table over clustered states.

Lightweight numpy KMeans (Lloyd's algorithm, fixed iters) clusters the state space
into K (default 100) clusters. Then we build:

    transition_count[c_state, action, c_next] -> int
    visit_count[c_state]                       -> int

For sampling we normalize:
    P(c_next | c, a) = transition_count[c, a, :] / sum
    representative_state[c] = cluster centroid (back to feature_dim)

This is intentionally crude — it just lets self-play augment the replay buffer with
plausible state transitions. Not load-bearing for production behavior.
"""
from __future__ import annotations
from typing import List, Optional, Tuple

import numpy as np

from .feature_extractor import FEATURE_DIM
from .policy_net import NUM_ACTIONS
from .move_policy_net import NUM_MOVE_ACTIONS
from .replay_buffer import Transition


def _kmeans(X: np.ndarray, K: int, iters: int = 25, seed: int = 0) -> Tuple[np.ndarray, np.ndarray]:
    """Returns (centroids[K, D], labels[N]) using Lloyd's algorithm."""
    rng = np.random.default_rng(seed)
    N, D = X.shape
    if N == 0:
        return np.zeros((0, D), dtype=np.float32), np.zeros(0, dtype=np.int64)
    K = min(K, N)
    # init: pick K rows
    init_idx = rng.choice(N, size=K, replace=False)
    centroids = X[init_idx].astype(np.float32, copy=True)
    labels = np.zeros(N, dtype=np.int64)
    for _ in range(iters):
        # Compute distances (N, K)
        # ||x - c||^2 = |x|^2 - 2 x.c + |c|^2 — but for small K just use loop or broadcast
        diff = X[:, None, :] - centroids[None, :, :]
        d2 = (diff * diff).sum(axis=-1)
        new_labels = d2.argmin(axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        # update centroids
        for k in range(K):
            mask = labels == k
            if mask.any():
                centroids[k] = X[mask].mean(axis=0)
    return centroids.astype(np.float32), labels


class EnvModel:
    """Cluster + transition table. fit() with transitions; step()/sample_initial_state() to roll out."""

    def __init__(self, n_clusters: int = 16, seed: int = 0):
        self.K = int(n_clusters)
        self.seed = int(seed)
        self.centroids: Optional[np.ndarray] = None
        # skill transitions[K, A, K]
        self.T: Optional[np.ndarray] = None
        self.R: Optional[np.ndarray] = None  # reward sum [K, A, K]
        self.Tn: Optional[np.ndarray] = None  # count [K, A]
        # movement transitions[K, M, K]
        self.Tm: Optional[np.ndarray] = None
        self.Rm: Optional[np.ndarray] = None
        self.Tmn: Optional[np.ndarray] = None
        self.fitted = False

    def fit(self, transitions: List[Transition]) -> None:
        if not transitions:
            return
        S = np.stack([t.s for t in transitions], axis=0).astype(np.float32)
        Sn = np.stack([t.s_next for t in transitions], axis=0).astype(np.float32)
        all_states = np.concatenate([S, Sn], axis=0)
        K = min(self.K, max(2, len(all_states)))
        centroids, _ = _kmeans(all_states, K=K, iters=20, seed=self.seed)
        self.centroids = centroids
        self.K = K
        # assign labels
        c_s = self._assign(S)
        c_sn = self._assign(Sn)
        self.T = np.zeros((K, NUM_ACTIONS, K), dtype=np.float32)
        self.R = np.zeros((K, NUM_ACTIONS, K), dtype=np.float32)
        self.Tn = np.zeros((K, NUM_ACTIONS), dtype=np.float32)
        self.Tm = np.zeros((K, NUM_MOVE_ACTIONS, K), dtype=np.float32)
        self.Rm = np.zeros((K, NUM_MOVE_ACTIONS, K), dtype=np.float32)
        self.Tmn = np.zeros((K, NUM_MOVE_ACTIONS), dtype=np.float32)
        for i, t in enumerate(transitions):
            a = int(t.a)
            ci = int(c_s[i])
            cj = int(c_sn[i])
            self.T[ci, a, cj] += 1.0
            self.R[ci, a, cj] += float(t.r)
            self.Tn[ci, a] += 1.0
            am = int(getattr(t, "a_move", NUM_MOVE_ACTIONS - 1))
            am = am % NUM_MOVE_ACTIONS
            self.Tm[ci, am, cj] += 1.0
            self.Rm[ci, am, cj] += float(t.r)
            self.Tmn[ci, am] += 1.0
        self.fitted = True

    def _assign(self, X: np.ndarray) -> np.ndarray:
        diff = X[:, None, :] - self.centroids[None, :, :]
        d2 = (diff * diff).sum(axis=-1)
        return d2.argmin(axis=1)

    def transition_count(self) -> int:
        if self.T is None:
            return 0
        return int(self.T.sum())

    def sample_initial_state(self) -> np.ndarray:
        """Pick a random cluster centroid as the starting state vector."""
        if not self.fitted or self.centroids is None or self.K == 0:
            return np.zeros(FEATURE_DIM, dtype=np.float32)
        idx = np.random.randint(0, self.K)
        return self.centroids[idx].copy()

    def step(self, s: np.ndarray, a: int) -> Tuple[np.ndarray, float]:
        """Sample next state + reward given current state vec and SKILL action.

        Falls back to "self-loop with 0 reward" if action never seen from that cluster.
        """
        if not self.fitted:
            return s.copy(), 0.0
        ci = int(self._assign(s[None, :])[0])
        a = int(a) % NUM_ACTIONS
        total = float(self.T[ci, a].sum())
        if total <= 0.0:
            return s.copy(), 0.0
        probs = self.T[ci, a] / total
        cj = int(np.random.choice(self.K, p=probs))
        # avg reward observed for this transition
        cnt = self.T[ci, a, cj]
        rew = float(self.R[ci, a, cj] / cnt) if cnt > 0 else 0.0
        return self.centroids[cj].copy(), rew

    def step_move(self, s: np.ndarray, am: int) -> Tuple[np.ndarray, float]:
        """Sample next state + reward given current state and MOVE action."""
        if not self.fitted or self.Tm is None:
            return s.copy(), 0.0
        ci = int(self._assign(s[None, :])[0])
        am = int(am) % NUM_MOVE_ACTIONS
        total = float(self.Tm[ci, am].sum())
        if total <= 0.0:
            return s.copy(), 0.0
        probs = self.Tm[ci, am] / total
        cj = int(np.random.choice(self.K, p=probs))
        cnt = self.Tm[ci, am, cj]
        rew = float(self.Rm[ci, am, cj] / cnt) if cnt > 0 else 0.0
        return self.centroids[cj].copy(), rew

    def step_combined(self, s: np.ndarray, a_skill: int, a_move: int) -> Tuple[np.ndarray, float]:
        """Combined step: take skill transition (priority) or fallback to move.

        Rule from spec: when skill is non-wait, ignore movement that step (the
        attacker is being supported). When skill == wait, use movement transition.
        """
        from .policy_net import NUM_ACTIONS as _NA  # local to avoid cycle
        if int(a_skill) != _NA - 1:  # not "wait"
            return self.step(s, a_skill)
        return self.step_move(s, a_move)


__all__ = ["EnvModel"]
