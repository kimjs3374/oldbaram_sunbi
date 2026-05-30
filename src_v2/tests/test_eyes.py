"""Phase 2 — Eyes (watchers) unit tests with mock adapters."""
import time
import pytest

from src_v2.core.event_bus import EventBus
from src_v2.core.snapshot import SnapshotStore
from src_v2.core.types import AttackerState

from src_v2.eyes.capture import CaptureWatcher
from src_v2.eyes.yolo_watcher import YoloWatcher
from src_v2.eyes.ocr_watcher import OcrWatcher
from src_v2.eyes.cooldown_watcher import CooldownWatcher
from src_v2.eyes.hpmp_watcher import HpMpWatcher
from src_v2.eyes.xp_watcher import XpWatcher
from src_v2.eyes.udp_watcher import UdpWatcher


# ===== Mock Adapters =====

class MockGrabber:
    def __init__(self, frames):
        self._frames = list(frames)
        self.calls = 0

    def grab(self):
        self.calls += 1
        if not self._frames:
            return None
        return self._frames.pop(0)

    def is_available(self):
        return True


class MockYolo:
    def __init__(self, preds):
        self.preds = preds  # list of preds per call (pop from front)
        self.calls = 0

    def predict(self, frame):
        self.calls += 1
        if isinstance(self.preds, list) and self.preds and isinstance(self.preds[0], list):
            return self.preds.pop(0)
        return list(self.preds)

    def is_available(self):
        return True


class MockOcr:
    def __init__(self, sequence):
        self.sequence = list(sequence)
        self.calls = 0

    def read(self, frame):
        self.calls += 1
        if self.sequence:
            return self.sequence.pop(0)
        return (None, "")

    def is_available(self):
        return True


class MockCd:
    def __init__(self, results):
        self.results = list(results)

    def read(self, frame):
        if self.results:
            return self.results.pop(0)
        return {}

    def is_available(self):
        return True


class MockHpMp:
    def __init__(self, vals):
        self.vals = list(vals)

    def read(self, frame):
        if self.vals:
            return self.vals.pop(0)
        return (-1, -1, -1, -1, -1, -1)

    def is_available(self):
        return True


class MockXp:
    def __init__(self, vals):
        self.vals = list(vals)

    def read(self, frame):
        if self.vals:
            return self.vals.pop(0)
        return -1

    def is_available(self):
        return True


class MockUdp:
    def __init__(self, msgs):
        self.msgs = list(msgs)

    def recv(self):
        if self.msgs:
            return self.msgs.pop(0)
        return None

    def is_available(self):
        return True


# ===== Tests =====

def test_capture_writes_frame():
    store = SnapshotStore()
    bus = EventBus()
    grabber = MockGrabber(["frame1"])
    w = CaptureWatcher(store, bus, grabber=grabber, poll_sec=0.001)
    w._tick()
    assert store.read_field("last_frame") == "frame1"
    assert store.read_field("last_frame_ts") > 0


def test_capture_handles_none_frame():
    store = SnapshotStore()
    bus = EventBus()
    grabber = MockGrabber([None])
    w = CaptureWatcher(store, bus, grabber=grabber, poll_sec=0.001)
    w._tick()
    assert store.read_field("last_frame") is None


def test_yolo_detects_red_tab():
    store = SnapshotStore()
    bus = EventBus()
    store.update(last_frame="dummy")

    seen_red = []
    bus.subscribe("eye.red_tab", lambda e: seen_red.append(e.payload))

    yolo = MockYolo([("red_tab", 100, 200, 140, 240, 0.95)])
    w = YoloWatcher(store, bus, yolo=yolo, poll_sec=0.001)
    w._tick()

    assert store.read_field("red_tab_present") is True
    assert store.read_field("red_tab_pos") == (120, 220)
    assert len(seen_red) == 1


def test_yolo_filters_low_conf():
    store = SnapshotStore()
    bus = EventBus()
    store.update(last_frame="dummy")
    yolo = MockYolo([("red_tab", 0, 0, 10, 10, 0.20)])
    w = YoloWatcher(store, bus, yolo=yolo, conf_threshold=0.45, poll_sec=0.001)
    w._tick()
    assert store.read_field("red_tab_present") is False


def test_yolo_picks_highest_conf():
    store = SnapshotStore()
    bus = EventBus()
    store.update(last_frame="dummy")
    yolo = MockYolo([
        ("red_tab", 0, 0, 10, 10, 0.50),
        ("red_tab", 100, 100, 110, 110, 0.95),
        ("red_tab", 50, 50, 60, 60, 0.70),
    ])
    w = YoloWatcher(store, bus, yolo=yolo, poll_sec=0.001)
    w._tick()
    det = store.read_field("red_tab_detection")
    assert det.conf == 0.95
    assert det.center == (105, 105)


def test_ocr_publishes_coord():
    store = SnapshotStore()
    bus = EventBus()
    store.update(last_frame="dummy")
    seen = []
    bus.subscribe("eye.coord", lambda e: seen.append(e.payload))

    ocr = MockOcr([((100, 200), "")])
    w = OcrWatcher(store, bus, ocr=ocr, poll_sec=0.001)
    w._tick()
    assert store.read_field("healer_coord") == (100, 200)
    assert seen == [(100, 200)]


def test_ocr_dedup_coord():
    store = SnapshotStore()
    bus = EventBus()
    store.update(last_frame="dummy")
    seen = []
    bus.subscribe("eye.coord", lambda e: seen.append(e.payload))

    ocr = MockOcr([((1, 1), ""), ((1, 1), ""), ((2, 2), "")])
    w = OcrWatcher(store, bus, ocr=ocr, poll_sec=0.001)
    w._tick(); w._tick(); w._tick()
    assert seen == [(1, 1), (2, 2)]


