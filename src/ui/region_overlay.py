"""힐러 PC 인식 영역 시각화 오버레이 (전체화면 click-through).

등록된 영역들(게임/맵/좌표/쿨/닉/경험치/체력/마력)을 초록 형광 테두리 +
라벨로 그린다. 마우스 입력은 전부 통과하여 실제 게임 조작을 방해하지 않음.
"""
from __future__ import annotations
from typing import Optional

from PyQt5 import QtCore, QtGui, QtWidgets


REGION_LABEL_KR = {
    "game":  "게임영역",
    "map":   "맵영역",
    "coord": "좌표영역",
    "cd":    "쿨영역",
    "nick":  "닉네임영역",
    "xp":    "경험치영역",
    "hp":    "체력영역",
    "mp":    "마력영역",
}


class RegionOverlay(QtWidgets.QWidget):
    """screen 전체를 덮는 click-through 오버레이.

    set_regions({kind: (x,y,w,h)}) 호출 시 즉시 리렌더.
    kind: 위 REGION_LABEL_KR 의 키.
    """

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool
            | QtCore.Qt.WindowTransparentForInput
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        screen = QtWidgets.QApplication.primaryScreen()
        self._screen_geo = screen.geometry()
        self.setGeometry(self._screen_geo)
        self._boxes: dict[str, tuple[int, int, int, int]] = {}

    def set_regions(self, regions: dict) -> None:
        """regions: {kind: (x,y,w,h)} 절대 화면 좌표."""
        self._boxes = {}
        for k, v in (regions or {}).items():
            if not v:
                continue
            try:
                x, y, w, h = int(v[0]), int(v[1]), int(v[2]), int(v[3])
            except Exception:
                continue
            if w > 0 and h > 0:
                self._boxes[str(k)] = (x, y, w, h)
        self.update()

    def paintEvent(self, _ev):
        qp = QtGui.QPainter(self)
        qp.setRenderHint(QtGui.QPainter.Antialiasing, True)
        font = QtGui.QFont("Malgun Gothic", 9, QtGui.QFont.Bold)
        qp.setFont(font)
        fm = qp.fontMetrics()
        origin_x = self._screen_geo.x()
        origin_y = self._screen_geo.y()
        for kind, (x, y, w, h) in self._boxes.items():
            # widget 좌표 = 화면 좌표 - widget topLeft.
            rx = x - origin_x
            ry = y - origin_y
            pen = QtGui.QPen(QtGui.QColor(0, 255, 60, 230), 2)
            qp.setPen(pen)
            qp.setBrush(QtCore.Qt.NoBrush)
            qp.drawRect(rx, ry, w, h)
            # 라벨 박스 (상단 좌측 — 화면 위로 넘치면 내부 상단).
            label = REGION_LABEL_KR.get(kind, kind)
            tw = fm.horizontalAdvance(label) + 10
            th = fm.height() + 2
            lx = rx
            ly = ry - th
            if ly < 0:
                ly = ry + 2
            qp.setPen(QtCore.Qt.NoPen)
            qp.setBrush(QtGui.QColor(0, 140, 20, 210))
            qp.drawRect(lx, ly, tw, th)
            qp.setPen(QtGui.QColor(255, 255, 255))
            qp.drawText(lx + 5, ly + fm.ascent(), label)
