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

import json
import sys
import threading
from datetime import datetime

from ..config import load as load_cfg
from .overlay import GameOverlay, SkillAlertOverlay
from .region_picker import RegionPicker
from .region_overlay import RegionOverlay
from .status_strip import StatusStrip
from .dialogs import SkillDialog, ParamDialog, NetworkDialog
# 2026-06-07: v2 facade redirect 제거 — 순수 v1 워커 직접 사용 (사용자 요청).
# v1 healer_worker / attacker_worker 가 실제 동작. src_v2 미경유.
from ..workers.healer_worker import HealerWorker
from ..workers.attacker_worker import AttackerWorker
from ..workers.heartbeat import HealerHeartbeat, AttackerHeartbeat
from ..workers.control_listener import ControlListener


class MainWindow(QtWidgets.QMainWindow):
    # 전역 단축키(F11/F12) 콜백이 non-GUI 스레드에서 도착하므로 signal 로
    # UI 스레드에 queue. 인자: "block_a" | "block_b".
    hotkey_fired = QtCore.pyqtSignal(str)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.worker = None           # HealerWorker or AttackerWorker
        self.role = "healer"         # "healer" | "attacker"
        self.setWindowTitle("옛바 컨트롤")
        # 팝업 다이얼로그들 (메인에서 버튼으로 오픈).
        self.skill_dlg = SkillDialog(self)
        self.param_dlg = ParamDialog(cfg, self)
        self.net_dlg = NetworkDialog(cfg, self)
        self._region_picker: RegionPicker = None
        # 격수 PC 전용 인게임 오버레이 (힐러 쿨다운 띄우기).
        self._overlay: GameOverlay = None
        # 스킬 임박 알림 오버레이 (맵 아래 중앙, 3초간 표시 후 자동 소멸).
        self._alert_overlay: Optional[SkillAlertOverlay] = None
        # 격수 전용 사냥 도우미 오버레이 (사용가능스킬 + 파력무참 지속시간).
        # 격수 모드일 때만 show. 오버레이 토글과 역할 토글 양쪽에 연동.
        self._helper_overlay = None  # type: Optional["HunterHelperOverlay"]
        # 격수 전용 힐러 HP/MP 오버레이 (각 힐러 퍼센트 + 색깔막대).
        # 데이터 출처: 힐러 CooldownReport.hp_pct/mp_pct. 오버레이 토글 공통.
        self._hpmp_overlay = None  # type: Optional["HealerStatusOverlay"]
        # 격수 스킬 범위 오버레이. 체크박스(chk_skill_range) 로 개별 토글.
        self._skill_range_overlay = None  # type: Optional["SkillRangeOverlay"]
        # 알림 edge 트리거 상태 (힐러별 직전 remaining 초).
        self._alert_prev: dict[int, dict] = {}
        # 힐러별 마지막 event_seq. 새 이벤트 수신 감지용.
        self._healer_last_event_seq: dict[int, int] = {}
        # 공증 임박 edge 트리거 상태 (힐러별). True = 현재 임박 구간 안.
        # mp_pct 가 (thr+10) 이하로 cross-down 순간 1회 알림.
        self._gyoungryeok_imminent_prev: dict[int, bool] = {}
        # 오버레이 수동 위치 (설정 영속화). {"cd","alert","helper","hpmp"}: (x,y).
        self._overlay_positions: dict[str, tuple] = {}
        # 6개 추가 영역 (game/map/coord/xp/hp/mp) — cd/nick은 cfg.cooldown에 저장.
        self._regions: dict[str, tuple] = {}
        self._region_buttons: dict = {}
        self._region_labels_kr: dict = {
            "game": "게임", "map": "맵", "coord": "좌표",
            "xp": "경험치", "hp": "체력", "mp": "마력",
        }
        self._region_overlay: Optional[RegionOverlay] = None
        self._status_strip: Optional[StatusStrip] = None
        # 격수 모드 힐러별 쿨다운 최신값 캐시.
        self._healer_cooldowns: dict[int, dict] = {}
        # 힐러 모드 healer_idx (GUI 설정 → cfg.net.healer_idx).
        # 기본 0. _load_settings에서 덮어씀.
        # alias (핸들러/_start_*가 직접 참조).
        self.skill_chks = self.skill_dlg.skill_chks
        self.skill_spins = self.skill_dlg.skill_spins
        self.parlyuk_spin = self.skill_dlg.parlyuk_spin
        self.parlyuk_maps_edit = self.skill_dlg.parlyuk_maps_edit
        self.conf_slider = self.param_dlg.conf_slider
        self.conf_label = self.param_dlg.conf_label
        self.minw_spin = self.param_dlg.minw_spin
        self.minh_spin = self.param_dlg.minh_spin
        self.tol_spin = self.param_dlg.tol_spin
        self.yn_spin = self.param_dlg.yn_spin
        self.peers_edit = self.net_dlg.peers_edit
        self.port_spin = self.net_dlg.port_spin
        self.rate_spin = self.net_dlg.rate_spin
        self._settings_path = Path.home() / ".oldbaram_gui.json"
        # 상시 원격 제어 리스너 (워커가 꺼져 있어도 start 명령 수신).
        self._ctrl_listener: ControlListener = None
        # 힐러 → 격수 heartbeat. GUI 기동 즉시 가동 → 격수 UI 초록불.
        self._heartbeat: Optional[HealerHeartbeat] = None
        # 격수 상시 heartbeat (워커 전에도 ping + CooldownReceiver).
        self._attacker_hb: Optional[AttackerHeartbeat] = None
        self._build_ui()
        self._wire_dialogs()
        self._load_settings()
        self._apply_role_ui()
        # 연결 상태 표시용 1Hz 타이머 (격수 모드에서 힐러 행 색 갱신).
        self._conn_timer = QtCore.QTimer(self)
        self._conn_timer.setInterval(1000)
        self._conn_timer.timeout.connect(self._tick_connection_state)
        self._conn_timer.start()
        # 사냥 분석 polling (격수 AttackerWorker 전용). 1Hz.
        self._analytics_timer = QtCore.QTimer(self)
        self._analytics_timer.setInterval(1000)
        self._analytics_timer.timeout.connect(self._tick_analytics)
        self._last_hunt_session_id: str = ""
        self._hunt_report_dlg = None
        # 힐러 FPS 실시간 표시 (HealerWorker.last_fps 폴링). 500ms.
        # frame_ready emit 스킵(저사양)과 무관하게 매 루프 갱신되는 값 사용.
        self._fps_timer = QtCore.QTimer(self)
        self._fps_timer.setInterval(500)
        self._fps_timer.timeout.connect(self._tick_fps)
        # msw 창 이동 추적 — 창이 옮겨지면 저장된 영역(게임/맵/좌표/경험치/hp/mp
        # + cfg.cooldown의 cd/nick)을 델타만큼 이동해 게임 내 좌표와 맞춤.
        # 첫 tick은 baseline 기록만, 이후 변동 감지 시에만 shift.
        self._msw_last_client_origin: Optional[Tuple[int, int]] = None
        self._msw_tracker_timer = QtCore.QTimer(self)
        self._msw_tracker_timer.setInterval(500)
        self._msw_tracker_timer.timeout.connect(self._tick_msw_tracker)
        self._msw_tracker_timer.start()
        # Overlay visibility 정책: StaysOnTop 는 항상 켜두되, "실제 노출"은
        # 포그라운드 앱이 msw.exe 이거나 내 UI 프로세스일 때만 허용.
        # → 다른 앱(Chrome, Discord 등)으로 포커스 옮기면 overlay 숨고,
        #   msw 최소화되면 숨고, msw/본UI 로 돌아오면 다시 표시.
        self._overlay_vis_timer = QtCore.QTimer(self)
        self._overlay_vis_timer.setInterval(300)
        self._overlay_vis_timer.timeout.connect(self._tick_overlay_visibility)
        self._overlay_vis_timer.start()
        # GUI 기동 직후: 도사 모드면 상시 listener 시작 + 워커 자동 시작.
        QtCore.QTimer.singleShot(0, self._auto_startup)
        # GUI 창을 msw.exe 오른쪽에 딱 붙여서 배치 (프레임/제목표시줄 보정).
        # show() 이후 프레임 마진이 WM 으로부터 확정된 뒤 1회만 실행.
        QtCore.QTimer.singleShot(80, self._snap_to_msw_right)
        # F11/F12 전역 단축키 (블록 A/B) — msw.exe 포그라운드에서도 동작.
        # hotkey 스레드 → Qt signal 로 브리지 (UI 스레드에서 실행해야 안전).
        self._hotkey_block_a_signal = getattr(
            self, "_hotkey_block_a_signal", None
        )
        try:
            self._setup_global_hotkeys()
        except Exception as _e:
            try:
                self._append_log(f"[HOTKEY] 초기화 실패: {_e}")
            except Exception:
                pass

    def moveEvent(self, event):
        """제목 표시줄(프레임 top)이 화면 위로 넘어가지 않게 clamp.
        이동 중 y<0 되면 창을 못 잡아 드래그 복귀 불가 → 즉시 보정.
        """
        frame = self.frameGeometry()
        if frame.top() < 0:
            dy = -frame.top()
            cur = self.pos()
            # move() 호출 시 moveEvent 재발생 가능하나, 보정 후엔 top>=0이라
            # 분기에 재진입하지 않음 → 무한루프 없음.
            self.move(cur.x(), cur.y() + dy)
        super().moveEvent(event)

    def _wire_dialogs(self):
        """팝업 위젯 signal → 메인 핸들러 연결."""
        # 스킬: 기원 라디오 + 혼마술 체크 + 각 스피너
        self.skill_dlg.rb_bonghwang.toggled.connect(self._on_cycle_changed)
        self.skill_dlg.rb_shinryoung.toggled.connect(self._on_cycle_changed)
        self.skill_dlg.chk_honmasul.stateChanged.connect(self._on_cycle_changed)
        self.skill_dlg.spin_bonghwang.valueChanged.connect(self._on_cycle_changed)
        self.skill_dlg.spin_shinryoung.valueChanged.connect(self._on_cycle_changed)
        self.skill_dlg.spin_honmasul.valueChanged.connect(self._on_cycle_changed)
        for name, c in self.skill_chks.items():
            c.stateChanged.connect(
                lambda _st, n=name: self._on_skill_toggle(n)
            )
        for name, sp in self.skill_spins.items():
            sp.valueChanged.connect(
                lambda v, n=name: self._on_skill_vk(n, v)
            )
        self.parlyuk_spin.valueChanged.connect(self._on_parlyuk_offset)
        self.parlyuk_maps_edit.textChanged.connect(self._on_parlyuk_maps)
        self.skill_dlg.btn_nl_off.clicked.connect(self._on_numlock_off)
        # 메인힐 NumPad 번호 변경 → 워커 skill_vks["메인힐"] 갱신 + 싸이클 재계산.
        try:
            self.skill_dlg.spin_mainheal.valueChanged.connect(
                self._on_mainheal_vk_changed
            )
        except Exception:
            pass
        # 임계치 (공력증강 MP%) 변경 → 워커 instance var 업데이트.
        try:
            self.skill_dlg.gyoungryeok_mp_spin.valueChanged.connect(
                self._on_gyoungryeok_thr_changed
            )
        except Exception:
            pass
        # 파라미터 팝업
        self.conf_slider.valueChanged.connect(self._on_conf)
        self.minw_spin.valueChanged.connect(self._on_minw)
        self.minh_spin.valueChanged.connect(self._on_minh)
        self.tol_spin.valueChanged.connect(self._on_tol)
        self.yn_spin.valueChanged.connect(self._on_yn)
        # peers 편집 완료 시 격수 모드면 힐러 행 재구성.
        self.peers_edit.editingFinished.connect(self._on_peers_edited)

    def _on_peers_edited(self) -> None:
        if self.role != "attacker":
            return
        try:
            self._refresh_healer_rows()
        except Exception:
            pass
        # 격수 heartbeat도 새 peers로 재기동.
        try:
            if self._attacker_hb is not None:
                self._stop_attacker_heartbeat()
                self._start_attacker_heartbeat()
        except Exception:
            pass

    def _current_cycle_vks(self) -> list:
        """NumLock 싸이클 VK 리스트.

        구성:
        - 기원 택1 (봉황 or 신령)
        - 혼마술 체크시 추가
        - 파력무참/백호의희원/백호의희원첨 제외 조건부 스킬 체크시 추가
        중복 VK는 제거.

        파력무참/백호의희원/백호의희원첨은 SkillScheduler v4 (burst + OCR 검증)
        로 처리. 넘락 토글 대상에서 제외.
        """
        vks = []
        if self.skill_dlg.rb_bonghwang.isChecked():
            vks.append(self._numpad_vk(self.skill_dlg.spin_bonghwang.value()))
        elif self.skill_dlg.rb_shinryoung.isChecked():
            vks.append(self._numpad_vk(self.skill_dlg.spin_shinryoung.value()))
        if self.skill_dlg.chk_honmasul.isChecked():
            vks.append(self._numpad_vk(self.skill_dlg.spin_honmasul.value()))
        # 2026-04-20: 공력증강/부활 추가 — 모두 조건부 스케줄러 시전.
        _SCHEDULED = {
            "파력무참", "백호의희원", "백호의희원첨",
            "공력증강", "부활",
        }
        for name, chk in self.skill_chks.items():
            if name in _SCHEDULED:
                continue  # SkillScheduler 가 burst 시전.
            if chk.isChecked():
                vks.append(self._numpad_vk(self.skill_spins[name].value()))
        # 중복 제거 (같은 NumPad 번호 지정 시 한 번만).
        seen = set()
        uniq = []
        for v in vks:
            if v not in seen:
                seen.add(v); uniq.append(v)
        return uniq

    def _on_cycle_changed(self, *_):
        vks = self._current_cycle_vks()
        if self.worker and hasattr(self.worker, "set_cycle_vks"):
            self.worker.set_cycle_vks(vks)
        self._append_log(f"[SLOT] cycle VKs = {[hex(v) for v in vks]}")

    def _build_ui(self):
        """메인 창: QTabWidget 2탭 (실행 / 설정).

        실행 탭: 프리뷰 + 상태 스트립 + 역할 + 시작·정지·ARM·따라가기 +
                 (격수) 힐러 제어 패널.
        설정 탭: 영역 지정(공통 게임/맵/좌표/경험치 + 도사 전용 체력/마력/
                 쿨/닉/idx) + 영역표시/저사양 체크박스 + 스킬·파라미터·네트워크.
        """
        # 창 크기. 탭 도입으로 세로 축소 가능.
        self.setFixedSize(480, 820)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(4)

        self.tabs = QtWidgets.QTabWidget()
        outer.addWidget(self.tabs, 1)

        # ================== 실행 탭 ==================
        run_tab = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(run_tab)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # 프리뷰.
        self.preview = QtWidgets.QLabel("프리뷰 대기")
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        self.preview.setStyleSheet(
            "background:#0f172a;color:#94a3b8;border:1px solid #e4e7ec;"
            "border-radius:10px;"
        )
        self.preview.setFixedSize(456, 285)
        self.preview.setSizePolicy(QtWidgets.QSizePolicy.Fixed,
                                   QtWidgets.QSizePolicy.Fixed)
        root.addWidget(self.preview, 0, QtCore.Qt.AlignHCenter)

        # 상태 스트립.
        self._status_strip = StatusStrip(self)
        root.addWidget(self._status_strip)

        # 역할 라디오.
        role_row = QtWidgets.QHBoxLayout()
        self.rb_healer = QtWidgets.QRadioButton("도사")
        self.rb_attacker = QtWidgets.QRadioButton("격수")
        self.rb_healer.setChecked(True)
        self.rb_healer.toggled.connect(self._on_role_change)
        role_row.addWidget(QtWidgets.QLabel("역할:"))
        role_row.addWidget(self.rb_healer)
        role_row.addWidget(self.rb_attacker)
        # 격수 서브클래스 (도적/전사) — 격수 라디오 옆에.
        self.subclass_container = QtWidgets.QWidget()
        sc_lay = QtWidgets.QHBoxLayout(self.subclass_container)
        sc_lay.setContentsMargins(8, 0, 0, 0)
        sc_lay.setSpacing(6)
        sc_lay.addWidget(QtWidgets.QLabel("|"))
        self.rb_thief = QtWidgets.QRadioButton("도적")
        self.rb_warrior = QtWidgets.QRadioButton("전사")
        self.rb_thief.setChecked(True)
        self._subclass_group = QtWidgets.QButtonGroup(self)
        self._subclass_group.addButton(self.rb_thief)
        self._subclass_group.addButton(self.rb_warrior)
        self.rb_thief.toggled.connect(self._on_subclass_change)
        sc_lay.addWidget(self.rb_thief)
        sc_lay.addWidget(self.rb_warrior)
        # 승급 (2차/3차/4차) — 격수 라디오 옆에. 상위 승급은 하위 스킬 포함.
        sc_lay.addWidget(QtWidgets.QLabel("|"))
        self.rb_rank2 = QtWidgets.QRadioButton("2차")
        self.rb_rank3 = QtWidgets.QRadioButton("3차")
        self.rb_rank4 = QtWidgets.QRadioButton("4차")
        self.rb_rank4.setChecked(True)
        self._rank_group = QtWidgets.QButtonGroup(self)
        self._rank_group.addButton(self.rb_rank2)
        self._rank_group.addButton(self.rb_rank3)
        self._rank_group.addButton(self.rb_rank4)
        self.rb_rank2.toggled.connect(self._on_rank_change)
        self.rb_rank3.toggled.connect(self._on_rank_change)
        sc_lay.addWidget(self.rb_rank2)
        sc_lay.addWidget(self.rb_rank3)
        sc_lay.addWidget(self.rb_rank4)
        role_row.addWidget(self.subclass_container)
        role_row.addStretch(1)
        root.addLayout(role_row)
        self.subclass_container.setVisible(False)
        self.attacker_subclass = "thief"
        self.attacker_rank = 4

        # 시작/정지 + ARM + 일시정지 + 따라가기.
        run_row = QtWidgets.QHBoxLayout()
        run_row.setSpacing(4)
        self.btn_start = QtWidgets.QPushButton("▶  시작")
        self.btn_start.setObjectName("primaryBtn")
        self.btn_stop = QtWidgets.QPushButton("■  정지")
        self.btn_stop.setObjectName("dangerBtn")
        self.btn_stop.setEnabled(False)
        self.btn_start.clicked.connect(self.start_worker)
        self.btn_stop.clicked.connect(self.stop_worker)
        for b in (self.btn_start, self.btn_stop):
            b.setMinimumWidth(64)
            b.setMinimumHeight(22)
        # ARM 체크박스 — 내부 worker.armed 플래그 관리용. UI 에선 숨기고
        # "시작=ARM ON / 일시정지=ARM 토글" 로 자연스럽게 동작.
        self.chk_arm = QtWidgets.QCheckBox("ARM")
        self.chk_arm.setToolTip("키 주입 안전장치 (내부 토글)")
        self.chk_arm.setChecked(True)
        self.chk_arm.stateChanged.connect(self._on_arm)
        self.chk_arm.setVisible(False)
        self.btn_pause = QtWidgets.QPushButton("‖ 일시정지")
        self.btn_pause.setToolTip("armed 토글. ARM 체크와 동일 플래그.")
        self.btn_pause.clicked.connect(self._on_pause_toggle)
        self.btn_pause.setMinimumWidth(58)
        self.chk_follow_only = QtWidgets.QCheckBox("따라가기")
        self.chk_follow_only.setToolTip(
            "스킬(주력힐·파력무참) OFF, 빨탭 무시, TAB-CONFIRM OFF. "
            "격수 좌표만 추종."
        )
        self.chk_follow_only.stateChanged.connect(self._on_follow_only)
        run_row.addWidget(self.btn_start)
        run_row.addWidget(self.btn_stop)
        # 실행 상태 pill — 시작/정지 누른 결과가 즉시 보이도록.
        self.lbl_run_status = QtWidgets.QLabel("정지됨")
        self.lbl_run_status.setObjectName("pillIdle")
        self.lbl_run_status.setAlignment(QtCore.Qt.AlignCenter)
        run_row.addWidget(self.lbl_run_status)
        # chk_arm 은 run_row 에 넣지 않음 (hidden 내부 상태).
        run_row.addWidget(self.btn_pause)
        run_row.addWidget(self.chk_follow_only)
        run_row.addStretch(1)
        root.addLayout(run_row)

        # 클라우드 패널 (설정 sync + 자동 업데이트). 미설정이면 조용히 비활성.
        try:
            from . import cloud_panel
            cloud_panel.attach(self, root)
        except Exception:
            pass

        # 격수 전용 힐러 제어 패널.
        self.attacker_panel = QtWidgets.QGroupBox("힐러 제어")
        self.attacker_panel_layout = QtWidgets.QVBoxLayout(self.attacker_panel)
        self.attacker_panel_layout.setContentsMargins(4, 4, 4, 4)
        self.attacker_panel_layout.setSpacing(2)
        self._healer_rows = []
        # 일괄 제어 행 (전체 ▶ ‖ ■ 따O 따X).
        all_row = QtWidgets.QHBoxLayout()
        all_row.setSpacing(3)
        btn_all_start = QtWidgets.QPushButton("전체 ▶")
        btn_all_pause = QtWidgets.QPushButton("전체 ‖")
        btn_all_stop = QtWidgets.QPushButton("전체 ■")
        btn_all_fol_on = QtWidgets.QPushButton("전체 따O")
        btn_all_fol_off = QtWidgets.QPushButton("전체 따X")
        btn_all_fol_on.setToolTip("전체 따라가기 전용 ON (주력힐/파력무참 OFF)")
        btn_all_fol_off.setToolTip("전체 따라가기 전용 OFF (전투 복귀)")
        btn_all_start.clicked.connect(lambda: self._send_ctrl(-1, "start"))
        btn_all_pause.clicked.connect(lambda: self._send_ctrl(-1, "pause"))
        btn_all_stop.clicked.connect(lambda: self._send_ctrl(-1, "stop"))
        btn_all_fol_on.clicked.connect(lambda: self._send_ctrl(-1, "follow_on"))
        btn_all_fol_off.clicked.connect(lambda: self._send_ctrl(-1, "follow_off"))
        for b in (btn_all_start, btn_all_pause, btn_all_stop,
                  btn_all_fol_on, btn_all_fol_off):
            b.setMinimumWidth(52)
            b.setMaximumWidth(90)
        all_row.addWidget(btn_all_start)
        all_row.addWidget(btn_all_pause)
        all_row.addWidget(btn_all_stop)
        all_row.addWidget(btn_all_fol_on)
        all_row.addWidget(btn_all_fol_off)
        all_container = QtWidgets.QWidget()
        all_container.setLayout(all_row)
        self.attacker_panel_layout.addWidget(all_container)
        # 오버레이 토글 행 (별도 라인).
        ov_row = QtWidgets.QHBoxLayout()
        ov_row.setSpacing(6)
        self.chk_overlay = QtWidgets.QCheckBox("오버레이")
        self.chk_overlay.setToolTip(
            "게임 화면 위에 힐러 쿨다운 + 스킬 임박 알림 띄우기 (msw 위)."
        )
        self.chk_overlay.stateChanged.connect(self._on_toggle_overlay)
        self.chk_overlay_edit = QtWidgets.QCheckBox("위치 편집")
        self.chk_overlay_edit.setToolTip(
            "체크 시 오버레이에 마우스 입력 받아 드래그로 위치 이동 가능. "
            "해제하면 입력 통과(클릭 무시) 모드로 복귀."
        )
        self.chk_overlay_edit.stateChanged.connect(
            self._on_toggle_overlay_edit
        )
        ov_row.addWidget(self.chk_overlay)
        ov_row.addWidget(self.chk_overlay_edit)
        ov_row.addSpacing(12)
        # 투명도 슬라이더: 10~100% (0.1~1.0). 기본 90%.
        ov_row.addWidget(QtWidgets.QLabel("투명도"))
        self.slider_overlay_opacity = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider_overlay_opacity.setMinimum(10)
        self.slider_overlay_opacity.setMaximum(100)
        self.slider_overlay_opacity.setSingleStep(5)
        self.slider_overlay_opacity.setPageStep(10)
        self.slider_overlay_opacity.setValue(90)
        self.slider_overlay_opacity.setFixedWidth(120)
        self.slider_overlay_opacity.setToolTip(
            "오버레이 창 전체 투명도 (30% ~ 100%). 값이 낮을수록 투명."
        )
        self.lbl_overlay_opacity = QtWidgets.QLabel("90%")
        self.lbl_overlay_opacity.setFixedWidth(36)
        self.slider_overlay_opacity.valueChanged.connect(
            self._on_overlay_opacity_changed
        )
        ov_row.addWidget(self.slider_overlay_opacity)
        ov_row.addWidget(self.lbl_overlay_opacity)
        ov_row.addStretch(1)
        ov_container = QtWidgets.QWidget()
        ov_container.setLayout(ov_row)
        self.attacker_panel_layout.addWidget(ov_container)

        # 스킬범위 설정은 별도 다이얼로그(SkillRangeDialog)로 분리 (2026-04-22).
        # 격수 패널에는 진입 버튼만 남김. 내부 위젯(chk_skill_range, spin_*,
        # chk_skill_enabled 등) 은 self.skill_range_dlg 에서 참조 공유.
        from .dialogs import SkillRangeDialog
        self.skill_range_dlg = SkillRangeDialog(self)
        self.chk_skill_range = self.skill_range_dlg.chk_skill_range
        self.spin_skill_tile_w = self.skill_range_dlg.spin_skill_tile_w
        self.spin_skill_tile_h = self.skill_range_dlg.spin_skill_tile_h
        self.spin_skill_tile = self.spin_skill_tile_w  # 하위호환 alias.
        self.spin_skill_u_x = self.skill_range_dlg.spin_skill_u_x
        self.spin_skill_u_y = self.skill_range_dlg.spin_skill_u_y
        self.spin_skill_d_x = self.skill_range_dlg.spin_skill_d_x
        self.spin_skill_d_y = self.skill_range_dlg.spin_skill_d_y
        self.spin_skill_l_x = self.skill_range_dlg.spin_skill_l_x
        self.spin_skill_l_y = self.skill_range_dlg.spin_skill_l_y
        self.spin_skill_r_x = self.skill_range_dlg.spin_skill_r_x
        self.spin_skill_r_y = self.skill_range_dlg.spin_skill_r_y
        self.chk_skill_enabled = self.skill_range_dlg.chk_skill_enabled
        self.sld_skill_alpha = self.skill_range_dlg.sld_skill_alpha
        self.lbl_skill_alpha = self.skill_range_dlg.lbl_skill_alpha
        # 시그널 연결 (main_window 핸들러 재사용).
        self.chk_skill_range.stateChanged.connect(self._on_toggle_skill_range)
        self.spin_skill_tile_w.valueChanged.connect(
            self._on_skill_tile_w_changed
        )
        self.spin_skill_tile_h.valueChanged.connect(
            self._on_skill_tile_h_changed
        )
        for _dk, _sx, _sy in (
            ("u", self.spin_skill_u_x, self.spin_skill_u_y),
            ("d", self.spin_skill_d_x, self.spin_skill_d_y),
            ("l", self.spin_skill_l_x, self.spin_skill_l_y),
            ("r", self.spin_skill_r_x, self.spin_skill_r_y),
        ):
            _sx.valueChanged.connect(
                lambda v, k=f"{_dk}_x": self._on_skill_offset_changed(k, v)
            )
            _sy.valueChanged.connect(
                lambda v, k=f"{_dk}_y": self._on_skill_offset_changed(k, v)
            )
        for _nm, _chk in self.chk_skill_enabled.items():
            _chk.stateChanged.connect(
                lambda st, n=_nm: self._on_skill_enabled_changed(
                    n, st == QtCore.Qt.Checked
                )
            )
        for _nm, _sld in self.sld_skill_alpha.items():
            _sld.valueChanged.connect(
                lambda v, n=_nm: self._on_skill_alpha_changed(n, int(v))
            )
        # 격수 패널에 스킬범위 설정 진입 버튼 1줄 (오버레이 토글 행 아래).
        btn_sr_row = QtWidgets.QHBoxLayout()
        btn_sr_row.setContentsMargins(0, 0, 0, 0)
        self.btn_open_skill_range = QtWidgets.QPushButton("스킬범위 설정…")
        self.btn_open_skill_range.setToolTip(
            "격수 스킬 타격범위 오버레이 설정 (체크박스·타일·방향 오프셋·투명도)."
        )
        self.btn_open_skill_range.clicked.connect(
            lambda: (self.skill_range_dlg.show(),
                     self.skill_range_dlg.raise_(),
                     self.skill_range_dlg.activateWindow())
        )
        btn_sr_row.addWidget(self.btn_open_skill_range)
        btn_sr_row.addStretch(1)
        btn_sr_wrap = QtWidgets.QWidget()
        btn_sr_wrap.setLayout(btn_sr_row)
        self.attacker_panel_layout.addWidget(btn_sr_wrap)

        # 격수 자체 상태 블록 (맵/좌표) — 힐러 행 위에 배치.
        self._self_status_box = QtWidgets.QGroupBox("격수 상태")
        _ss_lay = QtWidgets.QVBoxLayout(self._self_status_box)
        _ss_lay.setContentsMargins(8, 12, 8, 8)
        _ss_lay.setSpacing(2)
        self.lbl_self_map = QtWidgets.QLabel("맵: -")
        self.lbl_self_coord = QtWidgets.QLabel("좌표: -")
        for _lb in (self.lbl_self_map, self.lbl_self_coord):
            _lb.setStyleSheet("font-size: 10pt;")
        _ss_lay.addWidget(self.lbl_self_map)
        _ss_lay.addWidget(self.lbl_self_coord)
        self.attacker_panel_layout.addWidget(self._self_status_box)

        root.addWidget(self.attacker_panel)

        # 사냥 도우미는 본창 패널이 아니라 인게임 오버레이(HunterHelperOverlay)로
        # 제공. 생성·토글은 _on_toggle_overlay 에서 처리.

        root.addStretch(1)

        self.tabs.addTab(run_tab, "실행")

        # ================== 설정 탭 ==================
        cfg_tab = QtWidgets.QWidget()
        cfg_lay = QtWidgets.QVBoxLayout(cfg_tab)
        cfg_lay.setContentsMargins(6, 6, 6, 6)
        cfg_lay.setSpacing(6)

        # ----- 공통 영역 박스 (게임/맵/좌표/경험치 = 도사+격수 공통) -----
        common_box = QtWidgets.QGroupBox("공통 영역")
        common_grid = QtWidgets.QGridLayout(common_box)
        common_grid.setContentsMargins(6, 6, 6, 6)
        common_grid.setHorizontalSpacing(4)
        common_grid.setVerticalSpacing(3)
        _common_defs = [
            ("game", "게임"), ("map", "맵"),
            ("coord", "좌표"), ("xp", "경험치"),
        ]
        for i, (k, lb) in enumerate(_common_defs):
            r, c = divmod(i, 2)
            b = QtWidgets.QPushButton(lb)
            b.setMinimumWidth(72)
            b.clicked.connect(
                lambda _c=False, kk=k, lbl=lb: self._on_pick_region(kk, lbl)
            )
            bc = QtWidgets.QPushButton("해제")
            bc.setMinimumWidth(46)
            bc.setMaximumWidth(60)
            bc.clicked.connect(
                lambda _c=False, kk=k: self._on_clear_region(kk)
            )
            common_grid.addWidget(b, r, c * 2)
            common_grid.addWidget(bc, r, c * 2 + 1)
            self._region_buttons[k] = b
        # 경험치 OCR 확인 버튼 (3행에 span 4).
        self.btn_test_xp_ocr = QtWidgets.QPushButton("경험치 OCR 확인")
        self.btn_test_xp_ocr.setMaximumWidth(130)
        self.btn_test_xp_ocr.setToolTip(
            "현재 화면을 한 번 캡처해 경험치 영역에서 OCR을 돌리고 결과 표시.\n"
            "영역이 올바른지 / OCR이 숫자를 잘 읽는지 확인용."
        )
        self.btn_test_xp_ocr.clicked.connect(self._on_test_xp_ocr)
        common_grid.addWidget(self.btn_test_xp_ocr, 2, 0, 1, 2)
        cfg_lay.addWidget(common_box)

        # ----- 체력/마력/쿨/닉 영역 (도사/격수 공통으로 세팅 가능) -----
        self.dosa_extras = QtWidgets.QGroupBox("체력/마력/쿨/닉 영역")
        de_lay = QtWidgets.QVBoxLayout(self.dosa_extras)
        de_lay.setContentsMargins(6, 6, 6, 6)
        de_lay.setSpacing(3)
        # 체력/마력 (그리드).
        hpmp_row = QtWidgets.QGridLayout()
        hpmp_row.setHorizontalSpacing(4)
        hpmp_row.setVerticalSpacing(3)
        for i, (k, lb) in enumerate([("hp", "체력"), ("mp", "마력")]):
            b = QtWidgets.QPushButton(lb)
            b.setMinimumWidth(72)
            b.clicked.connect(
                lambda _c=False, kk=k, lbl=lb: self._on_pick_region(kk, lbl)
            )
            bc = QtWidgets.QPushButton("해제")
            bc.setMinimumWidth(46)
            bc.setMaximumWidth(60)
            bc.clicked.connect(
                lambda _c=False, kk=k: self._on_clear_region(kk)
            )
            hpmp_row.addWidget(b, 0, i * 2)
            hpmp_row.addWidget(bc, 0, i * 2 + 1)
            self._region_buttons[k] = b
        hpmp_w = QtWidgets.QWidget(); hpmp_w.setLayout(hpmp_row)
        de_lay.addWidget(hpmp_w)
        # HP/MP 최대값 — OCR 이 "cur+max" 를 붙여 읽는 문제 분리 + pct 환산 기준.
        # 격수/힐러 공통 (각 PC 가 자기 값 입력).
        hpmax_row = QtWidgets.QHBoxLayout()
        hpmax_row.setSpacing(4)
        hpmax_row.addWidget(QtWidgets.QLabel("최대 HP"))
        self.hp_max_spin = QtWidgets.QSpinBox()
        # 2026-04-20: 100만 단위까지 허용. 실측 MP OCR cur=1,056,279 사례 대응.
        self.hp_max_spin.setRange(0, 9999999)
        self.hp_max_spin.setSingleStep(1)
        self.hp_max_spin.setGroupSeparatorShown(True)
        self.hp_max_spin.setValue(int(getattr(self.cfg.cooldown, "hp_max", 0)))
        self.hp_max_spin.setMinimumWidth(110)
        self.hp_max_spin.setToolTip(
            "현재 캐릭터 최대 HP. OCR 이 '현재+최대' 붙여 읽는 문제 분리용.\n"
            "예: 표시값 '603/541' 이 '603541' 로 붙어도 max=541 이면 current=603 으로 분리.\n"
            "pct = current * 100 / max. 0 이면 pct 계산 생략."
        )
        self.hp_max_spin.valueChanged.connect(self._on_hp_max_changed)
        hpmax_row.addWidget(self.hp_max_spin)
        hpmax_row.addSpacing(12)
        hpmax_row.addWidget(QtWidgets.QLabel("최대 MP"))
        self.mp_max_spin = QtWidgets.QSpinBox()
        # 2026-04-20: 100만 단위까지 허용. 실측 MP OCR cur=1,056,279 사례 대응.
        self.mp_max_spin.setRange(0, 9999999)
        self.mp_max_spin.setSingleStep(1)
        self.mp_max_spin.setGroupSeparatorShown(True)
        self.mp_max_spin.setValue(int(getattr(self.cfg.cooldown, "mp_max", 0)))
        self.mp_max_spin.setMinimumWidth(110)
        self.mp_max_spin.setToolTip(
            "현재 캐릭터 최대 MP. HP 와 동일 용도."
        )
        self.mp_max_spin.valueChanged.connect(self._on_mp_max_changed)
        hpmax_row.addWidget(self.mp_max_spin)
        hpmax_row.addStretch(1)
        hpmax_w = QtWidgets.QWidget(); hpmax_w.setLayout(hpmax_row)
        de_lay.addWidget(hpmax_w)
        # 쿨 영역 행.
        de_row1 = QtWidgets.QHBoxLayout()
        de_row1.setSpacing(4)
        self.btn_cd_region = QtWidgets.QPushButton("쿨 영역")
        self.btn_cd_region.setMinimumWidth(72)
        self.btn_cd_region.clicked.connect(self._on_pick_cd_region)
        self.btn_cd_region_clear = QtWidgets.QPushButton("해제")
        self.btn_cd_region_clear.setMinimumWidth(46)
        self.btn_cd_region_clear.setMaximumWidth(60)
        self.btn_cd_region_clear.clicked.connect(self._on_clear_cd_region)
        self.lbl_cd_region = QtWidgets.QLabel("쿨 영역: 미지정")
        self.lbl_cd_region.setObjectName("mutedLabel")
        self.healer_idx_spin = QtWidgets.QSpinBox()
        self.healer_idx_spin.setRange(0, 9)
        self.healer_idx_spin.setValue(
            int(getattr(self.cfg.net, "healer_idx", 0))
        )
        self.healer_idx_spin.setToolTip(
            "내 힐러 인덱스 (격수 peers 순서와 일치). 0=힐러1."
        )
        self.healer_idx_spin.valueChanged.connect(self._on_healer_idx)
        de_row1.addWidget(self.btn_cd_region)
        de_row1.addWidget(self.btn_cd_region_clear)
        de_row1.addWidget(self.lbl_cd_region, 1)
        de_row1.addWidget(QtWidgets.QLabel("idx"))
        de_row1.addWidget(self.healer_idx_spin)
        de_row1_w = QtWidgets.QWidget(); de_row1_w.setLayout(de_row1)
        de_lay.addWidget(de_row1_w)
        # 닉 영역 행.
        de_row2 = QtWidgets.QHBoxLayout()
        de_row2.setSpacing(4)
        self.btn_nick_region = QtWidgets.QPushButton("닉 영역")
        self.btn_nick_region.setMinimumWidth(72)
        self.btn_nick_region.setToolTip(
            "캐릭터 닉네임 표시 위치를 드래그 지정. OCR로 읽어 격수에 전송."
        )
        self.btn_nick_region.clicked.connect(self._on_pick_nick_region)
        self.btn_nick_region_clear = QtWidgets.QPushButton("해제")
        self.btn_nick_region_clear.setMinimumWidth(46)
        self.btn_nick_region_clear.setMaximumWidth(60)
        self.btn_nick_region_clear.clicked.connect(self._on_clear_nick_region)
        self.lbl_nick_region = QtWidgets.QLabel("닉 영역: 미지정")
        self.lbl_nick_region.setObjectName("mutedLabel")
        de_row2.addWidget(self.btn_nick_region)
        de_row2.addWidget(self.btn_nick_region_clear)
        de_row2.addWidget(self.lbl_nick_region, 1)
        de_row2_w = QtWidgets.QWidget(); de_row2_w.setLayout(de_row2)
        de_lay.addWidget(de_row2_w)
        # 버프(파력무참 지속시간) 영역 행.
        de_row3 = QtWidgets.QHBoxLayout()
        de_row3.setSpacing(4)
        self.btn_buff_region = QtWidgets.QPushButton("버프 영역")
        self.btn_buff_region.setMinimumWidth(72)
        self.btn_buff_region.setToolTip(
            "파력무참 버프 지속시간 숫자가 표시되는 영역을 드래그. "
            "OCR로 실제 잔여 초를 읽어 격수에 전송 (쿨 역산 폴백 대체)."
        )
        self.btn_buff_region.clicked.connect(self._on_pick_buff_region)
        self.btn_buff_region_clear = QtWidgets.QPushButton("해제")
        self.btn_buff_region_clear.setMinimumWidth(46)
        self.btn_buff_region_clear.setMaximumWidth(60)
        self.btn_buff_region_clear.clicked.connect(self._on_clear_buff_region)
        self.lbl_buff_region = QtWidgets.QLabel("버프 영역: 미지정")
        self.lbl_buff_region.setObjectName("mutedLabel")
        de_row3.addWidget(self.btn_buff_region)
        de_row3.addWidget(self.btn_buff_region_clear)
        de_row3.addWidget(self.lbl_buff_region, 1)
        de_row3_w = QtWidgets.QWidget(); de_row3_w.setLayout(de_row3)
        de_lay.addWidget(de_row3_w)
        # 2026-04-20: 혼마술 전용 영역 rollback (버프 영역 = 혼마술 영역 공용).
        # 격수 PC 에서 "버프 영역" 으로 파력무참/혼마술 동시 OCR.
        # HP/MP 확인 버튼 (픽셀 리더 1회 실행 → %표시).
        test_row = QtWidgets.QHBoxLayout()
        test_row.setSpacing(4)
        self.btn_test_hpmp = QtWidgets.QPushButton("체력/마력 확인")
        self.btn_test_hpmp.setMaximumWidth(130)
        self.btn_test_hpmp.setToolTip(
            "현재 화면을 1회 캡처해 저장된 HP/MP 영역 픽셀 비율을 읽어 결과 표시.\n"
            "영역 위치/크기가 올바른지 % 값으로 확인용."
        )
        self.btn_test_hpmp.clicked.connect(self._on_test_hpmp)
        test_row.addWidget(self.btn_test_hpmp)
        test_row.addStretch(1)
        test_row_w = QtWidgets.QWidget(); test_row_w.setLayout(test_row)
        de_lay.addWidget(test_row_w)
        cfg_lay.addWidget(self.dosa_extras)

        # ----- 블록 A/B 테스트 (힐러 전용, 자힐/부활 파이프라인 검증) -----
        self.block_test_box = QtWidgets.QGroupBox("타겟 시퀀스 테스트 (힐러)")
        btb_lay = QtWidgets.QHBoxLayout(self.block_test_box)
        btb_lay.setContentsMargins(6, 6, 6, 6)
        btb_lay.setSpacing(4)
        self.btn_test_block_a = QtWidgets.QPushButton("블록 A 실행 (F11)")
        self.btn_test_block_a.setToolTip(
            "ESC → HOME → TAB 시퀀스 1회 실행 (self-target).\n"
            "msw.exe 포커스 상태에서만 눌러야 함."
        )
        self.btn_test_block_a.clicked.connect(self._on_test_block_a)
        self.btn_test_block_b = QtWidgets.QPushButton("블록 B 실행 (F12)")
        self.btn_test_block_b.setToolTip(
            "NumLock 스킬 해제 → ESC×3 → TAB×2 (격수 복귀 준비).\n"
            "워커 실행 중이면 NumLockCycler도 함께 해제."
        )
        self.btn_test_block_b.clicked.connect(self._on_test_block_b)
        btb_lay.addWidget(self.btn_test_block_a)
        btb_lay.addWidget(self.btn_test_block_b)
        btb_lay.addStretch(1)
        cfg_lay.addWidget(self.block_test_box)

        # ----- 기타 옵션 -----
        opt_row = QtWidgets.QHBoxLayout()
        opt_row.setSpacing(8)
        self.chk_region_overlay = QtWidgets.QCheckBox("영역 표시")
        self.chk_region_overlay.setToolTip(
            "등록된 영역들을 게임 화면 위에 초록 테두리로 표시 "
            "(마우스 입력 통과)."
        )
        self.chk_region_overlay.stateChanged.connect(
            self._on_toggle_region_overlay
        )
        self.chk_low_spec = QtWidgets.QCheckBox("저사양 모드")
        self.chk_low_spec.setToolTip(
            "저사양 PC용 FPS 최적화: YOLO imgsz 480, 매 2프레임 추론, "
            "프리뷰 5Hz 상한, OCR poll 3.0s, 캡처 game 영역 크롭."
        )
        self.chk_low_spec.stateChanged.connect(self._on_toggle_low_spec)
        opt_row.addWidget(self.chk_region_overlay)
        opt_row.addWidget(self.chk_low_spec)
        opt_row.addStretch(1)
        cfg_lay.addLayout(opt_row)

        # ----- 세부 설정 버튼 3개 -----
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(4)
        self.btn_skill_cfg = QtWidgets.QPushButton("스킬 …")
        self.btn_skill_cfg.clicked.connect(self._open_skill_dialog)
        self.btn_param_cfg = QtWidgets.QPushButton("파라미터 …")
        self.btn_param_cfg.clicked.connect(self._open_param_dialog)
        self.btn_net_cfg = QtWidgets.QPushButton("네트워크 …")
        self.btn_net_cfg.clicked.connect(self._open_net_dialog)
        self.btn_hunt_report = QtWidgets.QPushButton("사냥 리포트 …")
        self.btn_hunt_report.clicked.connect(self._open_hunt_report_dialog)
        for b in (self.btn_skill_cfg, self.btn_param_cfg,
                  self.btn_net_cfg, self.btn_hunt_report):
            b.setMinimumWidth(96)
        btn_row.addWidget(self.btn_skill_cfg)
        btn_row.addWidget(self.btn_param_cfg)
        btn_row.addWidget(self.btn_net_cfg)
        btn_row.addWidget(self.btn_hunt_report)
        cfg_lay.addLayout(btn_row)
        cfg_lay.addStretch(1)

        self.tabs.addTab(cfg_tab, "설정")

        # 상태 (2열, 핵심만) — UI에서는 숨기지만 라벨은 워커가 업데이트하므로
        # self에 바인딩해 GC 방지 (부모 지정 + self 참조).
        stat = QtWidgets.QGroupBox("상태", self)
        self._stat_box = stat
        sl = QtWidgets.QGridLayout(stat)
        sl.setContentsMargins(4, 4, 4, 4)
        sl.setHorizontalSpacing(6)
        sl.setVerticalSpacing(2)
        self.lbl_fsm = QtWidgets.QLabel("-")
        self.lbl_hold = QtWidgets.QLabel("-")
        self.lbl_seq = QtWidgets.QLabel("-")
        self.lbl_udp = QtWidgets.QLabel("-")
        self.lbl_red = QtWidgets.QLabel("-")
        self.lbl_fps = QtWidgets.QLabel("-")
        self.lbl_hcoord = QtWidgets.QLabel("-")
        self.lbl_acoord = QtWidgets.QLabel("-")
        self.lbl_map = QtWidgets.QLabel("-")
        self.lbl_want = QtWidgets.QLabel("-")
        self.lbl_reason = QtWidgets.QLabel("-")
        self.lbl_reason.setWordWrap(True)
        self.lbl_fg = QtWidgets.QLabel("-")
        self.lbl_nl = QtWidgets.QLabel("-")
        self.lbl_perf = QtWidgets.QLabel("-")
        self.lbl_perf.setWordWrap(True)
        # 2열 배치: (key, val, key, val)
        pairs = [
            ("FSM", self.lbl_fsm), ("want", self.lbl_want),
            ("hold", self.lbl_hold), ("FPS", self.lbl_fps),
            ("UDP", self.lbl_udp), ("NumLock", self.lbl_nl),
            ("힐러", self.lbl_hcoord), ("격수", self.lbl_acoord),
            ("맵", self.lbl_map), ("red", self.lbl_red),
            ("FG", self.lbl_fg), ("seq", self.lbl_seq),
        ]
        for i, (k, w) in enumerate(pairs):
            row, col = divmod(i, 2)
            sl.addWidget(QtWidgets.QLabel(k), row, col * 2)
            w.setStyleSheet("font-weight:bold;")
            sl.addWidget(w, row, col * 2 + 1)
        row_n = (len(pairs) + 1) // 2
        sl.addWidget(QtWidgets.QLabel("이유"), row_n, 0)
        sl.addWidget(self.lbl_reason, row_n, 1, 1, 3)
        sl.addWidget(QtWidgets.QLabel("perf"), row_n + 1, 0)
        sl.addWidget(self.lbl_perf, row_n + 1, 1, 1, 3)
        # 상태 패널/로그 박스는 UI 최소화로 숨김 (내부 참조는 유지).
        stat.hide()
        self.log = QtWidgets.QPlainTextEdit(self)
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(300)
        self.log.hide()
        root.addStretch(1)

    # GUI 로그 박스에서 숨길 진단 태그 (파일 로그에는 그대로 남음).
    _NOISY_LOG_PREFIXES = (
        "[STAT]", "[PERF]", "[UDP-STALL]", "[UDP-RECV]", "[UDP-BIND]",
        "[CD-OCR]", "[CTRL-LISTEN]", "[CTRL-RECV]", "[REMOTE-IDLE]",
        "[CYCLE]", "[NO-STATE]", "[HEALER-COORD", "[MAP-",
    )

    def _append_log(self, s: str):
        ts = time.strftime("%H:%M:%S")
        self.log.appendPlainText(f"[{ts}] {s}")

    def _append_log_filtered(self, s: str):
        """워커 log_msg 진단 태그를 GUI 박스에서 감추기. 파일 로그엔 남음."""
        for p in self._NOISY_LOG_PREFIXES:
            if s.startswith(p):
                return
        self._append_log(s)

    def _on_role_change(self, _checked=False):
        self.role = "healer" if self.rb_healer.isChecked() else "attacker"
        self._apply_role_ui()
        self._append_log(f"역할={self.role}")
        if self.role == "healer":
            # 격수 heartbeat 정리 → 힐러 리스너/heartbeat 가동.
            self._stop_attacker_heartbeat()
            self._start_ctrl_listener()
            self._start_heartbeat()
        else:
            # 힐러 heartbeat/리스너 정리 → 격수 heartbeat 가동.
            self._stop_ctrl_listener()
            self._stop_heartbeat()
            self._start_attacker_heartbeat()
        # 역할 변경 즉시 오버레이 가시성 재평가 (힐러 모드면 바로 숨김).
        try:
            self._tick_overlay_visibility()
        except Exception:
            pass

    def _apply_role_ui(self):
        """창 크기는 setFixedSize로 고정. 역할에 따라 프리뷰/버튼 가시성만 토글."""
        is_healer = (self.role == "healer")
        self.preview.setVisible(is_healer)
        # 힐러 전용 설정은 숨겨도 되지만, 팝업이라 버튼만 남기고 그대로 둠.
        # 격수 모드에선 ARM/스킬/파라미터 버튼 비활성.
        self.chk_arm.setEnabled(is_healer)
        self.btn_pause.setEnabled(is_healer)
        self.chk_follow_only.setEnabled(is_healer)
        self.btn_skill_cfg.setEnabled(is_healer)
        self.btn_param_cfg.setEnabled(is_healer)
        self.btn_net_cfg.setEnabled(not is_healer)
        # 체력/마력/쿨/닉 영역은 도사/격수 공통으로 세팅 가능 — 항상 표시.
        self.dosa_extras.setVisible(True)
        # 블록 A/B 테스트 — 힐러 전용 (격수는 사용 안 함).
        if hasattr(self, "block_test_box"):
            self.block_test_box.setVisible(is_healer)
        # 격수 패널은 격수 모드일 때만.
        self.attacker_panel.setVisible(not is_healer)
        # 도적/전사 서브클래스 선택 — 격수일 때만.
        if hasattr(self, "subclass_container"):
            self.subclass_container.setVisible(not is_healer)
        # 사냥 도우미 오버레이 — 격수 모드 + 오버레이 ON일 때만 show.
        try:
            if self._helper_overlay is not None:
                want = (not is_healer) and (self._overlay is not None
                                            and self._overlay.isVisible())
                if want and not self._helper_overlay.isVisible():
                    self._helper_overlay.show()
                elif (not want) and self._helper_overlay.isVisible():
                    self._helper_overlay.hide()
        except Exception:
            pass
        # 힐러 HP/MP 오버레이 — 격수 모드 + 오버레이 ON일 때만 show.
        try:
            if self._hpmp_overlay is not None:
                want = (not is_healer) and (self._overlay is not None
                                            and self._overlay.isVisible())
                if want and not self._hpmp_overlay.isVisible():
                    self._hpmp_overlay.show()
                elif (not want) and self._hpmp_overlay.isVisible():
                    self._hpmp_overlay.hide()
        except Exception:
            pass
        # 스킬범위 오버레이 — 격수 모드 + 체크박스 ON일 때만 show.
        try:
            if self._skill_range_overlay is not None:
                want = (not is_healer) and (
                    hasattr(self, "chk_skill_range")
                    and self.chk_skill_range.isChecked()
                )
                if want and not self._skill_range_overlay.isVisible():
                    self._skill_range_overlay.show()
                elif (not want) and self._skill_range_overlay.isVisible():
                    self._skill_range_overlay.hide()
        except Exception:
            pass
        if is_healer:
            self.setWindowTitle("옛바 컨트롤 — 도사")
        else:
            self.setWindowTitle("옛바 컨트롤 — 격수")
            self._refresh_healer_rows()

    def _refresh_healer_rows(self):
        """peers 입력 기반으로 힐러 행 재구성. 격수 모드 진입 시 호출.

        각 힐러는 QGroupBox 로 묶여 세로 3라인 표시:
          [●] 닉  (IP)  [▶][‖][■][따O][따X]
            맵: <healer_map>
            좌표: (x, y)
            상태: <state_text>
        """
        # 기존 동적 행 제거.
        for row in self._healer_rows:
            w = row.get("widget")
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._healer_rows.clear()
        peers_raw = self.peers_edit.text().strip()
        peers = [p.strip() for p in peers_raw.split(",") if p.strip()]
        # 최대 5명 (사용자 요청).
        peers = peers[:5]
        for idx, peer in enumerate(peers):
            box = QtWidgets.QGroupBox()
            box.setStyleSheet(
                "QGroupBox { margin-top: 4px; padding: 6px 6px 6px 6px;"
                " border: 1px solid #2a2e36; border-radius: 4px; }"
            )
            box_lay = QtWidgets.QVBoxLayout(box)
            box_lay.setContentsMargins(4, 4, 4, 4)
            box_lay.setSpacing(1)
            # 헤더 행: 뱃지 + 닉 + peer + 버튼.
            hdr = QtWidgets.QHBoxLayout()
            hdr.setContentsMargins(0, 0, 0, 0)
            hdr.setSpacing(4)
            lbl_badge = QtWidgets.QLabel("●")
            lbl_badge.setFixedWidth(12)
            lbl_badge.setStyleSheet("color:#b7bec9;font-weight:bold;")
            lbl_badge.setToolTip("미수신")
            _cached = self._healer_cooldowns.get(idx, {})
            _override_nick = ""
            try:
                if hasattr(self.net_dlg, "get_nicks"):
                    _nk_list = self.net_dlg.get_nicks()
                    if 0 <= idx < len(_nk_list):
                        _override_nick = str(_nk_list[idx] or "").strip()
            except Exception:
                pass
            _init_nick = _override_nick or (
                str(_cached.get("_locked_nick", "") or "").strip()
                or str(_cached.get("nickname", "") or "").strip()
            )
            lbl = QtWidgets.QLabel(_init_nick if _init_nick else f"힐러{idx + 1}")
            lbl.setMinimumWidth(55)
            lbl.setStyleSheet("font-weight:bold;")
            lbl_peer = QtWidgets.QLabel(peer)
            lbl_peer.setStyleSheet("color:#98a2b3;font-size:9pt;")
            lbl_peer.setMinimumWidth(95)
            btn_s = QtWidgets.QPushButton("▶")
            btn_p = QtWidgets.QPushButton("‖")
            btn_st = QtWidgets.QPushButton("■")
            btn_fon = QtWidgets.QPushButton("따O")
            btn_foff = QtWidgets.QPushButton("따X")
            btn_fon.setToolTip("따라가기 전용 ON")
            btn_foff.setToolTip("따라가기 전용 OFF (전투 복귀)")
            for b in (btn_s, btn_p, btn_st):
                b.setFixedWidth(22)
            for b in (btn_fon, btn_foff):
                b.setFixedWidth(38)
            btn_s.clicked.connect(lambda _, i=idx: self._send_ctrl(i, "start"))
            btn_p.clicked.connect(lambda _, i=idx: self._send_ctrl(i, "pause"))
            btn_st.clicked.connect(lambda _, i=idx: self._send_ctrl(i, "stop"))
            btn_fon.clicked.connect(lambda _, i=idx: self._send_ctrl(i, "follow_on"))
            btn_foff.clicked.connect(lambda _, i=idx: self._send_ctrl(i, "follow_off"))
            hdr.addWidget(lbl_badge)
            hdr.addWidget(lbl)
            hdr.addWidget(lbl_peer, 1)
            hdr.addWidget(btn_s)
            hdr.addWidget(btn_p)
            hdr.addWidget(btn_st)
            hdr.addWidget(btn_fon)
            hdr.addWidget(btn_foff)
            hdr_wrap = QtWidgets.QWidget()
            hdr_wrap.setLayout(hdr)
            box_lay.addWidget(hdr_wrap)
            # 상세 3라인 (맵/좌표/상태).
            lbl_map = QtWidgets.QLabel("맵: -")
            lbl_coord = QtWidgets.QLabel("좌표: -")
            lbl_state = QtWidgets.QLabel("상태: -")
            for _lb in (lbl_map, lbl_coord, lbl_state):
                _lb.setStyleSheet(
                    "color:#cbd2da;font-size:9pt;padding-left:18px;"
                )
                box_lay.addWidget(_lb)
            self.attacker_panel_layout.addWidget(box)
            self._healer_rows.append({
                "idx": idx, "peer": peer, "widget": box,
                "lbl": lbl, "lbl_badge": lbl_badge,
                "lbl_map": lbl_map, "lbl_coord": lbl_coord,
                "lbl_state": lbl_state,
                "default_name": f"힐러{idx + 1}",
            })
        # 이미 받아둔 쿨다운 값 반영.
        for idx, d in self._healer_cooldowns.items():
            self._paint_cooldown_row(idx, d)

    def _paint_cooldown_row(self, idx: int, d: dict) -> None:
        if not (0 <= idx < len(self._healer_rows)):
            return
        row = self._healer_rows[idx]
        nick = str(d.get("nickname", "") or "").strip()
        locked = str(d.get("_locked_nick", "") or "").strip()
        if nick:
            if not locked:
                d["_locked_nick"] = nick
                row["lbl"].setText(nick)
        # 맵/좌표/상태 라벨 갱신.
        hmap = str(d.get("healer_map", "") or "").strip()
        row["lbl_map"].setText(f"맵: {hmap if hmap else '-'}")
        if bool(d.get("coord_valid", False)):
            hx = int(d.get("healer_x", 0))
            hy = int(d.get("healer_y", 0))
            row["lbl_coord"].setText(f"좌표: ({hx}, {hy})")
        else:
            row["lbl_coord"].setText("좌표: -")
        st = str(d.get("state_text", "") or "").strip()
        row["lbl_state"].setText(f"상태: {st if st else '-'}")
        self._refresh_badge(idx)

    def _refresh_badge(self, idx: int) -> None:
        if not (0 <= idx < len(self._healer_rows)):
            return
        row = self._healer_rows[idx]
        d = self._healer_cooldowns.get(idx, {})
        last = float(d.get("_last_recv", 0.0))
        connected = (last > 0.0) and ((time.time() - last) < 3.0)
        armed = bool(d.get("armed", False)) if "armed" in d else False
        if not connected:
            row["lbl_badge"].setText("●")
            row["lbl_badge"].setStyleSheet("color:#b7bec9;font-weight:bold;")
            row["lbl_badge"].setToolTip("미수신")
        elif armed:
            row["lbl_badge"].setText("●")
            row["lbl_badge"].setStyleSheet("color:#16a34a;font-weight:bold;")
            row["lbl_badge"].setToolTip("연결됨 / ARM ON")
        else:
            row["lbl_badge"].setText("○")
            row["lbl_badge"].setStyleSheet("color:#16a34a;font-weight:bold;")
            row["lbl_badge"].setToolTip("연결됨 / ARM OFF")

    def _tick_connection_state(self) -> None:
        if self.role != "attacker":
            return
        for idx in range(len(self._healer_rows)):
            self._refresh_badge(idx)

    def _send_ctrl(self, target_idx: int, cmd: str) -> None:
        tag = 'ALL' if target_idx == -1 else f'힐러{target_idx+1}'
        # 1) 격수.txt 파일에도 버튼 클릭 자체 기록 (GUI textbox만으로는 손실됨).
        worker_log = getattr(self.worker, "log", None) if self.worker else None
        if worker_log is not None:
            try:
                worker_log.info(
                    f"[CTRL-CLICK] target={target_idx}({tag}) cmd={cmd}"
                )
            except Exception:
                pass
        if self.role != "attacker":
            self._append_log("[CTRL] 격수 모드 아님 — 송신 skip")
            if worker_log is not None:
                try:
                    worker_log.info("[CTRL-CLICK] skip (role!=attacker)")
                except Exception:
                    pass
            return
        if self.worker is None or not hasattr(self.worker, "send_control"):
            self._append_log("[CTRL] attacker 워커 미시작")
            if worker_log is not None:
                try:
                    worker_log.info("[CTRL-CLICK] skip (worker not running)")
                except Exception:
                    pass
            return
        ok = self.worker.send_control(target_idx, cmd)
        self._append_log(
            f"[CTRL] target={tag} cmd={cmd} ok={ok}"
        )

    def _on_attacker_cooldown(self, d: dict) -> None:
        src_ip = str(d.get("src_ip", "") or "").strip()
        # peers에 없는 IP면 자동 등록 → 힐러 행 자동 생성. 사용자가 IP를
        # 수동 입력 안 해도 실제로 수신되는 힐러만 표시됨.
        if src_ip:
            peers_raw = self.peers_edit.text().strip()
            peers = [p.strip() for p in peers_raw.split(",") if p.strip()]
            if src_ip not in peers:
                peers.append(src_ip)
                self.peers_edit.blockSignals(True)
                self.peers_edit.setText(", ".join(peers))
                self.peers_edit.blockSignals(False)
                try:
                    self.cfg.net.peers = list(peers)
                except Exception:
                    pass
                self._refresh_healer_rows()
                try:
                    if self._attacker_hb is not None:
                        self._stop_attacker_heartbeat()
                        self._start_attacker_heartbeat()
                except Exception:
                    pass
            # IP 기반으로 row_idx 재결정 (peers 인덱스 = 행 인덱스).
            idx = peers.index(src_ip)
        else:
            idx = int(d.get("src_idx", 0))
        # 이전 dict 상태 이어받기. OCR 실패 프레임이나 과거 heartbeat가
        # 쿨다운/닉 빈 값을 보내 직전 유효 정보를 덮어씌우지 않도록 방어.
        # armed는 그대로 새 값 반영(원격 정지/재개 즉시 반영).
        prev = self._healer_cooldowns.get(idx, {})
        if prev.get("_locked_nick") and not d.get("_locked_nick"):
            d["_locked_nick"] = prev["_locked_nick"]
        if int(d.get("cd_parlyuk", -1)) < 0 and int(prev.get("cd_parlyuk", -1)) >= 0:
            d["cd_parlyuk"] = prev["cd_parlyuk"]
        if int(d.get("cd_baekho", -1)) < 0 and int(prev.get("cd_baekho", -1)) >= 0:
            d["cd_baekho"] = prev["cd_baekho"]
        # 버프 지속시간 stick — OCR 실패(-1) 프레임이 이전 유효치를 덮어쓰지 않게.
        if (int(d.get("buff_parlyuk_sec", -1)) < 0
                and int(prev.get("buff_parlyuk_sec", -1)) >= 0):
            d["buff_parlyuk_sec"] = prev["buff_parlyuk_sec"]
        # HP/MP stick — event-only 패킷(alert 경로)이나 OCR 실패 프레임이
        # 이전 유효치를 덮어쓰지 않게. pct/cur 는 -1, max 는 0 기준.
        for _k in ("hp_pct", "mp_pct", "hp_cur", "mp_cur"):
            if int(d.get(_k, -1)) < 0 and int(prev.get(_k, -1)) >= 0:
                d[_k] = prev[_k]
        for _k in ("hp_max", "mp_max"):
            if int(d.get(_k, 0)) <= 0 and int(prev.get(_k, 0)) > 0:
                d[_k] = prev[_k]
        # 임계치 stick — heartbeat 빈 패킷(-1) 이 워커 기동 후 보낸 실제
        # 임계치를 덮어쓰지 않게.
        for _k in ("self_heal_hp_thr", "gyoungryeok_mp_thr"):
            if int(d.get(_k, -1)) < 0 and int(prev.get(_k, -1)) >= 0:
                d[_k] = prev[_k]
        if not str(d.get("nickname", "") or "").strip() and prev.get("nickname"):
            d["nickname"] = prev["nickname"]
        # NetworkDialog에 지정된 닉이 있으면 OCR 결과보다 우선 (최상위 override).
        try:
            if hasattr(self.net_dlg, "get_nicks"):
                _nk_list = self.net_dlg.get_nicks()
                if 0 <= idx < len(_nk_list):
                    _ov = str(_nk_list[idx] or "").strip()
                    if _ov:
                        d["nickname"] = _ov
                        d["_locked_nick"] = _ov
        except Exception:
            pass
        d["_last_recv"] = time.time()
        self._healer_cooldowns[idx] = d
        self._paint_cooldown_row(idx, d)
        # 사냥 도우미 오버레이 — 파력무참 지속시간 갱신.
        try:
            if (self._helper_overlay is not None
                    and self._helper_overlay.isVisible()):
                self._helper_overlay.update_data(self._healer_cooldowns)
        except Exception:
            pass
        # 오버레이에도 반영.
        if self._overlay is not None and self._overlay.isVisible():
            try:
                self._overlay.update_healer(idx, d)
            except Exception:
                pass
        # 힐러 HP/MP 상태 오버레이 — 격수 전용. 수신 힐러별 HP/MP 막대.
        try:
            if (self._hpmp_overlay is not None
                    and self._hpmp_overlay.isVisible()):
                self._hpmp_overlay.update_healer(idx, d)
        except Exception:
            pass
        # 공증 임박 자체 판정 — mp_pct/임계치 기반 edge 트리거 알림.
        try:
            self._check_gyoungryeok_imminent(idx, d)
        except Exception as _e:
            try:
                self._append_log(f"[공증 임박] 판정 예외: {_e}")
            except Exception:
                pass
        # 스킬 임박 카운트다운: 실제 cd=4 에서 "3초 전" 시작 → 3:"2초 전" →
        # 2:"1초 전" → 1:"곧 시전". 같은 key로 덮어써 한 줄이 갱신됨.
        try:
            nick = str(d.get("nickname", "") or "").strip() or f"힐러{idx + 1}"
            cur_p = int(d.get("cd_parlyuk", -1))
            cur_b = int(d.get("cd_baekho", -1))
            if (self._alert_overlay is not None
                    and self._alert_overlay.isVisible()):
                self._push_skill_countdown(
                    idx, "p", nick, "파력무참", cur_p
                )
                self._push_skill_countdown(
                    idx, "b", nick, "백호의희원", cur_b
                )
            self._alert_prev[idx] = {"p": cur_p, "b": cur_b}
        except Exception:
            pass
        # 2026-04-21: 힐러 → 격수 이벤트 알림 (공력증강 임박 / 자힐 하는중).
        # event_seq 가 이전과 다를 때만 새 이벤트로 취급 → overlay.push_alert.
        try:
            ev_seq = int(d.get("event_seq", 0) or 0)
            ev_text = str(d.get("event_text", "") or "").strip()
            if ev_text and ev_seq > 0:
                prev_seq = int(self._healer_last_event_seq.get(idx, 0))
                if ev_seq != prev_seq:
                    self._healer_last_event_seq[idx] = ev_seq
                    nick2 = (str(d.get("nickname", "") or "").strip()
                             or f"힐러{idx + 1}")
                    ov_state = (
                        "visible" if (self._alert_overlay is not None
                                      and self._alert_overlay.isVisible())
                        else ("hidden" if self._alert_overlay is not None
                              else "none")
                    )
                    self._append_log(
                        f"[ALERT-RECV] {nick2} {ev_text} "
                        f"(seq={ev_seq}, overlay={ov_state})"
                    )
                    if (self._alert_overlay is not None
                            and self._alert_overlay.isVisible()):
                        self._alert_overlay.push_alert(
                            f"{nick2} {ev_text}", duration_sec=3.0
                        )
        except Exception as _e:
            try:
                self._append_log(f"[ALERT-RECV] 처리 예외: {_e}")
            except Exception:
                pass

    def _ensure_skill_range_overlay(self) -> None:
        """신규 SkillRangeOverlay 생성 + 앵커/HWND/opacity/서브클래스 주입."""
        if self._skill_range_overlay is not None:
            try:
                self._append_log("[SKILL-RANGE] ensure: 이미 생성됨, skip")
            except Exception:
                pass
            return
        from .skill_range_overlay import SkillRangeOverlay
        self._skill_range_overlay = SkillRangeOverlay()
        try:
            self._append_log("[SKILL-RANGE] 오버레이 신규 생성")
        except Exception:
            pass
        # game_rect 앵커.
        try:
            game_r = self._regions.get("game")
            map_r = self._regions.get("map")
            self._skill_range_overlay.set_anchor_regions(game_r, map_r)
        except Exception:
            pass
        # msw HWND 바인딩 (다른 오버레이와 동일).
        try:
            from ..input.keys import find_windows_by_process
            tw = str(getattr(self.cfg.input, "target_window", "msw.exe"))
            wins = (find_windows_by_process(tw)
                    if tw.lower().endswith(".exe") else [])
            hwnd = wins[0] if wins else None
            if hwnd:
                self._skill_range_overlay.attach_to_hwnd(hwnd)
        except Exception:
            pass
        # 공용 투명도 슬라이더 제거됨 — 각 스킬 alpha 로만 조절.
        try:
            self._skill_range_overlay.set_opacity(1.0)
        except Exception:
            pass
        # 서브클래스/승급/타일.
        try:
            self._skill_range_overlay.set_subclass(
                getattr(self, "attacker_subclass", "thief")
            )
            self._skill_range_overlay.set_rank(
                int(getattr(self, "attacker_rank", 4))
            )
            self._skill_range_overlay.set_tile_w(
                int(self.spin_skill_tile_w.value())
            )
            self._skill_range_overlay.set_tile_h(
                int(self.spin_skill_tile_h.value())
            )
            self._skill_range_overlay.set_offset_u_x(
                int(self.spin_skill_u_x.value()))
            self._skill_range_overlay.set_offset_u_y(
                int(self.spin_skill_u_y.value()))
            self._skill_range_overlay.set_offset_d_x(
                int(self.spin_skill_d_x.value()))
            self._skill_range_overlay.set_offset_d_y(
                int(self.spin_skill_d_y.value()))
            self._skill_range_overlay.set_offset_l_x(
                int(self.spin_skill_l_x.value()))
            self._skill_range_overlay.set_offset_l_y(
                int(self.spin_skill_l_y.value()))
            self._skill_range_overlay.set_offset_r_x(
                int(self.spin_skill_r_x.value()))
            self._skill_range_overlay.set_offset_r_y(
                int(self.spin_skill_r_y.value()))
        except Exception:
            pass
        # 스킬별 투명도/사용여부 현재 UI 값 반영.
        try:
            for _nm, _sld in self.sld_skill_alpha.items():
                self._skill_range_overlay.set_skill_alpha(
                    _nm, int(_sld.value())
                )
            for _nm, _chk in self.chk_skill_enabled.items():
                self._skill_range_overlay.set_skill_enabled(
                    _nm, bool(_chk.isChecked())
                )
        except Exception:
            pass

    def _on_toggle_skill_range(self, state) -> None:
        on = (state == QtCore.Qt.Checked)
        if on:
            self._ensure_skill_range_overlay()
            try:
                # 격수 모드일 때만 실제 show.
                if self.role == "attacker":
                    self._skill_range_overlay.show()
                    self._append_log(
                        f"[SKILL-RANGE] ON show 완료 "
                        f"size={self._skill_range_overlay.width()}x"
                        f"{self._skill_range_overlay.height()} "
                        f"subclass={getattr(self, 'attacker_subclass', '?')} "
                        f"rank={getattr(self, 'attacker_rank', '?')}"
                    )
                else:
                    self._skill_range_overlay.hide()
                    self._append_log(
                        f"[SKILL-RANGE] ON 이지만 role={self.role} → hide 유지"
                    )
            except Exception as _e:
                self._append_log(f"[SKILL-RANGE] show 예외: {_e}")
            self._append_log("[스킬범위] ON")
        else:
            if self._skill_range_overlay is not None:
                try:
                    self._skill_range_overlay.hide()
                    self._append_log("[SKILL-RANGE] OFF hide 완료")
                except Exception as _e:
                    self._append_log(f"[SKILL-RANGE] hide 예외: {_e}")
            self._append_log("[스킬범위] OFF")
        try:
            self._save_settings()
        except Exception:
            pass

    def _on_skill_tile_changed(self, v: int) -> None:
        # 하위호환. 가로 스피너 변경과 동일.
        self._on_skill_tile_w_changed(v)

    def _on_skill_tile_w_changed(self, v: int) -> None:
        if self._skill_range_overlay is not None:
            try:
                self._skill_range_overlay.set_tile_w(int(v))
            except Exception:
                pass
        try:
            self._save_settings()
        except Exception:
            pass

    def _on_skill_tile_h_changed(self, v: int) -> None:
        if self._skill_range_overlay is not None:
            try:
                self._skill_range_overlay.set_tile_h(int(v))
            except Exception:
                pass
        try:
            self._save_settings()
        except Exception:
            pass

    def _on_skill_offset_changed(self, which: str, v: int) -> None:
        """방향별 오프셋 SpinBox 변경 공통 핸들러."""
        if self._skill_range_overlay is not None:
            try:
                fn = getattr(
                    self._skill_range_overlay, f"set_offset_{which}", None
                )
                if fn is not None:
                    fn(int(v))
            except Exception:
                pass
        try:
            self._save_settings()
        except Exception:
            pass

    # 하위호환 stub — 제거된 반폭/반높이 setter.
    def _on_skill_hw_changed(self, v: int) -> None: pass
    def _on_skill_hh_changed(self, v: int) -> None: pass

    def _on_skill_range_opacity_changed(self, v: int) -> None:
        # 공용 투명도 제거됨 — stub.
        pass

    def _on_skill_y_changed(self, v: int) -> None:
        # 제거된 공용 Y오프셋. 하위호환 stub.
        pass

    def _on_skill_enabled_changed(self, name: str, on: bool) -> None:
        if self._skill_range_overlay is not None:
            try:
                self._skill_range_overlay.set_skill_enabled(name, bool(on))
            except Exception:
                pass
        try:
            self._save_settings()
        except Exception:
            pass

    def _on_skill_alpha_changed(self, name: str, v: int) -> None:
        """스킬별 투명도 슬라이더 변경 → 오버레이 반영 + 라벨/저장."""
        try:
            pct = max(0, min(100, int(v)))
        except Exception:
            pct = 80
        try:
            if name in self.lbl_skill_alpha:
                self.lbl_skill_alpha[name].setText(f"{pct}%")
        except Exception:
            pass
        try:
            if self._skill_range_overlay is not None:
                self._skill_range_overlay.set_skill_alpha(name, pct)
        except Exception:
            pass
        try:
            self._save_settings()
        except Exception:
            pass

    def _check_gyoungryeok_imminent(self, idx: int, d: dict) -> None:
        """격수 자체 공증 임박 판정.

        조건: `mp_pct` 가 `gyoungryeok_mp_thr + margin` 이하로 cross-down
        한 순간 1회 알림. margin=10 (힐러 측 판정과 동일).
        알림은 alert_overlay.push_alert + 로그. 중복 방지 위해 edge 트리거.
        임박 구간을 한 번 벗어났다가 다시 진입 시 재알림.
        """
        MARGIN = 10
        mp = int(d.get("mp_pct", -1))
        thr = int(d.get("gyoungryeok_mp_thr", -1))
        # 판정 불가: MP 미관측 or 임계치 미수신.
        if mp < 0 or thr < 0:
            self._gyoungryeok_imminent_prev[idx] = False
            return
        # 임박 구간 = mp 가 (thr, thr+MARGIN] 에 들어왔을 때.
        # mp <= thr 면 이미 공증 시전 구간 → "임박" 아님.
        in_imminent = (thr < mp <= thr + MARGIN)
        prev = bool(self._gyoungryeok_imminent_prev.get(idx, False))
        self._gyoungryeok_imminent_prev[idx] = in_imminent
        if not (in_imminent and not prev):
            return  # edge 아님.
        nick = str(d.get("nickname", "") or "").strip() or f"힐러{idx + 1}"
        msg = f"{nick} 공증 임박 MP {mp}% (임계 {thr}%)"
        try:
            self._append_log(
                f"[공증 임박] idx={idx} nick={nick} mp={mp} thr={thr}"
            )
        except Exception:
            pass
        # alert_overlay 가 살아있을 때만 푸시 (오버레이 OFF면 로그만).
        try:
            if (self._alert_overlay is not None
                    and self._alert_overlay.isVisible()):
                self._alert_overlay.push_alert(msg, duration_sec=3.0)
        except Exception:
            pass

    def _push_skill_countdown(self, idx: int, slot: str,
                              nick: str, skill_name: str, cd: int) -> None:
        """cd(실제 남은 초) 기준 카운트다운 한 스텝 호출.

        규칙 (표시값 = cd - 1, '초 전'):
          cd == 4 → "3초 전"
          cd == 3 → "2초 전"
          cd == 2 → "1초 전"
          cd == 1 → "곧 시전"
          cd ≤ 0 or > 4: 해당 key 알림 제거.
        """
        if self._alert_overlay is None:
            return
        key = f"cd_{idx}_{slot}"
        if cd is None or cd <= 0 or cd > 4:
            self._alert_overlay.drop_countdown(key)
            return
        if cd == 1:
            msg = f"{nick} {skill_name} 곧 시전"
        else:
            msg = f"{nick} {skill_name} {cd - 1}초 전"
        # cd=1은 1.2s (다음 update가 없을 수 있음), cd>=2는 1.5s.
        dur = 1.2 if cd == 1 else 1.5
        self._alert_overlay.push_countdown(key, msg, duration_sec=dur)

    def _on_overlay_opacity_changed(self, v: int) -> None:
        """투명도 슬라이더 변경 → 두 오버레이에 즉시 반영 + 저장."""
        try:
            pct = int(v)
        except Exception:
            pct = 90
        if pct < 10:
            pct = 10
        elif pct > 100:
            pct = 100
        try:
            self.lbl_overlay_opacity.setText(f"{pct}%")
        except Exception:
            pass
        op = pct / 100.0
        for ov in (self._overlay, self._alert_overlay, self._helper_overlay,
                   self._hpmp_overlay):
            if ov is not None:
                try:
                    ov.set_opacity(op)
                except Exception:
                    pass
        # 스킬범위 오버레이는 자체 투명도 슬라이더 사용 — 여기서 건드리지 않음.
        try:
            self._save_settings()
        except Exception:
            pass

    def _current_overlay_opacity(self) -> float:
        try:
            return max(0.1, min(1.0,
                                int(self.slider_overlay_opacity.value()) / 100.0))
        except Exception:
            return 0.9

    def _on_toggle_overlay(self, state) -> None:
        on = (state == QtCore.Qt.Checked)
        if on:
            if self._overlay is None:
                self._overlay = GameOverlay()
                self._overlay.position_changed.connect(
                    lambda x, y: self._on_overlay_pos_changed("cd", x, y)
                )
            if self._alert_overlay is None:
                self._alert_overlay = SkillAlertOverlay()
                self._alert_overlay.position_changed.connect(
                    lambda x, y: self._on_overlay_pos_changed("alert", x, y)
                )
            if self._helper_overlay is None:
                from .hunter_helper_panel import HunterHelperOverlay
                self._helper_overlay = HunterHelperOverlay()
                self._helper_overlay.position_changed.connect(
                    lambda x, y: self._on_overlay_pos_changed("helper", x, y)
                )
                try:
                    self._helper_overlay.set_subclass(
                        getattr(self, "attacker_subclass", "thief")
                    )
                    self._helper_overlay.set_rank(
                        int(getattr(self, "attacker_rank", 4))
                    )
                except Exception:
                    pass
            if self._hpmp_overlay is None:
                from .healer_status_overlay import HealerStatusOverlay
                self._hpmp_overlay = HealerStatusOverlay()
                self._hpmp_overlay.position_changed.connect(
                    lambda x, y: self._on_overlay_pos_changed("hpmp", x, y)
                )
            # 쿨 복귀 알림 오버레이 참조 주입 — edge(>0→0) 시 push_alert 호출.
            try:
                if self._alert_overlay is not None:
                    self._helper_overlay.set_alert_overlay(self._alert_overlay)
            except Exception:
                pass
            # 생성 직후 현재 슬라이더 값으로 투명도 적용.
            op = self._current_overlay_opacity()
            try:
                self._overlay.set_opacity(op)
                self._alert_overlay.set_opacity(op)
                self._helper_overlay.set_opacity(op)
                self._hpmp_overlay.set_opacity(op)
            except Exception:
                pass
            # msw 창 HWND 바인딩 — 드래그/자동 앵커 둘 다 이 창 client rect
            # 안으로 clamp. 격수는 game region 지정 안 하므로 HWND 필수.
            try:
                from ..input.keys import find_windows_by_process
                tw = str(getattr(self.cfg.input, "target_window", "msw.exe"))
                wins = find_windows_by_process(tw) if tw.lower().endswith(".exe") else []
                hwnd = wins[0] if wins else None
                if hwnd:
                    self._overlay.attach_to_hwnd(hwnd)
                    self._alert_overlay.attach_to_hwnd(hwnd)
                    try:
                        self._helper_overlay.attach_to_hwnd(hwnd)
                    except Exception:
                        pass
                    try:
                        self._hpmp_overlay.attach_to_hwnd(hwnd)
                    except Exception:
                        pass
                    self._append_log(
                        f"[오버레이] {tw} hwnd={hwnd} 바인딩 (드래그 제한)"
                    )
                else:
                    self._append_log(
                        f"[오버레이] {tw} 창 없음 → 드래그 범위 제한 없음"
                    )
            except Exception as e:
                self._append_log(f"[오버레이] hwnd 조회 실패: {e}")
            # 자동 앵커(힐러용 game/map region)도 참고로 넘김.
            game_r = self._regions.get("game")
            map_r = self._regions.get("map")
            self._overlay.set_anchor_regions(game_r, map_r)
            self._alert_overlay.set_anchor_regions(game_r, map_r)
            try:
                self._helper_overlay.set_anchor_regions(game_r, map_r)
            except Exception:
                pass
            try:
                self._hpmp_overlay.set_anchor_regions(game_r, map_r)
            except Exception:
                pass
            # 수동 저장 위치 복원 (격수는 이게 주 경로).
            cd_pos = self._overlay_positions.get("cd")
            al_pos = self._overlay_positions.get("alert")
            hp_pos = self._overlay_positions.get("helper")
            hpmp_pos = self._overlay_positions.get("hpmp")
            if cd_pos:
                self._overlay.set_manual_pos(cd_pos[0], cd_pos[1])
            elif not game_r:
                # 수동 위치도, 자동 앵커도 없음 → 화면 좌상단 기본값.
                self._overlay.move(40, 40)
            if al_pos:
                self._alert_overlay.set_manual_pos(al_pos[0], al_pos[1])
            elif not game_r:
                self._alert_overlay.move(400, 40)
            if hp_pos:
                try:
                    self._helper_overlay.set_manual_pos(hp_pos[0], hp_pos[1])
                except Exception:
                    pass
            elif not game_r:
                try:
                    self._helper_overlay.move(40, 300)
                except Exception:
                    pass
            if hpmp_pos:
                try:
                    self._hpmp_overlay.set_manual_pos(hpmp_pos[0], hpmp_pos[1])
                except Exception:
                    pass
            elif not game_r:
                try:
                    self._hpmp_overlay.move(40, 500)
                except Exception:
                    pass
            for idx, d in self._healer_cooldowns.items():
                try:
                    self._overlay.update_healer(idx, d)
                except Exception:
                    pass
                try:
                    self._hpmp_overlay.update_healer(idx, d)
                except Exception:
                    pass
            # 캐시된 힐러 쿨 스냅샷으로 helper 초기 렌더.
            try:
                self._helper_overlay.update_data(self._healer_cooldowns)
            except Exception:
                pass
            self._overlay.show()
            self._alert_overlay.show()
            # 사냥 도우미/힐러 HP/MP 는 격수 모드에서만 보여줌.
            try:
                if self.role == "attacker":
                    self._helper_overlay.show()
                else:
                    self._helper_overlay.hide()
            except Exception:
                pass
            try:
                if self.role == "attacker":
                    self._hpmp_overlay.show()
                else:
                    self._hpmp_overlay.hide()
            except Exception:
                pass
            # 체크박스 상태 동기 (체크됨 상태로 시작).
            try:
                edit_on = self.chk_overlay_edit.isChecked()
                self._overlay.set_edit_mode(edit_on)
                self._alert_overlay.set_edit_mode(edit_on)
                self._helper_overlay.set_edit_mode(edit_on)
                self._hpmp_overlay.set_edit_mode(edit_on)
            except Exception:
                pass
            self._append_log("[오버레이] ON")
        else:
            if self._overlay is not None:
                self._overlay.hide()
            if self._alert_overlay is not None:
                self._alert_overlay.hide()
            if self._helper_overlay is not None:
                self._helper_overlay.hide()
            if self._hpmp_overlay is not None:
                self._hpmp_overlay.hide()
            self._append_log("[오버레이] OFF")
        # 토글 상태 즉시 영속화 — 재시작 시 자동 복원 보장.
        try:
            self._save_settings()
        except Exception:
            pass

    def _on_toggle_overlay_edit(self, state) -> None:
        on = (state == QtCore.Qt.Checked)
        for ov in (self._overlay, self._alert_overlay, self._helper_overlay,
                   self._hpmp_overlay):
            if ov is None:
                continue
            try:
                ov.set_edit_mode(on)
            except Exception:
                pass
        self._append_log(
            "[오버레이] 위치 편집 ON — 드래그로 이동" if on
            else "[오버레이] 위치 편집 OFF — 입력 통과 복귀"
        )

    def _on_overlay_pos_changed(self, kind: str, x: int, y: int) -> None:
        self._overlay_positions[kind] = (int(x), int(y))
        # 강제종료/크래시 대비 드래그 끝날 때마다 디스크 즉시 flush.
        try:
            self._save_settings()
        except Exception:
            pass
        self._append_log(f"[오버레이] {kind} 위치 저장 ({x},{y})")

    def _on_pause_toggle(self) -> None:
        """ARM 체크박스와 동일 플래그를 토글."""
        if self.role != "healer":
            return
        cur = self.chk_arm.isChecked()
        self.chk_arm.setChecked(not cur)  # stateChanged → _on_arm 재사용.

    def _on_pick_cd_region(self) -> None:
        """전체화면 오버레이 띄워 쿨 영역 드래그 선택."""
        if self._region_picker is not None:
            try:
                self._region_picker.close()
            except Exception:
                pass
        self._region_picker = RegionPicker()
        self._region_picker.region_selected.connect(self._on_cd_region_selected)
        self._region_picker.cancelled.connect(
            lambda: self._append_log("[쿨 영역] 취소")
        )
        self._region_picker.showFullScreen()
        self._append_log("[쿨 영역] 드래그로 선택 (ESC=취소)")

    def _on_cd_region_selected(self, x: int, y: int, w: int, h: int) -> None:
        self.cfg.cooldown.region_x = int(x)
        self.cfg.cooldown.region_y = int(y)
        self.cfg.cooldown.region_w = int(w)
        self.cfg.cooldown.region_h = int(h)
        self.lbl_cd_region.setText(f"쿨 영역: ({x},{y}) {w}×{h}")
        self._append_log(f"[쿨 영역] 설정 ({x},{y}) {w}×{h}")
        if self.worker and hasattr(self.worker, "set_cooldown_region"):
            try:
                self.worker.set_cooldown_region(x, y, w, h)
            except Exception as e:
                self._append_log(f"[쿨 영역] 워커 반영 실패: {e}")
        self._refresh_region_overlay()

    def _on_clear_cd_region(self) -> None:
        self.cfg.cooldown.region_x = -1
        self.cfg.cooldown.region_y = -1
        self.cfg.cooldown.region_w = 0
        self.cfg.cooldown.region_h = 0
        self.lbl_cd_region.setText("쿨 영역: 미지정")
        self._append_log("[쿨 영역] 해제")
        if self.worker and hasattr(self.worker, "clear_cooldown_region"):
            try:
                self.worker.clear_cooldown_region()
            except Exception:
                pass
        self._refresh_region_overlay()

    def _on_pick_nick_region(self) -> None:
        """전체화면 오버레이 띄워 닉네임 영역 드래그 선택."""
        if self._region_picker is not None:
            try:
                self._region_picker.close()
            except Exception:
                pass
        self._region_picker = RegionPicker()
        self._region_picker.region_selected.connect(
            self._on_nick_region_selected
        )
        self._region_picker.cancelled.connect(
            lambda: self._append_log("[닉 영역] 취소")
        )
        self._region_picker.showFullScreen()
        self._append_log("[닉 영역] 드래그로 선택 (ESC=취소)")

    def _on_nick_region_selected(
        self, x: int, y: int, w: int, h: int
    ) -> None:
        self.cfg.cooldown.nick_region_x = int(x)
        self.cfg.cooldown.nick_region_y = int(y)
        self.cfg.cooldown.nick_region_w = int(w)
        self.cfg.cooldown.nick_region_h = int(h)
        self.lbl_nick_region.setText(f"닉 영역: ({x},{y}) {w}×{h}")
        self._append_log(f"[닉 영역] 설정 ({x},{y}) {w}×{h}")
        if self.worker and hasattr(self.worker, "set_nick_region"):
            try:
                self.worker.set_nick_region(x, y, w, h)
            except Exception as e:
                self._append_log(f"[닉 영역] 워커 반영 실패: {e}")
        self._refresh_region_overlay()

    def _on_clear_nick_region(self) -> None:
        self.cfg.cooldown.nick_region_x = -1
        self.cfg.cooldown.nick_region_y = -1
        self.cfg.cooldown.nick_region_w = 0
        self.cfg.cooldown.nick_region_h = 0
        self.lbl_nick_region.setText("닉 영역: 미지정")
        self._append_log("[닉 영역] 해제")
        if self.worker and hasattr(self.worker, "clear_nick_region"):
            try:
                self.worker.clear_nick_region()
            except Exception:
                pass
        self._refresh_region_overlay()

    def _on_pick_buff_region(self) -> None:
        """파력무참 버프 지속시간 영역 드래그 선택."""
        if self._region_picker is not None:
            try:
                self._region_picker.close()
            except Exception:
                pass
        self._region_picker = RegionPicker()
        self._region_picker.region_selected.connect(
            self._on_buff_region_selected
        )
        self._region_picker.cancelled.connect(
            lambda: self._append_log("[버프 영역] 취소")
        )
        self._region_picker.showFullScreen()
        self._append_log("[버프 영역] 드래그로 선택 (ESC=취소)")

    def _on_buff_region_selected(
        self, x: int, y: int, w: int, h: int
    ) -> None:
        self.cfg.cooldown.buff_region_x = int(x)
        self.cfg.cooldown.buff_region_y = int(y)
        self.cfg.cooldown.buff_region_w = int(w)
        self.cfg.cooldown.buff_region_h = int(h)
        self.lbl_buff_region.setText(f"버프 영역: ({x},{y}) {w}×{h}")
        self._append_log(f"[버프 영역] 설정 ({x},{y}) {w}×{h}")
        if self.worker and hasattr(self.worker, "set_buff_region"):
            try:
                self.worker.set_buff_region(x, y, w, h)
            except Exception as e:
                self._append_log(f"[버프 영역] 워커 반영 실패: {e}")
        self._refresh_region_overlay()

    def _on_clear_buff_region(self) -> None:
        self.cfg.cooldown.buff_region_x = -1
        self.cfg.cooldown.buff_region_y = -1
        self.cfg.cooldown.buff_region_w = 0
        self.cfg.cooldown.buff_region_h = 0
        self.lbl_buff_region.setText("버프 영역: 미지정")
        self._append_log("[버프 영역] 해제")
        if self.worker and hasattr(self.worker, "clear_buff_region"):
            try:
                self.worker.clear_buff_region()
            except Exception:
                pass
        self._refresh_region_overlay()

    def _on_test_xp_ocr(self) -> None:
        """경험치 영역을 한 번 캡처해 OCR 시도 → 결과 다이얼로그 표시.

        워커 실행 여부와 무관하게 동작 (임시 XpOcr 인스턴스).
        """
        xp_r = self._regions.get("xp")
        if not xp_r:
            QtWidgets.QMessageBox.information(
                self, "경험치 OCR 확인",
                "경험치 영역을 먼저 지정하세요.\n\n설정 탭 → '경험치' 버튼 → 드래그."
            )
            return
        # 캡처: 듀얼 모니터에서 mss.monitors[1] 이 secondary 로 잡히는 이슈가
        # 있어 monitor_index=0 (가상 데스크톱 전체) 사용. region 절대좌표가 어느
        # 모니터에 있든 일관된 좌표계로 crop.
        try:
            from ..capture.screen import Grabber
            g = Grabber(monitor_index=0)
            frame = g.grab()
            origin = (int(g.mon["left"]), int(g.mon["top"]))
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self, "경험치 OCR 확인", f"화면 캡처 실패: {e}"
            )
            return
        # 임시 XpOcr 인스턴스로 일회성 OCR.
        try:
            from ..vision.xp_ocr import XpOcr
            ocr = XpOcr()
            ocr.set_region(*xp_r)
            res = ocr.test_once(frame, origin)
            try:
                ocr.stop()
            except Exception:
                pass
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self, "경험치 OCR 확인", f"OCR 실행 실패: {e}"
            )
            return
        rx, ry, rw, rh = xp_r
        cs = res.get("crop_shape")
        cs_txt = f"{cs[1]}x{cs[0]}" if cs else "-"
        dbg = res.get("debug_path", "")
        dbg_line = f"\ndebug: {dbg}" if dbg else ""
        if res.get("ok"):
            xp_val = int(res.get("xp") or 0)
            msg = (
                f"✓ 인식 성공\n\n"
                f"XP: {xp_val:,}\n"
                f"원문: '{res.get('raw_text','')}'\n\n"
                f"영역: ({rx},{ry}) {rw}×{rh}\n"
                f"crop: {cs_txt}{dbg_line}"
            )
            self._append_log(f"[XP OCR] OK xp={xp_val} dbg={dbg}")
            QtWidgets.QMessageBox.information(self, "경험치 OCR 확인", msg)
        else:
            msg = (
                f"✗ 인식 실패\n\n"
                f"진단: {res.get('diag','')}\n"
                f"원문: '{res.get('raw_text','')}'\n\n"
                f"영역: ({rx},{ry}) {rw}×{rh}\n"
                f"crop: {cs_txt}{dbg_line}"
            )
            self._append_log(
                f"[XP OCR] FAIL diag={res.get('diag','')} "
                f"text={res.get('raw_text','')!r} dbg={dbg}"
            )
            QtWidgets.QMessageBox.warning(self, "경험치 OCR 확인", msg)

    def _on_test_hpmp(self) -> None:
        """HP/MP 영역을 한 번 캡처해 OCR 결과 다이얼로그에 숫자로 표시.

        원시 OCR 텍스트 + 분리된 current/max + pct 를 모두 보여줌. 영역/max
        값이 올바른지 사용자가 직접 검증.
        """
        hp_r = self._regions.get("hp")
        mp_r = self._regions.get("mp")
        if not hp_r and not mp_r:
            QtWidgets.QMessageBox.information(
                self, "HP/MP 확인",
                "체력/마력 영역 중 하나라도 먼저 지정하세요.\n\n"
                "설정 탭 → '체력' / '마력' 버튼 → 드래그."
            )
            return
        # 듀얼 모니터 환경에서 mss.monitors[1] 이 secondary 를 리턴하는 경우가
        # 있어 region 이 frame 밖으로 빠지는 이슈 (origin=(1920,0), region.x=1111
        # → local x=-809). monitor_index=0 은 mss 의 "전체 가상 데스크톱" =
        # 모든 모니터 union. region 절대좌표가 어느 모니터에 있든 일관된 좌표계.
        try:
            from ..capture.screen import Grabber
            g = Grabber(monitor_index=0)
            frame = g.grab()
            origin = (int(g.mon["left"]), int(g.mon["top"]))
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self, "HP/MP 확인", f"화면 캡처 실패: {e}"
            )
            return
        try:
            from ..vision.hpmp import HpMpReader
            reader = HpMpReader(log_cb=self._append_log)
            if hp_r:
                reader.set_hp_region(*hp_r)
            if mp_r:
                reader.set_mp_region(*mp_r)
            reader.set_hp_max(int(self.hp_max_spin.value()))
            reader.set_mp_max(int(self.mp_max_spin.value()))
            res = reader.test_once(frame, origin, save_debug=True)
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self, "HP/MP 확인", f"HP/MP OCR 실행 실패: {e}"
            )
            return
        lines = []
        for kind, label in (("hp", "HP"), ("mp", "MP")):
            d = res.get(kind) or {}
            region = d.get("region")
            if region is None:
                lines.append(f"{label}: 영역 미지정")
                continue
            rx, ry, rw, rh = region
            if d.get("ok"):
                cur = int(d.get("cur", -1))
                mx = int(d.get("max", 0))
                pct = int(d.get("pct", -1))
                raw = str(d.get("raw", ""))
                if mx > 0 and pct >= 0:
                    main_line = (
                        f"{label}: {cur} / {mx}  ({pct}%)   "
                        f"영역=({rx},{ry}) {rw}×{rh}"
                    )
                else:
                    main_line = (
                        f"{label}: {cur}  (max 미입력)   "
                        f"영역=({rx},{ry}) {rw}×{rh}"
                    )
                lines.append(main_line)
                lines.append(f"    raw='{raw}'")
            else:
                diag = str(d.get("diag", ""))
                raw = str(d.get("raw", ""))
                lines.append(
                    f"{label}: 인식 실패 ({diag})   "
                    f"영역=({rx},{ry}) {rw}×{rh}"
                )
                if raw:
                    lines.append(f"    raw='{raw}'")
            dbg = d.get("debug_path")
            if dbg:
                lines.append(f"    crop: {dbg}")
        msg = "\n".join(lines)
        self._append_log(
            f"[HP/MP 확인] hp={res.get('hp', {}).get('cur', -1)} "
            f"mp={res.get('mp', {}).get('cur', -1)}"
        )
        QtWidgets.QMessageBox.information(self, "HP/MP 확인", msg)

    def _on_hp_max_changed(self, v: int) -> None:
        """최대 HP 변경 → cfg + 실행 중이면 워커에 즉시 반영."""
        try:
            self.cfg.cooldown.hp_max = int(v)
        except Exception:
            pass
        if self.worker is not None and hasattr(self.worker, "set_hp_max"):
            try:
                self.worker.set_hp_max(int(v))
            except Exception:
                pass
        try:
            self._save_settings()
        except Exception:
            pass

    def _on_mp_max_changed(self, v: int) -> None:
        try:
            self.cfg.cooldown.mp_max = int(v)
        except Exception:
            pass
        if self.worker is not None and hasattr(self.worker, "set_mp_max"):
            try:
                self.worker.set_mp_max(int(v))
            except Exception:
                pass
        try:
            self._save_settings()
        except Exception:
            pass

    def _on_test_block_a(self) -> None:
        """F11/테스트 버튼 — 스킬 설정 체크박스 따라 A 단독 or A+B 실행.

        Patch 2.12: 스킬 설정 다이얼로그 `chk_f11_ab_combined` 로 분기.
          - ON  → `run_block_ab_combined` (A → B 연속).
          - OFF → `run_block_a_test` (A 단독).

        워커 실행 중이면 NumLockCycler 를 전달해 실제 slots 언락 + _locked
        동기화. 아니면 DEFAULT_SLOTS (NUMPAD1/2/3) 로 fallback.
        """
        cycler = None
        try:
            if self.worker is not None:
                cycler = getattr(self.worker, "_cycler", None)
        except Exception:
            cycler = None
        combined = True
        try:
            chk = getattr(self.skill_dlg, "chk_f11_ab_combined", None)
            if chk is not None:
                combined = bool(chk.isChecked())
        except Exception:
            combined = True
        # 워커 실행 중이면 방향키 release_all 도 주입 (이동 간섭 차단).
        key_release_fn = None
        try:
            if self.worker is not None:
                _keys_ref = getattr(self.worker, "_keys", None)
                if _keys_ref is not None:
                    key_release_fn = _keys_ref.release_all
        except Exception:
            key_release_fn = None
        try:
            if combined:
                from ..input.target_sequence import run_block_ab_combined
                run_block_ab_combined(
                    cycler=cycler,
                    log_fn=self._append_log,
                    key_release_fn=key_release_fn,
                )
                self._append_log("[TEST] 블록 A+B 완료")
            else:
                from ..input.target_sequence import run_block_a_test
                run_block_a_test(cycler=cycler, log_fn=self._append_log)
                self._append_log("[TEST] 블록 A 완료 (B 자동 실행 OFF)")
        except Exception as e:
            mode = "A+B" if combined else "A"
            self._append_log(f"[TEST] 블록 {mode} 실패: {e}")

    def _on_test_block_b(self) -> None:
        """블록 B (NumLock 해제→ESC×3→TAB×2 격수 복귀) 1회 실행.

        워커 실행 중이면 NumLockCycler 도 해제. 아니면 키 입력만.
        """
        cycler = None
        try:
            if self.worker is not None:
                cycler = getattr(self.worker, "_cycler", None)
        except Exception:
            cycler = None
        try:
            from ..input.target_sequence import run_block_b_test
            run_block_b_test(cycler=cycler, log_fn=self._append_log)
            self._append_log("[TEST] 블록 B 완료")
        except Exception as e:
            self._append_log(f"[TEST] 블록 B 실패: {e}")

    def _setup_global_hotkeys(self) -> None:
        """F11=블록A, F12=블록B 전역 단축키 등록.

        msw.exe 포그라운드에서도 눌러지므로 힐러 봇 운영 중에도 수동 테스트 가능.
        콜백은 hotkey 스레드에서 날아오므로 signal 을 통해 UI 스레드로 queue.
        """
        from ..input.global_hotkeys import (
            GlobalHotkeys, VK_F11, VK_F12,
            MOD_CONTROL, MOD_SHIFT,
        )
        # 이미 초기화돼 있으면 스킵 (재진입 방지).
        if getattr(self, "_hotkey_mgr", None) is not None:
            return
        self._hotkey_mgr = GlobalHotkeys(log_fn=self._append_log)
        # signal → slot 연결.
        try:
            self.hotkey_fired.connect(self._on_hotkey_fired)
        except Exception:
            pass
        # F11/F12 는 NVIDIA/Discord/Xbox Game Bar 등이 선점하는 경우가 많음.
        # 실패 시 Ctrl+F11/12 → Shift+F11/12 순서로 폴백.
        # Patch 2.23 (2026-04-20): MOD_SHIFT alternate 제거.
        # Shift+F11/F12 를 RegisterHotKey 로 등록하면 Windows 가 전역 Shift
        # modifier 를 추적하다가 msw 게임창의 GetAsyncKeyState/DirectInput
        # 과 경합해 Shift+X 조합이 소비되는 사례 보고됨. Korean IME 환경에서
        # 특히 잦다. Ctrl+F11/F12 까지만 fallback 유지.
        self._hotkey_mgr.register(
            "block_a", VK_F11,
            callback=lambda: self.hotkey_fired.emit("block_a"),
            alternates=[(MOD_CONTROL, VK_F11)],
        )
        self._hotkey_mgr.register(
            "block_b", VK_F12,
            callback=lambda: self.hotkey_fired.emit("block_b"),
            alternates=[(MOD_CONTROL, VK_F12)],
        )
        self._hotkey_mgr.start()
        self._append_log("[HOTKEY] F11=블록A+B(통합), F12=블록B 등록 시도 "
                         "(실패 시 Ctrl+F11/F12 폴백)")

    def _on_hotkey_fired(self, name: str) -> None:
        """hotkey 스레드 → signal → UI 스레드에서 실제 실행."""
        if name == "block_a":
            combined = True
            try:
                chk = getattr(self.skill_dlg, "chk_f11_ab_combined", None)
                if chk is not None:
                    combined = bool(chk.isChecked())
            except Exception:
                combined = True
            self._append_log(
                f"[HOTKEY] F11 → 블록 {'A+B' if combined else 'A'}"
            )
            self._on_test_block_a()
        elif name == "block_b":
            self._append_log("[HOTKEY] F12 → 블록 B")
            self._on_test_block_b()

    def _on_pick_region(self, kind: str, label: str) -> None:
        """게임/맵/좌표/경험치/체력/마력 영역 드래그 선택."""
        if self._region_picker is not None:
            try:
                self._region_picker.close()
            except Exception:
                pass
        self._region_picker = RegionPicker(label)
        self._region_picker.region_selected.connect(
            lambda x, y, w, h, kk=kind, lb=label:
                self._on_region_selected(kk, lb, x, y, w, h)
        )
        self._region_picker.cancelled.connect(
            lambda lb=label: self._append_log(f"[{lb} 영역] 취소")
        )
        self._region_picker.showFullScreen()
        self._append_log(f"[{label} 영역] 드래그로 선택 (ESC=취소)")

    def _on_region_selected(
        self, kind: str, label: str, x: int, y: int, w: int, h: int
    ) -> None:
        self._regions[kind] = (int(x), int(y), int(w), int(h))
        btn = self._region_buttons.get(kind)
        if btn is not None:
            btn.setText(f"{label} ✓")
        self._append_log(f"[{label} 영역] 설정 ({x},{y}) {w}×{h}")
        # hp/mp 영역은 cfg.cooldown.hp_region_*/mp_region_* 에도 동기화
        # (워커 재기동 시 cfg에서 읽어가기 위함).
        self._sync_region_to_cfg(kind)
        self._apply_region_to_worker(kind)
        self._refresh_region_overlay()
        self._refresh_overlay_anchors()

    def _on_clear_region(self, kind: str) -> None:
        self._regions.pop(kind, None)
        btn = self._region_buttons.get(kind)
        lb = self._region_labels_kr.get(kind, kind)
        if btn is not None:
            btn.setText(lb)
        # hp/mp 해제 시 cfg 도 리셋.
        self._sync_region_to_cfg(kind, clear=True)
        self._apply_region_to_worker(kind, clear=True)
        self._refresh_region_overlay()
        self._refresh_overlay_anchors()
        self._append_log(f"[{lb} 영역] 해제")

    def _sync_region_to_cfg(self, kind: str, clear: bool = False) -> None:
        """_regions[hp/mp] → cfg.cooldown.hp_region_*/mp_region_* 동기화.

        다른 kind (game/map/coord/xp) 는 cfg.cooldown 에 매핑 안 됨 → no-op.
        """
        if kind not in ("hp", "mp"):
            return
        cd = self.cfg.cooldown
        if clear or kind not in self._regions:
            setattr(cd, f"{kind}_region_x", -1)
            setattr(cd, f"{kind}_region_y", -1)
            setattr(cd, f"{kind}_region_w", 0)
            setattr(cd, f"{kind}_region_h", 0)
            return
        x, y, w, h = self._regions[kind]
        setattr(cd, f"{kind}_region_x", int(x))
        setattr(cd, f"{kind}_region_y", int(y))
        setattr(cd, f"{kind}_region_w", int(w))
        setattr(cd, f"{kind}_region_h", int(h))

    def _refresh_overlay_anchors(self) -> None:
        """game/map region 변경 시 네 오버레이 위치·스케일 재적용."""
        game_r = self._regions.get("game")
        map_r = self._regions.get("map")
        for ov in (self._overlay, self._alert_overlay, self._helper_overlay,
                   self._hpmp_overlay, self._skill_range_overlay):
            if ov is None:
                continue
            try:
                ov.set_anchor_regions(game_r, map_r)
            except Exception:
                pass

    def _apply_region_to_worker(
        self, kind: str, clear: bool = False
    ) -> None:
        """영역을 해당 워커 setter로 실시간 반영. map/coord는 표시용.
        격수 워커는 xp/hp/mp 영역만 허용 (2026-04-20: HP/MP OCR 양측 사용).
        """
        if not self.worker:
            return
        if self.role == "attacker" and kind not in ("xp", "hp", "mp"):
            return
        setter_map = {
            "game": ("set_game_region", "clear_game_region"),
            "xp":   ("set_xp_region",   "clear_xp_region"),
            "hp":   ("set_hp_region",   "clear_hp_region"),
            "mp":   ("set_mp_region",   "clear_mp_region"),
        }
        if kind not in setter_map:
            return
        set_name, clr_name = setter_map[kind]
        try:
            if clear or kind not in self._regions:
                if hasattr(self.worker, clr_name):
                    getattr(self.worker, clr_name)()
            else:
                x, y, w, h = self._regions[kind]
                if hasattr(self.worker, set_name):
                    getattr(self.worker, set_name)(x, y, w, h)
        except Exception as e:
            self._append_log(f"[{kind} 영역] 워커 반영 실패: {e}")

    def _apply_all_regions_to_worker(self) -> None:
        """워커 기동 직후 저장된 6개 영역 일괄 반영."""
        for k in list(self._regions.keys()):
            self._apply_region_to_worker(k)

    def _on_toggle_region_overlay(self, state) -> None:
        on = (state == QtCore.Qt.Checked)
        if on:
            if self._region_overlay is None:
                self._region_overlay = RegionOverlay()
            self._refresh_region_overlay()
            self._region_overlay.showFullScreen()
        else:
            if self._region_overlay is not None:
                self._region_overlay.hide()

    def _refresh_region_overlay(self) -> None:
        if self._region_overlay is None:
            return
        all_regions = dict(self._regions)
        cd = self.cfg.cooldown
        if int(cd.region_w) > 0 and int(cd.region_x) >= 0:
            all_regions["cd"] = (
                int(cd.region_x), int(cd.region_y),
                int(cd.region_w), int(cd.region_h),
            )
        nx = int(getattr(cd, "nick_region_x", -1))
        nw = int(getattr(cd, "nick_region_w", 0))
        if nw > 0 and nx >= 0:
            all_regions["nick"] = (
                nx, int(getattr(cd, "nick_region_y", 0)),
                nw, int(getattr(cd, "nick_region_h", 0)),
            )
        bx = int(getattr(cd, "buff_region_x", -1))
        bw = int(getattr(cd, "buff_region_w", 0))
        if bw > 0 and bx >= 0:
            all_regions["buff"] = (
                bx, int(getattr(cd, "buff_region_y", 0)),
                bw, int(getattr(cd, "buff_region_h", 0)),
            )
        self._region_overlay.set_regions(all_regions)

    def _on_healer_idx(self, v: int) -> None:
        self.cfg.net.healer_idx = int(v)
        self._append_log(f"[idx] 힐러 인덱스 = {v}")
        # 상시 listener도 my_idx 갱신 (재시작 없이).
        if self._ctrl_listener is not None:
            try:
                self._ctrl_listener.set_my_idx(int(v))
            except Exception:
                pass

    def _on_arm(self, state):
        if self.worker and hasattr(self.worker, "armed"):
            self.worker.armed = (state == QtCore.Qt.Checked)
            self._append_log(f"ARM={'ON' if self.worker.armed else 'OFF'}")
        try:
            self._set_run_status(self.worker is not None and self.worker.isRunning())
        except Exception:
            pass

    def _on_own_cooldown(self, skills: dict) -> None:
        """격수 본인 쿨 OCR 결과 → 사냥 도우미 오버레이 반영."""
        try:
            if self._helper_overlay is not None:
                self._helper_overlay.update_own_cds(dict(skills or {}))
        except Exception:
            pass

    def _on_subclass_change(self, _checked=False):
        self.attacker_subclass = (
            "thief" if self.rb_thief.isChecked() else "warrior"
        )
        try:
            if self._helper_overlay is not None:
                self._helper_overlay.set_subclass(self.attacker_subclass)
                self._helper_overlay.set_rank(self.attacker_rank)
        except Exception:
            pass
        try:
            if self._skill_range_overlay is not None:
                self._skill_range_overlay.set_subclass(self.attacker_subclass)
                self._skill_range_overlay.set_rank(self.attacker_rank)
        except Exception:
            pass
        self._reinject_own_skill_names()
        try:
            self._save_settings()
        except Exception:
            pass
        try:
            self._append_log(
                f"[격수 서브클래스] {self.attacker_subclass}"
            )
        except Exception:
            pass

    def _on_rank_change(self, _checked=False):
        # 3개 라디오 중 하나만 체크 — toggled 시그널이 양쪽에서 튀므로 실제 체크된
        # 하나만 반영. 모든 라디오가 off 순간의 잡음은 무시.
        if self.rb_rank2.isChecked():
            r = 2
        elif self.rb_rank3.isChecked():
            r = 3
        elif self.rb_rank4.isChecked():
            r = 4
        else:
            return
        if r == self.attacker_rank:
            return
        self.attacker_rank = r
        try:
            if self._helper_overlay is not None:
                self._helper_overlay.set_rank(r)
        except Exception:
            pass
        try:
            if self._skill_range_overlay is not None:
                self._skill_range_overlay.set_rank(r)
        except Exception:
            pass
        self._reinject_own_skill_names()
        try:
            self._save_settings()
        except Exception:
            pass
        try:
            self._append_log(f"[격수 승급] {r}차")
        except Exception:
            pass

    def _reinject_own_skill_names(self) -> None:
        """현재 서브클래스 + 승급 기준으로 worker의 OCR 타겟 스킬 리스트 재주입."""
        try:
            if (self.worker is None
                    or not hasattr(self.worker, "set_own_skill_names")):
                return
            from .hunter_helper_panel import get_rank_skills
            names = [
                nm for (nm, _cd)
                in get_rank_skills(self.attacker_subclass, self.attacker_rank)
            ]
            self.worker.set_own_skill_names(names)
        except Exception:
            pass

    def _set_run_status(self, running: bool) -> None:
        """시작/정지 pill 라벨 갱신. 일시정지(ARM OFF) 상태면 경고색."""
        lbl = getattr(self, "lbl_run_status", None)
        if lbl is None:
            return
        if not running:
            lbl.setText("정지됨")
            lbl.setObjectName("pillIdle")
        else:
            armed = True
            try:
                armed = bool(self.chk_arm.isChecked())
            except Exception:
                pass
            if armed:
                lbl.setText("● 실행 중")
                lbl.setObjectName("pillOk")
            else:
                lbl.setText("‖ 일시정지")
                lbl.setObjectName("pillWarn")
        # objectName 바꾼 뒤 스타일 재적용 강제.
        lbl.style().unpolish(lbl)
        lbl.style().polish(lbl)
        lbl.update()

    def _on_follow_only(self, state):
        """따라가기 토글. 스킬 OFF + 경량 모드(빨탭 YOLO + cooldown/buff/hp/mp
        OCR 정지). 유지: coord/맵 OCR(이동) + 경험치 OCR + 격수 좌표 추종.
        저사양 대응."""
        on = (state == QtCore.Qt.Checked)
        if self.worker:
            if hasattr(self.worker, "follow_only"):
                self.worker.follow_only = on
            if hasattr(self.worker, "follow_light"):
                self.worker.follow_light = on
        self._append_log(f"FOLLOW(경량)={'ON' if on else 'OFF'}")

    def _on_toggle_low_spec(self, state) -> None:
        """저사양 모드 토글. YOLO 주기/해상도, 프리뷰 주기, OCR poll 일괄 조정.
        워커 실행 중이면 실시간 반영, 아니면 cfg에 저장해 다음 start에 적용.
        """
        on = (state == QtCore.Qt.Checked)
        try:
            # cfg 저장 (설정 파일 반영).
            setattr(self.cfg.vision, "low_spec", bool(on))
        except Exception:
            pass
        # YOLO 주기 N, imgsz는 HealerWorker가 지원하는 속성에 직접 주입.
        if self.worker is not None:
            try:
                if on:
                    if hasattr(self.worker, "yolo_every_n"):
                        self.worker.yolo_every_n = max(
                            2, int(getattr(self.worker, "yolo_every_n", 1) or 1)
                        )
                    if hasattr(self.worker, "yolo_imgsz"):
                        self.worker.yolo_imgsz = 480
                    if hasattr(self.worker, "preview_hz_limit"):
                        self.worker.preview_hz_limit = 5.0
                    if hasattr(self.worker, "ocr_poll_sec"):
                        self.worker.ocr_poll_sec = 3.0
                    if hasattr(self.worker, "crop_capture_to_game"):
                        self.worker.crop_capture_to_game = True
                else:
                    if hasattr(self.worker, "yolo_every_n"):
                        try:
                            self.worker.yolo_every_n = int(self.yn_spin.value())
                        except Exception:
                            self.worker.yolo_every_n = 1
                    if hasattr(self.worker, "yolo_imgsz"):
                        self.worker.yolo_imgsz = int(
                            getattr(self.cfg.vision, "imgsz", 640)
                        )
                    if hasattr(self.worker, "preview_hz_limit"):
                        self.worker.preview_hz_limit = 0.0
                    if hasattr(self.worker, "ocr_poll_sec"):
                        self.worker.ocr_poll_sec = 0.0  # 0=기본(워커 내부 초기값 유지).
                    if hasattr(self.worker, "crop_capture_to_game"):
                        self.worker.crop_capture_to_game = False
            except Exception as e:
                self._append_log(f"[저사양] 실시간 적용 실패: {e}")
        self._append_log(f"[저사양 모드] {'ON' if on else 'OFF'}")

    def _on_conf(self, v):
        c = v / 100.0
        self.conf_label.setText(f"YOLO conf: {c:.2f}")
        if self.worker:
            try:
                self.worker.yolo_conf = c
            except Exception:
                pass

    def _on_minw(self, v):
        if self.worker and hasattr(self.worker, "min_w"):
            self.worker.min_w = v

    def _on_minh(self, v):
        if self.worker and hasattr(self.worker, "min_h"):
            self.worker.min_h = v

    def _on_tol(self, v):
        if self.worker and hasattr(self.worker, "coord_tol"):
            self.worker.coord_tol = v

    def _on_yn(self, v):
        if self.worker and hasattr(self.worker, "yolo_every_n"):
            self.worker.yolo_every_n = v

    def _on_skill_toggle(self, name: str):
        on = self.skill_chks[name].isChecked()
        if self.worker and hasattr(self.worker, "set_skill_enabled"):
            self.worker.set_skill_enabled(name, on)
        self._append_log(f"[SKILL] {name} = {'ON' if on else 'OFF'}")
        # 스케줄러 전담 스킬은 NumLock 싸이클과 무관 → 갱신 불필요.
        if name not in (
            "파력무참", "백호의희원", "백호의희원첨",
            "공력증강", "부활",
        ):
            self._on_cycle_changed()

    def _on_parlyuk_offset(self, v: int):
        if self.worker and hasattr(self.worker, "set_parlyuk_offset"):
            self.worker.set_parlyuk_offset(float(v))

    def _on_parlyuk_maps(self, text: str):
        """파력무참 시전 굴 설정 → 워커 반영 (2026-06-10)."""
        if self.worker and hasattr(self.worker, "set_parlyuk_maps"):
            self.worker.set_parlyuk_maps(text)

    def _on_mainheal_vk_changed(self, n: int):
        """메인힐 NumPad 번호 변경 — 워커 skill_vks 갱신 + 싸이클 재계산.

        메인힐은 자힐 burst 에도 쓰이므로 skill_vks["메인힐"] 도 갱신 필요.
        """
        vk = self._numpad_vk(n)
        if self.worker and hasattr(self.worker, "set_skill_vk"):
            try:
                # 기존 set_skill_vk 가 "메인힐" 키를 인식하도록 전달.
                self.worker.set_skill_vk("메인힐", vk)
            except Exception:
                pass
        self._append_log(f"[SLOT] 메인힐 = NumPad{n}")
        # NumLock 싸이클 VK 재계산 (메인힐은 싸이클 슬롯이므로).
        self._on_cycle_changed()

    def _on_gyoungryeok_thr_changed(self, v: int):
        """공력증강 MP% 임계치 변경 → 워커 인스턴스 변수 갱신."""
        if self.worker and hasattr(self.worker, "gyoungryeok_mp_thr"):
            try:
                self.worker.gyoungryeok_mp_thr = int(v)
            except Exception:
                pass
        self._append_log(f"[THR] 공력증강 MP% = {v}")

    @staticmethod
    def _numpad_vk(n: int) -> int:
        """스피너 값 0~9 → VK_NUMPAD0~9."""
        return 0x60 + max(0, min(9, int(n)))

    def _on_skill_vk(self, name: str, n: int):
        vk = self._numpad_vk(n)
        if self.worker and hasattr(self.worker, "set_skill_vk"):
            self.worker.set_skill_vk(name, vk)
        self._append_log(f"[SLOT] {name} = NumPad{n}")
        # 파력무참 외는 싸이클 VK도 갱신.
        if name != "파력무참":
            self._on_cycle_changed()

    def _open_skill_dialog(self):
        self.skill_dlg.show()
        self.skill_dlg.raise_()
        self.skill_dlg.activateWindow()

    def _open_param_dialog(self):
        self.param_dlg.show()
        self.param_dlg.raise_()
        self.param_dlg.activateWindow()

    def _open_net_dialog(self):
        self.net_dlg.show()
        self.net_dlg.raise_()
        self.net_dlg.activateWindow()

    def _open_hunt_report_dialog(self):
        try:
            from .hunt_report_dialog import HuntReportDialog
        except Exception as e:
            self._append_log(f"[사냥 리포트] 모듈 로드 실패: {e}")
            return
        if self._hunt_report_dlg is None:
            self._hunt_report_dlg = HuntReportDialog(self)
        self._hunt_report_dlg.reload()
        self._hunt_report_dlg.show()
        self._hunt_report_dlg.raise_()
        self._hunt_report_dlg.activateWindow()

    def _tick_overlay_visibility(self) -> None:
        """Overlay 실제 show/hide 를 포그라운드·msw 상태 기준으로 결정.

        규칙:
          - role 이 attacker 아님 → 무조건 숨김 (힐러 UI 는 오버레이 불필요).
          - chk_overlay 미체크 → 무조건 숨김.
          - msw.exe 창 없음 → 숨김.
          - msw 최소화(IsIconic) → 숨김.
          - 현재 포그라운드 PID 가 msw.exe 도 아니고 내 프로세스도 아님 → 숨김
            (Chrome, Discord 등 다른 앱을 볼 땐 overlay 가 방해 안 됨).
          - 그 외 → 보이기. StaysOnTop 이 걸려 있어 본 UI 창 위에도 뜬다.
        """
        try:
            if getattr(self, "role", "healer") != "attacker":
                self._set_overlays_visible(False)
                return
            if not self.chk_overlay.isChecked():
                self._set_overlays_visible(False)
                return
            import ctypes as _ct
            from ..input.keys import find_windows_by_process
            from ..capture.screen import user32 as _u32
            tw = str(getattr(self.cfg.input, "target_window", "msw.exe"))
            wins = find_windows_by_process(tw) if tw.lower().endswith(".exe") else []
            if not wins:
                self._set_overlays_visible(False)
                return
            msw_hwnd = int(wins[0])
            try:
                if _u32.IsIconic(msw_hwnd):
                    self._set_overlays_visible(False)
                    return
            except Exception:
                pass
            # 포그라운드 PID 확인.
            fg = _u32.GetForegroundWindow()
            show = True
            if fg:
                try:
                    fg_pid = _ct.c_ulong(0)
                    _u32.GetWindowThreadProcessId(fg, _ct.byref(fg_pid))
                    fg_pid_v = int(fg_pid.value)
                    my_pid = int(_ct.windll.kernel32.GetCurrentProcessId())
                    msw_pid = _ct.c_ulong(0)
                    _u32.GetWindowThreadProcessId(msw_hwnd, _ct.byref(msw_pid))
                    msw_pid_v = int(msw_pid.value)
                    if fg_pid_v not in (my_pid, msw_pid_v):
                        show = False
                except Exception:
                    pass
            self._set_overlays_visible(show)
        except Exception:
            pass

    def _set_overlays_visible(self, on: bool) -> None:
        """overlay 들 show/hide 를 상태 기반으로 토글. 이미 맞으면 no-op."""
        for ov in (self._overlay, self._alert_overlay, self._helper_overlay,
                   self._hpmp_overlay):
            if ov is None:
                continue
            try:
                if on and not ov.isVisible():
                    ov.show()
                elif (not on) and ov.isVisible():
                    ov.hide()
            except Exception:
                pass
        # 스킬범위 오버레이는 "스킬범위" 체크박스 ON + 격수모드 + on 조건.
        try:
            if self._skill_range_overlay is not None:
                want = (on
                        and self.role == "attacker"
                        and self.chk_skill_range.isChecked())
                if want and not self._skill_range_overlay.isVisible():
                    self._skill_range_overlay.show()
                elif (not want) and self._skill_range_overlay.isVisible():
                    self._skill_range_overlay.hide()
        except Exception:
            pass

    def _tick_msw_tracker(self) -> None:
        """msw 창 이동 추적 → 저장된 모든 영역을 델타만큼 자동 shift.

        - 첫 tick: baseline 기록만(영역 변경 없음).
        - 이후 tick: client origin 이 바뀌면 dx,dy 를 구해
          self._regions(게임/맵/좌표/경험치/hp/mp) 와 cfg.cooldown 의
          cd/nick region 을 같이 이동. 워커 실행 중이면 즉시 반영.
          region_overlay/GameOverlay 앵커도 갱신. 설정 파일에도 즉시 저장.
        """
        try:
            from ..input.keys import find_windows_by_process
            from ..capture.screen import get_window_rect
            tw = str(getattr(self.cfg.input, "target_window", "msw.exe"))
            wins = find_windows_by_process(tw) if tw.lower().endswith(".exe") else []
            if not wins:
                return
            hwnd = int(wins[0])
            r = get_window_rect(hwnd)
            if not r:
                return
            origin = (int(r["left"]), int(r["top"]))
            prev = self._msw_last_client_origin
            if prev is None:
                self._msw_last_client_origin = origin
                return
            dx = origin[0] - prev[0]
            dy = origin[1] - prev[1]
            if dx == 0 and dy == 0:
                return
            self._msw_last_client_origin = origin
            # 1) self._regions shift.
            for k, (x, y, w, h) in list(self._regions.items()):
                self._regions[k] = (int(x + dx), int(y + dy), int(w), int(h))
            # 2) cfg.cooldown cd/nick region shift (음수/0은 유효치 없음 → 스킵).
            try:
                cd = self.cfg.cooldown
                if int(cd.region_w) > 0 and int(cd.region_x) >= 0:
                    cd.region_x = int(cd.region_x + dx)
                    cd.region_y = int(cd.region_y + dy)
                nx = int(getattr(cd, "nick_region_x", -1))
                nw = int(getattr(cd, "nick_region_w", 0))
                if nx >= 0 and nw > 0:
                    cd.nick_region_x = int(nx + dx)
                    cd.nick_region_y = int(
                        int(getattr(cd, "nick_region_y", 0)) + dy
                    )
                bx = int(getattr(cd, "buff_region_x", -1))
                bw = int(getattr(cd, "buff_region_w", 0))
                if bx >= 0 and bw > 0:
                    cd.buff_region_x = int(bx + dx)
                    cd.buff_region_y = int(
                        int(getattr(cd, "buff_region_y", 0)) + dy
                    )
                # HP/MP 영역 shift (cfg.cooldown 저장본 — 워커 재기동 대비).
                for _k in ("hp", "mp"):
                    _x = int(getattr(cd, f"{_k}_region_x", -1))
                    _w = int(getattr(cd, f"{_k}_region_w", 0))
                    if _x >= 0 and _w > 0:
                        setattr(cd, f"{_k}_region_x", int(_x + dx))
                        setattr(
                            cd, f"{_k}_region_y",
                            int(getattr(cd, f"{_k}_region_y", 0)) + dy,
                        )
            except Exception:
                pass
            # 3) 워커 실행 중이면 shift 결과를 즉시 주입.
            if self.worker is not None:
                try:
                    self._apply_all_regions_to_worker()
                except Exception:
                    pass
                # cd/nick/buff 영역도 강제 재주입 (cfg.cooldown 변경분).
                try:
                    cd2 = self.cfg.cooldown
                    if (int(cd2.region_w) > 0 and int(cd2.region_x) >= 0
                            and hasattr(self.worker, "set_cooldown_region")):
                        self.worker.set_cooldown_region(
                            int(cd2.region_x), int(cd2.region_y),
                            int(cd2.region_w), int(cd2.region_h),
                        )
                    nx2 = int(getattr(cd2, "nick_region_x", -1))
                    nw2 = int(getattr(cd2, "nick_region_w", 0))
                    if (nx2 >= 0 and nw2 > 0
                            and hasattr(self.worker, "set_nick_region")):
                        self.worker.set_nick_region(
                            nx2, int(getattr(cd2, "nick_region_y", 0)),
                            nw2, int(getattr(cd2, "nick_region_h", 0)),
                        )
                    bx2 = int(getattr(cd2, "buff_region_x", -1))
                    bw2 = int(getattr(cd2, "buff_region_w", 0))
                    if (bx2 >= 0 and bw2 > 0
                            and hasattr(self.worker, "set_buff_region")):
                        self.worker.set_buff_region(
                            bx2, int(getattr(cd2, "buff_region_y", 0)),
                            bw2, int(getattr(cd2, "buff_region_h", 0)),
                        )
                    # HP/MP 영역도 shift 즉시 반영 — _apply_region_to_worker
                    # 가 _regions 값으로 워커에 주입 (둘 다 shift 된 상태).
                except Exception:
                    pass
            # 4) region overlay / GameOverlay 앵커 갱신.
            try:
                self._refresh_region_overlay()
            except Exception:
                pass
            try:
                self._refresh_overlay_anchors()
            except Exception:
                pass
            # 4b) 오버레이 수동 위치도 dx/dy 동일 shift — msw 따라다니게.
            try:
                for _k, _xy in list(self._overlay_positions.items()):
                    try:
                        nx = int(_xy[0]) + dx
                        ny = int(_xy[1]) + dy
                        self._overlay_positions[_k] = (nx, ny)
                    except Exception:
                        pass
                _pair = [
                    ("cd", self._overlay),
                    ("alert", self._alert_overlay),
                    ("helper", self._helper_overlay),
                    ("hpmp", self._hpmp_overlay),
                ]
                for _k, _ov in _pair:
                    if _ov is None:
                        continue
                    _np = self._overlay_positions.get(_k)
                    if _np is None:
                        continue
                    try:
                        _ov.set_manual_pos(int(_np[0]), int(_np[1]))
                    except Exception:
                        pass
            except Exception:
                pass
            # 5) 디스크 즉시 반영 (크래시 시에도 이동 결과 보존).
            try:
                self._save_settings()
            except Exception:
                pass
            try:
                self._append_log(
                    f"[msw] 창 이동 감지 Δ=({dx:+d},{dy:+d}) → 영역 자동 이동"
                )
            except Exception:
                pass
        except Exception:
            pass

    def _tick_fps(self) -> None:
        """HealerWorker.last_fps 주기 폴링 → lbl_fps 갱신.

        frame_ready emit이 저사양 모드에서 스킵되어도 워커 내부 1초 윈도우로
        계산되는 last_fps는 계속 갱신되므로 실제 루프 FPS를 확인할 수 있음.
        """
        if self.role != "healer" or self.worker is None:
            return
        try:
            v = float(getattr(self.worker, "last_fps", 0.0) or 0.0)
        except Exception:
            v = 0.0
        try:
            suffix = " (저사양)" if self.chk_low_spec.isChecked() else ""
            self.lbl_fps.setText(f"{v:.1f}{suffix}")
        except Exception:
            pass

    def _tick_analytics(self) -> None:
        """AttackerWorker.get_analytics_snapshot 주기 조회 → 오버레이 반영.
        last_report.session_id 변경 감지 시 SkillAlertOverlay에 보고 표시.
        힐러 모드이거나 워커 없으면 무동작.
        """
        if self.role != "attacker" or self.worker is None:
            return
        try:
            snap = self.worker.get_analytics_snapshot() or {}
        except Exception:
            snap = {}
        if not snap:
            return
        # 오버레이 업데이트 (오버레이 비활성 시 None 방어).
        try:
            if self._overlay is not None:
                self._overlay.update_analytics(snap)
        except Exception:
            pass
        # 세션 종료 보고.
        last = snap.get("last_report")
        if last:
            sid = str(last.get("session_id") or "")
            if sid and sid != self._last_hunt_session_id:
                self._last_hunt_session_id = sid
                try:
                    msg = self._format_hunt_report_line(last)
                    if msg and self._alert_overlay is not None:
                        self._alert_overlay.push_alert(
                            msg,
                            duration_sec=30.0,
                            color=QtGui.QColor(160, 230, 255),
                        )
                    self._append_log(f"[사냥 보고] {msg}")
                except Exception as e:
                    self._append_log(f"[사냥 보고] 표시 실패: {e}")

    @staticmethod
    def _format_hunt_report_line(rec: dict) -> str:
        import datetime as _dt
        try:
            start = float(rec.get("start_ts") or 0)
            if start > 0:
                date_str = _dt.datetime.fromtimestamp(start).strftime(
                    "%Y-%m-%d %H:%M"
                )
            else:
                date_str = ""
        except Exception:
            date_str = ""
        dur = int(rec.get("duration_sec") or 0)
        gain = int(rec.get("xp_gain") or 0)
        laps = rec.get("laps") or []
        m, s = divmod(max(0, dur), 60)
        h, m2 = divmod(m, 60)
        if h > 0:
            dur_s = f"{h}시간{m2:02d}분"
        elif m > 0:
            dur_s = f"{m}분{s:02d}초"
        else:
            dur_s = f"{s}초"
        if gain >= 100_000_000:
            gain_s = f"{gain / 100_000_000.0:.2f}억"
        elif gain >= 10_000:
            gain_s = f"{gain / 10_000.0:.1f}만"
        else:
            gain_s = f"{gain}"
        return (
            f"사냥 보고 {date_str} · {dur_s} · {gain_s} "
            f"· 바퀴 {len(laps)}회"
        )

    def _on_numlock_off(self):
        try:
            from ..input.numlock_cycle import ensure_numlock_off
            changed = ensure_numlock_off()
            self._append_log(
                "[NumLock] 이미 OFF" if not changed else "[NumLock] OFF로 전환"
            )
        except Exception as e:
            self._append_log(f"[NumLock] 실패: {e}")

    def start_worker(self):
        if self.worker and self.worker.isRunning():
            return
        # 워커 시작 전 상시 리스너/heartbeat 정지 (포트 경합 회피).
        self._stop_ctrl_listener()
        self._stop_attacker_heartbeat()
        # "시작 = ARM ON" 고정. 이전에 btn_pause 로 OFF 둔 상태라도
        # 사용자가 시작을 누르면 바로 주입이 들어가야 한다.
        try:
            self.chk_arm.blockSignals(True)
            self.chk_arm.setChecked(True)
            self.chk_arm.blockSignals(False)
        except Exception:
            pass
        if self.role == "healer":
            self._start_healer()
        else:
            self._start_attacker()
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._set_run_status(True)
        # 시작 직후 overlay visibility 즉시 재평가 (본창 포그라운드 상태에서도
        # msw 가 살아 있으면 overlay 는 보여야 함 — StaysOnTop 정책).
        try:
            self._tick_overlay_visibility()
        except Exception:
            pass
        # 저사양 체크박스가 켜져 있으면 워커 기동 직후 실시간 튠 재적용.
        try:
            if hasattr(self, "chk_low_spec") and self.chk_low_spec.isChecked():
                self._on_toggle_low_spec(QtCore.Qt.Checked)
        except Exception:
            pass
        # 격수 모드: 사냥 분석 polling 시작 (새 세션 id 매번 초기화).
        if self.role == "attacker":
            try:
                self._last_hunt_session_id = ""
                if not self._analytics_timer.isActive():
                    self._analytics_timer.start()
            except Exception:
                pass
        # 힐러 모드: FPS polling 시작 (저사양 모드 확인용).
        if self.role == "healer":
            try:
                if not self._fps_timer.isActive():
                    self._fps_timer.start()
            except Exception:
                pass
        # 워커 기동 직후 msw 창을 전면으로. 힐러는 키 주입 대상, 격수는 화면 캡처
        # 안정화 목적. 실패해도 워커 동작엔 영향 없음.
        QtCore.QTimer.singleShot(300, self._activate_msw_window)

    # DWMWA_EXTENDED_FRAME_BOUNDS = 9. 실제 보이는 프레임(shadow 제외).
    _DWMWA_EXTENDED_FRAME_BOUNDS = 9

    def _dwm_visible_bounds(self, hwnd: int):
        """DwmGetWindowAttribute 로 visible frame (left,top,right,bottom) 반환.
        실패 시 None. GetWindowRect 의 DWM shadow 포함 문제 회피용.
        """
        import ctypes
        from ctypes import wintypes

        class _RECT(ctypes.Structure):
            _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                        ("right", wintypes.LONG), ("bottom", wintypes.LONG)]
        try:
            dwmapi = ctypes.WinDLL("dwmapi")
            r = _RECT()
            hr = dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(hwnd),
                ctypes.c_uint(self._DWMWA_EXTENDED_FRAME_BOUNDS),
                ctypes.byref(r),
                ctypes.sizeof(r),
            )
            if hr != 0:
                return None
            return (int(r.left), int(r.top), int(r.right), int(r.bottom))
        except Exception:
            return None

    def _snap_to_msw_right(self) -> None:
        """GUI 창을 msw.exe 창 오른쪽에 딱 붙여 배치 — 제목표시줄 Y 일자 정렬.

        Qt move() 와 DWM visible frame 의 좌표계 차이(내부 shadow, 프레임 개념
        불일치) 때문에 한 번의 계산만으로는 안 맞음. 실측 기반 자기보정 루프:
          1) 대충 이동 (msw 의 visible frame 우측·상단 좌표로 move)
          2) DWM 에서 내 실제 visible frame 위치 읽기
          3) 목표와의 delta 만큼 다시 move → 1~2회 반복이면 수렴
        """
        try:
            from ..input.keys import find_windows_by_process
            tw = str(getattr(self.cfg.input, "target_window", "msw.exe"))
            wins = find_windows_by_process(tw) if tw.lower().endswith(".exe") else []
            if not wins:
                return
            # 2026-04-21: msw 창 최소화 상태면 snap 하지 않음.
            # 마지막에 저장된 win_x/win_y 위치 (settings_io 가 이미 복원) 유지.
            try:
                import ctypes
                _user32 = ctypes.windll.user32
                if _user32.IsIconic(int(wins[0])):
                    self._append_log(
                        "[스냅] msw 최소화 → 저장된 GUI 위치 유지"
                    )
                    return
            except Exception:
                pass
            msw_vis = self._dwm_visible_bounds(int(wins[0]))
            if not msw_vis:
                return
            msw_vl, msw_vt, msw_vr, msw_vb = msw_vis
            target_vx = msw_vr   # GUI visible frame left  == msw visible right
            target_vy = msw_vt   # GUI visible frame top   == msw visible top (제목표시줄 일자)
            # 1) 대충 이동 — 이 좌표가 frame/client 중 뭘 지칭하든, 자기보정이 끌어당김.
            self.move(target_vx, target_vy)
            # 2) 페인트/프레임 확정 후 보정.
            QtCore.QTimer.singleShot(
                40, lambda: self._correct_snap(target_vx, target_vy, 3)
            )
        except Exception as e:
            try:
                self._append_log(f"[스냅] msw 우측 정렬 실패: {e}")
            except Exception:
                pass

    def _correct_snap(self, target_vx: int, target_vy: int, retries: int) -> None:
        """DWM 으로 측정한 내 visible frame 이 target 에 일치할 때까지 보정."""
        try:
            if retries <= 0:
                return
            my_vis = self._dwm_visible_bounds(int(self.winId()))
            if not my_vis:
                QtCore.QTimer.singleShot(
                    60, lambda: self._correct_snap(target_vx, target_vy, retries - 1)
                )
                return
            my_vl, my_vt, _, _ = my_vis
            dx = target_vx - my_vl
            dy = target_vy - my_vt
            if abs(dx) <= 1 and abs(dy) <= 1:
                try:
                    self._append_log(
                        f"[스냅] 완료: target=({target_vx},{target_vy}) "
                        f"actual=({my_vl},{my_vt})"
                    )
                except Exception:
                    pass
                return
            cur = self.pos()
            self.move(cur.x() + dx, cur.y() + dy)
            QtCore.QTimer.singleShot(
                40, lambda: self._correct_snap(target_vx, target_vy, retries - 1)
            )
        except Exception as e:
            try:
                self._append_log(f"[스냅] 보정 실패: {e}")
            except Exception:
                pass

    def _activate_msw_window(self) -> None:
        """target_window(msw.exe) 창을 foreground로 끌어오기.

        Patch 2.21 (2026-04-20): AttachThreadInput 블록 제거.
        detach 실패 시 GUI 스레드와 msw 스레드의 입력 큐가 영구 공유되어
        msw 게임 내에서 Shift 키가 씹히는 증상 유발 (사용자 보고: GUI 켜자마자
        발생, GUI 끄면 복귀). 일부 Win 버전에서 SetForegroundWindow 가
        실패할 수 있으나 BringWindowToTop 으로 대체. 포그라운드 전환이
        실패해도 워커 동작엔 영향 없음.
        """
        try:
            from ..input.keys import find_windows_by_process
            tw = str(getattr(self.cfg.input, "target_window", "msw.exe"))
            wins = find_windows_by_process(tw) if tw.lower().endswith(".exe") else []
            if not wins:
                self._append_log(f"[FG] {tw} 창 없음 → 활성화 skip")
                return
            hwnd = int(wins[0])
            import ctypes
            user32 = ctypes.windll.user32
            SW_RESTORE = 9
            try:
                user32.ShowWindow(hwnd, SW_RESTORE)
            except Exception:
                pass
            ok = False
            try:
                ok = bool(user32.SetForegroundWindow(hwnd))
            except Exception:
                ok = False
            try:
                user32.BringWindowToTop(hwnd)
            except Exception:
                pass
            self._append_log(f"[FG] {tw} hwnd={hwnd} activate ok={ok}")
        except Exception as e:
            try:
                self._append_log(f"[FG] 활성화 실패: {e}")
            except Exception:
                pass

    def _auto_startup(self):
        """GUI 로드 직후 1회: **통신 연결 + Heartbeat** (워커는 기동하지 않음).

        설계 원칙 (사용자 명시, 2026-04-17):
        - GUI 켜자마자 ControlListener로 격수 패킷 수신 → 격수 IP 자동 획득.
        - 동시에 HealerHeartbeat 스레드 기동 → 격수 IP 잡히면 1초마다 빈
          CooldownReport 송신 → 격수 UI 힐러 행 초록불 즉시 점등.
        - 워커(YOLO/OCR/키 주입)는 원격 `start` 또는 로컬 "시작" 버튼이
          눌릴 때만 기동.
        """
        try:
            self._append_log(
                "[AUTO] GUI 기동 → listener + heartbeat 시작 (워커 대기)"
            )
        except Exception:
            pass
        if self.role == "healer":
            try:
                self._start_ctrl_listener()
            except Exception as e:
                try:
                    self._append_log(f"[AUTO] listener 시작 실패: {e}")
                except Exception:
                    pass
            try:
                self._start_heartbeat()
            except Exception as e:
                try:
                    self._append_log(f"[AUTO] heartbeat 시작 실패: {e}")
                except Exception:
                    pass
        else:
            # 격수 모드: 시작버튼 누르기 전에도 ping + CooldownReceiver 가동.
            try:
                self._start_attacker_heartbeat()
            except Exception as e:
                try:
                    self._append_log(f"[AUTO] 격수 heartbeat 실패: {e}")
                except Exception:
                    pass

    def _start_attacker_heartbeat(self) -> None:
        if self._attacker_hb is not None and self._attacker_hb.isRunning():
            return
        # peers 입력값 cfg로 반영(힐러처럼 GUI 입력을 즉시 사용).
        try:
            peers_raw = self.peers_edit.text().strip()
            peers = [p.strip() for p in peers_raw.split(",") if p.strip()]
            if peers:
                self.cfg.net.peers = peers
            self.cfg.net.port = int(self.port_spin.value())
        except Exception:
            pass
        self._attacker_hb = AttackerHeartbeat(self.cfg)
        # 힐러 heartbeat 수신 → 격수 UI 갱신 (_on_attacker_cooldown 공용).
        self._attacker_hb.cooldown_update.connect(self._on_attacker_cooldown)
        self._attacker_hb.start()
        self._append_log(
            f"[ATK-HB] 격수 heartbeat 시작 → {self.cfg.net.peers}:"
            f"{self.cfg.net.port}"
        )

    def _stop_attacker_heartbeat(self) -> None:
        if self._attacker_hb is None:
            return
        try:
            self._attacker_hb.stop()
            self._attacker_hb.wait(1500)
        except Exception:
            pass
        self._attacker_hb = None

    def _start_heartbeat(self) -> None:
        if self._heartbeat is not None and self._heartbeat.isRunning():
            return
        self._heartbeat = HealerHeartbeat(self.cfg)
        # 시작 시 닉네임/armed/쿨다운 초기값 세팅.
        my_idx = int(getattr(self.cfg.net, "healer_idx", 0))
        self._heartbeat.update_state(
            armed=False,
            nickname=f"힐러{my_idx + 1}",
            cd_parlyuk=-1, cd_baekho=-1,
        )
        self._heartbeat.start()
        self._append_log(
            "[HEARTBEAT] started (격수 IP 확보 시 자동 송신)"
        )

    def _stop_heartbeat(self) -> None:
        if self._heartbeat is None:
            return
        try:
            self._heartbeat.stop()
            self._heartbeat.wait(1500)
        except Exception:
            pass
        self._heartbeat = None

    def _on_attacker_seen(self, ip: str, port: int) -> None:
        """ControlListener가 첫 패킷 src_addr을 알려주면 Heartbeat에 전달."""
        if self._heartbeat is not None:
            self._heartbeat.set_attacker_addr(str(ip), int(port))
        self._append_log(f"[CONNECT] 격수 감지 {ip}:{port} → heartbeat 송신")

    def _start_ctrl_listener(self) -> None:
        """상시 원격 제어 리스너 기동. 힐러 모드 한정."""
        if self.role != "healer":
            return
        if self._ctrl_listener is not None and self._ctrl_listener.isRunning():
            return
        try:
            my_idx = int(getattr(self.cfg.net, "healer_idx", 0))
            bind_host = getattr(self.cfg.net, "bind_host", "0.0.0.0")
            port = int(getattr(self.cfg.net, "port", 54545))
            self._ctrl_listener = ControlListener(bind_host, port, my_idx)
            self._ctrl_listener.cmd_received.connect(self._handle_remote_cmd)
            self._ctrl_listener.attacker_seen.connect(self._on_attacker_seen)
            self._ctrl_listener.start()
            self._append_log(
                f"[CTRL-LISTEN] idle listener on {bind_host}:{port} "
                f"idx={my_idx}"
            )
        except Exception as e:
            self._append_log(f"[CTRL-LISTEN] 시작 실패: {e}")

    def _stop_ctrl_listener(self) -> None:
        if self._ctrl_listener is None:
            return
        try:
            self._ctrl_listener.stop()
            self._ctrl_listener.wait(1000)
        except Exception:
            pass
        self._ctrl_listener = None

    def _handle_remote_cmd(self, cmd: str, target_idx: int) -> None:
        """워커 비활성 상태에서 받은 원격 제어 처리.

        start       → 워커 기동 + armed=True.
        pause       → 워커 기동 + armed=False.
        stop        → 이미 정지 상태 → 무시 + 로그.
        follow_on   → chk_follow_only ON (워커 기동하지 않음).
        follow_off  → chk_follow_only OFF (워커 기동하지 않음).
        """
        c = str(cmd or "").lower()
        # ping은 heartbeat 수신 표시만 하면 되므로 별도 로그/동작 없음.
        if c == "ping":
            return
        self._append_log(
            f"[REMOTE-IDLE] cmd={c} target={target_idx} "
            f"(worker={'running' if (self.worker and self.worker.isRunning()) else 'stopped'})"
        )
        if c == "stop":
            # 이미 정지 → 아무 것도 안 함.
            return
        if c in ("follow_on", "follow_off"):
            # follow 상태만 토글. 워커 idle 상태에서는 UI 체크박스만 업데이트.
            on = (c == "follow_on")
            try:
                self.chk_follow_only.blockSignals(True)
                self.chk_follow_only.setChecked(bool(on))
                self.chk_follow_only.blockSignals(False)
            except Exception:
                pass
            return
        if c in ("start", "pause"):
            armed_on = (c == "start")
            # chk_arm 먼저 시그널 차단으로 설정해 워커 생성 시 반영되게.
            try:
                self.chk_arm.blockSignals(True)
                self.chk_arm.setChecked(bool(armed_on))
                self.chk_arm.blockSignals(False)
            except Exception:
                pass
            self.start_worker()

    def _start_healer(self):
        self.worker = HealerWorker(self.cfg)
        self.worker.armed = self.chk_arm.isChecked()
        self.worker.follow_only = self.chk_follow_only.isChecked()
        self.worker.follow_light = self.chk_follow_only.isChecked()
        self.worker.min_w = self.minw_spin.value()
        self.worker.min_h = self.minh_spin.value()
        self.worker.coord_tol = self.tol_spin.value()
        self.worker.set_parlyuk_maps(self.parlyuk_maps_edit.text())
        self.worker.yolo_conf = self.conf_slider.value() / 100.0
        self.worker.yolo_every_n = self.yn_spin.value()
        # 스킬 토글/오프셋/VK 초기값 주입 (스케줄러/싸이클러 생성 전에 반영).
        self.worker.skill_enabled = {
            n: c.isChecked() for n, c in self.skill_chks.items()
        }
        self.worker.parlyuk_offset = float(self.parlyuk_spin.value())
        # NumLock 싸이클 VK = 기원 라디오(택1) + 혼마술 체크시 추가.
        self.worker.primary_vks = self._current_cycle_vks()
        self.worker.skill_vks = {
            n: self._numpad_vk(sp.value())
            for n, sp in self.skill_spins.items()
        }
        # 메인힐 VK (봉황/신령 공용) — 자힐 burst 시 재사용.
        try:
            self.worker.skill_vks["메인힐"] = self._numpad_vk(
                self.skill_dlg.spin_mainheal.value()
            )
        except Exception:
            pass
        # 공력증강 임계치 주입 (default_skills 호출 전에 반영 필수).
        try:
            self.worker.gyoungryeok_mp_thr = int(
                self.skill_dlg.gyoungryeok_mp_spin.value()
            )
        except Exception:
            pass
        # NumLock 상태는 skill_lock_vk가 내부에서 ON으로 복구함 (old.oldbaram 시퀀스).
        # 따라서 시작 시 강제 OFF 하지 않음. 사용자가 NumLock OFF였다면 ON으로 뒤바뀔 수 있음.
        # HP/MP 최대값 주입 (HpMpReader 의 cur/max 분리 + pct 환산용).
        try:
            if hasattr(self.worker, "set_hp_max"):
                self.worker.set_hp_max(int(self.hp_max_spin.value()))
            if hasattr(self.worker, "set_mp_max"):
                self.worker.set_mp_max(int(self.mp_max_spin.value()))
        except Exception:
            pass
        self.worker.frame_ready.connect(self._on_frame)
        self.worker.log_msg.connect(self._append_log_filtered)
        self.worker.stopped.connect(self._on_stopped)
        # UI 동기화: 원격(ControlCmd) 수신 시 chk_arm 체크박스 갱신.
        self.worker.remote_control_applied.connect(self._on_remote_control)
        self.worker.start()
        self._append_log("[healer] 시작")
        self._append_log(f"로그 파일: {self.worker.log_path}")
        # 저장된 6개 추가 영역을 워커에 일괄 반영 (game/xp/hp/mp만 setter 존재).
        try:
            self._apply_all_regions_to_worker()
        except Exception:
            pass

    def _on_remote_control(self, on: bool, cmd: str) -> None:
        """격수에서 받은 원격 제어를 GUI chk_arm / 로그에 반영.
        stop은 워커만 종료 → ARM 체크박스는 **건드리지 않음**. start/pause만 토글."""
        c = str(cmd or "").lower()
        if c == "stop":
            self._append_log(
                f"[REMOTE] cmd=stop → 워커 종료 (ARM={'ON' if on else 'OFF'} 유지)"
            )
            return
        if c in ("follow_on", "follow_off"):
            # follow는 chk_follow_only를 워커가 갱신하지 않음 → 여기서 업데이트.
            try:
                self.chk_follow_only.blockSignals(True)
                self.chk_follow_only.setChecked(c == "follow_on")
                self.chk_follow_only.blockSignals(False)
            except Exception:
                pass
            self._append_log(f"[REMOTE] cmd={c}")
            return
        # start / pause만 chk_arm 토글.
        try:
            self.chk_arm.blockSignals(True)
            self.chk_arm.setChecked(bool(on))
            self.chk_arm.blockSignals(False)
        except Exception:
            pass
        self._append_log(f"[REMOTE] cmd={c} → ARM={'ON' if on else 'OFF'}")

    def _start_attacker(self):
        # UI 값 → cfg 반영 (런타임 오버라이드)
        peers_raw = self.peers_edit.text().strip()
        peers = [p.strip() for p in peers_raw.split(",") if p.strip()]
        if peers:
            self.cfg.net.peers = peers
        self.cfg.net.port = int(self.port_spin.value())
        self.cfg.net.send_rate_hz = int(self.rate_spin.value())
        # peers 변경 시 힐러 행 재구성.
        self._refresh_healer_rows()
        self.worker = AttackerWorker(self.cfg)
        self.worker.log_msg.connect(self._append_log_filtered)
        self.worker.stat_ready.connect(self._on_attacker_stat)
        self.worker.cooldown_update.connect(self._on_attacker_cooldown)
        try:
            self.worker.own_cooldown_update.connect(self._on_own_cooldown)
        except Exception:
            pass
        self.worker.stopped.connect(self._on_stopped)
        # 격수 본인 쿨 OCR — 서브클래스 × 승급 스킬 + 저장된 쿨 영역 주입.
        try:
            from .hunter_helper_panel import get_rank_skills
            sub = getattr(self, "attacker_subclass", "thief")
            rank = int(getattr(self, "attacker_rank", 4))
            names = [nm for (nm, _cd) in get_rank_skills(sub, rank)]
            if names and hasattr(self.worker, "set_own_skill_names"):
                self.worker.set_own_skill_names(names)
        except Exception as _e:
            self._append_log(f"[cd] 서브클래스 스킬 주입 실패: {_e}")
        try:
            cd = self.cfg.cooldown
            cx = int(getattr(cd, "region_x", -1))
            cw = int(getattr(cd, "region_w", 0))
            if cx >= 0 and cw > 0 and hasattr(self.worker, "set_cooldown_region"):
                self.worker.set_cooldown_region(
                    cx, int(cd.region_y),
                    cw, int(cd.region_h),
                )
        except Exception as _e:
            self._append_log(f"[cd] 쿨 영역 주입 실패: {_e}")
        # 2026-04-20: attacker_buff_region 분리 제거 — 버프 영역(공용) 사용.
        # 격수 공용 버프 영역 주입 (힐러 파혼술 트리거용 혼마술 감시도 겸함).
        try:
            cd = self.cfg.cooldown
            bx = int(getattr(cd, "buff_region_x", -1))
            bw = int(getattr(cd, "buff_region_w", 0))
            if bx >= 0 and bw > 0 and hasattr(self.worker, "set_buff_region"):
                self.worker.set_buff_region(
                    bx, int(getattr(cd, "buff_region_y", 0)),
                    bw, int(getattr(cd, "buff_region_h", 0)),
                )
        except Exception as _e:
            self._append_log(f"[buff] 격수 버프 영역 주입 실패: {_e}")
        # HP/MP OCR 영역 주입 (저장된 _regions 및 cfg.cooldown.hp/mp_region_*).
        try:
            cd = self.cfg.cooldown
            for _k, _set_name in (("hp", "set_hp_region"),
                                   ("mp", "set_mp_region")):
                _x = int(getattr(cd, f"{_k}_region_x", -1))
                _w = int(getattr(cd, f"{_k}_region_w", 0))
                if _x >= 0 and _w > 0 and hasattr(self.worker, _set_name):
                    getattr(self.worker, _set_name)(
                        _x, int(getattr(cd, f"{_k}_region_y", 0)),
                        _w, int(getattr(cd, f"{_k}_region_h", 0)),
                    )
        except Exception as _e:
            self._append_log(f"[hp/mp] 격수 HP/MP 영역 주입 실패: {_e}")
        # HP/MP 최대값 주입 (격수도 hp_max/mp_max 사용).
        try:
            if hasattr(self.worker, "set_hp_max"):
                self.worker.set_hp_max(int(self.hp_max_spin.value()))
            if hasattr(self.worker, "set_mp_max"):
                self.worker.set_mp_max(int(self.mp_max_spin.value()))
        except Exception:
            pass
        self.worker.start()
        self._append_log(
            f"[attacker] 시작 → {self.cfg.net.peers}:{self.cfg.net.port} "
            f"@{self.cfg.net.send_rate_hz}Hz "
            f"recv_port={int(getattr(self.cfg.net,'attacker_recv_port',45455))}"
        )
        self._append_log(f"로그 파일: {self.worker.log_path}")
        # ★ 저장된 xp 영역을 격수 워커에 주입. 이전엔 healer 경로에만 있어
        # 격수에서 경험치 영역을 미리 지정해둬도 워커엔 안 붙어 xp OCR이 돌지
        # 않았음 → analytics.on_xp 호출이 없어 gain=0, 오버레이 "사냥" 라인 미표시.
        try:
            self._apply_all_regions_to_worker()
        except Exception:
            pass

    def _on_attacker_stat(self, d: dict):
        # 스킬범위 오버레이 업데이트 (빨탭 좌표 + 박스 + dir).
        try:
            if self._skill_range_overlay is not None:
                box = d.get("red_box")
                box_tup = None
                if (isinstance(box, (list, tuple))
                        and len(box) == 4):
                    box_tup = (int(box[0]), int(box[1]),
                               int(box[2]), int(box[3]))
                self._skill_range_overlay.update_state(
                    bool(d.get("red_tab", False)),
                    int(d.get("red_cx", 0) or 0),
                    int(d.get("red_cy", 0) or 0),
                    str(d.get("dir", "") or ""),
                    box=box_tup,
                )
        except Exception:
            pass
        self.lbl_fsm.setText("ATTACKER")
        self.lbl_hold.setText("-")
        self.lbl_seq.setText(str(d.get("seq", "-")))
        peers = d.get("peers", [])
        port = d.get("port", "-")
        self.lbl_udp.setText(f"→ {','.join(peers)}:{port}")
        self.lbl_red.setText("-")
        self.lbl_fps.setText("-")
        self.lbl_hcoord.setText("-")
        coord = d.get("coord")
        valid = d.get("valid", False)
        if coord and valid:
            self.lbl_acoord.setText(f"({coord[0]},{coord[1]})")
        else:
            self.lbl_acoord.setText("-")
        mp = d.get("map", "")
        self.lbl_map.setText(f"A={mp or '-'}")
        self.lbl_want.setText(d.get("dir", "-"))
        self.lbl_reason.setText("송신 중")
        self.lbl_fg.setText("-")
        self.lbl_nl.setText("-")
        self.lbl_perf.setText("-")

    def stop_worker(self):
        if self.worker:
            self.worker.stop()

    def _on_stopped(self):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._set_run_status(False)
        try:
            if self._analytics_timer.isActive():
                self._analytics_timer.stop()
        except Exception:
            pass
        try:
            if self._fps_timer.isActive():
                self._fps_timer.stop()
        except Exception:
            pass
        # 워커가 꺼지면 포트가 자유로워짐 → 상시 listener/heartbeat 다시 기동.
        if self.role == "healer":
            self._start_ctrl_listener()
        else:
            self._start_attacker_heartbeat()

    def _on_frame(self, payload):
        # 미리보기는 게임영역 크롭만 표시. 전체창은 표출 안 함.
        pv = payload.get("preview_frame")
        det = payload["det"]
        if pv is None:
            # 게임영역 미지정/무효 → 안내 문구.
            self.preview.setText(
                "게임영역을 먼저 지정하세요\n(도사 > 게임 버튼 → 화면 드래그)"
            )
            self.preview.setPixmap(QtGui.QPixmap())
        else:
            ox, oy = payload.get("preview_offset") or (0, 0)
            ox = int(ox); oy = int(oy)
            f = pv.copy()
            for d in payload["all_dets"]:
                cv2.rectangle(
                    f, (d.x1 - ox, d.y1 - oy), (d.x2 - ox, d.y2 - oy),
                    (100, 100, 100), 1,
                )
            if det is not None:
                cv2.rectangle(
                    f, (det.x1 - ox, det.y1 - oy),
                    (det.x2 - ox, det.y2 - oy),
                    (0, 255, 255), 2,
                )
                cv2.putText(
                    f, f"{det.conf:.2f}",
                    (det.x1 - ox, det.y1 - oy - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1,
                )
            pix = frame_to_qpix(f, max_w=464)
            self.preview.setPixmap(pix)

        self.lbl_fsm.setText(payload["state"].value)
        self.lbl_hold.setText(payload["hold"])
        self.lbl_seq.setText(str(payload["seq"]))
        self.lbl_udp.setText("Y" if payload["udp"] else "N")
        self.lbl_fps.setText(f"{payload['fps']:.1f}")
        if det is not None:
            self.lbl_red.setText(
                f"({det.cx},{det.cy}) {det.w}x{det.h} c={det.conf:.2f}"
            )
        else:
            self.lbl_red.setText("X")
        hc = payload.get("healer_coord")
        ac = payload.get("atk_coord")
        hm = payload.get("healer_map", "")
        am = payload.get("atk_map", "")
        self.lbl_hcoord.setText(f"({hc[0]},{hc[1]})" if hc else "-")
        self.lbl_acoord.setText(f"({ac[0]},{ac[1]})" if ac else "-")
        same = (hm and am and hm == am)
        self.lbl_map.setText(
            f"H={hm or '-'} / A={am or '-'} "
            f"{'OK' if same else ('DIFF' if hm and am else '?')}"
        )
        self.lbl_want.setText(payload.get("want", "-"))
        self.lbl_reason.setText(payload.get("reason") or "OK" if payload.get("armed") else "ARM OFF")
        fg = payload.get("hwnd_fg")
        self.lbl_fg.setText("Y" if fg else ("N (SendInput 씹힘)" if payload.get("armed") else "N"))
        nl = payload.get("numlock", False)
        self.lbl_nl.setText(
            "ON (싸이클 차단)" if nl else "OFF (싸이클 활성)"
        )
        perf = payload.get("perf")
        if perf:
            g, y, o, tt = perf
            self.lbl_perf.setText(
                f"grab={g:.0f} yolo={y:.0f} ocr={o:.0f} total={tt:.0f}"
            )
        # 상단 상태 스트립 갱신 (격수맵/힐러맵/격수좌표/힐러좌표/FSM 한글).
        if self._status_strip is not None:
            try:
                self._status_strip.update_from_frame(payload)
            except Exception:
                pass
        # 격수 자체 상태 블록 (힐러 패널 상단) 갱신.
        try:
            _self_map_lbl = getattr(self, "lbl_self_map", None)
            _self_coord_lbl = getattr(self, "lbl_self_coord", None)
            if _self_map_lbl is not None:
                _self_map_lbl.setText(f"맵: {am if am else '-'}")
            if _self_coord_lbl is not None:
                if ac:
                    _self_coord_lbl.setText(f"좌표: ({ac[0]}, {ac[1]})")
                else:
                    _self_coord_lbl.setText("좌표: -")
        except Exception:
            pass

    def _collect_settings(self) -> dict:
        """설정 수집. 본문은 ui.settings_io.collect 로 분리."""
        from .settings_io import collect
        return collect(self)

    def _save_settings(self):
        """JSON 저장. 본문은 ui.settings_io.save 로 분리."""
        from .settings_io import save
        save(self)

    def _load_settings(self):
        """JSON 로드 + UI 복원. 본문은 ui.settings_io.load 로 분리."""
        from .settings_io import load
        load(self)

    def closeEvent(self, ev):
        # 종료 시 디버그 로그 자동 업로드 (클라우드 미설정이면 조용히 skip).
        try:
            from . import cloud_panel
            cloud_panel.auto_upload_log(self)
        except Exception:
            pass
        # 설정 자동 저장.
        try:
            self._save_settings()
        except Exception:
            pass
        # 오버레이도 닫기.
        try:
            if self._overlay is not None:
                self._overlay.close()
        except Exception:
            pass
        try:
            if self._alert_overlay is not None:
                self._alert_overlay.close()
        except Exception:
            pass
        try:
            if self._helper_overlay is not None:
                self._helper_overlay.close()
        except Exception:
            pass
        try:
            if self._hpmp_overlay is not None:
                self._hpmp_overlay.close()
        except Exception:
            pass
        try:
            if self._skill_range_overlay is not None:
                self._skill_range_overlay.close()
        except Exception:
            pass
        try:
            if self._region_overlay is not None:
                self._region_overlay.close()
        except Exception:
            pass
        # 상시 원격 리스너/heartbeat 정리.
        self._stop_ctrl_listener()
        self._stop_heartbeat()
        self._stop_attacker_heartbeat()
        # 전역 단축키 해제.
        try:
            mgr = getattr(self, "_hotkey_mgr", None)
            if mgr is not None:
                mgr.stop()
                self._hotkey_mgr = None
        except Exception:
            pass
        if self.worker:
            self.worker.stop()
            self.worker.wait(2000)
        ev.accept()


_APP_QSS = """
QMainWindow, QDialog, QWidget {
    background: #1b1d22;
    color: #e6e6e6;
    font-family: 'Segoe UI', 'Malgun Gothic', sans-serif;
    font-size: 10pt;
}
QGroupBox {
    border: 1px solid #2f333b;
    border-radius: 6px;
    margin-top: 12px;
    padding: 6px 4px 4px 4px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 4px;
    color: #8c96a6;
    font-weight: bold;
}
QPushButton {
    background: #262a33;
    border: 1px solid #3a4150;
    border-radius: 4px;
    padding: 5px 10px;
    color: #e6e6e6;
}
QPushButton:hover { background: #2f3542; border-color: #556074; }
QPushButton:pressed { background: #1a1d24; }
QPushButton:disabled { color: #5a5f6a; border-color: #2a2e36; }
QPushButton:checked { background: #1f6feb; border-color: #2f7eff; }
QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QComboBox {
    background: #22262e;
    border: 1px solid #343a45;
    border-radius: 4px;
    padding: 3px 6px;
    color: #e6e6e6;
    selection-background-color: #2f6feb;
}
QCheckBox, QRadioButton { color: #e6e6e6; spacing: 6px; }
QCheckBox::indicator, QRadioButton::indicator {
    width: 14px; height: 14px;
}
QSlider::groove:horizontal {
    background: #2a2e36; height: 4px; border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #4a90ff; width: 14px; margin: -5px 0;
    border-radius: 7px;
}
QToolTip {
    background: #2a2e36; color: #e6e6e6;
    border: 1px solid #3a4150; padding: 3px 5px;
}
"""


