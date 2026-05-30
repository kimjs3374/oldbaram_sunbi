"""Skill executor — consumes CastRequest queue, runs sequence plugins.

Design ref: §2.6

v1 SoR 1:1 보강 (2026-04-25):
  - ready_gate: cycler 초기 lock 완료 전 시전 유예 (skill_scheduler.py:89-101).
  - verify: burst 후 ctx 풀(buffs/cooldowns) 조회로 성공 판정
            (skill_scheduler.py:167-194).
  - retry: 검증 실패 시 retry_max 회 재시도, retry_until_ready 시 영원 재시도.
  - busy_change: 이미 movement_lock 으로 처리됨 (input_dispatcher).
  - dedup: 동일 name 큐 중복 방지 (skill_scheduler.py:103-114).

verify/retry 옵션은 CastRequest.ctx 에 실어 전달:
  ctx["verify_kind"]    : "buff" | "cooldown" | None
  ctx["verify_target"]  : str — ctx["buffs"]/["cooldowns"] 키 (default name).
  ctx["verify_wait_sec"]: float — burst 후 OCR 반영 대기.
  ctx["retry_max"]      : int — 기본 1.
  ctx["retry_until_ready"]: bool — 기본 False. True 면 MAX_UNTIL_READY 회까지.
  ctx["_ctx_provider"]  : () -> dict — verify 시 호출, ctx 풀 조회.
"""
from __future__ import annotations
import logging
import queue
import threading
import time
from typing import Callable, Optional

from ..core.event_bus import EventBus
from ..core.plugin_registry import PluginRegistry
from ..core.types import CastRequest, CastResult, CastError
from .input_dispatcher import InputDispatcher
from ..config import v1_defaults as V1

log = logging.getLogger("src_v2.hands.executor")

_STOP = object()
# v1 skill_scheduler.py:258 — retry_until_ready 모드 무한 루프 차단.
MAX_UNTIL_READY = 3


class HandsAPI:
    """Thin facade exposed to brain — push CastRequest into the priority queue."""

    def __init__(self, q: "queue.PriorityQueue", dispatcher: InputDispatcher,
                 dedup: bool = False):
        self._q = q
        self.dispatcher = dispatcher
        self._counter = 0  # tie-breaker for equal priorities (FIFO)
        self._lock = threading.Lock()
        # v1 SkillScheduler.request_cast 의 dedup (skill_scheduler.py:111-112).
        # default False — 기존 테스트(같은 name 5회 enqueue) 호환. brain 룰
        # 엔진이 enable_dedup() 호출 시 활성. v1 healer 동작은 항상 dedup.
        self._dedup_enabled: bool = bool(dedup)
        self._pending_names: set = set()

    def enable_dedup(self, on: bool = True) -> None:
        """v1 동치 dedup 모드 토글. healer 워커 wiring 시 호출."""
        with self._lock:
            self._dedup_enabled = bool(on)

    def request_cast(self, req: CastRequest) -> None:
        with self._lock:
            if self._dedup_enabled and req.name in self._pending_names:
                # v1 skill_scheduler.py:111-112: 중복 요청 무시.
                log.debug("dedup: %s already pending", req.name)
                return
            self._counter += 1
            tie = self._counter
            if self._dedup_enabled:
                self._pending_names.add(req.name)
        # PriorityQueue compares tuples; CastRequest is frozen so tie needed
        self._q.put((req.priority, tie, req))

    def _release_pending(self, name: str) -> None:
        """Executor 가 큐에서 꺼낸 직후 호출 — 같은 스킬 재요청 허용."""
        with self._lock:
            if self._dedup_enabled:
                self._pending_names.discard(name)

    def queue_size(self) -> int:
        return self._q.qsize()


