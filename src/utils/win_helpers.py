"""Win32/이미지 공용 헬퍼.

원본 healer_gui.py 상단에서 분리. 행동 변경 없음.
"""
from __future__ import annotations
import ctypes

import cv2
import numpy as np
from PyQt5 import QtGui

_user32 = ctypes.WinDLL("user32", use_last_error=True)


def _is_fg_hwnd(hwnd) -> bool:
    if not hwnd:
        return False
    try:
        return int(_user32.GetForegroundWindow()) == int(hwnd)
    except Exception:
        return False


def frame_to_qpix(frame: np.ndarray, max_w: int = 640) -> QtGui.QPixmap:
    H, W = frame.shape[:2]
    scale = min(1.0, max_w / W)
    if scale < 1.0:
        frame = cv2.resize(frame, (int(W * scale), int(H * scale)))
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    qimg = QtGui.QImage(rgb.data, w, h, ch * w, QtGui.QImage.Format_RGB888)
    return QtGui.QPixmap.fromImage(qimg.copy())
