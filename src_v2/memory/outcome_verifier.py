"""OutcomeVerifier — 행동의 expected_outcome 검증.

각 행동(시전/이동/시퀀스/우클릭)에 대해:
  - expected_outcome 함수: snap_before vs snap_after 비교 → bool
  - deadline: 검증 완료 기한 (sec)
  - 결과: ok / fail / timeout / no_effect
  - bus.publish("memory.outcome", {action, status, latency_ms, ...})

builtin verifier 12종 (self_heal, self_revive, attacker_revive, gyoungryeok,
parhon, baekho, parlyuk, mujang, boho, move_direction, tab_confirm, seq_rclick).

EventBus 토픽 구독:
  - hand.cast_done : 시전 직후 (status=ok | skipped) → 검증 enqueue
  - hand.cast_failed : 시전 실패 → 즉시 fail 기록

내부 워커 스레드가 pending 검증을 deadline 만료까지 폴링.
"""
from __future__ import annotations
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from ..core.event_bus import Event, EventBus
from ..core.snapshot import Snapshot, SnapshotStore
from ..core.types import CastError, CastResult

log = logging.getLogger("src_v2.memory.outcome_verifier")


# ---------- snap 비교 helper ----------

def _snap_dict(s: Snapshot) -> Dict[str, Any]:
    return {
        "hp": int(getattr(s, "hp", -1) or -1),
        "mp": int(getattr(s, "mp", -1) or -1),
        "healer_coord": getattr(s, "healer_coord", None),
        "healer_map": getattr(s, "healer_map", "") or "",
        "healer_map_seq": int(getattr(s, "healer_map_seq", 0) or 0),
        "attacker_coord": getattr(s, "attacker_coord", None),
        "attacker_map": getattr(s, "attacker_map", "") or "",
        "attacker_map_seq": int(getattr(s, "attacker_map_seq", 0) or 0),
        "attacker_hp": int(getattr(s, "attacker_hp", -1) or -1),
        "attacker_honma_sec": int(getattr(s, "attacker_honma_sec", -1) or -1),
        "attacker_mujang_sec": int(getattr(s, "attacker_mujang_sec", -1) or -1),
        "attacker_boho_sec": int(getattr(s, "attacker_boho_sec", -1) or -1),
        "buff_parlyuk_active": bool(getattr(s, "buff_parlyuk_active", False)),
        "buff_baekho_active": bool(getattr(s, "buff_baekho_active", False)),
        "buff_gyoungryeok_active": bool(getattr(s, "buff_gyoungryeok_active", False)),
        "cd_baekho": int(getattr(s, "cd_baekho", -1) or -1),
        "cd_parlyuk": int(getattr(s, "cd_parlyuk", -1) or -1),
        "cd_parhon": int(getattr(s, "cd_parhon", -1) or -1),
        "cd_revive": int(getattr(s, "cd_revive", -1) or -1),
        "red_tab_present": bool(getattr(s, "red_tab_present", False)),
        "fps": float(getattr(s, "fps", 0.0) or 0.0),
    }


# ---------- 12 builtin verifiers ----------

VerifierFn = Callable[[Dict[str, Any], Dict[str, Any]], bool]


def _v_self_heal(b: Dict[str, Any], a: Dict[str, Any]) -> bool:
    """HP > before+5 within deadline, or already ≥95% (no room to gain)."""
    if b["hp"] < 0 or a["hp"] < 0:
        return False
    if a["hp"] >= 95:
        return True
    return a["hp"] > b["hp"] + 5


def _v_self_revive(b: Dict[str, Any], a: Dict[str, Any]) -> bool:
    """HP > 0 (caster alive)."""
    return a["hp"] > 0


def _v_attacker_revive(b: Dict[str, Any], a: Dict[str, Any]) -> bool:
    """attacker_hp > 0."""
    return a["attacker_hp"] > 0


def _v_gyoungryeok(b: Dict[str, Any], a: Dict[str, Any]) -> bool:
    """MP < before-30 AND buff_gyoungryeok_active=True."""
    mp_drop = (b["mp"] >= 0 and a["mp"] >= 0 and a["mp"] < b["mp"] - 30)
    buff_on = a["buff_gyoungryeok_active"]
    return bool(mp_drop or buff_on)


def _v_parhon(b: Dict[str, Any], a: Dict[str, Any]) -> bool:
    """attacker_honma_sec=0."""
    return a["attacker_honma_sec"] == 0


def _v_baekho(b: Dict[str, Any], a: Dict[str, Any]) -> bool:
    """cd_baekho > 0 (cooldown 시작)."""
    return a["cd_baekho"] > 0


