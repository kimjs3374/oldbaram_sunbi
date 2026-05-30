"""Phase 4 — Brain (rule engine + rules) unit tests."""
import importlib
import queue
import time
import pytest

from src_v2.core.event_bus import EventBus
from src_v2.core.plugin_registry import PluginRegistry
from src_v2.core.snapshot import Snapshot, SnapshotStore
from src_v2.core.types import RuleContext, CastResult, CastRequest

from src_v2.brain.decision import RuleContextBuilder
from src_v2.brain.rule_engine import RuleEngine

from src_v2.hands.input_dispatcher import InputDispatcher, NullKeys
from src_v2.hands.skill_executor import HandsAPI


def _reload_rules():
    """Helper — re-import rule modules so decorators run after reset."""
    import src_v2.brain.rules as rules_pkg
    importlib.reload(rules_pkg)
    for sub in [
        "self_heal", "self_revive", "attacker_revive", "parhon",
        "baekho", "parlyuk", "gyoungryeok", "mujang", "boho",
        "seq_rclick", "tab_lock",
    ]:
        m = importlib.import_module(f"src_v2.brain.rules.{sub}")
        importlib.reload(m)


# ----- individual rule tests (pure function semantics) -----

def test_self_heal_fires_below_threshold():
    _reload_rules()
    spec = PluginRegistry.get_rule("self_heal")
    snap = Snapshot(hp=30)
    ctx = RuleContext(cfg={"self_heal_hp_thr": 50})
    req = spec.handler(snap, ctx)
    assert req is not None
    assert req.name == "self_heal"


def test_self_heal_skip_above_threshold():
    _reload_rules()
    spec = PluginRegistry.get_rule("self_heal")
    snap = Snapshot(hp=80)
    ctx = RuleContext(cfg={"self_heal_hp_thr": 50})
    assert spec.handler(snap, ctx) is None


def test_self_heal_skip_when_in_progress():
    _reload_rules()
    spec = PluginRegistry.get_rule("self_heal")
    snap = Snapshot(hp=30)
    ctx = RuleContext(cfg={"self_heal_hp_thr": 50}, in_progress={"self_heal"})
    assert spec.handler(snap, ctx) is None


def test_self_revive_fires_at_zero():
    _reload_rules()
    spec = PluginRegistry.get_rule("self_revive")
    assert spec.handler(Snapshot(hp=0), RuleContext()).name == "self_revive"
    assert spec.handler(Snapshot(hp=10), RuleContext()) is None


def test_attacker_revive_fires_when_attacker_dead():
    """v1 1:1: atk_hp=0 + self_hp>0 edge."""
    _reload_rules()
    spec = PluginRegistry.get_rule("attacker_revive")
    # Self alive + atk dead = first edge → fire.
    assert spec.handler(
        Snapshot(attacker_hp=0, hp=80), RuleContext(extras={})
    ).name == "attacker_revive"
    # Atk alive — no fire.
    assert spec.handler(
        Snapshot(attacker_hp=50, hp=80), RuleContext(extras={})
    ) is None
    # Self dead — no fire (self_revive 우선).
    assert spec.handler(
        Snapshot(attacker_hp=0, hp=0), RuleContext(extras={})
    ) is None


def test_parhon_edge_trigger():
    """v1 1:1: honma>0 cross-up edge from snap, not cooldowns dict."""
    _reload_rules()
    spec = PluginRegistry.get_rule("parhon")
    # First edge → fire.
    assert spec.handler(
        Snapshot(attacker_honma_sec=2), RuleContext(extras={})
    ).name == "parhon"
    # honma=0 → no fire, prev cleared.
    assert spec.handler(
        Snapshot(attacker_honma_sec=0), RuleContext(extras={})
    ) is None
    # disabled
    assert spec.handler(
        Snapshot(attacker_honma_sec=2),
        RuleContext(cfg={"parhon_enabled": False}, extras={})
    ) is None


def test_baekho_skips_when_active():
    """v1 1:1: snap.cd_baekho==0 ready edge."""
    _reload_rules()
    spec = PluginRegistry.get_rule("baekho")
    snap_active = Snapshot(cd_baekho=0, buff_baekho_active=True)
    assert spec.handler(snap_active, RuleContext(extras={})) is None
    snap_idle = Snapshot(cd_baekho=0, buff_baekho_active=False)
    assert spec.handler(snap_idle, RuleContext(extras={})).name == "baekho"
    # cd remaining → no fire.
    assert spec.handler(
        Snapshot(cd_baekho=5), RuleContext(extras={})
    ) is None


