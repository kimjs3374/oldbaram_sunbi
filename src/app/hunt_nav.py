# -*- coding: utf-8 -*-
"""선비족 사냥 순서 네비게이션 로직 (격수 전용, 2026-06-12).

설계 문서: D:\\oldbaram\\선비족 네비게이션 오버레이.md

- 맵명 `선비족x-y` / `제2선비족x-y` 에서 x(지역 1~5), y(굴 1~5) 파싱.
  `(z)` 채널 숫자는 무관. 비매칭 맵(입구 등)은 무시 — 바퀴 상태 보존.
- 순서 우선순위: 수동 입력 > 학습 확정 > 추천 유도.
- 학습 확정 = 회전 규칙 하나로 통일: 바퀴 경계(이번 바퀴 방문 굴 재진입) 시
  visited 를 재방문 굴 기준으로 회전.
    5,4,3,2→5 ⇒ [5,4,3,2] / 5,4,3,2,1→4 ⇒ [4,3,2,1,5] /
    (확정 후) 4,3,2,1→4 ⇒ [4,3,2,1] (5 자연 탈락)
  확정 후에도 매 바퀴 재확정. 사이클 최소 길이 3 미만은 확정 보류.
- 추천 유도: 미확정 + 첫 굴 == 추천 시작굴 → 다섯굴 추천 잠정 안내
  (네/다섯굴 추천 시작굴 동일 — 사용자 예시 기준 다섯굴 기본).
- 순서 밖 굴 진입: 직전 강조 유지 + out_of_order 표시, 확정 불변.
- x 전환: 확정/수동 순서 x별 세션 캐시 저장·복원.

UI/오버레이는 src/ui/hunt_nav_overlay.py, 배선은 main_window.
"""
from __future__ import annotations

import re
import threading
from typing import Callable, List, Optional

# 맵명 파싱: 선비족x-y / 제2선비족x-y (+ 뒤에 (z) — z는 굴 내 층/채널).
_MAP_RE = re.compile(r"^(제2)?선비족([1-5])-([1-5])(?:\((\d+)\))?")
# 허브(통로) 맵: 선비족x (x 뒤에 -y 없음. '선비족1방' 류 변형 허용).
_HUB_RE = re.compile(r"^(제2)?선비족([1-5])(?:$|[^-\d])")

# x별 슬롯 배치. label: 1~5=굴, 0=입구.
# 슬롯: TL(좌상) TR(우상) LM(좌중) RM(우중) BL(좌하) BR(우하).
LAYOUTS = {
    1: {"TL": 4, "TR": 3, "LM": 5, "RM": 1, "BL": 0, "BR": 2},
    2: {"TL": 5, "TR": 4, "LM": 0, "RM": 1, "BL": 2, "BR": 3},
    3: {"TL": 5, "TR": 4, "LM": 0, "RM": 1, "BL": 2, "BR": 3},
    4: {"TL": 4, "TR": 3, "LM": 5, "RM": 0, "BL": 1, "BR": 2},
    5: {"TL": 4, "TR": 3, "LM": 5, "RM": 0, "BL": 1, "BR": 2},
}
# 추천 순서 (사용자 제공, 하단 "입력된" 네굴/다섯굴 순서).
RECOMMEND4 = {1: [5, 4, 3, 2], 2: [5, 4, 3, 2], 3: [5, 4, 3, 2],
              4: [3, 4, 1, 2], 5: [3, 4, 1, 2]}
RECOMMEND5 = {1: [5, 4, 3, 1, 2], 2: [5, 4, 1, 3, 2], 3: [5, 4, 1, 3, 2],
              4: [3, 4, 5, 1, 2], 5: [3, 4, 5, 1, 2]}

_MIN_CYCLE = 3  # 세굴 미만 사이클은 확정 보류.


def parse_map(map_name: str):
    """맵명 → (base, x, y, z). 굴=(base,x,y,z|0), 허브=(base,x,0,0).
    비매칭(입구/타 사냥터)은 None.

    허브 = `선비족x` (x 뒤 -y 없음) — 굴(7)에서 나오면 도착하는 통로.
    """
    s = str(map_name or "").strip()
    m = _MAP_RE.match(s)
    if m is not None:
        base = ("제2선비족" if m.group(1) else "선비족")
        z = int(m.group(4)) if m.group(4) else 0
        return base, int(m.group(2)), int(m.group(3)), z
    h = _HUB_RE.match(s)
    if h is not None:
        base = ("제2선비족" if h.group(1) else "선비족")
        return base, int(h.group(2)), 0, 0
    return None


