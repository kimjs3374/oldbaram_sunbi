"""Attacker Worker V2 — v1 1:1 격수 측 PC 워커.

v1 SoR:
  - dist_dosa/src/app/attacker.py:31-947 (Attacker 클래스 run loop)
  - dist_dosa/src/workers/attacker_worker.py:19-446 (QThread 래퍼 + CooldownReceiver)

격수 측 책임 (v1 1:1):
  - Capture + OCR (자기 좌표/맵) → UDP send (UdpSender → 힐러 N대)
  - YOLO 빨탭 detection (자기 빨탭 → 스킬범위 오버레이용)
  - HP/MP self-monitor → State.hp_pct/mp_pct 송신
  - own cooldown OCR (격수 본인 스킬 쿨)
  - buff OCR (혼마술/무장/보호) → State 의 debuff_/buff_*_sec 송신
  - F1 키 감지 → map_change_pending=True 예고 5s 창
  - 좌표 워프 감지 (≥25 칸 점프) → map_seq++ + burst 3회 송신
  - 맵 이름 변경 → map_seq++ + burst 3회 송신
  - CooldownReceiver: 힐러들의 CooldownReport 역수신 → bus publish

격수에는 자힐/공력증강/파혼술 등 룰 미존재 (사람이 직접 조작). 자힐/부활은
힐러 측 PC 에서 healer_worker 가 처리.
"""
from __future__ import annotations
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from ..core.event_bus import EventBus
from ..core.snapshot import SnapshotStore
from ..core.types import AttackerState

from ..eyes.capture import CaptureWatcher
from ..eyes.yolo_watcher import YoloWatcher
from ..eyes.ocr_watcher import OcrWatcher
from ..eyes.hpmp_watcher import HpMpWatcher
from ..eyes.cooldown_watcher import CooldownWatcher
from ..config import v1_defaults as V1

log = logging.getLogger("src_v2.workers.attacker")


# =====================================================================
# Adapter Protocols
# =====================================================================
class UdpSenderAdapter(Protocol):
    def send(self, payload: bytes) -> None: ...
    def is_available(self) -> bool: ...


class CooldownReceiverAdapter(Protocol):
    """힐러들이 보낸 CooldownReport 역수신 (UDP recv on attacker_recv_port).

    poll() 가 0건 또는 다수 보고 list 반환. 각 보고는
    (CooldownReport-like, src_addr) 쌍.
    """
    def poll(self) -> List[Tuple[Any, Optional[Tuple[str, int]]]]: ...
    def is_available(self) -> bool: ...


class F1KeyAdapter(Protocol):
    """F1 키 down 상태 감지 (win32api.GetAsyncKeyState 0x70).

    is_down() -> bool. fallback: 항상 False (테스트 환경).
    """
    def is_down(self) -> bool: ...


class _NullSender:
    def __init__(self):
        self.sent: List[bytes] = []
    def send(self, payload: bytes) -> None:
        self.sent.append(payload)
    def is_available(self) -> bool:
        return True


class _NullCdRecv:
    def poll(self): return []
    def is_available(self): return False


class _NullF1:
    def is_down(self): return False


# =====================================================================
# Config
# =====================================================================
@dataclass
class AttackerConfig:
    capture_poll_sec: float = V1.ATK_GRAB_TARGET_INTERVAL_S
    yolo_poll_sec: float = 0.05
    ocr_poll_sec: float = 0.05
    hpmp_poll_sec: float = V1.HPMP_POLL_SEC
    udp_send_hz: int = 30
    # F1 예고 창 (v1 attacker.py:101)
    f1_window_sec: float = V1.ATK_F1_WINDOW_SEC
    # 워프 임계값 (v1 attacker.py:97)
    warp_threshold: int = V1.ATK_WARP_THRESHOLD
    # 맵 변경 burst (v1 attacker.py:90)
    map_burst_n: int = V1.ATK_MAP_BURST_N
    # own_cd emit period (v1 attacker.py:108)
    own_cd_emit_period_sec: float = V1.ATK_OWN_CD_EMIT_PERIOD_SEC


