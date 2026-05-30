"""src_v2.core — skeleton (event_bus, snapshot, plugin_registry, types).

Design ref: §2.1-§2.3
"""
from .event_bus import EventBus, Event
from .snapshot import Snapshot, SnapshotStore
from .plugin_registry import (
    PluginRegistry, RuleSpec, rule, sequence, watcher,
)
from .types import (
    CastRequest, CastResult, CastError, RuleContext, ActionRecord,
    Detection, AttackerState,
)

__all__ = [
    "EventBus", "Event",
    "Snapshot", "SnapshotStore",
    "PluginRegistry", "RuleSpec", "rule", "sequence", "watcher",
    "CastRequest", "CastResult", "CastError", "RuleContext", "ActionRecord",
    "Detection", "AttackerState",
]
