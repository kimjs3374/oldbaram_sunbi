"""Cooldown UDP uplink — 힐러 PC 가 격수 PC 에 1Hz CooldownReport 역송.

v1 SoR (dist_dosa/src/workers/healer_worker.py:1995-2070).

송신 내용 (v1 net.protocol.CooldownReport):
  - cd_parlyuk / cd_baekho (own cooldown OCR 결과)
  - buff_parlyuk_sec
  - hp_pct / mp_pct / hp_cur / mp_cur / hp_max / mp_max
  - armed
  - nickname (own nick OCR)
  - xp_per_hour
  - event_text / event_seq (격수 화면 알림)
  - self_heal_hp_thr / gyoungryeok_mp_thr (격수 측 HUD 표시용)
  - force_coord_tol  (parlyuk 활성 시 1, else -1 → 격수가 OCR 안전계수 조정)

NOTE: v1 워커는 send_uplink() 를 1Hz tick 마다 호출.
"""
from __future__ import annotations
import logging
import threading
import time
from typing import Any, Callable, Optional, Protocol

from ..core.snapshot import SnapshotStore
from ..config import v1_defaults as V1

log = logging.getLogger("src_v2.eyes.cooldown_uplink")


class UplinkSenderAdapter(Protocol):
    def send(self, payload: bytes) -> None: ...
    def is_available(self) -> bool: ...


def _build_payload(snap: Any, alert_seq: int = 0) -> Optional[bytes]:
    """v1 CooldownReport 의 to_bytes() 1:1 시도. 실패 시 None."""
    try:
        from src.net.protocol import CooldownReport, now_ms  # type: ignore
    except Exception:
        return None

    # 힐러 자기 좌표/맵/상태 (격수 UI 힐러 행 표시용)
    hcoord = getattr(snap, "healer_coord", None) or (0, 0)
    try:
        hx, hy = int(hcoord[0]), int(hcoord[1])
    except Exception:
        hx, hy = 0, 0

    # state_text 한국어 (v1 _compute_state_text 와 동치)
    armed = bool(getattr(snap, "armed", False))
    follow_only = bool(getattr(snap, "follow_only", False))
    map_paused = bool(getattr(snap, "map_paused", False))
    udp_active = bool(getattr(snap, "udp_active", False))
    if not udp_active and not getattr(snap, "attacker_state", None):
        state_text = "정지"
    elif not armed:
        state_text = "일시정지"
    elif map_paused or bool(getattr(snap, "f1_pend_active", False)):
        state_text = "맵전환중"
    elif follow_only:
        state_text = "따라가기만"
    else:
        state_text = "전투중"

    rep = CooldownReport(
        src_idx=int(getattr(snap, "src_idx", 0)),
        ts_ms=now_ms(),
        cd_parlyuk=int(getattr(snap, "cd_parlyuk", -1) or -1),
        cd_baekho=int(getattr(snap, "cd_baekho", -1) or -1),
        armed=armed,
        nickname=str(getattr(snap, "nickname", "") or ""),
        # 2026-05-05 P0-4 fix: 이전엔 cd_parlyuk(쿨다운) 을 buff sec 자리에
        # 넣어 잘못 송신. snapshot.buff_parlyuk_sec 신설(snapshot.py).
        # buff OCR 가 채우면 실값, 미동작 시 -1 안전 송신.
        buff_parlyuk_sec=int(getattr(snap, "buff_parlyuk_sec", -1) or -1),
        hp_pct=int(getattr(snap, "hp", -1) or -1),
        mp_pct=int(getattr(snap, "mp", -1) or -1),
        hp_cur=int(getattr(snap, "hp_cur", -1) or -1),
        mp_cur=int(getattr(snap, "mp_cur", -1) or -1),
        hp_max=int(getattr(snap, "hp_max", 0) or 0),
        mp_max=int(getattr(snap, "mp_max", 0) or 0),
        self_heal_hp_thr=int(getattr(snap, "self_heal_hp_thr", -1) or -1),
        gyoungryeok_mp_thr=int(getattr(snap, "gyoungryeok_mp_thr", -1) or -1),
        xp_per_hour=int(getattr(snap, "xp_per_hour", 0) or 0),
        event_text=str(getattr(snap, "event_text", "") or ""),
        event_seq=int(alert_seq),
        healer_map=str(getattr(snap, "healer_map", "") or ""),
        healer_x=hx,
        healer_y=hy,
        coord_valid=bool(getattr(snap, "healer_coord", None) is not None),
        state_text=state_text,
    )
    # v1 CooldownReport 가 force_coord_tol 필드를 지원하는 경우 set.
    try:
        if bool(getattr(snap, "parlyuk_buff_active", False)):
            rep.force_coord_tol = 1  # type: ignore[attr-defined]
        else:
            rep.force_coord_tol = -1  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        return rep.to_bytes()
    except Exception:
        return None


class CooldownUplink(threading.Thread):
    """1Hz 로 store snapshot 을 packing 해 UDP 송신.

    Phase 0~4 의 Eyes 스타일 — 별 thread, store read + udp send.
    """

    def __init__(self,
                 store: SnapshotStore,
                 sender: UplinkSenderAdapter,
                 period_sec: float = 1.0,
                 alert_seq_provider: Optional[Callable[[], int]] = None) -> None:
        super().__init__(daemon=True, name="cd_uplink")
        self.store = store
        self.sender = sender
        self.period = float(period_sec)
        self._stop_evt = threading.Event()
        self._send_count: int = 0
        self._fail_count: int = 0
        self._alert_seq_provider = alert_seq_provider or (lambda: 0)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_evt.set()
        if self.is_alive():
            self.join(timeout=timeout)

    def run(self) -> None:
        log.info("cd_uplink start period=%.2fs", self.period)
        while not self._stop_evt.wait(self.period):
            try:
                if not self.sender.is_available():
                    continue
                snap = self.store.read()
                aseq = int(self._alert_seq_provider() or 0)
                pkt = _build_payload(snap, alert_seq=aseq)
                if pkt is None:
                    continue
                self.sender.send(pkt)
                self._send_count += 1
            except Exception:  # noqa: BLE001
                self._fail_count += 1
                log.exception("cd_uplink send fail")
        log.info("cd_uplink stop sent=%d fail=%d",
                 self._send_count, self._fail_count)

    def stats(self) -> dict:
        return {
            "send_count": self._send_count,
            "fail_count": self._fail_count,
            "period_sec": self.period,
            "alive": self.is_alive(),
        }
