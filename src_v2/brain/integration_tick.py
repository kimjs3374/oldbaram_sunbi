"""IntegrationTick — v1 healer_worker.run() 메인 루프 핵심 분기 1:1.

v1 SoR (dist_dosa/src/workers/healer_worker.py):
  - 1300-1350    : run() 진입부 + perf 기록
  - 1426-1496    : OCR 결과 수용 (raw_map_text, coord)
  - 1511-1577    : 격수 UDP edge (격수부활/파혼술/무장/보호)
  - 1581-1605    : parlyuk 버프 active → coord_tol=1 강제
  - 1610-1650    : Follower.update + tab_confirm_tick wiring
  - 1691-1738    : TAB-LOCK pending 검사 (snap.tab_lock_pending = True)
  - 1212-1225    : 자힐 EDGE-DEFER + post_self_heal_tab_until 갱신
  - 1798-1830    : 자힐/자가부활 후 15초 윈도 자동 TAB 복귀

이 모듈은 별 thread 가 아니라 healer_worker_v2._tick() 에서 5~10Hz 로 호출.
무거운 작업 없음 (모두 setattr / dict access / 산술).

목표:
  매 호출에서 snap 의 통합 필드 (force_exit_active, f1_pend_active, map_paused,
  tab_lock_pending, post_self_heal_tab_until, parlyuk_buff_active 등) 를 갱신.
  또한 ctx_builder 의 cfg 에 _map_transition_in_progress 동기화.

호출 순서 (healer_worker_v2._tick 안):
  1. follower.update(snap.attacker_state)  → fol.exit_dir / direction()
  2. tab_confirm_tick(now, red_raw, white_raw, h_coord)
  3. integration_tick(...)
  4. rule_engine 은 publish 받아 자체 평가
  5. main_loop muscle 은 별 thread (decide_direction 호출).
"""
from __future__ import annotations
import logging
import time
from typing import Any, Dict, Optional

from ..core.snapshot import Snapshot, SnapshotStore
from ..config import v1_defaults as V1

log = logging.getLogger("src_v2.brain.integration_tick")


# ---------------------------------------------------------------------------
# v1 SoR 상수 (healer_worker.py:198 / 1018 / 2026-04-23 자동 TAB 복귀)
# ---------------------------------------------------------------------------
# 자힐/자가부활 후 자동 TAB 복귀 윈도 (sec) — v1 healer_worker.py:1800.
SELF_HEAL_TAB_RETURN_WINDOW_SEC = 15.0
# 자동 TAB 복귀 거리 임계 (manhattan) — v1 healer_worker.py:1815.
SELF_HEAL_TAB_RETURN_DIST = 5
# 맵 전환 후 자힐 grace (sec) — v1 healer_worker.py:198.
POST_MAPCHG_GRACE_SEC = 5.0
# TAB-LOCK pending 활성 윈도 (sec) — v1 healer_worker.py:1018.
PENDING_TAB_LOCK_SEC = 20.0
# TAB-LOCK 거리 임계 — v1 healer_worker.py:1717.
TAB_LOCK_DIST_THR = 10
# TAB-LOCK 안정화 — v1 healer_worker.py:1719.
TAB_LOCK_STABILIZE_SEC = 0.5


class IntegrationState:
    """통합 tick 의 mutable bag — healer_worker_v2 가 보유."""

    __slots__ = (
        "_attacker_dead_prev", "_attacker_honma_prev",
        "_attacker_mujang_have_prev", "_attacker_boho_have_prev",
        "_self_dead_prev", "_hp_below_thr_prev", "_mp_below_thr_prev",
        "_last_a_map", "_last_h_map",
        "_last_map_change_ts", "_post_mapchg_grace_sec",
        "_pending_tab_lock_until",
        "_post_self_heal_tab_until", "_last_self_heal_ts",
        "_post_self_heal_tab_executed",
        "_parlyuk_buff_active", "_coord_tol_saved",
        "alert_seq",
    )

    def __init__(self) -> None:
        self._attacker_dead_prev = False
        self._attacker_honma_prev = False
        self._attacker_mujang_have_prev = True
        self._attacker_boho_have_prev = True
        self._self_dead_prev = False
        self._hp_below_thr_prev = False
        self._mp_below_thr_prev = False
        self._last_a_map = ""
        self._last_h_map = ""
        self._last_map_change_ts = 0.0
        self._post_mapchg_grace_sec = POST_MAPCHG_GRACE_SEC
        self._pending_tab_lock_until = 0.0
        self._post_self_heal_tab_until = 0.0
        self._last_self_heal_ts = 0.0
        self._post_self_heal_tab_executed = False
        self._parlyuk_buff_active = False
        self._coord_tol_saved: Optional[int] = None
        self.alert_seq = 0


