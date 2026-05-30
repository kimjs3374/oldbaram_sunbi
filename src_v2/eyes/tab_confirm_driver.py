"""TAB-CONFIRM driver — Follower.tab_confirm_tick + WHITETAB-ARM gate.

v1 SoR (dist_dosa/src/workers/healer_worker.py):
  - 1606-1620 : note_healer_map / fol.update 호출
  - 1622-1662 : tab_confirm_tick 호출
  - 2008-2080 : 흰탭 streak 갱신 + ARM gate (Route A 단일)

ARM gate (v1 2055-2070):
    confirm >= 3
    AND not red_raw            (빨탭 공존 시 Tab 재송신 금지)
    AND not tab_confirm_active (이미 진행 중)
    AND atk.coord_valid        (격수 좌표 유효)
    AND fsm not in {DEAD, DISCONNECTED}
    AND not follow_only        (follow_only 모드는 Tab 송신 금지)

깜빡임 흡수: 마지막 감지 후 250ms 내 다시 보이면 streak 유지.
break 시 confirm reset.
"""
from __future__ import annotations
import logging
import time
from typing import Any, Optional, Tuple

from ..core.snapshot import SnapshotStore

log = logging.getLogger("src_v2.eyes.tab_confirm_driver")


# 흰탭 깜빡임 gap 흡수 윈도 (sec) — v1 healer_worker.py:2027 (250ms).
WHITETAB_GAP_WINDOW_SEC = 0.25
# ARM 도달 임계 streak (프레임) — v1 healer_worker.py:2055.
WHITETAB_CONFIRM_STREAK = 3
# 2026-05-02 false-arm 차단: ARM 직전 N ms 동안 red_tab 미검출 필요.
# 사냥 중 = red 거의 항상 검출 → 차단. 진짜 맵 이동 = 격수 사라짐 → 통과.
# 시작값 250ms. BLOCK 분포 P95+50ms 로 2라운드 조정 예정.
RED_QUIET_MS = 250.0
# BLOCK 로그 throttle — 매 틱 찍으면 폭주.
BLOCK_LOG_THROTTLE_SEC = 0.5


class TabConfirmState:
    """v1 healer_worker self._whitetab_* 1:1."""

    __slots__ = (
        "_whitetab_confirm",
        "_whitetab_seen_ts",
        "_last_map_neq",
        "_last_attacker_map_seq",
        "_last_arm_log_ts",
        "_red_seen_ts",
        "_last_block_log_ts",
    )

    def __init__(self) -> None:
        self._whitetab_confirm = 0
        self._whitetab_seen_ts: float = 0.0
        self._last_map_neq: bool = False
        self._last_attacker_map_seq: int = 0
        self._last_arm_log_ts: float = 0.0
        self._red_seen_ts: float = 0.0
        self._last_block_log_ts: float = 0.0


