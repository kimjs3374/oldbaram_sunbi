"""watcher publish source_state 메타 계약 테스트 (v1_gap_fix_list P0-2).

eye.cooldown_state / eye.hpmp_state topic 의 4-state 시나리오 보장.
- unconfigured: adapter is_available=False
- empty: adapter 결과 비어있음
- observed: 비어있지 않은 결과
- rejected: read 예외
"""
from __future__ import annotations
import sys, os, types
import importlib

# 경로/패키지 stub
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


class _Bus:
    def __init__(self):
        self.events = []  # list[(topic, payload)]
    def publish(self, topic, payload):
        self.events.append((topic, payload))
    def subscribe(self, *a, **k):
        pass


class _Store:
    def __init__(self, fields=None):
        self._f = dict(fields or {})
    def read_field(self, name, default=None):
        return self._f.get(name, default)
    def update(self, **kw):
        self._f.update(kw)


class _CdAdapter:
    def __init__(self, available=True, result=None, raise_exc=False):
        self._a = available
        self._r = result if result is not None else {}
        self._raise = raise_exc
    def is_available(self): return self._a
    def read(self, frame, origin=None):
        if self._raise: raise RuntimeError("ocr crash")
        return self._r


class _HpmpAdapter:
    def __init__(self, available=True, tup=(-1,-1,-1,-1,-1,-1), raise_exc=False):
        self._a = available
        self._t = tup
        self._raise = raise_exc
    def is_available(self): return self._a
    def read(self, frame, origin=None):
        if self._raise: raise RuntimeError("hpmp crash")
        return self._t


def _state_events(bus, topic):
    return [p for t, p in bus.events if t == topic]


def test_cooldown_unconfigured():
    from src_v2.eyes.cooldown_watcher import CooldownWatcher
    bus = _Bus()
    w = CooldownWatcher(_Store(), bus, adapter=_CdAdapter(available=False), slot="cd")
    w._tick()
    evs = _state_events(bus, "eye.cooldown_state")
    assert evs and evs[-1]["source_state"] == "unconfigured"


def test_cooldown_empty_no_frame():
    from src_v2.eyes.cooldown_watcher import CooldownWatcher
    bus = _Bus()
    w = CooldownWatcher(_Store({"last_frame": None}), bus, adapter=_CdAdapter(available=True), slot="cd")
    w._tick()
    evs = _state_events(bus, "eye.cooldown_state")
    assert evs and evs[-1]["source_state"] == "empty"


def test_cooldown_observed():
    from src_v2.eyes.cooldown_watcher import CooldownWatcher
    bus = _Bus()
    store = _Store({"last_frame": object(), "monitor_origin": (0, 0)})
    w = CooldownWatcher(store, bus, adapter=_CdAdapter(result={"백호의희원": 50}), slot="cd")
    w._tick()
    evs = _state_events(bus, "eye.cooldown_state")
    assert evs and evs[-1]["source_state"] == "observed", evs


def test_cooldown_rejected():
    from src_v2.eyes.cooldown_watcher import CooldownWatcher
    bus = _Bus()
    store = _Store({"last_frame": object()})
    w = CooldownWatcher(store, bus, adapter=_CdAdapter(raise_exc=True), slot="cd")
    w._tick()
    evs = _state_events(bus, "eye.cooldown_state")
    assert evs and evs[-1]["source_state"] == "rejected"


def test_hpmp_unconfigured():
    from src_v2.eyes.hpmp_watcher import HpMpWatcher
    bus = _Bus()
    w = HpMpWatcher(_Store(), bus, adapter=_HpmpAdapter(available=False))
    w._tick()
    evs = _state_events(bus, "eye.hpmp_state")
    assert evs and evs[-1]["source_state"] == "unconfigured"


def test_hpmp_observed_and_empty():
    from src_v2.eyes.hpmp_watcher import HpMpWatcher
    bus = _Bus()
    store = _Store({"last_frame": object()})
    w = HpMpWatcher(store, bus, adapter=_HpmpAdapter(tup=(85, 70, 850, 700, 1000, 1000)))
    w._tick()
    evs = _state_events(bus, "eye.hpmp_state")
    assert evs and evs[-1]["source_state"] == "observed"

    bus2 = _Bus()
    w2 = HpMpWatcher(store, bus2, adapter=_HpmpAdapter(tup=(-1,-1,-1,-1,-1,-1)))
    w2._tick()
    evs2 = _state_events(bus2, "eye.hpmp_state")
    assert evs2 and evs2[-1]["source_state"] == "empty"


def test_hpmp_rejected():
    from src_v2.eyes.hpmp_watcher import HpMpWatcher
    bus = _Bus()
    store = _Store({"last_frame": object()})
    w = HpMpWatcher(store, bus, adapter=_HpmpAdapter(raise_exc=True))
    w._tick()
    evs = _state_events(bus, "eye.hpmp_state")
    assert evs and evs[-1]["source_state"] == "rejected"


if __name__ == "__main__":
    test_cooldown_unconfigured()
    test_cooldown_empty_no_frame()
    test_cooldown_observed()
    test_cooldown_rejected()
    test_hpmp_unconfigured()
    test_hpmp_observed_and_empty()
    test_hpmp_rejected()
    print("ALL PASS")
