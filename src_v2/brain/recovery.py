"""RecoveryDispatcher — memory.outcome 구독 → 복구 시퀀스 트리거.

`@recovery(action_name, on=[status...])` 데코레이터로 핸들러 등록.
각 핸들러는 outcome payload(dict) 를 받아 List[CastRequest] 를 리턴.
dispatcher 가 해당 요청을 hands_api.request_cast() 로 실행.

builtin recovery 8종:
  - self_heal no_effect/timeout → ESC + chat_check + 재시도
  - move_direction no_effect/timeout (stuck) → ortho_unstick (warning)
  - tab_confirm timeout → reset + force_exit_dir
  - seq_rclick no_effect → 다음 cycle 재캡처
  - attacker_revive no_effect → TAB+HOME+우클릭+SEQ-A
  - chat_popup → ESC
  - fg_lost → 's' 키 1회
  - fps_low → adapter restart (warning emit only)

cooldown: 동일 (action, status) 조합은 cooldown_sec 내 재트리거 금지.
"""
from __future__ import annotations
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from ..core.event_bus import Event, EventBus
from ..core.types import CastRequest

log = logging.getLogger("src_v2.brain.recovery")


# ---------- 데코레이터 + registry ----------

RecoveryFn = Callable[[Dict[str, Any], "RecoveryContext"], List[CastRequest]]

# (action, status) → handler
_REGISTRY: Dict[tuple, RecoveryFn] = {}


def recovery(action: str, on: List[str], cooldown_sec: float = 5.0) -> Callable[[RecoveryFn], RecoveryFn]:
    """데코레이터.

    Usage:
        @recovery("self_heal", on=["no_effect", "timeout"])
        def handler(outcome, ctx):
            return [CastRequest(name="self_heal", priority=8)]
    """
    def deco(fn: RecoveryFn) -> RecoveryFn:
        for st in on:
            key = (action, st)
            _REGISTRY[key] = fn
        # cooldown attr 부착
        setattr(fn, "_recovery_cooldown_sec", float(cooldown_sec))
        return fn
    return deco


def list_handlers() -> List[tuple]:
    return list(_REGISTRY.keys())


def clear_handlers() -> None:
    _REGISTRY.clear()


# ---------- ctx ----------

class RecoveryContext:
    """핸들러에 전달되는 부가 ctx — keys adapter, worker_state 노출."""

    def __init__(self,
                 keys_adapter: Any = None,
                 worker_state: Optional[Dict[str, Any]] = None,
                 bus: Optional[EventBus] = None,
                 log_emit: Optional[Callable[[str], None]] = None,
                 store: Any = None) -> None:
        self.keys_adapter = keys_adapter
        self.worker_state = worker_state or {}
        self.bus = bus
        self.log_emit = log_emit or (lambda s: None)
        self.store = store


# ---------- builtin handlers ----------

@recovery("self_heal", on=["no_effect", "timeout"], cooldown_sec=3.0)
def _r_self_heal(outcome: Dict[str, Any], ctx: RecoveryContext) -> List[CastRequest]:
    """ESC + chat_check + 재시도. 채팅 팝업 떠 있을 때 자힐 안 들어가는 케이스 대응."""
    ctx.log_emit("[RECOVERY] self_heal no_effect → ESC + 재시도")
    # ESC 직후 YOLO가 흰탭 오감지 → TAB-CONFIRM 오발동 방지: 2초 suppress
    if ctx.store is not None:
        try:
            ctx.store.update(esc_suppress_tab_until=time.time() + 2.0)
        except Exception:
            pass
    return [
        CastRequest(name="esc_recover", priority=5),
        CastRequest(name="self_heal", priority=8, ctx={"_retry": True}),
    ]


@recovery("move_direction", on=["no_effect", "timeout"], cooldown_sec=2.0)
def _r_stuck(outcome: Dict[str, Any], ctx: RecoveryContext) -> List[CastRequest]:
    """STUCK — 직교 방향 unstick 시도."""
    ctx.log_emit("[RECOVERY] move STUCK → ortho_unstick")
    return [CastRequest(name="ortho_unstick", priority=20)]


@recovery("tab_confirm", on=["timeout"], cooldown_sec=10.0)
def _r_tab_confirm(outcome: Dict[str, Any], ctx: RecoveryContext) -> List[CastRequest]:
    """TAB-CONFIRM 실패 → state reset + force_exit_dir 재시도."""
    ctx.log_emit("[RECOVERY] tab_confirm timeout → state reset + force_exit")
    ws = ctx.worker_state
    if ws is not None:
        ws["_tab_confirm_force_reset"] = True
    return []  # 직접 cast 없음 — flag 만 셋


@recovery("seq_rclick", on=["no_effect"], cooldown_sec=1.0)
def _r_seq_rclick(outcome: Dict[str, Any], ctx: RecoveryContext) -> List[CastRequest]:
    """우클릭 미스 → 다음 cycle 재캡처 hint."""
    ctx.log_emit("[RECOVERY] seq_rclick miss → 재캡처 대기")
    ws = ctx.worker_state
    if ws is not None:
        ws["_seq_rclick_recapture"] = True
    return []


@recovery("attacker_revive", on=["no_effect"], cooldown_sec=15.0)
def _r_atk_revive(outcome: Dict[str, Any], ctx: RecoveryContext) -> List[CastRequest]:
    """격수 부활 미스 → TAB+HOME+우클릭+SEQ-A 재시도."""
    ctx.log_emit("[RECOVERY] attacker_revive no_effect → TAB+HOME+우클릭+SEQ-A 재시도")
    return [
        CastRequest(name="tab_home_target", priority=8),
        CastRequest(name="attacker_revive", priority=10, ctx={"_retry": True}),
    ]


