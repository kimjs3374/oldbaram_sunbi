"""HP/MP watcher.

Design ref: §2.4 + §11.2 (src/vision/hpmp.py wrap)
"""
from __future__ import annotations
import logging
import time as _time
from typing import Any, Callable, Optional, Protocol, Tuple

from .base_watcher import BaseWatcher
from ..core.event_bus import EventBus
from ..core.snapshot import SnapshotStore

log = logging.getLogger("src_v2.eyes.hpmp")


class HpMpAdapter(Protocol):
    """HP/MP reader.

    `read(frame)` -> (hp_pct, mp_pct, hp_cur, mp_cur, hp_max, mp_max)
    Use -1 for unknown.
    """
    def read(self, frame: Any) -> Tuple[int, int, int, int, int, int]: ...
    def is_available(self) -> bool: ...


class _NullHpMp:
    def read(self, frame): return (-1, -1, -1, -1, -1, -1)
    def is_available(self): return False


class HpMpWatcher(BaseWatcher):
    TOPIC_HP = "eye.hp"
    TOPIC_MP = "eye.mp"
    # P0-2 (v1_gap_fix_list): publish contract 메타 topic.
    # payload = {"source_state": "unconfigured|empty|observed|rejected", "hp": int, "mp": int}
    # source_state 정의:
    #   unconfigured — adapter is_available=False (영역 미설정/OCR 비활성)
    #   empty        — adapter read 가 (-1,-1,...) (영역은 있으나 OCR 미탐지)
    #   observed     — hp/mp 둘 중 하나 이상 >=0
    #   rejected     — read 예외 throw (영역 깨짐 등)
    TOPIC_STATE = "eye.hpmp_state"

    def __init__(self,
                 store: SnapshotStore,
                 bus: EventBus,
                 adapter: Optional[HpMpAdapter] = None,
                 poll_sec: float = 0.5,
                 log_callback: Optional[Callable[[str], None]] = None) -> None:
        super().__init__("hpmp", store, bus, poll_sec=poll_sec, adapter=adapter)
        self.adapter: HpMpAdapter = adapter or _NullHpMp()
        self._log_emit = log_callback if callable(log_callback) else None
        self._announced_start: bool = False
        self._first_hp_logged: bool = False
        self._first_mp_logged: bool = False
        self._last_pending_warn_ts: float = 0.0

    def _emit(self, s: str) -> None:
        if self._log_emit:
            try:
                self._log_emit(s)
            except Exception:
                pass

    def _publish_state(self, state: str, hp: int = -1, mp: int = -1) -> None:
        """P0-2 contract — source_state 메타 publish."""
        try:
            self.bus.publish(self.TOPIC_STATE, {
                "source_state": state, "hp": int(hp), "mp": int(mp),
            })
        except Exception:
            pass

    def _tick(self) -> None:
        if not self.adapter.is_available():
            self._publish_state("unconfigured")
            if not self._announced_start:
                self._announced_start = True
                self._emit("[HPMP-H] adapter is_available=False — HP/MP OCR 비활성")
            return
        if not self._announced_start:
            self._announced_start = True
            self._emit("[HPMP-H] watcher 시작 — adapter is_available=True")
        frame = self.store.read_field("last_frame")
        if frame is None:
            self._publish_state("empty")
            return
        # 2026-04-25 origin 전달 — region 화면 절대 좌표 → frame 변환용.
        origin = self.store.read_field("monitor_origin", (0, 0)) or (0, 0)
        try:
            try:
                r = self.adapter.read(frame, origin=origin)
            except TypeError:
                r = self.adapter.read(frame)
        except Exception:
            self._publish_state("rejected")
            return
        hp, mp, hpc, mpc, hpm, mpm = r
        prev_hp = self.store.read_field("hp", -1)
        prev_mp = self.store.read_field("mp", -1)
        self.store.update(
            hp=hp, mp=mp, hp_cur=hpc, mp_cur=mpc, hp_max=hpm, mp_max=mpm,
        )
        # 2026-04-27 BUG-FIX: 값 변화 무관 매 tick publish.
        # 이전엔 hp/mp 가 변할 때만 publish → 워커 시작 시 이미 임계치 아래
        # (또는 도달 후 변화 없음) 면 룰 evaluate 영원 안 일어남 → 공증/자힐
        # 영원 안 fire. polling 모델로 강제.
        try:
            self.bus.publish(self.TOPIC_HP, hp)
            self.bus.publish(self.TOPIC_MP, mp)
            # P0-2: state 메타 분리 publish (raw topic 호환 유지).
            state = "observed" if (hp >= 0 or mp >= 0) else "empty"
            self._publish_state(state, hp=hp, mp=mp)
        except Exception:
            pass
        if hp != prev_hp and hp >= 0 and not self._first_hp_logged:
            self._first_hp_logged = True
            self._emit(f"[HPMP-H] HP 최초 publish hp={hp}% cur={hpc}/{hpm}")
        if mp != prev_mp and mp >= 0 and not self._first_mp_logged:
            self._first_mp_logged = True
            self._emit(f"[HPMP-H] MP 최초 publish mp={mp}% cur={mpc}/{mpm}")
        # 10초/1회 hp<0 + mp<0 (둘다 미관측) pending warn.
        if hp < 0 and mp < 0:
            now = _time.time()
            if (now - self._last_pending_warn_ts) >= 10.0:
                self._last_pending_warn_ts = now
                self._emit(
                    "[HPMP-H] hp/mp 둘 다 -1 — 영역/해상도/색상 임계 확인"
                )
