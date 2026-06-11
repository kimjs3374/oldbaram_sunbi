# -*- coding: utf-8 -*-
"""선비족 사냥 순서 네비게이션 오버레이 (격수 전용, 2026-06-12).

설계: D:\\oldbaram\\선비족 네비게이션 오버레이.md §5.
- x별 굴 배치 지도(슬롯 6개 고정 격자 — 오와열 정렬)를 그리고
  다음 굴 강조(노랑) + 현재 굴(녹색 테두리) 표시.
- 데이터: MainWindow._tick_analytics 가 worker.get_hunt_nav_snapshot()
  폴링 → update_nav(snap). 키 입력 없음(표시 전용).
- 위치 키 "huntnav" — 드래그/저장/투명도/편집모드는 _ScaledOverlay 공통.
"""
from __future__ import annotations

from typing import Optional

from PyQt5 import QtCore, QtGui

from .overlay import _ScaledOverlay
from ..app.hunt_nav import LAYOUTS

_STATE_KR = {
    "idle": "대기",
    "recommend": "추천",
    "learning": "학습중",
    "confirmed": "확정",
    "manual": "수동",
}

# 슬롯 → 격자 좌표 (col 비율, row 인덱스 0~2).
_SLOT_GRID = {
    "TL": (0.33, 0), "TR": (0.67, 0),
    "LM": (0.10, 1), "RM": (0.90, 1),
    "BL": (0.33, 2), "BR": (0.67, 2),
}


