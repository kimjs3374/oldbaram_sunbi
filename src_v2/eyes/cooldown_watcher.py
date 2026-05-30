"""Cooldown / buff / chat watcher.

Three slots (cd, buff, chat) — each polls its OCR region and updates Snapshot.

Design ref: §2.4 + §11.2 (src/vision/cooldown_ocr.py wrap)
"""
from __future__ import annotations
import logging
from typing import Any, Dict, Optional, Protocol

from .base_watcher import BaseWatcher
from ..core.event_bus import EventBus
from ..core.snapshot import SnapshotStore

log = logging.getLogger("src_v2.eyes.cooldown")


class CooldownAdapter(Protocol):
    """Cooldown OCR adapter.

    `read(frame)` -> dict of skill_name -> seconds_remaining (or 0 if ready).
    """
    def read(self, frame: Any) -> Dict[str, int]: ...
    def is_available(self) -> bool: ...


class _NullCooldown:
    def read(self, frame): return {}
    def is_available(self): return False


# Field map: OCR key -> Snapshot field
# v1 cooldown_ocr 는 한국어 스킬명 dict 반환 ("파력무참"/"백호의희원" 등).
# 영문 alias 도 함께 매핑 — adapter 가 번역해 보내는 경우 호환.
_FIELD_MAP_CD = {
    # 한국어 (v1 SoR)
    "파력무참": "cd_parlyuk",
    "백호의희원": "cd_baekho",
    "백호의희원첨": "cd_baekho",
    "파혼술": "cd_parhon",
    "부활": "cd_revive",
    # 영문 alias
    "parlyuk": "cd_parlyuk",
    "baekho": "cd_baekho",
    "parhon": "cd_parhon",
    "revive": "cd_revive",
}

_FIELD_MAP_BUFF = {
    # 한국어
    "파력무참": "buff_parlyuk_active",
    "백호의희원": "buff_baekho_active",
    "공력증강": "buff_gyoungryeok_active",
    "혼마술": "self_debuff_honma_sec",
    "무장": "self_buff_mujang_sec",
    "보호": "self_buff_boho_sec",
    # 영문
    "parlyuk_active": "buff_parlyuk_active",
    "baekho_active": "buff_baekho_active",
    "gyoungryeok_active": "buff_gyoungryeok_active",
}

# 2026-05-05 P0-4 후속 (Task 6): buff bool 필드와 함께 잔여 sec 도 채워야 하는
# 항목. OCR 결과 v 가 numeric 이면 sec 도 같이 set.
# CooldownReport.buff_parlyuk_sec 송신용 (cooldown_uplink.py).
# 백호/공증/무장/보호 등은 sec 별도 송신 불필요(또는 self_*_sec 으로 이미 매핑됨).
_FIELD_MAP_BUFF_SEC = {
    "파력무참": "buff_parlyuk_sec",
}


