"""Follower (FSM controller) — v1 1:1 native 이식.

v1 SoR:
  - dist_dosa/src/fsm/controller.py (949 LOC)
  - dist_dosa/src/fsm/tab_confirm.py (207 LOC)
  - dist_dosa/src/fsm/state.py (FsmState enum)

v2 native 구현으로 v1 import 의존 제거. v1 동작 보존:
  - TAB-CONFIRM Route A (Tab → A_wait_red → done_ok, hard timeout 10s)
  - MAP-PAUSE (MAP-SEQ-EDGE 0.5s + MAP-SYNC 1.5s 확장)
  - fresh_map_guard (TTL + threshold)
  - jump_reject_threshold / fresh_reject_threshold (좌표 점프 필터)
  - reversion_debounce (3프레임 확인)
  - exit_dash (map_neq 시 zigzag skip)
  - snap-forward (가장 가까운 idx)
  - next_waypoint (단조 진행 + tol)
  - update / force_exit_active / force_exit_remaining / exit_dir / exit_coord / exit_map
  - is_paused / last_seen_in / direction
  - trail_for / trail_tail / progress_of / _hist_direction

State 변환: src_v2.AttackerState ↔ v1 net.protocol.State 어댑터 (adapt_state).

설계 원칙: src/, dist_dosa/src/ 코드 수정 금지. 본 파일은 동작을 1:1
재구현하므로 v1 import 불필요. v1 변경 시 본 파일과 v1_defaults.py 동시 갱신.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from enum import Enum
from typing import Any, Optional, Tuple

from ..config import v1_defaults as V1

log = logging.getLogger("src_v2.brain.follower")


# =====================================================================
# FsmState — v1 fsm/state.py 1:1
# =====================================================================
class FsmState(Enum):
    IDLE = "IDLE"
    FOLLOW = "FOLLOW"
    COMBAT = "COMBAT"
    EMERGENCY = "EMERGENCY"
    MAP_CHANGE = "MAP_CHANGE"
    ENTER_PORTAL = "ENTER_PORTAL"
    LOADING = "LOADING"
    NEW_MAP = "NEW_MAP"
    STUCK = "STUCK"
    DEAD = "DEAD"
    DISCONNECTED = "DISCONNECTED"
    DEAD_RECKON = "DEAD_RECKON"


# =====================================================================
# _State — v1 net.protocol.State 와 동일 필드 (src_v2 AttackerState 어댑터용)
# =====================================================================
class _State:
    __slots__ = (
        "x", "y", "coord_valid", "map_name", "map_seq", "last_dir",
        "hp_pct", "debuff_honmasul_sec", "buff_mujang_sec", "buff_boho_sec",
        "red_tab", "seq",
    )

    def __init__(self) -> None:
        self.x: int = 0
        self.y: int = 0
        self.coord_valid: bool = False
        self.map_name: str = ""
        self.map_seq: int = 0
        self.last_dir: str = "-"
        self.hp_pct: int = -1
        self.debuff_honmasul_sec: int = -1
        self.buff_mujang_sec: int = -1
        self.buff_boho_sec: int = -1
        self.red_tab: bool = False
        self.seq: int = 1


def adapt_state(snap_attacker_state: Any) -> _State:
    """src_v2 AttackerState/UDP payload → _State.

    snap_attacker_state 는 dataclass / dict / 이미 _State 모두 지원.
    """
    if isinstance(snap_attacker_state, _State):
        return snap_attacker_state

    s = _State()
    if snap_attacker_state is None:
        return s

    def _g(name: str, default=None):
        if hasattr(snap_attacker_state, name):
            return getattr(snap_attacker_state, name, default)
        if isinstance(snap_attacker_state, dict):
            return snap_attacker_state.get(name, default)
        return default

    coord = _g("coord")
    if coord is not None:
        try:
            s.x, s.y = int(coord[0]), int(coord[1])
        except Exception:
            pass
    s.coord_valid = bool(_g("coord_valid", False))
    s.map_name = str(_g("map_name", "") or "")
    s.map_seq = int(_g("map_seq", 0) or 0)
    s.last_dir = str(_g("last_dir", "-") or "-")
    s.hp_pct = int(_g("hp", _g("hp_pct", -1)))
    s.debuff_honmasul_sec = int(_g("honma_sec", _g("debuff_honmasul_sec", -1)))
    s.buff_mujang_sec = int(_g("mujang_sec", _g("buff_mujang_sec", -1)))
    s.buff_boho_sec = int(_g("boho_sec", _g("buff_boho_sec", -1)))
    s.red_tab = bool(_g("red_tab", False))
    s.seq = int(_g("seq", 1) or 1)
    return s


# =====================================================================
# TabConfirm — v1 fsm/tab_confirm.py 1:1
# =====================================================================
class TabConfirm:
    """흰탭 → Tab → red&!white 확정 FSM. v1 Route A 단일."""

    def __init__(
        self,
        hard_timeout: float = V1.TAB_CONFIRM_HARD_TIMEOUT,
        required: int = V1.TAB_CONFIRM_REQUIRED_FRAMES,
        key_gap: float = V1.TAB_CONFIRM_KEY_GAP,
        fg_retry_max: int = V1.TAB_CONFIRM_FG_RETRY_MAX,
        pre_stability_duration: float = V1.TAB_CONFIRM_PRE_STABILITY_SEC,
        post_confirm_duration: float = 0.0,
        retry_max: int = V1.TAB_CONFIRM_RETRY_MAX,
    ):
        self.hard_timeout = hard_timeout
        self.required = required
        self.key_gap = key_gap
        self.fg_retry_max = fg_retry_max
        self.pre_stability_duration = pre_stability_duration
        self.post_confirm_duration = post_confirm_duration
        self.retry_max = retry_max

        self.active: bool = False
        self.started: float = 0.0
        self.substate: str = ''
        self.counter: int = 0
        self.pending_key: str = ''
        self.last_key_ts: float = 0.0
        self.map_neq_at_arm: bool = False
        self.fg_retry_count: int = 0
        self.retry_count: int = 0
        self.pre_stability_since: float = 0.0
        self.pre_stability_h_coord: Optional[Tuple[int, int]] = None
        self.post_confirm_pause_until: float = 0.0

    def arm(self, now: float, map_neq: bool = False) -> None:
        self.active = True
        self.started = now
        self.counter = 0
        self.last_key_ts = 0.0
        self.map_neq_at_arm = map_neq
        self.substate = 'A_wait_red'
        self.pending_key = 'home'
        self.fg_retry_count = 0
        self.retry_count = 0
        self.pre_stability_since = 0.0
        self.pre_stability_h_coord = None
        self.post_confirm_pause_until = 0.0

    def tick(
        self,
        now: float,
        red_raw: bool,
        white_raw: bool,
        h_coord: Optional[Tuple[int, int]] = None,
    ) -> str:
        if not self.active:
            return 'inactive'

        # 하드 타임아웃 + 재시도.
        if now - self.started >= self.hard_timeout:
            if self.retry_count < self.retry_max:
                self.retry_count += 1
                self.started = now
                self.pending_key = 'home'
                self.counter = 0
                self.last_key_ts = 0.0
                self.fg_retry_count = 0
                self.pre_stability_since = 0.0
                self.pre_stability_h_coord = None
                return 'retry_arm'
            self.active = False
            return 'done_timeout'

        # pending key 송신: Home → (key_gap) → Tab.
        if self.pending_key == 'home':
            if now - self.last_key_ts < self.key_gap:
                return 'wait'
            if self.pre_stability_since == 0.0:
                self.pre_stability_since = now
                self.pre_stability_h_coord = h_coord
                return 'wait_stable'
            if (self.pre_stability_h_coord is None
                    and h_coord is not None):
                self.pre_stability_h_coord = h_coord
                return 'wait_stable'
            if (h_coord is not None
                    and self.pre_stability_h_coord is not None
                    and h_coord != self.pre_stability_h_coord):
                self.pre_stability_since = now
                self.pre_stability_h_coord = h_coord
                return 'wait_stable'
            if ((now - self.pre_stability_since)
                    < self.pre_stability_duration):
                return 'wait_stable'
            self.pending_key = 'tab'
            self.last_key_ts = now
            self.pre_stability_since = 0.0
            self.pre_stability_h_coord = None
            return 'send_home'
        if self.pending_key == 'tab':
            if now - self.last_key_ts < self.key_gap:
                return 'wait'
            self.pending_key = ''
            self.last_key_ts = now
            self.counter = 0
            return 'send_tab'

        # A_wait_red: red&!white N프레임 연속.
        if red_raw and not white_raw:
            self.counter += 1
            if self.counter >= self.required:
                self.active = False
                return 'done_ok'
        else:
            self.counter = 0
        return 'wait'

    def note_fg_mismatch(self, now: float) -> bool:
        if not self.active:
            return False
        if self.fg_retry_count >= self.fg_retry_max:
            return False
        self.fg_retry_count += 1
        self.pending_key = 'home'
        self.counter = 0
        self.last_key_ts = 0.0
        self.pre_stability_since = 0.0
        self.pre_stability_h_coord = None
        return True

    def note_done_ok(self, now: float) -> None:
        self.post_confirm_pause_until = now + self.post_confirm_duration

    def reset(self) -> str:
        old_sub = self.substate
        self.active = False
        self.pending_key = ''
        self.substate = ''
        self.counter = 0
        self.fg_retry_count = 0
        self.retry_count = 0
        self.pre_stability_since = 0.0
        self.pre_stability_h_coord = None
        self.post_confirm_pause_until = 0.0
        return old_sub

    def is_pausing(self, now: float) -> bool:
        return self.active or (now < self.post_confirm_pause_until)


# =====================================================================
# Follower — v1 fsm/controller.Follower 1:1 native 이식
# =====================================================================
class Follower:
    """격수 좌표 이력 기반 이동 방향 추정 + hysteresis. v1 1:1."""

    def __init__(
        self,
        red_lost_sec: float = V1.RED_LOST_SEC_DEFAULT,
        stuck_sec: float = V1.STUCK_SEC_DEFAULT,
        dead_reckon_sec: float = V1.DEAD_RECKON_SEC_DEFAULT,
        red_gain_frames: int = V1.RED_GAIN_FRAMES,
        disconnect_sec: float = V1.DISCONNECT_SEC,
        portal_sec: float = V1.PORTAL_SEC,
        loading_sec: float = V1.LOADING_SEC,
        new_map_sec: float = V1.NEW_MAP_SEC,
    ):
        self.red_lost_sec = red_lost_sec
        self.stuck_sec = stuck_sec
        self.dead_reckon_sec = dead_reckon_sec
        self.red_gain_frames = red_gain_frames
        self.disconnect_sec = disconnect_sec
        self.portal_sec = portal_sec
        self.loading_sec = loading_sec
        self.new_map_sec = new_map_sec

        self._last_red_seen = 0.0
        self._red_confirm_count = 0
        self._red_confirmed = False
        self._last_coord: Optional[Tuple[int, int]] = None
        self._last_coord_change = time.time()
        self._last_map: str = ""
        self._healer_map: str = ""
        self._last_valid_coord_time = 0.0
        self._last_udp_time = 0.0
        self._transition_start = 0.0
        self._transition_phase: Optional[FsmState] = None
        self._state = FsmState.IDLE
        self._coord_hist: deque = deque(maxlen=6)

        # 격수 이전 맵 이탈 스냅샷.
        self._exit_map: str = ""
        self._exit_coord: Optional[Tuple[int, int]] = None
        self._exit_dir: str = "-"

        # 맵별 격수 마지막 관측.
        self._map_last_coord: dict = {}
        self._map_last_dir: dict = {}
        self._map_last_ts: dict = {}

        # 맵별 trail (breadcrumb).
        self._map_trail: dict = {}
        # 전역 trail — (map, x, y) 시퀀스.
        self._global_trail: deque = deque(maxlen=4000)

        # 태그 전이 감지 시 N초 동안 exit_dir 강제 홀드.
        self._force_exit_until: float = 0.0
        self._force_exit_sec: float = V1.FORCE_EXIT_SEC
        self._force_exit_start: float = 0.0
        self._force_exit_pend_sec: float = 0.0

        # 도사 trail 진행 idx (단조 증가).
        self._map_progress: dict = {}
        self._wp_last_diag: Optional[dict] = None

        self.log = None

        # TRAIL-PUSH 로그 스로틀.
        self._last_push_log_ts: dict = {}
        self._push_count: dict = {}

        # C안: 역방향 맵 복귀 디바운스.
        self._prev_last_map: str = ""
        self._last_mapchg_ts: float = 0.0
        self._pending_reversion_count: int = 0
        self._reversion_debounce_sec: float = V1.REVERSION_DEBOUNCE_SEC
        self._reversion_confirm_frames: int = V1.REVERSION_CONFIRM_FRAMES

        # F안: 힐러 자신의 맵 OCR 디바운스.
        self._prev_healer_map: str = ""
        self._last_healer_mapchg_ts: float = 0.0
        self._healer_reversion_count: int = 0

        # I안: 격수 map_seq edge → pause.
        self._last_seen_map_seq: int = 0
        self._pause_until: float = 0.0
        self._pause_sec: float = 0.1
        self._snap_forward_threshold: int = V1.SNAP_FORWARD_THRESHOLD

        # J안: 격수 coord jump filter.
        self._atk_last_valid_coord_by_map: dict = {}
        self._atk_jump_threshold: int = V1.ATK_JUMP_THRESHOLD

        # K안: 힐러맵 OCR crosscheck.
        self._hmap_coord_mismatch_count: int = 0
        self._hmap_coord_mismatch_confirm: int = V1.HMAP_COORD_MISMATCH_CONFIRM
        self._hmap_bbox_margin: int = V1.HMAP_BBOX_MARGIN

        # B안: 오염 거부 임계.
        self._jump_reject_threshold: int = V1.JUMP_REJECT_THRESHOLD
        self._fresh_reject_threshold: int = V1.FRESH_REJECT_THRESHOLD
        self._fresh_map_guard: dict = {}
        self._reject_count: dict = {}

        # MAP-SYNC.
        self._map_sync_until: float = 0.0
        self._map_sync_duration: float = V1.MAP_SYNC_DURATION_SEC
        self._healer_last_coord_by_map: dict = {}
        self._healer_coord_jump_threshold: int = V1.HEALER_COORD_JUMP_THRESHOLD

        # TAB-CONFIRM.
        self._tab = TabConfirm(
            hard_timeout=V1.TAB_CONFIRM_HARD_TIMEOUT,
            required=V1.TAB_CONFIRM_REQUIRED_FRAMES,
            key_gap=V1.TAB_CONFIRM_KEY_GAP,
            fg_retry_max=V1.TAB_CONFIRM_FG_RETRY_MAX,
            pre_stability_duration=V1.TAB_CONFIRM_PRE_STABILITY_SEC,
            post_confirm_duration=0.0,
            retry_max=V1.TAB_CONFIRM_RETRY_MAX,
        )

    # ------------------------------------------------------------------
    # note_healer_map — 힐러 자기 맵 OCR 주입
    # ------------------------------------------------------------------
    def note_healer_map(
        self,
        map_name: str,
        healer_coord: Optional[Tuple[int, int]] = None,
    ) -> None:
        now = time.time()
        # MAP-SYNC 트리거: 힐러 좌표 점프.
        if healer_coord is not None and map_name:
            prev_hc = self._healer_last_coord_by_map.get(map_name)
            if prev_hc is not None:
                dj = (abs(healer_coord[0] - prev_hc[0])
                      + abs(healer_coord[1] - prev_hc[1]))
                if dj > self._healer_coord_jump_threshold:
                    self._map_sync_until = now + self._map_sync_duration
                    if self.log is not None:
                        self.log.debug(
                            f"[HEALER-COORD-JUMP] map={map_name!r} "
                            f"prev={prev_hc} new={healer_coord} d={dj} "
                            f"thr={self._healer_coord_jump_threshold} "
                            f"→ MAP-SYNC hold "
                            f"{self._map_sync_duration*1000:.0f}ms"
                        )
            self._healer_last_coord_by_map[map_name] = healer_coord

        if not map_name or map_name == self._healer_map:
            self._healer_reversion_count = 0
            self._hmap_coord_mismatch_count = 0
            return

        # K안: bbox crosscheck.
        if healer_coord is not None:
            new_trail = self._map_trail.get(map_name)
            if new_trail and len(new_trail) >= 2:
                xs = [p[0] for p in new_trail]
                ys = [p[1] for p in new_trail]
                min_x, max_x = min(xs), max(xs)
                min_y, max_y = min(ys), max(ys)
                hx, hy = healer_coord
                m = self._hmap_bbox_margin
                out_of_bbox = (
                    hx < min_x - m or hx > max_x + m
                    or hy < min_y - m or hy > max_y + m
                )
                if out_of_bbox:
                    self._hmap_coord_mismatch_count += 1
                    if self.log is not None and (
                        self._hmap_coord_mismatch_count <= 5
                        or self._hmap_coord_mismatch_count
                           % self._hmap_coord_mismatch_confirm == 0
                    ):
                        self.log.debug(
                            f"[HMAP-COORD-MISMATCH] reject "
                            f"{self._healer_map!r}→{map_name!r} "
                            f"h={healer_coord} "
                            f"bbox=[{min_x-m},{max_x+m}]"
                            f"x[{min_y-m},{max_y+m}] "
                            f"frame={self._hmap_coord_mismatch_count}"
                        )
                    return
                else:
                    if (self._hmap_coord_mismatch_count > 0
                            and self.log is not None):
                        self.log.debug(
                            f"[HMAP-COORD-MISMATCH-CONFIRM] accepted "
                            f"{self._healer_map!r}→{map_name!r} "
                            f"h={healer_coord} entered bbox after "
                            f"{self._hmap_coord_mismatch_count} reject frames"
                        )
                    self._hmap_coord_mismatch_count = 0

        # F안: 역방향 디바운스.
        is_reverse = (
            map_name == getattr(self, "_prev_healer_map", "")
            and (now - getattr(self, "_last_healer_mapchg_ts", 0.0))
                < self._reversion_debounce_sec
        )
        if is_reverse:
            self._healer_reversion_count = getattr(
                self, "_healer_reversion_count", 0) + 1
            if self._healer_reversion_count < self._reversion_confirm_frames:
                if self.log is not None:
                    self.log.debug(
                        f"[HMAP-DEBOUNCE] hold reversion "
                        f"{self._healer_map!r}→{map_name!r} "
                        f"frame={self._healer_reversion_count}/"
                        f"{self._reversion_confirm_frames}"
                    )
                return
            if self.log is not None:
                self.log.debug(
                    f"[HMAP-DEBOUNCE-CONFIRM] accepted "
                    f"{self._healer_map!r}→{map_name!r} "
                    f"after {self._healer_reversion_count} frames"
                )
            self._healer_reversion_count = 0
        else:
            self._healer_reversion_count = 0

        self._prev_healer_map = self._healer_map
        self._last_healer_mapchg_ts = now
        self._healer_map = map_name

    # ------------------------------------------------------------------
    # update — 메인 tick
    # ------------------------------------------------------------------
    def update(self, s: Optional[Any]) -> FsmState:
        # adapt_state 통과: dataclass/dict → _State.
        if s is not None and not isinstance(s, _State):
            s = adapt_state(s)
        now = time.time()
        if s is None:
            s = _State()

        if getattr(s, "seq", 0) > 0 or getattr(s, "coord_valid", False):
            self._last_udp_time = now

        # C안: 역방향 맵 복귀 디바운스 — pre-pipeline.
        accept_frame = True
        if (s.map_name and s.map_name != self._last_map and self._last_map
                and s.map_name == self._prev_last_map
                and (now - self._last_mapchg_ts)
                    < self._reversion_debounce_sec):
            self._pending_reversion_count += 1
            if self._pending_reversion_count < self._reversion_confirm_frames:
                accept_frame = False
                if self.log is not None:
                    self.log.debug(
                        f"[MAP-DEBOUNCE] hold reversion "
                        f"{self._last_map!r}→{s.map_name!r} "
                        f"frame={self._pending_reversion_count}/"
                        f"{self._reversion_confirm_frames} "
                        f"elapsed={(now - self._last_mapchg_ts):.2f}s"
                    )
            else:
                if self.log is not None:
                    self.log.debug(
                        f"[MAP-DEBOUNCE-CONFIRM] reversion accepted "
                        f"{self._last_map!r}→{s.map_name!r} "
                        f"after {self._pending_reversion_count} frames"
                    )
                self._pending_reversion_count = 0
        else:
            self._pending_reversion_count = 0

        if not accept_frame:
            return self._eval_state_only(now)

        # I안: map_seq edge → pause + MAP-SYNC.
        s_map_seq = getattr(s, "map_seq", 0)
        if s_map_seq > self._last_seen_map_seq:
            prev_seq = self._last_seen_map_seq
            self._last_seen_map_seq = s_map_seq
            self._pause_until = now + self._pause_sec
            self._map_sync_until = now + self._map_sync_duration
            if s.map_name:
                self._atk_last_valid_coord_by_map.pop(s.map_name, None)
            if self.log is not None:
                self.log.debug(
                    f"[MAP-SEQ-EDGE] prev={prev_seq}→{s_map_seq} "
                    f"pause_until={self._pause_until:.3f} "
                    f"({self._pause_sec*1000:.0f}ms) "
                    f"map_sync_until={self._map_sync_until:.3f} "
                    f"({self._map_sync_duration*1000:.0f}ms) "
                    f"atk_map={s.map_name!r} "
                    f"healer_map={self._healer_map!r}"
                )
            # MAP-SEQ-EDGE 시 TAB-CONFIRM 활성이면 취소.
            if self._tab.active:
                old_sub = self._tab.reset()
                if self.log is not None:
                    self.log.debug(
                        f"[TAB-CONFIRM-CANCEL] MAP-SEQ-EDGE → "
                        f"route sub={old_sub!r} 폐기"
                    )

        # MAP-SYNC 해제: h_map == a_map 확정.
        if (self._map_sync_until > 0.0 and s.map_name
                and self._healer_map and s.map_name == self._healer_map):
            if self.log is not None:
                remain = max(0.0, self._map_sync_until - now)
                self.log.debug(
                    f"[MAP-SYNC-DONE] h_map==a_map={s.map_name!r} "
                    f"released early remain={remain*1000:.0f}ms"
                )
            self._map_sync_until = 0.0

        # J안: 격수 coord 점프 필터.
        if s.coord_valid and s.map_name:
            last_valid = self._atk_last_valid_coord_by_map.get(s.map_name)
            if last_valid is not None:
                dj = (abs(s.x - last_valid[0]) + abs(s.y - last_valid[1]))
                if dj > self._atk_jump_threshold:
                    if self.log is not None:
                        self.log.debug(
                            f"[ATK-COORD-JUMP] map={s.map_name!r} "
                            f"prev={last_valid} new=({s.x},{s.y}) d={dj} "
                            f"thr={self._atk_jump_threshold} "
                            f"→ coord_valid=False 강제"
                        )
                    s.coord_valid = False

        # 좌표 갱신 + trail push.
        if s.coord_valid:
            coord = (s.x, s.y)
            self._last_valid_coord_time = now
            if self._last_coord is None or coord != self._last_coord:
                self._last_coord_change = now
                self._last_coord = coord
                self._coord_hist.append((now, coord))
            if s.map_name:
                push_ok = True
                reject_reason = ""
                trail = self._map_trail.get(s.map_name)
                # B2: fresh guard.
                guard = self._fresh_map_guard.get(s.map_name)
                if guard is not None:
                    ex_coord, expires = guard
                    if now >= expires:
                        del self._fresh_map_guard[s.map_name]
                    elif ex_coord is not None:
                        d_exit = (abs(coord[0] - ex_coord[0])
                                  + abs(coord[1] - ex_coord[1]))
                        if d_exit <= self._fresh_reject_threshold:
                            push_ok = False
                            reject_reason = (
                                f"fresh_near_exit exit={ex_coord} "
                                f"d={d_exit}"
                            )
                # B1: jump reject.
                if push_ok and trail and len(trail) > 0:
                    px, py = trail[-1]
                    d_jump = abs(coord[0] - px) + abs(coord[1] - py)
                    if d_jump > self._jump_reject_threshold:
                        push_ok = False
                        reject_reason = (
                            f"jump last={trail[-1]} d={d_jump} "
                            f"threshold={self._jump_reject_threshold}"
                        )
                if not push_ok:
                    rc = self._reject_count.get(s.map_name, 0) + 1
                    self._reject_count[s.map_name] = rc
                    if self.log is not None:
                        self.log.debug(
                            f"[TRAIL-REJECT] map={s.map_name!r} "
                            f"coord={coord} reason={reject_reason} "
                            f"reject_total={rc}"
                        )
                else:
                    self._atk_last_valid_coord_by_map[s.map_name] = coord
                    self._map_last_coord[s.map_name] = coord
                    self._map_last_dir[s.map_name] = self._hist_direction()
                    self._map_last_ts[s.map_name] = now
                    if trail is None:
                        trail = deque(maxlen=2000)
                        self._map_trail[s.map_name] = trail
                    if not trail or trail[-1] != coord:
                        trail.append(coord)
                        tagged = (s.map_name, coord[0], coord[1])
                        if (not self._global_trail
                                or self._global_trail[-1] != tagged):
                            self._global_trail.append(tagged)
                        if s.map_name in self._fresh_map_guard:
                            del self._fresh_map_guard[s.map_name]
                        if self.log is not None:
                            cnt = self._push_count.get(s.map_name, 0) + 1
                            self._push_count[s.map_name] = cnt
                            last_ts = self._last_push_log_ts.get(
                                s.map_name, 0.0)
                            if cnt <= 10 or (now - last_ts) >= 1.0:
                                self._last_push_log_ts[s.map_name] = now
                                self.log.debug(
                                    f"[TRAIL-PUSH] map={s.map_name!r} "
                                    f"coord={coord} idx={len(trail)-1} "
                                    f"len={len(trail)} total_pushes={cnt}"
                                )

        # 격수 맵 변경 감지 → ENTER_PORTAL.
        if s.map_name and s.map_name != self._last_map and self._last_map:
            self._exit_map = self._last_map
            old_trail = self._map_trail.get(self._last_map)
            prior_exit_dir = self._exit_dir
            prior_exit_coord = self._exit_coord
            if old_trail and len(old_trail) >= 1:
                self._exit_coord = old_trail[-1]
                if len(old_trail) >= 2:
                    px, py = old_trail[-2]
                    lx, ly = old_trail[-1]
                    dx, dy = lx - px, ly - py
                    if abs(dx) < 1 and abs(dy) < 1:
                        self._exit_dir = "-"
                    elif abs(dx) >= abs(dy):
                        self._exit_dir = "R" if dx > 0 else "L"
                    else:
                        self._exit_dir = "D" if dy > 0 else "U"
                else:
                    self._exit_dir = "-"
            else:
                self._exit_coord = self._last_coord
                self._exit_dir = self._hist_direction()
            # E안 계승: '-'면 prior 이용.
            if self._exit_dir == "-" and prior_exit_dir in ("L", "R", "U", "D"):
                reverse_map = {"L": "R", "R": "L", "U": "D", "D": "U"}
                is_return = (s.map_name == self._prev_last_map
                             and self._prev_last_map != "")
                inherited_value = (reverse_map[prior_exit_dir]
                                   if is_return else prior_exit_dir)
                self._exit_dir = inherited_value
                if self._exit_coord is None:
                    self._exit_coord = prior_exit_coord
                if self.log is not None:
                    self.log.debug(
                        f"[EXIT-INHERIT] {self._last_map!r}→{s.map_name!r} "
                        f"inherited exit_dir={inherited_value!r} "
                        f"(prior={prior_exit_dir!r} return={is_return} "
                        f"prev_last={self._prev_last_map!r} "
                        f"old_trail_empty="
                        f"{len(old_trail) if old_trail else 0})"
                    )
            # 전역 trail 기반 재계산.
            g_clean_exit_dir = None
            g_clean_exit_coord = None
            same_map_tail = []
            for e in reversed(self._global_trail):
                if e[0] == self._last_map:
                    same_map_tail.append((e[1], e[2]))
                    if len(same_map_tail) >= 5:
                        break
                elif same_map_tail:
                    break
            if len(same_map_tail) >= 1:
                g_clean_exit_coord = same_map_tail[0]
                if len(same_map_tail) >= 2:
                    lx, ly = same_map_tail[0]
                    px, py = same_map_tail[1]
                    dx, dy = lx - px, ly - py
                    if abs(dx) >= 1 or abs(dy) >= 1:
                        if abs(dx) >= abs(dy):
                            g_clean_exit_dir = "R" if dx > 0 else "L"
                        else:
                            g_clean_exit_dir = "D" if dy > 0 else "U"
            if g_clean_exit_dir and g_clean_exit_dir != self._exit_dir:
                if self.log is not None:
                    self.log.debug(
                        f"[EXIT-DIR-GLOBAL] override "
                        f"old_trail_dir={self._exit_dir!r} → "
                        f"global_dir={g_clean_exit_dir!r} "
                        f"coord={g_clean_exit_coord} "
                        f"same_map_tail={same_map_tail[:3]}"
                    )
                self._exit_dir = g_clean_exit_dir
                if g_clean_exit_coord is not None:
                    self._exit_coord = g_clean_exit_coord

            # force_exit 창 개시.
            self._force_exit_start = now + self._force_exit_pend_sec
            self._force_exit_until = self._force_exit_start + self._force_exit_sec
            self._transition_start = now
            self._transition_phase = FsmState.ENTER_PORTAL
            self._prev_last_map = self._last_map
            self._last_mapchg_ts = now
            self._last_map = s.map_name
            self._coord_hist.clear()
            self._fresh_map_guard[s.map_name] = (self._exit_coord, now + 2.0)
            if self.log is not None:
                self.log.debug(
                    f"[CTRL-MAPCHG] {self._exit_map!r}→{s.map_name!r} "
                    f"exit_coord={self._exit_coord} "
                    f"exit_dir={self._exit_dir!r} "
                    f"old_trail_len={len(old_trail) if old_trail else 0} "
                    f"old_trail_tail={list(old_trail or [])[-10:]} "
                    f"fresh_guard_until={now+2.0:.3f}"
                )
            self._map_trail[s.map_name] = deque(maxlen=2000)
            self._map_progress[s.map_name] = 0
            self._push_count[s.map_name] = 0
            self._atk_last_valid_coord_by_map.pop(s.map_name, None)
        elif s.map_name and not self._last_map:
            self._last_map = s.map_name
            self._last_mapchg_ts = now

        # 맵 전환 3단계.
        if self._transition_phase is not None:
            elapsed = now - self._transition_start
            phase = self._transition_phase
            if phase == FsmState.ENTER_PORTAL:
                if (self._healer_map == self._last_map
                        or elapsed >= self.portal_sec):
                    self._transition_phase = FsmState.LOADING
                    self._transition_start = now
                    self._state = FsmState.LOADING
                    return self._state
                self._state = FsmState.ENTER_PORTAL
                return self._state
            if phase == FsmState.LOADING:
                if (self._healer_map == self._last_map
                        or elapsed >= self.loading_sec):
                    self._transition_phase = FsmState.NEW_MAP
                    self._transition_start = now
                    self._state = FsmState.NEW_MAP
                    return self._state
                self._state = FsmState.LOADING
                return self._state
            if phase == FsmState.NEW_MAP:
                if elapsed >= self.new_map_sec:
                    self._transition_phase = None
                else:
                    self._state = FsmState.NEW_MAP
                    return self._state

        # 빨탭 히스테리시스.
        if s.red_tab:
            self._last_red_seen = now
            self._red_confirm_count += 1
            if self._red_confirm_count >= self.red_gain_frames:
                self._red_confirmed = True
        else:
            self._red_confirm_count = 0
            if (now - self._last_red_seen) >= self.red_lost_sec:
                self._red_confirmed = False

        # 우선순위.
        if (self._last_udp_time > 0
                and (now - self._last_udp_time) > self.disconnect_sec):
            self._state = FsmState.DISCONNECTED
            return self._state
        coord_stale = (now - self._last_valid_coord_time) > self.dead_reckon_sec
        if self._red_confirmed:
            self._state = FsmState.COMBAT
        elif coord_stale:
            self._state = FsmState.DEAD_RECKON
        elif (now - self._last_coord_change) > self.stuck_sec:
            self._state = FsmState.STUCK
        else:
            self._state = FsmState.FOLLOW
        return self._state

    def _eval_state_only(self, now: float) -> FsmState:
        if (self._last_udp_time > 0
                and (now - self._last_udp_time) > self.disconnect_sec):
            self._state = FsmState.DISCONNECTED
            return self._state
        coord_stale = (now - self._last_valid_coord_time) > self.dead_reckon_sec
        if self._red_confirmed:
            self._state = FsmState.COMBAT
        elif coord_stale:
            self._state = FsmState.DEAD_RECKON
        elif (now - self._last_coord_change) > self.stuck_sec:
            self._state = FsmState.STUCK
        else:
            self._state = FsmState.FOLLOW
        return self._state

    # ------------------------------------------------------------------
    # 방향 / exit 정보
    # ------------------------------------------------------------------
    def direction(self) -> str:
        return self._hist_direction()

    def _hist_direction(self) -> str:
        if len(self._coord_hist) < 2:
            return "-"
        _, (x0, y0) = self._coord_hist[0]
        _, (x1, y1) = self._coord_hist[-1]
        dx, dy = x1 - x0, y1 - y0
        if abs(dx) < 1 and abs(dy) < 1:
            return "-"
        if abs(dx) >= abs(dy):
            return "R" if dx > 0 else "L"
        return "D" if dy > 0 else "U"

    def exit_dir(self) -> str:
        return self._exit_dir

    def exit_coord(self) -> Optional[Tuple[int, int]]:
        return self._exit_coord

    def exit_map(self) -> str:
        return self._exit_map

    def force_exit_active(self, now: Optional[float] = None) -> bool:
        if now is None:
            now = time.time()
        return self._force_exit_start <= now < self._force_exit_until

    def force_exit_remaining(self, now: Optional[float] = None) -> float:
        if now is None:
            now = time.time()
        return max(0.0, self._force_exit_until - now)

    # ------------------------------------------------------------------
    # 맵별 관측 / trail
    # ------------------------------------------------------------------
    def last_seen_in(self, map_name: str):
        if not map_name:
            return None
        c = self._map_last_coord.get(map_name)
        d = self._map_last_dir.get(map_name, "-")
        if c is None:
            return None
        return (c, d)

    def trail_for(self, map_name: str):
        if not map_name:
            return []
        t = self._map_trail.get(map_name)
        if not t:
            return []
        return list(t)

    def trail_tail(self, map_name: str, n: int = 10):
        t = self._map_trail.get(map_name)
        if not t:
            return []
        return list(t)[-n:]

    def progress_of(self, map_name: str):
        return self._map_progress.get(map_name, 0)

    def wp_diag(self):
        return self._wp_last_diag

    # ------------------------------------------------------------------
    # next_waypoint — snap-forward + 단조 진행 + exit_dash
    # ------------------------------------------------------------------
    def next_waypoint(
        self,
        map_name: str,
        healer_coord,
        tol: int = 2,
        exit_dash: bool = False,
    ):
        trail = self._map_trail.get(map_name)
        if not trail:
            self._wp_last_diag = {"map": map_name, "reason": "no_trail"}
            return None
        cur = self._map_progress.get(map_name, 0)
        last_idx = len(trail) - 1
        if cur > last_idx:
            cur = last_idx
        if healer_coord is None:
            self._wp_last_diag = {
                "map": map_name, "len": len(trail), "cur": cur,
                "wp": trail[cur], "h": None, "d": None,
                "tail": list(trail)[-5:], "reason": "h_none",
            }
            return (trail[cur], self._map_last_dir.get(map_name, "-"))
        hx, hy = healer_coord
        initial_cur = cur
        snap_thr = self._snap_forward_threshold
        best_snap_idx = cur
        best_snap_d = None
        if exit_dash:
            for i in range(last_idx, cur - 1, -1):
                wx, wy = trail[i]
                d = abs(wx - hx) + abs(wy - hy)
                if d <= snap_thr:
                    best_snap_idx = i
                    best_snap_d = d
                    break
            if best_snap_idx > cur and self.log is not None:
                self.log.debug(
                    f"[SNAP-EXIT] map={map_name!r} "
                    f"cur={cur}→{best_snap_idx}/{last_idx} "
                    f"h={healer_coord} d={best_snap_d} thr={snap_thr} "
                    f"(exit_dash zigzag skip)"
                )
            if best_snap_idx > cur:
                cur = best_snap_idx
        else:
            for i in range(cur, last_idx + 1):
                wx, wy = trail[i]
                d = abs(wx - hx) + abs(wy - hy)
                if d <= snap_thr:
                    if (best_snap_d is None
                            or d < best_snap_d
                            or (d == best_snap_d and i > best_snap_idx)):
                        best_snap_idx = i
                        best_snap_d = d
            if best_snap_idx > cur:
                if self.log is not None:
                    self.log.debug(
                        f"[SNAP-FWD] map={map_name!r} "
                        f"cur={cur}→{best_snap_idx}/{last_idx} "
                        f"h={healer_coord} d={best_snap_d} thr={snap_thr}"
                    )
                cur = best_snap_idx
        # tol 기반 전진.
        while cur < last_idx:
            wx, wy = trail[cur]
            if abs(wx - hx) + abs(wy - hy) <= tol:
                cur += 1
            else:
                break
        if cur != initial_cur and self.log is not None:
            self.log.debug(
                f"[PROGRESS] map={map_name!r} "
                f"cur={initial_cur}→{cur}/{last_idx} "
                f"h={healer_coord} reached_wps=[{initial_cur}:{cur}]"
            )
        self._map_progress[map_name] = cur
        wx, wy = trail[cur]
        d_to_wp = abs(wx - hx) + abs(wy - hy)
        base_diag = {
            "map": map_name, "len": len(trail), "cur": cur,
            "last_idx": last_idx, "wp": (wx, wy),
            "h": (hx, hy), "d": d_to_wp,
            "tail": list(trail)[-5:],
        }
        if cur >= last_idx:
            if exit_dash and d_to_wp > tol:
                base_diag["reason"] = "exit_dash_target"
                self._wp_last_diag = base_diag
                return (trail[cur], self._map_last_dir.get(map_name, "-"))
            base_diag["reason"] = "end_reached"
            self._wp_last_diag = base_diag
            return None
        base_diag["reason"] = "advancing"
        self._wp_last_diag = base_diag
        return (trail[cur], self._map_last_dir.get(map_name, "-"))

    # ------------------------------------------------------------------
    # is_paused — MAP-PAUSE + MAP-SYNC + TAB-CONFIRM
    # ------------------------------------------------------------------
    def is_paused(self, now: Optional[float] = None) -> bool:
        if now is None:
            now = time.time()
        return (now < self._pause_until
                or now < self._map_sync_until
                or self._tab.is_pausing(now))

    def pause_remaining(self, now: Optional[float] = None) -> float:
        if now is None:
            now = time.time()
        a = max(0.0, self._pause_until - now)
        b = max(0.0, self._map_sync_until - now)
        c = max(0.0, self._tab.post_confirm_pause_until - now)
        return max(a, b, c)

    # ------------------------------------------------------------------
    # TAB-CONFIRM 위임
    # ------------------------------------------------------------------
    def tab_confirm_tick(
        self,
        now: float,
        red_raw: bool,
        white_raw: bool,
        h_coord: Optional[Tuple[int, int]] = None,
    ) -> str:
        return self._tab.tick(now, red_raw, white_raw, h_coord)

    def arm_tab_confirm(self, now: float, map_neq: bool = False) -> None:
        self._tab.arm(now, map_neq)

    def note_tab_fg_mismatch(self, now: float) -> bool:
        return self._tab.note_fg_mismatch(now)

    def note_tab_done_ok(self, now: float) -> None:
        self._tab.note_done_ok(now)

    # ------------------------------------------------------------------
    # 하위 호환 property
    # ------------------------------------------------------------------
    @property
    def _tab_confirm_active(self) -> bool:
        return self._tab.active

    @property
    def _tab_confirm_substate(self) -> str:
        return self._tab.substate

    @property
    def _tab_confirm_started(self) -> float:
        return self._tab.started

    @property
    def _tab_confirm_required(self) -> int:
        return self._tab.required

    @property
    def _tab_confirm_map_neq_at_arm(self) -> bool:
        return self._tab.map_neq_at_arm

    @property
    def _tab_retry_count(self) -> int:
        return self._tab.retry_count

    @property
    def _tab_retry_max(self) -> int:
        return self._tab.retry_max

    @property
    def _tab_fg_retry_count(self) -> int:
        return self._tab.fg_retry_count

    @property
    def _tab_fg_retry_max(self) -> int:
        return self._tab.fg_retry_max

    @property
    def _tab_post_confirm_duration(self) -> float:
        return self._tab.post_confirm_duration


# =====================================================================
# Factory — 기존 src_v2 사용처 호환.
# =====================================================================
def make_follower(**kwargs) -> Follower:
    """Follower 인스턴스. kwargs 는 __init__ 포워딩."""
    return Follower(**kwargs)


# 하위 호환 alias — 기존 코드가 _MinimalFollowerStub 를 직접 import.
# 새 native Follower 가 동일 API + 더 풍부한 동작을 제공하므로 그대로 매핑.
_MinimalFollowerStub = Follower


__all__ = [
    "Follower",
    "FsmState",
    "TabConfirm",
    "make_follower",
    "adapt_state",
    "_MinimalFollowerStub",
]
