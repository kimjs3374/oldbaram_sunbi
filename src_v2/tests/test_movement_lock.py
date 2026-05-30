"""Movement lock parity tests — v1 1:1 (이번 turn 보강).

v1 SoR: healer_worker.py:1516-1539 + keys.set_movement_lock + sched.set_on_busy_change.

검증:
  T-060: blocks_movement 시퀀스 시전 중 set_direction 무시.
  T-061: lock True 진입 시 현재 held release.
  T-062: 10초 stuck → 강제 해제.
  T-063: lock True→False edge → on_lock_release 콜백 호출.
  T-064: BLOCKS_MOVEMENT_SEQUENCES 정의 = (self_heal, self_revive, attacker_revive).

이 테스트는 외부 환경 의존 없음 (Qt/grab/yolo/ocr/UDP 미사용). 단위 검증만.
"""
from __future__ import annotations

import time

import pytest

from src_v2.config import v1_defaults as V1
from src_v2.hands.input_dispatcher import (
    DIRECTION_VK,
    InputDispatcher,
    NullKeys,
)


# --------------------------------------------------------------------- #
# T-064: blocks_movement 정의 보존
# --------------------------------------------------------------------- #
def test_t064_blocks_movement_sequences_defined():
    """v1 SkillSpec.blocks_movement=True 인 시퀀스 = 자힐/자가부활/격수부활."""
    assert "self_heal" in V1.BLOCKS_MOVEMENT_SEQUENCES
    assert "self_revive" in V1.BLOCKS_MOVEMENT_SEQUENCES
    assert "attacker_revive" in V1.BLOCKS_MOVEMENT_SEQUENCES
    # 공력증강/파력무참/백호/파혼/무장/보호 는 blocks_movement=False.
    assert "gyoungryeok" not in V1.BLOCKS_MOVEMENT_SEQUENCES
    assert "parlyuk" not in V1.BLOCKS_MOVEMENT_SEQUENCES
    assert "baekho" not in V1.BLOCKS_MOVEMENT_SEQUENCES
    assert "parhon" not in V1.BLOCKS_MOVEMENT_SEQUENCES
    assert "mujang" not in V1.BLOCKS_MOVEMENT_SEQUENCES
    assert "boho" not in V1.BLOCKS_MOVEMENT_SEQUENCES


# --------------------------------------------------------------------- #
# T-060: lock=True 시 set_direction 무시
# --------------------------------------------------------------------- #
def test_t060_set_direction_ignored_when_locked():
    keys = NullKeys()
    disp = InputDispatcher(keys=keys)
    disp.set_direction("R")
    assert disp.held_direction() == "R"
    # press R 1회 기록.
    assert ("down", DIRECTION_VK["R"]) in keys.events

    # lock 진입.
    disp.set_movement_lock(True)
    # held release 확인 (T-061).
    assert disp.held_direction() is None
    assert ("up", DIRECTION_VK["R"]) in keys.events
    # 이후 set_direction("L") 무시.
    n_before = len(keys.events)
    disp.set_direction("L")
    n_after = len(keys.events)
    assert n_after == n_before, "lock 중 set_direction 이 keys 이벤트 발생시킴"
    assert disp.held_direction() is None


# --------------------------------------------------------------------- #
# T-061: lock 진입 시 held release
# --------------------------------------------------------------------- #
def test_t061_lock_true_releases_held():
    keys = NullKeys()
    disp = InputDispatcher(keys=keys)
    disp.set_direction("U")
    keys.events.clear()
    disp.set_movement_lock(True)
    # U release 발생.
    assert ("up", DIRECTION_VK["U"]) in keys.events


# --------------------------------------------------------------------- #
# T-063: lock True→False edge 콜백
# --------------------------------------------------------------------- #
def test_t063_lock_release_edge_callback():
    keys = NullKeys()
    disp = InputDispatcher(keys=keys)
    fired = {"n": 0}

    def cb():
        fired["n"] += 1

    disp.set_on_lock_release(cb)
    disp.set_movement_lock(True)
    assert fired["n"] == 0  # True 진입은 콜백 안 함.
    disp.set_movement_lock(False)
    assert fired["n"] == 1  # False edge 1회.
    # 동일 False 재호출은 edge 아님 (콜백 안 함).
    disp.set_movement_lock(False)
    assert fired["n"] == 1


