"""NumLock 싸이클 - 도사 주력 힐 자동 시전 (v3, old.oldbaram 이식).

실제 옛바 메커니즘 (old.oldbaram/remote_healer.py 검증됨):
1. 시작 시 1회, 각 스킬 키(NumPad X)에 대해 `skill_lock` 실행:
   - NumLock ON 보장
   - 해당 키의 NumPad scancode DOWN (KEYEVENTF_SCANCODE)
   - NumLock 토글 (ON→OFF)
   - 스캔코드 UP
   - NumLock 토글 (OFF→ON 복구)
   이 과정이 "해당 키를 스킬키로 잠금(lock)"하는 역할.
2. 그 후에는 일반 숫자키 "1","2"... 를 press 하면 스킬 발동.
   (NumPad 쪽이 아니라 메인 키보드 위쪽 숫자키!)
3. 쿨다운 기반 반복 press로 연속 사용.

긴급 차단: armed=False 또는 프로세스 종료.
"""
import ctypes
import random
import threading
import time

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

# NumPad VK → (NumPad scancode, 일반 숫자키 VK)
# (old.oldbaram/remote_healer.py의 NUMPAD_SCAN 테이블 기반)
NUMPAD_VK_MAP = {
    VK_NUMPAD0: {"scan": 0x52, "normal_vk": 0x30},  # "0"
    VK_NUMPAD1: {"scan": 0x4F, "normal_vk": 0x31},  # "1"
    VK_NUMPAD2: {"scan": 0x50, "normal_vk": 0x32},  # "2"
    VK_NUMPAD3: {"scan": 0x51, "normal_vk": 0x33},  # "3"
    VK_NUMPAD4: {"scan": 0x4B, "normal_vk": 0x34},  # "4"
    VK_NUMPAD5: {"scan": 0x4C, "normal_vk": 0x35},  # "5"
    VK_NUMPAD6: {"scan": 0x4D, "normal_vk": 0x36},  # "6"
    VK_NUMPAD7: {"scan": 0x47, "normal_vk": 0x37},  # "7"
    VK_NUMPAD8: {"scan": 0x48, "normal_vk": 0x38},  # "8"
    VK_NUMPAD9: {"scan": 0x49, "normal_vk": 0x39},  # "9"
}


def is_numlock_on() -> bool:
    return bool(user32.GetKeyState(VK_NUMLOCK) & 1)


