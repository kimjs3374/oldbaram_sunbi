"""F1 키 down 감지 — 격수 워프 트리거. v1 attacker_v2._run_gui 의 _Win32F1Key 1:1.

audit 8.1 3단계 분할.
"""
from __future__ import annotations


class Win32F1Key:
    """Windows GetAsyncKeyState 로 F1 down 여부 확인."""

    VK_F1 = 0x70

    def __init__(self) -> None:
        import ctypes
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)

    def is_down(self) -> bool:
        try:
            s = self._user32.GetAsyncKeyState(self.VK_F1)
            return bool(s & 0x8000)
        except Exception:
            return False
