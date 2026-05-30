"""PolicyNet — 32 -> 64 -> 64 -> NUM_ACTIONS, softmax output.

Action indices (10 total):
    0 self_heal
    1 self_revive
    2 attacker_revive
    3 parhon
    4 baekho
    5 parlyuk
    6 gyoungryeok
    7 seq_rclick
    8 tab_lock
    9 wait
"""
from __future__ import annotations
from typing import Dict, List

import numpy as np

from .nn_core import Linear, ReLU, Softmax

ACTION_INDEX_TO_RULE: List[str] = [
    "self_heal",
    "self_revive",
    "attacker_revive",
    "parhon",
    "baekho",
    "parlyuk",
    "gyoungryeok",
    "seq_rclick",
    "tab_lock",
    "wait",
]
NUM_ACTIONS = len(ACTION_INDEX_TO_RULE)
ACTION_RULE_TO_INDEX: Dict[str, int] = {n: i for i, n in enumerate(ACTION_INDEX_TO_RULE)}


class PolicyNet:
    """Forward gives probability distribution. Backward expects (p - y_target) gradient
    delivered to the LAST linear (cross-entropy + softmax fused gradient).
    """

    def __init__(self, in_dim: int = 32, hidden: int = 64,
                 out_dim: int = NUM_ACTIONS, seed: int | None = None):
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
        """Use fused softmax + cross-entropy gradient (= p - one_hot)."""
        g = self.l3.backward(p_minus_target)
        g = self.r2.backward(g)
        g = self.l2.backward(g)
        g = self.r1.backward(g)
        self.l1.backward(g)

    def layers(self):
        return (self.l1, self.l2, self.l3)

    def get_weights(self) -> Dict[str, np.ndarray]:
        return {
            "l1.W": self.l1.W.copy(), "l1.b": self.l1.b.copy(),
            "l2.W": self.l2.W.copy(), "l2.b": self.l2.b.copy(),
            "l3.W": self.l3.W.copy(), "l3.b": self.l3.b.copy(),
        }

    def set_weights(self, w: Dict[str, np.ndarray]) -> None:
        self.l1.W = w["l1.W"].astype(np.float32, copy=True)
        self.l1.b = w["l1.b"].astype(np.float32, copy=True)
        self.l2.W = w["l2.W"].astype(np.float32, copy=True)
        self.l2.b = w["l2.b"].astype(np.float32, copy=True)
        self.l3.W = w["l3.W"].astype(np.float32, copy=True)
        self.l3.b = w["l3.b"].astype(np.float32, copy=True)


__all__ = ["PolicyNet", "ACTION_INDEX_TO_RULE", "ACTION_RULE_TO_INDEX", "NUM_ACTIONS"]