def _numlock_press():
    """NumLock 키 한 번 눌러 상태 반전."""
    _keybd(VK_NUMLOCK, 0x45, KEYEVENTF_EXTENDEDKEY, 0)
    time.sleep(0.05)
    _keybd(VK_NUMLOCK, 0x45, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
    time.sleep(0.05)


def _ensure_numlock_on():
    if not is_numlock_on():
        _numlock_press()
        time.sleep(0.1)


def ensure_numlock_off() -> bool:
    """NumLock이 ON이면 OFF로. (구 시그니처 호환용.)"""
    if not is_numlock_on():
        return False
    _numlock_press()
    return True


_LOCK_DEBUG_FN = None  # 외부에서 set_lock_debug(log_fn) 으로 주입.


def set_lock_debug(fn) -> None:
    """skill_lock_vk 단계별 진단 로그 주입. None 이면 로그 없음.
    (2026-04-20: 토글 안걸리는 문제 원인 파악용.)
    """
    global _LOCK_DEBUG_FN
    _LOCK_DEBUG_FN = fn


def _d(msg: str) -> None:
    if _LOCK_DEBUG_FN is not None:
        try:
            _LOCK_DEBUG_FN(msg)
        except Exception:
            pass


def skill_lock_vk(vk: int) -> bool:
    """시작 시 1회 실행. 해당 NumPad VK를 스킬키로 잠금.

    반환: 수행 여부. (매핑 없는 VK면 False)
    """
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
    # NumPad scancode DOWN
    _keybd(0, scan, KEYEVENTF_SCANCODE, 0)
    _d(f"[LOCK-TRACE]   scan DOWN sent")
    time.sleep(0.1)
    # NumLock OFF
    _numlock_press()
    nl2 = is_numlock_on()
    _d(f"[LOCK-TRACE]   after numlock_press#1 NumLock={nl2} (expect OFF)")
    time.sleep(0.1)
    # scancode UP
    _keybd(0, scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0)
    _d(f"[LOCK-TRACE]   scan UP sent")
    time.sleep(0.05)
    # NumLock ON 복구
    _numlock_press()
    nl3 = is_numlock_on()
    _d(f"[LOCK-TRACE]   after numlock_press#2 NumLock={nl3} (expect ON) — done")
    time.sleep(0.05)
    return True


def press_normal_vk(vk: int, min_ms: int = 40, max_ms: int = 80):
    """잠금 완료된 스킬키 press (일반 숫자키 VK로 변환해 keybd_event).

    NumPad VK가 아니면 그대로 press.

    2026-04-21: 호출 추적 로그 추가. 파력무참(0x68) 같은 VK 의 press 출처
    식별용. 모든 호출이 로그에 남음.
    """
    info = NUMPAD_VK_MAP.get(vk)
    nvk = info["normal_vk"] if info else vk
    scan = user32.MapVirtualKeyW(nvk, 0)
    _d(f"[VK-PRESS] vk={hex(vk)} nvk={hex(nvk)} scan={hex(scan)}")
    _keybd(nvk, scan, 0, 0)
    time.sleep(random.uniform(min_ms, max_ms) / 1000)
    _keybd(nvk, scan, KEYEVENTF_KEYUP, 0)


def press_numpad_direct(vk: int, min_ms: int = 40, max_ms: int = 80) -> bool:
    """NumPad VK 의 scan 코드를 **그대로** 송신 (nvk 변환 안 함).

    NumLock ON 가정. 게임 단축키가 NumPad scan 만 받고 main-row 숫자는
    무시하는 경우 사용. 파혼술처럼 cycler 토글 대상이 아닌 직접 시전 스킬용.
    반환: 수행 여부 (매핑 없는 VK 면 False).
    """
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
    """NumLock ON 상태에서 해당 NumPad 스캔코드 직접 press → 게임에서 토글 OFF.

    사용자 지시: "넘락ON상태에서 해당 숫자키 눌러서 꺼라".
    skill_lock_vk가 NumPad scancode + NumLock 토글로 "잠금(토글 ON)"을
    걸었으므로, 해제에는 NumLock ON을 유지한 채 같은 scancode를 한 번
    press하면 토글 OFF로 되돌아간다.
    """
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
    """주력 힐 슬롯들을 "토글 ON" 상태로 유지.

    사용자 지시: "한번씩 쓰라는게 아니고 토글을 걸어놓으라고".
    → skill_lock_vk 시퀀스 1회가 곧 해당 키의 토글 ON. 그 후 게임이 알아서
      쿨마다 자동 시전. 매크로는 추가 press 하지 않음.

    동작:
    - armed=True이고 NumLock OFF 아니면 각 슬롯에 대해 skill_lock_vk 1회.
    - set_slots로 새 슬롯 추가되면 run 루프 다음 회차에 잠금.
    - armed=False면 기존 잠금은 유지(토글 ON 상태 그대로), 새 슬롯 잠금 보류.
    """

    def __init__(self, hwnd: int = 0, method: str = "sendinput",
                 slots: list = None,
                 poll_ms: int = 200,
                 start_delay_sec: float = 1.0):
        super().__init__(daemon=True)
        self.hwnd = hwnd            # 현재 구현은 무시 (keybd_event 직접 호출).
        self.method = method
        self.slots = list(slots) if slots else list(DEFAULT_SLOTS)
        self.poll_ms = poll_ms
        self.start_delay_sec = start_delay_sec  # 게임 창 포커스 옮길 시간 확보.
        self.armed = False
        self._stop = False
        self._lock = threading.Lock()
        self._locked = set()        # skill_lock 수행된 VK 집합.
        # 블록A/B 시퀀스 중 백그라운드 재-lock 차단용. armed 는 메인 루프가
        # 매 프레임 재주입하므로 별도 플래그로 관리 (2026-04-20 버그 수정).
        self._suspended = False
        # 2026-04-21: 초기 lock 시퀀스 완료 플래그. scheduler 가 이 값이 True
        # 될 때까지 큐/polling 시전 유예 → Shift+Z cast 와 NumPad scan 충돌
        # 방지 (봉황의기원 토글 꼬임 수정).
        self._initial_lock_done = False
        self._log_fn = None

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

    def suspend(self):
        """블록A/B 시퀀스 등 임시로 백그라운드 재-lock 차단.
        armed 플래그와 독립 — 메인 루프가 set_armed(True) 호출해도 영향 없음.
        """
        with self._lock:
            self._suspended = True

    def resume(self):
        with self._lock:
            self._suspended = False

    def set_slots(self, slots: list):
        with self._lock:
            self.slots = list(slots)

    def stop(self):
        self._stop = True

    def is_initial_lock_done(self) -> bool:
        """초기 slots 전부 lock 완료 여부. scheduler 가 시전 시작 전에 확인."""
        return bool(self._initial_lock_done)

    def _ensure_lock(self, slots):
        for vk in slots:
            if vk in self._locked:
                continue
            if skill_lock_vk(vk):
                self._locked.add(vk)
                self._log(f"[CYCLE] lock vk={hex(vk)} (토글 ON)")

    def run(self):
        dt = max(0.05, self.poll_ms / 1000)
        last_state = None
        # 시작 딜레이: 사용자가 게임 창 포커스 옮길 시간 확보.
        if self.start_delay_sec > 0:
            self._log(f"[CYCLE] 시작 딜레이 {self.start_delay_sec:.1f}s")
            t_end = time.time() + self.start_delay_sec
            while time.time() < t_end and not self._stop:
                time.sleep(0.1)
            if self._stop:
                return
            self._log("[CYCLE] 딜레이 종료, 잠금 시작")
        try:
            while not self._stop:
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
                    # 2026-04-21: 모든 slots 가 lock 완료된 순간 초기 플래그 set.
                    # scheduler 가 이 값을 보고 큐 처리 시작.
                    if (not self._initial_lock_done
                            and self._locked
                            and all(vk in self._locked for vk in slots)):
                        self._initial_lock_done = True
                        self._log("[CYCLE] 초기 lock 완료 — scheduler 시전 허용")
                time.sleep(dt)
        finally:
            # 종료 시 토글 ON 상태로 남아있던 슬롯들을 각 1회 press해서
            # 토글 OFF로 되돌림 + NumLock 자체도 OFF 복귀. 사용자 요구:
            # "원격 정지 시 넙락 걸려있던 스킬 다시 눌러 풀고 끝나게".
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
        """_locked 집합에 있는 슬롯 VK들을 NumLock ON 상태에서 NumPad 스캔코드로
        press → 게임 안 '토글 ON' 해제. 사용자 지시: "넘락ON상태에서 해당 숫자키"."""
        for vk in list(self._locked):
            try:
                press_numpad_scan(vk)
                self._log(f"[CYCLE] unlock vk={hex(vk)} (NumPad scan, 토글 OFF)")
            except Exception:
                pass
        self._locked.clear()
