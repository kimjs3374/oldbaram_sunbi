"""격수 전용 사냥 도우미 오버레이.

게임 위에 떠 있는 `_ScaledOverlay` 기반 HUD. 두 섹션으로 구성.

섹션 1: 사용가능스킬
  - 격수 서브클래스(도적/전사) + 승급(2/3/4)에 해당하는 스킬을 전부 행 단위로
    표시. 각 행: "스킬명  쿨초 or 준비됨".
  - 상위 승급은 하위 승급 스킬을 포함 (2차=2차만, 3차=2+3, 4차=2+3+4).
  - 쿨 OCR 미수신 스킬은 "-" (어두운 회색).

섹션 2: 파력무참 지속시간
  - 힐러별 남은 버프 초 표시. 기준 값은 힐러가 UDP로 보낸
    `buff_parlyuk_sec`(OCR) 또는 폴백 `cd_parlyuk - 135` (역산).
  - 좌측 GameOverlay 쿨다운과 동일하게 time.monotonic() 기반 로컬 감산으로
    초 단위 부드럽게 내려감. OCR 값은 **검증/보정용** — 로컬 감산값과 2초 이상
    오차일 때만 ts 재동기.

상수
  _PARLYUK_CD_SEC = 180 / _PARLYUK_DURATION_SEC = 45.
  역산 폴백: buff = max(0, cd_parlyuk - (180 - 45)) = max(0, cd_parlyuk - 135).
"""
from __future__ import annotations

import time
from typing import Dict, Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from .overlay import _ScaledOverlay


_PARLYUK_CD_SEC = 180            # 파력무참 쿨타임 (s).
_PARLYUK_DURATION_SEC = 45       # 파력무참 버프 지속시간 (s).
_ACTIVE_WINDOW_SEC = 10.0        # 힐러 보고 _last_recv 신선도 기준.
_BUFF_SYNC_TOL_SEC = 2           # OCR/CD 재동기 허용 오차 (초).
_OWN_CD_SYNC_TOL_SEC = 2         # 격수 본인 스킬 CD 로컬 감산 vs OCR 재동기 허용 오차.

# 격수 서브클래스 × 승급별 쿨타임 스킬. (표시명, 쿨타임 초).
# 상위 승급은 하위 승급 스킬을 모두 사용 가능 → get_rank_skills 가 누적 반환.
SUBCLASS_SKILLS: Dict[str, Dict[int, list]] = {
    "thief": {
        2: [("이기어검", 30)],
        3: [("무형검", 30), ("지옥무영", 30)],
        4: [("탈명사식'뇌", 120), ("파천검무", 40), ("분혼경천", 180)],
    },
    "warrior": {
        2: [("진백호령", 60), ("어검술", 20)],
        3: [("쇄혼비무", 30), ("초혼비무", 30), ("포효검황", 180)],
        4: [("탈명사식'염", 120), ("극백호참", 30), ("혈겁만파", 300)],
    },
}


def subclass_label(sub: str) -> str:
    return {"thief": "도적", "warrior": "전사"}.get(sub, sub)


def rank_label(rank: int) -> str:
    try:
        r = int(rank)
    except Exception:
        r = 4
    return {2: "2차", 3: "3차", 4: "4차"}.get(r, f"{r}차")


def get_rank_skills(sub: str, rank: int) -> list:
    """승급까지 누적된 스킬 리스트 반환. rank=2 → 2차만, 3 → 2+3, 4 → 2+3+4."""
    tree = SUBCLASS_SKILLS.get(str(sub or "thief")) or {}
    if not tree:
        return []
    try:
        r = int(rank)
    except Exception:
        r = 4
    r = max(2, min(4, r))
    out: list = []
    for k in (2, 3, 4):
        if k <= r:
            out.extend(tree.get(k, []))
    return out


def _fmt_sec(s: int) -> str:
    if s is None or s < 0:
        return "-"
    if s == 0:
        return "0s"
    if s < 60:
        return f"{s}s"
    m, r = divmod(int(s), 60)
    return f"{m}분{r:02d}s"