# =====================================================================
# State serialization helper
# =====================================================================
def _state_to_bytes(state_dict: Dict[str, Any]) -> bytes:
    """v1 net.protocol.State.to_bytes() 와 동치.

    실제 운영에서는 src.net.protocol.State 의 to_bytes() 를 직접 사용 — 본
    함수는 테스트 환경에서 직렬화 형태만 보장 (binary marker + field count).
    """
    try:
        # v1 SoR — 가능하면 그대로 사용
        from src.net.protocol import State, now_ms  # type: ignore
        st = State(
            seq=int(state_dict.get("seq", 0)),
            ts_ms=now_ms(),
            map_name=str(state_dict.get("map_name", "")),
            coord_valid=bool(state_dict.get("coord_valid", False)),
            x=int(state_dict.get("x", 0)),
            y=int(state_dict.get("y", 0)),
            last_dir=str(state_dict.get("last_dir", "-")),
            map_seq=int(state_dict.get("map_seq", 0)),
            map_change_pending=bool(state_dict.get("map_change_pending", False)),
            debuff_honmasul_sec=int(state_dict.get("debuff_honmasul_sec", -1)),
            hp_pct=int(state_dict.get("hp_pct", -1)),
            mp_pct=int(state_dict.get("mp_pct", -1)),
            buff_mujang_sec=int(state_dict.get("buff_mujang_sec", -1)),
            buff_boho_sec=int(state_dict.get("buff_boho_sec", -1)),
        )
        # red_tab fields
        try:
            st.red_tab = bool(state_dict.get("red_tab", False))
            st.red_cx = int(state_dict.get("red_cx", 0))
            st.red_cy = int(state_dict.get("red_cy", 0))
        except Exception:
            pass
        return st.to_bytes()
    except Exception:
        # 테스트 환경 fallback — repr 기반
        return repr(state_dict).encode("utf-8")


