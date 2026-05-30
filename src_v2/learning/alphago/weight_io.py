"""Atomic weight save/load + hot-swap.

Format: numpy .npz containing flat arrays (l1.W, l1.b, ..., v.l1.W, ...)
Atomicity:
  save_weights writes to {path}.tmp then os.replace -> atomic on the same volume.
  hot_swap accepts a dict of arrays and assigns to nets in-place — pre-validated shapes.
"""
from __future__ import annotations
import logging
import os
import threading
from typing import Dict

import numpy as np

from typing import Optional

from .move_policy_net import MovePolicyNet
from .policy_net import PolicyNet
from .value_net import ValueNet

log = logging.getLogger("src_v2.learning.alphago.weight_io")

_swap_lock = threading.Lock()


def save_weights(policy: PolicyNet, value: ValueNet, path: str,
                 move_policy: Optional[MovePolicyNet] = None) -> None:
    weights: Dict[str, np.ndarray] = {}
    weights.update(policy.get_weights())
    weights.update(value.get_weights())
    if move_policy is not None:
        weights.update(move_policy.get_weights())
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    np.savez(tmp, **weights)
    # numpy savez may add .npz suffix for some versions — handle both
    if not os.path.exists(tmp) and os.path.exists(tmp + ".npz"):
        tmp = tmp + ".npz"
    os.replace(tmp, path)


def load_weights(path: str) -> Dict[str, np.ndarray]:
    if not os.path.exists(path):
        # numpy savez may have appended .npz
        if os.path.exists(path + ".npz"):
            path = path + ".npz"
        else:
            raise FileNotFoundError(path)
    z = np.load(path)
    return {k: z[k] for k in z.files}


def hot_swap(policy: PolicyNet, value: ValueNet, weights: Dict[str, np.ndarray],
             move_policy: Optional[MovePolicyNet] = None) -> None:
    """Replace all weights atomically. Validates shapes BEFORE assignment so a failure
    mid-swap leaves nets unchanged.
    """
    p_keys = ("l1.W", "l1.b", "l2.W", "l2.b", "l3.W", "l3.b")
    v_keys = ("v.l1.W", "v.l1.b", "v.l2.W", "v.l2.b", "v.l3.W", "v.l3.b")
    m_keys = ("m.l1.W", "m.l1.b", "m.l2.W", "m.l2.b", "m.l3.W", "m.l3.b")
    # validate
    cur_p = policy.get_weights()
    cur_v = value.get_weights()
    for k in p_keys:
        if k not in weights:
            raise KeyError(f"policy weight missing: {k}")
        if weights[k].shape != cur_p[k].shape:
            raise ValueError(f"shape mismatch on {k}: {weights[k].shape} vs {cur_p[k].shape}")
    for k in v_keys:
        if k not in weights:
            raise KeyError(f"value weight missing: {k}")
        if weights[k].shape != cur_v[k].shape:
            raise ValueError(f"shape mismatch on {k}: {weights[k].shape} vs {cur_v[k].shape}")
    if move_policy is not None:
        cur_m = move_policy.get_weights()
        for k in m_keys:
            if k not in weights:
                raise KeyError(f"move policy weight missing: {k}")
            if weights[k].shape != cur_m[k].shape:
                raise ValueError(f"shape mismatch on {k}: {weights[k].shape} vs {cur_m[k].shape}")
    # apply under lock to ensure no torn read during forward by other thread
    with _swap_lock:
        policy.set_weights({k: weights[k] for k in p_keys})
        value.set_weights({k: weights[k] for k in v_keys})
        if move_policy is not None:
            move_policy.set_weights({k: weights[k] for k in m_keys})


__all__ = ["save_weights", "load_weights", "hot_swap"]
