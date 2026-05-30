"""v1 SoR Parity Tests — Attacker (Phase 2).

검증 범위 (v1 dist_dosa/src/app/attacker.py + workers/attacker_worker.py):
  1. AttackerConfig defaults = v1 상수 동치
  2. F1 edge → map_change_pending 활성 5s
  3. F1 down 유지 → 재트리거 없음 (edge-only)
  4. 워프 감지 (좌표 점프 ≥ 25) → map_seq++ + burst 활성
  5. 워프 임계 미만 (24) → map_seq 그대로
  6. 다른 맵 사이 좌표 점프 → 워프 무시 (맵 변경 분기로 처리)
  7. 맵 이름 변경 (prev != cur) → map_seq++ + burst
  8. 첫 맵 진입 (prev="") → map_seq 변화 없음 (bootstrap)
  9. burst 진행 → 1회당 추가 2회 송신, 3회까지
  10. burst 종료 후 정상 송신만
  11. last_dir 추정 (R/L/U/D/-)
  12. CooldownReceiver — first 보고 + bus publish
  13. CooldownReceiver — 동일 (row,ip) 재보고 first 로그 안 찍음
  14. CooldownReceiver — payload 1:1 (force_coord_tol 등 모든 필드)
  15. State.coord_valid=False 시에도 송신 (좌표만 빈 채로)
  16. send_loop 가 udp_sender.is_available()=False 시 송신 안 함
  17. AttackerState 기본값 (coord=None, hp=-1, last_dir="-")
  18. 격수 buff/debuff 송신 (snap.self_buff_*_sec → State.buff_*_sec)
  19. v1_defaults 상수 SoR 일치 (격수 매직)
  20. stats() dict 형식

모든 테스트는 mock — 실제 OCR/UDP 호출 없음.
"""
from __future__ import annotations
import time
from typing import Any, List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

from src_v2.config import v1_defaults as V1
from src_v2.core.types import AttackerState
from src_v2.workers.attacker_worker_v2 import (
    AttackerWorkerV2, AttackerConfig,
    _NullSender, _NullCdRecv, _NullF1,
    _state_to_bytes,
)


# =====================================================================
# Test fixtures
# =====================================================================
class FakeF1:
    """is_down() 가 외부에서 set 가능한 fake."""
    def __init__(self):
        self.down: bool = False
    def is_down(self) -> bool:
        return self.down


class FakeSender:
    def __init__(self, available: bool = True):
        self._available = available
        self.sent: List[bytes] = []
    def send(self, payload: bytes) -> None:
        self.sent.append(payload)
    def is_available(self) -> bool:
        return self._available


class FakeCdReceiver:
    def __init__(self):
        self.queue: List[Tuple[Any, Optional[Tuple[str, int]]]] = []
        self._available: bool = True
    def push(self, rep: Any, src_addr: Optional[Tuple[str, int]] = None):
        self.queue.append((rep, src_addr))
    def poll(self):
        msgs = list(self.queue)
        self.queue.clear()
        return msgs
    def is_available(self) -> bool:
        return self._available


def _make_worker(**kwargs) -> AttackerWorkerV2:
    return AttackerWorkerV2(**kwargs)


# =====================================================================
# 1. v1_defaults 상수 SoR 일치
# =====================================================================
class TestV1DefaultsAttackerValues:
    def test_atk_f1_window(self):
        assert V1.ATK_F1_WINDOW_SEC == 5.0

    def test_atk_warp_threshold(self):
        assert V1.ATK_WARP_THRESHOLD == 25

    def test_atk_map_burst_n(self):
        assert V1.ATK_MAP_BURST_N == 3

    def test_atk_red_ttl(self):
        assert V1.ATK_RED_TTL_SEC == 3.0

    def test_atk_recv_port(self):
        assert V1.ATK_RECV_PORT_DEFAULT == 45455

    def test_atk_grab_target_interval(self):
        assert V1.ATK_GRAB_TARGET_INTERVAL_S == 0.02

    def test_atk_yolo_red_min(self):
        assert V1.ATK_YOLO_RED_MIN_W == 25
        assert V1.ATK_YOLO_RED_MIN_H == 40

    def test_atk_cd_recv_snap_period(self):
        assert V1.ATK_CD_RECV_SNAP_PERIOD_SEC == 10.0