# --------------------------------------------------------------------- #
# T-062: 10초 stuck 강제 해제
# --------------------------------------------------------------------- #
def test_t062_stuck_force_release():
    keys = NullKeys()
    # stuck timeout 0.1s 로 단축해 빠른 검증.
    disp = InputDispatcher(keys=keys, movement_lock_stuck_sec=0.1)
    fired = {"n": 0}
    disp.set_on_lock_release(lambda: fired.__setitem__("n", fired["n"] + 1))

    disp.set_movement_lock(True)
    # 즉시 체크 — stuck 아님.
    assert disp.check_movement_lock_stuck() is False
    assert disp.is_movement_locked() is True
    # timeout 초과 후 체크 — 강제 해제.
    time.sleep(0.15)
    assert disp.check_movement_lock_stuck() is True
    assert disp.is_movement_locked() is False
    # 콜백도 발사 (재hold 예약).
    assert fired["n"] >= 1


# --------------------------------------------------------------------- #
# 통합: lock 해제 후 set_direction 정상 복구
# --------------------------------------------------------------------- #
def test_lock_release_restores_set_direction():
    keys = NullKeys()
    disp = InputDispatcher(keys=keys)
    disp.set_movement_lock(True)
    disp.set_direction("R")  # 무시
    assert disp.held_direction() is None
    disp.set_movement_lock(False)
    disp.set_direction("R")
    assert disp.held_direction() == "R"
    assert ("down", DIRECTION_VK["R"]) in keys.events


# --------------------------------------------------------------------- #
# SkillExecutor 통합: blocks_movement 시퀀스 자동 lock/unlock
# --------------------------------------------------------------------- #
def test_skill_executor_auto_locks_for_blocks_movement():
    """SkillExecutor._handle 가 blocks_movement 시퀀스에 대해 lock/unlock 자동 적용."""
    import queue

    from src_v2.core.event_bus import EventBus
    from src_v2.core.plugin_registry import PluginRegistry, sequence
    from src_v2.core.types import CastRequest
    from src_v2.hands.skill_executor import SkillExecutor

    keys = NullKeys()
    disp = InputDispatcher(keys=keys)
    bus = EventBus()
    q = queue.PriorityQueue()

    # 시퀀스 실행 도중 lock 상태 캡처.
    captured = {"locked_during_run": None}

    @sequence("self_heal", description="test stub")
    def _self_heal_stub(ctx):
        captured["locked_during_run"] = ctx["_dispatcher"].is_movement_locked()

    in_progress: set = set()
    ex = SkillExecutor(q, bus, disp, in_progress=in_progress)
    ex.start()
    try:
        q.put((10, 1, CastRequest(name="self_heal", priority=10)))
        # 처리 대기.
        deadline = time.time() + 2.0
        while captured["locked_during_run"] is None and time.time() < deadline:
            time.sleep(0.02)
    finally:
        ex.stop(timeout=1.0)

    assert captured["locked_during_run"] is True, "blocks_movement 시퀀스 중 lock 미적용"
    # 종료 후 unlock 확인.
    assert disp.is_movement_locked() is False, "blocks_movement 시퀀스 종료 후 unlock 안 됨"


def test_skill_executor_no_lock_for_non_blocking():
    """공력증강 등 blocks_movement=False 인 시퀀스는 lock 안 함."""
    import queue

    from src_v2.core.event_bus import EventBus
    from src_v2.core.plugin_registry import PluginRegistry, sequence
    from src_v2.core.types import CastRequest
    from src_v2.hands.skill_executor import SkillExecutor

    keys = NullKeys()
    disp = InputDispatcher(keys=keys)
    bus = EventBus()
    q = queue.PriorityQueue()

    captured = {"locked_during_run": None}

    @sequence("gyoungryeok", description="test stub")
    def _gy_stub(ctx):
        captured["locked_during_run"] = ctx["_dispatcher"].is_movement_locked()

    ex = SkillExecutor(q, bus, disp)
    ex.start()
    try:
        q.put((20, 1, CastRequest(name="gyoungryeok", priority=20)))
        deadline = time.time() + 2.0
        while captured["locked_during_run"] is None and time.time() < deadline:
            time.sleep(0.02)
    finally:
        ex.stop(timeout=1.0)

    assert captured["locked_during_run"] is False, (
        "blocks_movement=False 인 시퀀스에 lock 잘못 적용"
    )
