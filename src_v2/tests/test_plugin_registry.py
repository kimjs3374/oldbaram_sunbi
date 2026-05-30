"""Phase 1 — PluginRegistry unit tests."""
import pytest

from src_v2.core.plugin_registry import (
    PluginRegistry, RuleSpec, rule, sequence, watcher,
)


def test_register_rule_via_decorator():
    @rule(name="self_heal", priority=10, topics=["eye.hp"])
    def my_rule(snap, ctx):
        return None

    rules = PluginRegistry.get_rules()
    assert len(rules) == 1
    assert rules[0].name == "self_heal"
    assert rules[0].priority == 10
    assert rules[0].topics == ["eye.hp"]
    assert rules[0].handler is my_rule
    assert rules[0].enabled is True


def test_register_sequence_via_decorator():
    @sequence("self_heal_seq")
    def fn(ctx):
        pass

    h = PluginRegistry.get_sequence("self_heal_seq")
    assert h is fn
    assert "self_heal_seq" in PluginRegistry.list_sequences()


def test_register_watcher_via_decorator():
    @watcher("yolo")
    class FakeWatcher:
        pass

    cls = PluginRegistry.get_watcher("yolo")
    assert cls is FakeWatcher


def test_priority_sorting():
    @rule(name="low", priority=100, topics=["t"])
    def r1(s, c):
        return None

    @rule(name="high", priority=1, topics=["t"])
    def r2(s, c):
        return None

    @rule(name="mid", priority=50, topics=["t"])
    def r3(s, c):
        return None

    rules = sorted(PluginRegistry.get_rules(), key=lambda x: x.priority)
    assert [r.name for r in rules] == ["high", "mid", "low"]


def test_set_rule_enabled():
    @rule(name="x", priority=1, topics=[])
    def fn(s, c):
        return None

    assert PluginRegistry.set_rule_enabled("x", False)
    assert PluginRegistry.get_rule("x").enabled is False
    assert PluginRegistry.set_rule_enabled("nonexistent", False) is False


def test_overwrite_warning():
    @rule(name="dup", priority=1, topics=[])
    def fn1(s, c):
        return None

    @rule(name="dup", priority=2, topics=[])
    def fn2(s, c):
        return None

    # latest wins
    spec = PluginRegistry.get_rule("dup")
    assert spec.handler is fn2
    assert spec.priority == 2


def test_reset():
    @rule(name="x", priority=1, topics=[])
    def fn(s, c):
        return None

    assert PluginRegistry.get_rule("x") is not None
    PluginRegistry.reset()
    assert PluginRegistry.get_rule("x") is None


def test_get_unknown_returns_none():
    assert PluginRegistry.get_rule("unknown") is None
    assert PluginRegistry.get_sequence("unknown") is None
    assert PluginRegistry.get_watcher("unknown") is None
