"""격수 전용 힐러 HP/MP 상태 오버레이.

격수 화면에 떠 있는 HUD. 수신된 힐러별 HP/MP 퍼센트를 색깔막대 + 숫자로 표시.

데이터 출처
  - 힐러 CooldownReport 의 hp_pct/mp_pct/hp_cur/mp_cur/hp_max/mp_max 필드.
  - main_window `_on_attacker_cooldown` → `update_healer(idx, d)`.

시각 규칙
  - HP 색: 빨강 고정.  MP 색: 파랑 고정.
  - 막대 fill + 퍼센트 숫자는 오버레이 투명도 슬라이더와 무관하게 항상 진함
    (배경/라벨/닉만 opacity_mul 따라감). "게이지가 핵심"이라는 사용자 지시.
  - 미관측(pct==-1): 회색 placeholder + "--".
  - _last_recv 가 10초 이상 오래되면 행 전체 dim (alpha 절반).

기존 오버레이(`GameOverlay`/`SkillAlertOverlay`/`HunterHelperOverlay`)와 겹치지
않도록 기본 위치는 game_rect 좌하단. 사용자 "위치 편집" 토글로 드래그 조정.
"""
from __future__ import annotations

import time
from typing import Dict, Optional, Tuple

from PyQt5 import QtCore, QtGui, QtWidgets

from .overlay import _ScaledOverlay


_FRESH_WINDOW_SEC = 10.0
_BASE_W = 170
_NICK_H = 24            # 닉네임 라인 높이 (배경 띠 포함).
_NICK_BAR_GAP = 4       # 닉 띠와 첫 바 사이 간격.
_BAR_H = 18             # HP/MP 막대 라인 높이.
_BAR_GAP = 2            # HP-MP 막대 사이 간격.
_BLOCK_GAP = 14         # 힐러(블록) 사이 간격 — 닉 배경 띠와 함께 시각 구분.
_TOP_PAD = 8
_BOT_PAD = 8
# 한 블록(한 힐러) 세로 높이: 닉 + (닉-바 간격) + HP 막대 + 간격 + MP 막대.
_BLOCK_H = _NICK_H + _NICK_BAR_GAP + _BAR_H + _BAR_GAP + _BAR_H


