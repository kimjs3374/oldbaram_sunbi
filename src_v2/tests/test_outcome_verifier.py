"""OutcomeVerifier unit tests.

각 builtin verifier 가 snap_before vs snap_after 비교를 정확히 하는지,
deadline 만료 시 timeout/no_effect 분류가 맞는지 검증.
"""
import time
import pytest

from src_v2.core.event_bus import EventBus
from src_v2.core.snapshot import SnapshotStore
from src_v2.core.types import CastRequest, CastResult, CastError

from src_v2.memory.outcome_verifier import (
    OutcomeVerifier, BUILTIN_VERIFIERS,
    _v_self_heal, _v_self_revive, _v_attacker_revive,
    _v_gyoungryeok, _v_parhon, _v_baekho, _v_parlyuk,
    _v_mujang, _v_boho, _v_move_direction, _v_tab_confirm, _v_seq_rclick,
)


# ---------- pure verifier fn 단위 테스트 ----------

def test_self_heal_verifier_hp_increase():
    b = {"hp": 50, "mp": -1, "healer_coord": None}
    a = {"hp": 60, "mp": -1, "healer_coord": None}
    assert _v_self_heal(b, a) is True
    a2 = {"hp": 53, "mp": -1, "healer_coord": None}
    assert _v_self_heal(b, a2) is False  # +5 안 넘음


def test_self_revive_alive():
    assert _v_self_revive({"hp": 0}, {"hp": 100}) is True
    assert _v_self_revive({"hp": 0}, {"hp": 0}) is False


def test_attacker_revive_alive():
    assert _v_attacker_revive({"attacker_hp": 0}, {"attacker_hp": 100}) is True
    assert _v_attacker_revive({"attacker_hp": 0}, {"attacker_hp": 0}) is False


def test_gyoungryeok_mp_drop_or_buff_on():
    b = {"mp": 100, "buff_gyoungryeok_active": False}
    a = {"mp": 50, "buff_gyoungryeok_active": False}  # MP 50 떨어짐
    assert _v_gyoungryeok(b, a) is True
    a2 = {"mp": 100, "buff_gyoungryeok_active": True}
    assert _v_gyoungryeok(b, a2) is True


def test_parhon_honma_zero():
    assert _v_parhon({"attacker_honma_sec": 5}, {"attacker_honma_sec": 0}) is True
    assert _v_parhon({"attacker_honma_sec": 5}, {"attacker_honma_sec": 3}) is False


def test_baekho_cd_started():
    assert _v_baekho({"cd_baekho": -1}, {"cd_baekho": 30}) is True
    assert _v_baekho({"cd_baekho": -1}, {"cd_baekho": -1}) is False


def test_parlyuk_buff_on():
    assert _v_parlyuk({"buff_parlyuk_active": False}, {"buff_parlyuk_active": True}) is True


def test_mujang_boho_buff_sec():
    assert _v_mujang({"attacker_mujang_sec": -1}, {"attacker_mujang_sec": 60}) is True
    assert _v_boho({"attacker_boho_sec": -1}, {"attacker_boho_sec": 60}) is True


def test_move_direction_coord_change():
    assert _v_move_direction({"healer_coord": (10, 20)}, {"healer_coord": (11, 20)}) is True
    assert _v_move_direction({"healer_coord": (10, 20)}, {"healer_coord": (10, 20)}) is False
    assert _v_move_direction({"healer_coord": None}, {"healer_coord": (1, 1)}) is False


def test_tab_confirm_seq_change():
    assert _v_tab_confirm({"attacker_map_seq": 3}, {"attacker_map_seq": 4}) is True
    assert _v_tab_confirm({"attacker_map_seq": 3}, {"attacker_map_seq": 3}) is False


def test_seq_rclick_red_present():
    assert _v_seq_rclick({"red_tab_present": False}, {"red_tab_present": True}) is True
    assert _v_seq_rclick({"red_tab_present": False}, {"red_tab_present": False}) is False


# ---------- integration: cast_done → ok ----------