class SkillExecutor(threading.Thread):
    """Background worker — pulls CastRequest from queue, runs sequence."""

    def __init__(self,
                 q: "queue.PriorityQueue",
                 bus: EventBus,
                 dispatcher: InputDispatcher,
                 in_progress: Optional[set] = None,
                 worker_state: Optional[dict] = None,
                 cycler: Optional[object] = None,
                 hpmp_adapter: Optional[object] = None,
                 chat_adapter: Optional[object] = None,
                 hands_api: Optional["HandsAPI"] = None,
                 ctx_provider: Optional[Callable[[], dict]] = None,
                 ready_gate: Optional[Callable[[], bool]] = None,
                 log_callback: Optional[Callable[[str], None]] = None):
        super().__init__(daemon=True, name="hands_dispatch")
        self.q = q
        self.bus = bus
        self.dispatcher = dispatcher
        self.in_progress: set = in_progress if in_progress is not None else set()
        self.worker_state: dict = worker_state if worker_state is not None else {}
        self.cycler = cycler
        if hpmp_adapter is not None:
            self.worker_state.setdefault("_hpmp_adapter", hpmp_adapter)
        if chat_adapter is not None:
            self.worker_state.setdefault("_chat_adapter", chat_adapter)
        # 큐 dedup 해제용 — HandsAPI 주입 시 _release_pending 호출.
        self.hands_api = hands_api
        # verify 시 ctx 풀 조회용 () -> dict. None 이면 verify 강제 성공.
        self._ctx_provider = ctx_provider
        # 시전 허용 체크 () -> bool. False 면 큐 항목 다시 push 후 sleep.
        self._ready_gate = ready_gate
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._cast_count = 0
        self._fail_count = 0
        # 진단 로그 [HANDS] sequence start/done.
        self._log_emit = log_callback if callable(log_callback) else None
        self._first_seq_logged: set = set()

    def _emit(self, s: str) -> None:
        if self._log_emit:
            try:
                self._log_emit(s)
            except Exception:
                pass

    def set_ready_gate(self, fn: Callable[[], bool]) -> None:
        """v1 SkillScheduler.set_ready_gate 1:1.

        False 반환 동안엔 큐 처리를 유예 (cycler 초기 lock 완료 등).
        """
        self._ready_gate = fn

    def set_ctx_provider(self, fn: Callable[[], dict]) -> None:
        """v1 SkillScheduler.ctx_provider 1:1. verify 시 ctx 풀 조회."""
        self._ctx_provider = fn

    def _is_ready(self) -> bool:
        if self._ready_gate is None:
            return True
        try:
            return bool(self._ready_gate())
        except Exception:
            return True

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_evt.set()
        self.q.put((-1, -1, _STOP))  # type: ignore
        if self.is_alive():
            self.join(timeout=timeout)

    def run(self) -> None:
        log.info("hands executor start")
        while not self._stop_evt.is_set():
            try:
                priority, tie, item = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is _STOP:
                break
            req: CastRequest = item
            # v1 ready_gate: 시전 유예. 큐 항목 다시 push 후 100ms sleep.
            # 같은 스킬 무한 재push 방지 위해 dedup set 은 살려둠 (재push 시
            # 내부 직접 put 사용해 _pending_names 그대로 유지).
            if not self._is_ready():
                # priority/tie 보존하며 다시 큐에 넣기. _pending_names 는 손대지 않음.
                self.q.put((priority, tie, req))
                time.sleep(0.1)
                continue
            # 큐에서 꺼낸 시점부터 같은 스킬 재요청 허용.
            if self.hands_api is not None:
                try:
                    self.hands_api._release_pending(req.name)
                except Exception:
                    pass
            self._handle(req)
        log.info("hands executor stop count=%d fail=%d", self._cast_count, self._fail_count)

    def _verify(self, req: CastRequest) -> bool:
        """v1 SkillScheduler._verify 1:1.

        verify_kind 이 None 이면 항상 True (검증 스킵).
        verify_wait_sec 만큼 대기 후 ctx_provider 로 ctx 받아 풀 조회:
          - "buff"     → ctx["buffs"][key] > 0 이면 성공.
          - "cooldown" → ctx["cooldowns"][key] > 0 이면 성공 (=쿨 관측됨).
        """
        ctx = req.ctx or {}
        kind = ctx.get("verify_kind")
        if not kind:
            return True
        wait = float(ctx.get("verify_wait_sec", 2.0))
        if wait > 0:
            time.sleep(max(0.1, wait))
        if self._ctx_provider is None:
            # v1 에서는 ctx_provider 가 항상 존재 — 없으면 검증 불가 → 실패.
            log.warning("verify %s skipped — no ctx_provider", req.name)
            return False
        try:
            pool_root = dict(self._ctx_provider() or {})
        except Exception:
            pool_root = {}
        key = ctx.get("verify_target") or req.name
        if kind == "buff":
            pool = pool_root.get("buffs") or {}
        else:
            pool = pool_root.get("cooldowns") or {}
        try:
            val = int(pool.get(key, -1))
        except Exception:
            val = -1
        ok = val > 0
        log.debug("verify %s kind=%s key=%s val=%d ok=%s",
                  req.name, kind, key, val, ok)
        return ok

    def _handle(self, req: CastRequest) -> None:
        seq_fn = PluginRegistry.get_sequence(req.name)
        if seq_fn is None:
            self._fail_count += 1
            self._emit(f"[HANDS] sequence MISSING name={req.name} — PluginRegistry 미등록")
            self.bus.publish("hand.cast_failed", CastError(req, "no_sequence"))
            return
        # 첫 호출 1회만 진단 emit (반복 burst 시 스팸 방지).
        if req.name not in self._first_seq_logged:
            self._first_seq_logged.add(req.name)
            self._emit(
                f"[HANDS] sequence start name={req.name} priority={req.priority}"
            )
        # v1 1:1: blocks_movement 인 시퀀스는 시작 직전 movement_lock=True →
        # 종료 후 False. 자힐/자가부활/격수부활 SEQ-A 진행 중 방향키 차단.
        blocks_movement = req.name in V1.BLOCKS_MOVEMENT_SEQUENCES
        if blocks_movement:
            try:
                self.dispatcher.set_movement_lock(True)
            except Exception:  # noqa: BLE001
                log.exception("set_movement_lock(True) fail")
        # mark in-progress
        with self._lock:
            self.in_progress.add(req.name)
        t0 = time.monotonic()
        # v1 retry/verify 옵션 추출.
        ctx0 = req.ctx or {}
        retry_until_ready = bool(ctx0.get("retry_until_ready", False))
        retry_max = max(1, int(ctx0.get("retry_max", 1)))
        verify_kind = ctx0.get("verify_kind")
        try:
            success = False
            last_err: Optional[BaseException] = None
            if retry_until_ready:
                # v1 skill_scheduler.py:251-317: MAX_UNTIL_READY 회까지 burst+verify.
                tries_cap = MAX_UNTIL_READY
            else:
                tries_cap = retry_max
            for attempt in range(1, tries_cap + 1):
                if self._stop_evt.is_set():
                    break
                ctx = dict(ctx0)
                ctx.setdefault("_dispatcher", self.dispatcher)
                ctx.setdefault("_request", req)
                ctx.setdefault("_worker_state", self.worker_state)
                if self.cycler is not None:
                    ctx.setdefault("_cycler", self.cycler)
                ctx["_attempt"] = attempt
                try:
                    seq_fn(ctx)
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    log.exception("seq %s try=%d failed: %s",
                                  req.name, attempt, e)
                    # 시퀀스 자체 예외는 retry 대상 (verify_kind 무관).
                    continue
                # verify_kind 가 없으면 1회로 종료. 있으면 검증 후 재시도.
                if not verify_kind:
                    success = True
                    break
                if self._verify(req):
                    success = True
                    log.info("verify ok %s after %d tries", req.name, attempt)
                    break
                log.debug("verify fail %s try=%d/%d", req.name, attempt, tries_cap)
            if success:
                self._cast_count += 1
                # v1 1:1: 자힐/자가부활 종료 ts 기록 → integration_tick 가 15초
                # 윈도 자동 TAB 복귀 트리거에 사용 (healer_worker.py:1798).
                try:
                    if req.name in ("self_heal", "self_revive"):
                        self.worker_state["last_self_heal_ts"] = time.time()
                except Exception:
                    pass
                latency_ms = (time.monotonic() - t0) * 1000.0
                # 진단 — 첫 1회 또는 cast_count 25회 단위로 emit (스팸 방지).
                if (req.name in self._first_seq_logged
                        and (self._cast_count <= 3 or self._cast_count % 25 == 0)):
                    self._emit(
                        f"[HANDS] sequence done name={req.name} "
                        f"latency_ms={latency_ms:.0f} cast_count={self._cast_count}"
                    )
                self.bus.publish(
                    "hand.cast_done",
                    CastResult(request=req, status="ok",
                               detail=f"latency_ms={latency_ms:.1f}"),
                )
            else:
                self._fail_count += 1
                reason = "verify_giveup" if verify_kind else (
                    str(last_err) if last_err else "exhausted"
                )
                log.warning("seq %s GIVEUP after %d tries reason=%s",
                            req.name, tries_cap, reason)
                self._emit(
                    f"[HANDS] sequence GIVEUP name={req.name} "
                    f"tries={tries_cap} reason={reason}"
                )
                self.bus.publish("hand.cast_failed", CastError(req, reason))
        finally:
            with self._lock:
                self.in_progress.discard(req.name)
            # 시퀀스 종료 — movement_lock 해제 (lock True→False edge → 재hold).
            if blocks_movement:
                try:
                    self.dispatcher.set_movement_lock(False)
                except Exception:  # noqa: BLE001
                    log.exception("set_movement_lock(False) fail")

    def stats(self):
        return {
            "cast_count": self._cast_count,
            "fail_count": self._fail_count,
            "queue_size": self.q.qsize(),
            "in_progress": list(self.in_progress),
        }