def parse_order_text(text) -> Optional[List[int]]:
    """'5,4,3,2' → [5,4,3,2]. 유효 = 1~5 중복 없는 3~5개. 아니면 None."""
    out: List[int] = []
    for tok in str(text or "").replace(" ", "").split(","):
        if not tok:
            continue
        if not tok.isdigit():
            return None
        v = int(tok)
        if not (1 <= v <= 5) or v in out:
            return None
        out.append(v)
    if not (3 <= len(out) <= 5):
        return None
    return out


class CaveOrderTracker:
    """굴 순서 학습/확정/유도 상태머신 (순수 로직, 스레드 안전).

    상태(state): idle(대기) | recommend(추천 유도) | learning(학습중)
                 | confirmed(확정) | manual(수동)
    """

    def __init__(self, log_cb: Optional[Callable[[str], None]] = None):
        self._log = log_cb or (lambda s: None)
        self._lock = threading.RLock()
        self._base = ""
        self._x_ocr = 0          # OCR 관측 x (0=미관측)
        self._x_override = 0     # GUI 수동 x (0=자동)
        self._cur_y = 0
        self._visited: List[int] = []   # 이번 바퀴 방문 시퀀스
        self._order: List[int] = []     # 현재 적용 순서
        self._state = "idle"
        self._manual = False
        self._next_y = 0
        self._out_of_order = False
        self._cache: dict = {}          # x -> (order list, state)
        self._auto_text = ""            # GUI 텍스트필드 자동 입력값
        self._notice = ""
        self._notice_seq = 0
        # 허브/완주 추적 (2026-06-12): 굴(7)→허브(선비족x) 도착 = 강조 타이밍.
        self._at_hub = False
        self._from_z7 = False
        self._last_z = 0                # 현재(직전) 굴에서 마지막 관측 (z).

    # ── 외부 API ──────────────────────────────────────────────────────────

    def eff_x(self) -> int:
        return self._x_override or self._x_ocr

    def set_x_override(self, x: int) -> None:
        with self._lock:
            old = self.eff_x()
            self._x_override = int(x) if 1 <= int(x) <= 5 else 0
            new = self.eff_x()
            if new != old:
                self._switch_x(new)

    def set_manual_text(self, text, user_edit: bool = True) -> None:
        """텍스트필드 변경 반영.

        user_edit=True(사용자 직접 수정): 유효 → 수동 고정(학습이 덮지 않음),
        빈 값 → 학습 재개. 무효 비공백 → 무시(이전 유지).
        user_edit=False(프로그램 echo): no-op (자동입력 재진입 차단).
        """
        if not user_edit:
            return
        with self._lock:
            parsed = parse_order_text(text)
            if parsed:
                self._manual = True
                self._order = parsed
                self._state = "manual"
                x = self.eff_x()
                if x:
                    self._cache[x] = (list(parsed), "manual")
                self._recalc_next()
                self._set_notice(
                    f"굴 순서 수동 설정: {'→'.join(map(str, parsed))}")
            elif not str(text or "").strip():
                # 비움 → 학습 재개.
                self._manual = False
                self._order = []
                self._state = "idle"
                self._visited = [self._cur_y] if self._cur_y else []
                self._auto_text = ""
                x = self.eff_x()
                if x and x in self._cache:
                    del self._cache[x]
                self._recalc_next()
                self._set_notice("굴 순서 비움 — 자동 학습 재개")
            # 무효 비공백은 무시.

    def observe(self, map_name: str) -> None:
        """맵 변경 시 호출 (격수 OCR 확정 맵명). 비매칭 맵은 무시.

        허브(선비족x) 도착 시: 직전 굴이 (7)에서 나온 거면(from_z7) 다음 굴
        강조 안내 타이밍 (사용자 2026-06-12: '(7)에서 나와서 선비족x로
        왔을때'). 굴 진입 시 at_hub 해제.
        """
        p = parse_map(map_name)
        if p is None:
            return
        base, x, y, z = p
        with self._lock:
            self._base = base
            old_eff = self.eff_x()
            self._x_ocr = x
            new_eff = self.eff_x()
            if new_eff != old_eff and old_eff != 0:
                self._switch_x(new_eff)
            if y == 0:
                # 허브 도착 — 직전 굴 마지막 (z)가 7이면 강조 안내 발동.
                self._at_hub = True
                self._from_z7 = (self._last_z == 7)
                if self._from_z7 and self._next_y:
                    self._set_notice(f"다음 굴: {self._next_y}굴로 이동")
                return
            self._at_hub = False
            self._from_z7 = False
            if y == self._cur_y:
                self._last_z = z or self._last_z  # 같은 굴 내 (z) 진행 갱신.
                return  # dedup
            self._cur_y = y
            self._last_z = z
            if self._manual:
                self._recalc_next()
                return
            self._learn(new_eff, y)
            self._recalc_next()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "base": self._base or "선비족",
                "x": self.eff_x(),
                "cur_y": self._cur_y,
                "next_y": self._next_y,
                "order": list(self._order),
                "state": self._state,
                "out_of_order": self._out_of_order,
                "visited": list(self._visited),
                "auto_text": self._auto_text,
                "notice": self._notice,
                "notice_seq": self._notice_seq,
                "at_hub": self._at_hub,
                "from_z7": self._from_z7,
                "last_z": self._last_z,
            }

    # ── 내부 ─────────────────────────────────────────────────────────────

    def _learn(self, x: int, y: int) -> None:
        if y in self._visited:
            # 바퀴 경계 — 회전 확정.
            idx = self._visited.index(y)
            cyc = self._visited[idx:] + self._visited[:idx]
            if len(cyc) >= _MIN_CYCLE:
                self._order = cyc
                self._state = "confirmed"
                if x:
                    self._cache[x] = (list(cyc), "confirmed")
                self._auto_text = ",".join(map(str, cyc))
                self._set_notice(
                    f"굴 순서 확정: {'→'.join(map(str, cyc))}")
            self._visited = [y]
            return
        self._visited.append(y)
        if self._state in ("idle", "learning", "recommend"):
            # 확정 전에는 텍스트필드에 잠정 시퀀스 노출.
            self._auto_text = ",".join(map(str, self._visited))
        if (self._state == "idle" and len(self._visited) == 1 and x
                and RECOMMEND5.get(x) and y == RECOMMEND5[x][0]):
            # 첫 굴 == 추천 시작굴 → 다섯굴 추천 잠정 유도.
            self._order = list(RECOMMEND5[x])
            self._state = "recommend"
            self._set_notice(
                f"추천 순서 유도: {'→'.join(map(str, self._order))}")
        elif self._state == "idle":
            self._state = "learning"

    def _switch_x(self, new_x: int) -> None:
        """지역 전환 — 바퀴 리셋 + x별 캐시 복원."""
        self._visited = []
        self._cur_y = 0
        self._next_y = 0
        self._out_of_order = False
        self._at_hub = False
        self._from_z7 = False
        self._last_z = 0
        ent = self._cache.get(new_x)
        if ent:
            self._order = list(ent[0])
            self._state = str(ent[1])
            self._manual = (self._state == "manual")
            self._auto_text = ",".join(map(str, self._order))
        else:
            self._order = []
            self._state = "idle"
            self._manual = False
            self._auto_text = ""
        self._set_notice(f"지역 전환: {self._base or '선비족'}{new_x}")

    def _recalc_next(self) -> None:
        if self._order and self._cur_y in self._order:
            i = self._order.index(self._cur_y)
            self._next_y = self._order[(i + 1) % len(self._order)]
            self._out_of_order = False
        elif self._order and self._cur_y:
            # 순서 밖 굴 — 직전 강조 유지 + 표시만 (확정 불변).
            self._out_of_order = True
        elif not self._order:
            self._next_y = 0
            self._out_of_order = False

    def _set_notice(self, msg: str) -> None:
        self._notice = str(msg)
        self._notice_seq += 1
        try:
            self._log(f"[HUNT-NAV] {msg}")
        except Exception:
            pass
