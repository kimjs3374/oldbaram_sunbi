"""Trainer — batch SGD with Adam.

Policy loss:    cross-entropy(p, target_dist)
Value loss:     MSE(v, target_value) — target_value = clipped reward (proxy for return)

Total loss = policy_loss + value_coef * value_loss
"""
from __future__ import annotations
from typing import Dict, Optional, Tuple

import numpy as np

from .move_policy_net import MovePolicyNet, NUM_MOVE_ACTIONS
from .nn_core import Adam
from .policy_net import PolicyNet, NUM_ACTIONS
from .value_net import ValueNet


def _one_hot(actions: np.ndarray, K: int) -> np.ndarray:
    n = actions.shape[0]
    out = np.zeros((n, K), dtype=np.float32)
    out[np.arange(n), actions.astype(np.int64)] = 1.0
    return out


class Trainer:
    """Holds optimizers + does train_batch.

    Optionally trains MovePolicyNet alongside skill PolicyNet when `move_policy`
    is provided. Single ValueNet is shared (state value is policy-agnostic).
    """

    def __init__(self, policy: PolicyNet, value: ValueNet,
                 lr: float = 1e-3, value_coef: float = 0.5,
                 move_policy: Optional[MovePolicyNet] = None):
        self.policy = policy
        self.value = value
        self.move_policy = move_policy
        self.opt_p = Adam(lr=lr)
        self.opt_v = Adam(lr=lr)
        self.opt_m = Adam(lr=lr) if move_policy is not None else None
        self.value_coef = float(value_coef)
        self.last_loss: Dict[str, float] = {"policy": 0.0, "value": 0.0, "total": 0.0,
                                            "move": 0.0}

    def train_batch(self,
                    S: np.ndarray,
                    A: np.ndarray,
                    R: np.ndarray,
                    Sn: np.ndarray) -> Dict[str, float]:
        """One SGD step.

        S: (B, 32) state
        A: (B,)    action index
        R: (B,)    immediate reward
        Sn: (B, 32) next state (unused in current loss; kept for future bootstrapping)

        Returns loss dict.
        """
        B = S.shape[0]
        # ---------- policy ----------
        p = self.policy.forward(S)  # (B, K)
        target = _one_hot(A, NUM_ACTIONS)  # (B, K)
        # cross entropy = -sum target * log(p)
        eps = 1e-8
        ce = -np.sum(target * np.log(p + eps), axis=1).mean()
        # gradient: dL/dlogits = (p - target) / B (with mean reduction)
        dlogits = (p - target) / float(B)
        self.policy.backward(dlogits)
        self.opt_p.step(*self.policy.layers())

        # ---------- value ----------
        # target value: clipped reward to [-1, 1] (proxy)
        target_v = np.clip(R, -1.0, 1.0).astype(np.float32)[:, None]  # (B, 1)
        v = self.value.forward(S)  # (B, 1)
        diff = (v - target_v)
        mse = float((diff * diff).mean())
        # dL/dv = 2 * diff / B
        dv = 2.0 * diff / float(B)
        self.value.backward(dv)
        self.opt_v.step(*self.value.layers())

        total = float(ce) + self.value_coef * mse
        self.last_loss = {"policy": float(ce), "value": float(mse), "total": float(total),
                          "move": 0.0}
        return self.last_loss

    def train_batch_with_move(self,
                              S: np.ndarray, A: np.ndarray, Am: np.ndarray,
                              R: np.ndarray, Sn: np.ndarray) -> Dict[str, float]:
        """Like train_batch but also trains MovePolicyNet on Am targets.

        Requires self.move_policy to have been provided. Otherwise falls back
        to skill-only train_batch.
        """
        base = self.train_batch(S, A, R, Sn)
        if self.move_policy is None or self.opt_m is None:
            return base
        B = S.shape[0]
        pm = self.move_policy.forward(S)
        target_m = _one_hot(Am, NUM_MOVE_ACTIONS)
        eps = 1e-8
        ce_m = -np.sum(target_m * np.log(pm + eps), axis=1).mean()
        dlogits_m = (pm - target_m) / float(B)
        self.move_policy.backward(dlogits_m)
        self.opt_m.step(*self.move_policy.layers())
        self.last_loss["move"] = float(ce_m)
        self.last_loss["total"] = float(self.last_loss["total"] + ce_m)
        return self.last_loss


__all__ = ["Trainer"]
