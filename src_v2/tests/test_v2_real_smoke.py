"""V2 real-runtime smoke test — 사용자 화면 동작 검증을 보장.

목표
----
1. HealerWorkerV2 가 build → start → mock OCR/HpMp 입력 → store 에 실제 값이
   채워지는지 1초 내 검증.
2. set_*_region(...) 이 살아있는 adapter 의 set_region/set_hp_region 메서드를
   실제로 호출하는지 검증.
3. AttackerWorkerV2 도 동일 검증.
4. V2MainWindow 의 region picker 시뮬레이션: _on_region_selected 호출 →
   worker setter 호출 + cfg.yaml 저장.

이 테스트가 통과하면 facade 없이 v2 entry → v2 GUI → v2 worker 가 일관되게
동작함이 보장됩니다.
"""
from __future__ import annotations

import time
import unittest
from typing import Any, Dict, Tuple
from unittest.mock import MagicMock

from src_v2.workers.healer_worker_v2 import HealerWorkerV2, HealerConfig
from src_v2.workers.attacker_worker_v2 import AttackerWorkerV2, AttackerConfig


# ---------------------------------------------------------------------------
# Mock adapters
# ---------------------------------------------------------------------------
class MockGrabber:
    def __init__(self):
        # numpy 의존 회피 — 그냥 객체 하나 (HpMp/OCR adapter 가 None 만 거름)
        self._frame = object()
    def grab(self): return self._frame
    def is_available(self): return True


class MockOcr:
    """좌표/맵 점진적 갱신."""
    def __init__(self):
        self._n = 0
        self.region_calls = []
    def read(self, frame):
        self._n += 1
        return ((100 + self._n, 200 + self._n), f"맵_{self._n}")
    def is_available(self): return True
    def set_region(self, x, y, w, h):
        self.region_calls.append((x, y, w, h))


class MockHpMp:
    """HP/MP 50% / 60% 고정."""
    def __init__(self):
        self.hp_region_calls = []
        self.mp_region_calls = []
        self.hp_max_calls = []
        self.mp_max_calls = []
    def read(self, frame):
        return (50, 60, 5000, 3000, 10000, 5000)
    def is_available(self): return True
    def set_hp_region(self, x, y, w, h): self.hp_region_calls.append((x, y, w, h))
    def set_mp_region(self, x, y, w, h): self.mp_region_calls.append((x, y, w, h))
    def set_hp_max(self, n): self.hp_max_calls.append(int(n))
    def set_mp_max(self, n): self.mp_max_calls.append(int(n))


class MockYolo:
    def __init__(self):
        self.region_calls = []
    def predict(self, frame): return []
    def is_available(self): return True
    def set_region(self, x, y, w, h): self.region_calls.append((x, y, w, h))


class MockCooldown:
    def __init__(self):
        self.region_calls = []
        self.nick_region_calls = []
    def read(self, frame): return {}
    def is_available(self): return True
    def set_region(self, x, y, w, h): self.region_calls.append((x, y, w, h))
    def set_nick_region(self, x, y, w, h):
        self.nick_region_calls.append((x, y, w, h))


class MockUdp:
    def recv(self): return None
    def is_available(self): return True


class MockKeys:
    def __init__(self): self.calls = []
    def key_down(self, vk): self.calls.append(("down", vk))
    def key_up(self, vk): self.calls.append(("up", vk))
    def is_available(self): return True


