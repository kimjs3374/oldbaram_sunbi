"""격수 스킬 범위 시각화 오버레이 (2026-04-22).

격수 캐릭터 위치(빨탭 좌표) + 바라보는 방향(last_dir) 기준으로 쿨타임 스킬의
타격 범위를 게임 화면 위에 투명 HUD 로 표시.

좌표 소스
  - State.red_tab == True 인 동안만 표시. red_cx/red_cy 가 캐릭터 중심.
  - State.last_dir: "U"/"D"/"L"/"R". "-" 이면 직전 방향 유지.

표시 스킬 (서브클래스/승급별 누적)
  - 도적 4차: 파천검무 (앞 4x3).
  - 전사 2차: 어검술 (앞 1칸 + 상하좌/우 십자 끝 1칸).
  - 전사 3차: 쇄혼비무/초혼비무 (앞 4칸 건너뛰고 1칸).
  - 전사 4차: + 극백호참 (다이아/콘 5행).

회전 규칙 (R 기준 offset → 다른 방향)
  R:(x,y)  L:(-x,-y)  U:(y,-x)  D:(-y,x).
  게임 좌표 y+ 는 아래. U 는 위쪽(y 감소)으로 범위 뻗어야 함.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from PyQt5 import QtCore, QtGui, QtWidgets

from .overlay import _ScaledOverlay


# R(우측) 기준 범위 셀 오프셋 (dx, dy). ★ = (0, 0).
SKILL_RANGES_R: Dict[str, List[Tuple[int, int]]] = {
    "파천검무": [(x, y) for x in (1, 2, 3, 4) for y in (-1, 0, 1)],
    "극백호참": [
        (3, -2),
        (2, -1), (3, -1),
        (1, 0), (2, 0), (3, 0),
        (2, 1), (3, 1),
        (3, 2),
    ],
    "어검술": [(2, -1), (1, 0), (2, 0), (2, 1)],
    "쇄혼비무": [(4, 0)],
}

SKILL_COLOR: Dict[str, Tuple[int, int, int]] = {
    "파천검무": (230, 72, 72),    # 빨강
    "극백호참": (170, 110, 230),  # 보라
    "어검술": (80, 200, 200),     # 청록
    "쇄혼비무": (230, 200, 60),   # 노랑
}


def active_skills(sub: str, rank: int) -> List[str]:
    """서브클래스 + 승급 누적 기반 표시 대상 스킬 이름."""
    out: List[str] = []
    s = str(sub or "thief")
    try:
        r = int(rank)
    except Exception:
        r = 4
    if s == "thief":
        if r >= 4:
            out.append("파천검무")
    elif s == "warrior":
        if r >= 2:
            out.append("어검술")
        if r >= 3:
            out.append("쇄혼비무")
        if r >= 4:
            out.append("극백호참")
    return out


def rotate_offset(off: Tuple[int, int], dir_: str) -> Tuple[int, int]:
    x, y = off
    d = (dir_ or "R").upper()
    if d == "R":
        return (x, y)
    if d == "L":
        return (-x, -y)
    if d == "U":
        return (y, -x)
    if d == "D":
        return (-y, x)
    return (x, y)


class SkillRangeOverlay(_ScaledOverlay):
    """game_rect 전체를 덮는 투명 HUD. 캐릭터 기준 스킬 범위 렌더.

    기본 위치/크기는 game_rect 에 자동 맞춤 — 수동 드래그 불필요. edit_mode
    는 공통 체크박스에 연동되어도 시각적 테두리만 나오고 좌표는 고정.
    """

    def __init__(self):
        super().__init__()
        self._red_cx: int = 0
        self._red_cy: int = 0
        self._red_tab: bool = False
        self._last_dir: str = "R"
        self._subclass: str = "thief"
        self._rank: int = 4
        # 1타일 픽셀. 옛바 카메라가 쿼터뷰라 가로/세로 픽셀 길이가 다름 →
        # W/H 분리. UI 에서 각각 조정. 정사각 타일 게임이면 두 값 같게.
        self._tile_w: int = 32
        self._tile_h: int = 32
        self._tile: int = 32  # legacy getter 호환.
        # 방향별 X/Y 오프셋 — U/D/L/R 각각 독립.
        # 박스 중심 좌표(red_cx/red_cy) 에 더해져 최종 기준점.
        self._offset_u_x: int = 0
        self._offset_u_y: int = 0
        self._offset_d_x: int = 0
        self._offset_d_y: int = 0
        self._offset_l_x: int = 0
        self._offset_l_y: int = 0
        self._offset_r_x: int = 0
        self._offset_r_y: int = 0
        # legacy (상하/좌우 공용) — 기존 저장 설정 호환용.
        self._offset_vert_x: int = 0
        self._offset_vert_y: int = 0
        self._offset_horz_x: int = 0
        self._offset_horz_y: int = 0
        # legacy — 기존 설정 저장본 호환용. update 시 vert_y + horz_y 에 더함.
        self._char_offset_y: int = 0
        # 최근 수신한 YOLO 박스 bbox. 지금은 저장만 (향후 확장 대비).
        self._red_box: Optional[Tuple[int, int, int, int]] = None
        # 스킬별 투명도 0~100 (기본 80). fill/border alpha 에 비례.
        self._skill_alpha: Dict[str, int] = {
            nm: 80 for nm in SKILL_RANGES_R.keys()
        }
        # 스킬별 사용여부 (체크박스). False 면 해당 스킬 셀 렌더 생략.
        self._skill_enabled: Dict[str, bool] = {
            nm: True for nm in SKILL_RANGES_R.keys()
        }
        # 렌더 기준 좌상단 (절대 화면 좌표). game_rect 없으면 msw HWND client
        # rect 또는 primary screen 으로 폴백. update 될 때마다 _on_scale_changed
        # 에서 재계산.
        self._effective_origin: Tuple[int, int] = (0, 0)
        self.setFixedSize(400, 300)
        # 방향키 실시간 감지 타이머. coord 이동 기반 last_dir 은 정지 시
        # stale → 캐릭터 스프라이트가 새 방향 보는데 오버레이는 이전 방향
        # 유지 문제. 50ms 주기로 Win32 GetAsyncKeyState 확인.
        self._key_timer = QtCore.QTimer(self)
        self._key_timer.setInterval(50)
        self._key_timer.timeout.connect(self._poll_dir_keys)
        self._key_timer.start()

    def _poll_dir_keys(self) -> None:
        """Win32 GetAsyncKeyState 로 방향키(↑↓←→)만 감지. msw.exe 가
        foreground(활성창) 일 때만 적용 — 다른 앱 작업 중에 키 눌러도
        overlay 방향 바뀌지 않게. WASD 는 옛바 방향키 아니므로 제외.
        """
        try:
            import ctypes
            u32 = ctypes.windll.user32
            # msw hwnd 가 foreground 인지 확인. attach_to_hwnd 로 저장된
            # _target_hwnd 와 GetForegroundWindow() 비교.
            if self._target_hwnd:
                fg = int(u32.GetForegroundWindow())
                if fg != int(self._target_hwnd):
                    return
            VK_UP, VK_DOWN, VK_LEFT, VK_RIGHT = 0x26, 0x28, 0x25, 0x27

            def _down(vk: int) -> bool:
                return (u32.GetAsyncKeyState(int(vk)) & 0x8000) != 0

            new_dir = None
            if _down(VK_UP):
                new_dir = "U"
            elif _down(VK_DOWN):
                new_dir = "D"
            elif _down(VK_LEFT):
                new_dir = "L"
            elif _down(VK_RIGHT):
                new_dir = "R"
            if new_dir and new_dir != self._last_dir:
                self._last_dir = new_dir
                self.update()
        except Exception:
            pass

    # ---- 외부 API ----
    def update_state(self, red_tab: bool, red_cx: int, red_cy: int,
                     last_dir: str,
                     box: Optional[Tuple[int, int, int, int]] = None) -> None:
        """격수 빨탭 좌표 갱신. 방향은 무시(_poll_dir_keys 단독).
        box: (x1,y1,x2,y2) 절대 화면 좌표. 주어지면 방향별 기준점 계산에
        사용 (무기 반대쪽 모서리 + 캐릭터 반크기).
        """
        self._red_tab = bool(red_tab)
        self._red_cx = int(red_cx)
        self._red_cy = int(red_cy)
        if box is not None and len(box) == 4:
            self._red_box = (int(box[0]), int(box[1]),
                             int(box[2]), int(box[3]))
        self.update()

    # 방향별 X/Y setter (U/D/L/R 독립).
    def set_offset_u_x(self, v: int) -> None:
        try: self._offset_u_x = int(v)
        except Exception: pass
        self.update()

    def set_offset_u_y(self, v: int) -> None:
        try: self._offset_u_y = int(v)
        except Exception: pass
        self.update()

    def set_offset_d_x(self, v: int) -> None:
        try: self._offset_d_x = int(v)
        except Exception: pass
        self.update()

    def set_offset_d_y(self, v: int) -> None:
        try: self._offset_d_y = int(v)
        except Exception: pass
        self.update()

    def set_offset_l_x(self, v: int) -> None:
        try: self._offset_l_x = int(v)
        except Exception: pass
        self.update()

    def set_offset_l_y(self, v: int) -> None:
        try: self._offset_l_y = int(v)
        except Exception: pass
        self.update()

    def set_offset_r_x(self, v: int) -> None:
        try: self._offset_r_x = int(v)
        except Exception: pass
        self.update()

    def set_offset_r_y(self, v: int) -> None:
        try: self._offset_r_y = int(v)
        except Exception: pass
        self.update()

    # legacy setter (U/D/L/R 모두 동일 값 매핑).
    def set_offset_vert(self, x: int, y: int) -> None:
        self.set_offset_u_x(x); self.set_offset_u_y(y)
        self.set_offset_d_x(x); self.set_offset_d_y(y)

    def set_offset_horz(self, x: int, y: int) -> None:
        self.set_offset_l_x(x); self.set_offset_l_y(y)
        self.set_offset_r_x(x); self.set_offset_r_y(y)

    # legacy setter (제거한 반폭/반높이/char_offset_y 호환).
    def set_char_half_w(self, v: int) -> None:
        pass

    def set_char_half_h(self, v: int) -> None:
        pass

    def set_subclass(self, sub: str) -> None:
        self._subclass = str(sub or "thief")
        self.update()

    def set_rank(self, rank: int) -> None:
        try:
            self._rank = int(rank)
        except Exception:
            self._rank = 4
        self.update()

    def set_tile(self, tile: int) -> None:
        """가로/세로 동시 설정 (정사각 타일). 하위호환용."""
        try:
            t = int(tile)
        except Exception:
            t = 32
        t = max(8, min(120, t))
        self._tile = t
        self._tile_w = t
        self._tile_h = t
        self.update()

    def set_tile_w(self, v: int) -> None:
        try:
            t = int(v)
        except Exception:
            t = 32
        self._tile_w = max(8, min(120, t))
        self.update()

    def set_tile_h(self, v: int) -> None:
        try:
            t = int(v)
        except Exception:
            t = 32
        self._tile_h = max(8, min(120, t))
        self.update()

    def get_tile(self) -> int:
        return int(self._tile_w)

    def set_char_offset_y(self, v: int) -> None:
        try:
            self._char_offset_y = int(v)
        except Exception:
            self._char_offset_y = 0
        self.update()

    def set_skill_alpha(self, name: str, pct: int) -> None:
        """스킬별 투명도 설정 (0~100). 0 = 완전투명, 100 = 원본."""
        try:
            p = int(pct)
        except Exception:
            return
        p = max(0, min(100, p))
        if name in self._skill_alpha:
            self._skill_alpha[name] = p
            self.update()

    def set_skill_enabled(self, name: str, on: bool) -> None:
        if name in self._skill_enabled:
            self._skill_enabled[name] = bool(on)
            self.update()

    # ---- _ScaledOverlay 오버라이드 ----
    def _on_scale_changed(self) -> None:
        """렌더 영역 자동 결정. 우선순위: game_rect > msw HWND client > primary screen.

        `red_cx/red_cy` 는 절대 화면 좌표이므로 effective_origin 을 빼서 local
        좌표로 변환. 격수가 game region 을 설정 안 해도 HWND 폴백으로 동작.
        """
        gx, gy, gw, gh = self._resolve_effective_rect()
        self._effective_origin = (int(gx), int(gy))
        self.setFixedSize(max(100, int(gw)), max(100, int(gh)))
        cx, cy = self._clamp_to_bound(int(gx), int(gy))
        self.move(cx, cy)

    def _resolve_effective_rect(self) -> Tuple[int, int, int, int]:
        if self._game_rect is not None:
            return tuple(self._game_rect)  # type: ignore[return-value]
        # 2) msw HWND client rect (attach_to_hwnd 에서 설정됨).
        if self._target_hwnd:
            try:
                from ..capture.screen import user32
                import ctypes
                from ctypes import wintypes
                rect = wintypes.RECT()
                user32.GetClientRect(
                    int(self._target_hwnd), ctypes.byref(rect)
                )
                pt = wintypes.POINT(rect.left, rect.top)
                user32.ClientToScreen(
                    int(self._target_hwnd), ctypes.byref(pt)
                )
                w = int(rect.right - rect.left)
                h = int(rect.bottom - rect.top)
                if w > 0 and h > 0:
                    return (int(pt.x), int(pt.y), w, h)
            except Exception:
                pass
        # 3) primary screen.
        try:
            scr = QtWidgets.QApplication.primaryScreen().geometry()
            return (int(scr.x()), int(scr.y()),
                    int(scr.width()), int(scr.height()))
        except Exception:
            return (0, 0, 1920, 1080)

    # ---- paint ----
    def paintEvent(self, ev) -> None:  # type: ignore[override]
        qp = QtGui.QPainter(self)
        qp.setRenderHint(QtGui.QPainter.Antialiasing, False)
        ox, oy = self._effective_origin
        # 빨탭 미인식이면 셀 렌더 생략.
        if not self._red_tab:
            self._draw_edit_hint(qp)
            qp.end()
            return
        # 박스 중심 + 방향별 사용자 오프셋. 상하(U/D) 와 좌우(L/R) 각각
        # X/Y 한 쌍 제공 → 사용자가 방향 전환마다 수동 미세조정.
        base_x = int(self._red_cx - ox)
        base_y = int(self._red_cy - oy)
        if self._last_dir == "U":
            cx_l = base_x + int(self._offset_u_x)
            cy_l = base_y + int(self._offset_u_y)
        elif self._last_dir == "D":
            cx_l = base_x + int(self._offset_d_x)
            cy_l = base_y + int(self._offset_d_y)
        elif self._last_dir == "L":
            cx_l = base_x + int(self._offset_l_x)
            cy_l = base_y + int(self._offset_l_y)
        elif self._last_dir == "R":
            cx_l = base_x + int(self._offset_r_x)
            cy_l = base_y + int(self._offset_r_y)
        else:
            cx_l = base_x
            cy_l = base_y
        tw = int(self._tile_w)
        th = int(self._tile_h)
        skills = active_skills(self._subclass, self._rank)
        for nm in skills:
            # 체크박스 해제 시 해당 스킬 렌더 생략.
            if not self._skill_enabled.get(nm, True):
                continue
            cells = SKILL_RANGES_R.get(nm) or []
            r, g, b = SKILL_COLOR.get(nm, (200, 200, 200))
            # 스킬별 투명도 계수 (0~100). 0 이면 셀 생략.
            sp = int(self._skill_alpha.get(nm, 80))
            if sp <= 0:
                continue
            scale = sp / 100.0
            fill = QtGui.QColor(r, g, b, self._a(int(80 * scale)))
            border = QtGui.QColor(r, g, b, self._a(int(230 * scale)))
            qp.setBrush(fill)
            qp.setPen(QtGui.QPen(border, max(1, self._px(2))))
            for off in cells:
                dx, dy = rotate_offset(off, self._last_dir)
                rect = QtCore.QRect(
                    cx_l + dx * tw - tw // 2,
                    cy_l + dy * th - th // 2,
                    tw, th,
                )
                qp.drawRect(rect)
        # 캐릭터 중심 마커 (흰 원 + 검은 테두리).
        r_marker = max(4, min(tw, th) // 6)
        qp.setBrush(QtGui.QColor(255, 255, 255, self._a(230)))
        qp.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, self._a(230)),
                             max(1, self._px(1))))
        qp.drawEllipse(QtCore.QPoint(cx_l, cy_l), r_marker, r_marker)
        # 방향 표시: 작은 삼각형 팁을 마커 외곽에 그림.
        self._draw_dir_tip(qp, cx_l, cy_l, min(tw, th))
        self._draw_edit_hint(qp)
        qp.end()

    def _draw_dir_tip(self, qp: QtGui.QPainter,
                      cx: int, cy: int, tile: int) -> None:
        """last_dir 방향 쪽으로 작은 삼각형. 캐릭터에서 tile/2 거리."""
        d = self._last_dir
        off = tile // 2
        size = max(4, tile // 4)
        tri: List[QtCore.QPoint] = []
        if d == "R":
            tip = QtCore.QPoint(cx + off, cy)
            tri = [tip, QtCore.QPoint(tip.x() - size, cy - size // 2),
                   QtCore.QPoint(tip.x() - size, cy + size // 2)]
        elif d == "L":
            tip = QtCore.QPoint(cx - off, cy)
            tri = [tip, QtCore.QPoint(tip.x() + size, cy - size // 2),
                   QtCore.QPoint(tip.x() + size, cy + size // 2)]
        elif d == "U":
            tip = QtCore.QPoint(cx, cy - off)
            tri = [tip, QtCore.QPoint(cx - size // 2, tip.y() + size),
                   QtCore.QPoint(cx + size // 2, tip.y() + size)]
        elif d == "D":
            tip = QtCore.QPoint(cx, cy + off)
            tri = [tip, QtCore.QPoint(cx - size // 2, tip.y() - size),
                   QtCore.QPoint(cx + size // 2, tip.y() - size)]
        if not tri:
            return
        poly = QtGui.QPolygon(tri)
        qp.setBrush(QtGui.QColor(255, 255, 255, self._a(230)))
        qp.setPen(QtGui.QPen(QtGui.QColor(20, 20, 20, self._a(230)),
                             max(1, self._px(1))))
        qp.drawPolygon(poly)
