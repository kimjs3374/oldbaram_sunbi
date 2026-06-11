"""격수 PC 인게임 오버레이.

구성:
  GameOverlay        : 힐러별 쿨 진행 상황 + 시간당 경험치 표시 (왼쪽).
                       남은 쿨이 있는 스킬만 줄로 추가.
  SkillAlertOverlay  : 스킬 시전 3초전 1회 알림 전용. 3초간 표시 후 자동 소멸.
                       힐러별 별도 창 X. 한 창 안에서 줄 단위로 쌓임.
                       맵 영역 아래 + 게임 영역 수평 중심 배치.

공통: 해상도(게임 영역 크기)에 비례해 폰트·패딩·라인 높이 스케일.
모두 프레임리스 + 반투명 + 항상위 + 마우스 입력 통과.
"""
from __future__ import annotations

import ctypes
import time
from typing import Optional, Tuple

from PyQt5 import QtCore, QtGui, QtWidgets


_BASELINE_H = 720.0  # 스케일 기준 해상도 높이.


def _fmt_cd(sec: int) -> str:
    if sec is None or sec < 0:
        return "-"
    if sec <= 0:
        return "ready"
    if sec >= 60:
        m = sec // 60
        s = sec % 60
        return f"{m}m{s:02d}s"
    return f"{sec}s"


def _fmt_cd_kr(sec: int) -> str:
    """쿨 표기 (2026-06-12 사용자 요청): 준비=한글 '준비됨', 쿨중=잔여초.

    예) '준비됨' / '30s' / '2m14s'. 음수(미측정/미해당)는 호출측에서 숨김.
    """
    if sec is None or sec <= 0:
        return "준비됨"
    if sec >= 60:
        m = sec // 60
        s = sec % 60
        return f"{m}m{s:02d}s"
    return f"{sec}s"


def _fmt_xph(n: int) -> str:
    if not n or n <= 0:
        return "-"
    return f"{n / 100_000_000.0:.1f}억/h"


def _fmt_xph_compact(n: int) -> str:
    if not n or n <= 0:
        return "-"
    return f"{n / 100_000_000.0:.2f}억"


def _fmt_xp(n: int) -> str:
    if n is None or n <= 0:
        return "0"
    if n >= 100_000_000:
        return f"{n / 100_000_000.0:.2f}억"
    if n >= 10_000:
        return f"{n / 10_000.0:.1f}만"
    return str(int(n))


def _fmt_dur(sec: int) -> str:
    try:
        s = max(0, int(sec))
    except Exception:
        s = 0
    if s < 60:
        return f"{s}s"
    m, r = divmod(s, 60)
    if m < 60:
        return f"{m}m{r:02d}s"
    h, m2 = divmod(m, 60)
    return f"{h}h{m2:02d}m"