def _fmt_cd_readable(v: int) -> str:
    if v is None or v < 0:
        return "-"
    if v == 0:
        return "준비됨"
    if v < 60:
        return f"{v}s"
    m, r = divmod(int(v), 60)
    return f"{m}m{r:02d}s"


def _cd_color(v: int) -> QtGui.QColor:
    if v is None or v < 0:
        return QtGui.QColor(120, 120, 130)
    if v == 0:
        return QtGui.QColor(160, 230, 160)
    if v <= 5:
        return QtGui.QColor(240, 80, 80)
    if v <= 15:
        return QtGui.QColor(240, 170, 60)
    return QtGui.QColor(210, 210, 220)


class HunterHelperOverlay(_ScaledOverlay):
    """격수 전용 인게임 오버레이 — 사용가능스킬 + 힐러별 파력무참 지속시간."""

    def __init__(self):
        super().__init__()
        self._subclass: str = "thief"
        self._rank: int = 4  # 기본 최상위.
        # 격수 본인 스킬 쿨 — 로컬 monotonic anchor 기반.
        # name → {"cd": int(last_known_sec), "ts": float(monotonic_when_set)}
        # OCR 프레임 간 감산을 로컬에서 계속해 OCR 누락에도 "-" 깜빡임 방지.
        self._own_anchor: Dict[str, dict] = {}
        # OCR 이상치 pending — 기존 감산값보다 크게 작은 신값이 한 번 들어와도
        # 즉시 anchor 교체하지 않고, 연속 2회 유사 값이 나와야 수용.
        # "20s 남았던게 준비됨 뜸" 원인 = OCR 1프레임 오인식으로 anchor 망가짐.
        # name → {"v": int(last_rejected), "ts": float(monotonic)}
        self._own_pending: Dict[str, dict] = {}
        # edge 감지용 — 마지막 paint 시점의 effective 쿨.
        self._last_eff_own: Dict[str, int] = {}
        # 쿨 복귀 알림 타겟.
        self._alert_overlay = None
        # idx → {"nick": str, "buff": int, "ts": float(monotonic), "src": str}
        self._buff_rows: Dict[int, dict] = {}
        # 버프 OCR 이상치 pending (쿨과 동일 목적).
        # idx → {"v": int(last_rejected), "ts": float(monotonic)}
        self._buff_pending: Dict[int, dict] = {}
        # 2026-04-23: [PARLYUK-BUFF] 로그 스로틀 타임스탬프 (monotonic).
        self._buff_log_ts: float = 0.0
        self._base_w = 260
        self.setFixedSize(self._base_w, 120)
        # 500ms 재렌더 — 로컬 감산 초단위 흐름 반영.
        self._tick_timer = QtCore.QTimer(self)
        self._tick_timer.setInterval(500)
        self._tick_timer.timeout.connect(self._on_tick)
        self._tick_timer.start()

    def _on_tick(self) -> None:
        # 500ms 주기로 edge(>0 → 0) 감지 + 알림 방출. paint 사이드이펙트 분리.
        self._check_ready_edges()
        self._relayout()
        self.update()

    # -------- public API --------
    def set_alert_overlay(self, alert) -> None:
        """SkillAlertOverlay 참조 주입. 쿨 복귀 시 push_alert 호출."""
        self._alert_overlay = alert

    def set_subclass(self, sub: str) -> None:
        sub = str(sub or "thief")
        if sub not in SUBCLASS_SKILLS:
            sub = "thief"
        if sub == self._subclass:
            return
        self._subclass = sub
        # 서브클래스 바뀌면 이전 스킬 쿨은 무효.
        self._own_anchor = {}
        self._own_pending = {}
        self._last_eff_own = {}
        self._relayout()
        self.update()

    def set_rank(self, rank: int) -> None:
        try:
            r = int(rank)
        except Exception:
            r = 4
        r = max(2, min(4, r))
        if r == self._rank:
            return
        self._rank = r
        self._relayout()
        self.update()

    def _skill_max_cd(self, name: str) -> int:
        """SUBCLASS_SKILLS 에 선언된 스킬의 최대 쿨. 없으면 0."""
        for rank_map in SUBCLASS_SKILLS.values():
            for _r, items in rank_map.items():
                for nm, cd in items:
                    if nm == name:
                        return int(cd)
        return 0

    def update_own_cds(self, skill_cds: Dict[str, int]) -> None:
        """격수 본인 쿨 OCR 결과 반영 — {skill_name: remaining_sec}.

        비대칭 anchor 정책 (OCR 오인식 방어):
          - v < 0: 미수신 → anchor 유지 (감산 계속).
          - max_cd 선언 있는데 v > max_cd + 2: 말도 안 되는 값(OCR 글자+숫자
            오인식) → 무조건 거부.
          - 첫 관측(anchor 없음): pending 에 넣고 **연속 2회 유사값** 이어야만
            anchor 생성. 첫 프레임 오인식이 anchor 를 망쳐 앞당겨 0 도달하는
            "20s 남았는데 준비됨" 오탐 방지.
          - v > eff_old + tol: 재시전(상승) 즉시 수용.
          - |eff_old - v| < tol: 예측 부합 → 로컬 감산 유지.
          - v < eff_old - tol: 작은 값 큰 점프 → OCR 노이즈 의심.
              연속 2회 유사값 나와야만 anchor 교체.
        """
        if not isinstance(skill_cds, dict):
            return
        now_m = time.monotonic()
        for nm, v in skill_cds.items():
            try:
                v = int(v)
            except Exception:
                continue
            name = str(nm)
            if v < 0:
                self._own_pending.pop(name, None)
                continue
            # sanity: 스킬 선언된 max 쿨을 넘는 값은 OCR 오인식 (예 "탈명사식'뇌
            # 120초" 가 "1203초"로 읽힘). 무조건 거부.
            max_cd = self._skill_max_cd(name)
            if max_cd > 0 and v > max_cd + 2:
                continue
            prev = self._own_anchor.get(name)
            if prev is None:
                # 첫 관측도 연속 2회 일관 확인. OCR 첫 프레임 오인식 방어.
                pend = self._own_pending.get(name)
                if pend is not None and abs(int(pend.get("v", -999)) - v) <= 1:
                    self._own_anchor[name] = {"cd": v, "ts": now_m}
                    self._own_pending.pop(name, None)
                else:
                    self._own_pending[name] = {"v": v, "ts": now_m}
                continue
            try:
                eff_old = max(
                    0,
                    int(prev.get("cd", 0))
                    - int(now_m - float(prev.get("ts", now_m))),
                )
            except Exception:
                eff_old = -1
            if eff_old < 0:
                self._own_anchor[name] = {"cd": v, "ts": now_m}
                self._own_pending.pop(name, None)
            elif v > eff_old + _OWN_CD_SYNC_TOL_SEC:
                self._own_anchor[name] = {"cd": v, "ts": now_m}
                self._own_pending.pop(name, None)
            elif abs(eff_old - v) < _OWN_CD_SYNC_TOL_SEC:
                self._own_pending.pop(name, None)
            else:
                pend = self._own_pending.get(name)
                if pend is not None and abs(int(pend.get("v", -999)) - v) <= 1:
                    self._own_anchor[name] = {"cd": v, "ts": now_m}
                    self._own_pending.pop(name, None)
                else:
                    self._own_pending[name] = {"v": v, "ts": now_m}
        self._relayout()
        self.update()

    def _eff_own_cd(self, name: str) -> int:
        """-1(미수신) / 0(준비됨) / >0(남은초)."""
        a = self._own_anchor.get(name)
        if not a:
            return -1
        try:
            return max(0, int(a.get("cd", 0)) - int(time.monotonic() - float(a.get("ts", 0.0))))
        except Exception:
            return -1

    def _check_ready_edges(self) -> None:
        """edge (>0 → 0) 감지 시 알림 오버레이에 'NAME 준비됨!' 푸시."""
        if self._alert_overlay is None:
            return
        for nm, _cd_max in self._skill_lines():
            eff = self._eff_own_cd(nm)
            prev = self._last_eff_own.get(nm, -2)  # -2 = 미초기화.
            if prev > 0 and eff == 0:
                try:
                    self._alert_overlay.push_alert(
                        f"{nm} 준비됨!", 3.0,
                        QtGui.QColor(120, 255, 140),
                    )
                except Exception:
                    pass
            self._last_eff_own[nm] = eff

    def update_data(self, healer_cooldowns: Dict[int, dict]) -> None:
        """힐러별 파력무참 지속시간 행 갱신.

        정책 (2026-04-23 개정):
          - **버프가 실제로 켜진 힐러만 표시.** 한 번도 관측 안 됐거나 expired
            상태면 행 자체를 drop (이전엔 active 힐러면 전부 "-" 행 생성).
          - recv_buff > 0 (힐러가 버프 관측) → 첫 시전/재시전 판정 후 anchor.
          - recv_buff == 0 (힐러가 명시적 만료 보고) → 행 drop.
          - recv_buff < 0 (미수신/영역 미지정) → 기존 anchor 감산, eff>0 만 유지.

        재-anchor 엄격화:
          - recv_buff > _PARLYUK_DURATION_SEC (>45) → OCR 튐, reject.
          - recv_buff >= _PARLYUK_DURATION_SEC - 10 (35~45) 이고 eff+tol 초과 →
            신규 시전으로 인정 → anchor 리셋.
          - 그 외(eff < recv < 35) 상승 점프는 OCR 플리커로 간주 무시.

        throttled [PARLYUK-BUFF] 로그(5s/1회) — accept/reject 판정 추적.
        """
        now = time.time()
        now_m = time.monotonic()
        active: Dict[int, dict] = {}
        for idx, d in (healer_cooldowns or {}).items():
            try:
                last = float(d.get("_last_recv", 0.0))
            except Exception:
                last = 0.0
            if last <= 0 or (now - last) > _ACTIVE_WINDOW_SEC:
                continue
            active[idx] = d

        new_rows: Dict[int, dict] = {}
        log_due = (now_m - self._buff_log_ts) >= 5.0
        for idx, d in active.items():
            nick = (
                str(d.get("_locked_nick") or d.get("nickname") or "").strip()
                or f"힐러{idx + 1}"
            )
            try:
                buff_rx = int(d.get("buff_parlyuk_sec", -1))
            except Exception:
                buff_rx = -1
            src = "-"
            recv_buff = -1
            if buff_rx >= 0:
                recv_buff = max(0, buff_rx)
                src = "ocr"
            else:
                try:
                    cd_p = int(d.get("cd_parlyuk", -1))
                except Exception:
                    cd_p = -1
                if cd_p >= 0:
                    recv_buff = max(
                        0, cd_p - (_PARLYUK_CD_SEC - _PARLYUK_DURATION_SEC),
                    )
                    src = "cd"
            # sanity: 지속시간 초과 → OCR 튐, 이 틱은 아예 무시.
            if recv_buff > _PARLYUK_DURATION_SEC:
                if log_due:
                    self._log_buff(
                        f"REJECT over-duration idx={idx} recv={recv_buff} "
                        f"src={src}"
                    )
                recv_buff = -1  # 아래 분기에서 미수신 취급.
            prev = self._buff_rows.get(idx, {})
            prev_buff = int(prev.get("buff", 0))
            prev_ts = float(prev.get("ts", 0.0))
            prev_src = str(prev.get("src", "-"))
            eff_prev = 0
            if prev_ts > 0:
                eff_prev = max(0, prev_buff - int(now_m - prev_ts))

            if recv_buff > 0:
                # 2026-04-23 수정: 첫 관측은 recv 값 무관하게 anchor (원안 복구).
                # OCR 첫 캐치가 시전 1~2초 후면 recv 33~44 로 찍히는 것이 정상.
                # fresh_window 게이트는 정상 관측을 대부분 리젝트하는 버그였음.
                # 재-anchor 조건만 fresh_window + tolerance 로 엄격 유지.
                if prev_ts <= 0:
                    new_rows[idx] = {
                        "nick": nick, "buff": int(recv_buff),
                        "ts": now_m, "src": src,
                    }
                    if log_due:
                        self._log_buff(
                            f"ANCHOR first idx={idx} recv={recv_buff} "
                            f"src={src}"
                        )
                    continue
                # 기존 anchor 있음 — 재시전 판정.
                # fresh_window: 신규 시전 직후(35~45) 이어야만 re-anchor.
                # 중간값 플리커(eff=5 → OCR 20)로 버프 되살아나는 현상 차단.
                fresh_window = recv_buff >= (
                    _PARLYUK_DURATION_SEC - 10
                )
                if (recv_buff > eff_prev + _BUFF_SYNC_TOL_SEC
                        and fresh_window):
                    new_rows[idx] = {
                        "nick": nick, "buff": int(recv_buff),
                        "ts": now_m, "src": src,
                    }
                    if log_due:
                        self._log_buff(
                            f"RE-ANCHOR idx={idx} eff={eff_prev} "
                            f"recv={recv_buff} src={src}"
                        )
                    continue
                # 정상 감산 중 — eff_prev>0 이면 행 유지, 0 이면 drop.
                if eff_prev > 0:
                    new_rows[idx] = {
                        "nick": nick, "buff": prev_buff,
                        "ts": prev_ts, "src": prev_src,
                    }
                elif log_due:
                    self._log_buff(
                        f"DROP eff-zero idx={idx} recv={recv_buff} "
                        f"(not fresh, anchor expired)"
                    )
                continue
            if recv_buff == 0:
                # 힐러가 명시적 만료 보고 — 행 drop.
                if log_due and prev_ts > 0:
                    self._log_buff(
                        f"DROP healer-reports-zero idx={idx} "
                        f"prev_eff={eff_prev}"
                    )
                continue
            # recv_buff < 0: 미수신 — 기존 anchor 감산, eff_prev>0 만 유지.
            if eff_prev > 0:
                new_rows[idx] = {
                    "nick": nick, "buff": prev_buff,
                    "ts": prev_ts, "src": prev_src,
                }
        if log_due:
            self._buff_log_ts = now_m
        self._buff_rows = new_rows
        self._buff_pending.clear()
        self._relayout()
        self.update()

    def _log_buff(self, msg: str) -> None:
        """[PARLYUK-BUFF] 진단 로그 — attacker 로거(파일+콘솔)로 출력."""
        try:
            import logging as _logging
            _logging.getLogger("attacker").info(f"[PARLYUK-BUFF] {msg}")
        except Exception:
            pass

    def clear_all(self) -> None:
        self._buff_rows.clear()
        self._buff_pending.clear()
        self._own_anchor.clear()
        self._own_pending.clear()
        self._last_eff_own.clear()
        self._relayout()
        self.update()

    # -------- layout / anchor --------
    def _on_scale_changed(self) -> None:
        self._relayout()
        self._reanchor()

    def _reanchor(self) -> None:
        if self._manual_pos is not None:
            mx, my = self._manual_pos
            cx, cy = self._clamp_to_bound(mx, my)
            self.move(cx, cy)
            return
        if self._game_rect:
            gx, gy, gw, _gh = self._game_rect
            # 기본 위치: 게임 영역 우상단.
            x = gx + gw - self.width() - self._px(10)
            y = gy + self._px(10)
            cx, cy = self._clamp_to_bound(int(x), int(y))
            self.move(cx, cy)

    @staticmethod
    def _eff_buff(buff: int, ts: float) -> int:
        if buff is None or buff <= 0:
            return 0
        if ts <= 0:
            return int(buff)
        elapsed = int(max(0.0, time.monotonic() - ts))
        return max(0, int(buff) - elapsed)

    def _skill_lines(self) -> list:
        """[(name, cd_sec)] — 현재 서브클래스·승급의 스킬 전체."""
        return list(get_rank_skills(self._subclass, self._rank))

    def _relayout(self) -> None:
        w = self._px(self._base_w)
        pad = self._px(8)
        head_h = self._px(24)
        sep_gap = self._px(8)
        title_h = self._px(20)
        row_h = self._px(20)
        n_skills = max(1, len(self._skill_lines()))
        sec1_h = sep_gap + title_h + row_h * n_skills + self._px(2)
        n_buff = len(self._buff_rows)
        # 파력무참 섹션은 수신 0명이면 1줄(대시만) 유지.
        sec2_h = sep_gap + title_h + row_h * max(1, n_buff) + self._px(2)
        h = head_h + sec1_h + sec2_h + pad
        self.setFixedSize(w, h)

    # -------- paint --------
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
        row_h = self._px(20)
        qp.setFont(self._font(10))
        qp.setPen(QtGui.QColor(180, 210, 255))
        qp.drawText(
            left_pad, self._px(18),
            f"사냥 도우미 — {subclass_label(self._subclass)} {rank_label(self._rank)}",
        )

        y = self._px(18) + self._px(10)
        # 섹션 1: 사용가능스킬.
        y = self._draw_section_header(qp, y, left_pad, "사용가능스킬")
        skills = self._skill_lines()
        name_col = left_pad
        val_col = self._px(140)
        if not skills:
            qp.setFont(self._font(9, bold=False))
            qp.setPen(QtGui.QColor(140, 140, 150))
            qp.drawText(name_col, y, "스킬 데이터 없음")
            y += row_h
        else:
            qp.setFont(self._font(11))
            for nm, _cd_max in skills:
                cd_v = self._eff_own_cd(nm)
                qp.setPen(QtGui.QColor(210, 220, 235))
                qp.drawText(name_col, y, nm)
                qp.setPen(_cd_color(cd_v))
                qp.drawText(val_col, y, _fmt_cd_readable(cd_v))
                y += row_h

        # 섹션 2: 파력무참 지속시간 (로컬 감산).
        y = self._draw_section_header(qp, y, left_pad, "파력무참 지속시간")
        if not self._buff_rows:
            qp.setFont(self._font(9, bold=False))
            qp.setPen(QtGui.QColor(120, 120, 130))
            qp.drawText(name_col, y, "-")
            y += row_h
        else:
            qp.setFont(self._font(11))
            for idx in sorted(self._buff_rows.keys()):
                r = self._buff_rows[idx]
                nick = str(r.get("nick") or f"힐러{idx + 1}")
                eff = self._eff_buff(
                    int(r.get("buff", 0) or 0), float(r.get("ts", 0.0) or 0.0)
                )
                qp.setPen(QtGui.QColor(120, 200, 255))
                qp.drawText(name_col, y, nick)
                if eff > 0:
                    qp.setPen(QtGui.QColor(160, 230, 160))
                    qp.drawText(val_col, y, _fmt_sec(eff))
                else:
                    qp.setPen(QtGui.QColor(150, 150, 160))
                    qp.drawText(val_col, y, "-")
                y += row_h

        self._draw_edit_hint(qp)

    def _draw_section_header(self, qp, y: int, left_pad: int, title: str) -> int:
        sep_y = y + self._px(2)
        qp.setPen(QtGui.QColor(70, 90, 130, self._a(255)))
        qp.drawLine(left_pad, sep_y,
                    self.width() - left_pad, sep_y)
        y = sep_y + self._px(16)
        qp.setFont(self._font(10))
        qp.setPen(QtGui.QColor(180, 210, 255))
        qp.drawText(left_pad, y, title)
        return y + self._px(18)