class HealerStatusOverlay(_ScaledOverlay):
    """힐러 HP/MP 상태 HUD.

    API
      update_healer(idx, d): d 는 main_window `_healer_cooldowns[idx]` dict.
        필요한 키: nickname, hp_pct, mp_pct, hp_cur, mp_cur, hp_max, mp_max,
        _last_recv.
      clear(): 모든 행 제거.
    """

    def __init__(self):
        super().__init__()
        self._rows: Dict[int, dict] = {}
        self._base_w = _BASE_W
        # 초기 크기는 1블록 기준. 수신 들어올 때마다 _relayout 로 재조정.
        self.setFixedSize(self._base_w, _TOP_PAD + _BLOCK_H + _BOT_PAD)
        self._tick_timer = QtCore.QTimer(self)
        self._tick_timer.setInterval(500)
        self._tick_timer.timeout.connect(self._on_tick)
        self._tick_timer.start()

    # ---- 외부 API ----
    def update_healer(self, idx: int, d: dict) -> None:
        try:
            i = int(idx)
        except Exception:
            return
        # 필요한 키만 얕게 카피해 보관 (다른 곳에서 dict 변경 영향 차단).
        self._rows[i] = {
            "nickname": str(d.get("nickname", "") or "").strip(),
            "hp_pct": int(d.get("hp_pct", -1)),
            "mp_pct": int(d.get("mp_pct", -1)),
            "hp_cur": int(d.get("hp_cur", -1)),
            "mp_cur": int(d.get("mp_cur", -1)),
            "hp_max": int(d.get("hp_max", 0)),
            "mp_max": int(d.get("mp_max", 0)),
            "_last_recv": float(d.get("_last_recv", 0.0) or 0.0),
        }
        self._relayout()
        self.update()

    def clear(self) -> None:
        self._rows.clear()
        self._relayout()
        self.update()

    # ---- 내부 ----
    def _on_tick(self) -> None:
        # fresh 상태가 시간 경과만으로 바뀔 수 있어 주기 repaint.
        self.update()

    def _on_scale_changed(self) -> None:
        self._relayout()
        self._reanchor()

    def _relayout(self) -> None:
        n = max(1, len(self._rows))
        block_h = self._px(_BLOCK_H)
        gap = self._px(_BLOCK_GAP)
        top = self._px(_TOP_PAD)
        bot = self._px(_BOT_PAD)
        w = self._px(self._base_w)
        h = top + block_h * n + gap * max(0, n - 1) + bot
        self.setFixedSize(w, h)

    def _reanchor(self) -> None:
        """수동 위치 최우선. 없으면 game_rect 좌하단 기본."""
        if self._manual_pos is not None:
            cx, cy = self._clamp_to_bound(*self._manual_pos)
            self.move(cx, cy)
            return
        if self._game_rect is not None:
            gx, gy, gw, gh = self._game_rect
            # 좌하단에서 20/20 오프셋. 기존 오버레이(좌상=cd, 중상=alert,
            # 우측=helper) 와 겹치지 않는 기본 영역.
            px = gx + self._px(20)
            py = gy + gh - self.height() - self._px(20)
            cx, cy = self._clamp_to_bound(px, py)
            self.move(cx, cy)

    # ---- paint ----
    def paintEvent(self, ev) -> None:  # type: ignore[override]
        qp = QtGui.QPainter(self)
        qp.setRenderHint(QtGui.QPainter.Antialiasing, True)
        # 배경.
        bg = QtGui.QColor(18, 20, 26, self._a(220))
        qp.setBrush(bg)
        qp.setPen(QtCore.Qt.NoPen)
        qp.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1),
                           self._px(8), self._px(8))

        block_h = self._px(_BLOCK_H)
        gap = self._px(_BLOCK_GAP)
        top = self._px(_TOP_PAD)
        w = self.width()
        # 수신된 idx 오름차순 (peers 순서 = 힐러1, 힐러2 ...).
        idx_list = sorted(self._rows.keys())
        if not idx_list:
            qp.setPen(QtGui.QColor(150, 150, 160, self._a(200)))
            qp.setFont(self._font(10, bold=False))
            qp.drawText(self.rect(), QtCore.Qt.AlignCenter,
                        "힐러 수신 대기중")
            self._draw_edit_hint(qp)
            qp.end()
            return
        now = time.time()
        for row_i, idx in enumerate(idx_list):
            d = self._rows[idx]
            y0 = top + (block_h + gap) * row_i
            self._paint_block(qp, idx, d, 0, y0, w, block_h, now)
        self._draw_edit_hint(qp)
        qp.end()

    def _paint_block(self, qp: QtGui.QPainter, idx: int, d: dict,
                     x: int, y: int, w: int, block_h: int,
                     now: float) -> None:
        """한 힐러의 세로 3줄 블록 그리기: 닉 / HP 바 / MP 바."""
        # fresh 여부. _last_recv 이 오래되면 블록 전체 alpha 절반.
        last = float(d.get("_last_recv", 0.0) or 0.0)
        stale = (last <= 0.0) or ((now - last) > _FRESH_WINDOW_SEC)
        dim = 0.5 if stale else 1.0

        pad = self._px(10)
        nick_h = self._px(_NICK_H)
        nick_gap = self._px(_NICK_BAR_GAP)
        bar_h = self._px(_BAR_H)
        bar_gap = self._px(_BAR_GAP)

        # 1) 닉네임 라인 — 배경 띠 + 좌측 색 인디케이터 + 닉 글자.
        nick = str(d.get("nickname", "") or "").strip()
        if not nick:
            nick = f"힐러{idx + 1}"
        nick_rect = QtCore.QRect(x + pad, y, w - pad * 2, nick_h)
        # 배경 띠 (opacity_mul 무시 — 힐러 구분 가시성 최우선).
        band_col = QtGui.QColor(44, 52, 70, int(235 * dim))
        qp.setBrush(band_col)
        qp.setPen(QtCore.Qt.NoPen)
        qp.drawRoundedRect(nick_rect, self._px(5), self._px(5))
        # 좌측 색 인디케이터 (힐러별 다른 색).
        ind_w = self._px(4)
        ind_h = nick_h - self._px(6)
        ind_x = x + pad + self._px(4)
        ind_y = y + (nick_h - ind_h) // 2
        ind_col = self._indicator_color(idx, dim)
        qp.setBrush(ind_col)
        qp.drawRoundedRect(QtCore.QRect(ind_x, ind_y, ind_w, ind_h),
                           self._px(2), self._px(2))
        # 닉 글자.
        qp.setFont(self._font(12, bold=True))
        nick_col = QtGui.QColor(250, 252, 255, int(252 * dim))
        qp.setPen(nick_col)
        text_rect = QtCore.QRect(ind_x + ind_w + self._px(8), y,
                                 w - pad * 2 - (ind_w + self._px(16)), nick_h)
        qp.drawText(text_rect,
                    QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                    self._elide(nick, text_rect.width(), qp.fontMetrics()))

        # 2) HP 바.
        hp_y = y + nick_h + nick_gap
        self._paint_meter(qp,
                          x + pad, hp_y, w - pad * 2, bar_h,
                          pct=int(d.get("hp_pct", -1)),
                          cur=int(d.get("hp_cur", -1)),
                          mx=int(d.get("hp_max", 0)),
                          label="HP", kind="hp", dim=dim)

        # 3) MP 바.
        mp_y = hp_y + bar_h + bar_gap
        self._paint_meter(qp,
                          x + pad, mp_y, w - pad * 2, bar_h,
                          pct=int(d.get("mp_pct", -1)),
                          cur=int(d.get("mp_cur", -1)),
                          mx=int(d.get("mp_max", 0)),
                          label="MP", kind="mp", dim=dim)

    @staticmethod
    def _indicator_color(idx: int, dim: float) -> QtGui.QColor:
        """힐러별 고유 색 인디케이터. 5색 순환."""
        palette = [
            (102, 187, 255),  # 청록
            (255, 176, 96),   # 주황
            (162, 132, 255),  # 보라
            (96, 220, 170),   # 민트
            (255, 132, 176),  # 핑크
        ]
        r, g, b = palette[idx % len(palette)]
        c = QtGui.QColor(r, g, b)
        c.setAlpha(int(250 * dim))
        return c

    def _paint_meter(self, qp: QtGui.QPainter,
                     x: int, y: int, w: int, h: int,
                     pct: int, cur: int, mx: int,
                     label: str, kind: str, dim: float) -> None:
        """한 줄짜리 가로 막대. 좌측 라벨 → 막대(테두리 + fill + 중앙 텍스트)."""
        # 좌측 라벨 (HP/MP).
        lbl_w = self._px(22)
        qp.setFont(self._font(9, bold=True))
        lbl_col = QtGui.QColor(190, 194, 204, self._a(int(235 * dim)))
        qp.setPen(lbl_col)
        qp.drawText(QtCore.QRect(x, y, lbl_w, h),
                    QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, label)

        # 막대 영역 (라벨 오른쪽 전부).
        gap = self._px(4)
        bar_x = x + lbl_w + gap
        bar_w = max(self._px(40), w - lbl_w - gap)
        bar_h = h
        bar_y = y
        radius = self._px(3)
        rect = QtCore.QRect(bar_x, bar_y, bar_w, bar_h)

        # 트랙 (배경).
        track = QtGui.QColor(26, 28, 34, self._a(int(230 * dim)))
        qp.setBrush(track)
        qp.setPen(QtCore.Qt.NoPen)
        qp.drawRoundedRect(rect, radius, radius)

        # fill (opacity_mul 무시 — 항상 진함, dim 만 반영).
        if pct >= 0:
            p = max(0, min(100, int(pct)))
            fw = int(bar_w * p / 100.0)
            if fw > 0:
                col = self._meter_color(kind, p, dim)
                qp.setBrush(col)
                qp.setPen(QtCore.Qt.NoPen)
                qp.drawRoundedRect(QtCore.QRect(bar_x, bar_y, fw, bar_h),
                                   radius, radius)

        # 테두리 (가시성 향상용). opacity_mul 무시.
        border_col = QtGui.QColor(220, 224, 232, int(230 * dim))
        pen_w = max(1, self._px(1))
        pen = QtGui.QPen(border_col, pen_w)
        qp.setBrush(QtCore.Qt.NoBrush)
        qp.setPen(pen)
        qp.drawRoundedRect(rect, radius, radius)

        # 막대 내부 중앙 텍스트: "87% 1200/1380" 형태.
        if pct < 0:
            txt = "--"
        else:
            p = max(0, min(100, int(pct)))
            if int(mx) > 0 and int(cur) >= 0:
                txt = f"{p}%  {cur}/{mx}"
            elif int(cur) >= 0:
                txt = f"{p}%  {cur}"
            else:
                txt = f"{p}%"
        qp.setFont(self._font(9, bold=True))
        txt_col = QtGui.QColor(252, 252, 255, int(252 * dim))
        qp.setPen(txt_col)
        qp.drawText(rect, QtCore.Qt.AlignCenter, txt)

    @staticmethod
    def _elide(text: str, width: int, fm: QtGui.QFontMetrics) -> str:
        try:
            return fm.elidedText(text, QtCore.Qt.ElideRight, int(width))
        except Exception:
            return text

    def _meter_color(self, kind: str, pct: int, dim: float) -> QtGui.QColor:
        # HP 빨강 / MP 파랑 고정. 막대 fill 은 opacity_mul 무시 — 항상 진함.
        if kind == "mp":
            c = QtGui.QColor(58, 132, 230)
        else:
            c = QtGui.QColor(224, 72, 72)
        c.setAlpha(int(245 * dim))
        return c