class CooldownWatcher(BaseWatcher):
    """Generic cooldown OCR watcher. slot determines field map + topic.

    slot: 'cd' | 'buff' | 'chat'
    """
    TOPIC = "eye.cooldown"
    # P0-2 (v1_gap_fix_list): publish contract 메타 topic.
    # payload = {"slot": "cd|buff|chat", "source_state": "unconfigured|empty|observed|rejected"}
    # source_state 정의:
    #   unconfigured — adapter is_available=False
    #   empty        — adapter read 가 빈 dict (영역 있으나 OCR 미탐지)
    #   observed     — 비어있지 않은 dict
    #   rejected     — read 예외
    TOPIC_STATE = "eye.cooldown_state"

    def __init__(self,
                 store: SnapshotStore,
                 bus: EventBus,
                 adapter: Optional[CooldownAdapter] = None,
                 slot: str = "cd",
                 poll_sec: float = 1.0,
                 log_callback: Optional[Any] = None) -> None:
        super().__init__(f"cooldown_{slot}", store, bus, poll_sec=poll_sec, adapter=adapter)
        self.adapter: CooldownAdapter = adapter or _NullCooldown()
        self.slot = slot
        self._field_map = _FIELD_MAP_CD if slot == "cd" else _FIELD_MAP_BUFF if slot == "buff" else {}
        # 진단 로그 콜백 — facade _emit_log 라우팅.
        self._log_emit = log_callback if callable(log_callback) else None
        self._first_read_logged = False
        self._last_diag_ts = 0.0
        self._tick_count = 0
        # 2026-04-28 audit: v1 SoR 1:1 — ctx.cooldowns 가 매 tick 새 dict
        # (skills dict 에 키 없으면 _cd_empty default -1 → ready). v2 stick 차단.
        # stale_reset_sec=0 = 매 tick reset (v1 동치). 룰의 last_cast+5s 게이트가
        # 중복 시전 보호.
        self._key_last_seen: Dict[str, float] = {}
        self._stale_reset_sec = 0.0

    def _emit(self, s: str) -> None:
        if self._log_emit:
            try:
                self._log_emit(s)
            except Exception:
                pass

    def _publish_state(self, state: str) -> None:
        """P0-2 contract — source_state 메타 publish."""
        try:
            self.bus.publish(self.TOPIC_STATE, {
                "slot": self.slot, "source_state": state,
            })
        except Exception:
            pass

    def _tick(self) -> None:
        import time as _t
        self._tick_count += 1
        if not self.adapter.is_available():
            self._publish_state("unconfigured")
            # 진단: adapter 가 None 이면 1회만 emit.
            if not self._first_read_logged and self._tick_count <= 1:
                self._emit(f"[CD-OCR] slot={self.slot} adapter=None — skip")
                self._first_read_logged = True
            return
        frame = self.store.read_field("last_frame")
        if frame is None:
            self._publish_state("empty")
            return
        # 2026-04-25 origin 전달 — region 화면 절대 좌표 → frame 변환.
        origin = self.store.read_field("monitor_origin", (0, 0)) or (0, 0)
        try:
            try:
                result = self.adapter.read(frame, origin=origin)
            except TypeError:
                result = self.adapter.read(frame)
        except Exception:
            self._publish_state("rejected")
            return
        # 2026-04-27 BUG-FIX: 빈 result 여도 publish 강제.
        # 이전엔 if not result: return 으로 publish skip → rule_engine 이
        # eye.cooldown event 영원 못 받음 → baekho/parlyuk 룰 평가 자체 안
        # 일어남 → 한 번도 안 쓴 ready 상태 (cd OCR 비어있음) 영원 못 잡음.
        now = _t.monotonic()
        # 2026-04-28 audit fix: 빈 result 여도 통과 — 아래 v1 SoR 1:1 reset 로직
        # 이 모든 cd 필드를 -1 default 로 박음. 이전엔 여기서 return 해 reset
        # 안 되고 snap 값 stick (사용자 신고 root cause).
        if not result:
            result = {}  # noop — fall-through 해서 -1 default 적용
            if (now - self._last_diag_ts) >= 5.0:
                self._last_diag_ts = now
                ad = getattr(self.adapter, "underlying_ocr", None) or self.adapter
                init = getattr(ad, "init_note", lambda: "?")()
                reg = getattr(ad, "region", lambda: None)()
                self._emit(
                    f"[CD-OCR] slot={self.slot} pending init={init!r} "
                    f"region={reg} origin={origin}"
                )
        # 첫 결과 도달 시 1회 emit.
        if not self._first_read_logged:
            self._first_read_logged = True
            preview = ", ".join(f"{k}={v}" for k, v in list(result.items())[:6])
            self._emit(f"[CD-OCR] slot={self.slot} first read {{{preview}}}")
        # 2026-04-28 audit fix: v1 SoR 1:1 — 매 tick 알려진 모든 cd 필드 -1 default
        # 후 result 로 덮어쓰기. v1 ctx.cooldowns 가 매 tick 새 dict 인 동작 동치.
        # cooldown_ocr 가 키 못 잡으면 -1 (미관측) → 룰 _cd_empty(cd<=0) ready
        # → fire → last_cast+5s 게이트로 중복 시전 보호.
        # 이전 stick 패턴: result 에 키 없으면 store.update 안 함 → snap 값
        # stick → 룰이 영원 cd>0 분기로 차단되던 root cause.
        #
        # P0-4 fix 2026-04-28: bool 필드는 -1 reset 금지 (gyoungryeok 영구 차단 root).
        # buff_*_active (Snapshot bool 필드) 가 -1 로 박히면 truthy 판정 → 룰 line 49
        # `if snap.buff_gyoungryeok_active:` 영원 True → fire 0건.
        # blueprint_state §3 위반 항목.
        _BOOL_FIELDS = {
            "buff_parlyuk_active",
            "buff_baekho_active",
            "buff_gyoungryeok_active",
        }
        updates: Dict[str, Any] = {}
        for field in set(self._field_map.values()):
            updates[field] = False if field in _BOOL_FIELDS else -1
        # 2026-05-05 P0-4 후속 (Task 6): buff slot 에서 sec 필드도 default -1 reset.
        # 매 tick OCR 결과에 키 없으면 미관측(-1) 표시 → uplink 가 -1 안전 송신.
        if self.slot == "buff":
            for sec_field in set(_FIELD_MAP_BUFF_SEC.values()):
                updates[sec_field] = -1
        for k, v in result.items():
            field = self._field_map.get(k)
            if not field:
                continue
            if field in _BOOL_FIELDS:
                # v1 _buff_present(c, name): buff dict 에 키 존재하면 active.
                # 따라서 키가 result 에 들어왔다는 사실 자체가 active 신호.
                # bool 값이 명시적으로 들어오면 그 값 우선.
                if isinstance(v, bool):
                    updates[field] = v
                else:
                    updates[field] = True
                # 2026-05-05 P0-4 후속: parlyuk 의 sec 값도 같이 채움.
                # OCR 가 "파력무참": <int sec> 형태로 보내면 sec 필드 갱신.
                sec_field = _FIELD_MAP_BUFF_SEC.get(k)
                if sec_field is not None and isinstance(v, (int, float)):
                    try:
                        updates[sec_field] = int(v)
                    except Exception:
                        pass
            else:
                try:
                    updates[field] = (
                        int(v) if isinstance(v, (int, float)) else v
                    )
                except Exception:
                    updates[field] = v
        # cd slot 은 publisher / hunter_helper_panel 이 객체 attribute 로 사용.
        if self.slot == "cd":
            updates["cooldown_reading"] = _CooldownReadingCompat(result)
        if updates:
            self.store.update(**updates)
            self.bus.publish(self.TOPIC, dict(updates))
            # P0-2: state 메타 분리 publish.
            self._publish_state("observed" if result else "empty")