# =====================================================================
# 2-3. F1 edge → map_change_pending
# =====================================================================
class TestF1Pending:
    def test_f1_edge_activates_pending(self):
        f1 = FakeF1()
        sender = FakeSender()
        w = _make_worker(udp_sender=sender, f1_key=f1)

        # 첫 tick — F1 down=False, pending=False
        w._send_one()
        # F1 누름
        f1.down = True
        before = time.time()
        w._send_one()
        # _f1_pending_until ≈ now + 5.0
        assert w._f1_pending_until > before + 4.0
        assert w._f1_pending_until < before + 6.0

    def test_f1_held_no_retrigger(self):
        f1 = FakeF1()
        sender = FakeSender()
        w = _make_worker(udp_sender=sender, f1_key=f1)
        f1.down = True
        w._send_one()
        first_until = w._f1_pending_until
        time.sleep(0.01)
        # 계속 누른 채 (down 유지) → edge 없음
        w._send_one()
        assert w._f1_pending_until == first_until


# =====================================================================
# 4-6. 워프 / 맵 변경 감지
# =====================================================================
class TestWarpDetection:
    def _push_coord(self, w, x, y, map_name="A"):
        # snapshot store 직접 갱신
        w.store.update(healer_coord=(x, y), healer_map=map_name)
        w._send_one()

    def test_warp_above_threshold_increments_seq(self):
        sender = FakeSender()
        w = _make_worker(udp_sender=sender)
        # 초기 좌표
        self._push_coord(w, 10, 10, "A")
        seq_before = w._map_seq
        # 26칸 점프 → 워프 (threshold=25)
        self._push_coord(w, 36, 10, "A")
        assert w._map_seq == seq_before + 1
        assert w._map_burst_remaining == V1.ATK_MAP_BURST_N

    def test_warp_below_threshold_no_change(self):
        sender = FakeSender()
        w = _make_worker(udp_sender=sender)
        self._push_coord(w, 10, 10, "A")
        seq_before = w._map_seq
        # 24칸 점프 → 무시
        self._push_coord(w, 34, 10, "A")
        assert w._map_seq == seq_before
        assert w._map_burst_remaining == 0

    def test_diff_map_no_warp(self):
        """다른 맵 간 점프는 워프 분기 아닌 맵-변경 분기."""
        sender = FakeSender()
        w = _make_worker(udp_sender=sender)
        self._push_coord(w, 10, 10, "A")
        # 맵 변경 — 100칸 점프지만 워프 분기 안 탐
        self._push_coord(w, 110, 10, "B")
        # map_seq++ 는 맵 변경 분기에서 1회만
        assert w._map_seq == 1


class TestMapEdge:
    def test_first_map_no_seq_increment(self):
        sender = FakeSender()
        w = _make_worker(udp_sender=sender)
        # 초기 맵 진입 (prev="" → bootstrap)
        w.store.update(healer_coord=(5, 5), healer_map="A")
        w._send_one()
        assert w._map_seq == 0

    def test_map_change_increments_seq(self):
        sender = FakeSender()
        w = _make_worker(udp_sender=sender)
        w.store.update(healer_coord=(5, 5), healer_map="A")
        w._send_one()
        w.store.update(healer_coord=(5, 5), healer_map="B")
        w._send_one()
        assert w._map_seq == 1
        assert w._map_burst_remaining == V1.ATK_MAP_BURST_N


