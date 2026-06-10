"""블록 A / 블록 B 시퀀스.

사용자 2026-04-20 지시 기반 — 힐러 PC 자힐/부활 파이프라인.

**블록 A (self-target)**:
  ESC → HOME → TAB. 자기 자신에게 타겟 이동 (자힐/자가부활 전 단계).
  HOME 은 옛바에서 "자신 타겟" 키. ESC 는 기존 타겟/UI 해제.
  TAB 은 self-target 확정 (격수 타겟팅 루프 빠져나감).

**블록 B (격수 타겟 복귀)**:
  1. `NumLockCycler._unlock_all()` + `ensure_numlock_off()`
     (= 기존 원격 정지 로직과 동일. 주력 힐 토글 OFF)
  2. ESC × 3
  3. TAB × 2
  다시 격수에게 빨탭 걸기 + NumLock 싸이클 재잠금은 메인 루프가 처리.

msw.exe 포그라운드 가드는 호출측 책임 (SkillScheduler 와 동일 정책).

주의:
- press_normal_vk 는 NumPad VK 가 아니라면 그대로 press. VK_ESCAPE/HOME/TAB
  은 NumPad 매핑 없으므로 일반 scan-code 로 전달 (keybd_event).
- 블록 B 의 `_unlock_all` 은 NumLockCycler 인스턴스가 필요. cycler=None 이면
  그 단계 건너뜀 (테스트 버튼 등).
"""
from __future__ import annotations

import ctypes
import random
import time
from typing import Callable, Optional

from .numlock_cycle import (
    press_normal_vk,
    press_numpad_scan,
    skill_lock_vk,
    ensure_numlock_off,
    DEFAULT_SLOTS,
)

user32 = ctypes.WinDLL("user32", use_last_error=True)
_keybd = user32.keybd_event

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001

VK_ESCAPE = 0x1B
VK_HOME = 0x24
VK_TAB = 0x09

# Numpad VK (numlock_cycle.NUMPAD_VK_MAP 와 일치).
_VK_NUMPAD1 = 0x61  # 메인힐 (자힐).
_VK_NUMPAD6 = 0x66  # 부활.


# 키 사이 기본 간격 (ms).
_KEY_INTERVAL_MS = 80
# 블록 내 세그먼트 사이 간격 (ms).
_SEGMENT_INTERVAL_MS = 250


def _press_vk(vk: int, min_ms: int = 35, max_ms: int = 60,
              extended: bool = False) -> None:
    """단순 VK press (NumPad 매핑 우회). ESC/HOME/TAB 전용.

    HOME 은 일부 환경에서 extended flag 필요 → extended=True 옵션.
    """
    scan = user32.MapVirtualKeyW(vk, 0)
    flag = KEYEVENTF_EXTENDEDKEY if extended else 0
    _keybd(vk, scan, flag, 0)
    time.sleep(random.uniform(min_ms, max_ms) / 1000.0)
    _keybd(vk, scan, flag | KEYEVENTF_KEYUP, 0)


def _burst_press(vk: int,
                 burst_sec: float,
                 interval_sec: float,
                 log_fn: Optional[Callable[[str], None]],
                 label: str,
                 stop_flag: Optional[Callable[[], bool]] = None) -> int:
    """burst_sec 동안 interval_sec 간격으로 VK press 반복.

    VK 는 NumPad VK 여도 press_normal_vk 가 normal number VK 로 변환 후 press.
    반환: 실제 press 횟수.

    stop_flag: () -> bool. True 리턴 시 즉시 루프 종료 (worker stop 반응용).
    """
    cnt = 0
    t_end = time.time() + float(burst_sec)
    while time.time() < t_end:
        if stop_flag is not None:
            try:
                if stop_flag():
                    if log_fn:
                        try:
                            log_fn(f"[SEQ-A] {label} burst 중단 (stop_flag)")
                        except Exception:
                            pass
                    break
            except Exception:
                pass
        try:
            press_normal_vk(vk)
        except Exception as e:
            if log_fn:
                try:
                    log_fn(f"[SEQ-A] {label} press 예외 vk={hex(vk)}: {e}")
                except Exception:
                    pass
            break
        cnt += 1
        time.sleep(float(interval_sec))
    return cnt


