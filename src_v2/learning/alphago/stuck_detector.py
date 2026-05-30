"""STUCK Detector — discovers spatial hot-spots where the healer gets stuck.

Algorithm (NN-independent, can run standalone):
    1. Walk through ActionLog (or transitions augmented with pre/post coords).
    2. For each step where movement was attempted but coord didn't change for
       stuck_window_s seconds, record (map_name, cell, direction) as a candidate.
    3. Cluster by (map, cell, dir). When a cell exceeds occurrence threshold,
       it becomes a hot-spot.
    4. apply_blacklist() pushes hot-spots into PluginRegistry as learnable params:
         "muscle.blacklist.{map}.{x}.{y}.{dir}" -> True
    5. muscle.main_loop reads these to override decide_direction.

Recovery: a periodic decay re-evaluates hot-spots; if recent records show the
direction now succeeds, the entry is removed.

Why standalone: STUCK fixes are immediate-value — don't gate behind NN training.
"""
from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ...core.plugin_registry import PluginRegistry
from ...core.types import ActionRecord

log = logging.getLogger("src_v2.learning.alphago.stuck_detector")


@dataclass(frozen=True)
class StuckCandidate:
    map_name: str
    cell: Tuple[int, int]   # (x, y) bucketed
    direction: str          # "L" | "R" | "U" | "D"
    occurrences: int = 1
    avg_duration_s: float = 0.0
    last_seen_ts: float = 0.0