def test_outcome_verifier_emits_ok_on_hp_increase():
    store = SnapshotStore()
    bus = EventBus()
    store.update(hp=50)
    received = []
    bus.subscribe("memory.outcome", lambda e: received.append(e.payload))

    v = OutcomeVerifier(store, bus, poll_sec=0.01)
    v.attach()
    v.start()
    try:
        # 자힐 시전됐다고 알림
        req = CastRequest("self_heal", priority=10)
        bus.publish("hand.cast_done", CastResult(request=req, status="ok"))
        time.sleep(0.05)
        # HP 증가
        store.update(hp=80)
        # poll 대기
        deadline = time.time() + 1.5
        while time.time() < deadline and not received:
            time.sleep(0.02)
    finally:
        v.stop()

    assert len(received) >= 1
    assert received[0]["action"] == "self_heal"
    assert received[0]["status"] == "ok"


def test_outcome_verifier_emits_no_effect_on_timeout():
    """HP 변화 없이 deadline 지나면 no_effect/timeout."""
    store = SnapshotStore()
    bus = EventBus()
    store.update(hp=50)
    received = []
    bus.subscribe("memory.outcome", lambda e: received.append(e.payload))

    # baekho deadline=1.0s. cd_baekho 변화 없음 → no_effect.
    v = OutcomeVerifier(store, bus, poll_sec=0.02)
    # 짧은 deadline 으로 테스트
    v.verifiers["baekho"] = (BUILTIN_VERIFIERS["baekho"][0], 0.1)
    v.attach()
    v.start()
    try:
        req = CastRequest("baekho", priority=25)
        bus.publish("hand.cast_done", CastResult(request=req, status="ok"))
        time.sleep(0.5)
    finally:
        v.stop()

    assert len(received) >= 1
    assert received[0]["action"] == "baekho"
    assert received[0]["status"] in ("no_effect", "timeout")


def test_outcome_verifier_cast_failed_immediate_fail():
    store = SnapshotStore()
    bus = EventBus()
    received = []
    bus.subscribe("memory.outcome", lambda e: received.append(e.payload))
    v = OutcomeVerifier(store, bus, poll_sec=0.05)
    v.attach()
    v.start()
    try:
        req = CastRequest("self_heal", priority=10)
        bus.publish("hand.cast_failed", CastError(req, reason="executor busy"))
        time.sleep(0.1)
    finally:
        v.stop()

    assert any(e["status"] == "fail" and e["action"] == "self_heal" for e in received)


def test_unknown_action_is_ignored():
    store = SnapshotStore()
    bus = EventBus()
    received = []
    bus.subscribe("memory.outcome", lambda e: received.append(e.payload))
    v = OutcomeVerifier(store, bus, poll_sec=0.02)
    v.attach()
    v.start()
    try:
        req = CastRequest("unknown_skill", priority=99)
        bus.publish("hand.cast_done", CastResult(request=req, status="ok"))
        time.sleep(0.1)
    finally:
        v.stop()
    assert len(received) == 0


def test_submit_explicit_action_move_direction():
    """외부 submit() 으로 move_direction 검증 등록 → 좌표 변화시 ok."""
    store = SnapshotStore()
    bus = EventBus()
    store.update(healer_coord=(10, 20))
    received = []
    bus.subscribe("memory.outcome", lambda e: received.append(e.payload))
    v = OutcomeVerifier(store, bus, poll_sec=0.02)
    v.start()
    try:
        v.submit("move_direction", detail="direction=R")
        time.sleep(0.05)
        store.update(healer_coord=(11, 20))
        time.sleep(0.2)
    finally:
        v.stop()
    assert any(e["action"] == "move_direction" and e["status"] == "ok" for e in received)


def test_stats_counts():
    store = SnapshotStore()
    bus = EventBus()
    v = OutcomeVerifier(store, bus, poll_sec=0.02)
    v.attach()
    v.start()
    try:
        req = CastRequest("self_heal", priority=10)
        bus.publish("hand.cast_failed", CastError(req, reason="busy"))
        time.sleep(0.05)
    finally:
        v.stop()
    s = v.stats()
    assert s["fail"] >= 1