def test_parlyuk_cd_ready():
    """v1 1:1: snap.cd_parlyuk<=offset ready edge."""
    _reload_rules()
    spec = PluginRegistry.get_rule("parlyuk")
    assert spec.handler(
        Snapshot(cd_parlyuk=0, buff_parlyuk_active=False),
        RuleContext(extras={})
    ).name == "parlyuk"
    # cd remaining
    assert spec.handler(
        Snapshot(cd_parlyuk=5), RuleContext(extras={})
    ) is None


def test_gyoungryeok_mp_threshold():
    _reload_rules()
    spec = PluginRegistry.get_rule("gyoungryeok")
    ctx = RuleContext(cfg={"gyoungryeok_mp_thr": 30})
    assert spec.handler(Snapshot(mp=20), ctx).name == "gyoungryeok"
    assert spec.handler(Snapshot(mp=80), ctx) is None
    # buff active -> skip
    snap = Snapshot(mp=10, buff_gyoungryeok_active=True)
    assert spec.handler(snap, ctx) is None


def test_seq_rclick_requires_red_tab():
    _reload_rules()
    spec = PluginRegistry.get_rule("seq_rclick")
    ctx = RuleContext()
    snap_no_red = Snapshot(red_tab_present=False, red_tab_pos=None)
    assert spec.handler(snap_no_red, ctx) is None
    snap_red = Snapshot(red_tab_present=True, red_tab_pos=(100, 200))
    req = spec.handler(snap_red, ctx)
    assert req is not None
    assert req.ctx["red_tab_pos"] == (100, 200)


def test_tab_lock_pending():
    _reload_rules()
    spec = PluginRegistry.get_rule("tab_lock")
    ctx = RuleContext()
    assert spec.handler(Snapshot(tab_lock_pending=False), ctx) is None
    assert spec.handler(Snapshot(tab_lock_pending=True), ctx).name == "tab_lock"


# ----- engine integration tests -----

def test_engine_dispatches_on_topic():
    _reload_rules()
    bus = EventBus()
    store = SnapshotStore()
    q: queue.PriorityQueue = queue.PriorityQueue()
    api = HandsAPI(q, InputDispatcher())
    builder = RuleContextBuilder(cfg={"self_heal_hp_thr": 50})
    engine = RuleEngine(store, bus, api, ctx_builder=builder)
    engine.start()

    store.update(hp=30)
    bus.publish("eye.hp", 30)
    # request should have been queued
    assert q.qsize() == 1
    pri, _, req = q.get_nowait()
    assert req.name == "self_heal"


def test_engine_priority_first_wins():
    """When multiple rules subscribe to same topic, priority lowest wins."""
    PluginRegistry.reset()

    from src_v2.core.plugin_registry import rule as rule_dec

    @rule_dec(name="lower", priority=5, topics=["t"])
    def lower(s, c):
        return CastRequest("seq_lower", priority=5)

    @rule_dec(name="higher", priority=50, topics=["t"])
    def higher(s, c):
        return CastRequest("seq_higher", priority=50)

    bus = EventBus()
    store = SnapshotStore()
    q: queue.PriorityQueue = queue.PriorityQueue()
    api = HandsAPI(q, InputDispatcher())
    engine = RuleEngine(store, bus, api)
    engine.start()
    bus.publish("t", None)
    # Only first matching rule (lowest priority value) wins
    assert q.qsize() == 1
    _, _, r = q.get_nowait()
    assert r.name == "seq_lower"


def test_engine_disabled_rule_skipped():
    PluginRegistry.reset()
    from src_v2.core.plugin_registry import rule as rule_dec

    @rule_dec(name="r", priority=10, topics=["t"])
    def r(s, c):
        return CastRequest("rseq")

    PluginRegistry.set_rule_enabled("r", False)

    bus = EventBus()
    store = SnapshotStore()
    q: queue.PriorityQueue = queue.PriorityQueue()
    engine = RuleEngine(store, bus, HandsAPI(q, InputDispatcher()))
    engine.start()
    bus.publish("t", None)
    assert q.qsize() == 0


def test_engine_marks_last_cast_on_done():
    _reload_rules()
    bus = EventBus()
    store = SnapshotStore()
    q: queue.PriorityQueue = queue.PriorityQueue()
    api = HandsAPI(q, InputDispatcher())
    builder = RuleContextBuilder(cfg={"self_heal_hp_thr": 50})
    engine = RuleEngine(store, bus, api, ctx_builder=builder)
    engine.start()
    req = CastRequest("self_heal", priority=10)
    bus.publish("hand.cast_done", CastResult(request=req, status="ok"))
    assert "self_heal" in builder._last_cast


def test_rules_self_register():
    _reload_rules()
    names = {r.name for r in PluginRegistry.get_rules()}
    expected = {
        "self_heal", "self_revive", "attacker_revive", "parhon",
        "baekho", "parlyuk", "gyoungryeok", "seq_rclick", "tab_lock",
    }
    assert expected.issubset(names)