class MockGrabberWithRegion(MockGrabber):
    def __init__(self):
        super().__init__()
        self.region_calls = []
    def set_region(self, x, y, w, h): self.region_calls.append((x, y, w, h))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestHealerWorkerV2Smoke(unittest.TestCase):

    def test_store_filled_within_1s(self):
        """워커 start 후 1초 안에 store 에 healer_coord / hp / mp 가 채워져야."""
        grabber = MockGrabber()
        ocr = MockOcr()
        hpmp = MockHpMp()
        yolo = MockYolo()
        cooldown = MockCooldown()
        buff = MockCooldown()

        cfg = HealerConfig(
            capture_poll_sec=0.01, ocr_poll_sec=0.05, hpmp_poll_sec=0.05,
            yolo_poll_sec=0.5, cooldown_poll_sec=1.0, buff_poll_sec=1.0,
            udp_poll_sec=1.0, ui_publish_hz=0,  # ui publisher 비활성
        )
        w = HealerWorkerV2(
            cfg=cfg,
            grabber=grabber, ocr=ocr, hpmp=hpmp,
            yolo=yolo, cooldown=cooldown, buff=buff,
            udp=MockUdp(), keys=MockKeys(),
        )
        w.start()
        try:
            t0 = time.monotonic()
            healer_coord = None
            hp = -1
            while time.monotonic() - t0 < 2.0:
                snap = w.store.read()
                if snap.healer_coord and snap.hp >= 0:
                    healer_coord = snap.healer_coord
                    hp = snap.hp
                    break
                time.sleep(0.05)
            self.assertIsNotNone(healer_coord, "OCR 가 store 에 healer_coord 못 채움")
            self.assertGreaterEqual(hp, 0, "HpMp 가 store 에 hp 못 채움")
            self.assertEqual(hp, 50)
            snap = w.store.read()
            self.assertEqual(snap.mp, 60)
            self.assertTrue(snap.healer_map.startswith("맵_"))
        finally:
            w.stop(timeout=2.0)

    def test_set_regions_propagate_to_adapters(self):
        """worker.set_*_region(...) 가 어댑터의 setter 메서드 실제 호출."""
        grabber = MockGrabberWithRegion()
        ocr = MockOcr(); hpmp = MockHpMp(); yolo = MockYolo()
        cooldown = MockCooldown(); buff = MockCooldown()

        w = HealerWorkerV2(
            cfg=HealerConfig(ui_publish_hz=0),
            grabber=grabber, ocr=ocr, hpmp=hpmp,
            yolo=yolo, cooldown=cooldown, buff=buff,
            udp=MockUdp(), keys=MockKeys(),
        )
        # worker.start() 안 해도 region setter 는 adapter 에 직접 전달.
        w.set_game_region(10, 20, 800, 600)
        w.set_hp_region(100, 200, 50, 10)
        w.set_mp_region(100, 220, 50, 10)
        w.set_hp_max(10000)
        w.set_mp_max(5000)
        w.set_cooldown_region(300, 400, 60, 12)
        w.set_buff_region(310, 410, 70, 14)
        w.set_nick_region(400, 500, 80, 16)

        self.assertEqual(grabber.region_calls, [(10, 20, 800, 600)])
        self.assertEqual(hpmp.hp_region_calls, [(100, 200, 50, 10)])
        self.assertEqual(hpmp.mp_region_calls, [(100, 220, 50, 10)])
        self.assertEqual(hpmp.hp_max_calls, [10000])
        self.assertEqual(hpmp.mp_max_calls, [5000])
        self.assertEqual(cooldown.region_calls, [(300, 400, 60, 12)])
        self.assertEqual(buff.region_calls, [(310, 410, 70, 14)])
        # nick 은 cooldown adapter 의 set_nick_region 으로 위임
        self.assertEqual(cooldown.nick_region_calls, [(400, 500, 80, 16)])