def _v_parlyuk(b: Dict[str, Any], a: Dict[str, Any]) -> bool:
    """buff_parlyuk_active=True."""
    return bool(a["buff_parlyuk_active"])


def _v_mujang(b: Dict[str, Any], a: Dict[str, Any]) -> bool:
    """attacker_mujang_sec > 0."""
    return a["attacker_mujang_sec"] > 0


def _v_boho(b: Dict[str, Any], a: Dict[str, Any]) -> bool:
    """attacker_boho_sec > 0."""
    return a["attacker_boho_sec"] > 0


def _v_move_direction(b: Dict[str, Any], a: Dict[str, Any]) -> bool:
    """healer_coord 변화 ≥1."""
    bc = b.get("healer_coord")
    ac = a.get("healer_coord")
    if not bc or not ac:
        return False
    try:
        return (bc[0] != ac[0]) or (bc[1] != ac[1])
    except Exception:
        return False


def _v_tab_confirm(b: Dict[str, Any], a: Dict[str, Any]) -> bool:
    """attacker_map_seq 변경."""
    return a["attacker_map_seq"] != b["attacker_map_seq"]


def _v_seq_rclick(b: Dict[str, Any], a: Dict[str, Any]) -> bool:
    """red_tab_present=True after rclick."""
    return bool(a["red_tab_present"])


# action_name → (verifier, deadline_sec)
BUILTIN_VERIFIERS: Dict[str, Tuple[VerifierFn, float]] = {
    "self_heal": (_v_self_heal, 5.0),
    "self_revive": (_v_self_revive, 3.0),
    "attacker_revive": (_v_attacker_revive, 10.0),
    "gyoungryeok": (_v_gyoungryeok, 2.0),
    "parhon": (_v_parhon, 3.0),
    "baekho": (_v_baekho, 1.0),
    "parlyuk": (_v_parlyuk, 1.0),
    "mujang": (_v_mujang, 5.0),
    "boho": (_v_boho, 5.0),
    "move_direction": (_v_move_direction, 1.0),
    "tab_confirm": (_v_tab_confirm, 10.0),
    "seq_rclick": (_v_seq_rclick, 0.5),
    # alias 들 (한국어 / variant)
    "자가부활": (_v_self_revive, 3.0),
    "격수부활": (_v_attacker_revive, 10.0),
    "공력증강": (_v_gyoungryeok, 2.0),
    "파혼술": (_v_parhon, 3.0),
    "백호의희원": (_v_baekho, 1.0),
    "파력무참": (_v_parlyuk, 1.0),
    "무장": (_v_mujang, 5.0),
    "보호": (_v_boho, 5.0),
}


@dataclass
class _Pending:
    action: str
    snap_before: Dict[str, Any]
    started_at: float
    deadline: float  # absolute (monotonic)
    request_ts: float
    detail: str = ""


@dataclass
class OutcomeRecord:
    ts: float
    action: str
    status: str  # ok | fail | timeout | no_effect
    latency_ms: float
    detail: str = ""
    snap_before: Dict[str, Any] = field(default_factory=dict)
    snap_after: Dict[str, Any] = field(default_factory=dict)


