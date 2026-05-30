"""Lightweight UCT MCTS over EnvModel + PolicyNet (prior) + ValueNet (rollout).

Returns visit-count-based action distribution at the root.
"""
from __future__ import annotations
import math
from typing import Dict, List, Optional, Tuple

import numpy as np

from .env_model import EnvModel
from .move_policy_net import MovePolicyNet, NUM_MOVE_ACTIONS
from .policy_net import PolicyNet, NUM_ACTIONS
from .value_net import ValueNet


class MCTSNode:
    __slots__ = ("s", "P", "N", "W", "children")

    def __init__(self, s: np.ndarray, P: np.ndarray):
        self.s = s
        self.P = P             # prior (NUM_ACTIONS,)
        self.N = np.zeros(NUM_ACTIONS, dtype=np.float32)
        self.W = np.zeros(NUM_ACTIONS, dtype=np.float32)
        self.children: Dict[int, "MCTSNode"] = {}

    def Q(self) -> np.ndarray:
        N = np.where(self.N > 0, self.N, 1.0)
        return self.W / N

    def total_N(self) -> float:
        return float(self.N.sum())


def _ucb(node: MCTSNode, c_puct: float = 1.4) -> int:
    Ntot = max(1.0, node.total_N())
    Q = node.Q()
    U = c_puct * node.P * math.sqrt(Ntot) / (1.0 + node.N)
    return int((Q + U).argmax())


def mcts_search(root_s: np.ndarray,
                policy: PolicyNet,
                value: ValueNet,
                env: EnvModel,
                sims: int = 32,
                depth: int = 5,
                c_puct: float = 1.4,
                temperature: float = 1.0) -> np.ndarray:
    """Returns action visit distribution (NUM_ACTIONS,) summing to 1.

    Falls back to policy prior if env_model not fitted.
    """
    prior = policy.forward(root_s)[0]
    prior = np.clip(prior, 1e-8, 1.0)
    prior = prior / prior.sum()
    if not env.fitted:
        return prior
    root = MCTSNode(root_s.copy(), prior.astype(np.float32))
    for _ in range(int(sims)):
        node = root
        path: List[Tuple[MCTSNode, int]] = []
        cur_s = root_s
        for _step in range(int(depth)):
            a = _ucb(node, c_puct=c_puct)
            path.append((node, a))
            child = node.children.get(a)
            if child is None:
                # expand: roll one step in env
                s_next, _r = env.step(cur_s, a)
                # leaf — evaluate with value net, attach prior from policy net
                child_prior = policy.forward(s_next)[0]
                child_prior = np.clip(child_prior, 1e-8, 1.0)
                child_prior = child_prior / child_prior.sum()
                child = MCTSNode(s_next.copy(), child_prior.astype(np.float32))
                node.children[a] = child
                cur_s = s_next
                node = child
                break
            else:
                cur_s, _r = env.step(cur_s, a)
                node = child
        # Backup using value net at leaf
        v = float(value.forward(cur_s)[0, 0])
        for nd, ac in path:
            nd.N[ac] += 1.0
            nd.W[ac] += v
    # action distribution from visit counts
    counts = root.N.astype(np.float64)
    if counts.sum() == 0:
        return prior
    if temperature <= 1e-6:
        out = np.zeros_like(counts)
        out[counts.argmax()] = 1.0
        return out.astype(np.float32)
    counts = counts ** (1.0 / float(temperature))
    return (counts / counts.sum()).astype(np.float32)


def mcts_search_move(root_s: np.ndarray,
                     move_policy: MovePolicyNet,
                     value: ValueNet,
                     env: EnvModel,
                     sims: int = 16,
                     depth: int = 3,
                     c_puct: float = 1.4,
                     temperature: float = 1.0) -> np.ndarray:
    """MCTS over move action space (5). Same UCT logic as mcts_search but
    uses env.step_move() and the 5-class move policy as prior. Returns visit
    distribution (NUM_MOVE_ACTIONS,) summing to 1.
    """
    prior = move_policy.forward(root_s)[0]
    prior = np.clip(prior, 1e-8, 1.0)
    prior = prior / prior.sum()
    if not env.fitted:
        return prior.astype(np.float32)

    class _MoveNode:
        __slots__ = ("s", "P", "N", "W", "children")

        def __init__(self, s, P):
            self.s = s
            self.P = P
            self.N = np.zeros(NUM_MOVE_ACTIONS, dtype=np.float32)
            self.W = np.zeros(NUM_MOVE_ACTIONS, dtype=np.float32)
            self.children: Dict[int, "_MoveNode"] = {}

        def Q(self):
            N = np.where(self.N > 0, self.N, 1.0)
            return self.W / N

        def total_N(self):
            return float(self.N.sum())

    def _ucb_move(node, c=1.4):
        Ntot = max(1.0, node.total_N())
        Q = node.Q()
        U = c * node.P * math.sqrt(Ntot) / (1.0 + node.N)
        return int((Q + U).argmax())

    root = _MoveNode(root_s.copy(), prior.astype(np.float32))
    for _ in range(int(sims)):
        node = root
        path: List[Tuple[_MoveNode, int]] = []
        cur_s = root_s
        for _step in range(int(depth)):
            a = _ucb_move(node, c=c_puct)
            path.append((node, a))
            child = node.children.get(a)
            if child is None:
                s_next, _r = env.step_move(cur_s, a)
                child_prior = move_policy.forward(s_next)[0]
                child_prior = np.clip(child_prior, 1e-8, 1.0)
                child_prior = child_prior / child_prior.sum()
                child = _MoveNode(s_next.copy(), child_prior.astype(np.float32))
                node.children[a] = child
                cur_s = s_next
                node = child
                break
            else:
                cur_s, _r = env.step_move(cur_s, a)
                node = child
        v = float(value.forward(cur_s)[0, 0])
        for nd, ac in path:
            nd.N[ac] += 1.0
            nd.W[ac] += v
    counts = root.N.astype(np.float64)
    if counts.sum() == 0:
        return prior.astype(np.float32)
    if temperature <= 1e-6:
        out = np.zeros_like(counts)
        out[counts.argmax()] = 1.0
        return out.astype(np.float32)
    counts = counts ** (1.0 / float(temperature))
    return (counts / counts.sum()).astype(np.float32)


__all__ = ["MCTSNode", "mcts_search", "mcts_search_move"]