class _ScaledOverlay(QtWidgets.QWidget):
    """해상도 스케일 + 수동 드래그 위치 편집 공통 부모.

    편집 모드 OFF(기본) — 입력 통과(WindowTransparentForInput), 드래그 불가.
    편집 모드 ON              — 입력 받음, 좌클릭 드래그로 위치 이동,
                               release 시 `position_changed(x, y)` 방출 +
                               노란 테두리로 편집 상태 시각화.

    스케일 소스: `set_anchor_regions(game, map)` 호출 시 game.h/720.
    game_rect 없으면 scale=1.0. 수동 위치(`set_manual_pos`)가 있으면
    자동 앵커보다 우선.
    """

    position_changed = QtCore.pyqtSignal(int, int)

    def __init__(self):
        super().__init__()
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self._scale: float = 1.0
        self._game_rect: Optional[Tuple[int, int, int, int]] = None
        self._map_rect: Optional[Tuple[int, int, int, int]] = None
        self._target_hwnd: Optional[int] = None
        self._manual_pos: Optional[Tuple[int, int]] = None
        self._edit_mode: bool = False
        self._drag_offset: Optional[QtCore.QPoint] = None
        # paint-기반 투명도 (setWindowOpacity 대신).
        # Qt + WA_TranslucentBackground + setWindowOpacity 조합은 Windows
        # 계층창(Layered Window) 모드 충돌로 창 전체가 사라지는 사례가
        # 보고돼 있어 안전하게 QPainter alpha 스케일링으로 구현.
        self._opacity_mul: float = 1.0
        # 초기 플래그 = 입력 통과.
        self._apply_flags()

    def set_opacity(self, v: float) -> None:
        """0.0 ~ 1.0 범위 투명도 멀티플라이어 적용. 실제로는 paintEvent 내부
        QColor alpha 값에 이 값을 곱해 렌더링. setWindowOpacity 를 쓰지 않아
        WA_TranslucentBackground 와 충돌해서 창이 사라지는 현상이 없다.
        0.0 = 완전 투명(보이지 않음).
        """
        try:
            f = float(v)
        except Exception:
            f = 1.0
        if f < 0.0:
            f = 0.0
        elif f > 1.0:
            f = 1.0
        # 안전장치: setWindowOpacity 는 반드시 1.0 고정 (과거 저장값 잔재 제거).
        try:
            self.setWindowOpacity(1.0)
        except Exception:
            pass
        self._opacity_mul = f
        self.update()

    def _a(self, alpha: int) -> int:
        """기본 alpha 값에 opacity_mul 을 곱해 0~255 clamp."""
        try:
            return max(0, min(255, int(alpha * self._opacity_mul)))
        except Exception:
            return int(alpha)

    def _apply_flags(self) -> None:
        # 새 정책: StaysOnTop 을 항상 켠다. 단 "다른 앱 위에 안 뜬다" 와
        # "msw 최소화 시 같이 숨는다" 는 main_window 측 visibility 폴링으로
        # show/hide 를 제어해 구현한다. z-order 싸움(owner 대 foreground)에서
        # overlay 가 본창 뒤로 깔리던 문제는 이 조합으로만 안정적으로 해결됨.
        flags = (QtCore.Qt.FramelessWindowHint
                 | QtCore.Qt.Tool
                 | QtCore.Qt.WindowStaysOnTopHint)
        if not self._edit_mode:
            flags |= QtCore.Qt.WindowTransparentForInput
        vis = self.isVisible()
        pos = self.pos()
        self.setWindowFlags(flags)
        if vis:
            self.show()
            self.move(pos)
            self._enforce_owner_link()

    def set_edit_mode(self, on: bool) -> None:
        if bool(on) == self._edit_mode:
            return
        self._edit_mode = bool(on)
        self._apply_flags()
        # 서브클래스 레이아웃/앵커 재계산 (placeholder 크기 등).
        self._on_scale_changed()
        self.update()

    def set_manual_pos(self, x: Optional[int], y: Optional[int]) -> None:
        """수동 위치 저장. None/None 이면 해제. bound 있으면 clamp."""
        if x is None or y is None:
            self._manual_pos = None
        else:
            cx, cy = self._clamp_to_bound(int(x), int(y))
            self._manual_pos = (cx, cy)
        self._on_scale_changed()

    def set_anchor_regions(self,
                           game_rect: Optional[Tuple[int, int, int, int]],
                           map_rect: Optional[Tuple[int, int, int, int]]
                           ) -> None:
        self._game_rect = tuple(game_rect) if game_rect else None
        self._map_rect = tuple(map_rect) if map_rect else None
        if self._game_rect:
            self._scale = max(0.5, min(3.0,
                                       float(self._game_rect[3]) / _BASELINE_H))
        else:
            self._scale = 1.0
        self._on_scale_changed()

    def attach_to_hwnd(self, hwnd) -> None:
        """msw 창 HWND에 바인딩. 이 창 client rect 밖으로 드래그 못 나감.

        또한 GWLP_HWNDPARENT로 owner 관계를 맺어 msw 최소화/비포커스 시
        오버레이도 함께 숨거나 뒤로 보내짐 (다른 stay-on-top 창 위에 튀어나가지 않음).
        """
        try:
            self._target_hwnd = int(hwnd) if hwnd else None
        except Exception:
            self._target_hwnd = None
        # flags에 StaysOnTop 제거 의존성이 있으므로 먼저 재적용.
        self._apply_flags()
        self._enforce_owner_link()
        # 창이 이동/리사이즈되면 오버레이도 안쪽으로 재clamp (500ms 주기).
        if self._target_hwnd:
            if not hasattr(self, "_bound_timer") or self._bound_timer is None:
                self._bound_timer = QtCore.QTimer(self)
                self._bound_timer.setInterval(500)
                self._bound_timer.timeout.connect(self._enforce_bounds)
                self._bound_timer.start()
        self._on_scale_changed()

    def showEvent(self, ev) -> None:  # type: ignore[override]
        super().showEvent(ev)
        # show 시점마다 Qt가 HWND 재생성할 수 있어 owner 재설정.
        try:
            self._enforce_owner_link()
        except Exception:
            pass

    def _enforce_owner_link(self) -> None:
        """내 HWND의 GWLP_HWNDPARENT를 _target_hwnd로 설정.

        Patch 2.24 (2026-04-20): owner link 비활성화 — 실증 완료.
        도사 모드에서 msw HWND 와 Python 오버레이 창을 GWLP_HWNDPARENT 로
        묶으면 msw 내에서 Shift+X 조합 입력이 씹히는 증상 발생. 격수 모드에선
        동일 경로라도 증상 없음 (추정: 오버레이 show 타이밍/HWND 재생성 순서
        차이로 실제 링크가 유효하게 걸리는 건 도사 기동 플로우만). 링크 스킵
        후 Shift+X 정상 복귀 확인. z-order 는 StaysOnTop + visibility 폴링이
        커버.
        """
        return
        if not self._target_hwnd:
            return
        try:
            from ..capture.screen import user32 as _u32
            my_hwnd = int(self.winId())
            if not my_hwnd:
                return
            GWLP_HWNDPARENT = -8
            # 64-bit 안전 — Ptr 버전 우선.
            if hasattr(_u32, "SetWindowLongPtrW"):
                _u32.SetWindowLongPtrW.restype = ctypes.c_void_p
                _u32.SetWindowLongPtrW.argtypes = [
                    ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p
                ]
                _u32.SetWindowLongPtrW(
                    ctypes.c_void_p(my_hwnd),
                    GWLP_HWNDPARENT,
                    ctypes.c_void_p(int(self._target_hwnd)),
                )
            else:
                _u32.SetWindowLongW(
                    my_hwnd, GWLP_HWNDPARENT, int(self._target_hwnd)
                )
        except Exception:
            pass

    def _get_bound_rect(self) -> Optional[Tuple[int, int, int, int]]:
        """바인딩된 창의 client rect를 스크린 절대좌표로 반환."""
        hwnd = self._target_hwnd
        if not hwnd:
            return None
        try:
            from ..capture.screen import user32 as _u32
            from ctypes import wintypes, byref
            if not _u32.IsWindow(int(hwnd)):
                return None
            pt = wintypes.POINT(0, 0)
            _u32.ClientToScreen(int(hwnd), byref(pt))
            rc = wintypes.RECT()
            _u32.GetClientRect(int(hwnd), byref(rc))
            w = int(rc.right - rc.left)
            h = int(rc.bottom - rc.top)
            if w <= 0 or h <= 0:
                return None
            return (int(pt.x), int(pt.y), w, h)
        except Exception:
            return None

    def _clamp_to_bound(self, x: int, y: int) -> Tuple[int, int]:
        """(x,y)를 바인딩 창 client rect 안으로 clamp. 바인딩 없으면 그대로."""
        b = self._get_bound_rect()
        if not b:
            return (int(x), int(y))
        bx, by, bw, bh = b
        # 오버레이가 창 보다 크면 좌상단 고정.
        max_x = bx + max(0, bw - self.width())
        max_y = by + max(0, bh - self.height())
        cx = max(bx, min(int(x), max_x))
        cy = max(by, min(int(y), max_y))
        return (cx, cy)

    def _enforce_bounds(self) -> None:
        """주기 호출: 창이 움직여 오버레이가 밖에 나가 있으면 안쪽으로 끌어들임."""
        if not self._target_hwnd:
            return
        p = self.pos()
        cx, cy = self._clamp_to_bound(p.x(), p.y())
        if (cx, cy) != (p.x(), p.y()):
            self.move(cx, cy)
            # 수동 위치 저장값도 동기화 (다음 복원 시 안전).
            if self._manual_pos is not None:
                self._manual_pos = (cx, cy)

    def _on_scale_changed(self) -> None:
        """서브클래스 오버라이드."""
        pass

    def _px(self, base: int) -> int:
        return max(1, int(round(base * self._scale)))

    def _font(self, pt_base: int, bold: bool = True) -> QtGui.QFont:
        f = QtGui.QFont("Malgun Gothic", max(7, int(round(pt_base * self._scale))))
        f.setBold(bold)
        return f

    def _draw_edit_hint(self, qp: QtGui.QPainter) -> None:
        """편집 모드 시 노란 점선 테두리 + 좌상단 작은 힌트."""
        if not self._edit_mode:
            return
        pen = QtGui.QPen(QtGui.QColor(255, 220, 60), 2, QtCore.Qt.DashLine)
        qp.setPen(pen)
        qp.setBrush(QtCore.Qt.NoBrush)
        qp.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), 6, 6)
        qp.setFont(self._font(8, bold=True))
        qp.setPen(QtGui.QColor(255, 220, 60))
        qp.drawText(self._px(6), self._px(12), "드래그로 이동")

    def mousePressEvent(self, ev):
        if self._edit_mode and ev.button() == QtCore.Qt.LeftButton:
            self._drag_offset = ev.globalPos() - self.pos()
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if (self._edit_mode
                and (ev.buttons() & QtCore.Qt.LeftButton)
                and self._drag_offset is not None):
            g = ev.globalPos() - self._drag_offset
            cx, cy = self._clamp_to_bound(g.x(), g.y())
            self.move(cx, cy)
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if (self._edit_mode and ev.button() == QtCore.Qt.LeftButton
                and self._drag_offset is not None):
            self._drag_offset = None
            p = self.pos()
            cx, cy = self._clamp_to_bound(p.x(), p.y())
            if (cx, cy) != (p.x(), p.y()):
                self.move(cx, cy)
            self._manual_pos = (cx, cy)
            try:
                self.position_changed.emit(cx, cy)
            except Exception:
                pass
            ev.accept()
            return
        super().mouseReleaseEvent(ev)