class _CooldownReadingCompat:
    """v1 src.vision.cooldown_ocr.CooldownReading 호환 wrapper.

    main_window / hunter_helper_panel / overlay 가 attribute 로 read:
      - cd_parlyuk: int (남은 초 / -1 미관측)
      - cd_baekho:  int
      - skills:     Dict[str, int]  (한국어 키)
      - raw_text:   str  (비워둠 — v2 adapter 가 raw 미제공)
      - nickname:   str  (cooldown_uplink 가 따로 채움)
      - ts:         float
    """
    __slots__ = ("cd_parlyuk", "cd_baekho", "cd_parhon", "cd_revive",
                 "skills", "raw_text", "nickname", "ts")

    def __init__(self, raw: Dict[str, Any]) -> None:
        import time as _t
        # 한국어 스킬명 우선, 영문 alias fallback.
        def _grab(*keys, default=-1):
            for k in keys:
                if k in raw:
                    try:
                        v = raw[k]
                        return int(v) if isinstance(v, (int, float)) else default
                    except Exception:
                        return default
            return default
        self.cd_parlyuk = _grab("파력무참", "parlyuk")
        self.cd_baekho = _grab("백호의희원", "백호의희원첨", "baekho")
        self.cd_parhon = _grab("파혼술", "parhon")
        self.cd_revive = _grab("부활", "revive")
        # 원본 dict 유지 (한국어 키 보존).
        self.skills = dict(raw)
        self.raw_text = ""
        self.nickname = ""
        self.ts = _t.monotonic()
