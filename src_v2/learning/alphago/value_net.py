"""ValueNet — 32 -> 64 -> 64 -> 1, tanh output bounded in [-1, 1]."""
from __future__ import annotations
from typing import Dict

import numpy as np

from .nn_core import Linear, ReLU, Tanh


class ValueNet:
    def __init__(self, in_dim: int = 32, hidden: int = 64, seed: int | None = None):
        self.in_dim = in_dim
        self.hidden = hidden
        self.l1 = Linear(in_dim, hidden, seed=seed)
        self.r1 = ReLU()
        self.l2 = Linear(hidden, hidden, seed=None if seed is None else seed + 1)
        self.r2 = ReLU()
        self.l3 = Linear(hidden, 1, seed=None if seed is None else seed + 2)
        self.th = Tanh()

    def forward(self, x: np.ndarray) -> np.ndarray:
        if x.ndim == 1:
            x = x[None, :]
        a = self.l1.forward(x)
        a = self.r1.forward(a)
        a = self.l2.forward(a)
        a = self.r2.forward(a)
        a = self.l3.forward(a)
        y = self.th.forward(a)
        return y  # shape (B, 1) in [-1, 1]

    def backward(self, dL_dy: np.ndarray) -> None:
        # dL_dy: (B, 1). Apply tanh backward then linears.
        g = self.th.backward(dL_dy)
        g = self.l3.backward(g)
        g = self.r2.backward(g)
        g = self.l2.backward(g)
        g = self.r1.backward(g)
        self.l1.backward(g)

    def layers(self):
        return (self.l1, self.l2, self.l3)

    def get_weights(self) -> Dict[str, np.ndarray]:
        return {
            "v.l1.W": self.l1.W.copy(), "v.l1.b": self.l1.b.copy(),
            "v.l2.W": self.l2.W.copy(), "v.l2.b": self.l2.b.copy(),
            "v.l3.W": self.l3.W.copy(), "v.l3.b": self.l3.b.copy(),
        }

    def set_weights(self, w: Dict[str, np.ndarray]) -> None:
        self.l1.W = w["v.l1.W"].astype(np.float32, copy=True)
        self.l1.b = w["v.l1.b"].astype(np.float32, copy=True)
        self.l2.W = w["v.l2.W"].astype(np.float32, copy=True)
        self.l2.b = w["v.l2.b"].astype(np.float32, copy=True)
        self.l3.W = w["v.l3.W"].astype(np.float32, copy=True)
        self.l3.b = w["v.l3.b"].astype(np.float32, copy=True)


__all__ = ["ValueNet"]
