"""게임창 절대좌표 ↔ 창 상대좌표 변환 헬퍼 (v2 native).

2026-05-05 Cycle 5-1 — src/utils/window_geom.py 1:1 native port.
GetWindowRect 는 DWM shadow 포함 (border 포함 절대 좌표). 클라이언트 영역만
필요하면 win_helpers.get_client_rect_dict 사용.
"""
from __future__ import annotations
import ctypes
from typing import Optional, Tuple

_user32 = ctypes.WinDLL("user32", use_last_error=True)


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def get_window_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    """GetWindowRect — (left, top, right, bottom) 튜플. DWM shadow 포함."""
    if not hwnd:
        return None
    r = _RECT()
    if not _user32.GetWindowRect(int(hwnd), ctypes.byref(r)):
        return None
    return (int(r.left), int(r.top), int(r.right), int(r.bottom))


def abs_to_rel(hwnd: int, x: int, y: int) -> Optional[Tuple[int, int]]:
    """절대 화면 좌표 → 게임창 좌상단 기준 상대 좌표."""
    wr = get_window_rect(hwnd)
    if wr is None:
        return None
    return (int(x - wr[0]), int(y - wr[1]))


def rel_to_abs(hwnd: int, x: int, y: int) -> Optional[Tuple[int, int]]:
    """창 상대 좌표 → 현재 창 위치 기준 절대 좌표."""
    wr = get_window_rect(hwnd)
    if wr is None:
        return None
    return (int(x + wr[0]), int(y + wr[1]))
