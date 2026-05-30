"""Snapshot -> 32-dim float32 feature vector.

Design ref: alphago module §1.

Conventions:
    HP/MP/HP_attacker normalized to [-1, 1] (negative -> -1.0 marker)
    Cooldowns normalized by typical max (60 sec)
    Honma sec normalized by 30 sec, Mujang/Boho by 60 sec
    Coordinates normalized by 1000
    Booleans -> {0.0, 1.0}
"""
from __future__ import annotations
from typing import Any, Optional

import numpy as np

from ...core.snapshot import Snapshot

FEATURE_DIM = 32


def _norm_pct(x: int) -> float:
    if x is None or x < 0:
        return -1.0
    return float(x) / 100.0


def _norm_sec(x: int, denom: float) -> float:
    if x is None or x < 0:
        return 0.0
    return float(x) / denom


def _norm_coord(c: Optional[Any], idx: int) -> float:
    if not c:
        return 0.0
    try:
        return float(c[idx]) / 1000.0
    except Exception:
        return 0.0


def _coord_dist(a: Optional[Any], b: Optional[Any]) -> float:
    if not a or not b:
        return -1.0
    try:
        dx = a[0] - b[0]
        dy = a[1] - b[1]
        return float((dx * dx + dy * dy) ** 0.5) / 1000.0
    except Exception:
        return -1.0


class FeatureExtractor:
    """Snapshot -> np.ndarray(32, float32). Pure function (no state).

    Index assignment fixed — CHANGING ORDER BREAKS TRAINED WEIGHTS.
    """

    DIM = FEATURE_DIM

    def extract(self, snap: Snapshot) -> np.ndarray:
        v = np.zeros(FEATURE_DIM, dtype=np.float32)
        # 0..3 HP/MP
        v[0] = _norm_pct(snap.hp)
        v[1] = _norm_pct(snap.mp)
        v[2] = _norm_pct(snap.hp_cur if snap.hp_cur >= 0 else snap.hp)
        v[3] = _norm_pct(snap.mp_cur if snap.mp_cur >= 0 else snap.mp)
        # 4..7 cooldowns
        v[4] = _norm_sec(snap.cd_parlyuk, 60.0)
        v[5] = _norm_sec(snap.cd_baekho, 60.0)
        v[6] = _norm_sec(snap.cd_parhon, 60.0)
        v[7] = _norm_sec(snap.cd_revive, 60.0)
        # 8..10 buffs
        v[8] = 1.0 if snap.buff_parlyuk_active else 0.0
        v[9] = 1.0 if snap.buff_baekho_active else 0.0
        v[10] = 1.0 if snap.buff_gyoungryeok_active else 0.0
        # 11..14 attacker state
        v[11] = _norm_pct(snap.attacker_hp)
        v[12] = _norm_sec(snap.attacker_honma_sec, 30.0)
        v[13] = _norm_sec(snap.attacker_mujang_sec, 60.0)
        v[14] = _norm_sec(snap.attacker_boho_sec, 60.0)
        # 15..18 healer/attacker coords + map match + dist
        v[15] = _norm_coord(snap.healer_coord, 0)
        v[16] = _norm_coord(snap.healer_coord, 1)
        v[17] = _norm_coord(snap.attacker_coord, 0)
        v[18] = _norm_coord(snap.attacker_coord, 1)
        # 19 distance
        v[19] = _coord_dist(snap.healer_coord, snap.attacker_coord)
        # 20 map match
        try:
            same_map = bool(snap.healer_map) and snap.healer_map == snap.attacker_map
            v[20] = 1.0 if same_map else 0.0
        except Exception:
            v[20] = 0.0
        # 21..23 tab presence
        v[21] = 1.0 if snap.red_tab_present else 0.0
        v[22] = 1.0 if snap.white_tab_present else 0.0
        v[23] = 1.0 if snap.attacker_coord_valid else 0.0
        # 24..26 flags
        v[24] = 1.0 if snap.numlock_cycle_due else 0.0
        v[25] = 1.0 if snap.seq_in_progress else 0.0
        v[26] = 1.0 if snap.tab_lock_pending else 0.0
        # 27..29 attacker last_dir one-hot-ish
        d = snap.attacker_last_dir or "-"
        v[27] = 1.0 if d == "U" else (-1.0 if d == "D" else 0.0)
        v[28] = 1.0 if d == "R" else (-1.0 if d == "L" else 0.0)
        v[29] = 1.0 if d != "-" else 0.0
        # 30 healer alive heuristic
        v[30] = 1.0 if (snap.hp is not None and snap.hp > 0) else 0.0
        # 31 attacker alive heuristic
        v[31] = 1.0 if (snap.attacker_hp is not None and snap.attacker_hp > 0) else 0.0
        # final NaN/Inf guard
        np.nan_to_num(v, copy=False, nan=0.0, posinf=1.0, neginf=-1.0)
        return v


__all__ = ["FeatureExtractor", "FEATURE_DIM"]