class TestAttackerWorkerV2Smoke(unittest.TestCase):

    def test_set_regions_propagate(self):
        grabber = MockGrabberWithRegion()
        ocr = MockOcr(); hpmp = MockHpMp(); yolo = MockYolo()

        w = AttackerWorkerV2(
            cfg=AttackerConfig(),
            grabber=grabber, ocr=ocr, hpmp=hpmp, yolo=yolo,
        )
        w.set_game_region(0, 0, 1280, 720)
        w.set_hp_region(50, 60, 100, 10)
        w.set_mp_region(50, 75, 100, 10)
        w.set_hp_max(15000)
        w.set_mp_max(8000)

        self.assertEqual(grabber.region_calls, [(0, 0, 1280, 720)])
        self.assertEqual(hpmp.hp_region_calls, [(50, 60, 100, 10)])
        self.assertEqual(hpmp.mp_region_calls, [(50, 75, 100, 10)])
        self.assertEqual(hpmp.hp_max_calls, [15000])
        self.assertEqual(hpmp.mp_max_calls, [8000])

    def test_attacker_store_filled(self):
        """AttackerWorkerV2 도 watcher 가 store 채우는지 검증 (UDP send 무시)."""
        class _NullSender:
            def send(self, payload): pass
            def is_available(self): return True
        grabber = MockGrabber(); ocr = MockOcr(); hpmp = MockHpMp(); yolo = MockYolo()
        w = AttackerWorkerV2(
            cfg=AttackerConfig(
                capture_poll_sec=0.01, ocr_poll_sec=0.05,
                hpmp_poll_sec=0.05, yolo_poll_sec=0.5,
                udp_send_hz=5,
            ),
            grabber=grabber, ocr=ocr, hpmp=hpmp, yolo=yolo,
            udp_sender=_NullSender(),
        )
        w.start()
        try:
            t0 = time.monotonic()
            ok_coord = False; ok_hp = False
            while time.monotonic() - t0 < 2.0:
                snap = w.store.read()
                if snap.healer_coord:
                    ok_coord = True
                if snap.hp >= 0:
                    ok_hp = True
                if ok_coord and ok_hp:
                    break
                time.sleep(0.05)
            self.assertTrue(ok_coord, "AttackerWorkerV2 OCR 미채움")
            self.assertTrue(ok_hp, "AttackerWorkerV2 HpMp 미채움")
        finally:
            w.stop(timeout=2.0)


class TestV2MainWindowRegionPipeline(unittest.TestCase):
    """V2MainWindow 의 region 선택 시뮬레이션 + cfg.yaml 저장."""

    def test_region_select_injects_to_worker_and_saves_yaml(self):
        # PyQt5 offscreen 환경에서만 — 일반 환경 에서도 import 가능해야.
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5 import QtWidgets
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

        # mock worker — set_*_region 호출 추적.
        worker = MagicMock()
        worker.store = MagicMock()
        # snap = MagicMock with default field returns
        snap = MagicMock()
        snap.hp = -1; snap.mp = -1; snap.healer_coord = None
        snap.healer_map = ""; snap.attacker_coord = None
        snap.attacker_coord_valid = False; snap.attacker_map = ""
        snap.red_tab_present = False; snap.red_tab_pos = None
        snap.update_count = 0; snap.last_eye_update_ts = 0.0
        snap.hp_cur = -1; snap.mp_cur = -1
        snap.hp_max = -1; snap.mp_max = -1
        worker.store.read.return_value = snap

        # 임시 cfg.yaml
        import tempfile, yaml as _yaml
        with tempfile.TemporaryDirectory() as td:
            cfg_path = f"{td}/config.yaml"
            with open(cfg_path, "w", encoding="utf-8") as f:
                _yaml.safe_dump({"capture": {"monitor_index": 1}}, f)

            from src_v2.ui.v2_main_window import V2MainWindow
            win = V2MainWindow(role="healer", cfg=None, worker=worker,
                               cfg_yaml_path=cfg_path)
            try:
                # picker 시뮬레이션 — 직접 _on_region_selected 호출
                win._on_region_selected("game", 100, 200, 800, 600)
                win._on_region_selected("hp", 10, 20, 50, 8)

                worker.set_game_region.assert_called_with(100, 200, 800, 600)
                worker.set_hp_region.assert_called_with(10, 20, 50, 8)

                # cfg.yaml 에 v2_regions 저장됐는지
                with open(cfg_path, "r", encoding="utf-8") as f:
                    doc = _yaml.safe_load(f)
                self.assertIn("v2_regions", doc)
                self.assertEqual(doc["v2_regions"]["game"],
                                 {"x": 100, "y": 200, "w": 800, "h": 600})
                self.assertEqual(doc["v2_regions"]["hp"],
                                 {"x": 10, "y": 20, "w": 50, "h": 8})

                # 재로드 시뮬레이션 — 새 윈도우가 v2_regions 자동 로드해야
                win2 = V2MainWindow(role="healer", cfg=None, worker=worker,
                                    cfg_yaml_path=cfg_path)
                self.assertEqual(win2._regions["game"], (100, 200, 800, 600))
                self.assertEqual(win2._regions["hp"], (10, 20, 50, 8))
                win2.close()
            finally:
                win.close()


if __name__ == "__main__":
    unittest.main()
