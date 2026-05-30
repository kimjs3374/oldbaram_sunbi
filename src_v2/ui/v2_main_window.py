"""V2 자체 GUI — facade 폐기, v2 worker 직접 제어.

설계 의도
========
- v1 MainWindow 와 강결합 끊기. v1/main_window 절대 import 안 함.
- HealerWorkerV2 / AttackerWorkerV2 의 store (SnapshotStore) 를 1Hz 로 직접
  read 해서 텔레메트리 패널 갱신. frame_ready signal 의존 X.
- 영역 picker (src/ui/region_picker.RegionPicker — 무수정 재사용) 로 드래그한
  좌표를 worker.set_*_region(...) 으로 즉시 어댑터에 주입.
- cfg.yaml 에 영역 좌표 저장된 게 있으면 시작 시 자동 적용.
- 시작/정지/암드 토글 + 자힐 임계 등 핵심 설정만 노출.

사용
----
    from src_v2.workers.healer_worker_v2 import HealerWorkerV2
    worker = HealerWorkerV2(...)
    win = V2MainWindow(role="healer", cfg=cfg, worker=worker)
    win.show()
"""
from __future__ import annotations

import logging
import os
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import yaml
from PyQt5 import QtCore, QtGui, QtWidgets

# v1 region_picker 재사용 (src/ 무수정).
try:
    from src.ui.region_picker import RegionPicker
except Exception:  # noqa: BLE001
    RegionPicker = None  # type: ignore[assignment]

# 2026-05-05 Cycle 4-2 — 오버레이 2종 lazy import.
# Cycle 7(src 폴더 hide 스모크) 전까지는 src.ui.overlay 직접 사용 (운영 정본).
try:
    from src.ui.overlay import GameOverlay, SkillAlertOverlay
except Exception:  # noqa: BLE001
    GameOverlay = None  # type: ignore[assignment]
    SkillAlertOverlay = None  # type: ignore[assignment]

# 2026-05-05 Cycle 4-3 — 격수 측 오버레이 3종 lazy import.
# 격수 PC 가동 시 사용 — 메모리 project_healer_status_overlay /
# project_skill_range_overlay 참조.
try:
    from src.ui.healer_status_overlay import HealerStatusOverlay
except Exception:  # noqa: BLE001
    HealerStatusOverlay = None  # type: ignore[assignment]
try:
    from src.ui.hunter_helper_panel import HunterHelperOverlay
except Exception:  # noqa: BLE001
    HunterHelperOverlay = None  # type: ignore[assignment]
try:
    from src.ui.skill_range_overlay import SkillRangeOverlay
except Exception:  # noqa: BLE001
    SkillRangeOverlay = None  # type: ignore[assignment]

# 2026-05-05 Cycle 4-4 — Dialog 4종 lazy import.
# main_window_v2 와 동일 컴포넌트 — 메뉴 버튼으로 modeless 팝업.
try:
    from src.ui.dialogs import SkillDialog, ParamDialog, NetworkDialog
except Exception:  # noqa: BLE001
    SkillDialog = None  # type: ignore[assignment]
    ParamDialog = None  # type: ignore[assignment]
    NetworkDialog = None  # type: ignore[assignment]
try:
    from src.ui.hunt_report_dialog import HuntReportDialog
except Exception:  # noqa: BLE001
    HuntReportDialog = None  # type: ignore[assignment]

# 2026-05-05 Cycle 4-5 — StatusStrip + RegionOverlay lazy import.
# StatusStrip = 메인 창 inline 상태바 (state/coord/HP/MP 한 줄 요약).
# RegionOverlay = 영역 시각화 (showFullScreen, 영역 빨간 박스 표시).
try:
    from src.ui.status_strip import StatusStrip
except Exception:  # noqa: BLE001
    StatusStrip = None  # type: ignore[assignment]
try:
    from src.ui.region_overlay import RegionOverlay
except Exception:  # noqa: BLE001
    RegionOverlay = None  # type: ignore[assignment]

# 2026-05-05 Cycle 4-6 — ControlListener + Heartbeat lazy import.
# ControlListener = 원격 제어 명령 수신 (양 role 공용).
# HealerHeartbeat / AttackerHeartbeat = PC 상태 주기 송신 (role 별).
try:
    from src.workers.control_listener import ControlListener
except Exception:  # noqa: BLE001
    ControlListener = None  # type: ignore[assignment]
try:
    from src.workers.heartbeat import HealerHeartbeat, AttackerHeartbeat
except Exception:  # noqa: BLE001
    HealerHeartbeat = None  # type: ignore[assignment]
    AttackerHeartbeat = None  # type: ignore[assignment]

log = logging.getLogger("src_v2.ui.v2_main_window")


# 영역 키 → 사람 라벨 / worker setter 메서드명 매핑.
# 2026-05-05 Cycle 4-10 — coord/map picker 추가:
#   사용자 picker 절대 좌표 → game_region 기준 OcrCfg 패딩 역산 → Ocr attribute
#   직접 setattr (RealOcrAdapter.set_coord_region/set_map_region 참조).
#   격수 좌표/맵 OCR 가 격수 PC 운영 핵심 (UDP 송신값) → picker 필수.
REGION_DEFS_HEALER: Dict[str, Tuple[str, str]] = {
    "game":     ("게임 영역",      "set_game_region"),
    "coord":    ("좌표 영역",      "set_coord_region"),
    "map":      ("맵이름 영역",    "set_map_region"),
    "cooldown": ("쿨다운 영역",    "set_cooldown_region"),
    "buff":     ("버프 영역",      "set_buff_region"),
    "chat":     ("채팅 영역",      "set_chat_region"),
    "hp":       ("HP 영역",        "set_hp_region"),
    "mp":       ("MP 영역",        "set_mp_region"),
    "nick":     ("닉네임 영역",    "set_nick_region"),
    "xp":       ("경험치 영역",    "set_xp_region"),
}

REGION_DEFS_ATTACKER: Dict[str, Tuple[str, str]] = {
    # 격수 PC 운영 핵심: 자기 좌표/맵 OCR → UDP 송신 → 힐러가 추종 의존.
    # buff(mujang/보호/혼마술) 도 OCR 후 송신.
    "game":     ("게임 영역",      "set_game_region"),
    "coord":    ("좌표 영역",      "set_coord_region"),
    "map":      ("맵이름 영역",    "set_map_region"),
    "hp":       ("HP 영역",        "set_hp_region"),
    "mp":       ("MP 영역",        "set_mp_region"),
    "xp":       ("경험치 영역",    "set_xp_region"),
    "cooldown": ("쿨다운 영역",    "set_cooldown_region"),
    "buff":     ("버프 영역",      "set_buff_region"),
}


# 2026-05-05 Cycle 4 — 스킬 토글 + vk default (메모리 feedback_vk_layout_2026_04_20).
# 9개 한국어 스킬 이름 = HealerWorkerV2.set_skill_enabled key_map 의 키.
SKILL_TOGGLE_DEFS: list = [
    # (한국어 이름, default enabled, 설명)
    ("자힐",         True,  "HP 임계 자힐 (block A burst)"),
    ("부활",         True,  "자가부활 (HP=0 edge)"),
    ("공력증강",     True,  "공증 (MP 임계 edge)"),
    ("백호의희원",   True,  "백호 (cooldown ready)"),
    ("파력무참",     True,  "파력 (cooldown + offset, buff active=coord_tol 1)"),
    ("파혼술",       True,  "파혼 (격수 혼마술 edge)"),
    ("무장",         True,  "무장 (격수 무장 사라짐 edge)"),
    ("보호",         True,  "보호 (격수 보호 사라짐 edge)"),
    ("금강불체",     False, "manual-only (UI 토글로 ON 시 수동 발동 가능)"),
]

# 메모리 feedback_vk_layout_2026_04_20.md 기반 default. 사용자가 GUI 외 경로로
# 변경하지 않으면 이 값을 worker 에 일괄 주입.
SKILL_VK_DEFAULTS: Dict[str, int] = {
    "메인힐":          0x61,  # NUMPAD1
    "혼마술":          0x62,  # NUMPAD2
    "공력증강":        0x63,  # NUMPAD3
    "백호의희원":      0x64,  # NUMPAD4
    "백호의희원첨":    0x65,  # NUMPAD5
    "부활":            0x66,  # NUMPAD6
    "파혼술":          0x67,  # NUMPAD7
    "파력무참":        0x68,  # NUMPAD8
    "금강불체":        0x60,  # NUMPAD0
}

# NumLockCycler slots (default). 메인힐+혼마술+금강 토글 자동 시전 슬롯.
PRIMARY_VKS_DEFAULT: list = [0x61, 0x62, 0x60]


