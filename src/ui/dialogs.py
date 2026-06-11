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



class SkillDialog(QtWidgets.QDialog):
    """스킬 설정 전용 팝업.

    2026-04-20 VK 재배치:
    - 메인힐=1 (봉황/신령 택1 통합), 혼마술=2, 공력증강=3,
      백호=4, 백호첨=5, 부활=6, 파력무참=8, 금강불체=0.
    - NumLock 싸이클 슬롯: 메인힐 + 혼마술 (공력증강은 조건부로 이동).
    - 조건부 스킬: 백호/백호첨/공력증강/부활/파력무참/금강불체.
    - 임계치: 공력증강 MP% (부활은 HP==0 고정).

    속성 (main_window 의존):
      spin_mainheal, rb_bonghwang, rb_shinryoung, spin_honmasul, chk_honmasul,
      skill_chks[name], skill_spins[name], parlyuk_spin,
      gyoungryeok_mp_spin, btn_nl_off.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("스킬 설정")
        self.resize(380, 560)
        lay = QtWidgets.QVBoxLayout(self)

        # 주력 힐 박스 (NumLock 싸이클).
        self.primary_box = QtWidgets.QGroupBox("주력 힐 (NumLock 싸이클)")
        pl = QtWidgets.QGridLayout(self.primary_box)
        # 메인힐 — 봉황/신령 택1 (라디오), VK 공용 1슬롯.
        lbl_mh = QtWidgets.QLabel("메인힐")
        self.rb_bonghwang = QtWidgets.QRadioButton("봉황의기원")
        self.rb_shinryoung = QtWidgets.QRadioButton("신령의기원")
        self.rb_bonghwang.setChecked(True)
        self.spin_mainheal = QtWidgets.QSpinBox()
        self.spin_mainheal.setRange(0, 9); self.spin_mainheal.setValue(1)
        # 하위 호환용 alias (기존 코드가 spin_bonghwang/spin_shinryoung 참조 가능).
        self.spin_bonghwang = self.spin_mainheal
        self.spin_shinryoung = self.spin_mainheal
        pl.addWidget(lbl_mh, 0, 0)
        pl.addWidget(self.rb_bonghwang, 0, 1)
        pl.addWidget(self.rb_shinryoung, 0, 2)
        pl.addWidget(QtWidgets.QLabel("NumPad"), 0, 3)
        pl.addWidget(self.spin_mainheal, 0, 4)
        # 혼마술 독립.
        self.chk_honmasul = QtWidgets.QCheckBox("혼마술")
        self.chk_honmasul.setChecked(True)
        self.spin_honmasul = QtWidgets.QSpinBox()
        self.spin_honmasul.setRange(0, 9); self.spin_honmasul.setValue(2)
        pl.addWidget(self.chk_honmasul, 1, 0, 1, 3)
        pl.addWidget(QtWidgets.QLabel("NumPad"), 1, 3)
        pl.addWidget(self.spin_honmasul, 1, 4)
        self.btn_nl_off = QtWidgets.QPushButton("NumLock OFF (싸이클 활성)")
        pl.addWidget(self.btn_nl_off, 2, 0, 1, 5)
        lay.addWidget(self.primary_box)

        # 조건부 스킬 박스.
        self.skill_box = QtWidgets.QGroupBox("조건부 스킬")
        sl2 = QtWidgets.QGridLayout(self.skill_box)
        self.skill_chks = {}
        self.skill_spins = {}
        # name, default_on, default_vk, has_vk.
        # has_vk=False → VK 스피너 없음 (키 시퀀스 하드코딩, 사용자 변경 불가).
        defaults = [
            ("백호의희원", True, 4, True),
            ("백호의희원첨", True, 5, True),
            ("공력증강", True, 3, True),    # MP% 임계치로 조건부 전환.
            ("부활", True, 6, True),        # 자가부활(자기 HP=0) + 격수부활.
            ("파력무참", True, 8, True),
            ("금강불체", False, 0, True),
        ]
        for i, (name, on, vk_default, has_vk) in enumerate(defaults):
            c = QtWidgets.QCheckBox(name)
            c.setChecked(on)
            sl2.addWidget(c, i, 0)
            self.skill_chks[name] = c
            if has_vk:
                sl2.addWidget(QtWidgets.QLabel("NumPad"), i, 1)
                sp = QtWidgets.QSpinBox()
                sp.setRange(0, 9); sp.setValue(vk_default)
                sl2.addWidget(sp, i, 2)
                self.skill_spins[name] = sp
            else:
                # 키 시퀀스 고정 스킬은 설명 라벨.
                info = {}.get(name, "")
                sl2.addWidget(QtWidgets.QLabel(info), i, 1, 1, 2)
        # 파력무참 오프셋.
        self.parlyuk_spin = QtWidgets.QSpinBox()
        self.parlyuk_spin.setRange(0, 180); self.parlyuk_spin.setValue(0)
        sl2.addWidget(QtWidgets.QLabel("파력무참 오프셋(s)"),
                      len(defaults), 0)
        sl2.addWidget(self.parlyuk_spin, len(defaults), 1, 1, 2)
        # 파력무참 시전 굴 (2026-06-10): 맵명 끝 (N) 의 N 목록. 예 '3,5'.
        self.parlyuk_maps_edit = QtWidgets.QLineEdit()
        self.parlyuk_maps_edit.setPlaceholderText("예: 3,5 (비우면 전체 굴)")
        self.parlyuk_maps_edit.setToolTip(
            "파력무참을 특정 서브굴에서만 시전. 맵명 끝 (N) 의 N 을 쉼표로.\n"
            "예: '3,5' → 선비족x-x(3), 선비족x-x(5) 에서만. 비우면 전체 굴."
        )
        sl2.addWidget(QtWidgets.QLabel("파력무참 시전 굴"),
                      len(defaults) + 1, 0)
        sl2.addWidget(self.parlyuk_maps_edit, len(defaults) + 1, 1, 1, 2)
        lay.addWidget(self.skill_box)

        # 타겟 시퀀스 박스 (F11/F12 동작 토글).
        self.seq_box = QtWidgets.QGroupBox("타겟 시퀀스 (F11/F12)")
        sql = QtWidgets.QVBoxLayout(self.seq_box)
        self.chk_f11_ab_combined = QtWidgets.QCheckBox(
            "F11 누르면 블록 A 뒤 블록 B 자동 실행 (해제: 블록 A 단독)"
        )
        self.chk_f11_ab_combined.setChecked(True)
        self.chk_f11_ab_combined.setToolTip(
            "ON: F11 → 블록 A(부활) → 블록 B(격수 복귀 + 토글 재ON).\n"
            "OFF: F11 → 블록 A 만. 블록 B 는 F12 로 수동 실행."
        )
        sql.addWidget(self.chk_f11_ab_combined)
        lay.addWidget(self.seq_box)

        # 임계치 박스 (공력증강).
        self.thr_box = QtWidgets.QGroupBox("임계치 (조건부 발동)")
        tl = QtWidgets.QGridLayout(self.thr_box)
        tl.addWidget(QtWidgets.QLabel("공력증강 MP (% 미만)"), 0, 0)
        self.gyoungryeok_mp_spin = QtWidgets.QSpinBox()
        self.gyoungryeok_mp_spin.setRange(1, 99)
        self.gyoungryeok_mp_spin.setValue(30)
        tl.addWidget(self.gyoungryeok_mp_spin, 0, 1)
        lay.addWidget(self.thr_box)

        # 닫기 버튼.
        self.btn_close = QtWidgets.QPushButton("닫기")
        self.btn_close.clicked.connect(self.hide)
        lay.addWidget(self.btn_close)


class OverlayDialog(QtWidgets.QDialog):
    """오버레이 설정 전용 팝업 (2026-06-12 사용자 요청 — 별도 윈도우).

    위젯 생성만 담당, 시그널은 main_window 에서 연결 (SkillRangeDialog 패턴).
    체크된 오버레이만 표시 + 위치 편집 + 투명도.

    외부 참조 위젯:
      chk_overlay(전체 마스터), kind_chks(dict kind→QCheckBox),
      chk_overlay_edit, slider_overlay_opacity, lbl_overlay_opacity.
    """

    # (kind, 라벨, 툴팁) — kind 는 위치 저장 키와 동일.
    KINDS = (
        ("cd", "힐러 쿨 상태", "힐러/쩔캐별 스킬 쿨(파력무참·백호·지폭지술) 표시"),
        ("alert", "스킬 알림", "스킬 임박 카운트다운/이벤트 알림 (맵 아래 중앙)"),
        ("helper", "사냥 도우미", "사용가능 스킬 + 파력무참 지속시간"),
        ("hpmp", "힐러 HP/MP", "힐러별 HP/MP 막대"),
        ("hunt", "사냥 분석", "사냥 시간/획득/바퀴 + 맵 히스토리"),
        ("huntnav", "선비족 네비", "굴 사냥 순서 지도 — 다음 굴 강조"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("오버레이 설정")
        self.resize(300, 320)
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(6)

        self.chk_overlay = QtWidgets.QCheckBox("오버레이 전체 ON")
        self.chk_overlay.setToolTip(
            "게임 화면 위 오버레이 전체 마스터 토글 (msw 위)."
        )
        root.addWidget(self.chk_overlay)

        box = QtWidgets.QGroupBox("표시할 오버레이 (체크된 것만 보임)")
        box_lay = QtWidgets.QVBoxLayout(box)
        box_lay.setSpacing(3)
        self.kind_chks: dict = {}
        for kind, label, tip in self.KINDS:
            c = QtWidgets.QCheckBox(label)
            c.setToolTip(tip)
            c.setChecked(True)
            self.kind_chks[kind] = c
            box_lay.addWidget(c)
        root.addWidget(box)

        self.chk_overlay_edit = QtWidgets.QCheckBox("위치 편집 (드래그로 이동)")
        self.chk_overlay_edit.setToolTip(
            "체크 시 오버레이에 마우스 입력 받아 드래그로 위치 이동 가능. "
            "해제하면 입력 통과(클릭 무시) 모드로 복귀."
        )
        root.addWidget(self.chk_overlay_edit)

        op_row = QtWidgets.QHBoxLayout()
        op_row.addWidget(QtWidgets.QLabel("투명도"))
        self.slider_overlay_opacity = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider_overlay_opacity.setMinimum(10)
        self.slider_overlay_opacity.setMaximum(100)
        self.slider_overlay_opacity.setSingleStep(5)
        self.slider_overlay_opacity.setPageStep(10)
        self.slider_overlay_opacity.setValue(90)
        self.slider_overlay_opacity.setToolTip(
            "오버레이 창 전체 투명도. 값이 낮을수록 투명."
        )
        self.lbl_overlay_opacity = QtWidgets.QLabel("90%")
        self.lbl_overlay_opacity.setFixedWidth(36)
        op_row.addWidget(self.slider_overlay_opacity, 1)
        op_row.addWidget(self.lbl_overlay_opacity)
        root.addLayout(op_row)

        btn_close = QtWidgets.QPushButton("닫기")
        btn_close.clicked.connect(self.hide)
        root.addWidget(btn_close)


class SkillRangeDialog(QtWidgets.QDialog):
    """격수 스킬범위 오버레이 설정 전용 팝업.

    이 다이얼로그는 위젯 생성만 담당. 시그널은 main_window._wire_skill_range
    에서 연결 (SkillDialog 패턴과 동일).

    외부에서 참조할 위젯:
      chk_skill_range, spin_skill_tile_w, spin_skill_tile_h,
      spin_skill_{u|d|l|r}_{x|y}, chk_skill_enabled (dict),
      sld_skill_alpha (dict), lbl_skill_alpha (dict).
    """

    _SKILL_NAMES = ("파천검무", "극백호참", "어검술", "쇄혼비무")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("스킬범위 설정")
        self.resize(520, 440)
        root = QtWidgets.QVBoxLayout(self)

        # 체크박스 + 타일 W/H 행.
        sr_row = QtWidgets.QHBoxLayout()
        sr_row.setSpacing(6)
        self.chk_skill_range = QtWidgets.QCheckBox("스킬범위")
        self.chk_skill_range.setToolTip(
            "격수 스킬 타격 범위를 캐릭터 기준 HUD로 표시.\n"
            "빨탭 위치 고정. 방향(last_dir)에 따라 회전."
        )
        sr_row.addWidget(self.chk_skill_range)
        sr_row.addWidget(QtWidgets.QLabel("타일W"))
        self.spin_skill_tile_w = QtWidgets.QSpinBox()
        self.spin_skill_tile_w.setRange(8, 120)
        self.spin_skill_tile_w.setValue(32)
        self.spin_skill_tile_w.setSuffix("px")
        sr_row.addWidget(self.spin_skill_tile_w)
        sr_row.addWidget(QtWidgets.QLabel("타일H"))
        self.spin_skill_tile_h = QtWidgets.QSpinBox()
        self.spin_skill_tile_h.setRange(8, 120)
        self.spin_skill_tile_h.setValue(32)
        self.spin_skill_tile_h.setSuffix("px")
        sr_row.addWidget(self.spin_skill_tile_h)
        self.spin_skill_tile = self.spin_skill_tile_w  # 하위호환 alias.
        sr_row.addStretch(1)
        sr_wrap = QtWidgets.QWidget()
        sr_wrap.setLayout(sr_row)
        root.addWidget(sr_wrap)

        # 방향별 오프셋 (U/D/L/R).
        def _mk_dir_row(label_text):
            row = QtWidgets.QHBoxLayout()
            row.setContentsMargins(2, 2, 2, 2)
            row.setSpacing(8)
            lbl = QtWidgets.QLabel(label_text)
            lbl.setFixedWidth(66)
            row.addWidget(lbl)
            row.addWidget(QtWidgets.QLabel("X"))
            sx = QtWidgets.QSpinBox()
            sx.setRange(-300, 300); sx.setValue(0); sx.setSuffix("px")
            sx.setFixedWidth(86)
            row.addWidget(sx)
            row.addWidget(QtWidgets.QLabel("Y"))
            sy = QtWidgets.QSpinBox()
            sy.setRange(-300, 300); sy.setValue(0); sy.setSuffix("px")
            sy.setFixedWidth(86)
            row.addWidget(sy)
            row.addStretch(1)
            w = QtWidgets.QWidget(); w.setLayout(row)
            return w, sx, sy

        row_u, self.spin_skill_u_x, self.spin_skill_u_y = _mk_dir_row("상 (U)")
        row_d, self.spin_skill_d_x, self.spin_skill_d_y = _mk_dir_row("하 (D)")
        row_l, self.spin_skill_l_x, self.spin_skill_l_y = _mk_dir_row("좌 (L)")
        row_r, self.spin_skill_r_x, self.spin_skill_r_y = _mk_dir_row("우 (R)")
        root.addWidget(row_u)
        root.addWidget(row_d)
        root.addWidget(row_l)
        root.addWidget(row_r)

        # 스킬별 체크박스 + 색상 박스 + 투명도 슬라이더.
        try:
            from .skill_range_overlay import SKILL_COLOR as _COLOR
        except Exception:
            _COLOR = {}
        self.chk_skill_enabled: dict = {}
        self.sld_skill_alpha: dict = {}
        self.lbl_skill_alpha: dict = {}
        for _sk in self._SKILL_NAMES:
            r, g, b = _COLOR.get(_sk, (200, 200, 200))
            sk_row = QtWidgets.QHBoxLayout()
            sk_row.setContentsMargins(2, 2, 2, 2)
            sk_row.setSpacing(8)
            chk = QtWidgets.QCheckBox()
            chk.setChecked(True)
            chk.setToolTip(f"{_sk} 범위 표시 여부")
            sk_row.addWidget(chk)
            col_box = QtWidgets.QLabel()
            col_box.setFixedSize(18, 18)
            col_box.setStyleSheet(
                f"background:rgb({r},{g},{b});"
                f"border:1px solid #2a2e36;border-radius:3px;"
            )
            sk_row.addWidget(col_box)
            name_lbl = QtWidgets.QLabel(_sk)
            name_lbl.setFixedWidth(92)
            sk_row.addWidget(name_lbl)
            sld = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            sld.setRange(0, 100)
            sld.setValue(80)
            sld.setSingleStep(5)
            sld.setFixedWidth(150)
            sk_row.addWidget(sld)
            pct_lbl = QtWidgets.QLabel("80%")
            pct_lbl.setFixedWidth(50)
            sk_row.addWidget(pct_lbl)
            sk_row.addStretch(1)
            sk_wrap = QtWidgets.QWidget()
            sk_wrap.setLayout(sk_row)
            root.addWidget(sk_wrap)
            self.chk_skill_enabled[_sk] = chk
            self.sld_skill_alpha[_sk] = sld
            self.lbl_skill_alpha[_sk] = pct_lbl

        root.addStretch(1)
        self.btn_close = QtWidgets.QPushButton("닫기")
        self.btn_close.clicked.connect(self.hide)
        root.addWidget(self.btn_close)


class ParamDialog(QtWidgets.QDialog):
    """도사 파라미터 전용 팝업 (YOLO conf/min/tol/yn)."""
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.setWindowTitle("도사 파라미터")
        self.resize(300, 260)
        lay = QtWidgets.QGridLayout(self)

        self.conf_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.conf_slider.setRange(1, 90)
        self.conf_slider.setValue(int(cfg.vision.conf * 100))
        self.conf_label = QtWidgets.QLabel(f"YOLO conf: {cfg.vision.conf:.2f}")
        lay.addWidget(self.conf_label, 0, 0, 1, 2)
        lay.addWidget(self.conf_slider, 1, 0, 1, 2)

        self.minw_spin = QtWidgets.QSpinBox()
        self.minw_spin.setRange(0, 200); self.minw_spin.setValue(25)
        self.minh_spin = QtWidgets.QSpinBox()
        self.minh_spin.setRange(0, 200); self.minh_spin.setValue(40)
        lay.addWidget(QtWidgets.QLabel("빨탭 min_w"), 2, 0)
        lay.addWidget(self.minw_spin, 2, 1)
        lay.addWidget(QtWidgets.QLabel("빨탭 min_h"), 3, 0)
        lay.addWidget(self.minh_spin, 3, 1)

        self.tol_spin = QtWidgets.QSpinBox()
        self.tol_spin.setRange(0, 10); self.tol_spin.setValue(1)
        lay.addWidget(QtWidgets.QLabel("좌표 tol (월드 칸)"), 4, 0)
        lay.addWidget(self.tol_spin, 4, 1)

        self.yn_spin = QtWidgets.QSpinBox()
        self.yn_spin.setRange(1, 10); self.yn_spin.setValue(1)
        lay.addWidget(QtWidgets.QLabel("YOLO 주기 (매 N프레임)"), 5, 0)
        lay.addWidget(self.yn_spin, 5, 1)

        self.btn_close = QtWidgets.QPushButton("닫기")
        self.btn_close.clicked.connect(self.hide)
        lay.addWidget(self.btn_close, 6, 0, 1, 2)


class NetworkDialog(QtWidgets.QDialog):
    """격수 네트워크 팝업. 힐러 PC 목록을 [닉네임 | IP] 행으로 관리.

    행 추가/삭제 가능. 기존 코드 호환을 위해 hidden ``peers_edit``(콤마
    구분 IP 문자열)을 함께 유지하며 모든 편집에서 자동 동기화한다.
    """

    peers_changed = QtCore.pyqtSignal()

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.setWindowTitle("격수 네트워크")
        self.resize(420, 320)
        root = QtWidgets.QVBoxLayout(self)

        # 상단: 포트 / Hz.
        top = QtWidgets.QGridLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self.port_spin = QtWidgets.QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(int(cfg.net.port))
        self.rate_spin = QtWidgets.QSpinBox()
        self.rate_spin.setRange(1, 60)
        self.rate_spin.setValue(int(cfg.net.send_rate_hz))
        top.addWidget(QtWidgets.QLabel("포트"), 0, 0)
        top.addWidget(self.port_spin, 0, 1)
        top.addWidget(QtWidgets.QLabel("송신 Hz"), 0, 2)
        top.addWidget(self.rate_spin, 0, 3)
        root.addLayout(top)

        # 중단: 힐러 목록.
        root.addWidget(QtWidgets.QLabel("힐러 목록 (닉네임 | IP)"))
        rows_container = QtWidgets.QWidget()
        self._rows_box = QtWidgets.QVBoxLayout(rows_container)
        self._rows_box.setContentsMargins(0, 0, 0, 0)
        self._rows_box.setSpacing(2)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(rows_container)
        root.addWidget(scroll, 1)

        self._rows: list = []  # (nick_edit, ip_edit, container, del_btn)

        # 초기 행 — cfg.net.peers(list[str]) + cfg.net.nicks(optional list).
        init_ips = list(getattr(cfg.net, "peers", []) or [])
        init_nicks = list(getattr(cfg.net, "nicks", []) or [])
        if not init_ips:
            self.add_row("", "")
        else:
            for i, ip in enumerate(init_ips):
                nk = init_nicks[i] if i < len(init_nicks) else ""
                self.add_row(nk, ip)

        # 하단: 추가 / 닫기.
        btn_row = QtWidgets.QHBoxLayout()
        btn_add = QtWidgets.QPushButton("+ 행 추가")
        btn_add.clicked.connect(lambda: self.add_row("", ""))
        btn_row.addWidget(btn_add)
        btn_row.addStretch(1)
        self.btn_close = QtWidgets.QPushButton("닫기")
        self.btn_close.clicked.connect(self.hide)
        btn_row.addWidget(self.btn_close)
        root.addLayout(btn_row)

        # 기존 peers_edit alias (hidden, 자동 동기화).
        self.peers_edit = QtWidgets.QLineEdit()
        self.peers_edit.hide()
        self._sync_peers_edit()

    def add_row(self, nick: str = "", ip: str = "") -> None:
        cont = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(cont)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        nick_edit = QtWidgets.QLineEdit(str(nick or ""))
        nick_edit.setPlaceholderText("닉네임(선택)")
        nick_edit.setFixedWidth(110)
        ip_edit = QtWidgets.QLineEdit(str(ip or ""))
        ip_edit.setPlaceholderText("예: 100.75.199.37")
        btn_del = QtWidgets.QPushButton("✕")
        btn_del.setFixedWidth(28)
        row.addWidget(nick_edit)
        row.addWidget(ip_edit, 1)
        row.addWidget(btn_del)
        self._rows_box.addWidget(cont)
        entry = [nick_edit, ip_edit, cont, btn_del]
        self._rows.append(entry)

        def _rm():
            try:
                self._rows_box.removeWidget(cont)
                cont.deleteLater()
                if entry in self._rows:
                    self._rows.remove(entry)
            except Exception:
                pass
            self._sync_peers_edit()
            self.peers_changed.emit()

        btn_del.clicked.connect(_rm)
        for e in (nick_edit, ip_edit):
            e.editingFinished.connect(self._on_any_edit)

    def _on_any_edit(self):
        self._sync_peers_edit()
        self.peers_changed.emit()

    def get_peers(self) -> list:
        out = []
        for nick_edit, ip_edit, *_ in self._rows:
            v = ip_edit.text().strip()
            if v:
                out.append(v)
        return out

    def get_nicks(self) -> list:
        return [n.text().strip() for n, _ip, *_ in self._rows]

    def set_peers_string(self, s) -> None:
        """역호환: 콤마 구분 문자열 또는 리스트 → 행 재구성."""
        if isinstance(s, list):
            ips = [str(x).strip() for x in s if str(x).strip()]
        else:
            ips = [x.strip() for x in str(s or "").split(",") if x.strip()]
        while self._rows:
            entry = self._rows.pop()
            try:
                cont = entry[2]
                self._rows_box.removeWidget(cont)
                cont.deleteLater()
            except Exception:
                pass
        if not ips:
            self.add_row("", "")
        else:
            for ip in ips:
                self.add_row("", ip)
        self._sync_peers_edit()

    def set_rows(self, nicks: list, ips: list) -> None:
        while self._rows:
            entry = self._rows.pop()
            try:
                cont = entry[2]
                self._rows_box.removeWidget(cont)
                cont.deleteLater()
            except Exception:
                pass
        n = max(len(ips or []), 1)
        for i in range(n):
            nk = nicks[i] if nicks and i < len(nicks) else ""
            ip = ips[i] if ips and i < len(ips) else ""
            self.add_row(nk, ip)
        self._sync_peers_edit()

    def _sync_peers_edit(self):
        self.peers_edit.setText(",".join(self.get_peers()))