def test_ocr_map_change_increments_seq():
    store = SnapshotStore()
    bus = EventBus()
    store.update(last_frame="dummy")
    ocr = MockOcr([(None, "map_a"), (None, "map_b")])
    w = OcrWatcher(store, bus, ocr=ocr, poll_sec=0.001)
    w._tick()
    assert store.read_field("healer_map") == "map_a"
    assert store.read_field("healer_map_seq") == 1
    w._tick()
    assert store.read_field("healer_map") == "map_b"
    assert store.read_field("healer_map_seq") == 2


def test_cooldown_updates_fields():
    store = SnapshotStore()
    bus = EventBus()
    store.update(last_frame="dummy")
    seen = []
    bus.subscribe("eye.cooldown", lambda e: seen.append(e.payload))

    cd = MockCd([{"parlyuk": 5, "baekho": 0}])
    w = CooldownWatcher(store, bus, adapter=cd, slot="cd", poll_sec=0.001)
    w._tick()
    assert store.read_field("cd_parlyuk") == 5
    assert store.read_field("cd_baekho") == 0
    assert len(seen) == 1


def test_buff_slot_updates():
    store = SnapshotStore()
    bus = EventBus()
    store.update(last_frame="dummy")
    cd = MockCd([{"parlyuk_active": True}])
    w = CooldownWatcher(store, bus, adapter=cd, slot="buff", poll_sec=0.001)
    w._tick()
    assert store.read_field("buff_parlyuk_active") is True


def test_hpmp_publishes_on_change():
    store = SnapshotStore()
    bus = EventBus()
    store.update(last_frame="dummy")
    seen_hp = []
    seen_mp = []
    bus.subscribe("eye.hp", lambda e: seen_hp.append(e.payload))
    bus.subscribe("eye.mp", lambda e: seen_mp.append(e.payload))

    hpmp = MockHpMp([
        (80, 70, 800, 700, 1000, 1000),
        (80, 70, 800, 700, 1000, 1000),  # no change
        (50, 70, 500, 700, 1000, 1000),  # hp change only
    ])
    w = HpMpWatcher(store, bus, adapter=hpmp, poll_sec=0.001)
    w._tick(); w._tick(); w._tick()
    assert seen_hp == [80, 50]
    assert seen_mp == [70]


def test_xp_dedup():
    store = SnapshotStore()
    bus = EventBus()
    store.update(last_frame="dummy")
    seen = []
    bus.subscribe("eye.xp", lambda e: seen.append(e.payload))
    xp = MockXp([100, 100, 200])
    w = XpWatcher(store, bus, adapter=xp, poll_sec=0.001)
    w._tick(); w._tick(); w._tick()
    assert seen == [100, 200]


def test_udp_drains_and_publishes_latest():
    store = SnapshotStore()
    bus = EventBus()
    seen = []
    bus.subscribe("eye.attacker_state", lambda e: seen.append(e.payload))

    msgs = [
        AttackerState(coord=(10, 10), coord_valid=True, map_name="m1", last_dir="L"),
        AttackerState(coord=(11, 12), coord_valid=True, map_name="m1", last_dir="R"),
        AttackerState(coord=(13, 14), coord_valid=True, map_name="m1", last_dir="R"),
    ]
    udp = MockUdp(msgs)
    w = UdpWatcher(store, bus, adapter=udp, poll_sec=0.001)
    w._tick()
    # only LAST should have been published (drained queue)
    assert len(seen) == 1
    assert seen[0].coord == (13, 14)
    assert store.read_field("attacker_coord") == (13, 14)


def test_udp_map_change_increments_seq():
    store = SnapshotStore()
    bus = EventBus()
    udp = MockUdp([
        AttackerState(coord=(1, 1), coord_valid=True, map_name="map_a"),
    ])
    w = UdpWatcher(store, bus, adapter=udp, poll_sec=0.001)
    w._tick()
    assert store.read_field("attacker_map_seq") == 1

    udp2 = MockUdp([
        AttackerState(coord=(2, 2), coord_valid=True, map_name="map_b"),
    ])
    w.adapter = udp2
    w._tick()
    assert store.read_field("attacker_map_seq") == 2


def test_watcher_thread_runs_and_stops():
    store = SnapshotStore()
    bus = EventBus()
    store.update(last_frame="dummy")
    hpmp = MockHpMp([(50, 60, 500, 600, 1000, 1000)] * 100)
    w = HpMpWatcher(store, bus, adapter=hpmp, poll_sec=0.005)
    w.start()
    time.sleep(0.05)
    w.stop()
    assert not w.is_alive()
    assert w._tick_count > 0
    assert store.read_field("hp") == 50


def test_watcher_handles_adapter_exception():
    """Adapter raising must not crash watcher thread."""
    class BadAdapter:
        def read(self, frame):
            raise RuntimeError("kaboom")
        def is_available(self):
            return True

    store = SnapshotStore()
    bus = EventBus()
    store.update(last_frame="dummy")
    w = HpMpWatcher(store, bus, adapter=BadAdapter(), poll_sec=0.001)
    # _tick should swallow if BaseWatcher run wraps it; here _tick re-raises
    # but actual run() loop catches.
    with pytest.raises(RuntimeError):
        w._tick()
    # Now via thread:
    w2 = HpMpWatcher(store, bus, adapter=BadAdapter(), poll_sec=0.005)
    w2.start()
    time.sleep(0.03)
    w2.stop()
    assert w2._err_count > 0
    assert not w2.is_alive()
