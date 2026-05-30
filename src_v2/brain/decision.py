"""Decision helpers — RuleContext builder + cooldown helpers.

Design ref: §2.5 + §4.1 RuleContext

Phase 7: cfg lookups overlay PluginRegistry params so meta-learner tuning
takes effect on next rule evaluation without rebuild.
"""
from __future__ import annotations
import time
from typing import Any, Dict, Optional, Set

from ..core.plugin_registry import PluginRegistry
from ..core.snapshot import Snapshot, SnapshotStore
from ..core.types import RuleContext


# Map cfg key -> learnable target_id. Adding here is enough for runtime override.
_CFG_TO_TARGET = {
    "self_heal_hp_thr": "rule.self_heal.hp_thr",
    "self_heal_burst_count": "rule.self_heal.burst_count",
    "self_heal_burst_gap_ms": "rule.self_heal.burst_gap_ms",
    "gyoungryeok_mp_thr": "rule.gyoungryeok.mp_thr",
    "parhon_edge_sec": "rule.parhon.edge_sec",
    "seq_rclick_duration_ms": "rule.seq_rclick.duration_ms",
    "seq_rclick_interval_ms": "rule.seq_rclick.interval_ms",
    "parlyuk_offset_sec": "rule.parlyuk.offset_sec",
    "mujang_enabled": "rule.mujang.enabled",
    "boho_enabled": "rule.boho.enabled",
    "parhon_enabled": "rule.parhon.enabled",
}


def _overlay(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Return cfg overlaid with PluginRegistry param values where mapped."""
    snap = PluginRegistry.snapshot_params()
    if not snap:
        return cfg
    out = dict(cfg)
    for cfg_key, target in _CFG_TO_TARGET.items():
        if target in snap:
            out[cfg_key] = snap[target]
    return out


class RuleContextBuilder:
    """Builds a RuleContext per rule evaluation.

    Maintains last_cast timestamps + in_progress shared with hands executor.
    """

    def __init__(self,
                 cfg: Optional[Dict[str, Any]] = None,
                 in_progress: Optional[Set[str]] = None,
                 extras: Optional[Dict[str, Any]] = None) -> None:
        # 2026-04-27 BUG-FIX: cfg ref 그대로 보관 — set_skill_enabled 즉시 반영.
        self.cfg = cfg if cfg is not None else {}
        self._last_cast: Dict[str, float] = {}
        self.in_progress: Set[str] = in_progress if in_progress is not None else set()
        # 2026-04-28 audit 8.1 2단계: extras 도 ref 보관 (worker_state 와 단일화).
        # 이전 dict(extras or {}) copy 는 외부 (DecisionScratch) 와 동기화 깨짐.
        self.extras: Dict[str, Any] = extras if extras is not None else {}

    def mark_cast(self, name: str, ts: Optional[float] = None) -> None:
        self._last_cast[name] = ts or time.monotonic()

    def cooldown_remaining(self, name: str, period_sec: float, now: Optional[float] = None) -> float:
        last = self._last_cast.get(name, 0.0)
        if last == 0.0:
            return 0.0
        return max(0.0, period_sec - ((now or time.monotonic()) - last))

    def build(self, snap: Snapshot) -> RuleContext:
        cooldowns: Dict[str, float] = {}
        # snapshot fields (already from OCR)
        if snap.cd_parlyuk >= 0:
            cooldowns["parlyuk"] = float(snap.cd_parlyuk)
        if snap.cd_baekho >= 0:
            cooldowns["baekho"] = float(snap.cd_baekho)
        if snap.cd_parhon >= 0:
            cooldowns["parhon"] = float(snap.cd_parhon)
        if snap.cd_revive >= 0:
            cooldowns["revive"] = float(snap.cd_revive)
        # Note: extras 는 같은 dict ref 를 공유해야 룰 edge 상태(self_dead_prev 등)가
        # 호출 간 보존됨. v1 healer_worker 가 worker 인스턴스 멤버로 prev 플래그
        # 들고 있던 것과 동치.
        return RuleContext(
            cfg=_overlay(self.cfg),
            cooldowns=cooldowns,
            last_cast=dict(self._last_cast),
            in_progress=self.in_progress,
            extras=self.extras,
        )