# =====================================================================
# Worker
# =====================================================================
class AttackerWorkerV2:
    """v1 attacker.Attacker 1:1 — composition root.

    Watchers (eyes/*) 가 SnapshotStore 갱신 → _send_loop 가 30Hz UDP 송신.
    F1 / 워프 / 맵변경 edge 는 _send_loop 가 직접 평가 (snapshot 비교).
    CooldownReceiver 는 별도 thread 가 poll → bus publish.
    """

    def __init__(self,
                 cfg: Optional[AttackerConfig] = None,
                 grabber: Any = None,
                 yolo: Any = None,
                 ocr: Any = None,
                 cooldown: Any = None,
                 buff: Any = None,
                 hpmp: Any = None,
                 xp: Any = None,
                 udp_sender: Optional[UdpSenderAdapter] = None,
                 cd_receiver: Optional[CooldownReceiverAdapter] = None,
                 f1_key: Optional[F1KeyAdapter] = None,
                 ) -> None:
        self.cfg = cfg or AttackerConfig()
        self.bus = EventBus()
        self.store = SnapshotStore()
        self.udp_sender: UdpSenderAdapter = udp_sender or _NullSender()
        self.cd_receiver: CooldownReceiverAdapter = cd_receiver or _NullCdRecv()
        self.f1_key: F1KeyAdapter = f1_key or _NullF1()
        # 2026-04-27 audit 5.4: XP watcher 체계 편입.
        from ..eyes.xp_watcher import XpWatcher  # local import (순환 회피).
        self.xp = XpWatcher(
            self.store, self.bus, adapter=xp, poll_sec=1.0,
        )

        self.capture = CaptureWatcher(
            self.store, self.bus, grabber=grabber,
            poll_sec=self.cfg.capture_poll_sec,
        )
        self.yolo = YoloWatcher(
            self.store, self.bus, yolo=yolo,
            poll_sec=self.cfg.yolo_poll_sec,
        )
        self.ocr = OcrWatcher(
            self.store, self.bus, ocr=ocr,
            poll_sec=self.cfg.ocr_poll_sec,
        )
        self.hpmp = HpMpWatcher(
            self.store, self.bus, adapter=hpmp,
            poll_sec=self.cfg.hpmp_poll_sec,
        )
        # 2026-04-25 격수도 자기 own cooldown / buff OCR — v1 attacker_worker.py
        # 의 own_cd_cb 흐름 1:1. cooldown_reading.skills 가 채워지면 facade
        # own_cooldown_update.emit 가 1Hz 로 dict 송출.
        self.cooldown = CooldownWatcher(
            self.store, self.bus, adapter=cooldown, slot="cd",
            poll_sec=1.0,
        )
        self.buff = CooldownWatcher(
            self.store, self.bus, adapter=buff, slot="buff",
            poll_sec=1.0,
        )

        # Threading
        self._send_thread: Optional[threading.Thread] = None
        self._cd_recv_thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()

        # Statistics
        self._send_count: int = 0
        self._burst_send_count: int = 0
        self._cd_recv_count: int = 0
        self._running: bool = False

        # F1 / map / warp tracking (v1 attacker.py 1:1)
        self._f1_prev_down: bool = False
        self._f1_pending_until: float = 0.0
        self._map_seq: int = 0
        self._map_burst_remaining: int = 0
        self._prev_sent_map: str = ""
        self._prev_x: int = 0
        self._prev_y: int = 0
        self._prev_coord_valid: bool = False
        self._seq: int = 0
        self._last_dir: str = "-"

        # CD-RECV 첫 보고 로그 (key=(row_idx, src_ip))
        self._cd_recv_seen_keys: set = set()
        self._cd_recv_snap_ts: Dict[Tuple[int, str], float] = {}
        self._last_cd_recv_emit: float = 0.0
        # 2026-05-05 — 송신 진단 (사용자가 "격수 좌표 못 받음" 신고 → 송신 여부 격리).
        self._send_diag_first_logged: bool = False
        self._send_diag_unavail_logged: bool = False
        self._last_send_diag_ts: float = 0.0

    # -----------------------------------------------------------------
    # Send loop — UDP 송신 30Hz + edge 감지
    # -----------------------------------------------------------------
    def _send_loop(self) -> None:
        interval = 1.0 / max(1, self.cfg.udp_send_hz)
        while not self._stop_evt.wait(interval):
            try:
                self._send_one()
            except Exception:  # noqa: BLE001
                log.exception("attacker send tick fail")

    def _send_one(self) -> None:
        if not self.udp_sender.is_available():
            # 2026-05-05 — udp_sender 미준비 1회 alert. _NullSender 면 cfg.net.peers
            # 비어있거나 RealUdpSenderAdapter init 실패. 사용자가 이 로그로 즉시 격리.
            if not self._send_diag_unavail_logged:
                self._send_diag_unavail_logged = True
                log.warning(
                    "[ATK-UDP-SEND] adapter is_available=False — "
                    "cfg.net.peers/port 또는 RealUdpSenderAdapter init 확인 필요"
                )
            return
        snap = self.store.read()
        now = time.time()

        # F1 edge 감지 (v1 attacker.py:560-568) — 0→1 전이 시 pending 창 개시.
        try:
            f1_down = bool(self.f1_key.is_down())
        except Exception:
            f1_down = False
        if f1_down and not self._f1_prev_down:
            self._f1_pending_until = now + float(self.cfg.f1_window_sec)
            log.info(
                "[ATK-F1] map_change_pending 활성 — 힐러 B3 차단 %.1fs",
                self.cfg.f1_window_sec,
            )
        self._f1_prev_down = f1_down
        pending_now = now < self._f1_pending_until

        # 좌표 / 맵 (snapshot 에서 — v2 watcher 가 갱신)
        # 2026-05-05 P1-2: attacker_self_coord / attacker_self_map alias 사용.
        # 내부적으로는 healer_coord / healer_map 슬롯을 read 하지만 의미 명시.
        coord = getattr(snap, "attacker_self_coord", None)
        map_name = str(getattr(snap, "attacker_self_map", "") or "")
        coord_valid = coord is not None
        x, y = (int(coord[0]), int(coord[1])) if coord_valid else (0, 0)

        # 워프 감지 (v1 attacker.py:675-687) — 같은 맵 내 좌표 점프 ≥ warp_threshold
        if (coord_valid and map_name and map_name == self._prev_sent_map
                and self._prev_coord_valid):
            d = abs(x - self._prev_x) + abs(y - self._prev_y)
            if d > int(self.cfg.warp_threshold):
                self._map_seq += 1
                self._map_burst_remaining = int(self.cfg.map_burst_n)
                log.info(
                    "[ATK-WARP] same_map=%r prev=(%d,%d) new=(%d,%d) d=%d "
                    "thr=%d → map_seq=%d",
                    map_name, self._prev_x, self._prev_y, x, y, d,
                    self.cfg.warp_threshold, self._map_seq,
                )

        # 맵 이름 변경 감지 (v1 attacker.py:693-702)
        if map_name and map_name != self._prev_sent_map:
            if self._prev_sent_map:
                self._map_seq += 1
                self._map_burst_remaining = int(self.cfg.map_burst_n)
                log.info(
                    "[ATK-MAP-EDGE] %r→%r map_seq=%d burst=%d",
                    self._prev_sent_map, map_name, self._map_seq,
                    self.cfg.map_burst_n,
                )
            self._prev_sent_map = map_name

        # last_dir 추정 (v1 attacker.py:399-409 의 _dir 동치)
        if coord_valid and self._prev_coord_valid:
            dx = x - self._prev_x
            dy = y - self._prev_y
            if abs(dx) < 1 and abs(dy) < 1:
                self._last_dir = "-"
            elif abs(dx) >= abs(dy):
                self._last_dir = "R" if dx > 0 else "L"
            else:
                self._last_dir = "D" if dy > 0 else "U"

        # buff / debuff (snapshot 에서 — own buff_watcher 가 갱신)
        debuff_honma = int(getattr(snap, "self_debuff_honma_sec", -1))
        mujang = int(getattr(snap, "self_buff_mujang_sec", -1))
        boho = int(getattr(snap, "self_buff_boho_sec", -1))

        # HP/MP pct (v2 hpmp watcher 결과)
        hp_pct = int(getattr(snap, "hp", -1))
        mp_pct = int(getattr(snap, "mp", -1))

        # YOLO 빨탭 (v2 yolo watcher 결과)
        red_box = getattr(snap, "self_red_box", None)
        red_tab = bool(red_box is not None)
        red_cx = int(getattr(snap, "self_red_cx", 0))
        red_cy = int(getattr(snap, "self_red_cy", 0))

        state_dict = {
            "seq": self._seq,
            "map_name": map_name,
            "coord_valid": coord_valid,
            "x": x, "y": y,
            "last_dir": self._last_dir,
            "map_seq": self._map_seq,
            "map_change_pending": pending_now,
            "debuff_honmasul_sec": debuff_honma,
            "hp_pct": hp_pct,
            "mp_pct": mp_pct,
            "buff_mujang_sec": mujang,
            "buff_boho_sec": boho,
            "red_tab": red_tab,
            "red_cx": red_cx,
            "red_cy": red_cy,
        }
        pkt = _state_to_bytes(state_dict)

        # 정상 송신
        try:
            self.udp_sender.send(pkt)
            self._send_count += 1
            # 2026-05-05 — 송신 진단 로그.
            # 첫 1회 [ATK-UDP-SEND] first 로 송신 동작 확인.
            # 5초 / 1회 [ATK-UDP-SEND] cumulative 로 누적 카운트.
            if not self._send_diag_first_logged:
                self._send_diag_first_logged = True
                log.info(
                    "[ATK-UDP-SEND] first state map=%r coord=(%d,%d) "
                    "valid=%s seq=%d",
                    map_name, x, y, coord_valid, self._seq,
                )
            if (now - self._last_send_diag_ts) >= 5.0:
                self._last_send_diag_ts = now
                log.info(
                    "[ATK-UDP-SEND] cumulative send_count=%d burst=%d "
                    "current map=%r coord=(%d,%d) valid=%s",
                    self._send_count, self._burst_send_count,
                    map_name, x, y, coord_valid,
                )
        except Exception:  # noqa: BLE001
            log.exception("udp send fail")

        # 맵 변경/워프 burst (v1 attacker.py:839-845) — 추가 2회 즉시 송신
        if self._map_burst_remaining > 0:
            for _ in range(2):
                try:
                    self.udp_sender.send(pkt)
                    self._burst_send_count += 1
                except Exception:  # noqa: BLE001
                    pass
            self._map_burst_remaining -= 1
            if self._map_burst_remaining == 0:
                log.info("[ATK-MAP-BURST-END] map_seq=%d", self._map_seq)

        # 갱신
        self._prev_x = x
        self._prev_y = y
        self._prev_coord_valid = coord_valid
        self._seq += 1

    # -----------------------------------------------------------------
    # CooldownReceiver loop — 힐러들의 보고 역수신 → bus publish
    # -----------------------------------------------------------------
    def _cd_recv_loop(self) -> None:
        # 짧은 poll 간격 — receiver adapter 가 non-blocking 또는 short-block.
        while not self._stop_evt.wait(0.05):
            if not self.cd_receiver.is_available():
                continue
            try:
                msgs = self.cd_receiver.poll() or []
            except Exception:  # noqa: BLE001
                log.exception("cd recv poll fail")
                continue
            for rep, src_addr in msgs:
                self._handle_cd_report(rep, src_addr)

    def _handle_cd_report(self, rep: Any, src_addr: Optional[Tuple[str, int]]) -> None:
        """v1 attacker_worker.py:274-358 의 _on_cd 1:1.

        - row_idx = peers IP 매칭으로 결정 (없으면 reported src_idx).
        - first 보고 INFO 로그 + 10s 주기 SNAP 로그.
        - bus publish ("recv.cd_report") + store update.
        """
        try:
            # 2026-04-27 v1 heartbeat.py:68-80 1:1: peers IP 매칭 우선,
            # 매칭 실패 시 reported src_idx fallback. 다중 힐러 환경에서
            # healer_idx 설정 오류 방어 (audit 5.3).
            reported_idx = int(getattr(rep, "src_idx", 0))
            src_ip = ""
            # P1-1 (v1_gap_fix_list): resolved_row_idx = peers IP 매칭 결과,
            # -1 = 매칭 실패 (다중 힐러 환경에서 reported 신뢰 안 함).
            resolved_row_idx = -1
            if src_addr and isinstance(src_addr, tuple) and src_addr[0]:
                src_ip = str(src_addr[0])
                _peers = list(getattr(self, "_peers", []) or [])
                for _i, _p in enumerate(_peers):
                    if str(_p).strip() == src_ip:
                        resolved_row_idx = _i
                        break
            # row_idx = resolved 우선, 실패 시 reported fallback (UI/로그 호환).
            row_idx = resolved_row_idx if resolved_row_idx >= 0 else reported_idx
            # mismatch 1회 경고 — 이후 동일 키는 noop.
            if (resolved_row_idx >= 0 and resolved_row_idx != reported_idx
                    and not hasattr(self, "_cd_recv_mismatch_warned")):
                self._cd_recv_mismatch_warned = set()
            mw = getattr(self, "_cd_recv_mismatch_warned", None)
            if (mw is not None and resolved_row_idx >= 0
                    and resolved_row_idx != reported_idx
                    and src_ip not in mw):
                mw.add(src_ip)
                log.warning(
                    "[CD-RECV-MISMATCH] ip=%s resolved=%d reported=%d "
                    "(peers 우선 사용)", src_ip, resolved_row_idx, reported_idx,
                )
            key = (row_idx, src_ip)

            # First 보고 로그
            if key not in self._cd_recv_seen_keys:
                self._cd_recv_seen_keys.add(key)
                log.info(
                    "[CD-RECV] first from ip=%s row=%d reported_idx=%d "
                    "p=%s b=%s armed=%s nick=%r",
                    src_ip, row_idx, int(getattr(rep, "src_idx", 0)),
                    getattr(rep, "cd_parlyuk", None),
                    getattr(rep, "cd_baekho", None),
                    getattr(rep, "armed", False),
                    getattr(rep, "nickname", ""),
                )

            # SNAP 로그 (key 별 10s 주기)
            now = time.monotonic()
            last = float(self._cd_recv_snap_ts.get(key, 0.0))
            if now - last >= float(V1.ATK_CD_RECV_SNAP_PERIOD_SEC):
                log.info(
                    "[CD-RECV-SNAP] row=%d ip=%s p=%s b=%s buff_parlyuk_sec=%d "
                    "armed=%s nick=%r",
                    row_idx, src_ip,
                    getattr(rep, "cd_parlyuk", None),
                    getattr(rep, "cd_baekho", None),
                    int(getattr(rep, "buff_parlyuk_sec", -1)),
                    getattr(rep, "armed", False),
                    getattr(rep, "nickname", ""),
                )
                self._cd_recv_snap_ts[key] = now

            # bus publish 형식 — v1 attacker_worker.py:323-358 의 emit dict 1:1
            payload = {
                "src_idx": row_idx,  # resolved 우선 (UI 호환 키).
                "resolved_row_idx": int(resolved_row_idx),  # P1-1 명시 contract.
                "reported_idx": int(getattr(rep, "src_idx", 0)),
                "src_ip": src_ip,
                "cd_parlyuk": getattr(rep, "cd_parlyuk", None),
                "cd_baekho": getattr(rep, "cd_baekho", None),
                "ts_ms": int(getattr(rep, "ts_ms", 0)),
                "armed": bool(getattr(rep, "armed", False)),
                "nickname": str(getattr(rep, "nickname", "") or ""),
                "buff_parlyuk_sec": int(getattr(rep, "buff_parlyuk_sec", -1)),
                "xp_per_hour": int(getattr(rep, "xp_per_hour", 0)),
                "event_text": str(getattr(rep, "event_text", "") or ""),
                "event_seq": int(getattr(rep, "event_seq", 0)),
                "hp_pct": int(getattr(rep, "hp_pct", -1)),
                "mp_pct": int(getattr(rep, "mp_pct", -1)),
                "hp_cur": int(getattr(rep, "hp_cur", -1)),
                "mp_cur": int(getattr(rep, "mp_cur", -1)),
                "hp_max": int(getattr(rep, "hp_max", 0)),
                "mp_max": int(getattr(rep, "mp_max", 0)),
                "self_heal_hp_thr": int(getattr(rep, "self_heal_hp_thr", -1)),
                "gyoungryeok_mp_thr": int(getattr(rep, "gyoungryeok_mp_thr", -1)),
            }
            self.bus.publish("recv.cd_report", payload)
            self._cd_recv_count += 1
        except Exception:  # noqa: BLE001
            log.exception("cd report handle fail")

    # -----------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        for w in (self.capture, self.yolo, self.ocr, self.hpmp,
                  self.cooldown, self.buff, self.xp):
            w.start()
        self._send_thread = threading.Thread(
            target=self._send_loop, daemon=True, name="attacker_udp_send",
        )
        self._send_thread.start()
        self._cd_recv_thread = threading.Thread(
            target=self._cd_recv_loop, daemon=True, name="attacker_cd_recv",
        )
        self._cd_recv_thread.start()
        self._running = True

    def stop(self, timeout: float = 2.0) -> None:
        if not self._running:
            return
        self._stop_evt.set()
        for w in (self.xp, self.buff, self.cooldown, self.hpmp, self.ocr,
                  self.yolo, self.capture):
            try:
                w.stop(timeout=timeout)
            except Exception:  # noqa: BLE001
                pass
        for th in (self._send_thread, self._cd_recv_thread):
            if th and th.is_alive():
                try:
                    th.join(timeout=timeout)
                except Exception:  # noqa: BLE001
                    pass
        self._running = False

    # -----------------------------------------------------------------
    # Region setters — V2MainWindow 직접 호출.
    # -----------------------------------------------------------------
    def set_game_region(self, x: int, y: int, w: int, h: int) -> None:
        # 2026-05-05 Cycle 4-10: ocr adapter 에 game_region 전달 (coord/map picker 역산).
        ocr_ad = getattr(self.ocr, "adapter", None)
        if ocr_ad is not None and hasattr(ocr_ad, "set_game_region"):
            try:
                ocr_ad.set_game_region(int(x), int(y), int(w), int(h))
            except Exception:
                log.exception("atk ocr set_game_region fail")
        g = getattr(self.capture, "grabber", None)
        if g is None:
            return
        for m in ("set_region", "set_game_region", "set_crop"):
            fn = getattr(g, m, None)
            if callable(fn):
                try:
                    fn(int(x), int(y), int(w), int(h))
                    return
                except Exception:
                    log.exception("game set_region fail")
                    return

    # 2026-05-05 Cycle 4-10 — coord/map picker setter.
    def set_coord_region(self, x: int, y: int, w: int, h: int) -> None:
        ad = getattr(self.ocr, "adapter", None)
        if ad is not None and hasattr(ad, "set_coord_region"):
            try:
                ad.set_coord_region(int(x), int(y), int(w), int(h))
            except Exception:
                log.exception("atk set_coord_region fail")

    def set_map_region(self, x: int, y: int, w: int, h: int) -> None:
        ad = getattr(self.ocr, "adapter", None)
        if ad is not None and hasattr(ad, "set_map_region"):
            try:
                ad.set_map_region(int(x), int(y), int(w), int(h))
            except Exception:
                log.exception("atk set_map_region fail")

    def set_hp_region(self, x: int, y: int, w: int, h: int) -> None:
        ad = getattr(self.hpmp, "adapter", None)
        if ad is not None and hasattr(ad, "set_hp_region"):
            try: ad.set_hp_region(int(x), int(y), int(w), int(h))
            except Exception: log.exception("atk hp set_region fail")

    def set_mp_region(self, x: int, y: int, w: int, h: int) -> None:
        ad = getattr(self.hpmp, "adapter", None)
        if ad is not None and hasattr(ad, "set_mp_region"):
            try: ad.set_mp_region(int(x), int(y), int(w), int(h))
            except Exception: log.exception("atk mp set_region fail")

    def set_xp_region(self, x: int, y: int, w: int, h: int) -> None:
        # attacker_worker_v2 는 xp watcher 가 없으므로 grabber 에게 위임 시도.
        ad = getattr(self.ocr, "adapter", None)
        if ad is not None and hasattr(ad, "set_xp_region"):
            try: ad.set_xp_region(int(x), int(y), int(w), int(h))
            except Exception: log.exception("atk xp set_region fail")

    def set_cooldown_region(self, x: int, y: int, w: int, h: int) -> None:
        # cooldown_watcher.adapter 가 set_region 보유 (RealCooldownAdapter).
        ad = getattr(self.cooldown, "adapter", None)
        if ad is not None and hasattr(ad, "set_region"):
            try:
                ad.set_region(int(x), int(y), int(w), int(h))
                return
            except Exception:
                log.exception("atk cd set_region fail")

    def set_buff_region(self, x: int, y: int, w: int, h: int) -> None:
        ad = getattr(self.buff, "adapter", None)
        if ad is not None and hasattr(ad, "set_region"):
            try:
                ad.set_region(int(x), int(y), int(w), int(h))
                return
            except Exception:
                log.exception("atk buff set_region fail")

    def set_peers(self, peers) -> None:
        """힐러 IP list 주입 — _handle_cd_report row_idx 매칭용 (audit 5.3)."""
        try:
            self._peers = list(peers or [])
        except Exception:
            self._peers = []

    def set_own_skill_names(self, names) -> None:
        """격수 OCR 타겟 — cd 영역과 buff 영역을 각각 다른 키워드로 set.

        - cd  영역 = 격수 클래스 스킬 (도적/전사 승급 스킬, 사용자가 names 로 주입)
        - buff 영역 = 격수 자기 버프 (무장/보호) + 디버프 (혼마술 — 도사 파혼술 트리거용)

        2026-04-27 정정: 이전엔 cd + buff 둘 다 같은 names (클래스 스킬) 로 set →
        buff 영역에서 클래스 스킬명 못 찾고 미스. buff 는 hardcode 격수 buff/디버프.
        """
        # v1 SoR: src/app/attacker.py:116 — buff_ocr.set_target_skills(["혼마술","무장","보호"])
        cd_names = list(names or [])
        buff_names = ["혼마술", "무장", "보호"]
        # cd
        cd_w = getattr(self, "cooldown", None)
        cd_ad = getattr(cd_w, "adapter", None) if cd_w is not None else None
        if cd_ad is not None and hasattr(cd_ad, "set_target_skills"):
            try:
                cd_ad.set_target_skills(cd_names)
            except Exception:
                log.exception("atk set_own_skill_names cooldown fail")
        # buff
        bf_w = getattr(self, "buff", None)
        bf_ad = getattr(bf_w, "adapter", None) if bf_w is not None else None
        if bf_ad is not None and hasattr(bf_ad, "set_target_skills"):
            try:
                bf_ad.set_target_skills(buff_names)
            except Exception:
                log.exception("atk set_own_skill_names buff fail")

    def set_hp_max(self, n: int) -> None:
        ad = getattr(self.hpmp, "adapter", None)
        if ad is not None and hasattr(ad, "set_hp_max"):
            try: ad.set_hp_max(int(n))
            except Exception: pass

    def set_mp_max(self, n: int) -> None:
        ad = getattr(self.hpmp, "adapter", None)
        if ad is not None and hasattr(ad, "set_mp_max"):
            try: ad.set_mp_max(int(n))
            except Exception: pass

    def stats(self) -> dict:
        return {
            "send_count": self._send_count,
            "burst_send_count": self._burst_send_count,
            "cd_recv_count": self._cd_recv_count,
            "map_seq": self._map_seq,
            "f1_pending": time.time() < self._f1_pending_until,
            "capture": self.capture.stats(),
            "yolo": self.yolo.stats(),
            "ocr": self.ocr.stats(),
            "hpmp": self.hpmp.stats(),
        }
