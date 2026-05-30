"""ActionLog — chronological action records for analytics + future learning.

Subscribes to hand.cast_done / hand.cast_failed via EventBus and records.
Optionally writes to JSONL file (rotated by day).

Design ref: §2.8
"""
from __future__ import annotations
import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core.event_bus import Event, EventBus
from ..core.snapshot import SnapshotStore
from ..core.types import ActionRecord, CastError, CastResult

log = logging.getLogger("src_v2.memory.action_log")


class ActionLog:
    """In-memory ring buffer + optional JSONL file sink.

    Thread-safe append. Subscribes to hand.cast_done / hand.cast_failed.
    """

    def __init__(self,
                 store: SnapshotStore,
                 bus: EventBus,
                 capacity: int = 4096,
                 file_path: Optional[str] = None,
                 enabled: bool = True) -> None:
        self.store = store
        self.bus = bus
        self.enabled = enabled
        self._buf: deque[ActionRecord] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._file = None
        if file_path:
            os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
            self._file = open(file_path, "a", encoding="utf-8")
        self._on_done_count = 0
        self._on_fail_count = 0

    def attach(self) -> None:
        self.bus.subscribe("hand.cast_done", self._on_done)
        self.bus.subscribe("hand.cast_failed", self._on_failed)

    def _on_done(self, evt: Event) -> None:
        if not self.enabled:
            return
        payload: CastResult = evt.payload
        snap_dict = self._snap_summary()
        latency = (time.monotonic() - payload.request.requested_at) * 1000.0
        rec = ActionRecord(
            ts=evt.ts,
            action=payload.request.name,
            snapshot_at_decision=snap_dict,
            result=payload.status,
            latency_ms=latency,
            detail=payload.detail or "",
        )
        self._append(rec)
        self._on_done_count += 1

    def _on_failed(self, evt: Event) -> None:
        if not self.enabled:
            return
        payload: CastError = evt.payload
        rec = ActionRecord(
            ts=evt.ts,
            action=payload.request.name,
            snapshot_at_decision=self._snap_summary(),
            result="failed",
            latency_ms=0.0,
            detail=payload.reason,
        )
        self._append(rec)
        self._on_fail_count += 1

    def _snap_summary(self) -> Dict[str, Any]:
        s = self.store.read()
        return {
            "hp": s.hp, "mp": s.mp,
            "healer_coord": s.healer_coord,
            "healer_map": s.healer_map,
            "attacker_coord": s.attacker_coord,
            "attacker_map": s.attacker_map,
            "attacker_hp": s.attacker_hp,
            "red_tab": s.red_tab_present,
        }

    def _append(self, rec: ActionRecord) -> None:
        with self._lock:
            self._buf.append(rec)
            if self._file:
                try:
                    line = json.dumps({
                        "ts": rec.ts,
                        "action": rec.action,
                        "result": rec.result,
                        "latency_ms": rec.latency_ms,
                        "detail": rec.detail,
                        "snap": rec.snapshot_at_decision,
                    }, default=str, ensure_ascii=False)
                    self._file.write(line + "\n")
                    self._file.flush()
                except Exception:  # noqa: BLE001
                    log.exception("file sink write fail")

    def recent(self, n: int = 20) -> List[ActionRecord]:
        with self._lock:
            return list(self._buf)[-n:]

    def all(self) -> List[ActionRecord]:
        with self._lock:
            return list(self._buf)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    def close(self) -> None:
        if self._file:
            try:
                self._file.close()
            except Exception:  # noqa: BLE001
                pass

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "total": len(self._buf),
                "done": self._on_done_count,
                "failed": self._on_fail_count,
            }
