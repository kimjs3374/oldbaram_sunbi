"""Main loop — muscle. Snapshot read + decide_direction + key hold/release.

Strict performance budget: <= 2ms per iteration body (target <= 1ms).
NO ocr/yolo/log f-string/event publish/lock acquisition here.

Design ref: §2.7

decide_direction 은 v1 (dist_dosa/src/workers/healer_worker.py) 의
`_decide_move_raw` + `_apply_stuck_filter` 1:1 이식.

v1 SoR:
  - healer_worker.py:2650-3102 (_decide_move, _decide_move_raw, _apply_stuck_filter)
  - healer_worker.py:2655-2724 (blacklist add/remove/check)

순서:
  FORCE-EXIT → F1-PEND stale → MAP-JUMP-HOLD → B1/B2 trail (map_neq) →
  B3 to_target (격수 뒤 FOLLOW_OFFSET 가상타겟) → B4 a_invalid → B5 a_invalid h=None
  → STUCK-ORTHO1/2 → STUCK-RESET (blacklist add)
"""
from __future__ import annotations
import logging
import threading
import time
from time import perf_counter, sleep
from typing import Optional, List, Tuple, Dict, Any

from ..core.event_bus import EventBus
from ..core.plugin_registry import PluginRegistry
from ..core.snapshot import Snapshot, SnapshotStore
from ..hands.input_dispatcher import InputDispatcher
from ..hands.numlock_cycle import NumlockCycler
from ..config import v1_defaults as V1

log = logging.getLogger("src_v2.muscle")


def _read_win_state():
    """numlock 상태(bool), 힐러 프로세스 fg 여부(bool) read.
    Win32 GetKeyState + GetForegroundWindow. 실패 시 (None, None).
    """
    try:
        import ctypes
        u32 = ctypes.windll.user32
        VK_NUMLOCK = 0x90
        nlock = bool(u32.GetKeyState(VK_NUMLOCK) & 0x0001)
        fg_hwnd = u32.GetForegroundWindow()
        fg_on = bool(fg_hwnd != 0)
        return nlock, fg_on
    except Exception:
        return None, None


# Legacy helper retained for test imports (test_muscle.py imports _step_toward).
def _step_toward(h, a, cfg) -> str:
    """Legacy axis dominant step. v1 ‘B3:to_target’ 의 단순화 버전."""
    if h is None or a is None:
        return "-"
    hx, hy = h
    ax, ay = a
    dx, dy = ax - hx, ay - hy
    tol = int((cfg or {}).get("combat_band",
                              (cfg or {}).get("coord_tol",
                                              V1.COORD_TOL_DEFAULT)))
    if abs(dx) <= tol and abs(dy) <= tol:
        return "-"
    if abs(dx) >= abs(dy):
        return "R" if dx > 0 else "L"
    return "D" if dy > 0 else "U"


# =====================================================================
# Decision state (per loop instance) — kept outside Snapshot (mutable).
# v1 healer_worker._run_want / _run_start_ts / _run_start_pos 와 동치.
# =====================================================================

class DecisionState:
    """Mutable per-loop decision state. v1 self.* 멤버 1:1 매핑.

    Fields:
      run_want / run_start_ts / run_start_pos : STUCK 진행률 추적
      stuck_blacklist : (map, cx, cy, dir) → (expire_ts, hit)
      reset_history   : (map, cx, cy, dir) → last_reset_ts
      prev_atk_coord  : 직전 격수 좌표 (MAP-JUMP 감지용)
      map_jump_hold_until / map_jump_inferred_dir
      last_map_change_ts (자힐 EDGE-DEFER 와 공유)
      stuck_last_log
    """
    __slots__ = (
        "run_want", "run_start_ts", "run_start_pos",
        "stuck_blacklist", "reset_history",
        "prev_atk_coord", "map_jump_hold_until", "map_jump_inferred_dir",
        "last_map_change_ts", "stuck_last_log",
        "force_exit_until", "force_exit_dir",
        "f1_pend_active",  # F1 manual flag (격수 → 힐러 hint)
    )

    def __init__(self):
        self.run_want: Optional[str] = None
        self.run_start_ts: float = 0.0
        self.run_start_pos: Optional[Tuple[int, int]] = None
        self.stuck_blacklist: Dict[Tuple, Tuple[float, int]] = {}
        self.reset_history: Dict[Tuple, float] = {}
        self.prev_atk_coord: Optional[Tuple[int, int]] = None
        self.map_jump_hold_until: float = 0.0
        self.map_jump_inferred_dir: str = "-"
        self.last_map_change_ts: float = 0.0
        self.stuck_last_log: float = 0.0
        self.force_exit_until: float = 0.0
        self.force_exit_dir: str = "-"
        self.f1_pend_active: bool = False