def block_a_self_target(
    cycler=None,
    log_fn: Optional[Callable[[str], None]] = None,
    stop_flag: Optional[Callable[[], bool]] = None,
) -> None:
    """블록 A: 토글 ON 상태에서 시작 → self-target → 토글 OFF → 부활/자힐.

    사용자 지시 2026-04-20:
      "토글켜져있는상태에서 시작, tab→home→tab→토글오프→부활+자힐"

    흐름:
      1) TAB → HOME → TAB (self-target. 토글 ON 상태이므로 주력 힐은 계속
         자동 시전 중이지만, TAB/HOME 은 타겟 변경 키로 토글과 무관하게 동작).
      2) slots 전체 `press_numpad_scan` → 토글 OFF. 이제 수동 burst 가
         게임 내 자동 시전과 충돌하지 않음.
      3) 부활 burst (VK_NUMPAD6, 0.5s, 200ms 간격).
      4) 자힐 burst (VK_NUMPAD1, 1.0s, 200ms 간격).

    Block B 가 이어서 TAB×3 (격수 복귀) → 토글 재ON.

    slots 결정:
      - cycler 있으면 `cycler.slots` + `_locked` 상태 동기화.
      - cycler 없으면(테스트 단독) `DEFAULT_SLOTS`.
    """
    def _log(s: str):
        if log_fn:
            try:
                log_fn(s)
            except Exception:
                pass
    def _stopped() -> bool:
        if stop_flag is None:
            return False
        try:
            return bool(stop_flag())
        except Exception:
            return False
    sleep_s = 0.1  # 2026-04-23 200→100ms 단축 (사냥 중 7초 대기 줄이기).
    _log(f"[SEQ-A] 진입 cycler={cycler is not None}")
    if _stopped():
        _log("[SEQ-A] 진입 즉시 중단 (stop_flag)")
        return
    # ★ cycler 백그라운드 스레드의 재-lock 을 먼저 차단 (suspend).
    # armed 플래그는 워커 메인 루프가 매 프레임 set_armed(True) 로 덮어쓰므로
    # 그와 독립된 _suspended 플래그 사용 (2026-04-20 버그 수정).
    if cycler is not None:
        try:
            cycler.suspend()
            _log("[SEQ-A] cycler suspend (재-lock 차단)")
        except Exception as e:
            _log(f"[SEQ-A] cycler suspend 예외: {e}")
    # slots 결정.
    if cycler is not None:
        try:
            slots = list(cycler.slots)
        except Exception:
            slots = list(DEFAULT_SLOTS)
    else:
        slots = list(DEFAULT_SLOTS)
    _log(f"[SEQ-A] slots={[hex(v) for v in slots]}")

    # 1) self-target: TAB → HOME → TAB (토글 ON 상태 그대로).
    _log("[SEQ-A] 1) TAB → HOME → TAB (self-target)")
    if _stopped():
        _log("[SEQ-A] 1) 중단 (stop_flag)")
        return
    _press_vk(VK_TAB)
    time.sleep(sleep_s)
    if _stopped():
        _log("[SEQ-A] 1) TAB 후 중단 (stop_flag)")
        return
    _press_vk(VK_HOME, extended=True)
    time.sleep(sleep_s)
    if _stopped():
        _log("[SEQ-A] 1) HOME 후 중단 (stop_flag)")
        return
    _press_vk(VK_TAB)
    time.sleep(sleep_s)
    if _stopped():
        _log("[SEQ-A] 1) TAB 후 중단 (stop_flag)")
        return

    # 2) 토글 OFF.
    _log("[SEQ-A] 2) 토글 OFF")
    for vk in slots:
        try:
            ok = press_numpad_scan(vk)
            _log(f"[SEQ-A]   unlock vk={hex(vk)} ok={ok}")
        except Exception as e:
            _log(f"[SEQ-A]   unlock 예외 vk={hex(vk)}: {e}")
    if cycler is not None:
        try:
            cycler._locked.clear()
        except Exception:
            pass
    time.sleep(sleep_s)
    if _stopped():
        _log("[SEQ-A] 2) 토글 OFF 후 중단 (stop_flag) — burst skip")
        return

    # 3) 부활 burst. 2026-04-23 0.5s → 0.3s 단축.
    _log(f"[SEQ-A] 3) 부활 burst vk={hex(_VK_NUMPAD6)} 0.3s")
    n_rev = _burst_press(_VK_NUMPAD6, 0.3, sleep_s, log_fn, "부활",
                         stop_flag=stop_flag)
    _log(f"[SEQ-A] 3) 부활 press {n_rev}회")
    if _stopped():
        _log("[SEQ-A] 3) 부활 burst 후 중단 (stop_flag) — 자힐 skip")
        return

    # 4) 자힐 burst. 2026-04-23 1.0s → 0.5s 단축.
    _log(f"[SEQ-A] 4) 자힐 burst vk={hex(_VK_NUMPAD1)} 0.5s")
    n_heal = _burst_press(_VK_NUMPAD1, 0.5, sleep_s, log_fn, "자힐",
                          stop_flag=stop_flag)
    _log(f"[SEQ-A] 4) 자힐 press {n_heal}회")
    _log("[SEQ-A] 완료")


