"""MovePolicyNet — 32 -> 64 -> 64 -> NUM_MOVE_ACTIONS softmax.

Movement action space (5):
    0 move_L
    1 move_R
    2 move_U
    3 move_D
    4 move_stay

Decoupled from skill policy: skill = "WHAT to cast", movement = "HOW to chase".
Reward shaping (replay_buffer):
    distance to attacker decreased -> positive
    STUCK (post_coord == pre_coord for >= stuck_window_s) -> negative
"""
from __future__ import annotations
from typing import Dict, List

import numpy as np

from .nn_core import Linear, ReLU, Softmax

MOVE_INDEX_TO_DIR: List[str] = ["L", "R", "U", "D", "stay"]
NUM_MOVE_ACTIONS = len(MOVE_INDEX_TO_DIR)
MOVE_DIR_TO_INDEX: Dict[str, int] = {n: i for i, n in enumerate(MOVE_INDEX_TO_DIR)}


class MovePolicyNet:
    """Same architecture as PolicyNet but 5-class output."""

    def __init__(self, in_dim: int = 32, hidden: int = 64,
                 out_dim: int = NUM_MOVE_ACTIONS, seed: int | None = None):
        self.in_dim = in_dim
        self.hidden = hidden
        self.out_dim = out_dim
        self.l1 = Linear(in_dim, hidden, seed=seed)
        self.r1 = ReLU()
        self.l2 = Linear(hidden, hidden, seed=None if seed is None else seed + 1)
        self.r2 = ReLU()
        self.l3 = Linear(hidden, out_dim, seed=None if seed is None else seed + 2)
        self.sm = Softmax()

    def forward(self, x: np.ndarray) -> np.ndarray:
        if x.ndim == 1:
            x = x[None, :]
        a = self.l1.forward(x)
        a = self.r1.forward(a)
        a = self.l2.forward(a)
        a = self.r2.forward(a)
        a = self.l3.forward(a)
        p = self.sm.forward(a)
        return p

    def backward(self, p_minus_target: np.ndarray) -> None:
        g = self.l3.backward(p_minus_target)
        g = self.r2.backward(g)
        g = self.l2.backward(g)
        g = self.r1.backward(g)
        self.l1.backward(g)

    def layers(self):
        return (self.l1, self.l2, self.l3)

    def get_weights(self) -> Dict[str, np.ndarray]:
        return {
            "m.l1.W": self.l1.W.copy(), "m.l1.b": self.l1.b.copy(),
            "m.l2.W": self.l2.W.copy(), "m.l2.b": self.l2.b.copy(),
            "m.l3.W": self.l3.W.copy(), "m.l3.b": self.l3.b.copy(),
        }

    def set_weights(self, w: Dict[str, np.ndarray]) -> None:
        self.l1.W = w["m.l1.W"].astype(np.float32, copy=True)
        self.l1.b = w["m.l1.b"].astype(np.float32, copy=True)
        self.l2.W = w["m.l2.W"].astype(np.float32, copy=True)
        self.l2.b = w["m.l2.b"].astype(np.float32, copy=True)
        self.l3.W = w["m.l3.W"].astype(np.float32, copy=True)
        self.l3.b = w["m.l3.b"].astype(np.float32, copy=True)


__all__ = ["MovePolicyNet", "MOVE_INDEX_TO_DIR", "MOVE_DIR_TO_INDEX", "NUM_MOVE_ACTIONS"]
