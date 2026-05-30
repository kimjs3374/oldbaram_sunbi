"""Phase 1 — types unit tests."""
import pytest

from src_v2.core.types import (
    CastRequest, CastResult, CastError, RuleContext,
    Detection, AttackerState,
)


def test_cast_request_defaults():
    r = CastRequest("self_heal")
    assert r.name == "self_heal"
    assert r.priority == 100
    assert r.ctx == {}
    assert r.requested_at > 0


def test_cast_request_frozen():
    r = CastRequest("x")
    with pytest.raises(Exception):
        r.name = "y"


def test_detection_center_auto():
    d = Detection(cls="red_tab", bbox=(10, 20, 30, 40), conf=0.9)
    assert d.center == (20, 30)


def test_detection_explicit_center():
    d = Detection(cls="red_tab", bbox=(0, 0, 100, 100), conf=0.9, center=(50, 50))
    assert d.center == (50, 50)


def test_attacker_state_default():
    s = AttackerState()
    assert s.coord is None
    assert s.last_dir == "-"


def test_rule_context_mutable():
    ctx = RuleContext(cfg={"a": 1})
    ctx.cooldowns["x"] = 5.0
    ctx.in_progress.add("self_heal")
    assert ctx.cooldowns["x"] == 5.0
    assert "self_heal" in ctx.in_progress