class HuntNavOverlay(_ScaledOverlay):
    """선비족 굴 네비게이션 지도 오버레이."""

    def __init__(self):
        super().__init__()
        self._snap: dict = {}
        self._base_w = 230
        self._relayout()

    def update_nav(self, snap: Optional[dict]) -> None:
        s = dict(snap or {})
        if s == self._snap:
            return
        self._snap = s
        self._relayout()
        self.update()

    def _on_scale_changed(self) -> None:
        self._relayout()
        self._reanchor()

    def _reanchor(self) -> None:
        """우선순위: 수동 위치 > game_rect 우상단 > 유지."""
        if self._manual_pos is not None:
            mx, my = self._manual_pos
            cx, cy = self._clamp_to_bound(mx, my)
            self.move(cx, cy)
            return
        if self._game_rect:
            gx, gy, gw, _ = self._game_rect
            cx, cy = self._clamp_to_bound(
                int(gx + gw - self.width() - self._px(8)),
                int(gy) + self._px(8))
            self.move(cx, cy)

    # ── 지오메트리 ───────────────────────────────────────────────────────

    def _grid_metrics(self):
        w = self.width()
        top = self._px(30)          # 타이틀 아래 그리드 시작.
        row_h = self._px(36)
        return w, top, row_h

    def _slot_center(self, slot: str):
        w, top, row_h = self._grid_metrics()
        fx, r = _SLOT_GRID[slot]
        return int(w * fx), int(top + row_h * r + row_h // 2)

    def _relayout(self) -> None:
        w = self._px(self._base_w)
        top = self._px(30)
        row_h = self._px(36)
        grid_h = row_h * 3
        bottom_h = self._px(18) * 2 + self._px(8)
        self.setFixedSize(w, top + grid_h + bottom_h)

    # ── 렌더 ────────────────────────────────────────────────────────────

    def paintEvent(self, _ev):
        qp = QtGui.QPainter(self)
        qp.setRenderHint(QtGui.QPainter.Antialiasing, True)
        # 배경 박스 (투명도 적용).
        qp.setPen(QtCore.Qt.NoPen)
        qp.setBrush(QtGui.QColor(18, 20, 26, self._a(200)))
        radius = self._px(8)
        qp.drawRoundedRect(self.rect(), radius, radius)
        qp.setPen(QtGui.QColor(90, 110, 160, self._a(255)))
        qp.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), radius, radius)

        s = self._snap or {}
        base = str(s.get("base") or "선비족")
        x = int(s.get("x") or 0)
        cur_y = int(s.get("cur_y") or 0)
        next_y = int(s.get("next_y") or 0)
        order = [int(v) for v in (s.get("order") or [])]
        state = str(s.get("state") or "idle")
        out_of_order = bool(s.get("out_of_order"))
        # 강조 타이밍 (사용자 2026-06-12): 굴(7)에서 나와 허브(선비족x) 도착
        # = 강한 강조. 굴 내부 사냥 중엔 다음 굴을 옅은 테두리로만 표시.
        hub_alert = bool(s.get("at_hub")) and bool(s.get("from_z7"))

        left_pad = self._px(12)
        # 타이틀.
        qp.setFont(self._font(10))
        qp.setPen(QtGui.QColor(180, 210, 255))
        if x in LAYOUTS:
            qp.drawText(left_pad, self._px(20), f"{base}{x} 네비")
        else:
            qp.drawText(left_pad, self._px(20), "선비족 네비 — 맵 인식 대기")

        _, top, row_h = self._grid_metrics()
        grid_bottom_y = top + row_h * 3

        if x in LAYOUTS:
            layout = LAYOUTS[x]
            # 링(통로) — 슬롯 중심을 지나는 라운드 사각.
            lm = self._slot_center("LM")
            tl = self._slot_center("TL")
            br_ = self._slot_center("BR")
            ring = QtCore.QRectF(
                lm[0], tl[1],
                self._slot_center("RM")[0] - lm[0],
                self._slot_center("BL")[1] - tl[1],
            )
            qp.setBrush(QtCore.Qt.NoBrush)
            qp.setPen(QtGui.QPen(
                QtGui.QColor(80, 95, 125, self._a(220)), self._px(2)))
            qp.drawRoundedRect(ring, self._px(14), self._px(14))
            # 노드 박스 (고정 크기, 중심 정렬 — 오와열 보장).
            bw, bh = self._px(48), self._px(22)
            qp.setFont(self._font(10))
            for slot, label in layout.items():
                cx, cy = self._slot_center(slot)
                rect = QtCore.QRectF(cx - bw / 2, cy - bh / 2, bw, bh)
                is_entry = (label == 0)
                is_next = (not is_entry and next_y and label == next_y)
                is_cur = (not is_entry and cur_y and label == cur_y)
                in_order = (not is_entry and label in order)
                # 채움/테두리.
                if is_next and hub_alert:
                    # 허브 도착(굴(7) 완주 직후) — 강한 강조.
                    fill = QtGui.QColor(255, 200, 50, self._a(235))
                    pen = QtGui.QPen(QtGui.QColor(255, 255, 255), self._px(2))
                    txt_c = QtGui.QColor(20, 20, 20)
                elif is_next:
                    # 굴 내부 사냥 중 — 다음 굴 옅은 표시.
                    fill = QtGui.QColor(40, 44, 54, self._a(220))
                    pen = QtGui.QPen(
                        QtGui.QColor(255, 200, 50, self._a(255)), self._px(2))
                    txt_c = QtGui.QColor(255, 210, 90)
                elif is_entry:
                    fill = QtGui.QColor(35, 45, 60, self._a(220))
                    pen = QtGui.QPen(
                        QtGui.QColor(90, 110, 150, self._a(255)), 1)
                    txt_c = QtGui.QColor(150, 175, 205)
                else:
                    fill = QtGui.QColor(40, 44, 54, self._a(220))
                    pen = QtGui.QPen(
                        QtGui.QColor(90, 100, 120, self._a(255)), 1)
                    txt_c = (QtGui.QColor(215, 215, 225) if in_order
                             else QtGui.QColor(120, 120, 130))
                if is_cur:
                    pen = QtGui.QPen(QtGui.QColor(110, 230, 130), self._px(2))
                qp.setBrush(fill)
                qp.setPen(pen)
                qp.drawRoundedRect(rect, self._px(5), self._px(5))
                qp.setPen(txt_c)
                text = "입구" if is_entry else (
                    f"▶{label}" if (is_next and hub_alert) else str(label))
                qp.drawText(rect, QtCore.Qt.AlignCenter, text)
        else:
            qp.setFont(self._font(9, bold=False))
            qp.setPen(QtGui.QColor(140, 140, 150))
            qp.drawText(left_pad, top + row_h, "선비족x-y 맵 진입 시 표시")

        # 하단 2줄: 순서/상태 + 다음 굴.
        y_txt = grid_bottom_y + self._px(14)
        qp.setFont(self._font(9, bold=False))
        if order:
            qp.setPen(QtGui.QColor(200, 205, 215))
            qp.drawText(
                left_pad, y_txt,
                f"순서: {'→'.join(map(str, order))} "
                f"({_STATE_KR.get(state, state)})")
        else:
            qp.setPen(QtGui.QColor(140, 140, 150))
            qp.drawText(left_pad, y_txt,
                        f"순서: 미정 ({_STATE_KR.get(state, state)})")
        y_txt += self._px(18)
        if next_y and hub_alert:
            qp.setFont(self._font(10))
            qp.setPen(QtGui.QColor(255, 200, 50))
            qp.drawText(left_pad, y_txt, f"▶ {next_y}굴로 이동!")
        elif next_y:
            qp.setPen(QtGui.QColor(255, 210, 90))
            line = f"다음: {next_y}굴"
            if out_of_order and cur_y:
                line += f"  (현재 {cur_y}굴은 순서 밖)"
            qp.drawText(left_pad, y_txt, line)
        else:
            qp.setPen(QtGui.QColor(140, 140, 150))
            qp.drawText(left_pad, y_txt, "다음: -")
        self._draw_edit_hint(qp)