class GameOverlay(_ScaledOverlay):
    """힐러/쩔캐별 스킬 쿨 상태 + 시간당 경험치 (왼쪽 고정).

    2026-06-12: 사냥 분석/맵 히스토리 섹션은 HuntOverlay 로 분리.
    쿨 표기: 준비됨(쿨0) / 잔여초 — 측정된 스킬은 항상 줄 표시.
    """

    def __init__(self):
        super().__init__()
        self._rows: dict[int, dict] = {}
        self._base_w = 240
        self.setFixedSize(self._base_w, 80)
        self._tick_timer = QtCore.QTimer(self)
        self._tick_timer.setInterval(500)
        self._tick_timer.timeout.connect(self._on_tick)
        self._tick_timer.start()

    def _on_tick(self) -> None:
        # 로컬 감산으로 줄 수가 바뀔 수 있으니 레이아웃도 주기 재계산.
        self._relayout()
        self.update()

    def _on_scale_changed(self) -> None:
        self._relayout()
        self._reanchor()

    def _reanchor(self) -> None:
        """우선순위: 수동 위치 > game_rect 좌상단 > 유지. 모두 bound로 clamp."""
        if self._manual_pos is not None:
            mx, my = self._manual_pos
            cx, cy = self._clamp_to_bound(mx, my)
            self.move(cx, cy)
            return
        if self._game_rect:
            gx, gy, _, _ = self._game_rect
            cx, cy = self._clamp_to_bound(int(gx), int(gy))
            self.move(cx, cy)

    def clear_rows(self) -> None:
        self._rows.clear()
        self.update()

    def update_healer(self, idx: int, d: dict) -> None:
        key = int(idx)
        cur = self._rows.get(key, {})
        new_nick = str(d.get("nickname", "") or "").strip()
        prev_nick = str(cur.get("nick", "") or "").strip()
        if not new_nick:
            new_nick = prev_nick
        # p/b에 음수(정보 없음)가 들어오면 이전 유효값을 그대로 유지 —
        # 워커가 잠시 OCR 실패한 프레임에서 카운트다운이 끊기지 않게.
        new_p = int(d.get("cd_parlyuk", -1))
        new_b = int(d.get("cd_baekho", -1))
        now = time.monotonic()
        p_ts = float(cur.get("p_ts", 0.0) or 0.0)
        b_ts = float(cur.get("b_ts", 0.0) or 0.0)
        if new_p < 0:
            new_p = int(cur.get("p", -1))
        else:
            p_ts = now
        if new_b < 0:
            new_b = int(cur.get("b", -1))
        else:
            b_ts = now
        # 지폭지술 (쩔캐 현인, 2026-06-12). -1=미해당 → stick 없이 그대로
        # 반영해 현인 OFF 시 줄이 즉시 사라지게 (타이머 기반이라 OCR 실패
        # 프레임 개념 없음).
        new_j = int(d.get("cd_jipok", -1))
        j_ts = now if new_j >= 0 else 0.0
        self._rows[key] = {
            "nick": new_nick,
            "p": new_p,
            "b": new_b,
            "j": new_j,
            "p_ts": p_ts,
            "b_ts": b_ts,
            "j_ts": j_ts,
            "armed": bool(d.get("armed", False)),
            "has_armed": "armed" in d,
            "xph": int(d.get("xp_per_hour", 0) or 0),
        }
        self._relayout()
        self.update()

    @staticmethod
    def _eff_cd(cd: int, ts: float) -> int:
        """로컬 시계 기반 잔여 쿨 감산. ts=0이면 감산 안 함(값 없음).

        워커를 정지해도 오버레이 자체 타이머로 카운트가 흘러가도록 하는 핵심.
        """
        if cd is None or cd < 0:
            return -1
        if ts <= 0:
            return int(cd)
        elapsed = int(max(0.0, time.monotonic() - ts))
        return max(0, int(cd) - elapsed)

    def _visible_lines(self) -> list[tuple[int, str, str, int]]:
        """측정된(>=0) 스킬은 쿨 0이어도 항상 표시 — '준비됨' (2026-06-12).
        -1(미측정/미해당)만 숨김. 지폭지술은 쩔캐(현인) 행에서만 >=0."""
        lines: list[tuple[int, str, str, int]] = []
        for idx in sorted(self._rows.keys()):
            r = self._rows[idx]
            nick = r["nick"] or f"힐러{idx + 1}"
            p = self._eff_cd(r.get("p", -1), r.get("p_ts", 0.0))
            b = self._eff_cd(r.get("b", -1), r.get("b_ts", 0.0))
            j = self._eff_cd(r.get("j", -1), r.get("j_ts", 0.0))
            xph = r["xph"]
            if p >= 0:
                lines.append((idx, nick, f"파력무참 : {_fmt_cd_kr(p)}", p))
            if b >= 0:
                lines.append((idx, nick, f"백호의희원 : {_fmt_cd_kr(b)}", b))
            if j >= 0:
                lines.append((idx, nick, f"지폭지술 : {_fmt_cd_kr(j)}", j))
            if xph > 0:
                lines.append((idx, nick, _fmt_xph(xph), -1))
        return lines

    def _relayout(self) -> None:
        w = self._px(self._base_w)
        lines = self._visible_lines()
        n = len(lines)
        row_h = self._px(22)
        head_h = self._px(30)
        pad = self._px(8)
        h = head_h + (row_h * max(1, n)) + pad
        self.setFixedSize(w, h)

    def paintEvent(self, _ev):
        qp = QtGui.QPainter(self)
        qp.setRenderHint(QtGui.QPainter.Antialiasing, True)
        # ===== 배경·테두리: 투명도 적용 =====
        qp.setPen(QtCore.Qt.NoPen)
        qp.setBrush(QtGui.QColor(18, 20, 26, self._a(200)))
        radius = self._px(8)
        qp.drawRoundedRect(self.rect(), radius, radius)
        qp.setPen(QtGui.QColor(90, 110, 160, self._a(255)))
        qp.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), radius, radius)
        # ===== 이하 글씨: 투명도 영향 없이 원본 alpha 로 렌더 =====
        left_pad = self._px(12)
        nick_col = self._px(90)
        qp.setFont(self._font(10))
        qp.setPen(QtGui.QColor(180, 210, 255))
        qp.drawText(left_pad, self._px(20), "힐러 상태")
        lines = self._visible_lines()
        y = self._px(38)
        row_h = self._px(22)
        if not lines:
            qp.setFont(self._font(9, bold=False))
            qp.setPen(QtGui.QColor(140, 140, 150))
            qp.drawText(left_pad, y, "힐러 수신 대기")
            y += row_h
        else:
            qp.setFont(self._font(11))
            last_idx = -999
            for idx, nick, text, cd in lines:
                if idx != last_idx:
                    qp.setPen(QtGui.QColor(120, 200, 255))
                    qp.drawText(left_pad, y, f"{nick}")
                    last_idx = idx
                # cd<=0: 준비됨/xph(녹색). 쿨 임박~진행은 잔여초별 색.
                if cd <= 0:
                    color = QtGui.QColor(160, 230, 160)
                elif cd <= 5:
                    color = QtGui.QColor(240, 80, 80)
                elif cd <= 15:
                    color = QtGui.QColor(240, 170, 60)
                else:
                    color = QtGui.QColor(210, 210, 220)
                qp.setPen(color)
                qp.drawText(nick_col, y, text)
                y += row_h
        self._draw_edit_hint(qp)


