"""RecoveryDispatcher unit tests."""
import time
import pytest

from src_v2.core.event_bus import EventBus
from src_v2.core.types import CastRequest

from src_v2.brain.recovery import (
    RecoveryDispatcher, recovery, list_handlers, clear_handlers,
)


class FakeHandsAPI:
    def __init__(self):
        self.cast_calls = []

    def request_cast(self, req):
        self.cast_calls.append(req)


class FakeKeysAdapter:
    def __init__(self):
        self.sent = []

    def send_vk(self, vk, up):
        self.sent.append((vk, up))


def test_self_heal_recovery_emits_esc_and_retry():
    """self_heal no_effect → esc_recover + self_heal 재시도."""
    bus = EventBus()
    hands = FakeHandsAPI()
    disp = RecoveryDispatcher(bus, hands, log_emit=lambda s: None)
    disp.attach()
    bus.publish("memory.outcome", {
        "action": "self_heal", "status": "no_effect",
        "ts": time.monotonic(), "latency_ms": 5000.0,
        "snap_before": {}, "snap_after": {},
    })
    names = [r.name for r in hands.cast_calls]
    assert "esc_recover" in names
    assert "self_heal" in names


def test_recovery_cooldown_blocks_repeat():
    """동일 키 cooldown 내 재트리거 무시."""
    bus = EventBus()
    hands = FakeHandsAPI()
    disp = RecoveryDispatcher(bus, hands, log_emit=lambda s: None)
    disp.attach()
    payload = {
        "action": "self_heal", "status": "no_effect",
        "ts": time.monotonic(), "latency_ms": 5000.0,
        "snap_before": {}, "snap_after": {},
    }
    bus.publish("memory.outcome", payload)
    n1 = len(hands.cast_calls)
    bus.publish("memory.outcome", payload)
    n2 = len(hands.cast_calls)
    assert n2 == n1  # cooldown 으로 차단됨
    s = disp.stats()
    assert s["skipped_cooldown"] >= 1


def test_recovery_disabled_short_circuits():
    bus = EventBus()
    hands = FakeHandsAPI()
    disp = RecoveryDispatcher(bus, hands, log_emit=lambda s: None, enabled=False)
    disp.attach()
    bus.publish("memory.outcome", {
        "action": "self_heal", "status": "no_effect",
        "ts": time.monotonic(), "latency_ms": 5000.0,
    })
    assert len(hands.cast_calls) == 0


def test_unknown_action_no_op():
    bus = EventBus()
    hands = FakeHandsAPI()
    disp = RecoveryDispatcher(bus, hands, log_emit=lambda s: None)
    disp.attach()
    bus.publish("memory.outcome", {
        "action": "unknown_xyz", "status": "no_effect",
        "ts": time.monotonic(),
    })
    assert len(hands.cast_calls) == 0


def test_atk_revive_recovery_chain():
    """attacker_revive no_effect → tab_home_target + attacker_revive 재시도."""
    bus = EventBus()
    hands = FakeHandsAPI()
    disp = RecoveryDispatcher(bus, hands, log_emit=lambda s: None)
    disp.attach()
    bus.publish("memory.outcome", {
        "action": "attacker_revive", "status": "no_effect",
        "ts": time.monotonic(),
    })
    names = [r.name for r in hands.cast_calls]
    assert "tab_home_target" in names
    assert "attacker_revive" in names


def test_stuck_recovery_emits_ortho_unstick():
    bus = EventBus()
    hands = FakeHandsAPI()
    disp = RecoveryDispatcher(bus, hands, log_emit=lambda s: None)
    disp.attach()
    bus.publish("memory.outcome", {
        "action": "move_direction", "status": "no_effect",
        "ts": time.monotonic(),
    })
    names = [r.name for r in hands.cast_calls]
    assert "ortho_unstick" in names


def test_fg_lost_sends_s_key():
    bus = EventBus()
    hands = FakeHandsAPI()
    keys = FakeKeysAdapter()
    disp = RecoveryDispatcher(bus, hands, keys_adapter=keys, log_emit=lambda s: None)
    disp.attach()
    bus.publish("memory.outcome", {
        "action": "fg_lost", "status": "fail",
        "ts": time.monotonic(),
    })
    # send_vk 0x53 down/up 둘 다 호출
    vks = [v for v, _ in keys.sent]
    assert 0x53 in vks


def test_anomaly_fps_low_routes_to_fps_low_handler():
    bus = EventBus()
    hands = FakeHandsAPI()
    warnings = []
    bus.subscribe("memory.warn", lambda e: warnings.append(e.payload))
    disp = RecoveryDispatcher(bus, hands, log_emit=lambda s: None)
    disp.attach()
    bus.publish("memory.anomaly", {
        "metric": "fps_avg",
        "value": 5.0,
        "baseline": 30.0,
        "z_score": 5.0,
        "snap_after": {"fps": 5.0},
    })
    assert any(w.get("reason") == "fps_low" for w in warnings)


def test_handler_decorator_registers():
    """custom decorator 등록 → list_handlers 에 잡힘."""
    @recovery("custom_test_action", on=["fail"])
    def _h(payload, ctx):
        return [CastRequest(name="dummy", priority=99)]
    keys = list_handlers()
    assert ("custom_test_action", "fail") in keys
    # cleanup
    keys_to_keep = [k for k in keys if k[0] != "custom_test_action"]
    # 직접 manual remove (clear 는 builtin 도 날림)
    from src_v2.brain.recovery import _REGISTRY
    _REGISTRY.pop(("custom_test_action", "fail"), None)


def test_chat_popup_recovery():
    bus = EventBus()
    hands = FakeHandsAPI()
    disp = RecoveryDispatcher(bus, hands, log_emit=lambda s: None)
    disp.attach()
    bus.publish("memory.outcome", {
        "action": "chat_popup", "status": "fail",
        "ts": time.monotonic(),
    })
    names = [r.name for r in hands.cast_calls]
    assert "esc_recover" in names