def _bucket(coord: Optional[Tuple[int, int]], grid: int = 5) -> Optional[Tuple[int, int]]:
    """Bucket coord to a coarse grid so adjacent steps cluster together."""
    if not coord:
        return None
    try:
        x, y = int(coord[0]), int(coord[1])
        return (x // grid, y // grid)
    except Exception:
        return None


class StuckDetector:
    """Stateless analyzer + persistent blacklist publisher.

    Configuration:
        stuck_window_s: continuous no-progress duration to count as stuck (default 1.0)
        long_stuck_s:   threshold for "long stuck" extra penalty (default 10.0)
        threshold:      occurrences before a candidate becomes blacklisted (default 5)
        cell_grid:      coordinate bucketing grid size in pixels (default 5)
        decay_sec:      how often to re-check existing blacklist entries (default 300)
    """

    def __init__(self,
                 stuck_window_s: float = 1.0,
                 long_stuck_s: float = 10.0,
                 threshold: int = 5,
                 cell_grid: int = 5,
                 decay_sec: float = 300.0):
        self.stuck_window_s = float(stuck_window_s)
        self.long_stuck_s = float(long_stuck_s)
        self.threshold = int(threshold)
        self.cell_grid = int(cell_grid)
        self.decay_sec = float(decay_sec)
        # (map, cell, dir) -> StuckCandidate
        self._candidates: Dict[Tuple[str, Tuple[int, int], str], StuckCandidate] = {}
        # blacklisted keys -> first_added_ts
        self._blacklisted: Dict[str, float] = {}

    # ---- discovery ----

    def discover(self, log_records: List[ActionRecord]) -> List[StuckCandidate]:
        """Walk records pairwise. Return candidates updated this pass.

        Records use snapshot_at_decision dict — we read healer_coord, healer_map,
        and stuck_duration_s if present. If the new fields are absent (legacy),
        falls back to coord-equality check between consecutive records.
        """
        if not log_records or len(log_records) < 2:
            return []
        updated: List[StuckCandidate] = []
        for i in range(len(log_records) - 1):
            r0 = log_records[i]
            r1 = log_records[i + 1]
            d0 = r0.snapshot_at_decision or {}
            d1 = r1.snapshot_at_decision or {}
            coord0 = d0.get("healer_coord") or d0.get("pre_coord")
            coord1 = d1.get("healer_coord") or d1.get("pre_coord")
            if not coord0 or not coord1:
                continue
            map_name = d0.get("healer_map") or ""
            # explicit stuck_duration_s field (preferred)
            dur = float(d0.get("stuck_duration_s", 0.0) or 0.0)
            # fallback: equal coords between consecutive records
            same_coord = tuple(coord0) == tuple(coord1)
            dt = max(0.0, float(r1.ts) - float(r0.ts))
            if dur < self.stuck_window_s and not (same_coord and dt >= self.stuck_window_s):
                continue
            direction = d0.get("attacker_last_dir") or d0.get("intended_dir") or "-"
            if direction not in ("L", "R", "U", "D"):
                continue
            cell = _bucket(coord0, self.cell_grid)
            if cell is None:
                continue
            key = (map_name, cell, direction)
            prev = self._candidates.get(key)
            n = (prev.occurrences if prev else 0) + 1
            avg = ((prev.avg_duration_s * (n - 1)) if prev else 0.0)
            avg = (avg + max(dur, dt)) / max(1, n)
            cand = StuckCandidate(
                map_name=map_name,
                cell=cell,
                direction=direction,
                occurrences=n,
                avg_duration_s=avg,
                last_seen_ts=float(r0.ts),
            )
            self._candidates[key] = cand
            updated.append(cand)
        return updated

    def should_blacklist(self, candidate: StuckCandidate) -> bool:
        return candidate.occurrences >= self.threshold

    # ---- blacklist publication ----

    def apply_blacklist(self, candidates: Optional[List[StuckCandidate]] = None) -> int:
        """Publish qualifying candidates to PluginRegistry. Returns count added."""
        added = 0
        items = candidates if candidates is not None else list(self._candidates.values())
        for c in items:
            if not self.should_blacklist(c):
                continue
            key = self._key_str(c)
            if key in self._blacklisted:
                continue
            PluginRegistry.set_param(key, True, force=True)
            self._blacklisted[key] = time.monotonic()
            added += 1
            log.info("stuck blacklist add: %s (occ=%d avg=%.2fs)",
                     key, c.occurrences, c.avg_duration_s)
        return added

    def revoke_if_clear(self, recent_records: List[ActionRecord]) -> int:
        """Remove blacklist entries when recent log shows the direction now succeeds.

        Heuristic: if recent records visit the cell with the same direction and the
        coord changed (no stuck), revoke.
        """
        if not recent_records:
            return 0
        revoked = 0
        clear_keys: Dict[str, bool] = {}
        for i in range(len(recent_records) - 1):
            r0 = recent_records[i]
            r1 = recent_records[i + 1]
            d0 = r0.snapshot_at_decision or {}
            d1 = r1.snapshot_at_decision or {}
            c0 = d0.get("healer_coord")
            c1 = d1.get("healer_coord")
            if not c0 or not c1 or tuple(c0) == tuple(c1):
                continue
            cell = _bucket(c0, self.cell_grid)
            map_name = d0.get("healer_map") or ""
            direction = d0.get("attacker_last_dir") or d0.get("intended_dir") or "-"
            if cell is None or direction not in ("L", "R", "U", "D"):
                continue
            key = f"muscle.blacklist.{map_name}.{cell[0]}.{cell[1]}.{direction}"
            clear_keys[key] = True
        for k in clear_keys:
            if k in self._blacklisted:
                PluginRegistry.set_param(k, False, force=True)
                del self._blacklisted[k]
                # also reset candidate counter so we don't immediately re-blacklist
                revoked += 1
                log.info("stuck blacklist revoke: %s", k)
        return revoked

    def stats(self) -> dict:
        return {
            "candidates": len(self._candidates),
            "blacklisted": len(self._blacklisted),
            "threshold": self.threshold,
        }

    def _key_str(self, c: StuckCandidate) -> str:
        return f"muscle.blacklist.{c.map_name}.{c.cell[0]}.{c.cell[1]}.{c.direction}"


__all__ = ["StuckDetector", "StuckCandidate"]
