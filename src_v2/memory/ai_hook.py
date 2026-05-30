"""AI Hook — interface for plug-in learning modules.

Concrete RL/imitation learning implementations live in future PDCA cycles.
This module just defines the protocol + a no-op default.

Design ref: §2.8
"""
from __future__ import annotations
from typing import Optional, Protocol

from ..core.snapshot import Snapshot
from ..core.types import ActionRecord, CastRequest


class AiHook(Protocol):
    """Plug-in interface for AI/learning modules."""

    def on_action(self, record: ActionRecord) -> None:
        """Called for each completed action."""
        ...

    def suggest(self, snapshot: Snapshot) -> Optional[CastRequest]:
        """May return a CastRequest to add to brain's decision queue.
        Return None to defer to rule engine."""
        ...


class NullAiHook:
    """No-op default — does nothing."""

    def __init__(self) -> None:
        self.action_count = 0
        self.suggest_calls = 0

    def on_action(self, record: ActionRecord) -> None:
        self.action_count += 1

    def suggest(self, snapshot: Snapshot) -> Optional[CastRequest]:
        self.suggest_calls += 1
        return None
