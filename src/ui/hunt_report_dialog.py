"""사냥 리포트 다이얼로그.

좌측: 날짜 리스트 (최근부터).
우측: 선택한 날짜의 세션 테이블 + 하단 막대그래프 (세션별 xp/h).
"""
from __future__ import annotations

import datetime as _dt
import os
import pathlib
import subprocess
from typing import List, Dict, Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from ..app.hunt_analytics import HuntReportStorage


def _fmt_dur(sec: int) -> str:
    try:
        s = max(0, int(sec))
    except Exception:
        s = 0
    if s < 60:
        return f"{s}초"
    m, r = divmod(s, 60)
    if m < 60:
        return f"{m}분{r:02d}초"
    h, m2 = divmod(m, 60)
    return f"{h}시간{m2:02d}분"


def _fmt_xp(n: int) -> str:
    try:
        n = int(n)
    except Exception:
        n = 0
    if n >= 100_000_000:
        return f"{n / 100_000_000.0:.2f}억"
    if n >= 10_000:
        return f"{n / 10_000.0:.1f}만"
    return str(n)


def _fmt_xph(n: int) -> str:
    try:
        n = int(n)
    except Exception:
        n = 0
    if n <= 0:
        return "-"
    return f"{n / 100_000_000.0:.2f}억/h"


