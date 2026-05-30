"""NumLock 싸이클 (v2, v1 SoR 1:1 이식 — dist_dosa/src/input/numlock_cycle.py).

v1 healer_worker.py 의 핵심 메커니즘:
1. 시작 시 1회, 각 스킬 키(NumPad X)에 대해 `skill_lock_vk` 실행:
   - NumLock ON 보장
   - 해당 키의 NumPad scancode DOWN (KEYEVENTF_SCANCODE)
   - NumLock 토글 (ON→OFF)
   - 스캔코드 UP
   - NumLock 토글 (OFF→ON 복구)
   이 과정이 "해당 키를 스킬키로 잠금(lock)"하는 역할.
2. 그 후에는 일반 숫자키 "1","2"... 를 press 하면 스킬 발동.
3. 쿨다운 기반 반복 press 로 연속 사용.

v2 마이그레이션 정책 (2026-04-25):
- v1 모듈 1:1 그대로. 외부 인터페이스 동일 (skill_lock_vk, press_normal_vk,
  press_numpad_scan, press_numpad_direct, NumLockCycler 클래스).
- 기존 v2 더미(NumlockCycler-주기적 토글) 폐기. 호환을 위해 NumlockCycler
  alias 유지 — 호출부가 set_armed/suspend/resume 등 v1 메서드 사용 가능.
- enabled flag 도 유지 (default True — armed=True일때만 동작).
"""
from __future__ import annotations

import ctypes
import logging
import random
import threading
import time

log = logging.getLogger("src_v2.hands.numlock")

user32 = ctypes.WinDLL("user32", use_last_error=True)
_keybd = user32.keybd_event

VK_NUMLOCK = 0x90
VK_NUMPAD0 = 0x60
VK_NUMPAD1 = 0x61
VK_NUMPAD2 = 0x62
VK_NUMPAD3 = 0x63
VK_NUMPAD4 = 0x64
VK_NUMPAD5 = 0x65
VK_NUMPAD6 = 0x66
VK_NUMPAD7 = 0x67
VK_NUMPAD8 = 0x68
VK_NUMPAD9 = 0x69

DEFAULT_SLOTS = [VK_NUMPAD1, VK_NUMPAD2, VK_NUMPAD3]

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_SCANCODE = 0x0008

NUMPAD_VK_MAP = {
    VK_NUMPAD0: {"scan": 0x52, "normal_vk": 0x30},
    VK_NUMPAD1: {"scan": 0x4F, "normal_vk": 0x31},
    VK_NUMPAD2: {"scan": 0x50, "normal_vk": 0x32},
    VK_NUMPAD3: {"scan": 0x51, "normal_vk": 0x33},
    VK_NUMPAD4: {"scan": 0x4B, "normal_vk": 0x34},
    VK_NUMPAD5: {"scan": 0x4C, "normal_vk": 0x35},
    VK_NUMPAD6: {"scan": 0x4D, "normal_vk": 0x36},
    VK_NUMPAD7: {"scan": 0x47, "normal_vk": 0x37},
    VK_NUMPAD8: {"scan": 0x48, "normal_vk": 0x38},
    VK_NUMPAD9: {"scan": 0x49, "normal_vk": 0x39},
}


def is_numlock_on() -> bool:
    return bool(user32.GetKeyState(VK_NUMLOCK) & 1)


