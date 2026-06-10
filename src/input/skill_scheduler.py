"""조건부 스킬 스케줄러 엔진 (v4, 2초 홀드 + OCR 검증 + 재시도).

스킬 *선언* 은 `skill_blueprints.py` 로 분리됨. 이 파일은 엔진만.

시전 방식 (v4, 사용자 결정 2026-04-19):
- 매 쿨 만료 시 `_press_burst`로 ~2초간 반복 press.
- burst 후 `verify_wait_sec`만큼 대기 → OCR 캐시 조회.
  - verify_kind="buff" → ctx["buffs"][name] > 0 이면 성공.
  - verify_kind="cooldown" → ctx["cooldowns"][name] > 0 이면 성공.
  - verify_kind=None → 검증 스킵 (파혼술처럼 predicate 재평가로 루프).
- 실패 시 `retry_max` 회 재burst. 모두 실패하면 last_cast 를 현재 시각으로
  두어 다음 쿨까지 넘어감 (무한 루프 방지).
"""
import threading
import time
from collections import deque
from typing import Callable, Optional

from .numlock_cycle import press_normal_vk
# 하위 호환 re-export: 기존 `from ..input.skill_scheduler import SkillSpec,
# default_skills` 를 쓰는 코드 (healer_worker 등) 를 깨지 않기 위함.
from .skill_blueprints import (  # noqa: F401
    SkillSpec,
    default_skills,
    _cd_empty,
    _buff_present,
)


