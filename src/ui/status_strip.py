"""격수맵 / 힐러맵 / 격수좌표 / 힐러좌표 / 현재상태 5필드 한글 상태 바."""
from __future__ import annotations
from typing import Optional

from PyQt5 import QtCore, QtWidgets

from ..utils.state_names import fsm_kr


class StatusStrip(QtWidgets.QGroupBox):
    """GUI 중앙에 고정되는 간단 상태 패널. 외부에서 update_*()로 갱신.

    - 격수맵 / 힐러맵 : OCR 결과 (문자열)
    - 격수좌표 / 힐러좌표 : (x,y) 튜플 → "x,y"
    - 현재상태 : FSM state 영문 → 한글 매핑 (utils.state_names)
    """

    def __init__(self, parent=None):
        super().__init__("기본 정보", parent)
        g = QtWidgets.QGridLayout(self)
        g.setContentsMargins(6, 4, 6, 4)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(2)

        self.lbl_atk_map = QtWidgets.QLabel("-")
        self.lbl_hlr_map = QtWidgets.QLabel("-")
        self.lbl_atk_coord = QtWidgets.QLabel("-")
        self.lbl_hlr_coord = QtWidgets.QLabel("-")
        self.lbl_state = QtWidgets.QLabel("-")

        for w in (self.lbl_atk_map, self.lbl_hlr_map,
                  self.lbl_atk_coord, self.lbl_hlr_coord, self.lbl_state):
            w.setStyleSheet("font-weight:600; color:#4338ca;")
            w.setMinimumWidth(60)
            w.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

        pairs = [
            ("격수맵",   self.lbl_atk_map),
            ("힐러맵",   self.lbl_hlr_map),
            ("격수좌표", self.lbl_atk_coord),
            ("힐러좌표", self.lbl_hlr_coord),
        ]
        for i, (k, v) in enumerate(pairs):
            r, c = divmod(i, 2)
            key = QtWidgets.QLabel(k)
            key.setStyleSheet("color:#475467; font-weight:500;")
            g.addWidget(key, r, c * 2)
            g.addWidget(v, r, c * 2 + 1)

        key2 = QtWidgets.QLabel("현재상태")
        key2.setStyleSheet("color:#475467; font-weight:500;")
        g.addWidget(key2, 2, 0)
        g.addWidget(self.lbl_state, 2, 1, 1, 3)

    @staticmethod
    def _fmt_coord(c) -> str:
        if c is None:
            return "-"
        try:
            x, y = c
            return f"{int(x)},{int(y)}"
        except Exception:
            return str(c)

    def update_from_frame(self, d: dict) -> None:
        """HealerWorker.frame_ready payload 에서 필요한 필드만 꺼내 갱신."""
        if not d:
            return
        self.lbl_atk_map.setText(str(d.get("atk_map") or "-"))
        self.lbl_hlr_map.setText(str(d.get("healer_map") or "-"))
        self.lbl_atk_coord.setText(self._fmt_coord(d.get("atk_coord")))
        self.lbl_hlr_coord.setText(self._fmt_coord(d.get("healer_coord")))
        st = d.get("state")
        st_name = getattr(st, "name", None) or (str(st) if st else "")
        self.lbl_state.setText(fsm_kr(st_name) if st_name else "-")

    def set_attacker_snapshot(self, atk_map: str,
                              atk_coord: Optional[tuple]) -> None:
        """격수 모드에서는 HealerWorker frame이 없음.
        attacker stat(stat_ready) payload에서 들어온 정보로 업데이트.
        """
        if atk_map is not None:
            self.lbl_atk_map.setText(str(atk_map or "-"))
        self.lbl_atk_coord.setText(self._fmt_coord(atk_coord))

    def set_state_text(self, text: str) -> None:
        self.lbl_state.setText(text or "-")