def _numlock_press():
    _keybd(VK_NUMLOCK, 0x45, KEYEVENTF_EXTENDEDKEY, 0)
    time.sleep(0.05)
    _keybd(VK_NUMLOCK, 0x45, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
    time.sleep(0.05)


def _ensure_numlock_on():
    if not is_numlock_on():
        _numlock_press()
        time.sleep(0.1)


def ensure_numlock_off() -> bool:
    if not is_numlock_on():
        return False
    _numlock_press()
    return True


_LOCK_DEBUG_FN = None


def set_lock_debug(fn) -> None:
    global _LOCK_DEBUG_FN
    _LOCK_DEBUG_FN = fn


def _d(msg: str) -> None:
    if _LOCK_DEBUG_FN is not None:
        try:
            _LOCK_DEBUG_FN(msg)
        except Exception:
            pass


def skill_lock_vk(vk: int) -> bool:
    """시작 시 1회 실행. NumPad VK 를 스킬키 토글 ON 으로 잠금."""
    info = NUMPAD_VK_MAP.get(vk)
    if info is None:
        _d(f"[LOCK-TRACE] vk={hex(vk)} 매핑 없음 — skip")
        return False
    scan = info["scan"]
    nl0 = is_numlock_on()
    _d(f"[LOCK-TRACE] vk={hex(vk)} scan={hex(scan)} start NumLock={nl0}")
    _ensure_numlock_on()
    nl1 = is_numlock_on()
    _d(f"[LOCK-TRACE]   after ensure_on NumLock={nl1}")
    time.sleep(0.05)
    _keybd(0, scan, KEYEVENTF_SCANCODE, 0)
    _d("[LOCK-TRACE]   scan DOWN sent")
    time.sleep(0.1)
    _numlock_press()
    nl2 = is_numlock_on()
    _d(f"[LOCK-TRACE]   after numlock_press#1 NumLock={nl2} (expect OFF)")
    time.sleep(0.1)
    _keybd(0, scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0)
    _d("[LOCK-TRACE]   scan UP sent")
    time.sleep(0.05)
    _numlock_press()
    nl3 = is_numlock_on()
    _d(f"[LOCK-TRACE]   after numlock_press#2 NumLock={nl3} (expect ON) — done")
    time.sleep(0.05)
    return True


def press_normal_vk(vk: int, min_ms: int = 40, max_ms: int = 80):
    """잠금 완료된 스킬키 press (일반 숫자키 VK 변환)."""
    info = NUMPAD_VK_MAP.get(vk)
    nvk = info["normal_vk"] if info else vk
    scan = user32.MapVirtualKeyW(nvk, 0)
    _d(f"[VK-PRESS] vk={hex(vk)} nvk={hex(nvk)} scan={hex(scan)}")
    _keybd(nvk, scan, 0, 0)
    time.sleep(random.uniform(min_ms, max_ms) / 1000)
    _keybd(nvk, scan, KEYEVENTF_KEYUP, 0)


def press_numpad_direct(vk: int, min_ms: int = 40, max_ms: int = 80) -> bool:
    info = NUMPAD_VK_MAP.get(vk)
    if info is None:
        return False
    scan = info["scan"]
    _d(f"[VK-PRESS-NPD] vk={hex(vk)} scan={hex(scan)} (NumPad 직접)")
    _keybd(vk, scan, 0, 0)
    time.sleep(random.uniform(min_ms, max_ms) / 1000)
    _keybd(vk, scan, KEYEVENTF_KEYUP, 0)
    return True


def press_numpad_scan(vk: int, min_ms: int = 40, max_ms: int = 80) -> bool:
    """NumLock ON 상태에서 NumPad scan 직접 press → 토글 OFF."""
    info = NUMPAD_VK_MAP.get(vk)
    if info is None:
        return False
    scan = info["scan"]
    _ensure_numlock_on()
    time.sleep(0.05)
    _keybd(0, scan, KEYEVENTF_SCANCODE, 0)
    time.sleep(random.uniform(min_ms, max_ms) / 1000)
    _keybd(0, scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0)
    time.sleep(0.05)
    return True


class NumLockCycler(threading.Thread):
    """v1 NumLockCycler 1:1 이식.

    armed=True 이면 slots 의 NumPad VK 를 토글 ON 으로 lock. set_slots/suspend/
    resume/set_armed/is_initial_lock_done 등 v1 모든 인터페이스 유지.
    """

    def __init__(self, hwnd: int = 0, method: str = "sendinput",
                 slots=None,
                 poll_ms: int = 200,
                 start_delay_sec: float = 1.0,
                 # v2 facade 호환을 위한 추가 인자 (무시되어도 OK).
                 adapter=None,
                 interval_sec: float = 0.0,
                 enabled: bool = True):
        super().__init__(daemon=True)
        self.hwnd = hwnd
        self.method = method
        self.slots = list(slots) if slots else list(DEFAULT_SLOTS)
        self.poll_ms = poll_ms
        self.start_delay_sec = start_delay_sec
        self.armed = False
        self._stop_flag = False
        self._lock = threading.Lock()
        self._locked = set()
        self._suspended = False
        self._initial_lock_done = False
        self._log_fn = None
        # v2 facade 가 enabled flag 만 토글하는 케이스 호환.
        self.enabled = bool(enabled)
        # adapter / interval_sec 은 v2 호환 슬롯 (구버전 NumlockCycler API).
        # 본 클래스는 v1 동작이므로 사용하지 않음.
        self._adapter = adapter
        self._interval_sec = interval_sec

    def set_log(self, fn):
        self._log_fn = fn

    def _log(self, s: str):
        if self._log_fn:
            try:
                self._log_fn(s)
            except Exception:
                pass

    def set_armed(self, on: bool):
        with self._lock:
            self.armed = on

    def set_enabled(self, on: bool):
        # v2 호환 — armed 와 동의어로 취급.
        self.set_armed(on)
        self.enabled = bool(on)

    def suspend(self):
        with self._lock:
            self._suspended = True

    def resume(self):
        with self._lock:
            self._suspended = False

    def set_slots(self, slots):
        with self._lock:
            self.slots = list(slots)

    def stop(self):
        self._stop_flag = True

    def is_initial_lock_done(self) -> bool:
        return bool(self._initial_lock_done)

    # v2 NumlockCycler 호환 (no-op, 호출되어도 무해).
    def tick(self, now=None) -> bool:
        return False

    def stats(self) -> dict:
        return {
            "armed": self.armed,
            "suspended": self._suspended,
            "locked": [hex(v) for v in sorted(self._locked)],
            "slots": [hex(v) for v in self.slots],
            "initial_lock_done": self._initial_lock_done,
        }

    def _ensure_lock(self, slots):
        for vk in slots:
            if vk in self._locked:
                continue
            self._log(f"[CYCLE] lock 시도 vk={hex(vk)}")
            ok = skill_lock_vk(vk)
            if ok:
                self._locked.add(vk)
                self._log(f"[CYCLE] lock vk={hex(vk)} (토글 ON)")
            else:
                self._log(f"[CYCLE] lock 실패 vk={hex(vk)} (NUMPAD_VK_MAP 미존재)")

    def run(self):
        dt = max(0.05, self.poll_ms / 1000)
        last_state = None
        self._log(f"[CYCLE] thread run 진입 slots={[hex(v) for v in self.slots]}")
        if self.start_delay_sec > 0:
            self._log(f"[CYCLE] 시작 딜레이 {self.start_delay_sec:.1f}s")
            t_end = time.time() + self.start_delay_sec
            while time.time() < t_end and not self._stop_flag:
                time.sleep(0.1)
            if self._stop_flag:
                self._log("[CYCLE] 딜레이 중 stop 신호 — 종료")
                return
            self._log("[CYCLE] 딜레이 종료, 잠금 루프 진입")
        try:
            while not self._stop_flag:
                with self._lock:
                    armed = self.armed
                    suspended = self._suspended
                    slots = list(self.slots)
                cur = (armed, suspended, tuple(slots))
                if cur != last_state:
                    self._log(
                        f"[CYCLE] state armed={armed} "
                        f"suspended={suspended} "
                        f"slots={[hex(v) for v in slots]} "
                        f"locked={[hex(v) for v in sorted(self._locked)]}"
                    )
                    last_state = cur
                if armed and not suspended and slots:
                    self._ensure_lock(slots)
                    if (not self._initial_lock_done
                            and self._locked
                            and all(vk in self._locked for vk in slots)):
                        self._initial_lock_done = True
                        self._log("[CYCLE] 초기 lock 완료 — scheduler 시전 허용")
                time.sleep(dt)
        finally:
            try:
                self._unlock_all()
            except Exception:
                pass
            try:
                ensure_numlock_off()
                self._log("[CYCLE] NumLock OFF 복귀")
            except Exception:
                pass

    def _unlock_all(self):
        for vk in list(self._locked):
            try:
                press_numpad_scan(vk)
                self._log(f"[CYCLE] unlock vk={hex(vk)} (NumPad scan, 토글 OFF)")
            except Exception:
                pass
        self._locked.clear()


# v2 호환 alias — 기존 healer_worker_v2.py 가 NumlockCycler 이름으로 import.
NumlockCycler = NumLockCycler


class _NullNumlock:
    """v2 테스트용 — NumLock 토글 어댑터 noop."""

    def __init__(self):
        self.toggle_count = 0

    def toggle_numlock(self) -> None:
        self.toggle_count += 1