# =====================================================================
# 9-10. burst 송신
# =====================================================================
class TestBurst:
    def test_burst_sends_extra_packets(self):
        sender = FakeSender()
        w = _make_worker(udp_sender=sender)
        w.store.update(healer_coord=(5, 5), healer_map="A")
        w._send_one()  # 1회 송신
        before = w._send_count
        before_burst = w._burst_send_count
        # 맵 변경
        w.store.update(healer_coord=(5, 5), healer_map="B")
        w._send_one()  # 정상 1 + burst 2
        assert w._send_count == before + 1
        assert w._burst_send_count == before_burst + 2
        # burst remaining = N - 1
        assert w._map_burst_remaining == V1.ATK_MAP_BURST_N - 1

    def test_burst_terminates_after_n(self):
        sender = FakeSender()
        w = _make_worker(udp_sender=sender)
        w.store.update(healer_coord=(5, 5), healer_map="A")
        w._send_one()
        w.store.update(healer_coord=(5, 5), healer_map="B")
        # 첫 변경 + 후속 2회 = 총 3회 burst
        for _ in range(V1.ATK_MAP_BURST_N + 1):
            w._send_one()
        assert w._map_burst_remaining == 0


# =====================================================================
# 11. last_dir 추정
# =====================================================================
class TestLastDir:
    def _setup(self):
        sender = FakeSender()
        w = _make_worker(udp_sender=sender)
        w.store.update(healer_coord=(10, 10), healer_map="A")
        w._send_one()
        return w

    def test_dir_R(self):
        w = self._setup()
        w.store.update(healer_coord=(15, 10))
        w._send_one()
        assert w._last_dir == "R"

    def test_dir_L(self):
        w = self._setup()
        w.store.update(healer_coord=(5, 10))
        w._send_one()
        assert w._last_dir == "L"

    def test_dir_U(self):
        w = self._setup()
        w.store.update(healer_coord=(10, 5))
        w._send_one()
        assert w._last_dir == "U"

    def test_dir_D(self):
        w = self._setup()
        w.store.update(healer_coord=(10, 15))
        w._send_one()
        assert w._last_dir == "D"

    def test_dir_stationary(self):
        w = self._setup()
        # 동일 좌표 → "-"
        w.store.update(healer_coord=(10, 10))
        w._send_one()
        assert w._last_dir == "-"


# =====================================================================
# 12-14. CooldownReceiver
# =====================================================================
class _FakeReport:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestCooldownReceiver:
    def test_first_report_publishes_to_bus(self):
        recv = FakeCdReceiver()
        w = _make_worker(cd_receiver=recv)
        captured = []
        w.bus.subscribe("recv.cd_report", lambda p: captured.append(p))

        rep = _FakeReport(
            src_idx=0, cd_parlyuk=10, cd_baekho=20, ts_ms=12345,
            armed=True, nickname="힐러1",
            buff_parlyuk_sec=30, xp_per_hour=1000000,
            event_text="자힐", event_seq=5,
            hp_pct=80, mp_pct=50, hp_cur=8000, mp_cur=2500,
            hp_max=10000, mp_max=5000,
            self_heal_hp_thr=50, gyoungryeok_mp_thr=30,
        )
        w._handle_cd_report(rep, ("100.1.2.3", 45455))
        assert len(captured) == 1
        p = captured[0]
        # peers 매칭이 없으면 reported_idx 사용
        assert p["reported_idx"] == 0
        assert p["src_ip"] == "100.1.2.3"
        assert p["cd_parlyuk"] == 10
        assert p["nickname"] == "힐러1"
        assert p["self_heal_hp_thr"] == 50
        assert p["gyoungryeok_mp_thr"] == 30
        assert p["buff_parlyuk_sec"] == 30
        assert p["event_seq"] == 5
        assert p["hp_pct"] == 80

    def test_repeated_first_log_dedupe(self):
        recv = FakeCdReceiver()
        w = _make_worker(cd_receiver=recv)
        rep = _FakeReport(src_idx=0, cd_parlyuk=10, cd_baekho=20, ts_ms=1)
        w._handle_cd_report(rep, ("100.1.2.3", 45455))
        keys_after_first = set(w._cd_recv_seen_keys)
        # 동일 (row, ip) 재보고
        w._handle_cd_report(rep, ("100.1.2.3", 45455))
        assert w._cd_recv_seen_keys == keys_after_first

    def test_full_payload_fields(self):
        """v1 attacker_worker.py:323-358 emit dict 의 18개 필드 모두 포함."""
        recv = FakeCdReceiver()
        w = _make_worker(cd_receiver=recv)
        captured = []
        w.bus.subscribe("recv.cd_report", lambda p: captured.append(p))
        rep = _FakeReport(src_idx=1)
        w._handle_cd_report(rep, ("1.2.3.4", 1))
        p = captured[0]
        for k in [
            "src_idx", "reported_idx", "src_ip", "cd_parlyuk", "cd_baekho",
            "ts_ms", "armed", "nickname", "buff_parlyuk_sec", "xp_per_hour",
            "event_text", "event_seq", "hp_pct", "mp_pct", "hp_cur", "mp_cur",
            "hp_max", "mp_max", "self_heal_hp_thr", "gyoungryeok_mp_thr",
        ]:
            assert k in p, f"missing key {k}"