class OutcomeVerifier:
    """검증 큐 + 워커 스레드.

    - hand.cast_done 수신 → snap_before(직전 cast 시점) + deadline 으로 enqueue
    - hand.cast_failed → 즉시 fail 기록 + bus emit
    - 워커 스레드 50ms 폴링 → 검증 함수 evaluate. True 면 ok, deadline 만료 시 timeout
      (만료 직전 한번 더 evaluate; False 면 no_effect 분류)
    - 모든 결과는 bus.publish("memory.outcome", OutcomeRecord)
    """

    def __init__(self,
                 store: SnapshotStore,
                 bus: EventBus,
                 verifiers: Optional[Dict[str, Tuple[VerifierFn, float]]] = None,
                 poll_sec: float = 0.05,
                 history_capacity: int = 1024,
                 enabled: bool = True) -> None:
        self.store = store
        self.bus = bus
        self.verifiers: Dict[str, Tuple[VerifierFn, float]] = dict(BUILTIN_VERIFIERS)
        if verifiers:
            self.verifiers.update(verifiers)
        self.poll_sec = float(poll_sec)
        self.enabled = enabled
        self._pending: List[_Pending] = []
        self._lock = threading.Lock()
        self._history: Deque[OutcomeRecord] = deque(maxlen=history_capacity)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._counts: Dict[str, int] = {"ok": 0, "fail": 0, "timeout": 0, "no_effect": 0}

    # ---- public API ----
    def attach(self) -> None:
        self.bus.subscribe("hand.cast_done", self._on_done)
        self.bus.subscribe("hand.cast_failed", self._on_failed)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="outcome_verifier", daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "pending": len(self._pending),
                "history": len(self._history),
                **self._counts,
            }

    def history(self, n: int = 20) -> List[OutcomeRecord]:
        with self._lock:
            return list(self._history)[-n:]

    def submit(self, action: str, detail: str = "") -> None:
        """외부에서 명시적 검증 요청 (non-cast actions: move_direction, tab_confirm)."""
        if not self.enabled:
            return
        v = self.verifiers.get(action)
        if not v:
            return  # unknown
        _, deadline_sec = v
        snap_before = _snap_dict(self.store.read())
        now = time.monotonic()
        with self._lock:
            self._pending.append(_Pending(
                action=action,
                snap_before=snap_before,
                started_at=now,
                deadline=now + deadline_sec,
                request_ts=now,
                detail=detail,
            ))

    # ---- internal ----
    def _on_done(self, evt: Event) -> None:
        if not self.enabled:
            return
        try:
            payload: CastResult = evt.payload
            name = payload.request.name
            v = self.verifiers.get(name)
            if not v:
                return  # 검증 정의 없음 — 조용히 skip
            _, deadline_sec = v
            snap_before = _snap_dict(self.store.read())
            now = time.monotonic()
            with self._lock:
                self._pending.append(_Pending(
                    action=name,
                    snap_before=snap_before,
                    started_at=now,
                    deadline=now + deadline_sec,
                    request_ts=payload.request.requested_at,
                    detail=payload.detail or "",
                ))
        except Exception:
            log.exception("outcome _on_done fail")

    def _on_failed(self, evt: Event) -> None:
        if not self.enabled:
            return
        try:
            payload: CastError = evt.payload
            name = payload.request.name
            now = time.monotonic()
            rec = OutcomeRecord(
                ts=now,
                action=name,
                status="fail",
                latency_ms=(now - payload.request.requested_at) * 1000.0,
                detail=payload.reason,
                snap_before={},
                snap_after=_snap_dict(self.store.read()),
            )
            self._record(rec)
        except Exception:
            log.exception("outcome _on_failed fail")

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_sec):
            try:
                self._tick()
            except Exception:
                log.exception("outcome verifier tick fail")

    def _tick(self) -> None:
        now = time.monotonic()
        snap_after = _snap_dict(self.store.read())
        with self._lock:
            # 검증 시도 + deadline 만료 분리
            still_pending: List[_Pending] = []
            for p in self._pending:
                v = self.verifiers.get(p.action)
                if not v:
                    continue
                fn, _ = v
                ok = False
                try:
                    ok = bool(fn(p.snap_before, snap_after))
                except Exception:
                    log.exception("verifier fn fail action=%s", p.action)
                if ok:
                    rec = OutcomeRecord(
                        ts=now,
                        action=p.action,
                        status="ok",
                        latency_ms=(now - p.started_at) * 1000.0,
                        detail=p.detail,
                        snap_before=p.snap_before,
                        snap_after=snap_after,
                    )
                    self._record_locked(rec)
                elif now >= p.deadline:
                    # 마지막 한 번 더 evaluate 후 분류
                    status = "no_effect"
                    if (now - p.started_at) >= 1.5 * (p.deadline - p.started_at):
                        status = "timeout"
                    rec = OutcomeRecord(
                        ts=now,
                        action=p.action,
                        status=status,
                        latency_ms=(now - p.started_at) * 1000.0,
                        detail=p.detail,
                        snap_before=p.snap_before,
                        snap_after=snap_after,
                    )
                    self._record_locked(rec)
                else:
                    still_pending.append(p)
            self._pending = still_pending

    def _record(self, rec: OutcomeRecord) -> None:
        with self._lock:
            self._record_locked(rec)

    def _record_locked(self, rec: OutcomeRecord) -> None:
        self._history.append(rec)
        self._counts[rec.status] = self._counts.get(rec.status, 0) + 1
        # bus emit (lock 외부에서 publish 하면 중첩 publish 시 데드락 위험 없음)
        try:
            self.bus.publish("memory.outcome", {
                "ts": rec.ts,
                "action": rec.action,
                "status": rec.status,
                "latency_ms": rec.latency_ms,
                "detail": rec.detail,
                "snap_before": rec.snap_before,
                "snap_after": rec.snap_after,
            })
        except Exception:
            log.exception("outcome bus publish fail")