class SessionBarChart(QtWidgets.QWidget):
    """세션별 xp/h 막대그래프. 간단한 QPainter 구현."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: List[tuple] = []  # (label, xph)
        self.setMinimumHeight(140)

    def set_data(self, sessions: List[Dict]) -> None:
        out = []
        for s in sessions:
            # xp/h 계산 (저장된 peak 대신 전체 평균).
            dur = max(1, int(s.get("duration_sec") or 0))
            gain = int(s.get("xp_gain") or 0)
            xph = int(gain / dur * 3600.0) if dur >= 10 else 0
            label = str(s.get("start_iso") or "")[-8:-3]  # HH:MM
            out.append((label, xph))
        self._data = out
        self.update()

    def paintEvent(self, _ev):
        qp = QtGui.QPainter(self)
        qp.setRenderHint(QtGui.QPainter.Antialiasing, True)
        r = self.rect()
        # 라이트 카드 배경 + 라운드.
        qp.setBrush(QtGui.QColor(255, 255, 255))
        qp.setPen(QtGui.QPen(QtGui.QColor(228, 231, 236), 1))
        qp.drawRoundedRect(r.adjusted(0, 0, -1, -1), 10, 10)
        pad_l = 54
        pad_b = 26
        pad_t = 18
        pad_r = 14
        w = max(1, r.width() - pad_l - pad_r)
        h = max(1, r.height() - pad_t - pad_b)
        # 축.
        qp.setPen(QtGui.QPen(QtGui.QColor(228, 231, 236), 1))
        qp.drawLine(pad_l, r.height() - pad_b, r.width() - pad_r,
                    r.height() - pad_b)
        qp.drawLine(pad_l, pad_t, pad_l, r.height() - pad_b)
        if not self._data:
            qp.setPen(QtGui.QColor(152, 162, 179))
            qp.drawText(r, QtCore.Qt.AlignCenter, "데이터 없음")
            return
        vals = [v for _, v in self._data]
        max_v = max(vals + [1])
        # Y축 라벨.
        qp.setPen(QtGui.QColor(71, 84, 103))
        qp.setFont(QtGui.QFont("Pretendard", 8))
        qp.drawText(
            4, pad_t + 10, f"{max_v / 100_000_000.0:.1f}억/h"
        )
        qp.drawText(
            4, pad_t + h + 4, "0"
        )
        # 막대.
        n = len(self._data)
        bar_w = max(4, (w - (n - 1) * 4) // n)
        qp.setFont(QtGui.QFont("Pretendard", 7))
        accent = QtGui.QColor(99, 102, 241)        # indigo 500
        zero_c = QtGui.QColor(228, 231, 236)
        for i, (label, v) in enumerate(self._data):
            x = pad_l + i * (bar_w + 4)
            bh = int(h * (v / max_v)) if max_v else 0
            by = r.height() - pad_b - bh
            color = accent if v > 0 else zero_c
            qp.setBrush(color)
            qp.setPen(QtCore.Qt.NoPen)
            qp.drawRoundedRect(x, by, bar_w, max(bh, 2), 3, 3)
            if n <= 20 or i % max(1, n // 10) == 0:
                qp.setPen(QtGui.QColor(152, 162, 179))
                qp.drawText(
                    x - 4, r.height() - pad_b + 14, bar_w + 8, 14,
                    QtCore.Qt.AlignCenter, label
                )


class HuntReportDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("사냥 리포트")
        self.resize(900, 600)
        self.storage = HuntReportStorage()

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # 좌측: 날짜 리스트.
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(4)
        left.addWidget(QtWidgets.QLabel("날짜"))
        self.date_list = QtWidgets.QListWidget()
        self.date_list.setFixedWidth(140)
        self.date_list.currentItemChanged.connect(self._on_date_changed)
        left.addWidget(self.date_list, 1)
        self.btn_open_dir = QtWidgets.QPushButton("폴더 열기")
        self.btn_open_dir.clicked.connect(self._open_dir)
        left.addWidget(self.btn_open_dir)
        self.btn_refresh = QtWidgets.QPushButton("새로고침")
        self.btn_refresh.clicked.connect(self.reload)
        left.addWidget(self.btn_refresh)

        # 우측: 요약 + 테이블 + 그래프.
        right = QtWidgets.QVBoxLayout()
        right.setSpacing(6)
        self.lbl_summary = QtWidgets.QLabel("-")
        self.lbl_summary.setObjectName("accentLabel")
        right.addWidget(self.lbl_summary)

        self.table = QtWidgets.QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "시작", "종료", "기간", "획득", "시간당",
            "바퀴", "맵",
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers
        )
        self.table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectRows
        )
        self.table.itemSelectionChanged.connect(self._on_session_selected)
        right.addWidget(self.table, 2)

        # 세션 상세 (바퀴 리스트).
        self.lbl_detail = QtWidgets.QLabel("세션을 선택하세요.")
        self.lbl_detail.setObjectName("mutedLabel")
        self.lbl_detail.setWordWrap(True)
        right.addWidget(self.lbl_detail)
        self.lap_table = QtWidgets.QTableWidget(0, 5)
        self.lap_table.setHorizontalHeaderLabels([
            "#", "시작", "기간", "획득", "맵",
        ])
        self.lap_table.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers
        )
        self.lap_table.horizontalHeader().setStretchLastSection(True)
        right.addWidget(self.lap_table, 1)

        self.chart = SessionBarChart()
        right.addWidget(self.chart, 0)

        root.addLayout(left, 0)
        root.addLayout(right, 1)

        self._sessions: List[Dict] = []
        self.reload()

    # -------- public --------
    def reload(self) -> None:
        self.date_list.clear()
        dates = self.storage.list_dates()
        if not dates:
            self.lbl_summary.setText("저장된 사냥 리포트가 없습니다.")
            self.chart.set_data([])
            self.table.setRowCount(0)
            self.lap_table.setRowCount(0)
            return
        for d in dates:
            self.date_list.addItem(d)
        self.date_list.setCurrentRow(0)

    # -------- slots --------
    def _on_date_changed(self, cur, _prev):
        if cur is None:
            return
        self._load_date(cur.text())

    def _load_date(self, date_str: str) -> None:
        sessions = self.storage.read_date(date_str)
        # 최신 → 오래된 순.
        sessions.sort(key=lambda s: s.get("start_ts") or 0, reverse=True)
        self._sessions = sessions
        self.table.setRowCount(len(sessions))
        total_dur = 0
        total_gain = 0
        total_laps = 0
        for i, s in enumerate(sessions):
            start = str(s.get("start_iso") or "")[-8:-3]
            end = str(s.get("end_iso") or "")[-8:-3]
            dur = int(s.get("duration_sec") or 0)
            gain = int(s.get("xp_gain") or 0)
            xph = int(gain / dur * 3600.0) if dur >= 10 else 0
            laps = s.get("laps") or []
            total_dur += dur
            total_gain += gain
            total_laps += len(laps)
            vals = [
                start, end, _fmt_dur(dur), _fmt_xp(gain),
                _fmt_xph(xph), str(len(laps)), str(s.get("map_top") or ""),
            ]
            for j, v in enumerate(vals):
                it = QtWidgets.QTableWidgetItem(v)
                self.table.setItem(i, j, it)
        self.table.resizeColumnsToContents()
        # 요약.
        if sessions:
            ses_xph = int(total_gain / total_dur * 3600.0) \
                if total_dur >= 10 else 0
            self.lbl_summary.setText(
                f"{date_str} — 세션 {len(sessions)}건 · "
                f"총 {_fmt_dur(total_dur)} · 획득 {_fmt_xp(total_gain)} · "
                f"바퀴 {total_laps}회 · 종합 {_fmt_xph(ses_xph)}"
            )
        else:
            self.lbl_summary.setText(f"{date_str} — 데이터 없음")
        # 그래프 (오름차순 시간).
        self.chart.set_data(list(reversed(sessions)))
        # 기본: 첫 세션 선택 해제.
        self.lap_table.setRowCount(0)
        self.lbl_detail.setText("세션을 선택하세요.")

    def _on_session_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        idx = rows[0].row()
        if idx < 0 or idx >= len(self._sessions):
            return
        s = self._sessions[idx]
        laps = s.get("laps") or []
        stats = s.get("map_stats") or []
        if laps:
            # 바퀴 기반 세부내역.
            self.lap_table.setHorizontalHeaderLabels([
                "#", "시작", "기간", "획득", "맵",
            ])
            self.lap_table.setRowCount(len(laps))
            for i, l in enumerate(laps):
                start = _dt.datetime.fromtimestamp(
                    float(l.get("start_ts") or 0)
                ).strftime("%H:%M:%S") if l.get("start_ts") else "-"
                dur = int(l.get("duration_sec") or 0)
                gain = int(l.get("xp_gain") or 0)
                lap_idx = int(l.get("lap_idx") or 0)
                self.lap_table.setItem(
                    i, 0, QtWidgets.QTableWidgetItem(str(lap_idx))
                )
                self.lap_table.setItem(i, 1, QtWidgets.QTableWidgetItem(start))
                self.lap_table.setItem(
                    i, 2, QtWidgets.QTableWidgetItem(_fmt_dur(dur))
                )
                self.lap_table.setItem(
                    i, 3, QtWidgets.QTableWidgetItem(_fmt_xp(gain))
                )
                self.lap_table.setItem(
                    i, 4, QtWidgets.QTableWidgetItem(str(l.get("map_top") or "-"))
                )
        elif stats:
            # 바퀴 이벤트 없음(맵 OCR이 `(1)` 탈락 등) → map_stats로 대체 표시.
            self.lap_table.setHorizontalHeaderLabels([
                "#", "방문", "체류", "획득", "맵",
            ])
            self.lap_table.setRowCount(len(stats))
            for i, st in enumerate(stats):
                try:
                    nm = str(st.get("name") or "")
                    cnt = int(st.get("count") or 0)
                    dur_m = int(st.get("duration_sec") or 0)
                    gain_m = int(st.get("xp_gain") or 0)
                except Exception:
                    nm, cnt, dur_m, gain_m = "", 0, 0, 0
                self.lap_table.setItem(
                    i, 0, QtWidgets.QTableWidgetItem(str(i + 1))
                )
                self.lap_table.setItem(
                    i, 1, QtWidgets.QTableWidgetItem(f"{cnt}회")
                )
                self.lap_table.setItem(
                    i, 2, QtWidgets.QTableWidgetItem(_fmt_dur(dur_m))
                )
                self.lap_table.setItem(
                    i, 3, QtWidgets.QTableWidgetItem(_fmt_xp(gain_m))
                )
                self.lap_table.setItem(
                    i, 4, QtWidgets.QTableWidgetItem(nm or "-")
                )
        else:
            self.lap_table.setHorizontalHeaderLabels([
                "#", "시작", "기간", "획득", "맵",
            ])
            self.lap_table.setRowCount(0)
        self.lap_table.resizeColumnsToContents()
        # 상세 요약 + 방문한 사냥터 목록 구성.
        dur_s = int(s.get("duration_sec") or 0)
        gain_s = int(s.get("xp_gain") or 0)
        xph_s = int(gain_s / dur_s * 3600.0) if dur_s >= 10 else 0
        head = (
            f"세션 {s.get('session_id', '')} — "
            f"{_fmt_dur(dur_s)} · 획득 {_fmt_xp(gain_s)} · {_fmt_xph(xph_s)}"
        )
        if laps:
            avg_d = sum(int(l.get("duration_sec") or 0) for l in laps) / len(laps)
            avg_g = sum(int(l.get("xp_gain") or 0) for l in laps) / len(laps)
            head += (
                f"  ·  바퀴 {len(laps)}회 · 평균 "
                f"{_fmt_dur(int(avg_d))} · {_fmt_xp(int(avg_g))}"
            )
        # 방문 맵 상세: map_stats(신) > map_counts(중간) > map_history > map_top fallback.
        # 신: "맵×n | 체류시간 | 획득xp" 줄 단위. 서브맵(선비족3-1/3-2/3-3) 구분 목적 —
        # 이동만 한 맵(gain=0) 도 표기 (어디 거쳤는지 추적용).
        stats = s.get("map_stats") or []
        counts = s.get("map_counts") or []
        history = s.get("map_history") or []
        lines: List[str] = []
        if stats:
            for st in stats:
                try:
                    nm = str(st.get("name") or "")
                    cnt = int(st.get("count") or 0)
                    dur_m = int(st.get("duration_sec") or 0)
                    gain_m = int(st.get("xp_gain") or 0)
                except Exception:
                    continue
                if not nm:
                    continue
                label = f"{nm}×{cnt}" if cnt > 1 else nm
                lines.append(f"{label} | {_fmt_dur(dur_m)} | {_fmt_xp(gain_m)}")
            if lines:
                head += "\n사냥터:\n  " + "\n  ".join(lines)
            else:
                head += "\n사냥터: -"
        elif counts:
            parts = [
                f"{name}×{cnt}" if cnt and int(cnt) > 1 else str(name)
                for name, cnt in counts
            ]
            head += "\n사냥터: " + " → ".join(parts) if parts else "\n사냥터: -"
        elif history:
            ordered: List[str] = []
            cmap: Dict[str, int] = {}
            for nm in history:
                if nm not in cmap:
                    ordered.append(nm)
                cmap[nm] = cmap.get(nm, 0) + 1
            parts = [
                f"{nm}×{cmap[nm]}" if cmap[nm] > 1 else nm for nm in ordered
            ]
            head += "\n사냥터: " + " → ".join(parts) if parts else "\n사냥터: -"
        elif s.get("map_top"):
            head += f"\n사냥터: {s.get('map_top')}"
        else:
            head += "\n사냥터: -"
        self.lbl_detail.setText(head)
        self.lbl_detail.setWordWrap(True)

    def _open_dir(self) -> None:
        try:
            path = str(self.storage.base_dir)
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass
