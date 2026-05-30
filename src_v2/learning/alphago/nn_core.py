"""Numpy-only NN primitives — Linear / ReLU / Softmax / Tanh + Adam.

All forward()/backward() ops accept batched input shape (B, in_dim).
Single-sample callers pass (1, in_dim).
"""
from __future__ import annotations

import numpy as np


class Linear:
    """y = x @ W + b. Adam stats embedded.

    He init for ReLU. Buffers self.x cached for backward.
    """

    __slots__ = ("W", "b", "x", "dW", "db",
                 "mW", "vW", "mb", "vb", "_t")

    def __init__(self, in_dim: int, out_dim: int, seed: int | None = None):
        rng = np.random.default_rng(seed)
        self.W = rng.standard_normal((in_dim, out_dim)).astype(np.float32) * np.float32(np.sqrt(2.0 / in_dim))
        self.b = np.zeros(out_dim, dtype=np.float32)
        self.x = None
        self.dW = np.zeros_like(self.W)
        self.db = np.zeros_like(self.b)
        self.mW = np.zeros_like(self.W)
        self.vW = np.zeros_like(self.W)
        self.mb = np.zeros_like(self.b)
        self.vb = np.zeros_like(self.b)
        self._t = 0

    def forward(self, x: np.ndarray) -> np.ndarray:
        self.x = x
        return x @ self.W + self.b

    def backward(self, grad_out: np.ndarray) -> np.ndarray:
        # grad_out: (B, out)
        # dW: (in, out) = x.T @ grad_out
        self.dW[:] = self.x.T @ grad_out
        self.db[:] = grad_out.sum(axis=0)
        # grad_in: (B, in) = grad_out @ W.T
        return grad_out @ self.W.T

    def adam_step(self, lr: float = 1e-3, beta1: float = 0.9,
                  beta2: float = 0.999, eps: float = 1e-8) -> None:
        self._t += 1
        t = self._t
        # weights
        self.mW = beta1 * self.mW + (1 - beta1) * self.dW
        self.vW = beta2 * self.vW + (1 - beta2) * (self.dW * self.dW)
        m_hat = self.mW / (1 - beta1 ** t)
        v_hat = self.vW / (1 - beta2 ** t)
        self.W -= (lr * m_hat / (np.sqrt(v_hat) + eps)).astype(self.W.dtype)
        # bias
        self.mb = beta1 * self.mb + (1 - beta1) * self.db
        self.vb = beta2 * self.vb + (1 - beta2) * (self.db * self.db)
        m_hat_b = self.mb / (1 - beta1 ** t)
        v_hat_b = self.vb / (1 - beta2 ** t)
        self.b -= (lr * m_hat_b / (np.sqrt(v_hat_b) + eps)).astype(self.b.dtype)


class ReLU:
    __slots__ = ("mask",)

    def __init__(self):
        self.mask = None

    def forward(self, x: np.ndarray) -> np.ndarray:
        self.mask = (x > 0).astype(x.dtype)
        return x * self.mask

    def backward(self, grad_out: np.ndarray) -> np.ndarray:
        return grad_out * self.mask


class Tanh:
    __slots__ = ("y",)

    def __init__(self):
        self.y = None

    def forward(self, x: np.ndarray) -> np.ndarray:
        self.y = np.tanh(x)
        return self.y

    def backward(self, grad_out: np.ndarray) -> np.ndarray:
        return grad_out * (1.0 - self.y * self.y)


class Softmax:
    """Softmax along last axis. Cached for cross-entropy backward (which expects p - y)."""

    __slots__ = ("p",)

    def __init__(self):
        self.p = None

    def forward(self, x: np.ndarray) -> np.ndarray:
        # Numerically stable
        z = x - x.max(axis=-1, keepdims=True)
        e = np.exp(z)
        s = e.sum(axis=-1, keepdims=True)
        self.p = (e / s).astype(x.dtype)
        return self.p

    def backward(self, grad_out: np.ndarray) -> np.ndarray:
        # NOTE: in our trainer we feed (p - target) directly into the LAST Linear's backward,
        # bypassing this. This implementation handles the rare case where it's used standalone.
        # Generic Jacobian-vector product:
        # dy_i / dx_j = p_i * (delta_ij - p_j)
        p = self.p
        # (B, K)
        dot = (grad_out * p).sum(axis=-1, keepdims=True)
        return p * (grad_out - dot)


class Adam:
    """Lightweight wrapper to step multiple Linears with shared lr."""

    __slots__ = ("lr", "beta1", "beta2", "eps")

    def __init__(self, lr: float = 1e-3, beta1: float = 0.9,
                 beta2: float = 0.999, eps: float = 1e-8) -> None:
        self.lr = lr
        self.beta1 = beta1
        self.beta2 = beta2
        self.eps = eps

    def step(self, *layers: Linear) -> None:
        for l in layers:
            if isinstance(l, Linear):
                l.adam_step(self.lr, self.beta1, self.beta2, self.eps)


__all__ = ["Linear", "ReLU", "Softmax", "Tanh", "Adam"]