def integration_tick(
    store: SnapshotStore,
    state: IntegrationState,
    follower: Any,
    rule_cfg: Dict[str, Any],
    ctx_extras: Dict[str, Any],
    request_cast: Optional[Any] = None,
    worker_state: Optional[Dict[str, Any]] = None,
    now: Optional[float] = None,
) -> None:
    """v1 healer_worker.run() 매 iter 분기 통합. snap 의 v1 1:1 신호 필드 갱신.

    Args:
      store: SnapshotStore
      state: IntegrationState (worker 가 보유)
      follower: v1 Follower (또는 stub) — update/force_exit_active/is_paused/
                tab_confirm_tick 인터페이스
      rule_cfg: HealerConfig.rule_cfg (in-place 수정 — _map_transition_in_progress)
      ctx_extras: RuleContextBuilder.extras (in-place 수정 — *_prev edge)
      request_cast: callable(name) — sched.request_cast 동치. None 이면 cast 못 함.
      worker_state: SkillExecutor 와 공유하는 dict (_pending_tab_lock_until,
                    _post_self_heal_tab_until 등 sequence/rule 가 read).
      now: time.time() 주입 (테스트용). None 이면 자동.
    """
    now = now or time.time()
    snap = store.read()

    # -----------------------------------------------------------------
    # 1) Follower 미러 — force_exit_active / is_paused / direction
    # -----------------------------------------------------------------
    fea = False
    fed = "-"
    fer = 0.0
    paused = False
    pause_rem = 0.0
    try:
        fea = bool(follower.force_exit_active(now))
    except Exception:
        fea = False
    try:
        fed = str(follower.exit_dir() or "-")
    except Exception:
        fed = "-"
    try:
        fer = float(follower.force_exit_remaining(now))
    except Exception:
        fer = 0.0
    try:
        paused = bool(follower.is_paused(now))
    except Exception:
        paused = False
    try:
        pause_rem = float(follower.pause_remaining(now))
    except Exception:
        pause_rem = 0.0

    # -----------------------------------------------------------------
    # 2) F1-PEND mirror (v1 healer_worker.py:1611-1620 + atk.map_change_pending)
    #    attacker_state.map_change_pending → snap.f1_pend_active.
    # -----------------------------------------------------------------
    f1_pend = False
    atk_state = getattr(snap, "attacker_state", None)
    if atk_state is not None:
        f1_pend = bool(getattr(atk_state, "map_change_pending", False))

    # -----------------------------------------------------------------
    # 3) 격수 UDP edge — 2026-05-05 P1-1 권고안 A: rule layer 단독 책임으로 이관.
    #
    #    이전엔 여기서 격수부활/파혼술/무장/보호 4종을 직접 request_cast 했음.
    #    동시에 brain/rules/{attacker_revive,parhon,mujang,boho}.py 가 같은
    #    edge 로 CastRequest 를 반환 → name(한국어 vs 영어) 차이로 SkillExecutor
    #    in_progress dedup 가 정확히 작동하지 않을 경우 중복 시전 위험.
    #
    #    이 4 기능의 truth 위치를 한 곳(brain/rules/*) 으로 단일화.
    #    integration_tick 은 force_exit/map_pause/parlyuk_tol/post_self_heal_tab
    #    등 v1 1:1 mirror 책임만 유지.
    #
    #    IntegrationState 의 _attacker_dead_prev / _attacker_honma_prev /
    #    _attacker_mujang_have_prev / _attacker_boho_have_prev 필드는 외부
    #    호환을 위해 슬롯만 보존. 본 함수에서 더 이상 갱신/조회하지 않는다
    #    (deprecated, 후속 정리 예정).
    # -----------------------------------------------------------------

    # 3b) 힐러 자체 EDGE (자힐/자가부활/공력증강) 는 brain/rules/self_heal.py /
    #     self_revive.py / gyoungryeok.py 가 RuleEngine 경로로 처리.
    #     integration_tick 은 force_exit/map_pause/tab_lock_pending 등
    #     mirror 전용.

    # -----------------------------------------------------------------
    # 4) parlyuk 버프 active edge (v1 healer_worker.py:1581-1605)
    #    → coord_tol=1 강제, 만료 시 원복.
    # -----------------------------------------------------------------
    # P0-4: coord_tol single source of truth = SnapshotStore.coord_tol_override.
    # rule_cfg dict mutation 폐기. muscle.main_loop 이 store override 우선 read.
    parlyuk_active = bool(getattr(snap, "buff_parlyuk_active", False))
    if parlyuk_active and not state._parlyuk_buff_active:
        state._parlyuk_buff_active = True
        state._coord_tol_saved = int(rule_cfg.get("coord_tol", V1.COORD_TOL_DEFAULT))
        store.update(coord_tol_override=1)
        log.info(
            "[PARLYUK-TOL] 버프 감지 coord_tol %s→1 강제 (store override)",
            state._coord_tol_saved,
        )
    elif not parlyuk_active and state._parlyuk_buff_active:
        state._parlyuk_buff_active = False
        if state._coord_tol_saved is not None:
            store.update(coord_tol_override=-1)
            log.info(
                "[PARLYUK-TOL] 버프 만료 coord_tol 복원 →%s (override 해제)",
                state._coord_tol_saved,
            )
            state._coord_tol_saved = None

    # -----------------------------------------------------------------
    # 5) 맵 변경 감지 → _last_map_change_ts 갱신 (v1 healer_worker.py:1798)
    # -----------------------------------------------------------------
    h_map = str(getattr(snap, "healer_map", "") or "")
    a_map = str(getattr(snap, "attacker_map", "") or "")
    if h_map and h_map != state._last_h_map:
        state._last_map_change_ts = max(state._last_map_change_ts, now)
        state._last_h_map = h_map
        # 자힐 후 TAB 복귀 — 맵 변경되면 reset (이전 윈도 무효)
        state._post_self_heal_tab_executed = False
    if a_map and a_map != state._last_a_map:
        state._last_a_map = a_map

    # 맵 전환 진행 상태 — 자힐/공력증강 EDGE-DEFER 용
    map_neq = bool(h_map and a_map and h_map != a_map)
    map_jump = paused or fea
    map_recent = (now - state._last_map_change_ts) < state._post_mapchg_grace_sec
    rule_cfg["_map_transition_in_progress"] = bool(
        map_neq or map_jump or map_recent
    )

    # -----------------------------------------------------------------
    # 6) TAB-LOCK pending — v1 healer_worker.py:1691-1738
    #    조건: now < pending_until && h_map == a_map &&
    #          manhattan(h, a) <= TAB_LOCK_DIST_THR &&
    #          now - last_map_change_ts >= TAB_LOCK_STABILIZE_SEC.
    #    pending_until 은 맵 변경 edge 시 worker 가 set.
    # -----------------------------------------------------------------
    tab_lock_pending = False
    if now < state._pending_tab_lock_until and h_map and h_map == a_map:
        h = getattr(snap, "healer_coord", None)
        a = getattr(snap, "attacker_coord", None)
        a_valid = bool(getattr(snap, "attacker_coord_valid", False))
        if h is not None and a_valid and a is not None:
            d = abs(h[0] - a[0]) + abs(h[1] - a[1])
            stab = (now - state._last_map_change_ts) >= TAB_LOCK_STABILIZE_SEC
            if d <= TAB_LOCK_DIST_THR and stab:
                tab_lock_pending = True

    # -----------------------------------------------------------------
    # 7) 자힐 EDGE — extras 의 hp_below_thr_prev 와 동기, 자힐 발생 시각
    #    추적 → 15초 윈도 자동 TAB 복귀 (v1 healer_worker.py:1798-1830)
    # -----------------------------------------------------------------
    # 자힐 cast 됐는지는 worker_state 또는 extras["last_self_heal_ts"] 를
    # SkillExecutor 가 set.
    last_heal = float(ctx_extras.get("last_self_heal_ts", 0.0) or 0.0)
    if worker_state is not None:
        ws_last = float(worker_state.get("last_self_heal_ts", 0.0) or 0.0)
        if ws_last > last_heal:
            last_heal = ws_last
            ctx_extras["last_self_heal_ts"] = ws_last
    if last_heal > state._last_self_heal_ts:
        state._last_self_heal_ts = last_heal
        state._post_self_heal_tab_until = last_heal + SELF_HEAL_TAB_RETURN_WINDOW_SEC
        state._post_self_heal_tab_executed = False
        log.info(
            "[POST-HEAL-TAB] 자힐 후 %ds 동안 격수 근접 시 자동 TAB 복귀",
            int(SELF_HEAL_TAB_RETURN_WINDOW_SEC),
        )

    # 자동 TAB 복귀 trigger — 윈도 내 + map_eq + manhattan ≤ 5
    if (request_cast is not None
            and now < state._post_self_heal_tab_until
            and not state._post_self_heal_tab_executed
            and h_map and a_map and h_map == a_map
            and not map_neq):
        h = getattr(snap, "healer_coord", None)
        a = getattr(snap, "attacker_coord", None)
        a_valid = bool(getattr(snap, "attacker_coord_valid", False))
        if h is not None and a_valid and a is not None:
            d = abs(h[0] - a[0]) + abs(h[1] - a[1])
            if d <= SELF_HEAL_TAB_RETURN_DIST:
                try:
                    request_cast("tab_lock")
                    state._post_self_heal_tab_executed = True
                    log.info(
                        "[POST-HEAL-TAB-FIRE] d=%d ≤ %d → tab_lock 시퀀스 요청",
                        d, SELF_HEAL_TAB_RETURN_DIST,
                    )
                except Exception:
                    log.exception("[POST-HEAL-TAB] tab_lock cast fail")

    # -----------------------------------------------------------------
    # 8) worker_state ↔ state 양방향 mirror
    #    - self_heal_seq 가 자힐 종료 시 worker_state['_pending_tab_lock_until']
    #      를 직접 set (sequence 컨텍스트). 이 값이 state 보다 새로우면 state 갱신.
    #    - 이후 tab_lock_pending 게이트 + worker_state 다시 mirror.
    # -----------------------------------------------------------------
    if worker_state is not None:
        ws_pending = float(
            worker_state.get("_pending_tab_lock_until", 0.0) or 0.0
        )
        if ws_pending > state._pending_tab_lock_until:
            state._pending_tab_lock_until = ws_pending
        worker_state["_pending_tab_lock_until"] = state._pending_tab_lock_until
        worker_state["_post_self_heal_tab_until"] = state._post_self_heal_tab_until
        worker_state["_last_map_change_ts"] = state._last_map_change_ts

    # -----------------------------------------------------------------
    # 9) snap 갱신 — atomic setattr (lock-free)
    # -----------------------------------------------------------------
    store.update(
        force_exit_active=fea,
        force_exit_dir=fed,
        force_exit_remaining=fer,
        f1_pend_active=f1_pend,
        map_paused=paused,
        map_pause_remaining=pause_rem,
        tab_lock_pending=tab_lock_pending,
        post_self_heal_tab_until=state._post_self_heal_tab_until,
        last_self_heal_ts=state._last_self_heal_ts,
        parlyuk_buff_active=parlyuk_active,
    )


def arm_tab_lock_pending(state: IntegrationState, now: Optional[float] = None) -> None:
    """맵 edge 감지 시 TAB-LOCK pending 활성. v1 healer_worker.py:1664-1670."""
    now = now or time.time()
    state._pending_tab_lock_until = now + PENDING_TAB_LOCK_SEC
    log.info(
        "[TAB-LOCK-ARM] pending %ds (manhattan ≤ %d, stabilize ≥ %.1fs 후 발동)",
        int(PENDING_TAB_LOCK_SEC), TAB_LOCK_DIST_THR, TAB_LOCK_STABILIZE_SEC,
    )


def set_force_exit(state: IntegrationState, follower: Any,
                   direction: str, duration_sec: float = 1.5) -> None:
    """muscle DecisionState 와 sync 하는 force_exit setter (테스트/외부 호출용).

    Follower 자체 force_exit 가 우선. follower 미지원이면 noop.
    """
    if direction not in ("L", "R", "U", "D"):
        return
    fn = getattr(follower, "set_force_exit", None) or \
         getattr(follower, "arm_force_exit", None)
    if callable(fn):
        try:
            fn(direction, duration_sec)
        except Exception:
            log.exception("follower set_force_exit fail")