# =====================================================================
# Blacklist helpers (v1 healer_worker.py:2655-2724 1:1)
# =====================================================================

def _bl_cell(coord: Tuple[int, int]) -> Tuple[int, int]:
    return (coord[0] // V1.BL_CELL_GRID, coord[1] // V1.BL_CELL_GRID)


def blacklist_add(state: DecisionState, map_name: str,
                  coord: Tuple[int, int], direction: str) -> None:
    """v1 _blacklist_add 1:1.

    첫 RESET 은 용서 (도사끼리 일시 정체 대응) — BL_FORGIVE_WINDOW_SEC 내 재발 시만 등록.
    """
    if not map_name or coord is None or direction not in ("L", "R", "U", "D"):
        return
    now = time.time()
    cx, cy = _bl_cell(coord)
    key = (map_name, cx, cy, direction)
    # 주변 ±1 블록 이력 조회
    prev_reset = 0.0
    for (m, bx, by, d), ts in state.reset_history.items():
        if m == map_name and d == direction \
                and abs(bx - cx) <= V1.BL_NEIGHBOR_RANGE \
                and abs(by - cy) <= V1.BL_NEIGHBOR_RANGE:
            prev_reset = max(prev_reset, ts)
    # 오래된 이력 정리
    if len(state.reset_history) > 100:
        cutoff = now - V1.BL_FORGIVE_WINDOW_SEC
        state.reset_history = {
            k: v for k, v in state.reset_history.items() if v >= cutoff
        }
    state.reset_history[key] = now
    if now - prev_reset > V1.BL_FORGIVE_WINDOW_SEC:
        # 첫 발생 — 용서 (v1 [BL-SKIP])
        log.info(
            "[BL-SKIP] first offense — map=%r cell=(%d,%d) dir=%s (도사 충돌 가능성)",
            map_name, cx, cy, direction,
        )
        return
    # 10초 내 재발 → 진짜 벽. exponential TTL.
    prev_bl = state.stuck_blacklist.get(key)
    hit = (prev_bl[1] + 1) if prev_bl else 1
    ttl = min(V1.BL_TTL_MAX_SEC, V1.BL_TTL_SEC_BASE * (2 ** (hit - 1)))
    state.stuck_blacklist[key] = (now + ttl, hit)
    log.info(
        "[BL-ADD] map=%r cell=(%d,%d) dir=%s hit=%d ttl=%.0fs (재발 확인)",
        map_name, cx, cy, direction, hit, ttl,
    )


def blacklist_remove_at(state: DecisionState, map_name: str,
                        coord: Tuple[int, int], direction: str) -> None:
    """v1 _blacklist_remove_at 1:1. 진행 감지 시 호출."""
    if not map_name or coord is None or direction not in ("L", "R", "U", "D"):
        return
    cx, cy = _bl_cell(coord)
    for (m, bx, by, d) in list(state.stuck_blacklist.keys()):
        if m == map_name and d == direction \
                and abs(bx - cx) <= V1.BL_NEIGHBOR_RANGE \
                and abs(by - cy) <= V1.BL_NEIGHBOR_RANGE:
            del state.stuck_blacklist[(m, bx, by, d)]
            log.info(
                "[BL-REMOVE] 진행 감지 → 차단 해제 map=%r cell=(%d,%d) dir=%s",
                map_name, bx, by, direction,
            )


def blacklist_check(state: DecisionState, map_name: str,
                    coord: Tuple[int, int], direction: str) -> bool:
    """v1 _blacklist_check 1:1. lazy cleanup 포함."""
    if not map_name or coord is None or direction not in ("L", "R", "U", "D"):
        return False
    now = time.time()
    cx, cy = _bl_cell(coord)
    expired = [k for k, v in state.stuck_blacklist.items() if v[0] <= now]
    for k in expired:
        del state.stuck_blacklist[k]
    for (m, bx, by, d), (exp, _hit) in state.stuck_blacklist.items():
        if m == map_name and d == direction \
                and abs(bx - cx) <= V1.BL_NEIGHBOR_RANGE \
                and abs(by - cy) <= V1.BL_NEIGHBOR_RANGE:
            return True
    return False


# =====================================================================
# B1/B2 trail follow helper (v1 healer_worker.py:2866-2959 1:1)
# =====================================================================

# Module-level diag throttle state — v1 self._last_trail_diag*.
_LAST_TRAIL_DIAG_KEY: Any = None
_LAST_TRAIL_DIAG_TS: float = 0.0


def _b1_b2_trail_follow(snap: Snapshot,
                        state: DecisionState,
                        follower: Any,
                        h: Optional[Tuple[int, int]],
                        h_map: str,
                        a_map: str) -> Tuple[str, str]:
    """v1 healer_worker.py:2866-2959 1:1.

    트레일 정책: 격수 전체 경로 breadcrumb 따라 + exit_dash=True (직선 단축).
    반환된 wp 가 없으면 4단계 폴백 체인:
      1) follower.exit_dir()
      2) follower.direction()
      3) follower.last_seen_in(a_map)[1] (격수 맵 내 격수 최근 방향)
      4) 힐러→last_seen_in(h_map) 좌표 델타
    """
    global _LAST_TRAIL_DIAG_KEY, _LAST_TRAIL_DIAG_TS

    trail_tol = V1.TRAIL_TOL  # 1
    try:
        wp = follower.next_waypoint(
            h_map, h, tol=trail_tol, exit_dash=V1.TRAIL_EXIT_DASH,
        )
    except Exception:  # noqa: BLE001
        wp = None

    # TRAIL-DIAG (v1 2877-2896) — 변경 시 or 0.5s throttle.
    try:
        diag = follower.wp_diag() if hasattr(follower, "wp_diag") else None
    except Exception:  # noqa: BLE001
        diag = None
    if diag is not None:
        now_diag = time.time()
        key = (diag.get("map"), diag.get("cur"), diag.get("wp"),
               diag.get("reason"))
        if (_LAST_TRAIL_DIAG_KEY != key
                or now_diag - _LAST_TRAIL_DIAG_TS >= 0.5):
            _LAST_TRAIL_DIAG_KEY = key
            _LAST_TRAIL_DIAG_TS = now_diag
            log.info(
                "[TRAIL-DIAG] map=%r len=%s cur=%s last_idx=%s wp=%s "
                "h=%s d=%s tol=%d reason=%r tail=%s",
                diag.get("map"), diag.get("len"), diag.get("cur"),
                diag.get("last_idx"), diag.get("wp"), diag.get("h"),
                diag.get("d"), trail_tol, diag.get("reason"), diag.get("tail"),
            )

    # wp 있음 — v1 2897-2913
    if wp is not None:
        (wx, wy), ld = wp
        if h is not None:
            hx, hy = h
            dx, dy = wx - hx, wy - hy
            if dx == 0 and dy == 0:
                # 동일 좌표 — next 호출 시 best_i 진행. 여기선 ld 또는 exit_dir.
                ed = ld if ld in ("L", "R", "U", "D") else _safe_call(follower, "exit_dir")
                w = ed if ed in ("L", "R", "U", "D") else "-"
                return w, f"B1a:TRAIL wp{(wx,wy)}동일 dir={w}"
            if abs(dx) >= abs(dy):
                w = "R" if dx > 0 else "L"
            else:
                w = "D" if dy > 0 else "U"
            return w, f"B1:TRAIL→{(wx,wy)} d=({dx},{dy})"
        # h=None — exit_dir 폴백
        ed = _safe_call(follower, "exit_dir") or "-"
        w = ed if ed in ("L", "R", "U", "D") else "-"
        return w, f"B1b:TRAIL h=None exit_dir={ed!r}"

    # wp 없음 = trail_end → exit_dir 4단계 폴백 체인 (v1 2918-2959)
    ed = _safe_call(follower, "exit_dir") or "-"
    src = "exit_dir"
    w = ed if ed in ("L", "R", "U", "D") else None
    if w is None:
        fd = _safe_call(follower, "direction") or "-"
        if fd in ("L", "R", "U", "D"):
            w, src = fd, "fol.direction"
    if w is None and a_map:
        try:
            ls_a = follower.last_seen_in(a_map)
        except Exception:  # noqa: BLE001
            ls_a = None
        if ls_a is not None and ls_a[1] in ("L", "R", "U", "D"):
            w, src = ls_a[1], f"ls_in({a_map})"
    if w is None and h is not None:
        try:
            ls_h = follower.last_seen_in(h_map)
        except Exception:  # noqa: BLE001
            ls_h = None
        if ls_h is not None:
            (lx, ly), _ = ls_h
            hx, hy = h
            dx, dy = lx - hx, ly - hy
            if abs(dx) >= abs(dy) and dx != 0:
                w, src = ("R" if dx > 0 else "L"), "ls_h_delta"
            elif dy != 0:
                w, src = ("D" if dy > 0 else "U"), "ls_h_delta"
    if w is None:
        w, src = "-", "none"
    return w, (
        f"B2:MAPNEQ exit_dir={ed!r} h={h} trail_end "
        f"fallback={src} dir={w}"
    )


def _safe_call(obj: Any, method: str):
    """Best-effort attribute access — None 반환 시 무시."""
    try:
        f = getattr(obj, method, None)
        if callable(f):
            return f()
    except Exception:  # noqa: BLE001
        pass
    return None


# =====================================================================
# decide_direction — v1 _decide_move_raw + _apply_stuck_filter 1:1
# =====================================================================

def decide_direction(snap: Snapshot,
                     cfg_or_state=None,
                     state_or_cfg=None,
                     move_hint: Optional[str] = None,
                     follower: Any = None):
    """v1 _decide_move 와 동치. 다중 호출 시그니처 지원.

    Forms:
      decide_direction(snap)                              -> str (legacy)
      decide_direction(snap, cfg)                          -> str (legacy)
      decide_direction(snap, state)                        -> (want, reason)
      decide_direction(snap, state, cfg)                   -> (want, reason)
      decide_direction(snap, state, cfg, move_hint)        -> (want, reason)
      decide_direction(snap, state, cfg, move_hint, follower) -> (want, reason)

    follower: brain.follower.Follower (or None). map_neq 시 next_waypoint /
              exit_dir / direction / last_seen_in / wp_diag 사용 — v1 B1/B2
              trail follow 1:1 복원.

    Returns:
      legacy: str
      new: (want, reason) tuple
    """
    # 인자 정규화
    state: Optional[DecisionState] = None
    cfg: Optional[dict] = None
    legacy = False
    if isinstance(cfg_or_state, DecisionState):
        state = cfg_or_state
        cfg = state_or_cfg if isinstance(state_or_cfg, dict) else None
    elif isinstance(cfg_or_state, dict) or cfg_or_state is None:
        # legacy: (snap, cfg)
        cfg = cfg_or_state
        legacy = True
    if state is None:
        state = DecisionState()
        legacy = True
    cfg = cfg or {}

    # 'combat_band' legacy alias → v1 coord_tol
    if "combat_band" in cfg and "coord_tol" not in cfg:
        cfg = dict(cfg)
        cfg["coord_tol"] = cfg["combat_band"]

    raw, raw_reason = _decide_move_raw(snap, state, cfg, move_hint, follower)
    want, reason = _apply_stuck_filter(snap, state, raw, raw_reason)
    if legacy:
        return want
    return want, reason


def _decide_move_raw(snap: Snapshot,
                     state: DecisionState,
                     cfg: dict,
                     move_hint: Optional[str],
                     follower: Any = None) -> Tuple[str, str]:
    """v1 _decide_move_raw 1:1 이식 (healer_worker.py:2827-3102).

    follower: brain.follower.Follower or None.  None이면 attacker_last_dir
    단순 폴백 (legacy 테스트 경로). 정식 wiring 에선 항상 전달.
    """
    # P0-4 (v1_gap_fix_list): single source = snap.coord_tol_override.
    # >=0 이면 강제값 (parlyuk 등), -1 이면 cfg 사용. integration_tick 이 set.
    _ovr = int(getattr(snap, "coord_tol_override", -1))
    tol = _ovr if _ovr >= 0 else int(cfg.get("coord_tol", V1.COORD_TOL_DEFAULT))
    h = snap.healer_coord
    h_map = snap.healer_map or ""
    a = snap.attacker_coord
    a_map = snap.attacker_map or ""
    a_valid = bool(snap.attacker_coord_valid) and a is not None
    map_neq = bool(h_map and a_map and h_map != a_map)

    now = time.time()

    # FORCE-EXIT (v1 healer_worker.py:2845-2852, fol.force_exit_active())
    if now < state.force_exit_until \
            and state.force_exit_dir in ("L", "R", "U", "D"):
        remain = state.force_exit_until - now
        return state.force_exit_dir, (
            f"FORCE-EXIT exit_dir={state.force_exit_dir!r} "
            f"remain={remain:.2f}s (global trail map transition)"
        )

    # NN move_hint (v2 확장 — STUCK detour)
    if move_hint and move_hint in ("U", "D", "L", "R") and not map_neq:
        return move_hint, f"NN-HINT={move_hint}"

    # F1-PEND stale (v1 2858-2864)
    # snap.f1_pend_active 는 integration_tick 이 격수 map_change_pending edge 로 갱신.
    f1_pend = bool(getattr(snap, "f1_pend_active", False)) or state.f1_pend_active
    if f1_pend and not map_neq and h is not None and a_valid:
        d_ha = abs(h[0] - a[0]) + abs(h[1] - a[1])
        if d_ha > V1.F1_PEND_STALE_DIST:
            return "-", (
                f"F1-PEND stale d={d_ha} h={h} a=({a[0]},{a[1]}) STAY"
            )

    # B1/B2: map_neq trail follow (v1 2866-2959)
    # follower 주입 시 v1 1:1 — next_waypoint(exit_dash=True) + 4단계 폴백 체인.
    if map_neq:
        if follower is not None:
            return _b1_b2_trail_follow(snap, state, follower, h, h_map, a_map)
        # follower 없는 legacy 경로 — attacker_last_dir 단순 폴백.
        ed = (snap.attacker_last_dir or "-")
        if ed in ("L", "R", "U", "D"):
            return ed, f"B2:MAPNEQ exit_dir={ed!r} h={h}"
        return "-", f"B2:MAPNEQ no_exit h={h} a_map={a_map!r}"

    # MAP-JUMP-HOLD (v1 2967-3022): 격수 좌표 점프 ≥ 8 (다른 맵일 때만)
    if h is not None and a_valid:
        prev_a = state.prev_atk_coord
        if prev_a is not None:
            jump = abs(a[0] - prev_a[0]) + abs(a[1] - prev_a[1])
            same_map = bool(h_map and a_map and h_map == a_map)
            if jump >= V1.MAP_JUMP_THRESHOLD and not same_map:
                px, py = prev_a
                inferred_ed = "-"
                if px >= V1.EXIT_BOUNDARY_R:
                    inferred_ed = "R"
                elif px <= V1.EXIT_BOUNDARY_L:
                    inferred_ed = "L"
                elif py >= V1.EXIT_BOUNDARY_D:
                    inferred_ed = "D"
                elif py <= V1.EXIT_BOUNDARY_U:
                    inferred_ed = "U"
                state.map_jump_hold_until = now + V1.MAP_JUMP_HOLD_SEC
                state.map_jump_inferred_dir = inferred_ed
                state.last_map_change_ts = max(
                    state.last_map_change_ts, now
                )
                log.warning(
                    "[MAP-JUMP] 격수 좌표 %s→(%d,%d) d=%d "
                    "inferred_exit=%r %.1fs hold.",
                    prev_a, a[0], a[1], jump, inferred_ed,
                    V1.MAP_JUMP_HOLD_SEC,
                )
        state.prev_atk_coord = (a[0], a[1])
        if now < state.map_jump_hold_until:
            ed = state.map_jump_inferred_dir
            if ed in ("L", "R", "U", "D"):
                return ed, (
                    f"MAP-JUMP-HOLD exit_dir={ed!r} "
                    f"remain={(state.map_jump_hold_until-now):.2f}s"
                )
            return "-", (
                f"MAP-JUMP-HOLD STAY "
                f"remain={(state.map_jump_hold_until-now):.2f}s"
            )

    # B3: 같은 맵 + 양쪽 좌표 OK → 격수 뒤 FOLLOW_OFFSET 가상 타겟
    # (v1 3023-3091)
    if h is not None and a_valid:
        hx, hy = h
        ax, ay = a
        ald = snap.attacker_last_dir or "-"
        if ald == "R":
            tx, ty = ax - V1.FOLLOW_OFFSET, ay
        elif ald == "L":
            tx, ty = ax + V1.FOLLOW_OFFSET, ay
        elif ald == "D":
            tx, ty = ax, ay - V1.FOLLOW_OFFSET
        elif ald == "U":
            tx, ty = ax, ay + V1.FOLLOW_OFFSET
        else:
            tx, ty = ax, ay
        tdx, tdy = tx - hx, ty - hy
        if abs(tdx) <= tol and abs(tdy) <= tol:
            return "-", (
                f"B3a:at_target d_t=({tdx},{tdy}) h={h} "
                f"t=({tx},{ty}) a=({ax},{ay}) atk_dir={ald}"
            )
        x_dir = "R" if tdx > 0 else ("L" if tdx < 0 else None)
        y_dir = "D" if tdy > 0 else ("U" if tdy < 0 else None)
        if abs(tdx) >= abs(tdy):
            first, second = x_dir, y_dir
        else:
            first, second = y_dir, x_dir
        reverse_map = {"L": "R", "R": "L", "U": "D", "D": "U"}
        bl_first = bool(first) and blacklist_check(state, h_map, h, first)
        bl_second = bool(second) and blacklist_check(state, h_map, h, second)
        chosen = None
        tag = "B3:to_target"
        if first and not bl_first:
            chosen = first
        elif second and not bl_second:
            chosen = second
            tag = "B3:to_target_BL-DETOUR"
        elif first and second and bl_first and bl_second:
            for c in (reverse_map[first], reverse_map[second]):
                if not blacklist_check(state, h_map, h, c):
                    chosen = c
                    tag = "B3:to_target_BL-RETREAT"
                    break
        if chosen is None:
            return "-", (
                f"B3:to_target_BL-STALL d_t=({tdx},{tdy}) h={h} "
                f"t=({tx},{ty}) a=({ax},{ay}) atk_dir={ald}"
            )
        return chosen, (
            f"{tag} d_t=({tdx},{tdy}) h={h} "
            f"t=({tx},{ty}) a=({ax},{ay}) atk_dir={ald}"
        )

    # B4: 힐러 좌표 없음 + 격수 좌표 OK
    if h is None and a_valid:
        d = snap.attacker_last_dir or "-"
        w = d if d in ("L", "R", "U", "D") else "-"
        return w, f"B4:h=None 맹목→격수방향={d!r} a=({a[0]},{a[1]})"

    # B5: 격수 좌표 무효
    d = snap.attacker_last_dir or "-"
    w = d if d in ("L", "R", "U", "D") else "-"
    return w, f"B5:a_invalid 맹목→last_dir={d!r} h={h}"


def _apply_stuck_filter(snap: Snapshot,
                        state: DecisionState,
                        want: str,
                        reason: str) -> Tuple[str, str]:
    """v1 _apply_stuck_filter 1:1 (healer_worker.py:2726-2825)."""
    now = time.time()
    h = snap.healer_coord
    h_map = snap.healer_map or ""

    # 흰탭 confirm 중이면 STUCK 스킵 (v1 2741-2746)
    whitetab = bool(getattr(snap, "white_tab_present", False))
    if h is None or want not in ("L", "R", "U", "D") or whitetab:
        state.run_want = None
        state.run_start_ts = 0.0
        state.run_start_pos = None
        return want, reason

    hx, hy = h
    if state.run_want != want or state.run_start_pos is None:
        state.run_want = want
        state.run_start_ts = now
        state.run_start_pos = (hx, hy)
        return want, reason

    bx, by = state.run_start_pos
    manhattan_delta = abs(hx - bx) + abs(hy - by)
    if manhattan_delta >= V1.STUCK_RESET_MANHATTAN_DELTA:
        blacklist_remove_at(state, h_map, (hx, hy), want)
        state.run_start_ts = now
        state.run_start_pos = (hx, hy)
        return want, reason

    # 주축 진행 체크
    if want in ("L", "R"):
        delta = hx - bx
        expected = 1 if want == "R" else -1
    else:
        delta = hy - by
        expected = 1 if want == "D" else -1
    progress = delta * expected
    if progress > 0:
        blacklist_remove_at(state, h_map, (hx, hy), want)
        state.run_start_ts = now
        state.run_start_pos = (hx, hy)
        return want, reason

    dur = now - state.run_start_ts
    if dur < V1.STUCK_NORMAL_MAX_SEC:
        return want, reason

    # 직교축 결정 (v1 2782-2797)
    a = snap.attacker_coord
    a_valid = bool(snap.attacker_coord_valid) and a is not None
    if want in ("L", "R"):
        if a_valid and a[1] != hy:
            ortho1 = "D" if a[1] > hy else "U"
        else:
            ortho1 = "U"
        ortho2 = "D" if ortho1 == "U" else "U"
    else:
        if a_valid and a[0] != hx:
            ortho1 = "R" if a[0] > hx else "L"
        else:
            ortho1 = "R"
        ortho2 = "L" if ortho1 == "R" else "R"

    # v1 2798-2805: STUCK warning throttle
    if now - state.stuck_last_log >= V1.STUCK_LOG_THROTTLE_SEC:
        state.stuck_last_log = now
        log.warning(
            "[STUCK] dur=%.1fs h=%s blocked=%s base=%s atk=%s a_valid=%s "
            "ortho1=%s ortho2=%s",
            dur, h, want, state.run_start_pos,
            (a if a_valid else None), a_valid, ortho1, ortho2,
        )

    if dur < V1.STUCK_ORTHO1_MAX_SEC:
        return ortho1, (
            f"STUCK-ORTHO1 dur={dur:.1f}s h={h} "
            f"blocked={want} try={ortho1}"
        )
    if dur < V1.STUCK_ORTHO2_MAX_SEC:
        return ortho2, (
            f"STUCK-ORTHO2 dur={dur:.1f}s h={h} "
            f"blocked={want} try={ortho2}"
        )
    # RESET → blacklist add (3.5s 초과)
    blacklist_add(state, h_map, h, want)
    state.run_want = None
    state.run_start_ts = 0.0
    state.run_start_pos = None
    return "-", (
        f"STUCK-RESET dur={dur:.1f}s h={h} blocked={want} → BL-ADD "
        f"map={h_map!r} ttl={V1.BL_TTL_SEC_BASE:.0f}s"
    )


# =====================================================================
# Main loop runner
# =====================================================================

class MainLoop(threading.Thread):
    """Runs the muscle loop in its own thread.

    Per-iter work:
      1. snapshot read (lock-free)
      2. decide_direction (pure)
      3. dispatcher.set_direction (only on change)
      4. numlock cycler tick
    """

    def __init__(self,
                 store: SnapshotStore,
                 dispatcher: InputDispatcher,
                 cycler: Optional[NumlockCycler] = None,
                 cfg: Optional[dict] = None,
                 hz_cap: int = 200,
                 follower: Any = None) -> None:
        super().__init__(daemon=True, name="muscle_main")
        self.store = store
        self.dispatcher = dispatcher
        self.cycler = cycler or NumlockCycler()
        # 2026-04-27 BUG-FIX: dict copy 폐기, ref 공유. integration_tick 가
        # rule_cfg["coord_tol"]=1 갱신 시 muscle 즉시 반영 (이전엔 별도 dict
        # 라 영원 default). v1 → v2 전환 시 누락된 ref 공유 (audit 5.1).
        self.cfg = cfg if cfg is not None else {}
        self.target_dt = 1.0 / max(1, hz_cap)
        self._stop_evt = threading.Event()
        self._iter_count = 0
        self._last_dir: str = "-"
        self._last_reason: str = ""
        self._dec_state = DecisionState()
        self.follower = follower  # v1 1:1 — B1/B2 trail follow + 폴백 체인
        self._perf_window: List[float] = []
        self._perf_max = 256
        # v1 1:1: movement_lock True→False edge 시 즉시 재hold (다음 iter 강제).
        # v1 healer_worker.py:1519-1523 의 "_need_rehold" 와 동치.
        self._need_rehold: bool = False
        try:
            dispatcher.set_on_lock_release(self._on_lock_release)
        except Exception:  # noqa: BLE001
            pass

    def _on_lock_release(self) -> None:
        """movement_lock 해제 edge — 다음 iter 에 강제 재hold."""
        self._need_rehold = True
        log.info("[LOCK-RELEASED] movement_lock 해제 → 재hold 예약")

    def _reset_stuck_state(self) -> None:
        """v1 _apply_stuck_filter:2743-2745 1:1 — want='-' 강제 시 STUCK 추적 reset."""
        self._dec_state.run_want = None
        self._dec_state.run_start_ts = 0.0
        self._dec_state.run_start_pos = None

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_evt.set()
        if self.is_alive():
            self.join(timeout=timeout)

    def run(self) -> None:
        log.info("muscle main_loop start")
        while not self._stop_evt.is_set():
            t0 = perf_counter()
            # v1 1:1: 매 iter 시작에서 movement_lock 10초 stuck 체크 (강제 해제).
            try:
                self.dispatcher.check_movement_lock_stuck()
            except Exception:  # noqa: BLE001
                pass
            snap = self.store.read()

            # v1 1:1 게이트 (healer_worker.py:1890-2093):
            #   1) movement_lock (자힐 SEQ-A/B) → want="-" + STUCK 추적 skip
            #   2) 흰탭 white_tab_present → want="-" + release_all
            #   3) is_paused (TAB-CONFIRM/MAP-PAUSE/MAP-SYNC) → want="-"
            #   4) armed=False → want="-"
            #   5) 평소: decide_direction
            armed = bool(getattr(snap, "armed", True))
            white_raw = bool(getattr(snap, "white_tab_present", False))
            mv_locked = False
            try:
                mv_locked = self.dispatcher.is_movement_locked()
            except Exception:  # noqa: BLE001
                mv_locked = False
            paused = False
            if self.follower is not None:
                try:
                    paused = bool(self.follower.is_paused())
                except Exception:  # noqa: BLE001
                    paused = False

            if not armed:
                want, reason = "-", "ARMED-OFF"
                self._reset_stuck_state()
            elif mv_locked:
                want, reason = "-", "SEQ-AB-LOCK"
                self._reset_stuck_state()
                # 자힐 중에도 _prev_atk_coord 는 갱신 (v1 healer_worker.py:2089-2090).
                a = snap.attacker_coord
                if bool(snap.attacker_coord_valid) and a is not None:
                    self._dec_state.prev_atk_coord = (a[0], a[1])
            elif white_raw:
                # 흰탭 → 즉시 release. ARM/Tab 송신은 tab_confirm_driver 가 수행.
                want, reason = "-", "WHITETAB-BLOCK"
                self._reset_stuck_state()
            elif paused:
                want, reason = "-", "MAP-PAUSE"
                self._reset_stuck_state()
            else:
                res = decide_direction(
                    snap, self._dec_state, self.cfg,
                    follower=self.follower,
                )
                want, reason = res if isinstance(res, tuple) else (res, "")

            # v1 1:1: lock 해제 edge → want 동일해도 강제 재hold.
            if self._need_rehold and want in ("L", "R", "U", "D"):
                self._last_dir = "-"  # force diff → re-press.
                self._need_rehold = False
            if want != self._last_dir:
                self.dispatcher.set_direction(want)
                self._last_dir = want
                self._last_reason = reason
            self.cycler.tick(t0)

            self._iter_count += 1
            elapsed = perf_counter() - t0
            self._perf_window.append(elapsed * 1000.0)
            if len(self._perf_window) > self._perf_max:
                self._perf_window.pop(0)

            # 텔레메트리 — UI publisher 가 직접 read 하는 필드들을 매 iter 갱신.
            # 가벼운 setattr 만 (lock 없음, 1us 미만).
            try:
                # FSM 상태 — map_neq / force_exit / 정상에 따라 단순 분류.
                fsm = "FOLLOW"
                if self._dec_state.force_exit_until > time.time():
                    fsm = "FORCE_EXIT"
                elif snap.healer_map and snap.attacker_map \
                        and snap.healer_map != snap.attacker_map:
                    fsm = "MAP_NEQ"
                # numlock / hwnd_fg 은 비싸므로 1Hz 만.
                update_kw = {
                    "fsm_state": fsm,
                    "current_dir": self._last_dir,
                    "want_dir": want,
                    "move_reason": reason or self._last_reason,
                }
                if (self._iter_count & 0x7F) == 0:  # ~매 128 iter (200Hz → 1.5Hz)
                    nlock_on, fg_on = _read_win_state()
                    if nlock_on is not None:
                        update_kw["numlock_on"] = nlock_on
                    if fg_on is not None:
                        update_kw["hwnd_fg"] = fg_on
                # FPS 는 capture watcher 가 frame 캡처 간격으로 채움 (v1 의미).
                self.store.update(**update_kw)
            except Exception:  # noqa: BLE001
                pass

            rem = self.target_dt - elapsed
            if rem > 0:
                sleep(rem)
        log.info("muscle main_loop stop iter=%d", self._iter_count)

    def stats(self) -> dict:
        if self._perf_window:
            avg = sum(self._perf_window) / len(self._perf_window)
            mx = max(self._perf_window)
        else:
            avg = mx = 0.0
        return {
            "iter_count": self._iter_count,
            "last_dir": self._last_dir,
            "last_reason": self._last_reason,
            "avg_ms": round(avg, 3),
            "max_ms": round(mx, 3),
            "alive": self.is_alive(),
        }
