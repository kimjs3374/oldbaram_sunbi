"""Adapter unit tests — verify normalization logic with fake src objects."""
import pytest

from src_v2.adapters.grabber_adapter import SrcGrabberAdapter
from src_v2.adapters.yolo_adapter import SrcYoloAdapter
from src_v2.adapters.ocr_adapter import SrcOcrAdapter
from src_v2.adapters.hpmp_adapter import SrcHpMpAdapter
from src_v2.adapters.cooldown_adapter import SrcCooldownAdapter
from src_v2.adapters.udp_adapter import SrcUdpAdapter
from src_v2.adapters.keys_adapter import SrcKeysAdapter
from src_v2.core.types import AttackerState


def test_grabber_adapter_calls_latest():
    class FakeG:
        def latest(self): return "frame"
    a = SrcGrabberAdapter(FakeG())
    assert a.grab() == "frame"
    assert a.is_available() is True


def test_grabber_adapter_none():
    a = SrcGrabberAdapter(None)
    assert a.grab() is None
    assert a.is_available() is False


def test_yolo_adapter_normalizes_dict():
    class FakeY:
        def predict(self, frame):
            return [{"cls": "red_tab", "bbox": [10, 20, 30, 40], "conf": 0.9}]
    a = SrcYoloAdapter(FakeY())
    out = a.predict("frame")
    assert out == [("red_tab", 10, 20, 30, 40, 0.9)]


def test_yolo_adapter_normalizes_tuple():
    class FakeY:
        def predict(self, frame):
            return [("red_tab", 10, 20, 30, 40, 0.9)]
    a = SrcYoloAdapter(FakeY())
    assert a.predict("f") == [("red_tab", 10, 20, 30, 40, 0.9)]


def test_yolo_adapter_handles_exception():
    class FakeY:
        def predict(self, frame):
            raise RuntimeError("boom")
    a = SrcYoloAdapter(FakeY())
    assert a.predict("f") == []


def test_ocr_adapter_returns_coord_and_map():
    class CoordOcr:
        def read(self, frame): return (100, 200)
    class MapOcr:
        def read(self, frame): return "map_x"
    a = SrcOcrAdapter(CoordOcr(), MapOcr())
    coord, name = a.read("frame")
    assert coord == (100, 200)
    assert name == "map_x"


def test_ocr_adapter_missing_components():
    a = SrcOcrAdapter(None, None)
    assert a.read("frame") == (None, "")
    assert a.is_available() is False


def test_hpmp_adapter_dict_form():
    class FakeH:
        def read(self, f):
            return {"hp_pct": 80, "mp_pct": 70, "hp_cur": 800, "mp_cur": 700,
                    "hp_max": 1000, "mp_max": 1000}
    a = SrcHpMpAdapter(FakeH())
    assert a.read("f") == (80, 70, 800, 700, 1000, 1000)


def test_hpmp_adapter_tuple6():
    class FakeH:
        def read(self, f): return (50, 60, 500, 600, 1000, 1000)
    a = SrcHpMpAdapter(FakeH())
    assert a.read("f") == (50, 60, 500, 600, 1000, 1000)


def test_hpmp_adapter_tuple2():
    class FakeH:
        def read(self, f): return (50, 60)
    a = SrcHpMpAdapter(FakeH())
    assert a.read("f") == (50, 60, -1, -1, -1, -1)


def test_cooldown_adapter():
    class FakeC:
        def read(self, f): return {"parlyuk": 5, "baekho": 0}
    a = SrcCooldownAdapter(FakeC())
    assert a.read("f") == {"parlyuk": 5, "baekho": 0}


def test_udp_adapter_passes_attackerstate():
    s = AttackerState(coord=(1, 1), map_name="m", coord_valid=True)
    class FakeU:
        def __init__(self): self.q = [s, None]
        def recv(self):
            return self.q.pop(0) if self.q else None
    a = SrcUdpAdapter(FakeU())
    assert a.recv() is s
    assert a.recv() is None


def test_udp_adapter_dict_normalize():
    class FakeU:
        def recv(self):
            return {"coord": [10, 20], "map": "abc", "hp": 80, "honma_sec": 5}
    a = SrcUdpAdapter(FakeU())
    s = a.recv()
    assert isinstance(s, AttackerState)
    assert s.coord == (10, 20)
    assert s.map_name == "abc"
    assert s.hp == 80
    assert s.honma_sec == 5


def test_keys_adapter_calls_press_release():
    events = []
    class FakeK:
        def key_down(self, vk): events.append(("d", vk))
        def key_up(self, vk): events.append(("u", vk))
    a = SrcKeysAdapter(FakeK())
    a.key_down(0x10)
    a.key_up(0x10)
    assert events == [("d", 0x10), ("u", 0x10)]


def test_keys_adapter_tap_fallback_to_down_up():
    events = []
    class FakeK:
        def down(self, vk): events.append(("d", vk))
        def up(self, vk): events.append(("u", vk))
    a = SrcKeysAdapter(FakeK())
    a.key_tap(0x20, hold_ms=1)
    assert ("d", 0x20) in events
    assert ("u", 0x20) in events
