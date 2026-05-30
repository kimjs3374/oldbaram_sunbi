"""UDP watcher — receives attacker state from network.

Wraps a UdpAdapter (e.g. src/net/udp_receiver.py).

Design ref: §2.4 + §11.2 (src/net/udp_receiver.py wrap)
"""
from __future__ import annotations
import logging
import time
from typing import Any, Optional, Protocol

from .base_watcher import BaseWatcher
from ..core.event_bus import EventBus
from ..core.snapshot import SnapshotStore
from ..core.types import AttackerState

log = logging.getLogger("src_v2.eyes.udp")


class UdpAdapter(Protocol):
    """UDP receiver adapter.

    `recv()` -> AttackerState or None (non-blocking-ish; returns None if no msg).
    """
    def recv(self) -> Optional[AttackerState]: ...
    def is_available(self) -> bool: ...


class _NullUdp:
    def recv(self): return None
    def is_available(self): return False


class UdpWatcher(BaseWatcher):
    TOPIC = "eye.attacker_state"

    def __init__(self,
                 store: SnapshotStore,
                 bus: EventBus,
                 adapter: Optional[UdpAdapter] = None,
                 poll_sec: float = 0.02,
                 log_callback: Optional[Any] = None) -> None:
        super().__init__("udp", store, bus, poll_sec=poll_sec, adapter=adapter)
        self.adapter: UdpAdapter = adapter or _NullUdp()
        self._log_emit = log_callback if callable(log_callback) else None
        self._first_recv_logged = False
        self._last_no_state_warn_ts = 0.0
        self._tick_count = 0
        self._announced_start = False
        # 2026-04-27 v1 stall edge (healer_worker.py:1749-1774) 1:1.
        self._udp_stalled = False
        self._udp_stall_since = 0.0
        self._last_seq = 0

    def _emit(self, s: str) -> None:
        if self._log_emit:
            try:
                self._log_emit(s)
            except Exception:
                pass

    def _tick(self) -> None:
        self._tick_count += 1
        if not self.adapter.is_available():
            if not self._announced_start:
                self._announced_start = True
                self._emit("[UDP-RECV] adapter=None — udp 비활성")
            return
        if not self._announced_start:
            self._announced_start = True
            self._emit("[UDP-RECV] watcher 시작 — adapter is_available=True")
        # Drain all pending messages this tick
        latest: Optional[AttackerState] = None
        n_msgs = 0
        for _ in range(64):
            msg = self.adapter.recv()
            if msg is None:
                break
            latest = msg
            n_msgs += 1
        # udp_active edge — 5초 내 수신 여부 평가
        last_rx = self.store.read_field("attacker_state", None)
        last_rx_ts = getattr(last_rx, "received_at", 0.0) if last_rx else 0.0
        now = time.monotonic()
        if latest is not None:
            last_rx_ts = now
        active = (now - last_rx_ts) < 5.0 if last_rx_ts else False
        if latest is None:
            # 그래도 udp_active 만 갱신.
            self.store.update(udp_active=active)
            # 진단: 격수 State 미수신 — 10초/1회 [NO-STATE] 로그 (v1 healer 와 동치).
            import time as _wt
            wnow = _wt.time()
            if (wnow - self._last_no_state_warn_ts) >= 10.0:
                self._last_no_state_warn_ts = wnow
                self._emit(
                    "[NO-STATE] 격수 State 미수신 — 격수 PC 가동/UDP send/peer 확인"
                )
            return
        latest.received_at = now
        # 2026-04-27 v1 동치 stall/resume edge — healer_worker.py:1749-1774.
        seq_alive = int(latest.seq) > int(self._last_seq)
        if seq_alive:
            if self._udp_stalled:
                stall_dur = now - self._udp_stall_since
                self._emit(
                    f"[UDP-RESUME] seq={latest.seq} stall_dur={stall_dur:.1f}s"
                )
                self._udp_stalled = False
            self._last_seq = int(latest.seq)
            self._udp_stall_since = now
        else:
            if (not self._udp_stalled
                    and int(latest.seq) > 0
                    and (now - self._udp_stall_since) > 3.0):
                self._emit(
                    f"[UDP-STALL] seq={latest.seq} 3초+ 동일 — 격수 송신 중단?"
                )
                self._udp_stalled = True
        # 첫 수신 1회 emit ([UDP-RECV] first State).
        if not self._first_recv_logged:
            self._first_recv_logged = True
            self._emit(
                f"[UDP-RECV] first State map='{latest.map_name}' "
                f"coord_valid={latest.coord_valid} hp={latest.hp}"
            )
        prev_seq = self.store.read_field("attacker_map_seq", 0) or 0
        new_seq = prev_seq
        prev_map = self.store.read_field("attacker_map", "")
        if latest.map_name and latest.map_name != prev_map:
            new_seq = prev_seq + 1
        # attacker_seq — UDP 메시지 카운트 누적
        prev_atk_seq = self.store.read_field("attacker_seq", 0) or 0
        self.store.update(
            attacker_coord=latest.coord,
            attacker_coord_valid=latest.coord_valid,
            attacker_map=latest.map_name,
            attacker_map_seq=new_seq,
            attacker_last_dir=latest.last_dir,
            attacker_hp=latest.hp,
            attacker_honma_sec=latest.honma_sec,
            attacker_mujang_sec=latest.mujang_sec,
            attacker_boho_sec=latest.boho_sec,
            attacker_state=latest,
            attacker_seq=int(prev_atk_seq) + int(n_msgs),
            udp_active=active,
        )
        # 첫 수신 후 매 N tick 마다 진단 로그 (사용자가 "흐름 끊김" 주장 시 원인 격리).
        if (self._tick_count % 250) == 0:
            self._emit(
                f"[UDP-RECV-SNAP] map='{latest.map_name}' coord={latest.coord} "
                f"hp={latest.hp} mp={latest.mp} honma={latest.honma_sec} "
                f"mujang={latest.mujang_sec} boho={latest.boho_sec} "
                f"map_change_pending={latest.map_change_pending}"
            )
        self.bus.publish(self.TOPIC, latest)