class HuntOverlay(_ScaledOverlay):
    """사냥 분석 + 맵 히스토리 전용 오버레이 (2026-06-12 GameOverlay에서 분리).

    격수 모드 전용. MainWindow._tick_analytics 가 update_analytics(snap) 주입.
    위치는 'hunt' 키로 별도 저장/드래그 (위치 편집 모드 공통).
    """

    def __init__(self):
        super().__init__()
        self._analytics: Optional[dict] = None
        self._base_w = 240
        self.setFixedSize(self._base_w, 60)

    def update_analytics(self, snap: Optional[dict]) -> None:
        self._analytics = snap or None
        self._relayout()
        self.update()

    def _on_scale_changed(self) -> None:
        self._relayout()
        self._reanchor()

    def _reanchor(self) -> None:
        """우선순위: 수동 위치 > game_rect 좌상단+아래 오프셋 > 유지."""
        if self._manual_pos is not None:
            mx, my = self._manual_pos
            cx, cy = self._clamp_to_bound(mx, my)
            self.move(cx, cy)
            return
        if self._game_rect:
            gx, gy, _, _ = self._game_rect
            # GameOverlay(쿨 상태)가 좌상단을 쓰므로 그 아래 기본 배치.
            cx, cy = self._clamp_to_bound(int(gx), int(gy) + self._px(180))
            self.move(cx, cy)

    def _analytics_lines(self) -> list[str]:
        """사냥 분석 섹션 (시간/획득/시간당/바퀴). 맵 히스토리는 별도 섹션."""
        a = self._analytics
        if not a:
            return []
        out: list[str] = []
        sess = a.get("session") or {}
        laps = a.get("laps") or {}
        if sess.get("active"):
            dur = int(sess.get("duration_sec") or 0)
            gain = int(sess.get("xp_gain") or 0)
            xph = int(sess.get("xp_per_hour") or 0)
            # 사냥 시작~현재까지 실시간 총 획득 경험치.
            out.append(f"사냥 {_fmt_dur(dur)}")
            out.append(f"획득 {_fmt_xp(gain)}")
            if xph > 0:
                out.append(f"시간당 {_fmt_xph_compact(xph)}")
        n = int(laps.get("count") or 0)
        if n >= 3:
            avg_d = int(laps.get("avg_duration_sec") or 0)
            avg_g = int(laps.get("avg_xp_gain") or 0)
            out.append(
                f"바퀴 {n}회 평균 {_fmt_dur(avg_d)} · {_fmt_xp(avg_g)}"
            )
        elif n >= 1:
            avg_d = int(laps.get("avg_duration_sec") or 0)
            avg_g = int(laps.get("avg_xp_gain") or 0)
            out.append(
                f"바퀴 {n}회 (평균 {_fmt_dur(avg_d)} · {_fmt_xp(avg_g)})"
            )
        if laps.get("in_progress"):
            el = int(laps.get("cur_elapsed_sec") or 0)
            out.append(f"이번 바퀴 {_fmt_dur(el)}")
        return out

    def _map_history_lines(self) -> list[str]:
        """맵 히스토리 섹션 (최근 5개, 오래된 → 최신, 최신은 ▶ 표시).
        포맷: `▶ 선비족3-3 | 2m14s | 1.2만` — 맵 base name | 누적 체류 | 누적 획득.
        누적값은 세션 내 해당 base 의 map_stats 합 (동일 sublevel 재방문 시 합산).
        """
        a = self._analytics
        if not a:
            return []
        sess = a.get("session") or {}
        hist = sess.get("map_history") or []
        if not hist:
            return []
        stats = sess.get("map_stats") or {}
        recent = list(hist)[-5:]
        out: list[str] = []
        for i, m in enumerate(recent):
            marker = "▶" if i == len(recent) - 1 else "·"
            st = stats.get(m) or {}
            dur = int(st.get("duration_sec") or 0)
            gain = int(st.get("xp_gain") or 0)
            out.append(f"  {marker} {m} | {_fmt_dur(dur)} | {_fmt_xp(gain)}")
        return out

    def _relayout(self) -> None:
        w = self._px(self._base_w)
        title_h = self._px(22)
        inner_row_h = self._px(18)
        sep_gap = self._px(10)
        pad = self._px(10)
        a_lines = self._analytics_lines()
        m_lines = self._map_history_lines()
        h = title_h + pad
        h += inner_row_h * max(1, len(a_lines))
        if m_lines:
            h += sep_gap + title_h + inner_row_h * len(m_lines)
        self.setFixedSize(w, h + self._px(6))

    def _draw_section_header(self, qp, y: int, left_pad: int, title: str) -> int:
        """구분선 + 섹션 타이틀. 다음 라인 y 반환."""
        sep_y = y + self._px(2)
        qp.setPen(QtGui.QColor(70, 90, 130, self._a(255)))
        qp.drawLine(left_pad, sep_y,
                    self.width() - left_pad, sep_y)
        y = sep_y + self._px(18)
        qp.setFont(self._font(10))
        qp.setPen(QtGui.QColor(180, 210, 255))
        qp.drawText(left_pad, y, title)
        return y + self._px(18)

    def paintEvent(self, _ev):
        qp = QtGui.QPainter(self)
        qp.setRenderHint(QtGui.QPainter.Antialiasing, True)
        qp.setPen(QtCore.Qt.NoPen)
        qp.setBrush(QtGui.QColor(18, 20, 26, self._a(200)))
        radius = self._px(8)
        qp.drawRoundedRect(self.rect(), radius, radius)
        qp.setPen(QtGui.QColor(90, 110, 160, self._a(255)))
        qp.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), radius, radius)
        left_pad = self._px(12)
        qp.setFont(self._font(10))
        qp.setPen(QtGui.QColor(180, 210, 255))
        qp.drawText(left_pad, self._px(20), "사냥 분석")
        y = self._px(38)
        inner_row_h = self._px(18)
        a_lines = self._analytics_lines()
        if not a_lines:
            qp.setFont(self._font(9, bold=False))
            qp.setPen(QtGui.QColor(140, 140, 150))
            qp.drawText(left_pad, y, "사냥 데이터 대기")
            y += inner_row_h
        else:
            qp.setFont(self._font(9, bold=False))
            qp.setPen(QtGui.QColor(220, 220, 230))
            for s in a_lines:
                qp.drawText(left_pad, y, s)
                y += inner_row_h
        m_lines = self._map_history_lines()
        if m_lines:
            y = self._draw_section_header(qp, y, left_pad, "맵 히스토리")
            qp.setFont(self._font(9, bold=False))
            qp.setPen(QtGui.QColor(220, 220, 230))
            for s in m_lines:
                qp.drawText(left_pad, y, s)
                y += inner_row_h
        self._draw_edit_hint(qp)