@recovery("chat_popup", on=["fail", "no_effect"], cooldown_sec=2.0)
def _r_chat_popup(outcome: Dict[str, Any], ctx: RecoveryContext) -> List[CastRequest]:
    """채팅 팝업 → ESC 닫기."""
    ctx.log_emit("[RECOVERY] chat_popup → ESC")
    return [CastRequest(name="esc_recover", priority=5)]


@recovery("fg_lost", on=["fail", "no_effect"], cooldown_sec=10.0)
def _r_fg_lost(outcome: Dict[str, Any], ctx: RecoveryContext) -> List[CastRequest]:
    """fg 상실 → 's' 키 1회 재송신 (startup_s 와 동일)."""
    ctx.log_emit("[RECOVERY] fg_lost → 's' 키 송신")
    ad = ctx.keys_adapter
    if ad is not None and hasattr(ad, "send_vk"):
        try:
            ad.send_vk(0x53, False)
            time.sleep(0.05)
            ad.send_vk(0x53, True)
        except Exception:
            log.exception("fg_lost recover send_vk fail")
    return []


@recovery("fps_low", on=["fail"], cooldown_sec=60.0)
def _r_fps_low(outcome: Dict[str, Any], ctx: RecoveryContext) -> List[CastRequest]:
    """fps 저하 → 경고 emit만 (자동 restart 위험 → warn only)."""
    fps = (outcome.get("snap_after") or {}).get("fps", 0.0)
    ctx.log_emit(f"[RECOVERY] fps_low warn — fps={fps:.1f} (manual restart 권장)")
    if ctx.bus is not None:
        try:
            ctx.bus.publish("memory.warn", {"reason": "fps_low", "fps": fps})
        except Exception:
            pass
    return []


# ---------- dispatcher ----------

class RecoveryDispatcher:
    """memory.outcome / memory.anomaly 구독 → 핸들러 트리거.

    cooldown: 동일 (action, status) 키는 cooldown_sec 내 무시.
    """

    def __init__(self,
                 bus: EventBus,
                 hands_api: Any,
                 keys_adapter: Any = None,
                 worker_state: Optional[Dict[str, Any]] = None,
                 log_emit: Optional[Callable[[str], None]] = None,
                 enabled: bool = True,
                 store: Any = None) -> None:
        self.bus = bus
        self.hands_api = hands_api
        self.ctx = RecoveryContext(
            keys_adapter=keys_adapter,
            worker_state=worker_state,
            bus=bus,
            log_emit=log_emit,
            store=store,
        )
        self.enabled = enabled
        self._lock = threading.Lock()
        self._last_trigger: Dict[tuple, float] = {}
        self._counts: Dict[str, int] = {"triggered": 0, "skipped_cooldown": 0, "skipped_disabled": 0}
        self.history: List[Dict[str, Any]] = []
        self._history_capacity = 256

    def attach(self) -> None:
        self.bus.subscribe("memory.outcome", self._on_outcome)
        self.bus.subscribe("memory.anomaly", self._on_anomaly)

    def _on_outcome(self, evt: Event) -> None:
        if not self.enabled:
            self._counts["skipped_disabled"] += 1
            return
        try:
            payload = evt.payload or {}
            action = payload.get("action", "")
            status = payload.get("status", "")
            self._dispatch(action, status, payload)
        except Exception:
            log.exception("recovery _on_outcome fail")

    def _on_anomaly(self, evt: Event) -> None:
        """anomaly 도 recovery 트리거 가능 (e.g. fps_low metric)."""
        if not self.enabled:
            return
        try:
            payload = evt.payload or {}
            metric = payload.get("metric", "")
            # metric 이름을 action 처럼 매핑
            action_map = {
                "fps_avg": "fps_low",
                "ocr_success_rate": None,  # 일단 처리 안 함
                "cast_success_rate": None,
            }
            action = action_map.get(metric)
            if not action:
                return
            self._dispatch(action, "fail", payload)
        except Exception:
            log.exception("recovery _on_anomaly fail")

    def _dispatch(self, action: str, status: str, payload: Dict[str, Any]) -> None:
        key = (action, status)
        fn = _REGISTRY.get(key)
        if not fn:
            return
        cooldown_sec = float(getattr(fn, "_recovery_cooldown_sec", 5.0))
        now = time.monotonic()
        with self._lock:
            last = self._last_trigger.get(key, 0.0)
            if now - last < cooldown_sec:
                self._counts["skipped_cooldown"] += 1
                return
            self._last_trigger[key] = now
            self._counts["triggered"] += 1
        try:
            casts = fn(payload, self.ctx) or []
        except Exception:
            log.exception("recovery handler fail key=%s", key)
            casts = []
        for req in casts:
            try:
                self.hands_api.request_cast(req)
            except Exception:
                log.exception("recovery cast req fail name=%s", getattr(req, "name", "?"))
        self._record({"ts": now, "action": action, "status": status, "n_casts": len(casts)})

    def _record(self, e: Dict[str, Any]) -> None:
        self.history.append(e)
        if len(self.history) > self._history_capacity:
            self.history = self.history[-self._history_capacity:]

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._counts, handlers=len(_REGISTRY))