def tab_confirm_tick(
    store: SnapshotStore,
    state: TabConfirmState,
    follower: Any,
    now: Optional[float] = None,
    log_emit: Optional[Any] = None,
) -> str:
    """v1 1:1 — 흰탭 streak + ARM gate + Follower.tab_confirm_tick 위임.

    Returns: tab_confirm_tick 의 phase 문자열.
    """
    now = now or time.time()
    snap = store.read()

    # red/white raw 신호
    red_raw = bool(getattr(snap, "red_tab_present", False))
    white_raw = bool(getattr(snap, "white_tab_present", False))
    h_coord: Optional[Tuple[int, int]] = getattr(snap, "healer_coord", None)
    h_map = str(getattr(snap, "healer_map", "") or "")
    a_map = str(getattr(snap, "attacker_map", "") or "")
    a_seq = int(getattr(snap, "attacker_map_seq", 0) or 0)
    map_neq = bool(h_map and a_map and h_map != a_map)

    # note_healer_map (force_exit 계산에 healer_map 주입)
    try:
        follower.note_healer_map(h_map, h_coord)
    except Exception:  # noqa: BLE001
        pass

    # ----------------------------------------------------------------
    # 흰탭 streak 갱신 (v1 healer_worker.py:1997-2033 1:1)
    # ----------------------------------------------------------------
    tab_active = False
    try:
        tab_active = bool(getattr(follower, "_tab_confirm_active", False))
    except Exception:  # noqa: BLE001
        tab_active = False

    if tab_active:
        # ARM 진행 중엔 confirm 누적 금지 (v1 1997-1999).
        state._whitetab_confirm = 0
        state._whitetab_seen_ts = 0.0
    elif white_raw:
        if (now - state._whitetab_seen_ts) > WHITETAB_GAP_WINDOW_SEC:
            state._whitetab_confirm = 1
        else:
            state._whitetab_confirm += 1
        state._whitetab_seen_ts = now
    else:
        # 250ms gap 이내는 유지, 초과 시 reset (v1 2030-2033).
        if (state._whitetab_confirm > 0
                and (now - state._whitetab_seen_ts) > WHITETAB_GAP_WINDOW_SEC):
            state._whitetab_confirm = 0
            state._whitetab_seen_ts = 0.0

    # ----------------------------------------------------------------
    # ARM gate (v1 2055-2070 1:1)
    # ----------------------------------------------------------------
    a_state = getattr(snap, "attacker_state", None)
    a_coord_valid = False
    if a_state is not None:
        a_coord_valid = bool(getattr(a_state, "coord_valid", False))
    else:
        a_coord_valid = bool(getattr(snap, "attacker_coord_valid", False))
    follow_only = bool(getattr(snap, "follow_only", False))
    fsm_state = str(getattr(snap, "fsm_state", "FOLLOW") or "FOLLOW")
    arm_ok_fsm = fsm_state not in ("DEAD", "DISCONNECTED")

    state._last_map_neq = map_neq
    state._last_attacker_map_seq = a_seq

    esc_suppress_until = float(getattr(snap, "esc_suppress_tab_until", 0.0) or 0.0)

    # 2026-05-02 red_quiet 게이트: red_raw True 면 _red_seen_ts 갱신.
    # ARM 직전 RED_QUIET_MS 이내 red 검출 시 차단 (사냥 중 false-arm 방지).
    if red_raw:
        state._red_seen_ts = now
    red_quiet_ms = (now - state._red_seen_ts) * 1000.0
    red_quiet_ok = red_quiet_ms >= RED_QUIET_MS

    base_gate_ok = (
        state._whitetab_confirm >= WHITETAB_CONFIRM_STREAK
        and not red_raw
        and not tab_active
        and a_coord_valid
        and arm_ok_fsm
        and not follow_only
        and now >= esc_suppress_until
    )

    if base_gate_ok and red_quiet_ok:
        try:
            follower.arm_tab_confirm(now, map_neq=map_neq)
        except Exception:  # noqa: BLE001
            pass
        msg = (
            f"[WHITETAB-ARM] confirm={state._whitetab_confirm} "
            f"red_quiet_ms={red_quiet_ms:.0f} "
            f"map_neq={map_neq} fsm={fsm_state} "
            f"h_map={h_map!r} a_map={a_map!r}"
        )
        if log_emit is not None and (now - state._last_arm_log_ts) >= 0.5:
            try:
                log_emit(msg)
            except Exception:  # noqa: BLE001
                pass
            state._last_arm_log_ts = now
        else:
            log.info(msg)
        state._whitetab_confirm = 0  # ARM 후 reset
    elif base_gate_ok and not red_quiet_ok:
        # red_quiet 만 부족해서 차단된 케이스 — false-arm 시도 추적.
        msg = (
            f"[WHITETAB-BLOCK] reason=red_quiet ms={red_quiet_ms:.0f} "
            f"confirm={state._whitetab_confirm} "
            f"map_neq={map_neq} fsm={fsm_state}"
        )
        if (now - state._last_block_log_ts) >= BLOCK_LOG_THROTTLE_SEC:
            if log_emit is not None:
                try:
                    log_emit(msg)
                except Exception:  # noqa: BLE001
                    log.info(msg)
            else:
                log.info(msg)
            state._last_block_log_ts = now

    # tab_confirm_tick 위임 (Home → Tab → A_wait_red → done_ok)
    phase = "inactive"
    try:
        phase = str(follower.tab_confirm_tick(
            now, red_raw, white_raw, h_coord,
        ) or "inactive")
    except Exception:  # noqa: BLE001
        phase = "inactive"

    # ARM 진행 중일 때만 phase 처리 — send_home/send_tab 시 실제 키 송신.
    # v1 healer_worker.py:1893-1957 1:1.
    if phase in ("send_home", "send_tab", "done_ok", "retry_arm",
                 "done_timeout"):
        try:
            _handle_tab_phase(phase, follower, log_emit)
        except Exception:  # noqa: BLE001
            log.exception("tab_phase handle fail phase=%s", phase)

    return phase