class _LogHandler(logging.Handler):
    """worker / src_v2 로그를 GUI 로그박스에 tail."""
    def __init__(self, callback: Callable[[str], None]):
        super().__init__()
        self._cb = callback
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._cb(self.format(record))
        except Exception:  # noqa: BLE001
            pass


class V2MainWindow(QtWidgets.QMainWindow):
    """v2 자체 GUI — Healer / Attacker 공용.

    role = "healer" | "attacker".
    worker 는 HealerWorkerV2 또는 AttackerWorkerV2 (둘 다 .store .start() .stop()
    + set_*_region() 인터페이스 동일).
    """

    def __init__(self,
                 role: str,
                 cfg: Any,
                 worker: Any,
                 cfg_yaml_path: Optional[str] = None,
                 worker_factory: Optional[Callable[[], Any]] = None,
                 parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.role = role
        self.cfg = cfg
        self.worker = worker
        # 2026-05-05 — 재시작 RuntimeError fix.
        # BaseWatcher 가 threading.Thread 직접 상속이라 stop 후 같은 인스턴스
        # start() 다시 호출 시 RuntimeError("threads can only be started once").
        # _on_stop 시 self.worker=None, _on_start 시 worker is None 이면 factory()
        # 호출로 새 인스턴스 생성. healer_gui_v2._run_v2_native /
        # attacker_v2._run_gui 가 factory 람다 전달.
        self._worker_factory: Optional[Callable[[], Any]] = worker_factory
        self._cfg_yaml_path = cfg_yaml_path or "config.yaml"
        self._regions: Dict[str, Tuple[int, int, int, int]] = {}
        self._armed: bool = False
        self._log_buf: deque[str] = deque(maxlen=200)
        self._region_defs = (
            REGION_DEFS_HEALER if role == "healer" else REGION_DEFS_ATTACKER
        )
        self._picker: Optional[Any] = None  # 활성 RegionPicker 보관
        # 2026-05-05 Cycle 4-2 — 오버레이 2종.
        self._game_overlay: Optional[Any] = None
        self._alert_overlay: Optional[Any] = None
        # 알림 dedup (동일 메시지 1초 내 중복 push 차단).
        self._last_alert_msg: str = ""
        self._last_alert_ts: float = 0.0
        # 2026-05-05 Cycle 4-3 — 격수 측 오버레이 3종.
        self._healer_status_overlay: Optional[Any] = None
        self._hunter_helper_overlay: Optional[Any] = None
        self._skill_range_overlay: Optional[Any] = None
        # 2026-05-05 Cycle 4-4 — Dialog 4종 (lazy 인스턴스, 첫 클릭에 생성).
        self._skill_dlg: Optional[Any] = None
        self._param_dlg: Optional[Any] = None
        self._net_dlg: Optional[Any] = None
        self._hunt_report_dlg: Optional[Any] = None
        # 2026-05-05 Cycle 4-5 — StatusStrip(inline) / RegionOverlay(toplevel).
        self._status_strip: Optional[Any] = None
        self._region_overlay: Optional[Any] = None
        # 2026-05-05 Cycle 4-6 — ControlListener / Heartbeat (QThread).
        self._ctrl_listener: Optional[Any] = None
        self._heartbeat: Optional[Any] = None
        # 2026-05-05 Cycle 4-11 — msw 창 이동 추적 (영역 자동 shift).
        # v1 main_window.py:2895-2954 패턴 + 메모리 feedback_region_follow_msw.
        self._msw_last_origin: Optional[Tuple[int, int]] = None

        title = "옛바 v2 (힐러)" if role == "healer" else "옛바 v2 (격수)"
        self.setWindowTitle(title)
        # 2026-05-05 사이즈 정정 — main_window_v2 와 동치 (480×820). 사용자 환경
        # 1920×1080 + 게임 1280×720 우측 빈 공간(640×1080)에 들어가도록.
        # setFixedSize 대신 minimum + resize — 사용자가 늘릴 수 있게.
        self.setMinimumSize(480, 700)
        self.resize(480, 820)
        # 2026-05-05 — 인라인 다크 QSS 시도했으나 StatusStrip / QFormLayout label
        # 등 child widget 의 자체 css 가 다크 배경 위에 어두운 글자로 충돌 →
        # 사용자 가동 시 텔레메트리/기본정보 텍스트 가독성 망가짐. 또한 button
        # padding 4px 이 영역 picker grid 압축 유발. light theme 으로 복귀.
        # 다크 적용은 styles.py 또는 child widget 별 QSS 일괄 정비 후 별도 작업.
        self._build_ui()

        # 1) cfg.yaml 에서 영역 자동 로드 → worker 주입은 worker.start() 직후.
        self._load_regions_from_cfg()
        # 2) 로그 tail handler 부착.
        self._attach_log_handler()
        # 3) 텔레메트리 1Hz 타이머.
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._refresh_telemetry)
        self._timer.start(1000)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        """2026-05-05 — QTabWidget 분리 구조.

        Top (탭 밖, 항상 보임):
            시작/정지/ARMED ctl + Dialog 4버튼

        탭:
            "메인"  : 자힐/공증/파력 임계 + 스킬 활성 9개 + 기타 토글 +
                      오버레이 토글 + (격수 오버레이) + 로그
            "영역"  : RegionPicker 8개 (한 번 지정 후 cfg.yaml 자동 저장)
            "상태"  : StatusStrip(기본 정보) + 텔레메트리(1Hz)
        """
        cw = QtWidgets.QWidget(self)
        self.setCentralWidget(cw)
        L = QtWidgets.QVBoxLayout(cw)

        # ===== Top: 시작/정지/ARMED — 탭 밖, 항상 보임 =====
        ctl = QtWidgets.QHBoxLayout()
        self.btn_start = QtWidgets.QPushButton("▶ 시작")
        self.btn_stop = QtWidgets.QPushButton("■ 정지")
        self.btn_stop.setEnabled(False)
        self.chk_armed = QtWidgets.QCheckBox("ARMED (실제 키 입력)")
        ctl.addWidget(self.btn_start)
        ctl.addWidget(self.btn_stop)
        ctl.addWidget(self.chk_armed)
        ctl.addStretch(1)
        L.addLayout(ctl)
        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop.clicked.connect(self._on_stop)
        self.chk_armed.stateChanged.connect(self._on_armed_changed)

        # ===== Top: Dialog 메뉴 4버튼 (탭 밖, 항상 보임) =====
        dlg_row = QtWidgets.QHBoxLayout()
        self.btn_skill_dlg = QtWidgets.QPushButton("스킬 ...")
        self.btn_skill_dlg.setEnabled(SkillDialog is not None)
        self.btn_skill_dlg.clicked.connect(self._open_skill_dialog)
        dlg_row.addWidget(self.btn_skill_dlg)
        self.btn_param_dlg = QtWidgets.QPushButton("파라미터 ...")
        self.btn_param_dlg.setEnabled(ParamDialog is not None)
        self.btn_param_dlg.clicked.connect(self._open_param_dialog)
        dlg_row.addWidget(self.btn_param_dlg)
        self.btn_net_dlg = QtWidgets.QPushButton("네트워크 ...")
        self.btn_net_dlg.setEnabled(NetworkDialog is not None)
        self.btn_net_dlg.clicked.connect(self._open_network_dialog)
        dlg_row.addWidget(self.btn_net_dlg)
        self.btn_hunt_report = QtWidgets.QPushButton("사냥 리포트")
        self.btn_hunt_report.setEnabled(HuntReportDialog is not None)
        self.btn_hunt_report.clicked.connect(self._open_hunt_report_dialog)
        dlg_row.addWidget(self.btn_hunt_report)
        dlg_row.addStretch(1)
        L.addLayout(dlg_row)

        # ===== QTabWidget — 메인 / 영역 / 상태 =====
        # 각 탭은 QScrollArea 로 감싸 480 폭 안에서 컨텐츠 overflow 시 스크롤.
        self._tabs = QtWidgets.QTabWidget()
        L.addWidget(self._tabs, stretch=1)

        def _scroll(content: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
            sa = QtWidgets.QScrollArea()
            sa.setWidgetResizable(True)
            sa.setFrameShape(QtWidgets.QFrame.NoFrame)
            sa.setWidget(content)
            return sa

        # ----- 탭 1: "메인" (스킬/토글/오버레이/로그) -----
        tab_main = QtWidgets.QWidget()
        mL = QtWidgets.QVBoxLayout(tab_main)

        if self.role == "healer":
            grp_cfg = QtWidgets.QGroupBox("자힐/공증 임계 + 파력 오프셋")
            cL = QtWidgets.QFormLayout(grp_cfg)
            self.spn_self_heal = QtWidgets.QSpinBox()
            self.spn_self_heal.setRange(0, 100); self.spn_self_heal.setValue(50)
            self.spn_self_heal.valueChanged.connect(self._on_self_heal_changed)
            cL.addRow("자힐 HP %:", self.spn_self_heal)
            self.spn_gyoung = QtWidgets.QSpinBox()
            self.spn_gyoung.setRange(0, 100); self.spn_gyoung.setValue(30)
            self.spn_gyoung.valueChanged.connect(self._on_gyoung_changed)
            cL.addRow("공증 MP %:", self.spn_gyoung)
            self.spn_parlyuk_off = QtWidgets.QDoubleSpinBox()
            self.spn_parlyuk_off.setRange(0.0, 60.0)
            self.spn_parlyuk_off.setSingleStep(0.5)
            self.spn_parlyuk_off.setValue(0.0)
            self.spn_parlyuk_off.setSuffix(" s")
            self.spn_parlyuk_off.valueChanged.connect(self._on_parlyuk_offset_changed)
            cL.addRow("파력 offset:", self.spn_parlyuk_off)
            mL.addWidget(grp_cfg)

            grp_skills = QtWidgets.QGroupBox(
                "스킬 활성 (체크 해제 시 해당 룰/시퀀스 fire 안 함)"
            )
            sL = QtWidgets.QGridLayout(grp_skills)
            self._skill_chks: Dict[str, QtWidgets.QCheckBox] = {}
            for i, (name, default_on, tooltip) in enumerate(SKILL_TOGGLE_DEFS):
                chk = QtWidgets.QCheckBox(name)
                chk.setChecked(bool(default_on))
                chk.setToolTip(tooltip)
                chk.stateChanged.connect(
                    lambda _st, _n=name: self._on_skill_toggle(_n)
                )
                sL.addWidget(chk, i // 3, i % 3)
                self._skill_chks[name] = chk
            mL.addWidget(grp_skills)

            grp_misc = QtWidgets.QGroupBox("기타 토글")
            misL = QtWidgets.QHBoxLayout(grp_misc)
            self.chk_follow_only = QtWidgets.QCheckBox(
                "FOLLOW-ONLY (시전 차단, 추종만)"
            )
            self.chk_follow_only.setChecked(False)
            self.chk_follow_only.stateChanged.connect(self._on_follow_only_changed)
            misL.addWidget(self.chk_follow_only)
            misL.addStretch(1)
            mL.addWidget(grp_misc)

        # 오버레이 토글 (양 role 공용).
        grp_overlay = QtWidgets.QGroupBox("오버레이 (게임창 위 표시)")
        oL = QtWidgets.QHBoxLayout(grp_overlay)
        self.chk_game_overlay = QtWidgets.QCheckBox("쿨/사냥 분석")
        self.chk_game_overlay.setChecked(False)
        self.chk_game_overlay.setEnabled(GameOverlay is not None)
        if GameOverlay is None:
            self.chk_game_overlay.setToolTip(
                "src.ui.overlay.GameOverlay import 실패"
            )
        self.chk_game_overlay.stateChanged.connect(self._on_game_overlay_toggled)
        oL.addWidget(self.chk_game_overlay)
        self.chk_alert_overlay = QtWidgets.QCheckBox("스킬 알림")
        self.chk_alert_overlay.setChecked(False)
        self.chk_alert_overlay.setEnabled(SkillAlertOverlay is not None)
        self.chk_alert_overlay.stateChanged.connect(self._on_alert_overlay_toggled)
        oL.addWidget(self.chk_alert_overlay)
        self.chk_region_overlay = QtWidgets.QCheckBox("영역 시각화")
        self.chk_region_overlay.setChecked(False)
        self.chk_region_overlay.setEnabled(RegionOverlay is not None)
        self.chk_region_overlay.stateChanged.connect(self._on_region_overlay_toggled)
        oL.addWidget(self.chk_region_overlay)
        oL.addStretch(1)
        mL.addWidget(grp_overlay)

        # 격수 전용 오버레이 (role="attacker" 한정).
        if self.role == "attacker":
            grp_atk_overlay = QtWidgets.QGroupBox("격수 전용 오버레이")
            aoL = QtWidgets.QGridLayout(grp_atk_overlay)
            self.chk_healer_status = QtWidgets.QCheckBox("힐러 HP/MP")
            self.chk_healer_status.setChecked(False)
            self.chk_healer_status.setEnabled(HealerStatusOverlay is not None)
            self.chk_healer_status.stateChanged.connect(
                self._on_healer_status_toggled
            )
            aoL.addWidget(self.chk_healer_status, 0, 0)
            self.chk_hunter_helper = QtWidgets.QCheckBox("헬퍼 패널")
            self.chk_hunter_helper.setChecked(False)
            self.chk_hunter_helper.setEnabled(HunterHelperOverlay is not None)
            self.chk_hunter_helper.stateChanged.connect(
                self._on_hunter_helper_toggled
            )
            aoL.addWidget(self.chk_hunter_helper, 0, 1)
            self.chk_skill_range = QtWidgets.QCheckBox("스킬 범위 HUD")
            self.chk_skill_range.setChecked(False)
            self.chk_skill_range.setEnabled(SkillRangeOverlay is not None)
            self.chk_skill_range.stateChanged.connect(
                self._on_skill_range_toggled
            )
            aoL.addWidget(self.chk_skill_range, 0, 2)
            mL.addWidget(grp_atk_overlay)

        # 로그 박스 — 메인 탭 하단 stretch=1.
        grp_log = QtWidgets.QGroupBox("로그 (최근 200줄)")
        logL = QtWidgets.QVBoxLayout(grp_log)
        self.txt_log = QtWidgets.QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumBlockCount(200)
        self.txt_log.setStyleSheet("font-family:Consolas; font-size:11px;")
        logL.addWidget(self.txt_log)
        mL.addWidget(grp_log, stretch=1)

        self._tabs.addTab(_scroll(tab_main), "메인")

        # ----- 탭 2: "영역" (RegionPicker 8개) -----
        tab_region = QtWidgets.QWidget()
        rL = QtWidgets.QVBoxLayout(tab_region)
        grp_region = QtWidgets.QGroupBox("영역 지정 (드래그 후 cfg.yaml 자동 저장)")
        gL = QtWidgets.QGridLayout(grp_region)
        gL.setColumnStretch(0, 0)
        gL.setColumnStretch(1, 1)
        self._region_btns: Dict[str, QtWidgets.QPushButton] = {}
        self._region_lbls: Dict[str, QtWidgets.QLabel] = {}
        row = 0
        for key, (label, _setter) in self._region_defs.items():
            btn = QtWidgets.QPushButton(f"{label} 지정")
            lbl = QtWidgets.QLabel("(미지정)")
            lbl.setStyleSheet("color:#888")
            btn.clicked.connect(lambda _ck, k=key: self._on_pick_region(k))
            gL.addWidget(btn, row, 0)
            gL.addWidget(lbl, row, 1)
            self._region_btns[key] = btn
            self._region_lbls[key] = lbl
            row += 1
        rL.addWidget(grp_region)
        rL.addStretch(1)
        self._tabs.addTab(_scroll(tab_region), "영역")

        # ----- 탭 3: "상태" (StatusStrip + 텔레메트리) -----
        tab_status = QtWidgets.QWidget()
        stL = QtWidgets.QVBoxLayout(tab_status)
        if StatusStrip is not None:
            try:
                self._status_strip = StatusStrip(self)
                stL.addWidget(self._status_strip)
            except Exception:  # noqa: BLE001
                log.exception("StatusStrip 생성 fail")
                self._status_strip = None

        grp_tel = QtWidgets.QGroupBox("텔레메트리 (1Hz)")
        tL = QtWidgets.QFormLayout(grp_tel)
        self.lbl_running = QtWidgets.QLabel("정지")
        self.lbl_hp = QtWidgets.QLabel("-")
        self.lbl_mp = QtWidgets.QLabel("-")
        self.lbl_coord = QtWidgets.QLabel("-")
        self.lbl_map = QtWidgets.QLabel("-")
        self.lbl_atk_coord = QtWidgets.QLabel("-")
        self.lbl_atk_map = QtWidgets.QLabel("-")
        self.lbl_red = QtWidgets.QLabel("-")
        self.lbl_update_count = QtWidgets.QLabel("0")
        self.lbl_eye_age = QtWidgets.QLabel("-")
        tL.addRow("상태:", self.lbl_running)
        tL.addRow("HP:", self.lbl_hp)
        tL.addRow("MP:", self.lbl_mp)
        tL.addRow("내 좌표:", self.lbl_coord)
        tL.addRow("내 맵:", self.lbl_map)
        tL.addRow("격수 좌표:", self.lbl_atk_coord)
        tL.addRow("격수 맵:", self.lbl_atk_map)
        tL.addRow("빨탭:", self.lbl_red)
        tL.addRow("snap update:", self.lbl_update_count)
        tL.addRow("eye age:", self.lbl_eye_age)
        stL.addWidget(grp_tel)
        stL.addStretch(1)
        self._tabs.addTab(_scroll(tab_status), "상태")

    # ------------------------------------------------------------------ logs
    def _attach_log_handler(self) -> None:
        self._log_handler = _LogHandler(self._on_log_line)
        self._log_handler.setLevel(logging.INFO)
        # src_v2.* 전체 + src.* (어댑터/워커 wrap 통해 흘러나오는 로그) tail.
        for nm in ("src_v2", "src"):
            logging.getLogger(nm).addHandler(self._log_handler)

    def _on_log_line(self, line: str) -> None:
        # logging emit 은 워커 thread 에서 올 수 있으므로 invokeMethod 로 GUI thread.
        QtCore.QMetaObject.invokeMethod(
            self.txt_log, "appendPlainText",
            QtCore.Qt.QueuedConnection,
            QtCore.Q_ARG(str, line),
        )

    # ------------------------------------------------------------------ start/stop
    def _on_start(self) -> None:
        try:
            # 2026-05-05 — worker 가 None 이면 factory 로 재생성 (정지 후 재시작).
            if self.worker is None:
                if self._worker_factory is None:
                    raise RuntimeError(
                        "worker is None and no factory provided — 재시작 불가"
                    )
                self._append_log("[v2-gui] worker 재생성 (정지 후 재시작)")
                self.worker = self._worker_factory()
            # 영역 cfg 적용 (worker 가 살아있는 watcher 의 adapter 에 직접 주입).
            self._inject_all_regions()
            # 2026-05-05 Cycle 4: 스킬/vk/임계 cfg 일괄 주입 (시작 직전).
            if self.role == "healer":
                self._inject_skill_cfg()
            self.worker.start()
            # 2026-05-05 — ControlListener / Heartbeat 자동 start 비활성.
            # 사용자 가동에서 cfg.net.port (54545) 가 UDP State 수신과 충돌해
            # 격수 좌표 미수신 의심. v1 main_window 는 사용자 명시 신호로만
            # 활성. v2 도 같은 패턴 — 후속 보강에서 별도 cfg/UI 토글로 제공.
            # self._start_ctrl_listener()
            # self._start_heartbeat()
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
            self.lbl_running.setText("실행 중")
            log.info("[v2-gui] worker start (role=%s)", self.role)
        except Exception as e:  # noqa: BLE001
            log.exception("worker start fail: %s", e)
            QtWidgets.QMessageBox.critical(self, "시작 실패", str(e))

    def _on_stop(self) -> None:
        try:
            if self.worker is not None:
                self.worker.stop()
        except Exception:  # noqa: BLE001
            log.exception("worker stop fail")
        # 2026-05-05 Cycle 4-6: ControlListener + Heartbeat 정지.
        self._stop_ctrl_listener()
        self._stop_heartbeat()
        # 2026-05-05 — 정지 후 worker = None.
        # 재시작 시 factory 가 새 인스턴스 생성 (BaseWatcher 재시작 RuntimeError 방지).
        # factory 가 없으면 worker 그대로 보존 (재시작 시 RuntimeError 가능 — UX 안내).
        if self._worker_factory is not None:
            self.worker = None
            self._append_log("[v2-gui] worker=None (재시작 시 factory 재생성)")
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.lbl_running.setText("정지")
        log.info("[v2-gui] worker stop")

    # ------------------------------------------------------------------ ctrl/heartbeat
    # 2026-05-05 Cycle 4-6 — ControlListener + Heartbeat 라이프사이클.
    def _start_ctrl_listener(self) -> None:
        if ControlListener is None:
            return
        if self._ctrl_listener is not None:
            try:
                if self._ctrl_listener.isRunning():
                    return
            except Exception:  # noqa: BLE001
                pass
        try:
            net = getattr(self.cfg, "net", None)
            my_idx = int(getattr(net, "healer_idx", 0)) if net else 0
            bind_host = str(getattr(net, "bind_host", "0.0.0.0") if net else "0.0.0.0")
            port = int(getattr(net, "port", 54545)) if net else 54545
            self._ctrl_listener = ControlListener(bind_host, port, my_idx)
            # cmd_received / attacker_seen signal — 단순 log.
            for sig_name, slot in (
                ("cmd_received", self._on_remote_cmd),
                ("attacker_seen", self._on_attacker_seen_evt),
            ):
                try:
                    sig = getattr(self._ctrl_listener, sig_name, None)
                    if sig is not None:
                        sig.connect(slot)
                except Exception:  # noqa: BLE001
                    pass
            self._ctrl_listener.start()
            self._append_log(
                f"[CTRL-LISTEN] {bind_host}:{port} idx={my_idx} 시작"
            )
        except Exception as e:  # noqa: BLE001
            log.exception("ControlListener 시작 실패")
            self._append_log(f"[CTRL-LISTEN] 시작 실패: {e}")
            self._ctrl_listener = None

    def _stop_ctrl_listener(self) -> None:
        if self._ctrl_listener is None:
            return
        try:
            self._ctrl_listener.stop()
        except Exception:  # noqa: BLE001
            log.exception("ControlListener stop fail")
        try:
            self._ctrl_listener.wait(1500)
        except Exception:  # noqa: BLE001
            pass
        self._ctrl_listener = None
        self._append_log("[CTRL-LISTEN] 정지")

    def _on_remote_cmd(self, *args, **kwargs) -> None:
        """원격 제어 명령 수신 시 호출. 단순 log (후속에서 ControlCmd 처리 hook)."""
        self._append_log(f"[CTRL-RECV] cmd args={args} kwargs={kwargs}")

    def _on_attacker_seen_evt(self, *args, **kwargs) -> None:
        """ControlListener 가 격수 IP 학습 시 호출. heartbeat 에 set_attacker_addr."""
        if self._heartbeat is None:
            return
        try:
            # args 형태: (ip, port) 또는 (addr_tuple) — 단순 first-positional 시도.
            if args and isinstance(args[0], tuple) and len(args[0]) >= 2:
                ip, port = args[0][0], int(args[0][1])
            elif len(args) >= 2:
                ip, port = str(args[0]), int(args[1])
            else:
                return
            fn = getattr(self._heartbeat, "set_attacker_addr", None)
            if callable(fn):
                fn(str(ip), int(port))
                self._append_log(f"[CTRL-RECV] attacker_seen {ip}:{port}")
        except Exception:  # noqa: BLE001
            log.exception("attacker_seen handle fail")

    def _start_heartbeat(self) -> None:
        cls = HealerHeartbeat if self.role == "healer" else AttackerHeartbeat
        if cls is None:
            return
        if self._heartbeat is not None:
            try:
                if self._heartbeat.isRunning():
                    return
            except Exception:  # noqa: BLE001
                pass
        try:
            self._heartbeat = cls(self.cfg)
            self._heartbeat.start()
            self._append_log(f"[HEARTBEAT] {cls.__name__} 시작")
        except Exception as e:  # noqa: BLE001
            log.exception("Heartbeat 시작 실패")
            self._append_log(f"[HEARTBEAT] 시작 실패: {e}")
            self._heartbeat = None

    def _stop_heartbeat(self) -> None:
        if self._heartbeat is None:
            return
        try:
            self._heartbeat.stop()
        except Exception:  # noqa: BLE001
            log.exception("Heartbeat stop fail")
        try:
            self._heartbeat.wait(1500)
        except Exception:  # noqa: BLE001
            pass
        self._heartbeat = None
        self._append_log("[HEARTBEAT] 정지")

    def _on_armed_changed(self, _state: int) -> None:
        self._armed = bool(self.chk_armed.isChecked())
        # 2026-05-05 Cycle 4-3: worker.set_armed 우선, 없으면 fallback.
        try:
            fn = getattr(self.worker, "set_armed", None)
            if callable(fn):
                fn(self._armed)
                return
            disp = getattr(self.worker, "dispatcher", None)
            if disp is not None and hasattr(disp, "armed"):
                disp.armed = self._armed
            elif hasattr(self.worker, "armed"):
                self.worker.armed = self._armed
        except Exception:  # noqa: BLE001
            log.exception("armed toggle fail")

    def _on_self_heal_changed(self, v: int) -> None:
        # 2026-05-05 Cycle 4-3: worker.set_self_heal_hp_thr 우선.
        try:
            fn = getattr(self.worker, "set_self_heal_hp_thr", None)
            if callable(fn):
                fn(int(v))
                return
            cfg = getattr(self.worker, "cfg", None)
            if cfg is not None and hasattr(cfg, "rule_cfg"):
                cfg.rule_cfg["self_heal_hp_thr"] = int(v)
        except Exception:  # noqa: BLE001
            log.exception("self_heal_hp_thr set fail")

    def _on_gyoung_changed(self, v: int) -> None:
        # 2026-05-05 Cycle 4-3: worker.set_gyoungryeok_mp_thr 우선.
        try:
            fn = getattr(self.worker, "set_gyoungryeok_mp_thr", None)
            if callable(fn):
                fn(int(v))
                return
            cfg = getattr(self.worker, "cfg", None)
            if cfg is not None and hasattr(cfg, "rule_cfg"):
                cfg.rule_cfg["gyoungryeok_mp_thr"] = int(v)
        except Exception:  # noqa: BLE001
            log.exception("gyoungryeok_mp_thr set fail")

    def _on_parlyuk_offset_changed(self, v: float) -> None:
        # 2026-05-05 Cycle 4-3: worker.set_parlyuk_offset.
        try:
            fn = getattr(self.worker, "set_parlyuk_offset", None)
            if callable(fn):
                fn(float(v))
        except Exception:  # noqa: BLE001
            log.exception("parlyuk_offset set fail")

    def _on_follow_only_changed(self, _state: int) -> None:
        # 2026-05-05 Cycle 4-3: worker.set_follow_only.
        on = bool(self.chk_follow_only.isChecked())
        try:
            fn = getattr(self.worker, "set_follow_only", None)
            if callable(fn):
                fn(on)
        except Exception:  # noqa: BLE001
            log.exception("follow_only set fail")
        self._append_log(f"[v2-gui] FOLLOW-ONLY {'ON' if on else 'OFF'}")

    # ------------------------------------------------------------------ overlay
    def _on_game_overlay_toggled(self, _state: int) -> None:
        """2026-05-05 Cycle 4-2 — GameOverlay show/hide 토글."""
        on = bool(self.chk_game_overlay.isChecked())
        if on:
            if self._game_overlay is None:
                if GameOverlay is None:
                    self._append_log("[v2-gui] GameOverlay 사용 불가 (import 실패)")
                    self.chk_game_overlay.setChecked(False)
                    return
                try:
                    self._game_overlay = GameOverlay()
                except Exception:  # noqa: BLE001
                    log.exception("GameOverlay 생성 fail")
                    self.chk_game_overlay.setChecked(False)
                    return
            try:
                self._game_overlay.show()
                self._append_log("[v2-gui] GameOverlay show")
            except Exception:  # noqa: BLE001
                log.exception("GameOverlay show fail")
        else:
            if self._game_overlay is not None:
                try:
                    self._game_overlay.hide()
                    self._append_log("[v2-gui] GameOverlay hide")
                except Exception:  # noqa: BLE001
                    pass

    def _on_alert_overlay_toggled(self, _state: int) -> None:
        """2026-05-05 Cycle 4-2 — SkillAlertOverlay show/hide 토글."""
        on = bool(self.chk_alert_overlay.isChecked())
        if on:
            if self._alert_overlay is None:
                if SkillAlertOverlay is None:
                    self._append_log("[v2-gui] SkillAlertOverlay 사용 불가")
                    self.chk_alert_overlay.setChecked(False)
                    return
                try:
                    self._alert_overlay = SkillAlertOverlay()
                except Exception:  # noqa: BLE001
                    log.exception("SkillAlertOverlay 생성 fail")
                    self.chk_alert_overlay.setChecked(False)
                    return
            try:
                self._alert_overlay.show()
                self._append_log("[v2-gui] SkillAlertOverlay show")
            except Exception:  # noqa: BLE001
                log.exception("SkillAlertOverlay show fail")
        else:
            if self._alert_overlay is not None:
                try:
                    self._alert_overlay.hide()
                    self._append_log("[v2-gui] SkillAlertOverlay hide")
                except Exception:  # noqa: BLE001
                    pass

    # 2026-05-05 Cycle 4-3 — 격수 측 오버레이 3종 토글 핸들러.
    def _toggle_overlay_generic(
        self, attr_name: str, chk: QtWidgets.QCheckBox,
        cls: Optional[Any], log_label: str,
    ) -> None:
        """공통 토글 헬퍼 — chk on/off → instance create/show/hide."""
        on = bool(chk.isChecked())
        if on:
            if getattr(self, attr_name) is None:
                if cls is None:
                    self._append_log(f"[v2-gui] {log_label} 사용 불가 (import 실패)")
                    chk.setChecked(False)
                    return
                try:
                    setattr(self, attr_name, cls())
                except Exception:  # noqa: BLE001
                    log.exception("%s 생성 fail", log_label)
                    chk.setChecked(False)
                    return
            try:
                getattr(self, attr_name).show()
                self._append_log(f"[v2-gui] {log_label} show")
            except Exception:  # noqa: BLE001
                log.exception("%s show fail", log_label)
        else:
            ov = getattr(self, attr_name)
            if ov is not None:
                try:
                    ov.hide()
                    self._append_log(f"[v2-gui] {log_label} hide")
                except Exception:  # noqa: BLE001
                    pass

    def _on_healer_status_toggled(self, _state: int) -> None:
        self._toggle_overlay_generic(
            "_healer_status_overlay", self.chk_healer_status,
            HealerStatusOverlay, "HealerStatusOverlay",
        )

    def _on_hunter_helper_toggled(self, _state: int) -> None:
        self._toggle_overlay_generic(
            "_hunter_helper_overlay", self.chk_hunter_helper,
            HunterHelperOverlay, "HunterHelperOverlay",
        )
        # alert overlay wire (있으면).
        if self._hunter_helper_overlay is not None and self._alert_overlay is not None:
            try:
                fn = getattr(self._hunter_helper_overlay, "set_alert_overlay", None)
                if callable(fn):
                    fn(self._alert_overlay)
            except Exception:  # noqa: BLE001
                pass

    def _on_skill_range_toggled(self, _state: int) -> None:
        self._toggle_overlay_generic(
            "_skill_range_overlay", self.chk_skill_range,
            SkillRangeOverlay, "SkillRangeOverlay",
        )

    # 2026-05-05 Cycle 4-5 — RegionOverlay 토글 (showFullScreen + set_regions).
    def _on_region_overlay_toggled(self, _state: int) -> None:
        on = bool(self.chk_region_overlay.isChecked())
        if on:
            if self._region_overlay is None:
                if RegionOverlay is None:
                    self._append_log("[v2-gui] RegionOverlay 사용 불가")
                    self.chk_region_overlay.setChecked(False)
                    return
                try:
                    self._region_overlay = RegionOverlay()
                except Exception:  # noqa: BLE001
                    log.exception("RegionOverlay 생성 fail")
                    self.chk_region_overlay.setChecked(False)
                    return
            try:
                # 현재 _regions 일괄 적용.
                self._region_overlay.set_regions(dict(self._regions))
                self._region_overlay.showFullScreen()
                self._append_log(
                    f"[v2-gui] RegionOverlay show ({len(self._regions)} 영역)"
                )
            except Exception:  # noqa: BLE001
                log.exception("RegionOverlay show fail")
        else:
            if self._region_overlay is not None:
                try:
                    self._region_overlay.hide()
                    self._append_log("[v2-gui] RegionOverlay hide")
                except Exception:  # noqa: BLE001
                    pass

    def push_alert(self, msg: str, duration_sec: float = 3.0) -> None:
        """외부 트리거용 public API — 알림 push.

        2026-05-05 Cycle 4-2. 동일 메시지 1초 내 중복 차단.
        future hook: cooldown watcher 의 ready edge 또는 buff 임박 edge 가 호출.
        """
        if not msg or self._alert_overlay is None:
            return
        import time as _t
        now = _t.monotonic()
        if msg == self._last_alert_msg and (now - self._last_alert_ts) < 1.0:
            return
        self._last_alert_msg = msg
        self._last_alert_ts = now
        try:
            self._alert_overlay.push_alert(msg, duration_sec=float(duration_sec))
        except Exception:  # noqa: BLE001
            log.exception("push_alert fail")

    def _refresh_overlay(self, snap: Any) -> None:
        """2026-05-05 Cycle 4-2/4-3 — _refresh_telemetry 에서 매 tick 호출.
        GameOverlay/HealerStatusOverlay/HunterHelperOverlay/SkillRangeOverlay 갱신.
        """
        # 2026-05-05 Cycle 4-2 — GameOverlay (양 role).
        if self._game_overlay is not None and self._game_overlay.isVisible():
            try:
                idx = int(getattr(snap, "src_idx", 0) or 0)
                d = {
                    "nickname":     str(getattr(snap, "nickname", "") or ""),
                    "cd_parlyuk":   int(getattr(snap, "cd_parlyuk", -1) or -1),
                    "cd_baekho":    int(getattr(snap, "cd_baekho", -1) or -1),
                    "armed":        bool(getattr(snap, "armed", False)),
                    "xp_per_hour":  int(getattr(snap, "xp_per_hour", 0) or 0),
                }
                self._game_overlay.update_healer(idx, d)
                # 사냥 분석 — 최소 dict (현재 v2 store 에 분석 데이터 없음).
                # 추후 hook 으로 map_history / xp_total 등 채울 수 있음.
                self._game_overlay.update_analytics(None)
            except Exception:  # noqa: BLE001
                log.exception("game overlay refresh fail")

        # 2026-05-05 Cycle 4-3 — 격수 측 오버레이 3종 (role="attacker" 한정).
        if self.role != "attacker":
            return

        # HealerStatusOverlay — 격수 화면에 힐러 HP/MP/cd 막대.
        # 데이터 source: attacker_worker_v2._handle_cd_report 가 받은 CooldownReport.
        # 현재 v2 snapshot 에 healer_cooldowns 필드 없어 단순 hook 만 (후속 단계에서
        # snapshot 확장 또는 worker 가 직접 overlay.update_healer 호출).
        if (self._healer_status_overlay is not None
                and self._healer_status_overlay.isVisible()):
            try:
                d = {
                    "nickname":     str(getattr(snap, "nickname", "") or ""),
                    "cd_parlyuk":   int(getattr(snap, "cd_parlyuk", -1) or -1),
                    "cd_baekho":    int(getattr(snap, "cd_baekho", -1) or -1),
                    "hp_pct":       int(getattr(snap, "hp", -1) or -1),
                    "mp_pct":       int(getattr(snap, "mp", -1) or -1),
                }
                self._healer_status_overlay.update_healer(0, d)
            except Exception:  # noqa: BLE001
                log.exception("healer_status overlay refresh fail")

        # HunterHelperOverlay — 격수 sub/rank 별 쿨 표시. 격수 자기 cd 는
        # snap 에 있으나 cooldown_reading.skills 에 한국어 키.
        if (self._hunter_helper_overlay is not None
                and self._hunter_helper_overlay.isVisible()):
            try:
                cd_reading = getattr(snap, "cooldown_reading", None)
                own_cds: Dict[str, int] = {}
                if cd_reading is not None:
                    skills = getattr(cd_reading, "skills", {}) or {}
                    for k, v in skills.items():
                        try:
                            own_cds[str(k)] = int(v) if v is not None else -1
                        except Exception:
                            pass
                self._hunter_helper_overlay.update_own_cds(own_cds)
                # healer_cooldowns 단일 idx (v2 snap 에 다중 힐러 데이터 없음).
                # 후속에서 attacker_worker_v2 가 _handle_cd_report row 별 직접 갱신.
                self._hunter_helper_overlay.update_data({})
            except Exception:  # noqa: BLE001
                log.exception("hunter_helper overlay refresh fail")

        # SkillRangeOverlay — 격수 자기 빨탭 좌표 + 캐릭터 위치.
        if (self._skill_range_overlay is not None
                and self._skill_range_overlay.isVisible()):
            try:
                red_box = getattr(snap, "self_red_box", None)
                red_present = bool(red_box is not None)
                cx = int(getattr(snap, "self_red_cx", 0) or 0)
                cy = int(getattr(snap, "self_red_cy", 0) or 0)
                self._skill_range_overlay.update_state(red_present, cx, cy)
            except Exception:  # noqa: BLE001
                log.exception("skill_range overlay refresh fail")

    # ------------------------------------------------------------------ skill cfg
    def _on_skill_toggle(self, name: str) -> None:
        # 2026-05-05 Cycle 4-1: 스킬 체크박스 → worker.set_skill_enabled.
        chk = self._skill_chks.get(name)
        if chk is None:
            return
        on = bool(chk.isChecked())
        try:
            fn = getattr(self.worker, "set_skill_enabled", None)
            if callable(fn):
                fn(name, on)
                self._append_log(f"[v2-gui] {name} {'ON' if on else 'OFF'}")
        except Exception:  # noqa: BLE001
            log.exception("set_skill_enabled fail name=%s", name)

    def _inject_skill_cfg(self) -> None:
        """시작 직전 스킬/vk/cycler slots 일괄 주입.

        2026-05-05 Cycle 4 — _compat_healer_facade 의 cfg setter sync 와 동치.
        UI 체크박스 + default vk + primary slots 를 worker 에 일괄 적용.
        """
        # 1) skill_enabled — UI 체크박스 상태 일괄 주입.
        try:
            fn = getattr(self.worker, "set_skill_enabled", None)
            if callable(fn):
                for name, chk in self._skill_chks.items():
                    try:
                        fn(name, bool(chk.isChecked()))
                    except Exception:  # noqa: BLE001
                        log.exception("inject set_skill_enabled name=%s", name)
        except Exception:  # noqa: BLE001
            log.exception("inject skill_enabled fail")
        # 2) skill_vk — default 매핑 (메모리 4-20).
        try:
            fn = getattr(self.worker, "set_skill_vk", None)
            if callable(fn):
                for name, vk in SKILL_VK_DEFAULTS.items():
                    try:
                        fn(name, int(vk))
                    except Exception:  # noqa: BLE001
                        log.exception("inject set_skill_vk name=%s", name)
        except Exception:  # noqa: BLE001
            log.exception("inject skill_vk fail")
        # 3) primary_vks — NumLockCycler slots default.
        try:
            fn = getattr(self.worker, "set_primary_vks", None)
            if callable(fn):
                fn(list(PRIMARY_VKS_DEFAULT))
        except Exception:  # noqa: BLE001
            log.exception("inject primary_vks fail")
        # 4) 임계/오프셋 — UI spinbox 현재 값 일괄 적용.
        try:
            self._on_self_heal_changed(int(self.spn_self_heal.value()))
            self._on_gyoung_changed(int(self.spn_gyoung.value()))
            self._on_parlyuk_offset_changed(float(self.spn_parlyuk_off.value()))
        except Exception:  # noqa: BLE001
            log.exception("inject thresholds fail")
        # 5) follow_only 초기 상태.
        try:
            self._on_follow_only_changed(0)
        except Exception:  # noqa: BLE001
            log.exception("inject follow_only fail")
        self._append_log(
            f"[v2-gui] skill cfg 일괄 주입 — toggles={len(self._skill_chks)} "
            f"vk_defaults={len(SKILL_VK_DEFAULTS)} primary={PRIMARY_VKS_DEFAULT}"
        )

    # ------------------------------------------------------------------ region picker
    def _on_pick_region(self, key: str) -> None:
        if RegionPicker is None:
            QtWidgets.QMessageBox.warning(
                self, "picker 없음", "src.ui.region_picker.RegionPicker import 실패"
            )
            return
        label = self._region_defs[key][0]
        picker = RegionPicker(label=label)
        # 람다 바인딩 시 self/key 캡처.
        def _on_sel(x: int, y: int, w: int, h: int, _k=key) -> None:
            self._on_region_selected(_k, x, y, w, h)
        def _on_cancel(_k=key) -> None:
            self._append_log(f"[v2-gui] {self._region_defs[_k][0]} 선택 취소")
        picker.region_selected.connect(_on_sel)
        picker.cancelled.connect(_on_cancel)
        # 시그널 + show — picker 가 close 되면 GC 되므로 self 에 보관.
        self._picker = picker
        picker.show()

    def _on_region_selected(self, key: str, x: int, y: int, w: int, h: int) -> None:
        self._regions[key] = (int(x), int(y), int(w), int(h))
        self._region_lbls[key].setText(f"{x},{y} {w}x{h}")
        self._region_lbls[key].setStyleSheet("color:#0a0")
        # worker 가 시작되어있으면 즉시 주입. 시작 전이면 _on_start 가 일괄 주입.
        try:
            self._inject_region(key, (x, y, w, h))
        except Exception:  # noqa: BLE001
            log.exception("region inject %s fail", key)
        # cfg.yaml 자동 저장.
        try:
            self._save_regions_to_cfg()
        except Exception:  # noqa: BLE001
            log.exception("cfg yaml save fail")
        self._append_log(
            f"[v2-gui] {self._region_defs[key][0]} 지정 ({x},{y},{w},{h})"
        )

    def _inject_region(self, key: str, region: Tuple[int, int, int, int]) -> None:
        setter_name = self._region_defs[key][1]
        fn = getattr(self.worker, setter_name, None)
        if callable(fn):
            x, y, w, h = region
            fn(int(x), int(y), int(w), int(h))

    def _inject_all_regions(self) -> None:
        for key, region in self._regions.items():
            try:
                self._inject_region(key, region)
            except Exception:  # noqa: BLE001
                log.exception("inject_all_regions %s fail", key)

    # ------------------------------------------------------------------ cfg yaml IO
    def _load_regions_from_cfg(self) -> None:
        """config.yaml 의 v2_regions + v2_msw_origin 섹션에서 영역/창위치 로드.

        2026-05-05 Cycle 4-12 — v2_msw_origin 추가:
          가동 종료 후 게임창 옮기고 재가동하면 origin 비교로 첫 tick 에 즉시
          dx/dy shift. 영역 picker 로 다시 지정할 필요 없음.
        """
        path = Path(self._cfg_yaml_path)
        if not path.is_file():
            return
        try:
            with path.open("r", encoding="utf-8") as f:
                doc = yaml.safe_load(f) or {}
        except Exception:  # noqa: BLE001
            log.exception("cfg load fail")
            return
        if not isinstance(doc, dict):
            return
        regions = doc.get("v2_regions") or {}
        if isinstance(regions, dict):
            for key in self._region_defs.keys():
                v = regions.get(key)
                if not isinstance(v, dict):
                    continue
                try:
                    x = int(v["x"]); y = int(v["y"])
                    w = int(v["w"]); h = int(v["h"])
                    if w > 0 and h > 0:
                        self._regions[key] = (x, y, w, h)
                        if key in self._region_lbls:
                            self._region_lbls[key].setText(f"{x},{y} {w}x{h}")
                            self._region_lbls[key].setStyleSheet("color:#06a")
                except Exception:  # noqa: BLE001
                    continue
        # 2026-05-05 Cycle 4-12 — last msw origin 복원.
        mo = doc.get("v2_msw_origin")
        if isinstance(mo, dict):
            try:
                self._msw_last_origin = (int(mo["x"]), int(mo["y"]))
            except Exception:  # noqa: BLE001
                pass
        if self._regions:
            origin_str = (
                f" + msw_origin {self._msw_last_origin}"
                if self._msw_last_origin is not None else ""
            )
            self._append_log(
                f"[v2-gui] cfg.yaml v2_regions 로드 {len(self._regions)} 건"
                f"{origin_str}"
            )

    def _save_regions_to_cfg(self) -> None:
        path = Path(self._cfg_yaml_path)
        try:
            doc: Dict[str, Any] = {}
            if path.is_file():
                with path.open("r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f)
                    if isinstance(loaded, dict):
                        doc = loaded
            v2r: Dict[str, Any] = {}
            for key, (x, y, w, h) in self._regions.items():
                v2r[key] = {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}
            doc["v2_regions"] = v2r
            # 2026-05-05 Cycle 4-12 — last msw origin 영구화.
            if self._msw_last_origin is not None:
                doc["v2_msw_origin"] = {
                    "x": int(self._msw_last_origin[0]),
                    "y": int(self._msw_last_origin[1]),
                }
            tmp = path.with_suffix(path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False)
            os.replace(str(tmp), str(path))
        except Exception:  # noqa: BLE001
            log.exception("cfg save fail")

    # ------------------------------------------------------------------ telemetry
    def _refresh_telemetry(self) -> None:
        store = getattr(self.worker, "store", None)
        if store is None:
            return
        try:
            snap = store.read()
        except Exception:  # noqa: BLE001
            return
        try:
            self.lbl_hp.setText(self._fmt_hpmp(
                getattr(snap, "hp", -1), getattr(snap, "hp_cur", -1),
                getattr(snap, "hp_max", -1)))
            self.lbl_mp.setText(self._fmt_hpmp(
                getattr(snap, "mp", -1), getattr(snap, "mp_cur", -1),
                getattr(snap, "mp_max", -1)))
            coord = getattr(snap, "healer_coord", None)
            self.lbl_coord.setText(f"{coord[0]},{coord[1]}" if coord else "-")
            self.lbl_map.setText(getattr(snap, "healer_map", "") or "-")
            atk = getattr(snap, "attacker_coord", None)
            self.lbl_atk_coord.setText(
                f"{atk[0]},{atk[1]}" if atk and getattr(snap, "attacker_coord_valid", False) else "-"
            )
            self.lbl_atk_map.setText(getattr(snap, "attacker_map", "") or "-")
            red = getattr(snap, "red_tab_present", False)
            red_pos = getattr(snap, "red_tab_pos", None)
            if red and red_pos:
                self.lbl_red.setText(f"있음 @ {red_pos[0]},{red_pos[1]}")
            else:
                self.lbl_red.setText("없음")
            self.lbl_update_count.setText(str(getattr(snap, "update_count", 0)))
            ts = float(getattr(snap, "last_eye_update_ts", 0.0))
            if ts > 0:
                import time as _t
                age = max(0.0, _t.monotonic() - ts)
                self.lbl_eye_age.setText(f"{age:.2f}s")
            else:
                self.lbl_eye_age.setText("-")
        except Exception:  # noqa: BLE001
            log.exception("telemetry refresh fail")
        # 2026-05-05 Cycle 4-2 — 오버레이 갱신.
        try:
            self._refresh_overlay(snap)
        except Exception:  # noqa: BLE001
            log.exception("overlay refresh dispatch fail")
        # 2026-05-05 Cycle 4-11 — msw 창 이동 시 영역 자동 shift.
        try:
            self._track_msw_origin()
        except Exception:  # noqa: BLE001
            log.exception("msw origin track fail")
        # 2026-05-05 Cycle 4-5 — StatusStrip update_from_frame.
        if self._status_strip is not None:
            try:
                payload = {
                    "state":            self.lbl_running.text(),
                    "hp_pct":           int(getattr(snap, "hp", -1) or -1),
                    "mp_pct":           int(getattr(snap, "mp", -1) or -1),
                    "hp_cur":           int(getattr(snap, "hp_cur", -1) or -1),
                    "mp_cur":           int(getattr(snap, "mp_cur", -1) or -1),
                    "hp_max":           int(getattr(snap, "hp_max", 0) or 0),
                    "mp_max":           int(getattr(snap, "mp_max", 0) or 0),
                    "healer_coord":     getattr(snap, "healer_coord", None),
                    "healer_map":       str(getattr(snap, "healer_map", "") or ""),
                    "attacker_coord":   getattr(snap, "attacker_coord", None),
                    "attacker_map":     str(getattr(snap, "attacker_map", "") or ""),
                    "armed":            bool(getattr(snap, "armed", False)),
                    "fps":              float(getattr(snap, "fps", 0.0) or 0.0),
                    "xp_per_hour":      int(getattr(snap, "xp_per_hour", 0) or 0),
                }
                self._status_strip.update_from_frame(payload)
            except Exception:  # noqa: BLE001
                log.exception("status_strip update fail")

    # ------------------------------------------------------------------ dialogs
    # 2026-05-05 Cycle 4-4 — Dialog 4종 lazy 생성 + show/raise/activate.
    def _ensure_dialog(self, attr_name: str, cls: Optional[Any],
                       cfg_arg: bool, label: str) -> Optional[Any]:
        """공통 헬퍼 — 첫 호출 시 dlg 인스턴스 생성, 이후엔 재사용."""
        if cls is None:
            self._append_log(f"[v2-gui] {label} 사용 불가 (import 실패)")
            return None
        dlg = getattr(self, attr_name)
        if dlg is None:
            try:
                dlg = cls(self.cfg, self) if cfg_arg else cls(self)
            except Exception:  # noqa: BLE001
                log.exception("%s 생성 fail", label)
                self._append_log(
                    f"[v2-gui] {label} 생성 실패 (cfg 호환성 확인 필요)"
                )
                return None
            setattr(self, attr_name, dlg)
        return dlg

    def _open_skill_dialog(self) -> None:
        dlg = self._ensure_dialog("_skill_dlg", SkillDialog, False, "SkillDialog")
        if dlg is None:
            return
        try:
            dlg.show(); dlg.raise_(); dlg.activateWindow()
            self._append_log("[v2-gui] SkillDialog show")
        except Exception:  # noqa: BLE001
            log.exception("SkillDialog show fail")

    def _open_param_dialog(self) -> None:
        dlg = self._ensure_dialog("_param_dlg", ParamDialog, True, "ParamDialog")
        if dlg is None:
            return
        try:
            dlg.show(); dlg.raise_(); dlg.activateWindow()
            self._append_log("[v2-gui] ParamDialog show")
        except Exception:  # noqa: BLE001
            log.exception("ParamDialog show fail")

    def _open_network_dialog(self) -> None:
        dlg = self._ensure_dialog("_net_dlg", NetworkDialog, True, "NetworkDialog")
        if dlg is None:
            return
        # 2026-05-05 — peers_changed signal connect (한 번만).
        # main_window_v2 (compat) 가 connect 안 해서 dialog 변경이 cfg/config.yaml
        # 에 안 반영됐던 버그. V2MainWindow 가 직접 처리.
        if not getattr(dlg, "_peers_changed_connected_v2", False):
            try:
                dlg.peers_changed.connect(self._on_network_changed)
                dlg._peers_changed_connected_v2 = True
            except Exception:  # noqa: BLE001
                log.exception("peers_changed connect fail")
        try:
            dlg.show(); dlg.raise_(); dlg.activateWindow()
            self._append_log("[v2-gui] NetworkDialog show")
        except Exception:  # noqa: BLE001
            log.exception("NetworkDialog show fail")

    def _on_network_changed(self) -> None:
        """2026-05-05 — NetworkDialog 변경 시 cfg.net + config.yaml 갱신."""
        dlg = self._net_dlg
        if dlg is None:
            return
        try:
            peers: list = []
            nicks: list = []
            for entry in getattr(dlg, "_rows", []):
                if len(entry) < 2:
                    continue
                nick_edit, ip_edit = entry[0], entry[1]
                ip = str(ip_edit.text() or "").strip()
                nk = str(nick_edit.text() or "").strip()
                if ip:
                    peers.append(ip)
                    nicks.append(nk)
            try:
                port = int(dlg.port_spin.value())
            except Exception:  # noqa: BLE001
                port = int(getattr(getattr(self.cfg, "net", None), "port", 54545))
            try:
                rate = int(dlg.rate_spin.value())
            except Exception:  # noqa: BLE001
                rate = 30
            # cfg.net 갱신 (in-memory).
            net = getattr(self.cfg, "net", None)
            if net is not None:
                try:
                    net.peers = list(peers)
                    net.nicks = list(nicks)
                    net.port = port
                    net.send_rate_hz = rate
                except Exception:  # noqa: BLE001
                    pass
            # config.yaml 의 net 섹션 영구 저장.
            self._save_net_to_cfg(peers, nicks, port, rate)
            self._append_log(
                f"[v2-gui] net peers 갱신 — {peers} (cfg.yaml 저장)"
            )
        except Exception:  # noqa: BLE001
            log.exception("network changed handler fail")

    def _save_net_to_cfg(self, peers: list, nicks: list,
                         port: int, send_rate_hz: int) -> None:
        """config.yaml 의 net 섹션 갱신 (다른 attribute 보존)."""
        path = Path(self._cfg_yaml_path)
        try:
            doc: Dict[str, Any] = {}
            if path.is_file():
                with path.open("r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f)
                    if isinstance(loaded, dict):
                        doc = loaded
            net = dict(doc.get("net", {}) or {})
            net["peers"] = list(peers)
            if nicks and any(n for n in nicks):
                net["nicks"] = list(nicks)
            net["port"] = int(port)
            net["send_rate_hz"] = int(send_rate_hz)
            doc["net"] = net
            tmp = path.with_suffix(path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False)
            os.replace(str(tmp), str(path))
        except Exception:  # noqa: BLE001
            log.exception("save net to cfg fail")

    def _open_hunt_report_dialog(self) -> None:
        dlg = self._ensure_dialog(
            "_hunt_report_dlg", HuntReportDialog, False, "HuntReportDialog",
        )
        if dlg is None:
            return
        try:
            # main_window_v2 패턴: reload 먼저 호출.
            fn = getattr(dlg, "reload", None)
            if callable(fn):
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    log.exception("HuntReportDialog reload fail")
            dlg.show(); dlg.raise_(); dlg.activateWindow()
            self._append_log("[v2-gui] HuntReportDialog show")
        except Exception:  # noqa: BLE001
            log.exception("HuntReportDialog show fail")

    # ------------------------------------------------------------------ msw track
    def _track_msw_origin(self) -> None:
        """2026-05-05 Cycle 4-11 — 게임창 이동 감지 시 _regions 일괄 shift.

        v1 main_window.py:2895-2954 1:1 + 메모리 feedback_region_follow_msw.
        1Hz timer (_refresh_telemetry) 안에서 호출. 사용자가 game 창을 화면에서
        옮기면 다음 tick(<=1s) 에 dx/dy 계산 → 모든 _regions 절대 좌표 갱신 +
        worker 즉시 재주입(가동 중) + cfg.yaml 저장 + label 갱신.

        target_window cfg 가 .exe 가 아니면 (또는 hwnd 못 찾으면) noop.

        2026-05-05 Cycle 5-1 — src_v2.utils.win_helpers 우선 + src.* fallback.
        v2 native 가 동작하면 src.* 의존 없음 (Cycle 7 의 src 폴더 hide 준비).
        """
        find_windows_by_process = None
        get_window_rect = None
        try:
            from src_v2.utils.win_helpers import (
                find_windows_by_process as _find_v2,
                get_client_rect_dict as _rect_v2,
            )
            find_windows_by_process = _find_v2
            get_window_rect = _rect_v2
        except Exception:  # noqa: BLE001
            pass
        if find_windows_by_process is None or get_window_rect is None:
            try:
                from src.input.keys import find_windows_by_process as _find_v1
                from src.capture.screen import get_window_rect as _rect_v1
                find_windows_by_process = _find_v1
                get_window_rect = _rect_v1
            except Exception:  # noqa: BLE001
                return
        cfg_input = getattr(self.cfg, "input", None)
        tw = str(getattr(cfg_input, "target_window", "") or "") if cfg_input else ""
        if not tw.lower().endswith(".exe"):
            return
        try:
            wins = find_windows_by_process(tw)
        except Exception:  # noqa: BLE001
            return
        if not wins:
            return
        try:
            hwnd = int(wins[0])
            r = get_window_rect(hwnd)
        except Exception:  # noqa: BLE001
            return
        if r is None:
            return
        # r = {"left", "top", "width", "height"} (capture/screen.py:37 client rect dict)
        try:
            origin = (int(r["left"]), int(r["top"]))
        except Exception:  # noqa: BLE001
            return
        prev = self._msw_last_origin
        if prev is None:
            # 첫 호출 — origin 저장만 (shift 안 함)
            self._msw_last_origin = origin
            return
        dx = origin[0] - prev[0]
        dy = origin[1] - prev[1]
        if dx == 0 and dy == 0:
            return
        self._msw_last_origin = origin

        # 1) self._regions 일괄 shift + label 갱신.
        for k, (x, y, w, h) in list(self._regions.items()):
            nx, ny = int(x + dx), int(y + dy)
            self._regions[k] = (nx, ny, int(w), int(h))
            lbl = self._region_lbls.get(k)
            if lbl is not None:
                try:
                    lbl.setText(f"{nx},{ny} {w}x{h}")
                except Exception:  # noqa: BLE001
                    pass

        # 2) worker 가동 중이면 즉시 재주입.
        if self.btn_stop.isEnabled():
            try:
                self._inject_all_regions()
            except Exception:  # noqa: BLE001
                log.exception("msw shift inject fail")

        # 3) cfg.yaml 자동 저장 (다음 가동 보존).
        try:
            self._save_regions_to_cfg()
        except Exception:  # noqa: BLE001
            pass

        self._append_log(
            f"[v2-gui] msw 창 이동 dx={dx} dy={dy} → 영역 {len(self._regions)} 건 shift"
        )

    @staticmethod
    def _fmt_hpmp(pct: int, cur: int, mx: int) -> str:
        if pct < 0:
            return "-"
        if cur >= 0 and mx > 0:
            return f"{pct}% ({cur}/{mx})"
        return f"{pct}%"

    # ------------------------------------------------------------------ misc
    def _append_log(self, msg: str) -> None:
        log.info(msg)
        try:
            self.txt_log.appendPlainText(msg)
        except Exception:  # noqa: BLE001
            pass

    def closeEvent(self, ev: QtGui.QCloseEvent) -> None:  # noqa: N802
        try:
            self.worker.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._timer.stop()
        except Exception:  # noqa: BLE001
            pass
        # 2026-05-05 Cycle 4-2/4-3 — 오버레이 5종 close.
        for ov in (
            self._game_overlay, self._alert_overlay,
            self._healer_status_overlay, self._hunter_helper_overlay,
            self._skill_range_overlay,
        ):
            if ov is not None:
                try:
                    ov.close()
                except Exception:  # noqa: BLE001
                    pass
        # 2026-05-05 Cycle 4-4 — Dialog 4종 close.
        for dlg in (
            self._skill_dlg, self._param_dlg, self._net_dlg, self._hunt_report_dlg,
        ):
            if dlg is not None:
                try:
                    dlg.close()
                except Exception:  # noqa: BLE001
                    pass
        # 2026-05-05 Cycle 4-5 — RegionOverlay close (toplevel).
        # StatusStrip 은 main 창 자식 widget 이라 자동 정리.
        if self._region_overlay is not None:
            try:
                self._region_overlay.close()
            except Exception:  # noqa: BLE001
                pass
        # 2026-05-05 Cycle 4-6 — ControlListener + Heartbeat thread 정지.
        try:
            self._stop_ctrl_listener()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._stop_heartbeat()
        except Exception:  # noqa: BLE001
            pass
        try:
            for nm in ("src_v2", "src"):
                logging.getLogger(nm).removeHandler(self._log_handler)
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(ev)


__all__ = ["V2MainWindow"]
