"""TAB-CONFIRM FSM.

흰탭 감지 → Tab 키 송신 → 빨탭 복귀 확정 상태기계.

설계 (2026-04-16 사용자 G1/G2/G3 확정):
  G1: Tab 은 힐러 자신에 빨탭 찍는 동작 → 다음 맵 진입 시 격수에게 자동 전이.
  G2: ESC 무의미 (격수 스킬 계속 시전 → 흰탭 즉시 재등장). Route B 삭제.
  G3: 포탈 통과는 exit_dir 단순 방향키. 단 빨탭 상태여야 이동 가능.

Route A 단일 흐름 (map_neq 무관):
  arm → pending 'home' → Home(pre-stability) → pending 'tab' → send_tab
  → A_wait_red → red&!white 2프레임 → done_ok
  → is_paused=False → exit_dir → 포탈 통과

사전 강화 5종 (2026-04-17):
  #1 fg_match=False 재시도 (note_fg_mismatch).
  #2 Pre-stability: h_coord 불변 시간.
  #3 red&!white 양자 조건 (self-target 오판 방지).
  #4 Post done_ok stabilize (Tab 반영 시간 확보).
  #5 hard timeout 재시도.

외부 API:
  tick(now, red_raw, white_raw, h_coord) -> str 상태 코드
  arm(now, map_neq=False) -> None
  note_fg_mismatch(now) -> bool (재큐잉 가능 여부)
  note_done_ok(now) -> None (post-confirm pause 설정)
  reset() -> None (MAP-SEQ-EDGE 취소 등)

사용처: `fsm.controller.Follower` 가 `self._tab` 으로 보유.
"""
from __future__ import annotations

from typing import Optional


class TabConfirm:
    """흰탭 → Tab → red&!white 확정 FSM."""

    def __init__(
        self,
        hard_timeout: float = 10.0,
        required: int = 2,
        key_gap: float = 0.06,
        fg_retry_max: int = 2,
        pre_stability_duration: float = 0.0,
        post_confirm_duration: float = 0.0,
        retry_max: int = 2,
    ):
        # 상수 (Follower 생성 시 주입).
        self.hard_timeout = hard_timeout
        self.required = required
        self.key_gap = key_gap
        self.fg_retry_max = fg_retry_max
        self.pre_stability_duration = pre_stability_duration
        self.post_confirm_duration = post_confirm_duration
        self.retry_max = retry_max

        # 런타임 상태.
        self.active: bool = False
        self.started: float = 0.0
        self.substate: str = ''   # 'A_wait_red' 단독.
        self.counter: int = 0
        self.pending_key: str = ''   # 'home' | 'tab' | ''
        self.last_key_ts: float = 0.0
        self.map_neq_at_arm: bool = False  # 로그 전용.
        self.fg_retry_count: int = 0
        self.retry_count: int = 0
        self.pre_stability_since: float = 0.0
        self.pre_stability_h_coord: Optional[tuple] = None
        self.post_confirm_pause_until: float = 0.0

    # ----- 외부 API --------------------------------------------------

    def arm(self, now: float, map_neq: bool = False) -> None:
        """흰탭 arm 조건 확정 시 호출. Route A 단독."""
        self.active = True
        self.started = now
        self.counter = 0
        self.last_key_ts = 0.0
        self.map_neq_at_arm = map_neq
        self.substate = 'A_wait_red'
        # Home → Tab 2-step. Home 이 흰탭을 힐러 자신에게 강제 focus.
        self.pending_key = 'home'
        # transient 초기화.
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
        h_coord: Optional[tuple] = None,
    ) -> str:
        """매 프레임 호출. 반환값:
          'inactive'     — 비활성. 호출자: 일반 로직.
          'wait'         — 확정 대기. 호출자: 이동 차단.
          'wait_stable'  — Pre-Tab stability 대기. 이동 차단.
          'send_home'    — Home 키 송신 프레임.
          'send_tab'     — Tab 키 송신 프레임.
          'done_ok'      — 확정 (red 유지 + white 소멸 N프레임).
          'retry_arm'    — hard timeout 재시도. 이동 차단 유지.
          'done_timeout' — hard timeout + retry 소진. 일반 follow 복귀.
        """
        if not self.active:
            return 'inactive'

        # 하드 타임아웃 (#5 재시도 포함).
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
            # #2 Pre-stability.
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
            # 안정 확정 → Home 송신. Tab 큐잉.
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

        # A_wait_red (#3): red&!white N프레임 연속.
        if red_raw and not white_raw:
            self.counter += 1
            if self.counter >= self.required:
                self.active = False
                return 'done_ok'
        else:
            self.counter = 0
        return 'wait'

    def note_fg_mismatch(self, now: float) -> bool:
        """(#1) _press_tab 결과 fg_match=False 시 호출. 재큐잉 가능하면 True."""
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
        """(#4) done_ok 직후 post-confirm pause 창 설정."""
        self.post_confirm_pause_until = now + self.post_confirm_duration

    def reset(self) -> str:
        """MAP-SEQ-EDGE 취소 등. 이전 substate 를 반환 (로깅용)."""
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
        """is_paused 체크용: 활성이거나 post-confirm pause 창 중이면 True."""
        return self.active or (now < self.post_confirm_pause_until)
