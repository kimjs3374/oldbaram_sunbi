"""Windows 헬퍼 (v2 native).

2026-05-05 Cycle 5-1 — src/input/keys.find_windows_by_process +
src/capture/screen.get_window_rect 의 핵심 기능 native 이전.

V2MainWindow._track_msw_origin 등이 src.* 의존 없이 동작하도록.
"""
from __future__ import annotations
import ctypes
from ctypes import wintypes
from typing import List, Optional, Dict

try:
    import psutil  # type: ignore
except Exception:  # noqa: BLE001
    psutil = None  # type: ignore[assignment]


_user32 = ctypes.WinDLL("user32", use_last_error=True)


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def find_windows_by_process(proc_name: str) -> List[int]:
    """프로세스 이름(예: 'msw.exe')으로 visible top-level HWND 목록 반환.

    v1 src/input/keys.py:137-160 1:1 동치. psutil + EnumWindows 패턴.
    psutil 미설치 시 빈 리스트 반환.
    """
    if psutil is None:
        return []
    pn = str(proc_name).lower()
    try:
        pids = {p.info["pid"] for p in psutil.process_iter(["pid", "name"])
                if p.info.get("name") and p.info["name"].lower() == pn}
    except Exception:  # noqa: BLE001
        return []
    if not pids:
        return []
    found: List[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not _user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            length = _user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                found.append(int(hwnd))
        return True

    _user32.EnumWindows(cb, 0)
    return found


def get_client_rect_dict(hwnd: int) -> Optional[Dict[str, int]]:
    """HWND 의 클라이언트 영역 → mss monitor dict 형식.

    v1 src/capture/screen.py:26-37 1:1 동치.
    Returns: {"left": x, "top": y, "width": w, "height": h} 또는 None.
    """
    if not hwnd:
        return None
    rect = _RECT()
    if not _user32.GetClientRect(int(hwnd), ctypes.byref(rect)):
        return None
    pt = wintypes.POINT(0, 0)
    _user32.ClientToScreen(int(hwnd), ctypes.byref(pt))
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return None
    return {"left": int(pt.x), "top": int(pt.y), "width": int(w), "height": int(h)}