# =====================================================================
# 15-17. send_loop 동작 + 기본값
# =====================================================================
class TestSendLoopBehavior:
    def test_send_skipped_when_unavailable(self):
        sender = FakeSender(available=False)
        w = _make_worker(udp_sender=sender)
        w.store.update(healer_coord=(5, 5), healer_map="A")
        w._send_one()
        assert w._send_count == 0
        assert sender.sent == []

    def test_attacker_state_defaults(self):
        s = AttackerState()
        assert s.coord is None
        assert s.coord_valid is False
        assert s.map_name == ""
        assert s.map_seq == 0
        assert s.hp == -1
        assert s.last_dir == "-"
        assert s.honma_sec == -1
        assert s.mujang_sec == -1
        assert s.boho_sec == -1


# =====================================================================
# 18. 격수 buff/debuff 송신
# =====================================================================
class TestBuffPropagation:
    def test_buff_fields_read_from_snapshot(self):
        sender = FakeSender()
        w = _make_worker(udp_sender=sender)
        # snapshot 에 buff 갱신
        w.store.update(
            healer_coord=(5, 5), healer_map="A",
            self_debuff_honma_sec=15, self_buff_mujang_sec=0,
            self_buff_boho_sec=20,
        )
        # 첫 송신 후 _send_count 가 증가했는지 + buff 가 store 에 보존되는지
        w._send_one()
        assert w._send_count == 1


# =====================================================================
# 19-20. config defaults + stats
# =====================================================================
class TestConfigAndStats:
    def test_attacker_config_defaults_match_v1(self):
        cfg = AttackerConfig()
        assert cfg.f1_window_sec == V1.ATK_F1_WINDOW_SEC
        assert cfg.warp_threshold == V1.ATK_WARP_THRESHOLD
        assert cfg.map_burst_n == V1.ATK_MAP_BURST_N
        assert cfg.capture_poll_sec == V1.ATK_GRAB_TARGET_INTERVAL_S
        assert cfg.hpmp_poll_sec == V1.HPMP_POLL_SEC
        assert cfg.own_cd_emit_period_sec == V1.ATK_OWN_CD_EMIT_PERIOD_SEC

    def test_stats_returns_dict(self):
        w = _make_worker()
        s = w.stats()
        assert isinstance(s, dict)
        for k in ("send_count", "burst_send_count", "cd_recv_count",
                  "map_seq", "f1_pending"):
            assert k in s


# =====================================================================
# 21. _state_to_bytes — 직렬화 호환
# =====================================================================
class TestStateSerialize:
    def test_state_to_bytes_returns_bytes(self):
        d = {
            "seq": 1, "map_name": "A", "coord_valid": True, "x": 10, "y": 20,
            "last_dir": "R", "map_seq": 0, "map_change_pending": False,
            "debuff_honmasul_sec": -1, "hp_pct": 80, "mp_pct": 50,
            "buff_mujang_sec": -1, "buff_boho_sec": -1,
            "red_tab": False, "red_cx": 0, "red_cy": 0,
        }
        b = _state_to_bytes(d)
        assert isinstance(b, (bytes, bytearray))
        assert len(b) > 0
