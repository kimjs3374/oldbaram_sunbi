"""키 입력. SendInput (포그라운드) + PostMessage (백그라운드).

Human-like: random keydown duration + jitter.
"""
import ctypes
import random
import time
from ctypes import wintypes
from typing import List, Optional

import psutil

user32 = ctypes.WinDLL("user32", use_last_error=True)

VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28

VK_MAP = {"L": VK_LEFT, "U": VK_UP, "R": VK_RIGHT, "D": VK_DOWN}

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101

PUL = ctypes.POINTER(ctypes.c_ulong)

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", PUL)]

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", PUL)]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]

class _II(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("ii", _II)]

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_SCANCODE = 0x0008
MAPVK_VK_TO_VSC_EX = 4

# Mouse event flags.
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010


def mouse_click_at(x: int, y: int, *, button: str = "left",
                   settle_ms: int = 30, hold_ms: int = 35) -> bool:
    """절대 화면 좌표 (x,y) 에 마우스 클릭.

    2026-04-23 추가. 자힐 후 self-target 고착 해소용 — 격수 화면 위치 찾아
    좌클릭해 재타겟.
    """
    try:
        user32.SetCursorPos(int(x), int(y))
        time.sleep(max(0, settle_ms) / 1000.0)
        if button == "right":
            down, up = MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP
        else:
            down, up = MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP
        user32.mouse_event(down, 0, 0, 0, 0)
        time.sleep(max(0, hold_ms) / 1000.0)
        user32.mouse_event(up, 0, 0, 0, 0)
        return True
    except Exception:
        return False


def _vk_to_scan(vk: int) -> int:
    return user32.MapVirtualKeyExW(vk, MAPVK_VK_TO_VSC_EX, 0) & 0xFF


def _send_input(vk: int, up: bool):
    # 2026-04-21: 방향키도 일반 VK 기반 keybd_event 로 전환.
    # Patch 2.20 의 `0xE000|scan + KEYEVENTF_SCANCODE` 방식은 사용자 환경에서
    # 방향키가 게임에 먹지 않는 현상 유발. 옛바 수동 방향키 입력은 정상 동작
    # 확인됐으므로 가장 naive 한 keybd_event(vk, scan, flags) 로 복귀.
    scan = _vk_to_scan(vk)
    is_arrow = vk in (VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN)
    flags = KEYEVENTF_EXTENDEDKEY if is_arrow else 0
    if up:
        flags |= KEYEVENTF_KEYUP
    try:
        user32.keybd_event(vk, scan, flags, 0)
    except Exception:
        pass
    # 동시에 로그로 실제 송신 추적 (방향키 먹지 않는 원인 진단용).
    try:
        _DOWN_LOG(vk, scan, flags, up)
    except Exception:
        pass


_DOWN_LOG_FN = None  # 외부에서 set_down_debug(log_fn) 으로 주입.


def set_down_debug(fn):
    global _DOWN_LOG_FN
    _DOWN_LOG_FN = fn


def _DOWN_LOG(vk: int, scan: int, flags: int, up: bool):
    if _DOWN_LOG_FN is None:
        return
    try:
        tag = "UP" if up else "DOWN"
        _DOWN_LOG_FN(
            f"[VK-SEND] vk={hex(vk)} scan={hex(scan)} "
            f"flags={hex(flags)} {tag}"
        )
    except Exception:
        pass


def _post(hwnd: int, vk: int, up: bool):
    if up:
        lparam = (1) | (1 << 24) | (1 << 30) | (1 << 31)
        user32.PostMessageW(hwnd, WM_KEYUP, vk, lparam)
    else:
        lparam = (1) | (1 << 24)
        user32.PostMessageW(hwnd, WM_KEYDOWN, vk, lparam)


def find_windows_by_process(proc_name: str) -> List[int]:
    """프로세스 이름(예: 'msw.exe')으로 visible top-level HWND 목록 반환."""
    pn = proc_name.lower()
    pids = {p.info["pid"] for p in psutil.process_iter(["pid", "name"])
            if p.info.get("name") and p.info["name"].lower() == pn}
    if not pids:
        return []
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            # 타이틀 있는 top-level만 (child/hidden 배제)
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                found.append(int(hwnd))
        return True

    user32.EnumWindows(cb, 0)
    return found


def find_window(target: str) -> Optional[int]:
    """target이 '.exe'로 끝나면 프로세스 이름 매칭, 아니면 창 제목 매칭."""
    if target.lower().endswith(".exe"):
        wins = find_windows_by_process(target)
        return wins[0] if wins else None
    hwnd = user32.FindWindowW(None, target)
    if hwnd:
        return int(hwnd)
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if target.lower() in buf.value.lower():
                found.append(int(hwnd))
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


class KeyController:
    def __init__(self, window_name: str = "msw", method: str = "postmessage",
                 keydown_ms_min: int = 30, keydown_ms_max: int = 70,
                 jitter_ms: int = 50):
        self.method = method
        self.hwnd = find_window(window_name)
        self.keydown_ms_min = keydown_ms_min
        self.keydown_ms_max = keydown_ms_max
        self.jitter_ms = jitter_ms
        self._held = {}  # dir → start_time
        # 2026-04-20 Patch 2.14: 이동 잠금 플래그.
        # True 면 hold/tap 이 즉시 return (방향키 press 억제). A+B 시퀀스
        # (자힐/자가부활) 동안 TAB/HOME 과 방향키 경쟁 방지용.
        # release/release_all 은 lock 과 무관하게 동작 (이미 눌린 키는 뗄 수 있게).
        self._movement_locked: bool = False

    def _down(self, vk: int):
        if self.method == "sendinput" or self.hwnd is None:
            _send_input(vk, up=False)
        else:
            _post(self.hwnd, vk, up=False)

    def _up(self, vk: int):
        if self.method == "sendinput" or self.hwnd is None:
            _send_input(vk, up=True)
        else:
            _post(self.hwnd, vk, up=True)

    def set_movement_lock(self, on: bool) -> None:
        """방향키 press 잠금 on/off.

        on=True 진입 시 현재 held 인 방향 즉시 release. off 시 메인 루프가
        자연스럽게 want 에 따라 다시 hold 함.
        """
        self._movement_locked = bool(on)
        if self._movement_locked:
            self.release_all()

    def is_movement_locked(self) -> bool:
        return self._movement_locked

    def tap(self, direction: str):
        """짧은 tap. 실시간 따라가기엔 hold가 더 자연스러움."""
        if self._movement_locked:
            return
        vk = VK_MAP.get(direction)
        if vk is None:
            return
        self._down(vk)
        dur = random.uniform(self.keydown_ms_min, self.keydown_ms_max) / 1000
        time.sleep(dur)
        self._up(vk)

    def hold(self, direction: str):
        """key down만. release까지 이어서 누름 상태 유지."""
        if self._movement_locked:
            return
        if direction in self._held:
            return
        vk = VK_MAP.get(direction)
        if vk is None:
            return
        self._down(vk)
        self._held[direction] = time.time()

    def release(self, direction: str):
        vk = VK_MAP.get(direction)
        if vk is None:
            return
        # 2026-04-24 thread-safe: check-then-del 패턴은 워커 stop 시 멀티
        # 스레드 동시 release 경쟁으로 KeyError 유발. dict.pop 으로 atomic 처리.
        if self._held.pop(direction, None) is None:
            return
        self._up(vk)

    def release_all(self):
        # snapshot 기반 순회. release 도 thread-safe (pop) 이라 race 안전.
        for d in list(self._held.keys()):
            try:
                self.release(d)
            except Exception:
                pass
