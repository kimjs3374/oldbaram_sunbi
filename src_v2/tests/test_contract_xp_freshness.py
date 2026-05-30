"""XP watcher freshness 메타 계약 테스트 (v1_gap_fix_list P1-2)."""
from __future__ import annotations
import os, sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


class _Bus:
    def __init__(self): self.events = []
    def publish(self, t, p): self.events.append((t, p))
    def subscribe(self, *a, **k): pass


class _Store:
    def __init__(self, fields=None): self._f = dict(fields or {})
    def read_field(self, n, d=None): return self._f.get(n, d)
    def update(self, **kw): self._f.update(kw)


class _XpAdapter:
    def __init__(self, available=True, value=-1, raise_exc=False):
        self._a, self._v, self._r = available, value, raise_exc
    def is_available(self): return self._a
    def read(self, f, origin=None):
        if self._r: raise RuntimeError("xp crash")
        return self._v


def _state(bus): return [p for t, p in bus.events if t == "eye.xp_state"]


def test_unconfigured():
    from src_v2.eyes.xp_watcher import XpWatcher
    bus = _Bus()
    w = XpWatcher(_Store(), bus, adapter=_XpAdapter(available=False))
    w._tick()
    assert _state(bus)[-1]["source_state"] == "unconfigured"


def test_observed_publishes_age():
    from src_v2.eyes.xp_watcher import XpWatcher
    bus = _Bus()
    w = XpWatcher(_Store({"last_frame": object()}), bus,
                  adapter=_XpAdapter(value=1234567))
    w._tick()
    e = _state(bus)[-1]
    assert e["source_state"] == "observed"
    assert e["xp"] == 1234567
    # age 첫 관측 직후라 작음 (>=0).
    assert e["last_observed_age_sec"] >= 0


def test_rejected_on_exc():
    from src_v2.eyes.xp_watcher import XpWatcher
    bus = _Bus()
    w = XpWatcher(_Store({"last_frame": object()}), bus,
                  adapter=_XpAdapter(raise_exc=True))
    w._tick()
    assert _state(bus)[-1]["source_state"] == "rejected"


def test_empty_when_negative_value():
    from src_v2.eyes.xp_watcher import XpWatcher
    bus = _Bus()
    w = XpWatcher(_Store({"last_frame": object()}), bus,
                  adapter=_XpAdapter(value=-1))
    w._tick()
    assert _state(bus)[-1]["source_state"] == "empty"


if __name__ == "__main__":
    test_unconfigured()
    test_observed_publishes_age()
    test_rejected_on_exc()
    test_empty_when_negative_value()
    print("ALL PASS")