class SkillAlertOverlay(_ScaledOverlay):
    """스킬 시전 3초전 1회 알림 + 3초 후 자동 소멸.

    트리거는 외부(격수 MainWindow)에서 edge detect 후 push_alert 호출.
    자체는 duration(기본 3s) 지나면 리스트에서 제거 + repaint.
    """

    def __init__(self):
        super().__init__()
        self._alerts: list[dict] = []
        self._dup_window = 2.0
        self._base_w = 520
        self.setFixedSize(self._base_w, 1)
        self._anchor_timer = QtCore.QTimer(self)
        self._anchor_timer.setInterval(500)
        self._anchor_timer.timeout.connect(self._reanchor)
        self._anchor_timer.start()
        self._expire_timer = QtCore.QTimer(self)
        self._expire_timer.setInterval(250)
        self._expire_timer.timeout.connect(self._expire_tick)
        self._expire_timer.start()

    def _on_scale_changed(self) -> None:
        self._relayout()
        self._reanchor()

    def _reanchor(self) -> None:
        """우선순위: 수동 위치 > 자동 앵커 > 유지. 모두 bound 안으로 clamp."""
        if self._manual_pos is not None:
            mx, my = self._manual_pos
            cx, cy = self._clamp_to_bound(mx, my)
            self.move(cx, cy)
            return
        if not self._game_rect:
            return
        try:
            gx, gy, gw, gh = self._game_rect
            mid = gx + gw // 2
            if self._map_rect:
                mx, my, mw, mh = self._map_rect
                y = my + mh + self._px(8)
            else:
                y = gy + int(gh * 0.15)
            x = mid - self.width() // 2
            cx, cy = self._clamp_to_bound(int(x), int(y))
            self.move(cx, cy)
        except Exception:
            pass

    def push_alert(self, msg: str, duration_sec: float = 3.0,
                   color: Optional[QtGui.QColor] = None) -> None:
        if not msg:
            return
        now = time.monotonic()
        for a in self._alerts:
            if a["msg"] == msg and (now - a.get("created_ts", 0)) < self._dup_window:
                a["expire_ts"] = now + duration_sec
                self.update()
                return
        self._alerts.append({
            "msg": str(msg),
            "key": None,
            "created_ts": now,
            "expire_ts": now + float(duration_sec),
            "color": color or QtGui.QColor(255, 230, 80),
        })
        self._relayout()
        self._reanchor()
        self.update()

    def push_countdown(self, key: str, msg: str,
                       duration_sec: float = 1.5,
                       color: Optional[QtGui.QColor] = None) -> None:
        """동일 key의 기존 알림을 덮어써 카운트다운처럼 매 초 텍스트만 갱신.

        main_window가 cd 4→3→2→1 변화마다 같은 key로 호출하면 한 줄이
        '3초전 → 2초전 → 1초전' 으로 바뀌며 유지됨. duration_sec은 다음 갱신이
        오지 않을 때의 잔존 시간(기본 1.5s: 1초 tick 한 번 거르는 여유).
        """
        if not key or not msg:
            return
        now = time.monotonic()
        col = color or QtGui.QColor(255, 230, 80)
        for a in self._alerts:
            if a.get("key") == key:
                a["msg"] = str(msg)
                a["expire_ts"] = now + float(duration_sec)
                a["color"] = col
                self.update()
                return
        self._alerts.append({
            "msg": str(msg),
            "key": str(key),
            "created_ts": now,
            "expire_ts": now + float(duration_sec),
            "color": col,
        })
        self._relayout()
        self._reanchor()
        self.update()

    def drop_countdown(self, key: str) -> None:
        """카운트다운 즉시 제거 (cd=0 또는 값 소실 시)."""
        if not key:
            return
        before = len(self._alerts)
        self._alerts = [a for a in self._alerts if a.get("key") != key]
        if len(self._alerts) != before:
            self._relayout()
            self._reanchor()
            self.update()

    def _expire_tick(self) -> None:
        now = time.monotonic()
        before = len(self._alerts)
        self._alerts = [a for a in self._alerts if a["expire_ts"] > now]
        if len(self._alerts) != before:
            self._relayout()
            self._reanchor()
            self.update()

    def _relayout(self) -> None:
        w = self._px(self._base_w)
        n = len(self._alerts)
        if n == 0:
            # 편집 모드면 드래그 잡을 수 있게 일정 높이 확보.
            self.setFixedSize(w, self._px(40) if self._edit_mode else 1)
        else:
            h = self._px(16) + self._px(32) * n
            self.setFixedSize(w, h)

    def paintEvent(self, _ev):
        qp = QtGui.QPainter(self)
        qp.setRenderHint(QtGui.QPainter.Antialiasing, True)
        if not self._alerts:
            if self._edit_mode:
                # 편집 모드 placeholder — 배경만 투명도 적용.
                qp.setPen(QtCore.Qt.NoPen)
                qp.setBrush(QtGui.QColor(0, 0, 0, self._a(120)))
                qp.drawRoundedRect(self.rect(), self._px(8), self._px(8))
                qp.setFont(self._font(10, bold=False))
                qp.setPen(QtGui.QColor(200, 200, 200))
                qp.drawText(self.rect(), QtCore.Qt.AlignCenter,
                            "스킬 알림 영역 (드래그로 이동)")
                self._draw_edit_hint(qp)
            return
        radius = self._px(10)
        # 배경·테두리 투명도 적용.
        qp.setPen(QtCore.Qt.NoPen)
        qp.setBrush(QtGui.QColor(0, 0, 0, self._a(190)))
        qp.drawRoundedRect(self.rect(), radius, radius)
        qp.setPen(QtGui.QColor(255, 220, 80, self._a(180)))
        qp.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), radius, radius)
        # 메시지 글씨는 원본 alpha 그대로.
        qp.setFont(self._font(14))
        line_h = self._px(32)
        y = self._px(28)
        for a in self._alerts:
            col = a["color"]
            # 사용자가 넘긴 QColor 는 alpha 가 대개 255. 글씨는 투명도 영향 없음.
            text_col = QtGui.QColor(col.red(), col.green(), col.blue(), 255)
            qp.setPen(text_col)
            qp.drawText(0, y - self._px(22), self.width(), line_h,
                        QtCore.Qt.AlignCenter, a["msg"])
            y += line_h
        self._draw_edit_hint(qp)
