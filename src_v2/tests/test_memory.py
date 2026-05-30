"""Phase 7 — Memory unit tests."""
import os
import json
import tempfile
import time
import pytest

from src_v2.core.event_bus import EventBus
from src_v2.core.snapshot import SnapshotStore
from src_v2.core.types import CastRequest, CastResult, CastError, ActionRecord

from src_v2.memory.action_log import ActionLog
from src_v2.memory.ai_hook import NullAiHook


def test_action_log_records_done():
    store = SnapshotStore()
    bus = EventBus()
    store.update(hp=50)
    log = ActionLog(store, bus)
    log.attach()

    req = CastRequest("self_heal", priority=10)
    bus.publish("hand.cast_done", CastResult(request=req, status="ok"))
    time.sleep(0.005)
    recs = log.all()
    assert len(recs) == 1
    assert recs[0].action == "self_heal"
    assert recs[0].result == "ok"
    assert recs[0].snapshot_at_decision["hp"] == 50


def test_action_log_records_failed():
    store = SnapshotStore()
    bus = EventBus()
    log = ActionLog(store, bus)
    log.attach()
    req = CastRequest("seq", priority=10)
    bus.publish("hand.cast_failed", CastError(req, "timeout"))
    recs = log.all()
    assert len(recs) == 1
    assert recs[0].result == "failed"
    assert recs[0].detail == "timeout"


def test_action_log_capacity():
    store = SnapshotStore()
    bus = EventBus()
    log = ActionLog(store, bus, capacity=5)
    log.attach()
    for i in range(20):
        bus.publish("hand.cast_done", CastResult(
            request=CastRequest(f"seq{i}", priority=10),
            status="ok",
        ))
    recs = log.all()
    assert len(recs) == 5
    # last 5
    actions = [r.action for r in recs]
    assert actions == [f"seq{i}" for i in range(15, 20)]


def test_action_log_disabled():
    store = SnapshotStore()
    bus = EventBus()
    log = ActionLog(store, bus, enabled=False)
    log.attach()
    bus.publish("hand.cast_done", CastResult(
        request=CastRequest("x"), status="ok",
    ))
    assert log.all() == []


def test_action_log_file_sink(tmp_path):
    store = SnapshotStore()
    bus = EventBus()
    fp = str(tmp_path / "actions.jsonl")
    log = ActionLog(store, bus, file_path=fp)
    log.attach()
    bus.publish("hand.cast_done", CastResult(
        request=CastRequest("self_heal"), status="ok",
    ))
    log.close()
    assert os.path.exists(fp)
    with open(fp) as f:
        line = f.readline()
        rec = json.loads(line)
        assert rec["action"] == "self_heal"


def test_null_ai_hook():
    h = NullAiHook()
    rec = ActionRecord(ts=0, action="x", snapshot_at_decision={}, result="ok", latency_ms=0)
    h.on_action(rec)
    assert h.action_count == 1
    assert h.suggest(None) is None
    assert h.suggest_calls == 1
