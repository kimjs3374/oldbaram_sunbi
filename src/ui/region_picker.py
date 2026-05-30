from __future__ import annotations
import ctypes
import logging
import os
import time
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets

from ..utils.logger_setup import _setup_logger
from ..utils.win_helpers import _user32, _is_fg_hwnd, frame_to_qpix



class RegionPicker(QtWidgets.QWidget):
    """전체화면 반투명 오버레이. 드래그로 사각 영역 선택.

    사용: picker = RegionPicker(on_selected); picker.show()
    on_selected(x, y, w, h) 콜백은 스크린 절대 좌표. 취소는 ESC.
    """
    region_selected = QtCore.pyqtSignal(int, int, int, int)
    cancelled = QtCore.pyqtSignal()

    def __init__(self, label: str = ""):
        super().__init__()
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setCursor(QtCore.Qt.CrossCursor)
        # 전체 화면 크기 (다중 모니터는 primary만).
        screen = QtWidgets.QApplication.primaryScreen()
        self._screen_geo = screen.geometry()
        self.setGeometry(self._screen_geo)
        self._start: QtCore.QPoint = None
        self._end: QtCore.QPoint = None
        self._label_text: str = str(label or "")

    def keyPressEvent(self, ev):
        if ev.key() == QtCore.Qt.Key_Escape:
            self.cancelled.emit()
            self.close()
            return
        super().keyPressEvent(ev)

    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            self._start = ev.pos()
            self._end = ev.pos()
            self.update()

    def mouseMoveEvent(self, ev):
        if self._start is not None:
            self._end = ev.pos()
            self.update()

    def mouseReleaseEvent(self, ev):
        if self._start is None or self._end is None:
            return
        x1 = min(self._start.x(), self._end.x())
        y1 = min(self._start.y(), self._end.y())
        x2 = max(self._start.x(), self._end.x())
        y2 = max(self._start.y(), self._end.y())
        w = x2 - x1; h = y2 - y1
        # 스크린 절대 좌표로 변환 (primary 기준 QWidget 좌표 = 화면 좌표).
        gx = self._screen_geo.x() + x1
        gy = self._screen_geo.y() + y1
        if w < 5 or h < 5:
            self.cancelled.emit()
        else:
            self.region_selected.emit(int(gx), int(gy), int(w), int(h))
        self.close()

    def paintEvent(self, _ev):
        qp = QtGui.QPainter(self)
        qp.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 90))
        if self._start and self._end:
            rect = QtCore.QRect(self._start, self._end).normalized()
            qp.setCompositionMode(QtGui.QPainter.CompositionMode_Clear)
            qp.fillRect(rect, QtCore.Qt.transparent)
            qp.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
            pen = QtGui.QPen(QtGui.QColor(0, 255, 0, 220), 2)
            qp.setPen(pen)
            qp.drawRect(rect)
        if self._label_text:
            qp.setPen(QtGui.QColor(255, 255, 255))
            qp.setBrush(QtGui.QColor(0, 120, 0, 230))
            font = QtGui.QFont("Malgun Gothic", 12, QtGui.QFont.Bold)
            qp.setFont(font)
            fm = qp.fontMetrics()
            msg = f"{self._label_text} 영역 드래그 (ESC=취소)"
            tw = fm.horizontalAdvance(msg) + 20
            th = fm.height() + 10
            qp.setPen(QtCore.Qt.NoPen)
            qp.drawRect(20, 20, tw, th)
            qp.setPen(QtGui.QColor(255, 255, 255))
            qp.drawText(30, 20 + fm.ascent() + 5, msg)