def block_b_return_to_attacker(
    cycler=None,
    log_fn: Optional[Callable[[str], None]] = None,
    stop_flag: Optional[Callable[[], bool]] = None,
    defer_tab: bool = False,
) -> None:
    """블록 B: TAB×3 (격수 재타겟팅) → 토글 재ON.

    2026-04-23 defer_tab: 맵 전환 중이면 ESC만 하고 TAB×2 스킵. 새 맵 도착
    후 worker가 별도로 TAB×2 시전해 격수 고정 (사용자 지시).

    Block A 가 이미 numlock 토글 OFF 를 수행했다고 가정 (2026-04-20 사용자
    지시). Block B 는 격수 타겟 복귀 + 주력 힐 토글 다시 걸기만.

    흐름:
      1) ESC → TAB → TAB (50ms 간격) — self-target 해제 + 격수 재타겟팅.
      2) 50ms.
      3) slots 전체 `skill_lock_vk` → 토글 재ON (자동 시전 재개).

    **재lock 은 반드시 TAB 재타겟팅 뒤**. self-target 상태에서 토글 걸면
    주력 힐이 자기 자신에게 물린 채 돌아감.

    Patch 2.9 (2026-04-20): TAB×3 250ms → ESC→TAB→TAB 50ms. 사용자 피드백:
    맨 앞 ESC 로 self-target 해제 뒤 TAB×2 로 격수 순환이 안정적.
    """
    def _log(s: str):
        if log_fn:
            try:
                log_fn(s)
            except Exception:
                pass
    def _stopped() -> bool:
        if stop_flag is None:
            return False
        try:
            return bool(stop_flag())
        except Exception:
            return False
    sleep_s = 0.1  # 2026-04-23 200→100ms 단축.
    _log(f"[SEQ-B] 진입 cycler={cycler is not None}")
    # 2026-04-24 사용자 지시: SEQ-B는 항상 ESC만 실행. TAB×2와 토글 재ON은
    # worker가 red_raw=True (격수 빨탭 감지) + 맵 동기화 조건 시 묶어서 처리.
    # 이전 구조(일반모드: ESC+TAB+TAB+재ON)는 격수 화면 밖이면 self-target
    # 고착 → 메인힐/부활이 self에 발동 → 가짜 격수부활 9회 등 문제 발생.
    # defer_tab 파라미터는 이제 무시 (backward compat 유지).
    _log("[SEQ-B] 1) ESC only — 토글 OFF 유지, TAB×2/재ON은 worker가 "
         "red_raw 감지 시 처리")
    try:
        _press_vk(VK_ESCAPE, extended=False)
    except Exception as e:
        _log(f"[SEQ-B] ESC 예외: {e}")
    time.sleep(sleep_s)
    _log("[SEQ-B] 완료 (ESC only, 재ON 보류)")


def run_block_a_test(cycler=None,
                     log_fn: Optional[Callable[[str], None]] = None) -> None:
    """테스트 버튼 전용 — 블록 A 1회 실행.

    cycler: NumLockCycler (힐러 워커 실행 중일 때 제공). None 이면 DEFAULT_SLOTS.
    """
    block_a_self_target(cycler=cycler, log_fn=log_fn)


def run_block_b_test(cycler=None,
                     log_fn: Optional[Callable[[str], None]] = None) -> None:
    """테스트 버튼 전용 — 블록 B 1회 실행."""
    block_b_return_to_attacker(cycler=cycler, log_fn=log_fn)


def run_block_ab_combined(
    cycler=None,
    log_fn: Optional[Callable[[str], None]] = None,
    key_release_fn: Optional[Callable[[], None]] = None,
    stop_flag: Optional[Callable[[], bool]] = None,
    defer_tab_fn: Optional[Callable[[], bool]] = None,
) -> None:
    """F11 / 자힐 통합 — 블록 A → 블록 B 연속 실행.

    사용자 지시 2026-04-20: "A랑 B랑 붙여서 F11 로 만들어봐".
    사용자 지시 2026-04-20 (추가): "A+B 로직 들어갈 때 모든 방향키 정지".

    흐름:
      0) key_release_fn() — 방향키 U/D/L/R 전부 release (이동 정지).
      1) block_a_self_target  (TAB→HOME→TAB → 토글 OFF → 부활/자힐 burst).
      2) block_b_return_to_attacker (ESC→TAB→TAB → 토글 재ON).

    Args:
      key_release_fn: () -> None. 호출 시 모든 방향키 release_all. 워커는
                     `keys.release_all`, main_window 테스트는 worker._keys
                     참조로 주입. None 이면 skip (스탠드얼론 테스트).
    """
    def _log(s: str):
        if log_fn:
            try:
                log_fn(s)
            except Exception:
                pass
    _log("[SEQ-AB] 진입 — A → B 연속 실행")
    # 0) 방향키 전부 정지 — A+B 시퀀스 중 이동 간섭 차단.
    if key_release_fn is not None:
        try:
            key_release_fn()
            _log("[SEQ-AB] 0) 방향키 release_all")
        except Exception as e:
            _log(f"[SEQ-AB] 0) release_all 예외: {e}")
    block_a_self_target(cycler=cycler, log_fn=log_fn, stop_flag=stop_flag)
    # 블록 B 진입 시점에 맵 전환 중이면 TAB×2 스킵 (격수는 화면 밖이라 잡혀봐야
    # 실패). defer_tab_fn 콜백으로 worker 현재 상태 질의.
    _defer = False
    if defer_tab_fn is not None:
        try:
            _defer = bool(defer_tab_fn())
        except Exception:
            _defer = False
    block_b_return_to_attacker(cycler=cycler, log_fn=log_fn,
                               stop_flag=stop_flag, defer_tab=_defer)
    _log(f"[SEQ-AB] 완료 (defer_tab={_defer})")
