"""Circular replay buffer.

Each Transition: (snap_vec[32], action_idx, reward, next_snap_vec[32], done).

Conversion from ActionRecord:
    Each consecutive pair (record_t, record_t+1) becomes a transition with:
        state    = features extracted from record_t.snapshot_at_decision (filled into Snapshot)
        action   = ACTION_RULE_TO_INDEX[record_t.action]  (default = wait if unknown)
        reward   = computed by reward_from_records(window)
        next_s   = features from record_t+1.snapshot
        done     = False (always — episodes are open-ended)
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

import numpy as np

from ...core.snapshot import Snapshot
from ...core.types import ActionRecord
from .feature_extractor import FeatureExtractor, FEATURE_DIM
from .policy_net import ACTION_RULE_TO_INDEX, NUM_ACTIONS
from .move_policy_net import MOVE_DIR_TO_INDEX, NUM_MOVE_ACTIONS


@dataclass
class Transition:
    """A replay transition.

    Skill action and move action are stored in parallel:
        a       — skill action index (0..NUM_ACTIONS-1, default = wait if no skill)
        a_move  — move action index  (0..NUM_MOVE_ACTIONS-1, default = stay)
    Backward compatible: legacy producers may set only `a`.
    """
    s: np.ndarray  # (32,)
    a: int
    r: float
    s_next: np.ndarray  # (32,)
    done: bool = False
    a_move: int = NUM_MOVE_ACTIONS - 1  # default = stay


def _snap_from_dict(d: dict) -> Snapshot:
    """ActionRecord.snapshot_at_decision is a small dict — re-hydrate to Snapshot."""
    snap = Snapshot()
    for k, v in (d or {}).items():
        if hasattr(snap, k):
            try:
                setattr(snap, k, v)
            except Exception:
                pass
    # action_log uses "red_tab" key for boolean
    if isinstance(d, dict) and "red_tab" in d:
        try:
            snap.red_tab_present = bool(d["red_tab"])
        except Exception:
            pass
    return snap


def _record_reward(rec: ActionRecord, next_rec: Optional[ActionRecord] = None) -> float:
    """Per-step reward = skill_term + movement_term + map_term.

    HIGHER is better. `next_rec` (when given) supplies the post-transition
    snapshot used to compute movement-related shaping (distance delta, STUCK,
    map transition success).

    Optional snapshot fields read (all default to absent):
        pre_coord, post_coord       — for STUCK detection (post == pre -> stuck)
        stuck_duration_s            — explicit accumulated STUCK time
        attacker_dist_delta         — int, signed change in distance to attacker
        map_changed                 — bool, set on successful map transition

    Legacy records without these fields receive only the skill_term (back-compat).
    """
    if rec is None:
        return 0.0
    a = rec.action
    res = rec.result
    # ---------- skill term (existing) ----------
    if a == "self_revive":
        skill_r = -1.0  # death = strong negative
    elif a == "attacker_revive":
        skill_r = -0.5
    elif res == "ok":
        if a == "seq_rclick":
            skill_r = 0.3
        elif a in ("parhon", "baekho", "parlyuk"):
            skill_r = 0.2
        elif a == "self_heal":
            skill_r = 0.05
        else:
            skill_r = 0.05
    elif res == "failed":
        skill_r = -0.05
    else:
        skill_r = 0.0
    # ---------- movement term (new fields, optional) ----------
    move_r = 0.0
    snap = rec.snapshot_at_decision or {}
    next_snap = (next_rec.snapshot_at_decision if next_rec else None) or {}
    # STUCK: explicit duration field takes priority
    stuck_dur = float(snap.get("stuck_duration_s", 0.0) or 0.0)
    if stuck_dur >= 1.0:
        # -0.2 per second STUCK, capped
        move_r += -0.2 * min(stuck_dur, 5.0)
        if stuck_dur >= 10.0:
            # death-level penalty for prolonged STUCK
            move_r += -2.0
    else:
        # fallback: pre/post coord equality with non-stay intent
        pre = snap.get("pre_coord") or snap.get("healer_coord")
        post = snap.get("post_coord") or next_snap.get("healer_coord")
        if pre and post and tuple(pre) == tuple(post):
            intent = snap.get("intended_dir") or snap.get("attacker_last_dir") or "-"
            if intent in ("L", "R", "U", "D"):
                move_r += -0.2
    # STUCK recovery bonus: if previous stuck cleared this step
    if snap.get("stuck_recovered", False):
        move_r += 0.1
    # map transition success
    if snap.get("map_changed", False):
        move_r += 0.3
    # distance delta (negative delta = closer = good)
    delta = snap.get("attacker_dist_delta")
    if delta is not None:
        try:
            d = int(delta)
            if d < 0:
                move_r += 0.05 * min(abs(d), 5)
            elif d > 0:
                move_r += -0.02 * min(d, 5)
        except Exception:
            pass
    return float(skill_r + move_r)


def _record_move_action(rec: ActionRecord, next_rec: Optional[ActionRecord] = None) -> int:
    """Infer the move action index from the snapshot pair.

    Priority:
        1. snapshot["intended_dir"] if "L"|"R"|"U"|"D"
        2. coord delta sign of (next.healer_coord - rec.healer_coord) — dominant axis
        3. fallback "stay"
    """
    snap = rec.snapshot_at_decision or {}
    intent = snap.get("intended_dir")
    if intent in MOVE_DIR_TO_INDEX:
        return MOVE_DIR_TO_INDEX[intent]
    if next_rec is not None:
        c0 = snap.get("healer_coord")
        c1 = (next_rec.snapshot_at_decision or {}).get("healer_coord")
        if c0 and c1:
            try:
                dx = int(c1[0]) - int(c0[0])
                dy = int(c1[1]) - int(c0[1])
                if dx == 0 and dy == 0:
                    return MOVE_DIR_TO_INDEX["stay"]
                if abs(dx) >= abs(dy):
                    return MOVE_DIR_TO_INDEX["R"] if dx > 0 else MOVE_DIR_TO_INDEX["L"]
                else:
                    return MOVE_DIR_TO_INDEX["D"] if dy > 0 else MOVE_DIR_TO_INDEX["U"]
            except Exception:
                pass
    return MOVE_DIR_TO_INDEX["stay"]


class ReplayBuffer:
    """Fixed-capacity circular buffer of Transition. Thread-safe-ish (single writer).

    sample(n) returns vectorized arrays for batched training.
    """

    def __init__(self, capacity: int = 50000, feature_dim: int = FEATURE_DIM):
        self.capacity = int(capacity)
        self.feature_dim = int(feature_dim)
        self._buf: List[Optional[Transition]] = [None] * self.capacity
        self._idx = 0
        self._size = 0
        self._extractor = FeatureExtractor()
        self._last_log_ts: float = 0.0  # for incremental update_from_log

    def __len__(self) -> int:
        return self._size

    def add(self, t: Transition) -> None:
        self._buf[self._idx] = t
        self._idx = (self._idx + 1) % self.capacity
        self._size = min(self._size + 1, self.capacity)

    def add_episode(self, transitions: Iterable[Transition]) -> int:
        n = 0
        for t in transitions:
            self.add(t)
            n += 1
        return n

    def update_from_log(self, records: List[ActionRecord]) -> int:
        """Convert ActionRecords (sorted by ts) into transitions, only NEW ones.

        Compares record.ts to self._last_log_ts. Returns number of transitions added.
        """
        if not records:
            return 0
        added = 0
        # Filter: keep records with ts > _last_log_ts AND known action
        new_records = [r for r in records if r.ts > self._last_log_ts]
        if len(new_records) < 2:
            return 0
        for i in range(len(new_records) - 1):
            r0 = new_records[i]
            r1 = new_records[i + 1]
            a_idx = ACTION_RULE_TO_INDEX.get(r0.action, NUM_ACTIONS - 1)  # unknown -> wait
            s = self._extractor.extract(_snap_from_dict(r0.snapshot_at_decision))
            s_next = self._extractor.extract(_snap_from_dict(r1.snapshot_at_decision))
            r = _record_reward(r0, r1)
            a_move = _record_move_action(r0, r1)
            self.add(Transition(s=s, a=a_idx, a_move=a_move, r=r, s_next=s_next, done=False))
            added += 1
        self._last_log_ts = new_records[-1].ts
        return added

    def sample(self, batch: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Returns (S[B,32], A[B], R[B], S_next[B,32]). Backward-compatible 4-tuple.

        For movement training use sample_with_move() which returns 5-tuple.
        """
        S, A, R, Sn, _Am = self._sample_arrays(batch)
        return S, A, R, Sn

    def sample_with_move(self, batch: int) -> Tuple[np.ndarray, np.ndarray,
                                                    np.ndarray, np.ndarray, np.ndarray]:
        """Returns (S, A_skill, A_move, R, S_next)."""
        S, A, R, Sn, Am = self._sample_arrays(batch)
        return S, A, Am, R, Sn

    def _sample_arrays(self, batch: int):
        if self._size == 0:
            raise ValueError("replay buffer is empty")
        idxs = np.random.randint(0, self._size, size=batch)
        S = np.zeros((batch, self.feature_dim), dtype=np.float32)
        A = np.zeros(batch, dtype=np.int64)
        Am = np.zeros(batch, dtype=np.int64)
        R = np.zeros(batch, dtype=np.float32)
        Sn = np.zeros((batch, self.feature_dim), dtype=np.float32)
        for i, j in enumerate(idxs):
            t = self._buf[j]
            S[i] = t.s
            A[i] = t.a
            Am[i] = getattr(t, "a_move", NUM_MOVE_ACTIONS - 1)
            R[i] = t.r
            Sn[i] = t.s_next
        return S, A, R, Sn, Am

    def all(self) -> List[Transition]:
        return [t for t in self._buf if t is not None]


__all__ = ["ReplayBuffer", "Transition"]