class SkillScheduler(threading.Thread):
    """쿨 기반 스킬 시전 스레드 (v4, burst + OCR 검증 + retry).

    cast_fn(vk) 인터페이스는 단발 press 래퍼로 유지 (하위 호환). burst 는
    내부 루프가 직접 cast_fn 을 반복 호출.
    """

    def __init__(self,
                 cast_fn: Optional[Callable[[int], None]] = None,
                 ctx_provider: Callable[[], dict] = None,
                 skills: Optional[list] = None,
                 poll_sec: float = 0.1,
                 start_delay_sec: float = 1.0):
        super().__init__(daemon=True)
        # cast_fn은 하위 호환. None이면 press_normal_vk 사용.
        self.cast_fn = cast_fn or (lambda vk: press_normal_vk(vk))
        self.ctx_provider = ctx_provider or (lambda: {})
        self.skills = skills if skills is not None else default_skills()
        # 2026-04-20 리뉴얼: 폴링 간격 0.5 → 0.1. 더 빠른 반응.
        # HpMpReader 가 _tick_event 로 즉시 깨우므로 사실상 OCR 지연 수준.
        self.poll_sec = poll_sec
        self.start_delay_sec = start_delay_sec  # 게임 창 포커스 시간 확보.
        self.armed = False
        self._stop = False
        self._start_time = time.time()
        self._log_fn: Optional[Callable[[str], None]] = None
        # HpMpReader.on_update / 기타 구독자가 notify_tick() 으로 깨울 수
        # 있는 이벤트. run() 루프가 sleep 대신 wait 를 사용하므로 OCR 이
        # 새 값 올린 순간 predicate 가 바로 평가됨.
        self._tick_event = threading.Event()
        # 2026-04-20 Patch 2.14: blocks_movement 스킬 시전 중 상태.
        # 외부(예: healer_worker)가 _on_busy_change 콜백으로 keys.set_movement_lock
        # 토글하도록 연결. A+B 시퀀스 진행 중 방향키 press 가 TAB/HOME 과
        # 섞이는 문제 차단.
        self._busy_blocking: bool = False
        self._on_busy_change: Optional[Callable[[bool], None]] = None
        # edge-triggered 시전 요청 큐. 외부(예: worker) 가 OCR 값 변화 edge
        # 감지 시 request_cast(name) 호출. run() 루프가 polling 하기 전에
        # 큐를 먼저 비워 즉시 시전. 같은 스킬 중복 요청은 무시.
        self._cast_queue: deque = deque()
        self._queue_lock = threading.Lock()
        # 2026-04-21: 시전 준비 게이트. () -> bool. False 반환 동안엔 tick
        # 루프가 큐/polling 모두 skip (요청은 큐에 쌓여둠). NumLockCycler 초기
        # lock 완료 전 cast 가 NumPad scan 과 경쟁해 봉황 토글 꼬이는 문제
        # 해결용.
        self._ready_gate: Optional[Callable[[], bool]] = None

    def notify_tick(self) -> None:
        """외부에서 즉시 tick 을 깨울 때 사용 (예: HpMpReader 새 값 갱신)."""
        self._tick_event.set()

    def _should_yield_to_edge(self) -> bool:
        """retry 루프 안에서 edge 큐에 요청이 쌓여있는지 확인.
        True 면 현재 시전 즉시 중단하고 edge 우선 처리하도록 함.
        """
        with self._queue_lock:
            return len(self._cast_queue) > 0

    def set_ready_gate(self, fn: Callable[[], bool]) -> None:
        """시전 허용 여부 체크 함수. False 반환 동안엔 모든 시전 유예.
        예: NumLockCycler 초기 lock 완료 체크.
        """
        self._ready_gate = fn

    def _is_ready(self) -> bool:
        if self._ready_gate is None:
            return True
        try:
            return bool(self._ready_gate())
        except Exception:
            return True

    def request_cast(self, name: str) -> None:
        """외부에서 edge-triggered 시전 요청. 이미 큐에 있으면 무시.

        scheduler 는 다음 tick 에 큐를 우선 비우고 해당 SkillSpec 을 1회 시전.
        enabled=False 인 스킬은 실제 시전 단계에서 skip. busy 상태면 대기 후
        처리.
        """
        with self._queue_lock:
            if name in self._cast_queue:
                return
            self._cast_queue.append(name)
        self._tick_event.set()

    def set_on_busy_change(self, cb: Callable[[bool], None]) -> None:
        """blocks_movement 스킬 진입/종료 시 호출될 콜백.

        cb(True)  — 이동 잠금 필요 (자힐/자가부활 A+B 시작).
        cb(False) — 이동 잠금 해제 (A+B 종료).
        """
        self._on_busy_change = cb

    def is_blocking(self) -> bool:
        return self._busy_blocking

    def set_armed(self, on: bool):
        self.armed = on

    def set_log(self, fn):
        self._log_fn = fn

    def stop(self):
        self._stop = True
        # 이벤트 깨워 즉시 종료.
        try:
            self._tick_event.set()
        except Exception:
            pass
        # 이동 잠금 잔존 방지. scheduler 종료 시 반드시 lock 해제.
        if self._busy_blocking and self._on_busy_change is not None:
            try:
                self._on_busy_change(False)
            except Exception:
                pass
        self._busy_blocking = False

    def _log(self, s: str):
        if self._log_fn:
            try:
                self._log_fn(s)
            except Exception:
                pass

    def _press_burst(self, vk: int, sec: float, interval: float):
        """vk 를 `sec`초 동안 `interval`초 간격으로 반복 press."""
        t_end = time.time() + max(0.05, float(sec))
        while time.time() < t_end:
            if self._stop or not self.armed:
                return
            try:
                self.cast_fn(vk)
            except Exception as e:
                self._log(f"[SKILL] cast 예외 vk={hex(vk)}: {e}")
            time.sleep(max(0.02, float(interval)))

    def _verify(self, sk: SkillSpec) -> bool:
        """verify_wait_sec 만큼 대기 후 최신 ctx 로부터 OCR 결과 조회.

        verify_kind 가 None 이면 항상 True (= 검증 스킵).
        """
        if not sk.verify_kind:
            return True
        time.sleep(max(0.1, float(sk.verify_wait_sec)))
        try:
            ctx = dict(self.ctx_provider() or {})
        except Exception:
            ctx = {}
        key = sk.verify_target or sk.name
        if sk.verify_kind == "buff":
            pool = ctx.get("buffs") or {}
        else:
            pool = ctx.get("cooldowns") or {}
        try:
            val = int(pool.get(key, -1))
        except Exception:
            val = -1
        # -1 (미확인) 은 실패로 간주 — retry 유도.
        ok = val > 0
        self._log(
            f"[SKILL] verify {sk.name} kind={sk.verify_kind} key={key} "
            f"val={val} ok={ok}"
        )
        return ok

    def _run_hook(self, hook, ctx: dict, label: str) -> None:
        """pre_block / post_block 훅 안전 실행.

        예외는 잡아서 로그만. 스킬 시전 자체는 계속 진행.
        """
        if hook is None:
            return
        try:
            self._log(f"[SKILL] {label} 훅 실행")
            hook(ctx)
        except Exception as e:
            self._log(f"[SKILL] {label} 훅 예외: {e}")

    def _cast_with_retry(self, sk: SkillSpec):
        """burst + verify 시도. 성공 시 last_cast 갱신.

        - retry_until_ready=True: 쿨 잡힐 때까지 무한 반복. 실패해도 last_cast
          안 찍음 → 다음 poll 에 즉시 재시도 (ready 판정은 last_cast==0 이라 True).
          armed off / stop 시 중단.
        - 기본: retry_max 회 시도. 전부 실패 시 last_cast=now (180s 드러누움).
        - pre_block: burst 전 실행 (예: 자힐→블록A self-target).
        - post_block: 성공/실패와 무관하게 마지막에 실행 (예: 자힐→블록B).
        - blocks_movement=True: 시전 구간 전체를 _busy_blocking 으로 감싸서
          외부(healer_worker)가 방향키 press 를 잠그도록 콜백 호출.
          공력증강/파력무참처럼 VK 만 쓰는 스킬은 False → 이동 잠금 없음.
        """
        lock_on = bool(getattr(sk, "blocks_movement", False))
        if lock_on:
            self._busy_blocking = True
            if self._on_busy_change is not None:
                try:
                    self._on_busy_change(True)
                except Exception as e:
                    self._log(f"[SKILL] busy_change(True) 예외: {e}")
        try:
            # pre_block 훅. burst 전에 self-target 등 준비 작업.
            try:
                ctx_hook = dict(self.ctx_provider() or {})
            except Exception:
                ctx_hook = {}
            self._run_hook(sk.pre_block, ctx_hook, f"{sk.name} pre")
            # burst_sec<=0: pre_block (예: A+B 통합 훅) 이 전체 시퀀스 담당.
            # main burst/verify 스킵 → post_block 만 호출하고 쿨 기록.
            if sk.burst_sec <= 0:
                sk.last_cast = time.time()
                self._log(
                    f"[SKILL] {sk.name} hook-only (burst_sec=0) "
                    f"cool={sk.cooldown_sec:.0f}s"
                )
                try:
                    ctx_post = dict(self.ctx_provider() or {})
                except Exception:
                    ctx_post = {}
                self._run_hook(sk.post_block, ctx_post, f"{sk.name} post")
                return
            if sk.retry_until_ready:
                attempt = 0
                # MAX_UNTIL_READY: verify 영원 실패 (OCR 영역 문제 등) 시 포기 한계.
                # 초과하면 last_cast 를 현재 시각으로 찍어 cooldown_sec(5s) 만큼
                # 대기 후 재진입 허용. 정상 OCR 이면 1~2회로 verify 통과하므로
                # 3회면 충분. burst 2s + verify 2s ≈ 4s/try * 3 = 최대 12초 블로킹
                # (edge 스킬 들어오면 yield 로 즉시 해제).
                MAX_UNTIL_READY = 3
                while not self._stop and self.armed:
                    # (A) edge 큐 yield: 자힐/공증 등 edge 요청이 쌓이면 즉시 반환.
                    # last_cast 는 갱신하지 않아 다음 tick 에서 predicate 재평가로
                    # 재진입. blocks_movement 스킬이 아니라 이동 영향도 없음.
                    if self._should_yield_to_edge():
                        self._log(
                            f"[SKILL] {sk.name} yield → edge 요청 우선 "
                            f"(try={attempt})"
                        )
                        try:
                            ctx_post = dict(self.ctx_provider() or {})
                        except Exception:
                            ctx_post = {}
                        self._run_hook(sk.post_block, ctx_post, f"{sk.name} post(yield)")
                        return
                    attempt += 1
                    self._log(
                        f"[SKILL] cast {sk.name} try={attempt} (until_ready) "
                        f"vk={hex(sk.vk)} burst={sk.burst_sec:.1f}s"
                    )
                    self._press_burst(sk.vk, sk.burst_sec, sk.burst_interval_sec)
                    if self._verify(sk):
                        sk.last_cast = time.time()
                        self._log(
                            f"[SKILL] OK {sk.name} cool={sk.cooldown_sec:.0f}s "
                            f"after {attempt} tries"
                        )
                        # post_block 훅 (정상 완료).
                        try:
                            ctx_post = dict(self.ctx_provider() or {})
                        except Exception:
                            ctx_post = {}
                        self._run_hook(sk.post_block, ctx_post, f"{sk.name} post")
                        return
                    # (B) N회 시도 후 포기. OCR 영역/게임 상태 문제일 가능성.
                    if attempt >= MAX_UNTIL_READY:
                        sk.last_cast = time.time()
                        self._log(
                            f"[SKILL] GIVEUP {sk.name} after {attempt} tries "
                            f"(MAX_UNTIL_READY={MAX_UNTIL_READY}, "
                            f"backoff={sk.cooldown_sec:.0f}s)"
                        )
                        try:
                            ctx_post = dict(self.ctx_provider() or {})
                        except Exception:
                            ctx_post = {}
                        self._run_hook(sk.post_block, ctx_post, f"{sk.name} post(giveup)")
                        return
                    self._log(
                        f"[SKILL] retry {sk.name} — 쿨 미관측 "
                        f"({attempt}/{MAX_UNTIL_READY})"
                    )
                # stop/disarm 로 빠져나온 경우에도 post_block 호출 (자힐 중단 등).
                try:
                    ctx_post = dict(self.ctx_provider() or {})
                except Exception:
                    ctx_post = {}
                self._run_hook(sk.post_block, ctx_post, f"{sk.name} post(abort)")
                return
            tries = max(1, int(sk.retry_max))
            success = False
            # edge_only 스킬은 edge 큐 경로로 진입했으므로 yield 불필요.
            # polling 진입 스킬(백호 등) 만 yield 대상. 2026-04-21 수정:
            # 이전엔 파혼술(edge_only) 도 yield 해서 try=1/1 에서 GIVEUP 됐음.
            _yield_allowed = not getattr(sk, "edge_only", False)
            for i in range(tries):
                if self._stop or not self.armed:
                    break
                if _yield_allowed and self._should_yield_to_edge():
                    self._log(
                        f"[SKILL] {sk.name} yield → edge 요청 우선 "
                        f"(try={i + 1}/{tries})"
                    )
                    break
                self._log(
                    f"[SKILL] cast {sk.name} try={i + 1}/{tries} "
                    f"vk={hex(sk.vk)} burst={sk.burst_sec:.1f}s"
                )
                self._press_burst(sk.vk, sk.burst_sec, sk.burst_interval_sec)
                if self._verify(sk):
                    sk.last_cast = time.time()
                    self._log(
                        f"[SKILL] OK {sk.name} cool={sk.cooldown_sec:.0f}s"
                    )
                    success = True
                    break
                self._log(f"[SKILL] retry {sk.name} — 검증 실패")
            if not success:
                sk.last_cast = time.time()
                self._log(f"[SKILL] GIVEUP {sk.name} after {tries} tries")
            # post_block 훅 (성공/실패 무관). 자힐 → 블록B 복귀 필수.
            try:
                ctx_post = dict(self.ctx_provider() or {})
            except Exception:
                ctx_post = {}
            self._run_hook(sk.post_block, ctx_post, f"{sk.name} post")
        finally:
            if lock_on:
                self._busy_blocking = False
                if self._on_busy_change is not None:
                    try:
                        self._on_busy_change(False)
                    except Exception as e:
                        self._log(f"[SKILL] busy_change(False) 예외: {e}")

    def run(self):
        # 시작 딜레이: 게임 창 포커스 옮길 시간 확보.
        if self.start_delay_sec > 0:
            self._log(
                f"[SKILL] 시작 딜레이 {self.start_delay_sec:.1f}s"
            )
            t_end = time.time() + self.start_delay_sec
            while time.time() < t_end and not self._stop:
                time.sleep(0.1)
            if self._stop:
                return
            self._start_time = time.time()  # 오프셋 기준 재설정.
            self._log("[SKILL] 딜레이 종료 (v4 burst + verify)")
        try:
            while not self._stop:
                # sleep 대신 event.wait: 외부 push(notify_tick) 오면 즉시
                # 깨어나 predicate 평가. timeout 은 기존 polling 간격 유지.
                self._tick_event.wait(self.poll_sec)
                self._tick_event.clear()
                if self._stop:
                    break
                if not self.armed:
                    continue
                # 2026-04-21: cycler 초기 lock 등 준비 완료 전엔 시전 유예.
                # 큐는 건드리지 않고 다음 tick 대기 — ready_gate 가 True
                # 반환하는 순간 쌓여있던 요청이 순차 처리됨.
                if not self._is_ready():
                    continue
                # ── edge-triggered 큐 우선 처리 ─────────────────────────
                # 외부(worker)가 OCR 값 변화 edge 감지 시 request_cast(name)
                # 로 쌓아둠. polling 보다 우선 시전. cooldown/ready 체크 없이
                # 호출자가 감지한 edge 자체를 신뢰. 단 enabled=False 면 skip.
                pending: list = []
                with self._queue_lock:
                    while self._cast_queue:
                        pending.append(self._cast_queue.popleft())
                for name in pending:
                    if self._stop or not self.armed:
                        break
                    sk = next((s for s in self.skills if s.name == name), None)
                    if sk is None or not sk.enabled:
                        continue
                    self._log(f"[SKILL] edge-cast {name}")
                    try:
                        self._cast_with_retry(sk)
                    except Exception as e:
                        self._log(f"[SKILL] edge-cast {name} 예외: {e}")
                    time.sleep(0.3)
                if self._stop or not self.armed:
                    continue
                # ── polling fallback (edge 놓쳤을 때) ───────────────────
                now = time.time()
                ctx = dict(self.ctx_provider() or {})
                ctx.setdefault("start_time", self._start_time)
                # 2026-04-20: priority 오름차순 정렬.
                # 자가부활(0) > 격수부활(1) > 자힐(2) > 공력증강(3) > 기타.
                # 한 tick 안에서 매번 정렬 (cheap, N<=10). skills 리스트 직접
                # 재배치는 하지 않음 (외부 참조 유지 위해 로컬 복사본).
                ordered = sorted(
                    list(self.skills),
                    key=lambda s: int(getattr(s, "priority", 10)),
                )
                for sk in ordered:
                    # edge_only 스킬은 polling 경로에서 skip (request_cast 로만 시전).
                    if getattr(sk, "edge_only", False):
                        continue
                    if self._stop or not self.armed:
                        break
                    if sk.ready(now, ctx):
                        try:
                            self._cast_with_retry(sk)
                        except Exception as e:
                            self._log(
                                f"[SKILL] {sk.name} 시전 실패: {e}"
                            )
                        # 스킬 간 간격 확보 (연속 burst 금지).
                        time.sleep(0.3)
                        # 부활 계열 시전 후 다음 tick 에 ctx 재평가 — 우선순위
                        # 체인 끊기 (자가부활 → 자힐 → 기타 순으로 퍼지).
                        break
        except Exception:
            pass