def _handle_tab_phase(phase: str, follower: Any,
                      log_emit: Optional[Any] = None) -> None:
    """phase 별 키 송신 / 로그 — v1 healer_worker.py:1898-1957 1:1.

    keybd_event 는 FG 창에만 들어감. ARM 시 follow_only=False AND
    arm_ok_fsm 로 게이트했으므로 여기선 무조건 송신.
    """
    import ctypes
    import time as _t

    def _emit(msg: str) -> None:
        if log_emit is not None:
            try:
                log_emit(msg)
                return
            except Exception:  # noqa: BLE001
                pass
        log.info(msg)

    if phase == "send_home":
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            HOME_VK, HOME_SCAN = 0x24, 0x47
            user32.keybd_event(HOME_VK, HOME_SCAN, 0x0001, 0)
            _t.sleep(0.04)
            user32.keybd_event(HOME_VK, HOME_SCAN, 0x0001 | 0x0002, 0)
            _emit(f"[TAB-CONFIRM-HOME] sub={follower._tab_confirm_substate!r}")
        except Exception as e:  # noqa: BLE001
            _emit(f"[TAB-CONFIRM-HOME] 실패: {e}")
    elif phase == "send_tab":
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            TAB_VK = 0x09
            user32.keybd_event(TAB_VK, 0x0F, 0, 0)
            _t.sleep(0.04)
            user32.keybd_event(TAB_VK, 0x0F, 0x0002, 0)
            _emit(
                f"[TAB-CONFIRM-TAB] sub={follower._tab_confirm_substate!r} "
                f"retry={follower._tab_retry_count}"
            )
        except Exception as e:  # noqa: BLE001
            _emit(f"[TAB-CONFIRM-TAB] 실패: {e}")
    elif phase == "done_ok":
        try:
            now = _t.time()
            elapsed = now - follower._tab_confirm_started
            follower.note_tab_done_ok(now)
            _emit(
                f"[TAB-CONFIRM-DONE] 복귀 확정 elapsed={elapsed*1000:.0f}ms "
                f"route=A retry={follower._tab_retry_count}"
            )
        except Exception:  # noqa: BLE001
            pass
    elif phase == "retry_arm":
        _emit(
            f"[TAB-CONFIRM-RETRY] hard timeout → Tab 재arm "
            f"count={follower._tab_retry_count}/{follower._tab_retry_max}"
        )
    elif phase == "done_timeout":
        try:
            elapsed = _t.time() - follower._tab_confirm_started
        except Exception:  # noqa: BLE001
            elapsed = 0.0
        _emit(
            f"[TAB-CONFIRM-TIMEOUT] {elapsed:.1f}s 내 복귀 실패 → 일반 follow 복귀"
        )


def note_tab_fg_mismatch(follower: Any, now: Optional[float] = None) -> bool:
    """헬퍼 — fg 미스매치 시 호출."""
    now = now or time.time()
    try:
        return bool(follower.note_tab_fg_mismatch(now))
    except Exception:
        return False


def note_tab_done_ok(follower: Any, now: Optional[float] = None) -> None:
    try:
        follower.note_tab_done_ok(now or time.time())
    except Exception:
        pass
