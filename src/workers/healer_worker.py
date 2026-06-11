from __future__ import annotations
import ctypes
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets

from ..utils.logger_setup import _setup_logger
from ..utils.win_helpers import _user32, _is_fg_hwnd, frame_to_qpix
from .skill_cd_timer import SkillCdTimer

# HealerWorker 내부에서 런타임 import 되는 무거운 모듈은 그대로 유지.


class HealerWorker(QtCore.QThread):
    frame_ready = QtCore.pyqtSignal(dict)
    log_msg = QtCore.pyqtSignal(str)
    stopped = QtCore.pyqtSignal()
    # UI 동기화: 원격(ControlCmd) 수신 시 GUI 스레드에 armed 값 전달.
    remote_control_applied = QtCore.pyqtSignal(bool, str)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.log, self.log_path = _setup_logger()
        self._stop = False
        # 맵 OCR 백그라운드 워커(선택). run() 진입 후 attach; stop() 에서 join.
        self._map_worker = None
        self.min_w = 25
        self.min_h = 40
        # 흰탭 전용 크기 필터 (커서형이라 빨탭보다 작음).
        # 2026-04-18 probe_whitetab.py 187장 실측: WHITE bbox w=13~88, h=24~97
        # (median 31x61). 운영값 min_w=25/min_h=40으로는 9.9% 유실 (stuck 원인).
        # 15x25로 완화 시 97.9% 통과. 메모리 규칙 "흰탭 raw 감지 1프레임
        # = 즉시 release_all" 준수하려면 recall 최우선.
        self.white_min_w = 15
        self.white_min_h = 25
        self.yolo_conf = cfg.vision.conf
        self.armed = False  # 키 주입 on/off
        # 따라가기 전용 모드 (2026-04-17 추가): True면 스킬 완전 OFF,
        # 이동/TAB-CONFIRM만. 격수 뒤만 졸졸 따라다니는 용도.
        self.follow_only = False
        # 2026-06-07 따라가기 경량 모드 (저사양 대응). True 면 빨탭 YOLO +
        # cooldown/buff/hp/mp OCR 정지. 유지: coord/맵 OCR(이동) + 경험치 OCR
        # + 격수 좌표 UDP 추종. main_window 체크박스로 런타임 토글.
        self.follow_light = False
        # v6-edge: HpMpReader 값 갱신 시 edge 감지용 상태.
        # False→True 전환(상태 cross-down) 순간에만 request_cast. 상태 유지 중
        # 에는 재시전 안 함. 사용자 원 설계 "OCR 받자마자 1회 즉시 시전".
        self._self_dead_prev = False
        self._mp_below_thr_prev = False
        # 2026-04-22: _mp_imminent_prev 제거 — "공력증강 임박" 힐러측 알림은
        # 격수 로컬 판정(_check_gyoungryeok_imminent) 과 중복이라 폐기.
        # 힐러 → 격수 알림 이벤트 seq (격수가 새 이벤트 판정용).
        self._alert_seq = 0
        # attacker UDP State 기반 edge (격수부활).
        self._attacker_dead_prev = False
        # 이동 lock stuck 감시: movement_lock=True 가 10초 이상 지속되면
        # 강제 해제. scheduler 예외/데드락 방어. 따라가기 기능 절대 보장.
        self._movement_lock_since: float = 0.0
        # lock True→False edge 감지용. SEQ-AB 종료 직후 main loop 가 want==
        # current_dir 이유로 재hold 안 해 캐릭 정지하던 버그 수정. lock 해제
        # 순간 _need_rehold=True 강제.
        self._was_movement_locked: bool = False
        # 워커 시작 후 첫 fg_ok=True 순간에 's' 키 1회 송신.
        # 사용자 지시 2026-04-21: 워커 시작 시 옛바창에 s 누르기.
        self._startup_s_sent: bool = False
        self.coord_tol = 1  # 월드 좌표 Δ ≤ tol → 정지. 1=오차 ≤1 유지(밀착 추종).
        # 2026-04-22 사용자 요청: 파력무참 버프 활성 중 coord_tol=1 강제, 만료
        # 시 원복. buff OCR 에서 "파력무참" >0 관측이 active 판정 기준.
        self._parlyuk_buff_active: bool = False
        self._coord_tol_saved: Optional[int] = None
        # 2026-06-10 사용자 요청: 파력무참 시전 허용 굴 집합 (맵명 끝 (N) 의 N).
        # 빈 집합 = 전체 굴 허용(기존 동작). set_parlyuk_maps 로 UI 반영.
        self._parlyuk_maps: set = set()
        # 공력증강 hysteresis 상태 (2026-06-10): MP<임계 시작 → 90% 도달까지 시전.
        self._gyoung_active: bool = False
        # 2026-06-11: 현재 프레임 red/white raw (포탈 진입 전 빨탭 확인 게이트용).
        self._cur_red_raw: bool = False
        self._cur_white_raw: bool = False
        # red bbox 화면좌표 (self-target 구분 측정용 — PORTAL-ENTER 로그에 기록).
        self._cur_red_cx: float = -1.0
        self._cur_red_cy: float = -1.0
        # 포탈 진입 로그 1회 게이트 (맵 도달 시 리셋).
        self._portal_enter_logged: bool = False
        self.yolo_every_n = 1
        # --- 저사양 모드 런타임 튠 (main_window._on_toggle_low_spec이 setattr). ---
        # 0/기본값이면 효과 없음. YoloRunner.imgsz는 런타임 값 치환 가능.
        self.yolo_imgsz = int(getattr(cfg.vision, "imgsz", 640))
        # frame_ready emit 주기 제한 (Hz). 0이면 매 프레임.
        self.preview_hz_limit = 0.0
        self._last_preview_emit_ts = 0.0
        # OCR poll 주기 (cooldown/xp). 런타임 치환: XpOcr.poll_sec, CooldownOcr.poll_sec.
        # 0이면 기본 유지.
        self.ocr_poll_sec = 0.0
        # 저사양 상태 플래그 (디버그/표시용).
        self._low_spec_on = False
        # 실시간 FPS (GUI 폴링용). frame_ready emit 스킵과 무관하게 매 초 갱신.
        self.last_fps = 0.0
        self._last_all_dets = []
        self._last_det = None
        # 구간별 경과 시간 (ms)
        self.t_grab = 0.0
        self.t_yolo = 0.0
        self.t_ocr = 0.0
        self.t_total = 0.0
        # 런타임 상태 (UI 표시용)
        self.healer_coord = None
        self.healer_map = ""
        # 스킬 토글/오프셋: GUI에서 start 전에 주입, 이후엔 런타임 반영.
        # 2026-04-20: 공력증강 조건부 전환, 부활 신규 추가.
        self.skill_enabled: dict = {
            "백호의희원": True, "백호의희원첨": True,
            "공력증강": True, "부활": True,
            "파력무참": True, "금강불체": False,
        }
        self.parlyuk_offset: float = 0.0
        self._skills = None  # SkillScheduler가 사용하는 SkillSpec 리스트 참조.
        # 자힐/공력증강 임계치 (main_window 에서 start 직전 주입).
        self.self_heal_hp_thr: int = 50
        self.gyoungryeok_mp_thr: int = 30
        # VK 매핑 (NumPad 번호). 0x60=NUMPAD0 ... 0x69=NUMPAD9.
        # 주력 힐 NumLock 싸이클 슬롯: 메인힐 + 혼마술 (공력증강 제외).
        # 사용자 2026-04-20: 공력증강은 MP 임계치 기반 조건부로 전환.
        self.primary_vks = [0x61, 0x62]  # NUMPAD1(메인힐)/NUMPAD2(혼마술)
        # 조건부 스킬 VK. 자가부활/격수부활은 공용(부활) VK 사용.
        self.skill_vks = {
            "메인힐": 0x61,
            "백호의희원": 0x64, "백호의희원첨": 0x65,
            "공력증강": 0x63,
            "부활": 0x66,
            "파력무참": 0x68,
            "금강불체": 0x60,
        }
        self._cycler = None  # NumLockCycler 참조 (런타임 슬롯 변경용).
        self._last_state = None  # FSM 상태 전이 감지용.
        self._last_map_neq = False  # 맵 불일치 False→True 전이 시 Tab press.
        # 2026-06-10 정지 latch: tol 충족(at_target) 후 격수가 tol 초과 이동
        # 전까지 정지 유지. 격수 방향만 바뀌거나 OCR 미세흔들림에 안 따라가
        # "계속 돌아다니는" 현상 차단 (사용자 요청).
        self._follow_parked = False
        self._park_atk: Optional[tuple] = None
        self._last_f1_pending = False  # 격수 F1 예고 플래그 엣지 감지용.
        self._last_hold_ts = 0.0    # 강제 재hold 스로틀 (같은 방향 장기 고정 시 재발송).
        self._need_rehold = False   # 스킬 시전 직후 메인 루프에 재hold 요청.
        self._last_want = "-"       # want 전환 즉시 로그용.
        self._last_fg_ok = None     # 힐러 창 FG 변화 감지용.
        self._last_seq = -1         # UDP seq 변화 감지용.
        self._no_udp_warned = 0.0   # UDP 끊김 경고 스로틀 (deprecated, _udp_stalled로 대체).
        self._udp_stalled = False   # UDP stall edge 상태 (진입/복귀 1회씩만 로그).
        self._udp_stall_since = 0.0 # stall 진입 시각 (진입 판정용).
        # [PERF] 10s 집계 누산기 (매초 집계 유지, 10초마다 평균 1회 출력).
        self._perf_fps_sum = 0.0
        self._perf_grab_sum = 0.0
        self._perf_yolo_sum = 0.0
        self._perf_ocr_sum = 0.0
        self._perf_total_sum = 0.0
        self._perf_full_sum = 0.0
        self._pfull_sum_1s = 0.0
        self._perf_samples = 0
        self._perf_last_emit = 0.0
        # [STAT] 이벤트 기반 로그: 핵심 상태 튜플 diff + 30s heartbeat.
        self._last_stat_key = None
        self._stat_heartbeat_ts = 0.0
        self._last_h_coord = None   # 힐러 좌표 최초 획득 로그용.
        self._h_coord_last_change = 0.0  # 힐러 좌표 마지막 변경 시각 (재hold 판정용).
        self._last_h_map = ""       # 힐러 맵 최초 획득 로그용.
        self._last_a_map = ""       # 격수 맵 변화 로그용.
        # 흰탭 감지 → TAB-CONFIRM arm 트리거용 카운터.
        # 1프레임 차단 원칙(메모리 tab_mechanics). 단 YOLO가 흰탭을
        # 1프레임씩 깜빡이며 오탐하면 confirm이 매번 0→1 로 리셋되어
        # 3프레임 ARM에 절대 도달 못 함(2026-04-18 로그 확인).
        # → _whitetab_seen_ts로 마지막 감지 시각을 기록, 250ms 이내면
        #   confirm 유지 (깜빡임 gap 흡수). 250ms 초과 gap 이후엔 리셋.
        self._whitetab_confirm = 0     # 최근 감지 streak 카운터.
        self._whitetab_seen_ts: float = 0.0  # 마지막 white_raw=True ts.
        self._whitetab_suspect_dumped: float = 0.0  # 흰탭 덤프 throttle ts
        # STAY(past) 무한 정지 방지. past=True 유지 시각 기록.
        # N초 경과 & map_neq 계속이면 exit_dir로 한 번 더 밀기.
        self._past_stuck_since: float = 0.0
        # 2026-04-23 사용자 요청: 벽 박힘 감지 (v3 직교축 전용).
        # 로직: X축(L/R) 막힘 → Y축으로만 시도. Y축(U/D) 막힘 → X축으로만 시도.
        # REV(반대방향 복귀) 로직 폐기 — 사용자 논리상 뒤로 돌아가면 몹 어그로
        # 유지한 채 길 잃음. 직교 1차(격수 방향) → 직교 2차(반대) → RELEASE 리셋.
        # run_start: 현재 want 런 시작 시각/좌표. 진행 발생 시 baseline 갱신.
        # stuck_dur = now - run_start_ts (마지막 진행 이후 경과).
        #   <0.8s    = 정상 (키 방금 눌렀을 수도)
        #   0.8~2.0s = STUCK-ORTHO1 (격수 쪽 직교)
        #   2.0~3.5s = STUCK-ORTHO2 (반대 직교)
        #   >3.5s    = RESET (release 후 다음 진입 재카운트, 원 방향 재시도)
        self._run_want = None          # 현재 지속 중인 UDLR want
        self._run_start_ts: float = 0.0
        self._run_start_pos = None     # (hx, hy) baseline
        self._stuck_last_log: float = 0.0
        # 2026-04-23 맵 전환 JUMP GATE: 격수 좌표 급변 → 맵 전환 중 판정
        self._prev_atk_coord = None
        self._map_jump_hold_until: float = 0.0
        self._map_jump_inferred_dir: str = "-"
        # 2026-04-23 맵 전환 직후 자힐 그레이스: 힐러가 새 맵 도착 후 N초는
        # 격수 TAB 타겟 범위 밖일 가능성 높음 → 자힐 시 self-target 영구 고착.
        # 맵 변경 감지 시각 기록 → EDGE 자힐/자가부활이 이 시간 내엔 보류.
        self._last_map_change_ts: float = 0.0
        self._post_mapchg_grace_sec: float = 5.0
        # 2026-04-23 자동 TAB 복귀: 자힐/자가부활 후 15초 내 힐러가 격수 근접
        # (맨해튼 ≤ 5) + map_eq 조건 만족하면 ESC→TAB→TAB 자동 재시전.
        # SEQ-B가 TAB 복귀 실패한 경우(격수 멀리) 보험. 사용자가 수동으로 TAB
        # 누를 필요 없음.
        # 2026-04-23 사용자 지시: 맵 전환 중 자힐이면 SEQ-B의 TAB×2 스킵하고
        # 새 맵 도착 후 worker가 TAB×2 시전해 격수 고정. pending 플래그.
        self._pending_tab_lock_until: float = 0.0
        # 2026-04-24 자힐 중 빨탭 우클릭으로 격수 거리 좁히기. 0.5s 간격 throttle.
        self._seq_rclick_last_ts: float = 0.0
        # _hook_block_ab 진입 시점에 잡은 YOLO 빨탭 절대 화면 좌표. 자힐
        # 중 동일 위치 계속 우클릭 (격수가 그 자리에 있을 가능성 높음).
        # self-target 이후 YOLO 빨탭은 self 위라 못 씀.
        self._seq_rclick_target = None  # (abs_x, abs_y) or None
        # 2026-04-24 SEQ-RCLICK 디버그 덤프용: 최신 crop frame + 그 origin.
        self._last_crop_frame = None
        self._last_crop_offset = (0, 0)
        # 2026-04-23 STUCK blacklist (v5.1): RESET 발생한 (맵, 좌표±2, 방향)을
        # TTL로 기록. 하지만 도사 둘이 서로 막는 일시 정체를 "벽"으로 오인하면
        # 실제 길을 영구 차단함. 따라서:
        #  1. 첫 RESET은 "용서" (blacklist 등록 X, 이력만 기록)
        #  2. 10초 내 재발 시에만 진짜 벽으로 판정해 blacklist 등록
        #  3. 해당 방향으로 실제 진행 감지 시 관련 blacklist 즉시 제거
        self._stuck_blacklist: dict = {}  # {(map,cx,cy,dir): (expire_ts, hit)}
        self._reset_history: dict = {}    # {(map,cx,cy,dir): last_reset_ts}
        self._bl_ttl_sec: float = 5.0
        self._bl_forgive_window: float = 10.0  # 첫 RESET 후 이 시간 내 재발만 차단
        # world(a-h) → screen px 캘리브레이션 (비침습 관찰).
        # 격수 빨탭 복구 로직(이후 단계)에서 격수 예상 px 계산에 사용.
        # 현 단계: 샘플 수집 + 로그만, 어떤 제어도 영향 없음.
        from ..vision.calibration import Calibrator
        self._calibrator = Calibrator(self.log)
        # 쿨다운 OCR (v5). region 미지정 시 비활성.
        # 2026-04-22: poll_sec 원래대로 (반응 속도 유지). GIL 경합은 OCR
        # 스레드 staggered start 로 완화 (서로 다른 오프셋으로 시작해서 동시
        # predict 확률 감소).
        from ..vision.cooldown_ocr import CooldownOcr
        self._cooldown_ocr = CooldownOcr(
            poll_sec=getattr(cfg.cooldown, "poll_sec", 1.0)
        )
        if cfg.cooldown.region_x >= 0 and cfg.cooldown.region_w > 0:
            self._cooldown_ocr.set_region(
                cfg.cooldown.region_x, cfg.cooldown.region_y,
                cfg.cooldown.region_w, cfg.cooldown.region_h,
            )
            # 시작 시 첫 3회 crop 저장 (좌표계 검증용).
            try:
                from pathlib import Path as _P
                self._cooldown_ocr.set_dump_dir(_P("logs") / "cd_crop", n=3)
            except Exception:
                pass
        # 닉네임 영역도 있으면 적용.
        if (getattr(cfg.cooldown, "nick_region_x", -1) >= 0
                and getattr(cfg.cooldown, "nick_region_w", 0) > 0):
            self._cooldown_ocr.set_nick_region(
                cfg.cooldown.nick_region_x, cfg.cooldown.nick_region_y,
                cfg.cooldown.nick_region_w, cfg.cooldown.nick_region_h,
            )
        # 파력무참 버프 지속시간 전용 OCR (별도 인스턴스 — 버프창 영역).
        # custom 타겟 모드 → OCR에 잡힌 다른 라인이 있을 때 파력무참 미검출 = 0 처리.
        self._buff_ocr = CooldownOcr(
            poll_sec=getattr(cfg.cooldown, "poll_sec", 1.0)
        )
        try:
            self._buff_ocr.set_target_skills(["파력무참"])
        except Exception:
            pass
        if (getattr(cfg.cooldown, "buff_region_x", -1) >= 0
                and getattr(cfg.cooldown, "buff_region_w", 0) > 0):
            self._buff_ocr.set_region(
                cfg.cooldown.buff_region_x, cfg.cooldown.buff_region_y,
                cfg.cooldown.buff_region_w, cfg.cooldown.buff_region_h,
            )
        # 스킬 내부 datetime 타이머 (OCR은 오차검증용).
        self._timer_parlyuk = SkillCdTimer("parlyuk", self.log)
        self._timer_baekho = SkillCdTimer("baekho", self.log)
        # 경험치 OCR → 시간당 예상 경험치.
        try:
            from ..vision.xp_ocr import XpOcr
            self._xp_ocr = XpOcr(poll_sec=2.0)
        except Exception:
            self._xp_ocr = None
        self._cd_last_send = 0.0
        self._cd_send_interval = 1.0  # 초당 1회 쿨다운 역송.
        self._last_cooldown = None  # 최근 CooldownReading (GUI 표시용).
        self._cd_last_log_ts = 0.0   # OCR 결과 1Hz 로깅 (진단용).
        self._cd_last_err_ts = 0.0   # 에러 5s throttle.
        self._cd_last_send_ok = None  # 송신 결과 변화 감지 (True/False/None).
        self._no_state_warn_ts = 0.0  # 격수 State 미수신 경고 스로틀.
        # 원격 제어 수신용 sender (힐러→격수 쿨다운 보고에도 동일 소켓 공유).
        self._udp_out = None
        self._attacker_addr = None  # recv_from src_ip + attacker_recv_port.
        # 영역 설정 (절대 화면 좌표). game만 지정 시 YOLO 추론 크롭.
        self._game_region_abs: Optional[Tuple[int, int, int, int]] = None
        self._xp_region_abs: Optional[Tuple[int, int, int, int]] = None
        self._hp_region_abs: Optional[Tuple[int, int, int, int]] = None
        self._mp_region_abs: Optional[Tuple[int, int, int, int]] = None
        # HP/MP OCR 리더 — 자힐/공력증강/자가부활 predicate 용.
        # log_cb 는 아래 self.log 사용 — initialize 시점에 이미 존재.
        from ..vision.hpmp import HpMpReader
        self._hpmp = HpMpReader(
            log_cb=lambda s: self.log.info(s) if hasattr(self, "log") else None
        )
        # cfg 저장 max 복원 (GUI 시작 시 set_hp_max/set_mp_max 로 재주입됨).
        try:
            self._hpmp.set_hp_max(int(getattr(cfg.cooldown, "hp_max", 0)))
            self._hpmp.set_mp_max(int(getattr(cfg.cooldown, "mp_max", 0)))
        except Exception:
            pass
        if (getattr(cfg.cooldown, "hp_region_x", -1) >= 0
                and getattr(cfg.cooldown, "hp_region_w", 0) > 0):
            self._hpmp.set_hp_region(
                cfg.cooldown.hp_region_x, cfg.cooldown.hp_region_y,
                cfg.cooldown.hp_region_w, cfg.cooldown.hp_region_h,
            )
            self._hp_region_abs = (
                cfg.cooldown.hp_region_x, cfg.cooldown.hp_region_y,
                cfg.cooldown.hp_region_w, cfg.cooldown.hp_region_h,
            )
        if (getattr(cfg.cooldown, "mp_region_x", -1) >= 0
                and getattr(cfg.cooldown, "mp_region_w", 0) > 0):
            self._hpmp.set_mp_region(
                cfg.cooldown.mp_region_x, cfg.cooldown.mp_region_y,
                cfg.cooldown.mp_region_w, cfg.cooldown.mp_region_h,
            )
            self._mp_region_abs = (
                cfg.cooldown.mp_region_x, cfg.cooldown.mp_region_y,
                cfg.cooldown.mp_region_w, cfg.cooldown.mp_region_h,
            )

    def set_cooldown_region(self, x: int, y: int, w: int, h: int) -> None:
        """GUI "쿨 영역 지정" 완료 시 실시간 반영."""
        try:
            self._cooldown_ocr.set_region(x, y, w, h)
            # 다음 3회 crop을 로그 폴더에 저장 (사용자 검증용).
            try:
                from pathlib import Path
                self._cooldown_ocr.set_dump_dir(
                    Path("logs") / "cd_crop", n=3
                )
            except Exception:
                pass
            self.log.info(
                f"[COOLDOWN] region 설정 x={x} y={y} w={w} h={h} "
                f"(다음 3회 crop을 logs/cd_crop/에 저장)"
            )
        except Exception as e:
            self.log.warning(f"[COOLDOWN] region 설정 실패: {e}")

    def clear_cooldown_region(self) -> None:
        try:
            self._cooldown_ocr.clear_region()
            self.log.info("[COOLDOWN] region 해제")
        except Exception:
            pass

    def set_nick_region(self, x: int, y: int, w: int, h: int) -> None:
        """GUI "닉네임 영역 지정" 완료 시 실시간 반영."""
        try:
            self._cooldown_ocr.set_nick_region(x, y, w, h)
            self.log.info(
                f"[NICK] region 설정 x={x} y={y} w={w} h={h}"
            )
        except Exception as e:
            self.log.warning(f"[NICK] region 설정 실패: {e}")

    def clear_nick_region(self) -> None:
        try:
            self._cooldown_ocr.clear_nick_region()
            self.log.info("[NICK] region 해제")
        except Exception:
            pass

    def set_buff_region(self, x: int, y: int, w: int, h: int) -> None:
        """파력무참 버프 지속시간 OCR 영역 지정."""
        try:
            self._buff_ocr.set_region(int(x), int(y), int(w), int(h))
            self.log.info(
                f"[BUFF] region 설정 x={x} y={y} w={w} h={h}"
            )
        except Exception as e:
            self.log.warning(f"[BUFF] region 설정 실패: {e}")

    def clear_buff_region(self) -> None:
        try:
            self._buff_ocr.clear_region()
            self.log.info("[BUFF] region 해제")
        except Exception:
            pass

    def set_game_region(self, x: int, y: int, w: int, h: int) -> None:
        """YOLO 추론을 이 절대 화면 사각형으로 크롭."""
        try:
            self._game_region_abs = (int(x), int(y), int(w), int(h))
            self.log.info(f"[GAME] region 설정 x={x} y={y} w={w} h={h}")
        except Exception as e:
            self.log.warning(f"[GAME] region 설정 실패: {e}")

    def clear_game_region(self) -> None:
        self._game_region_abs = None
        try:
            self.log.info("[GAME] region 해제 (전체 프레임 추론)")
        except Exception:
            pass

    def set_xp_region(self, x: int, y: int, w: int, h: int) -> None:
        self._xp_region_abs = (int(x), int(y), int(w), int(h))
        try:
            if self._xp_ocr is not None:
                self._xp_ocr.set_region(x, y, w, h)
                self._xp_ocr.reset()
            self.log.info(f"[XP] region 설정 x={x} y={y} w={w} h={h}")
        except Exception:
            pass

    def clear_xp_region(self) -> None:
        self._xp_region_abs = None
        try:
            if self._xp_ocr is not None:
                self._xp_ocr.clear_region()
                self._xp_ocr.reset()
        except Exception:
            pass

    def set_hp_region(self, x: int, y: int, w: int, h: int) -> None:
        self._hp_region_abs = (int(x), int(y), int(w), int(h))
        try:
            self._hpmp.set_hp_region(int(x), int(y), int(w), int(h))
            self.log.info(f"[HP] region 설정 x={x} y={y} w={w} h={h}")
        except Exception:
            pass

    def set_hp_max(self, n: int) -> None:
        try:
            self._hpmp.set_hp_max(int(n))
            self.log.info(f"[HP] max 설정 n={n}")
        except Exception:
            pass

    def set_mp_max(self, n: int) -> None:
        try:
            self._hpmp.set_mp_max(int(n))
            self.log.info(f"[MP] max 설정 n={n}")
        except Exception:
            pass

    def clear_hp_region(self) -> None:
        self._hp_region_abs = None
        try:
            self._hpmp.clear_hp_region()
        except Exception:
            pass

    def set_mp_region(self, x: int, y: int, w: int, h: int) -> None:
        self._mp_region_abs = (int(x), int(y), int(w), int(h))
        try:
            self._hpmp.set_mp_region(int(x), int(y), int(w), int(h))
            self.log.info(f"[MP] region 설정 x={x} y={y} w={w} h={h}")
        except Exception:
            pass

    def latest_hpmp(self):
        """테스트 버튼용 — 현재 프레임 즉시 HP/MP 읽어 반환.

        main 루프는 매 프레임 self._hpmp.read 를 호출하므로 그 캐시(latest)
        를 돌려줌. 아직 한 번도 읽지 않았으면 (-1, -1).
        """
        try:
            return self._hpmp.latest()
        except Exception:
            from ..vision.hpmp import HpMp
            return HpMp(hp=-1, mp=-1)

    def clear_mp_region(self) -> None:
        self._mp_region_abs = None
        try:
            self._hpmp.clear_mp_region()
        except Exception:
            pass

    def apply_remote_control(self, cmd: str) -> None:
        """격수에서 받은 ControlCmd를 반영.

        - start      : armed=True (워커 재개)
        - pause      : armed=False (동작 중단, 상태 유지)
        - stop       : self._stop=True → run 루프 종료. **armed는 건드리지 않음.**
        - follow_on  : follow_only=True (주력힐/파력무참 OFF, 이동만)
        - follow_off : follow_only=False (전투 복귀)
        """
        c = str(cmd or "").lower()
        if c == "ping":
            return
        if c in ("follow_on", "follow_off"):
            self.follow_only = (c == "follow_on")
            try:
                self.log.info(
                    f"[CTRL-RECV] cmd={c} → follow_only={self.follow_only}"
                )
            except Exception:
                pass
            try:
                self.remote_control_applied.emit(bool(self.armed), str(c))
            except Exception:
                pass
            return
        if c == "stop":
            # stop은 워커 종료만. armed는 그대로 유지 → 재시작 시 동일 상태.
            self._stop = True
            try:
                self.log.info(
                    f"[CTRL-RECV] stop → self._stop=True (armed={self.armed} 유지)"
                )
            except Exception:
                pass
            try:
                self.remote_control_applied.emit(bool(self.armed), str(c))
            except Exception:
                pass
            return
        # start / pause만 armed 토글.
        if c in ("start", "pause"):
            on = (c == "start")
            self.armed = on
            try:
                self.log.info(f"[CTRL-RECV] cmd={c} → armed={on}")
            except Exception:
                pass
            try:
                self.remote_control_applied.emit(bool(on), str(c))
            except Exception:
                pass

    def _compute_state_text(self) -> str:
        """현재 힐러 런타임 상태 요약 (격수 UI 표시용).

        우선순위: 정지 > 일시정지 > 맵전환중 > 따라가기만 > 전투중.
        """
        if getattr(self, "_stop", False):
            return "정지"
        if not bool(getattr(self, "armed", False)):
            return "일시정지"
        # map_change_pending 은 격수 State 수신값을 별도 보관하지 않음.
        # 힐러 쪽은 fsm/controller 내부 flag 참조 — 간단히 follow_only 우선.
        if bool(getattr(self, "follow_only", False)):
            return "따라가기만"
        return "전투중"

    def stop(self):
        self._stop = True

    def _press_tab(self, target_hwnd=None):
        """Tab 키 1회 press (맵 전환 시 본인에게 빨탭 걸기 트릭).

        keybd_event는 현재 FG 창에만 들어감 → target_hwnd가 FG 아니면 효과 없음.
        반환: (fg_hwnd, fg_matches_target) — 로그 진단용.
        """
        import ctypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        TAB_VK = 0x09
        fg = int(user32.GetForegroundWindow())
        fg_matches = bool(target_hwnd) and fg == int(target_hwnd)
        user32.keybd_event(TAB_VK, 0x0F, 0, 0)       # DOWN (scan=0x0F)
        time.sleep(0.04)
        user32.keybd_event(TAB_VK, 0x0F, 0x0002, 0)  # UP
        return fg, fg_matches

    def _press_home(self, target_hwnd=None):
        """Home 키 1회 press. 흰탭을 힐러 자신에게 강제 focus.
        Tab 누르기 직전에 호출 → Tab이 확실한 self-target이 되게 보장.
        (흰탭이 몹/NPC 등 엉뚱한 대상에 있던 경우 Tab이 그 대상을 확정해
        다음 맵에서 빨탭 오인 고정 → 격수 힐 못 받고 사망 방지용).
        """
        import ctypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        HOME_VK = 0x24
        HOME_SCAN = 0x47  # Home extended key
        fg = int(user32.GetForegroundWindow())
        fg_matches = bool(target_hwnd) and fg == int(target_hwnd)
        # 0x0001 = KEYEVENTF_EXTENDEDKEY (Home은 extended).
        user32.keybd_event(HOME_VK, HOME_SCAN, 0x0001, 0)          # DOWN
        time.sleep(0.04)
        user32.keybd_event(HOME_VK, HOME_SCAN, 0x0001 | 0x0002, 0)  # UP
        return fg, fg_matches

    def run(self):
        """HealerWorker 메인 루프. HealerWorker.run() 의 기존 본문 전체."""
        # 힐러 전용 의존성: 여기서 지연 import.
        from ..capture.screen import Grabber, AsyncGrabber
        from ..vision.yolo import YoloRunner, AsyncYolo
        from ..vision.ocr import Ocr, AsyncOcr
        from ..vision.map_ocr import MapOcrWorker
        from ..net.udp_receiver import UdpReceiver
        from ..net.protocol import State
        from ..fsm.controller import Follower
        from ..fsm.state import FsmState
        from ..input.keys import KeyController, find_windows_by_process
        from ..input.numlock_cycle import NumLockCycler, is_numlock_on
        from ..input.skill_scheduler import SkillScheduler
        from ..input.skill_blueprints import default_skills
        from ..input.keys import _send_input, _post
    
        cfg = self.cfg
        self.log.info("=== worker start ===")
        self.log.info(f"log_path={self.log_path}")
        self.log.info(f"cfg.vision weights={cfg.vision.weights} "
                      f"imgsz={cfg.vision.imgsz} conf={cfg.vision.conf} "
                      f"half={cfg.vision.half} device={cfg.vision.device}")
        self.log.info(f"cfg.ocr every_n={cfg.ocr.ocr_every_n_frames}")
        self.log.info(f"cfg.input target={cfg.input.target_window} "
                      f"method={cfg.input.method}")
        self.log.info(f"cfg.net port={cfg.net.port} "
                      f"bind={cfg.net.bind_host}")
        try:
            import torch
            cuda_ok = torch.cuda.is_available()
            dev_name = torch.cuda.get_device_name(0) if cuda_ok else "CPU"
            ver = torch.__version__
            bcu = getattr(torch.version, "cuda", None)
            self.log.info(f"[ENV] torch={ver} cuda_available={cuda_ok} "
                          f"built_cuda={bcu} device={dev_name}")
            self.log_msg.emit(f"[ENV] torch.cuda={cuda_ok} device={dev_name}")
            if not cuda_ok:
                self.log.warning("[ENV] CUDA 없음 → CPU 추론. FPS 낮음.")
        except Exception as e:
            self.log.exception(f"torch 확인 실패: {e}")
        try:
            hwnd = None
            if cfg.input.target_window.lower().endswith(".exe"):
                wins = find_windows_by_process(cfg.input.target_window)
                self.log.info(f"find_windows_by_process({cfg.input.target_window}) -> {wins}")
                if wins:
                    hwnd = wins[0]
                    self.log_msg.emit(f"창 hwnd={hwnd}")
                    self.log.info(f"hwnd={hwnd}")
                else:
                    self.log_msg.emit(
                        f"[!] {cfg.input.target_window} 창 없음 → monitor fallback"
                    )
                    self.log.warning(f"{cfg.input.target_window} 창 없음")
            # AsyncGrabber: 격수 OFF 기준 fps 20→62 실측 개선 확인됨.
            # 격수 ON 시 fps 드롭은 별개 원인 → 유지.
            grab = AsyncGrabber(cfg.capture.monitor_index, hwnd=hwnd,
                                target_interval_s=0.02)
            self.log_msg.emit(f"capture region={grab.mon} [async]")
            self.log.info(f"capture region={grab.mon} [async grabber]")
            # 닉네임 영역 기본값 자동 설정 (msw.exe 기준 1039,677,123,33).
            # 사용자가 별도로 "닉 영역" 드래그하지 않아도 닉네임 OCR 동작하게 함.
            try:
                if self._cooldown_ocr.nick_region() is None:
                    m = grab.mon or {}
                    nx = int(m.get("left", 0)) + 1039
                    ny = int(m.get("top", 0)) + 677
                    nw, nh = 123, 33
                    self._cooldown_ocr.set_nick_region(nx, ny, nw, nh)
                    self.log.info(
                        f"[NICK] 기본값 자동 설정 region=({nx},{ny}) {nw}x{nh} "
                        f"(mon.left={m.get('left')} mon.top={m.get('top')})"
                    )
            except Exception as _e:
                self.log.warning(f"[NICK] 기본값 설정 실패: {_e}")
            yolo = YoloRunner(cfg.vision.weights, imgsz=cfg.vision.imgsz,
                              conf=self.yolo_conf, iou=cfg.vision.iou,
                              half=cfg.vision.half, device=cfg.vision.device,
                              log_fn=self.log.info)
            # 2026-04-21: 게임 GPU queue 경합으로 YOLO predict 가 5ms→981ms 로
            # 튀어 메인 루프가 1 FPS 수준까지 떨어짐. AsyncYolo 로 백그라운드
            # 분리. 메인 루프는 submit(frame) + 최신 latest() 참조 (비블로킹).
            yolo_async = AsyncYolo(yolo)
            self.log.info("[YOLO-ASYNC] 백그라운드 detection 스레드 시작")
            # 2026-04-21 20:xx: EasyOCR CPU 강제 실험 실패. CPU 에서 coord1
            # 45ms→742ms 로 악화. GPU 로 복귀. 진짜 원인은 EasyOCR 아니라
            # 게임(msw.exe) 스킬 렌더링이 GPU 점유해 YOLO 큐 대기.
            ocr = Ocr(coord_w=cfg.ocr.coord_w, coord_h=cfg.ocr.coord_h,
                      coord_right_pad=cfg.ocr.coord_right_pad,
                      coord_bottom_pad=cfg.ocr.coord_bottom_pad,
                      coord_upscale=cfg.ocr.coord_upscale,
                      map_w=cfg.ocr.map_w, map_h=cfg.ocr.map_h,
                      map_top_pad=cfg.ocr.map_top_pad,
                      map_left_pad=getattr(cfg.ocr, "map_left_pad", -1),
                      map_upscale=cfg.ocr.map_upscale,
                      gpu=True,
                      map_interval_s=getattr(cfg.ocr,
                                             "map_interval_s", 2.0))
            _easy_note = getattr(ocr, "_easy_device_note", "unknown")
            self.log.info(f"[OCR-INIT] EasyOCR={_easy_note}")
            # [OCR-PROF] 단계별 ms 진단 (2026-04-20 FPS=10 원인).
            # ocr_every_n_frames=10 이라 10회 호출=100프레임≈10초에 1회 로그.
            try:
                ocr.set_profile_log(lambda s: self.log.info(s), every=10)
            except Exception as _e:
                self.log.warning(f"[OCR-PROF] set_profile_log 실패: {_e}")
            # 맵 OCR 백그라운드 워커: PaddleOCR predict(~230ms) 를 별도 스레드로
            # 분리해 메인 루프 블로킹 제거 (2026-04-20 CUDA 13.1 환경: Paddle
            # GPU 불가 → 스레드 분리로 fps 회복).
            try:
                map_worker = MapOcrWorker(
                    map_w=cfg.ocr.map_w,
                    map_h=cfg.ocr.map_h,
                    map_top_pad=cfg.ocr.map_top_pad,
                    map_left_pad=getattr(cfg.ocr, "map_left_pad", -1),
                    interval_s=getattr(cfg.ocr, "map_interval_s", 2.0),
                )
                map_worker.start()
                ocr.attach_map_worker(map_worker)
                self._map_worker = map_worker
                self.log.info(
                    f"[MAP-OCR] async worker started "
                    f"(interval={map_worker.interval_s}s)"
                )
            except Exception as _e:
                self._map_worker = None
                self.log.warning(f"[MAP-OCR] worker start 실패 (sync fallback): {_e}")
            # 2026-04-21: 좌표 OCR (EasyOCR) 도 메인 루프 blocking 이 원인이라
            # async 래퍼로 분리. submit(frame) 은 비블로킹, latest() 는 최신
            # OcrResult 반환. 좌표 OCR 885ms spike 가 발생해도 메인 루프는
            # 계속 돈다. ocr 객체 자체는 그대로 유지 (set_known_maps 등
            # 내부 state 접근용).
            ocr_async = AsyncOcr(ocr)
            self.log.info("[OCR-ASYNC] 백그라운드 좌표/맵 OCR 스레드 시작")
            self.log_msg.emit("OCR 준비 완료")
            recv = UdpReceiver(cfg.net.bind_host, cfg.net.port)
            fol = Follower(red_lost_sec=cfg.fsm.red_lost_sec,
                           stuck_sec=cfg.fsm.stuck_sec,
                           dead_reckon_sec=cfg.fsm.dead_reckon_sec)
            # Follower 내부 이벤트(TRAIL-PUSH, CTRL-MAPCHG, PROGRESS)를 같은 로거로.
            fol.log = self.log
            # 2026-04-22 원상복구: _snap_forward_threshold 기본값(10) 사용.
            keys = KeyController(window_name=cfg.input.target_window,
                                 method=cfg.input.method,
                                 keydown_ms_min=cfg.input.keydown_ms_min,
                                 keydown_ms_max=cfg.input.keydown_ms_max,
                                 jitter_ms=cfg.input.jitter_ms)
            # main_window 테스트 버튼(F11) 에서 self._keys.release_all 참조.
            self._keys = keys
            cycler = NumLockCycler(hwnd=keys.hwnd, method=cfg.input.method,
                                    slots=list(self.primary_vks))
            cycler.set_log(lambda s: self.log.info(s))
            # skill_lock_vk 단계별 진단 로그 주입 (2026-04-20 토글 미작동 진단).
            try:
                from ..input.numlock_cycle import set_lock_debug
                set_lock_debug(lambda s: self.log.info(s))
            except Exception as _e:
                self.log.warning(f"[LOCK-TRACE] set_lock_debug 실패: {_e}")
            # 방향키 SendInput 호출 추적 로그 (2026-04-21: 이동 실패 진단).
            try:
                from ..input.keys import set_down_debug
                set_down_debug(lambda s: self.log.info(s))
            except Exception as _e:
                self.log.warning(f"[VK-SEND] set_down_debug 실패: {_e}")
            cycler.start()
            self._cycler = cycler
            self.log_msg.emit(
                f"[CYCLE] init slots={[hex(v) for v in self.primary_vks]} "
                f"method={cfg.input.method} hwnd={keys.hwnd}"
            )
    
            # 스킬 시전은 old.oldbaram 이식 시퀀스: press_normal_vk 반복.
            # v4: SkillScheduler 가 burst(_press_burst) 로 cast_fn 을 반복 호출.
            # 2026-04-21 복원: _need_rehold=True 재설정. 이전 제거 실험은
            # 사용자 환경에서 이동 실패 유발 → 기존 잘 동작하던 로직 복구.
            from ..input.numlock_cycle import press_normal_vk
            _worker_self = self
            # 허용 VK 화이트리스트. scheduler 가 enabled=True 인 스킬의 VK
            # 만 호출해야 하지만 이중 안전장치. 호출 시점에 skill_enabled
            # 상태를 다시 확인.
            def _cast(vk):
                try:
                    # 해당 VK 가 어떤 스킬 것인지 역매핑 + enabled 체크.
                    _blocked = False
                    for _sk in (_worker_self._skills or []):
                        if int(_sk.vk) == int(vk) and not _sk.enabled:
                            _blocked = True
                            _worker_self.log.warning(
                                f"[SKILL-BLOCK] vk={hex(vk)} "
                                f"→ {_sk.name}(enabled=False) 시전 차단"
                            )
                            break
                    if _blocked:
                        return
                except Exception:
                    pass
                press_normal_vk(vk)
                _worker_self._need_rehold = True
    
            def _ctx():
                # v4: SkillScheduler._verify 가 ctx["buffs"] / ctx["cooldowns"]
                # 를 참조. 1Hz OCR 최신 결과 주입.
                # + self_hp_pct / self_mp_pct: 힐러 HpMpReader.latest().
                # + attacker_hp_pct / attacker_mp_pct: 격수 State.hp_pct/mp_pct.
                ctx = {}
                try:
                    cd = _worker_self._cooldown_ocr.latest()
                    ctx["cooldowns"] = dict(getattr(cd, "skills", {}) or {})
                except Exception:
                    ctx["cooldowns"] = {}
                try:
                    bf = _worker_self._buff_ocr.latest()
                    ctx["buffs"] = dict(getattr(bf, "skills", {}) or {})
                except Exception:
                    ctx["buffs"] = {}
                atk = None
                try:
                    atk = recv.latest()
                except Exception:
                    atk = None
                # 힐러 자신 HP/MP (픽셀 비율, 매 프레임 메인 루프가 갱신).
                try:
                    hm = _worker_self._hpmp.latest()
                    ctx["self_hp_pct"] = int(getattr(hm, "hp", -1))
                    ctx["self_mp_pct"] = int(getattr(hm, "mp", -1))
                except Exception:
                    ctx["self_hp_pct"] = -1
                    ctx["self_mp_pct"] = -1
                # 격수 HP/MP (UDP State 에서).
                try:
                    if atk is not None:
                        ctx["attacker_hp_pct"] = int(
                            getattr(atk, "hp_pct", -1)
                        )
                        ctx["attacker_mp_pct"] = int(
                            getattr(atk, "mp_pct", -1)
                        )
                    else:
                        ctx["attacker_hp_pct"] = -1
                        ctx["attacker_mp_pct"] = -1
                except Exception:
                    ctx["attacker_hp_pct"] = -1
                    ctx["attacker_mp_pct"] = -1
                # 파력무참 시전 굴 판정용 현재 힐러 맵.
                ctx["map_name"] = getattr(_worker_self, "healer_map", "") or ""
                return ctx
    
            # 블록A/B 훅 — 자힐/자가부활 pre/post.
            from ..input.target_sequence import (
                block_a_self_target, block_b_return_to_attacker,
                run_block_ab_combined,
            )
            _log_i = lambda s: self.log.info(s)
            _cycler_ref = cycler
            _keys_ref = keys
    
            def _hook_block_a(_c):
                block_a_self_target(cycler=_cycler_ref, log_fn=_log_i)
    
            def _hook_block_b(_c):
                block_b_return_to_attacker(cycler=_cycler_ref, log_fn=_log_i)
    
            def _send_healer_alert(text: str) -> None:
                """힐러 상태 이벤트를 격수 알림 오버레이에 표시하도록 송신.
                CooldownReport 의 event_text / event_seq 필드에 실어 보냄.
                """
                try:
                    src = recv.last_src_addr()
                    if src is None:
                        return
                    _worker_self._alert_seq += 1
                    from ..net.protocol import CooldownReport, now_ms
                    _hc = getattr(_worker_self, "healer_coord", None)
                    _hx = int(_hc[0]) if _hc is not None else 0
                    _hy = int(_hc[1]) if _hc is not None else 0
                    rep = CooldownReport(
                        src_idx=int(getattr(cfg.net, "healer_idx", 0)),
                        ts_ms=now_ms(),
                        armed=bool(_worker_self.armed),
                        event_text=str(text),
                        event_seq=int(_worker_self._alert_seq),
                        healer_map=str(
                            getattr(_worker_self, "healer_map", "") or ""
                        ),
                        healer_x=_hx,
                        healer_y=_hy,
                        coord_valid=(_hc is not None),
                        state_text=_worker_self._compute_state_text(),
                    )
                    port = int(getattr(cfg.net, "attacker_recv_port", 45455))
                    _worker_self._udp_out.send_to(src[0], port, rep.to_bytes())
                    _worker_self.log.info(
                        f"[ALERT] send → {src[0]}:{port} text={text!r}"
                    )
                except Exception as _e:
                    _worker_self.log.warning(f"[ALERT] 송신 실패: {_e}")

            def _hook_block_ab(_c):
                # 자힐 진입 순간 YOLO 빨탭(=그 시점엔 격수 머리 위) 절대 화면
                # 좌표 저장. 자힐 중 메인 루프가 이 위치 계속 우클릭.
                # SEQ-A TAB/HOME 이후엔 빨탭이 self로 옮겨가므로 진입 순간의
                # 격수 위치가 유일하게 유효. 격수가 그 자리 있을 가능성 전제.
                try:
                    _det0 = getattr(_worker_self, "_last_det", None)
                    _gr0 = getattr(_worker_self, "_game_region_abs", None)
                    if _det0 is not None and _gr0 is not None:
                        # 2026-04-24 버그 수정: AsyncYolo가 det.cx/cy에 이미
                        # game_region offset을 적용해 반환함 (yolo.py 394~401).
                        # 따라서 game_region.left를 또 더하는 건 이중 오프셋
                        # 버그 — 저장 좌표가 237px+ 오른쪽으로 벗어남.
                        _ax0 = int(float(_det0.cx))
                        _ay0 = int(float(_det0.cy))
                        _gx0, _gy0 = int(_gr0[0]), int(_gr0[1])
                        if (_gx0 <= _ax0 < _gx0 + int(_gr0[2])
                                and _gy0 <= _ay0 < _gy0 + int(_gr0[3])):
                            _worker_self._seq_rclick_target = (_ax0, _ay0)
                            _worker_self.log.info(
                                f"[SEQ-RCLICK-TARGET] 격수 빨탭 위치 저장 "
                                f"screen=({_ax0},{_ay0}) "
                                f"det=({_det0.cx:.0f},{_det0.cy:.0f})"
                            )
                            # 2026-04-24 프레임 덤프: bbox 그려서 저장
                            try:
                                import cv2 as _cv2
                                from pathlib import Path as _Path
                                from datetime import datetime as _dt
                                _crop = _worker_self._last_crop_frame
                                _co = _worker_self._last_crop_offset
                                if _crop is not None:
                                    _img = _crop.copy()
                                    # det는 screen 절대 좌표 → crop 내부 좌표로 변환
                                    _cx_crop = int(_det0.cx - _co[0])
                                    _cy_crop = int(_det0.cy - _co[1])
                                    # bbox 그리기
                                    _x1 = int(_det0.x1 - _co[0])
                                    _y1 = int(_det0.y1 - _co[1])
                                    _x2 = int(_det0.x2 - _co[0])
                                    _y2 = int(_det0.y2 - _co[1])
                                    _cv2.rectangle(_img, (_x1,_y1), (_x2,_y2),
                                                   (0,0,255), 2)
                                    _cv2.circle(_img, (_cx_crop,_cy_crop),
                                                6, (0,255,255), -1)
                                    _ddir = _Path("logs") / "seq_rclick_dump"
                                    _ddir.mkdir(parents=True, exist_ok=True)
                                    _ts = _dt.now().strftime("%H%M%S_%f")[:-3]
                                    _fp = _ddir / f"rclick_{_ts}.png"
                                    _cv2.imwrite(str(_fp), _img)
                                    _worker_self.log.info(
                                        f"[SEQ-RCLICK-DUMP] saved {_fp}"
                                    )
                            except Exception as _de:
                                _worker_self.log.warning(
                                    f"[SEQ-RCLICK-DUMP] fail: {_de}"
                                )
                        else:
                            _worker_self._seq_rclick_target = None
                    else:
                        _worker_self._seq_rclick_target = None
                except Exception as _pe:
                    _worker_self.log.warning(f"[SEQ-RCLICK-TARGET] 예외: {_pe}")
                    _worker_self._seq_rclick_target = None
                try:
                    _send_healer_alert("자힐 하는중")
                except Exception:
                    pass
                def _map_transition_stop() -> bool:
                    try:
                        atk_now = recv.latest()
                        hm = _worker_self.healer_map or ""
                        am = (atk_now.map_name if atk_now else "") or ""
                        if hm and am and hm != am:
                            return True
                        _now = time.time()
                        if _now < getattr(
                                _worker_self, "_map_jump_hold_until", 0.0):
                            return True
                        # 최근 5초 내 맵 변경(CTRL-MAPCHG 또는 MAP-JUMP)이
                        # 발생한 경우 SEQ-B 진입 시점엔 h_map==a_map으로 보여도
                        # 힐러 자기 화면은 아직 새 맵 안정화 전 → defer 필요.
                        if _now - getattr(
                                _worker_self, "_last_map_change_ts",
                                0.0) < 5.0:
                            return True
                    except Exception:
                        return False
                    return False
                # 2026-04-24 SEQ-B는 항상 ESC만 실행. TAB×2 + 토글 재ON은
                # worker가 "같은 맵 + 힐러-격수 근접" 조건 확인 후 일괄 시전.
                # 모든 자힐 케이스에 _pending_tab_lock_until 설정 (분기 제거).
                run_block_ab_combined(
                    cycler=_cycler_ref,
                    log_fn=_log_i,
                    key_release_fn=_keys_ref.release_all,
                    stop_flag=_map_transition_stop,
                )
                _worker_self._pending_tab_lock_until = time.time() + 20.0
                _worker_self.log.info(
                    "[TAB-LOCK-PEND] SEQ-AB 완료 — 힐러-격수 근접 + 맵 동기화 "
                    "확인 후 TAB×2 + 토글 재ON 일괄 시전 (20s 창)"
                )
    
            def _hook_self_resurrect_post(_c):
                # 자가부활 후: 블록B → 자힐 한 번 더 (HP=1 직후 복구).
                block_b_return_to_attacker(cycler=_cycler_ref, log_fn=_log_i)
                try:
                    from ..input.numlock_cycle import press_normal_vk
                    # 메인힐 VK 로 1회 burst.
                    mh_vk = int(_worker_self.skill_vks.get("메인힐", 0x61))
                    t_end = time.time() + 1.2
                    while time.time() < t_end:
                        press_normal_vk(mh_vk)
                        time.sleep(0.1)
                except Exception as _e:
                    self.log.warning(f"[SELF-RES-POST] 자힐 예외: {_e}")
    
            # 임계치는 callable 로 전달 → predicate 매 tick 마다 최신 워커 값 조회.
            # (UI 스피너 변경 시 self.self_heal_hp_thr / gyoungryeok_mp_thr 즉시 반영.)
            _worker_for_thr = self
            skills = default_skills(
                parlyuk_offset_sec=self.parlyuk_offset,
                vk_map=dict(self.skill_vks),
                self_heal_hp_thr=lambda: int(_worker_for_thr.self_heal_hp_thr),
                gyoungryeok_mp_thr=lambda: int(_worker_for_thr.gyoungryeok_mp_thr),
                pre_block_a=_hook_block_a,
                post_block_b=_hook_block_b,
                post_self_resurrect=_hook_self_resurrect_post,
                pre_block_ab=_hook_block_ab,
                parlyuk_maps_getter=lambda: set(_worker_for_thr._parlyuk_maps),
                gyoung_active_getter=lambda: _worker_for_thr._gyoung_active,
                gyoung_active_setter=lambda v: setattr(
                    _worker_for_thr, "_gyoung_active", bool(v)),
            )
            # 초기 enabled 반영 (UI 토글과 일치).
            # "부활" 토글 하나가 자가부활/격수부활 둘 다 제어.
            for s in skills:
                if s.name in self.skill_enabled:
                    s.enabled = self.skill_enabled[s.name]
                # 부활 계열 둘 다 동일 토글.
                if s.name in ("자가부활", "격수부활"):
                    s.enabled = bool(self.skill_enabled.get("부활", True))
            # v6-edge 진단 로그: 최종 skills enabled 상태 한 번 찍기.
            # 파력무참 체크 해제됐는데 시전되는 현상 진단용.
            self.log.info(
                "[SKILL-INIT] skill_enabled=" + str(dict(self.skill_enabled))
            )
            self.log.info(
                "[SKILL-INIT] active="
                + ", ".join(
                    f"{s.name}({'ON' if s.enabled else 'OFF'})"
                    for s in skills
                )
            )
            self._skills = skills
            sched = SkillScheduler(_cast, _ctx, skills=skills)
            sched.set_log(lambda s: self.log.info(s))
            # 2026-04-21: cycler 가 초기 lock 시퀀스 (봉황/혼마술 토글) 를
            # 완료할 때까지 scheduler 시전 유예. Shift+Z 시퀀스와 NumPad scan
            # 이 같은 ms 에 병렬 실행되어 봉황 토글 꼬이는 문제 해결.
            try:
                sched.set_ready_gate(cycler.is_initial_lock_done)
            except Exception as _e:
                self.log.warning(f"[SKILL] ready_gate 연결 실패: {_e}")
            sched.start()
            self._sched = sched
            # 2026-04-20 v6-edge: HpMpReader 가 새 HP/MP 값 갱신하면 즉시
            # edge 감지 → scheduler.request_cast() 로 1회 시전 요청.
            # predicate polling 은 fallback (edge 놓쳤을 때 15s cooldown 후).
            # 사용자 원 설계: "OCR 값 받는 순간 조건 충족 시 즉시 시전, 상태
            # 유지되는 한 재시전 X (상태 전환 edge 기반)".
            def _on_hpmp_update():
                try:
                    hm = _worker_self._hpmp.latest()
                    hp = int(getattr(hm, "hp", -1))
                    mp = int(getattr(hm, "mp", -1))
                except Exception:
                    hp, mp = -1, -1
                # 2026-04-23 사용자 요청: 맵 전환 중 자힐/자가부활 금지.
                # 자힐 시퀀스 = TAB/HOME으로 self-target 7초 → 이 동안 맵이 바뀌면
                # 복귀 TAB이 새 맵에서 격수를 못 잡아 본인 타겟에 영구 고정됨.
                # map_neq(맵 불일치) 또는 MAP-JUMP-HOLD(맵 전환 감지) 시 요청 차단.
                _now_mc = time.time()
                _map_transition_in_progress = (
                    (bool(atk.map_name) and bool(_worker_self.healer_map)
                     and atk.map_name != _worker_self.healer_map)
                    or _now_mc < getattr(_worker_self, "_map_jump_hold_until", 0.0)
                    or (_now_mc - getattr(_worker_self, "_last_map_change_ts", 0.0)
                        < getattr(_worker_self, "_post_mapchg_grace_sec", 5.0))
                )
                # 자가부활 edge: HP==0 로 처음 진입할 때만. (맵 전환 중엔 보류)
                dead_now = (hp == 0)
                if dead_now and not _worker_self._self_dead_prev:
                    if _map_transition_in_progress:
                        _worker_self.log.warning(
                            f"[EDGE-DEFER] HP==0 자가부활 요청 보류 "
                            f"(맵 전환 중 h_map={_worker_self.healer_map!r} "
                            f"a_map={atk.map_name!r})"
                        )
                        # prev 플래그 업데이트 안 함 → 다음 프레임 재평가, 맵
                        # 동기화되면 즉시 발동.
                    else:
                        sched.request_cast("자가부활")
                        _worker_self.log.info("[EDGE] HP==0 → 자가부활 요청")
                        _worker_self._self_dead_prev = dead_now
                else:
                    _worker_self._self_dead_prev = dead_now
                # 자힐 edge — 2026-06-07 자힐 스킬 완전 제거. HP 가 낮아도
                # self-target/자힐 요청을 보내지 않음. (자가부활 edge 는 유지.)
                # 공력증강 edge: MP<thr 로 처음 cross down 할 때만.
                try:
                    thr_mp = int(_worker_self.gyoungryeok_mp_thr)
                except Exception:
                    thr_mp = 30
                mp_below_now = (0 <= mp < thr_mp)
                if mp_below_now and not _worker_self._mp_below_thr_prev:
                    sched.request_cast("공력증강")
                    _worker_self.log.info(
                        f"[EDGE] MP {mp}<{thr_mp} → 공력증강 요청"
                    )
                    # 2026-04-24 공증은 HP 60% 소모 → HP OCR 급감 필터가
                    # 정당한 감소를 reject 하지 않도록 5초 허용 윈도우 설정.
                    try:
                        _worker_self._hpmp.allow_hp_drop_for(5.0)
                    except Exception:
                        pass
                _worker_self._mp_below_thr_prev = mp_below_now
                # 2026-04-22: "공력증강 임박" 힐러측 알림 제거. 격수측
                # _check_gyoungryeok_imminent(임계치 기반 로컬 판정) 와 중복
                # 발생해 오버레이에 두 번 뜨던 증상 해결. 로컬 판정만 유지.
                # polling fallback 도 즉시 깨움 (edge 놓칠 경우 대비).
                sched.notify_tick()

            try:
                self._hpmp.set_on_update(_on_hpmp_update)
            except Exception as _e:
                self.log.warning(f"[HPMP] on_update 연결 실패: {_e}")
            # 2026-04-20 Patch 2.14: blocks_movement 스킬(자힐/자가부활 A+B)
            # 시전 중 방향키 press 잠금. 공력증강·파력무참·백호·파혼술은
            # blocks_movement=False → 이동 병행 허용.
            try:
                sched.set_on_busy_change(keys.set_movement_lock)
            except Exception as _e:
                self.log.warning(f"[SKILL] busy_change 연결 실패: {_e}")
            # 원격 제어(ControlCmd) 콜백 등록.
            def _on_ctrl(c):
                # target_idx: -1 = 전체, 그 외 = peers 인덱스 일치만.
                my_idx = int(getattr(cfg.net, "healer_idx", 0))
                if c.target_idx not in (-1, my_idx):
                    return
                self.apply_remote_control(c.cmd)
            recv.set_control_handler(_on_ctrl)
            recv.start()
            self.log_msg.emit(f"UDP listen {cfg.net.bind_host}:{cfg.net.port}")
            # 힐러→격수 쿨다운 보고용 sender (송신 IP=recv src, port=cfg).
            from ..net.udp_sender import UdpSender
            self._udp_out = UdpSender([], 0)  # send_to 단일 송신 전용.
            # 2026-04-22: OCR 스레드 staggered 시작. 동시에 start 하면
            # predict 주기가 겹쳐 PaddleX GIL 구간에서 경합 → main thread 기아.
            # 0.25s 오프셋으로 시작해서 predict 타이밍 분산.
            import time as _time
            try:
                self._cooldown_ocr.start()
            except Exception as _e:
                self.log.warning(f"[COOLDOWN] thread start 실패: {_e}")
            try:
                _time.sleep(0.25)
                self._buff_ocr.start()
            except Exception as _e:
                self.log.warning(f"[BUFF] thread start 실패: {_e}")
            self.log.info(
                f"[COOLDOWN] healer_idx={int(getattr(cfg.net,'healer_idx',0))} "
                f"atk_recv_port={int(getattr(cfg.net,'attacker_recv_port',45455))} "
                f"region={self._cooldown_ocr.region()} "
                f"nick_region={self._cooldown_ocr.nick_region()}"
            )
        except Exception as e:
            self.log_msg.emit(f"[init 실패] {e}")
            self.log.exception(f"init 실패: {e}")
            self.stopped.emit()
            return
    
        current_dir = "-"
        t0 = time.time()
        frames = 0
        fps = 0.0
        ocr_n = max(1, cfg.ocr.ocr_every_n_frames)
        frame_idx = 0
        last_state = None
        pg = py = po = ptot = 0.0
        pn = 0
        try:
            _prev_loop_t0 = None
            while not self._stop:
                loop_t0 = time.perf_counter()
                # 진짜 iter 주기 = 이번 loop 시작 - 지난 loop 시작. 이 값만이
                # fps 역수와 정확히 일치. t_full_iter 는 loop_t0 부터 측정
                # 지점까지라 그 측정 이후 구간(emit/sleep/continue) 이 빠져 있음.
                if _prev_loop_t0 is not None:
                    self.t_iter_period = (loop_t0 - _prev_loop_t0) * 1000
                _prev_loop_t0 = loop_t0
                self.t_yolo = 0.0
                self.t_ocr = 0.0
                t1 = time.perf_counter()
                frame = grab.grab()
                self.t_grab = (time.perf_counter() - t1) * 1000
                H, W = frame.shape[:2]
    
                frame_idx += 1
                yn = max(1, self.yolo_every_n)
                # 게임영역 기반 크롭 (YOLO + preview 공통). 매 프레임 계산.
                # 없으면 크롭 불가 → YOLO 스킵 정책.
                gr = self._game_region_abs
                crop_frame = None
                gx_off = 0
                gy_off = 0
                if gr:
                    try:
                        ml = int(grab.mon.get("left", 0))
                        mt = int(grab.mon.get("top", 0))
                        gx, gy, gw, gh = gr
                        rx = max(0, int(gx) - ml)
                        ry = max(0, int(gy) - mt)
                        rw2 = min(int(gw), frame.shape[1] - rx)
                        rh2 = min(int(gh), frame.shape[0] - ry)
                        if rw2 > 20 and rh2 > 20:
                            crop_frame = frame[ry:ry + rh2, rx:rx + rw2]
                            gx_off, gy_off = rx, ry
                            # 2026-04-24 SEQ-RCLICK 덤프용 최신 crop 저장
                            self._last_crop_frame = crop_frame.copy()
                            self._last_crop_offset = (rx, ry)
                    except Exception:
                        crop_frame = None
    
                # 저사양: YOLO imgsz 런타임 반영 (최저 160 하한).
                try:
                    desired_imgsz = max(160, int(self.yolo_imgsz))
                    if getattr(yolo, "imgsz", None) != desired_imgsz:
                        yolo.imgsz = desired_imgsz
                except Exception:
                    pass
                # 저사양: OCR poll_sec 런타임 반영 (cooldown/xp).
                try:
                    if self.ocr_poll_sec and self.ocr_poll_sec > 0:
                        p = float(self.ocr_poll_sec)
                        co = getattr(self, "_cooldown_ocr", None)
                        if co is not None and getattr(co, "poll_sec", 0) != p:
                            co.poll_sec = p
                        xo = getattr(self, "_xp_ocr", None)
                        if xo is not None and getattr(xo, "poll_sec", 0) != p:
                            xo.poll_sec = p
                except Exception:
                    pass
    
                if frame_idx % yn == 0 and not self.follow_light:
                    t1 = time.perf_counter()
                    # 백그라운드 YOLO 에 최신 frame 제출 (비블로킹, 즉시 리턴).
                    if crop_frame is not None:
                        yolo_async.submit(crop_frame, (gx_off, gy_off))
                    elif (time.time()
                            - getattr(self, "_no_gr_warn_ts", 0.0) > 5.0):
                        self.log.warning(
                            "[GAME] region 미지정 → YOLO 스킵. "
                            "GUI에서 '게임' 영역을 먼저 지정하세요."
                        )
                        self._no_gr_warn_ts = time.time()
                    # 최신 detection 참조 (백그라운드 스레드 결과).
                    new_dets, _off, age_ms, predict_ms = yolo_async.latest()
                    # t_yolo 는 submit 블로킹 시간 (≈0) + 백그라운드 predict ms
                    # 참고값. 메인 루프 실제 점유 시간은 submit 만.
                    self.t_yolo = float(predict_ms)
                    if age_ms >= 0:
                        all_dets = new_dets
                        # RED/WHITE 분리. RED 최고 conf → red_tab 신호용 det.
                        det = None
                        det_white = None
                        for d in all_dets:
                            if d.tab_color == "RED":
                                if d.w < self.min_w or d.h < self.min_h:
                                    continue
                                if det is None or d.conf > det.conf:
                                    det = d
                            else:
                                if (d.w < self.white_min_w
                                        or d.h < self.white_min_h):
                                    continue
                                if (det_white is None
                                        or d.conf > det_white.conf):
                                    det_white = d
                        self._last_all_dets = all_dets
                        self._last_det = det
                        self._last_det_white = det_white
                    else:
                        # 아직 한 번도 detection 못 함 → 이전(빈) 상태 유지.
                        all_dets = self._last_all_dets
                        det = self._last_det
                        det_white = getattr(self, "_last_det_white", None)
                else:
                    all_dets = self._last_all_dets
                    det = self._last_det
                    det_white = getattr(self, "_last_det_white", None)
    
                if frame_idx % ocr_n == 0:
                    t1 = time.perf_counter()
                    try:
                        # v5.16: 격수 UDP로 수신·축적된 맵 이름 집합을 OCR에 주입.
                        # resolver가 맵 전환 첫 프레임부터 canonical 강제 교정에 사용.
                        # OCR이 "족" 등 한 글자 영구 누락해도 h_map = a_map 일치 보장.
                        try:
                            ocr.set_known_maps(fol._map_last_coord.keys())
                        except Exception:
                            pass
                        # 2026-04-21: OCR 백그라운드화. submit 은 비블로킹.
                        # 메인 루프는 최신 결과(latest) 만 참조. 결과가 아직
                        # 없으면 r=None — 이전 healer_coord/map 유지.
                        ocr_async.submit(frame)
                        r = ocr_async.latest()
                        if r is None:
                            # 초기 1~2프레임: 아직 OCR 결과 없음 → 아무 것도 안 함.
                            raise RuntimeError("_no_result")
                        if r.coord is not None:
                            self.healer_coord = r.coord
                        if r.map_name:
                            self.healer_map = r.map_name
                        # 힐러 좌표 OCR 실패 진단: raw/crop_box 기록 (1초 스로틀).
                        if r.coord is None:
                            now_fail = time.time()
                            if now_fail - getattr(self, "_last_h_ocr_fail_log",
                                                  0.0) >= 1.0:
                                self._last_h_ocr_fail_log = now_fail
                                box = getattr(ocr, "_last_coord_box", None)
                                # 실패 순간 내부 상태까지 같이 찍어야 어느 분기가
                                # 트리거됐는지 알 수 있음. pending/last_map/last_coord/
                                # reject_count 전부 노출.
                                self.log.info(
                                    f"[ocr-fail-H] raw={r.raw_coord_text!r} "
                                    f"raw_m_fresh={r.raw_map_text!r} "
                                    f"last_map={getattr(ocr,'_last_map','?')!r} "
                                    f"pending_map={getattr(ocr,'_pending_map','?')!r} "
                                    f"last_coord={getattr(ocr,'_last_coord',None)} "
                                    f"reject_count={getattr(ocr,'_reject_count','?')} "
                                    f"frame={frame.shape[1]}x{frame.shape[0]} "
                                    f"crop_box={box}"
                                )
                                # 실시간 게임이라 스크린샷 불가 → 실패 순간 프레임을
                                # 자동 덤프. logs/ocr_fail/ 아래 타임스탬프로 저장.
                                # 1초 스로틀과 공유되어 디스크 폭주 방지.
                                try:
                                    import cv2
                                    dump_dir = Path("logs") / "ocr_fail"
                                    dump_dir.mkdir(parents=True, exist_ok=True)
                                    ts = datetime.now().strftime(
                                        "%Y%m%d_%H%M%S_%f")[:-3]
                                    fp = dump_dir / f"ocr_fail_{ts}.png"
                                    cv2.imwrite(str(fp), frame)
                                    self.log.info(
                                        f"[ocr-fail-H-DUMP] saved {fp}"
                                    )
                                except Exception as _e:
                                    self.log.info(
                                        f"[ocr-fail-H-DUMP] fail: {_e}"
                                    )
                        # 맵 OCR 진단: fresh OCR 결과(pending 전) vs resolved map_name.
                        # 1초 스로틀. 둘이 다르면 pending 로직이 새 맵을 막고 있다는 뜻.
                        now_mlog = time.time()
                        if now_mlog - getattr(self, "_last_h_map_log", 0.0) >= 1.0:
                            self._last_h_map_log = now_mlog
                            if r.raw_map_text != r.map_name:
                                self.log.info(
                                    f"[ocr-map-H] fresh={r.raw_map_text!r} "
                                    f"resolved={r.map_name!r}"
                                )
                    except RuntimeError as _noresult:
                        # 초기 warm-up 전용 sentinel. 에러 아님.
                        if str(_noresult) != "_no_result":
                            self.log_msg.emit(f"OCR 에러: {_noresult}")
                    except Exception as e:
                        self.log_msg.emit(f"OCR 에러: {e}")
                    # t_ocr = 백그라운드 OCR 마지막 predict ms (참고값).
                    # 메인 루프 blocking 시간은 submit 자체 ≈ 0ms.
                    try:
                        self.t_ocr = ocr_async.last_predict_ms()
                    except Exception:
                        self.t_ocr = 0.0

                self.t_total = (time.perf_counter() - loop_t0) * 1000
    
                atk = recv.latest() or State()
                # ── 이동 lock 상태 추적 (따라가기 절대 보장) ───────────────
                # (1) True→False edge: SEQ-AB 종료 직후 즉시 재hold 요청.
                #     main loop 의 current_dir 이 SEQ-AB 전 방향 그대로 남아
                #     want==current_dir 조건으로 재발송 안 하던 버그 수정.
                # (2) 10초 stuck: scheduler 예외/데드락 시 강제 해제.
                try:
                    _lock_now = keys.is_movement_locked()
                    if self._was_movement_locked and not _lock_now:
                        self._need_rehold = True
                        self.log.info(
                            "[LOCK-RELEASED] movement_lock 해제 감지 → 재hold 예약"
                        )
                    self._was_movement_locked = _lock_now
                    _t_now = time.time()
                    if _lock_now:
                        if self._movement_lock_since == 0.0:
                            self._movement_lock_since = _t_now
                        elif (_t_now - self._movement_lock_since) > 10.0:
                            keys.set_movement_lock(False)
                            self._movement_lock_since = 0.0
                            self._need_rehold = True
                            self.log.warning(
                                "[LOCK-STUCK] movement_lock 10초 초과 → 강제 해제"
                            )
                    else:
                        self._movement_lock_since = 0.0
                except Exception:
                    pass
                # v6-edge: attacker UDP State 기반 edge 감지 → 격수부활.
                try:
                    atk_hp = int(getattr(atk, "hp_pct", -1))
                    # 격수부활: attacker HP==0 cross-down + 힐러 살아있을 때.
                    self_hp_now = int(self._hpmp.latest().hp) if self._hpmp else -1
                    atk_dead_now = (atk_hp == 0 and self_hp_now > 0)
                    if atk_dead_now and not self._attacker_dead_prev:
                        self._sched.request_cast("격수부활")
                        self.log.info(
                            f"[EDGE] atk_hp==0 & self_hp={self_hp_now}>0 → 격수부활 요청"
                        )
                    self._attacker_dead_prev = atk_dead_now
                except Exception:
                    pass
                # 2026-04-22 사용자 요청: 파력무참 버프 지속 중 coord_tol=1 강제,
                # 만료되면 원복. buff OCR 에서 "파력무참" >0 관측 시 활성 판정.
                # -1(미관측) 은 상태 전환 보류 (현재 상태 유지).
                try:
                    _bf_latest = self._buff_ocr.latest()
                    _parlyuk_val = int(
                        (getattr(_bf_latest, "skills", {}) or {})
                        .get("파력무참", -1)
                    )
                    if _parlyuk_val > 0:
                        if not self._parlyuk_buff_active:
                            self._parlyuk_buff_active = True
                            self._coord_tol_saved = int(self.coord_tol)
                            self.coord_tol = 1
                            self.log.info(
                                f"[PARLYUK-TOL] 버프 감지({_parlyuk_val}s) "
                                f"coord_tol {self._coord_tol_saved}→1 강제"
                            )
                    elif _parlyuk_val == 0:
                        if self._parlyuk_buff_active:
                            _restore = (
                                int(self._coord_tol_saved)
                                if self._coord_tol_saved is not None
                                else 1
                            )
                            self.coord_tol = _restore
                            self.log.info(
                                f"[PARLYUK-TOL] 버프 만료 → coord_tol 1→{_restore} 복원"
                            )
                            self._parlyuk_buff_active = False
                            self._coord_tol_saved = None
                except Exception:
                    pass
                red_raw = det is not None  # RED bbox 검출 (override 전).
                white_raw = det_white is not None  # WHITE bbox 검출 (가드용).
                # 맵전환 전 빨탭 확인 게이트용 (_decide_move_raw 의 exit 분기에서 참조).
                self._cur_red_raw = red_raw
                self._cur_white_raw = white_raw
                self._cur_red_cx = float(det.cx) if det is not None else -1.0
                self._cur_red_cy = float(det.cy) if det is not None else -1.0
                atk.red_tab = red_raw
                # 따라가기 전용 모드: 빨탭 무시 → COMBAT 진입 차단.
                # red_raw 자체는 유지(디버그/로그용) but atk.red_tab=False로
                # FSM의 전투 분기를 막는다. map_neq 때와 동일한 방식.
                if self.follow_only:
                    atk.red_tab = False
                fol.note_healer_map(self.healer_map, self.healer_coord)
                state = fol.update(atk)
    
                now_sec = time.time()
    
                # --- [DIAG] OCR 첫 획득 / 변화 로그 ---
                if self._last_h_map != self.healer_map and self.healer_map:
                    self.log.info(
                        f"[OCR-H] 힐러맵 OCR "
                        f"{self._last_h_map!r} → {self.healer_map!r}"
                    )
                    # 맵 변경 시각 기록 → 자힐/자가부활 그레이스 적용
                    if self._last_h_map:  # 첫 획득이 아닌 "전환"만 기록
                        self._last_map_change_ts = now_sec
                    self._last_h_map = self.healer_map
                if atk.map_name and atk.map_name != self._last_a_map:
                    self.log.info(
                        f"[OCR-A] 격수맵(UDP) "
                        f"{self._last_a_map!r} → {atk.map_name!r} "
                        f"a_coord=({atk.x},{atk.y}) valid={atk.coord_valid}"
                    )
                    # 격수 맵 변경도 그레이스 트리거 (힐러 OCR보다 먼저 올 때 대비)
                    if self._last_a_map:
                        self._last_map_change_ts = max(
                            self._last_map_change_ts, now_sec
                        )
                    self._last_a_map = atk.map_name
                # 2026-04-24 자힐 중: _hook_block_ab에서 저장한 격수 빨탭
                # 위치를 0.5s 간격 반복 우클릭. 계산/예측 없이 저장값 그대로.
                try:
                    _locked_now = keys.is_movement_locked()
                except Exception:
                    _locked_now = False
                if _locked_now and self._seq_rclick_target is not None \
                        and now_sec - self._seq_rclick_last_ts >= 0.5:
                    try:
                        from ..input.keys import mouse_click_at
                        _tx, _ty = self._seq_rclick_target
                        mouse_click_at(_tx, _ty, button="right")
                        self._seq_rclick_last_ts = now_sec
                        self.log.info(
                            f"[SEQ-RCLICK] screen=({_tx},{_ty}) "
                            f"(저장된 격수 빨탭 위치)"
                        )
                    except Exception as _rce:
                        self.log.warning(f"[SEQ-RCLICK] 예외: {_rce}")
                # 자힐 종료 시 저장 타겟 해제 (다음 자힐 진입에 새로 저장).
                elif not _locked_now and self._seq_rclick_target is not None:
                    self._seq_rclick_target = None
                # 2026-04-24 pending TAB-LOCK 시퀀스:
                # SEQ-B가 ESC만 하고 끝낸 상태 (토글 OFF + cycler suspended).
                # 조건 일괄 확인 후 묶음 시전:
                #   1) TAB → TAB (격수 빨탭 고정)
                #   2) slots 토글 재ON (메인힐 자동 발동 재개)
                #   3) cycler.resume() (백그라운드 재lock 복귀)
                # 트리거 조건:
                #   - 같은 맵 (h_map == a_map)
                #   - 힐러-격수 맨해튼 거리 ≤ 10 (격수 화면 내 TAB 순환 포함)
                #   - 맵 변경 후 0.5s 안정화
                # red_raw 기반 트리거는 불가 (self-target이면 self 머리 위
                # 빨탭 뜸, YOLO로 구분 불가). 위치 기반이 유일하게 신뢰 가능.
                _TAB_LOCK_DIST_THR = 10
                if (now_sec < self._pending_tab_lock_until
                        and bool(self.healer_map) and bool(atk.map_name)
                        and self.healer_map == atk.map_name
                        and self.healer_coord is not None
                        and atk.coord_valid):
                    _hx, _hy = self.healer_coord
                    _ax, _ay = atk.x, atk.y
                    _dist = abs(_hx - _ax) + abs(_hy - _ay)
                    if now_sec - self._last_map_change_ts >= 0.5 \
                            and _dist <= _TAB_LOCK_DIST_THR:
                        try:
                            from ..input.target_sequence import (
                                _press_vk, VK_TAB, DEFAULT_SLOTS
                            )
                            from ..input.numlock_cycle import skill_lock_vk
                            # (1) TAB → TAB
                            _press_vk(VK_TAB)
                            time.sleep(0.1)
                            _press_vk(VK_TAB)
                            time.sleep(0.1)
                            # (2) 토글 재ON: cycler slots 또는 DEFAULT
                            _slots = list(DEFAULT_SLOTS)
                            if self._cycler is not None:
                                try:
                                    _slots = list(self._cycler.slots)
                                except Exception:
                                    pass
                            for _vk in _slots:
                                try:
                                    skill_lock_vk(_vk)
                                    self.log.info(
                                        f"[TAB-LOCK]   relock vk={hex(_vk)}"
                                    )
                                except Exception as _le:
                                    self.log.warning(
                                        f"[TAB-LOCK] relock 예외 vk={hex(_vk)}: {_le}"
                                    )
                            if self._cycler is not None:
                                try:
                                    self._cycler._locked.clear()
                                    self._cycler._locked.update(_slots)
                                except Exception:
                                    pass
                            # (3) cycler resume
                            if self._cycler is not None:
                                try:
                                    self._cycler.resume()
                                except Exception as _ce:
                                    self.log.warning(
                                        f"[TAB-LOCK] cycler resume 예외: {_ce}"
                                    )
                            self.log.info(
                                f"[TAB-LOCK] TAB×2 + 토글 재ON + cycler "
                                f"resume 완료 h_map={self.healer_map!r} "
                                f"h={self.healer_coord} a=({_ax},{_ay}) "
                                f"dist={_dist}"
                            )
                        except Exception as _te:
                            self.log.warning(f"[TAB-LOCK] 실패: {_te}")
                        self._pending_tab_lock_until = 0.0
                if self.healer_coord != self._last_h_coord:
                    if self._last_h_coord is None and self.healer_coord is not None:
                        self.log.info(
                            f"[OCR-H] 힐러좌표 최초 획득 {self.healer_coord}"
                        )
                    self._h_coord_last_change = now_sec
                    self._last_h_coord = self.healer_coord
    
                # --- [DIAG] UDP seq 변화 감지 (edge 기반: 진입/복귀 1회씩) ---
                seq_alive = atk.seq > self._last_seq
                if seq_alive:
                    # seq 증가: stall 중이었으면 복귀 로그 1회.
                    if self._udp_stalled:
                        stall_dur = now_sec - self._udp_stall_since
                        self.log.info(
                            f"[UDP-RESUME] seq={atk.seq} "
                            f"stall_dur={stall_dur:.1f}s"
                        )
                        self._udp_stalled = False
                    # seq 동일한 기간 진입 시각 추적.
                    self._last_seq = atk.seq
                    self._udp_stall_since = now_sec
                else:
                    # seq 동일: 3초 넘었고 아직 경고 안 했으면 진입 경고 1회.
                    if (
                        not self._udp_stalled
                        and atk.seq > 0
                        and (now_sec - self._udp_stall_since) > 3.0
                    ):
                        self.log.warning(
                            f"[UDP-STALL] seq={atk.seq} 3초+ 동일 — "
                            f"격수 송신 중단? recv.latest()={recv.latest() is not None}"
                        )
                        self._udp_stalled = True
    
                # --- [DIAG] 힐러 창 FG 변화 감지 ---
                fg_ok = _is_fg_hwnd(keys.hwnd)
                if self._last_fg_ok is not None and fg_ok != self._last_fg_ok:
                    self.log.info(
                        f"[FG] 힐러창 포커스 {self._last_fg_ok}→{fg_ok} "
                        f"keys.hwnd={keys.hwnd} keys.method={keys.method}"
                    )
                self._last_fg_ok = fg_ok

                # 워커 시작 후 옛바창 포커스 확보되면 's' 키 1회 송신.
                # 사용자 지시 2026-04-21.
                if not self._startup_s_sent and fg_ok:
                    try:
                        _send_input(0x53, up=False)  # 'S' down
                        time.sleep(0.05)
                        _send_input(0x53, up=True)   # 'S' up
                        self.log.info("[STARTUP-S] 's' 키 송신 완료")
                    except Exception as _e:
                        self.log.warning(f"[STARTUP-S] 실패: {_e}")
                    self._startup_s_sent = True
    
                # 맵 불일치 직접 판정 (FSM 상태 대신 — 피드백 2).
                # 양쪽 맵이 확정(len>=2)되고 다르면 True.
                h_ok = bool(self.healer_map) and len(self.healer_map) >= 2
                a_ok = bool(atk.map_name) and len(atk.map_name) >= 2
                # 격수 좌표급변(=맵전환)→map_change_pending 즉시 ON (맵OCR 지연 무관,
                # map_seq/좌표 기반 0.01초). 사냥중 흰탭/trail reject 근본해결:
                # map_name(느린 OCR) 대신 격수 좌표신호를 신뢰.
                map_neq = ((h_ok and a_ok and self.healer_map != atk.map_name)
                           or bool(getattr(atk, "map_change_pending", False)))
    
                # 격수 F1 예고 플래그 엣지 로그.
                f1_pending = bool(getattr(atk, "map_change_pending", False))
                if f1_pending and not self._last_f1_pending:
                    self.log.info(
                        f"[F1-PENDING] ON — 격수 예고 수신, B3 coord-follow 차단 활성 "
                        f"h_map={self.healer_map!r} a_map={atk.map_name!r} "
                        f"h={self.healer_coord} a=({atk.x},{atk.y})"
                    )
                elif not f1_pending and self._last_f1_pending:
                    self.log.info(
                        f"[F1-PENDING] OFF — 예고 창 만료, 정상 follow 복귀 "
                        f"h_map={self.healer_map!r} a_map={atk.map_name!r} "
                        f"h={self.healer_coord} a=({atk.x},{atk.y})"
                    )
                self._last_f1_pending = f1_pending
    
                # 맵 불일치 동안 YOLO 빨탭 무시 (사용자 지시:
                # "yolo무시하고 위의 좌표대로 진행해서 맵이동").
                if map_neq:
                    atk.red_tab = False
    
                # --- [MAPCHG] 전이 상세 로그 ---
                if map_neq and not self._last_map_neq:
                    # False→True: 힐러와 격수가 방금 다른 맵에 있게 됨.
                    ls = fol.last_seen_in(self.healer_map)
                    self.log.info(
                        f"[MAPCHG-ENTER] h_map={self.healer_map!r} "
                        f"a_map={atk.map_name!r} "
                        f"h_coord={self.healer_coord} "
                        f"a_coord=({atk.x},{atk.y}) a_valid={atk.coord_valid} "
                        f"ls={ls} "
                        f"exit=(map={fol.exit_map()!r} "
                        f"coord={fol.exit_coord()} "
                        f"dir={fol.exit_dir()!r}) "
                        f"a_last_dir={fol.direction()!r} "
                        f"red_raw={red_raw}(→ override False) "
                        f"known_maps={list(fol._map_last_coord.keys())} "
                        f"armed={self.armed}"
                    )
                    # MAPCHG 진입 시 양쪽 맵 trail tail + progress idx 스냅샷.
                    try:
                        self.log.info(
                            f"[MAPCHG-TRAIL] h_map={self.healer_map!r} "
                            f"trail_tail={fol.trail_tail(self.healer_map, 15)} "
                            f"progress={fol.progress_of(self.healer_map)}/"
                            f"{len(fol.trail_for(self.healer_map))}"
                        )
                        self.log.info(
                            f"[MAPCHG-TRAIL] a_map={atk.map_name!r} "
                            f"trail_tail={fol.trail_tail(atk.map_name, 15)} "
                            f"progress={fol.progress_of(atk.map_name)}/"
                            f"{len(fol.trail_for(atk.map_name))}"
                        )
                    except Exception as e:
                        self.log.info(f"[MAPCHG-TRAIL] 덤프 실패: {e}")
                    # MAPCHG-TAB 원샷 제거 (2026-04-16): 새 TAB-CONFIRM은
                    # 흰탭 감지 시 arm되므로 맵 전환 즉시 Tab 송신은 불필요.
                elif (not map_neq) and self._last_map_neq:
                    # True→False: 해소됨.
                    self._past_stuck_since = 0.0  # past_unstick 상태 리셋
                    self.log.info(
                        f"[MAPCHG-EXIT] h_map={self.healer_map!r} "
                        f"a_map={atk.map_name!r} "
                        f"h_coord={self.healer_coord} "
                        f"a_coord=({atk.x},{atk.y})"
                    )
                self._last_map_neq = map_neq
                self._last_state = state
    
                # 이동/스킬 금지. 맵 불일치 동안 스킬 차단, 이동은 허용.
                skill_blocked = (state in (FsmState.DEAD, FsmState.DISCONNECTED)
                                  or map_neq)
                # 따라가기 전용: self.follow_only=True면 주력힐/파력무참 모두 OFF.
                cycler.set_armed(self.armed and not skill_blocked
                                  and not self.follow_only)
                sched.set_armed(self.armed and not skill_blocked
                                 and not self.follow_only)
    
                # I안: 격수 맵전환 직후 pause 창 — 키 release + 이동 결정 스킵.
                # Follower.update()에서 map_seq edge 감지 시 _pause_until 설정됨.
                # pause 구간엔 want/reason을 강제로 "-"/MAP-PAUSE로 고정해 키 차단.
                # TAB-CONFIRM (2026-04-16 최종, 사용자 G1/G2/G3):
                #   Route A 단일: Tab → A_wait_red → red 3프레임 → done_ok
                #     (Tab = 힐러 self-target 빨탭 → 다음 맵에서 격수 전이)
                #   map_neq 분기 없음. Route B/ESC 폐기 (격수 스킬 시전 중 ESC 무의미).
                #   흰탭 중 방향키=커서만 이동이라 _tab_confirm_active 동안 이동 차단.
                #   is_paused 시 tab_confirm_tick 호출해 Tab 송신 관리. hard timeout 10s.
                map_paused = fol.is_paused(now_sec)
                if map_paused:
                    # #2: h_coord 전달 → Pre-Tab stability 감시에 사용.
                    tab_action = fol.tab_confirm_tick(
                        now_sec, red_raw, white_raw,
                        h_coord=self.healer_coord,
                    )
                    sub = fol._tab_confirm_substate
                    if tab_action == 'send_home':
                        try:
                            fg_pre, fg_match = self._press_home(keys.hwnd)
                            self.log.info(
                                f"[TAB-CONFIRM-HOME] sub={sub!r} "
                                f"fg=0x{fg_pre:x} match={fg_match} "
                                f"red_raw={red_raw} white_raw={white_raw}"
                            )
                        except Exception as e:
                            self.log.info(f"[TAB-CONFIRM-HOME] 실패: {e}")
                    elif tab_action == 'send_tab':
                        try:
                            fg_pre, fg_match = self._press_tab(keys.hwnd)
                            self.log.info(
                                f"[TAB-CONFIRM-TAB] sub={sub!r} "
                                f"fg=0x{fg_pre:x} match={fg_match} "
                                f"red_raw={red_raw} white_raw={white_raw} "
                                f"retry={fol._tab_retry_count} "
                                f"fg_retry={fol._tab_fg_retry_count}"
                            )
                            # #1: fg 비일치 시 Tab 재큐잉.
                            if not fg_match:
                                retried = fol.note_tab_fg_mismatch(now_sec)
                                self.log.info(
                                    f"[TAB-CONFIRM-FG-RETRY] retried={retried} "
                                    f"count={fol._tab_fg_retry_count}/"
                                    f"{fol._tab_fg_retry_max} fg=0x{fg_pre:x} "
                                    f"hwnd=0x{int(keys.hwnd):x}"
                                )
                        except Exception as e:
                            self.log.info(f"[TAB-CONFIRM-TAB] 실패: {e}")
                    elif tab_action == 'done_ok':
                        elapsed = now_sec - fol._tab_confirm_started
                        # #4: post-confirm stabilize 창 설정 (150ms).
                        fol.note_tab_done_ok(now_sec)
                        self.log.info(
                            f"[TAB-CONFIRM-DONE] 복귀 확정 (red&!white "
                            f"{fol._tab_confirm_required}f) "
                            f"elapsed={elapsed*1000:.0f}ms route=A "
                            f"map_neq_at_arm={fol._tab_confirm_map_neq_at_arm} "
                            f"retry={fol._tab_retry_count} "
                            f"post_pause="
                            f"{fol._tab_post_confirm_duration*1000:.0f}ms"
                        )
                    elif tab_action == 'retry_arm':
                        # #5: hard timeout 후 Tab 재시도 진입.
                        self.log.warning(
                            f"[TAB-CONFIRM-RETRY] hard timeout → Tab 재arm "
                            f"count={fol._tab_retry_count}/"
                            f"{fol._tab_retry_max} sub={sub!r} "
                            f"red_raw={red_raw} white_raw={white_raw}"
                        )
                    elif tab_action == 'done_timeout':
                        elapsed = now_sec - fol._tab_confirm_started
                        self.log.warning(
                            f"[TAB-CONFIRM-TIMEOUT] {elapsed:.1f}s 내 복귀 "
                            f"실패 retry={fol._tab_retry_count} → "
                            f"일반 follow 복귀 sub={sub!r} "
                            f"h_map={self.healer_map!r} a_map={atk.map_name!r}"
                        )
                    want, reason = "-", "MAP-PAUSE"
                    if current_dir != "-":
                        keys.release_all()
                        self.log.info(
                            f"[MAP-PAUSE] release_all remain="
                            f"{fol.pause_remaining(now_sec)*1000:.0f}ms "
                            f"tab_confirm_active={fol._tab_confirm_active} "
                            f"atk_map={atk.map_name!r} "
                            f"h_map={self.healer_map!r}"
                        )
                        current_dir = "-"
                else:
                    # 재설계 v4 최종 (2026-04-16, 사용자 G1/G2/G3):
                    # 핵심: 흰탭 상태 = 방향키 무효 (커서만 이동) → 무조건 차단.
                    #   빨탭 상태에서만 캐릭터 방향키 이동 가능.
                    # 1) 첫 프레임 덤프 (edge, 5s throttle).
                    # 2) raw_white_only → release_all + want="-" (무조건).
                    # 3) arm(Tab 송신)은 3프레임 + !tab_active + coord_valid +
                    #    FSM not in (DEAD/DISCONNECTED).
                    # 4) Route A 단일: Tab → 힐러 자기 빨탭 → 포탈 통과 시
                    #    격수에게 자동 전이 (G1). Route B/ESC 폐기 (G2).
                    # 차단은 공격적: 흰탭 감지되면 red 공존이어도 방향키 차단.
                    # ARM(Tab 송신)은 보수적: red_raw=False일 때만 (red+white
                    # 동시일 땐 이미 누가 타겟된 상태, Tab 재송신은 오히려 엉뚱).
                    # Patch 2.17: SEQ-AB(자힐/자가부활) 내부 self-target TAB 이
                    # 화면에 흰 커서 띄우고 YOLO 가 white_tab 으로 감지 → 외부
                    # TAB-CONFIRM 이 병렬 TAB 쏴 SEQ-A/B 와 경쟁 → 자힐 꼬임.
                    # scheduler.is_blocking() True 면 blocks_movement 스킬
                    # 시전 중 → WHITETAB 경로 통째로 skip.
                    try:
                        seq_blocking = bool(self._sched.is_blocking()) \
                            if self._sched else False
                    except Exception:
                        seq_blocking = False
                    # 2026-06-11 사용자 지적: 빨탭(red_raw)이 동시에 검출되면
                    # YOLO 흰탭 오검출(false white)로 간주 → block 안 함. 게임상
                    # 빨탭 락이면 흰탭 커서는 없음, 공존=오검출이라 빨탭 우선 이동.
                    # (23,1 빨탭인데 흰탭 박스도 떠 WHITETAB-BLOCK 정지한 사고.)
                    block_by_white = (white_raw and not red_raw
                                      and not seq_blocking)
                    # TAB-CONFIRM 진행 중엔 confirm 누적 금지.
                    # (active 풀리는 순간 쌓인 confirm이 한꺼번에 폭발해
                    #  Tab 재송신되는 문제 방지. 2026-04-18 로그에서 confirm=13
                    #  관측.)
                    if fol._tab_confirm_active or seq_blocking:
                        self._whitetab_confirm = 0
                        self._whitetab_seen_ts = 0.0
                    elif block_by_white:
                        # edge: streak 새로 시작일 때만 덤프 (throttle 5s).
                        is_new_streak = (self._whitetab_confirm == 0)
                        if (is_new_streak
                                and (now_sec - self._whitetab_suspect_dumped
                                     >= 5.0)):
                            self._whitetab_suspect_dumped = now_sec
                            try:
                                import cv2 as _cv2  # 함수 내 로컬 스코프 가드
                                dump_dir = Path("logs") / "whitetab_suspect"
                                dump_dir.mkdir(parents=True, exist_ok=True)
                                ts = datetime.now().strftime(
                                    "%Y%m%d_%H%M%S_%f")[:-3]
                                tag = "mapneq" if map_neq else "sameMap"
                                fp = dump_dir / f"whitetab_{tag}_{ts}.png"
                                _cv2.imwrite(str(fp), frame)
                                self.log.info(
                                    f"[WHITETAB-DUMP] saved {fp} "
                                    f"red_coexist={red_raw}"
                                )
                            except Exception as _e:
                                self.log.info(
                                    f"[WHITETAB-DUMP] fail: {_e}"
                                )
                        self._whitetab_confirm += 1
                        self._whitetab_seen_ts = now_sec
                    else:
                        # 깜빡임 흡수: 마지막 감지 이후 250ms까지 confirm 유지.
                        # 흰탭이 1프레임 꺼졌다 다시 켜지는 YOLO 오탐 패턴에서도
                        # ARM(3프레임)까지 도달 가능. 250ms 초과 gap은 리셋.
                        if (self._whitetab_confirm > 0
                                and now_sec - self._whitetab_seen_ts > 0.25):
                            self._whitetab_confirm = 0
                            self._whitetab_seen_ts = 0.0
    
                    if block_by_white:
                        # 무조건 방향키 차단 (map_neq / red 공존 무관).
                        if current_dir != "-":
                            keys.release_all()
                            self.log.info(
                                f"[WHITETAB-BLOCK] 즉시 release confirm="
                                f"{self._whitetab_confirm} "
                                f"red_coexist={red_raw} "
                                f"map_neq={map_neq} fsm={state.value} "
                                f"h_map={self.healer_map!r} "
                                f"a_map={atk.map_name!r}"
                            )
                            current_dir = "-"
                        # arm 게이트: 3프레임 + !tab_active + coord_valid +
                        # not red_raw (red+white 공존은 Tab 재송신 금지).
                        # FSM은 DEAD/DISCONNECTED만 제외.
                        arm_ok_fsm = state not in (FsmState.DEAD,
                                                    FsmState.DISCONNECTED)
                        # 2026-06-10 속도: 격수 맵전환 확정 신호(map_neq 또는
                        # map_change_pending) 동반 흰탭이면 오탐 아님 → confirm
                        # 1프레임 즉시 arm. 사람의 "격수 사라짐 보고 즉시 Tab"
                        # 재현 (3프레임 대기 ~0.15s 제거). 신호 없으면 기존 3.
                        _arm_need = 3
                        if map_neq or bool(getattr(
                                atk, "map_change_pending", False)):
                            _arm_need = 1
                        # 따라가기 전용: TAB-CONFIRM 자체 차단. Tab 송신 금지.
                        # 빨탭 없이 coord-follow + exit_dir 만으로 맵 이동 수행.
                        # 수정A (2026-06-11): 본인 빨탭 후 맵전환 grace 중엔
                        # arm 억제 (5-1 본인빨탭 루프 차단). grace 끝나도 흰탭
                        # 지속이면 그때 정상 arm.
                        _grace = (hasattr(fol, "post_tab_grace_active")
                                  and fol.post_tab_grace_active(now_sec))
                        if (self._whitetab_confirm >= _arm_need
                                and not red_raw
                                and not fol._tab_confirm_active
                                and atk.coord_valid
                                and arm_ok_fsm
                                and not self.follow_only
                                and not _grace):
                            self.log.info(
                                f"[WHITETAB-ARM] confirm="
                                f"{self._whitetab_confirm} route=A "
                                f"map_neq={map_neq} fsm={state.value} "
                                f"h_map={self.healer_map!r} "
                                f"a_map={atk.map_name!r}"
                            )
                            fol.arm_tab_confirm(now_sec, map_neq)
                            self._whitetab_confirm = 0
                            want, reason = "-", "WHITETAB-ARM"
                        else:
                            want, reason = "-", "WHITETAB-BLOCK"
                    else:
                        # 2026-04-23: blocks_movement 스킬(자힐/자가부활 SEQ-AB)
                        # 시전 중이면 이동 결정 자체 스킵. 방향키 press 금지 +
                        # STUCK 감지/blacklist 등록 차단 (자힐 중 U 방향이
                        # blacklist 등록돼 자힐 끝나고 빠꾸/우회 도는 문제 해결).
                        _is_locked = False
                        try:
                            _is_locked = keys.is_movement_locked()
                        except Exception:
                            _is_locked = False
                        if _is_locked:
                            want, reason = "-", "SEQ-AB-LOCK"
                            # 2026-04-23 자힐 중에도 _prev_atk_coord는 계속
                            # 업데이트. 안 그러면 자힐 종료 시 격수 좌표가
                            # 많이 변한 걸로 보여 MAP-JUMP 오탐 → 엉뚱한 방향
                            # 으로 빠꾸. (실증: 16:55:58 R로 7칸 빠꾸)
                            if atk.coord_valid:
                                self._prev_atk_coord = (atk.x, atk.y)
                        else:
                            # 흰탭 없음 → 일반 이동 결정.
                            want, reason = self._decide_move(atk, fol, map_neq)
                # want 값이 바뀐 순간 로그 (1초 STAT 기다리지 않고 즉시).
                if want != self._last_want:
                    self.log.info(
                        f"[MOVE] {self._last_want}→{want} reason={reason!r} "
                        f"map_neq={map_neq} armed={self.armed} "
                        f"fsm={state.value} h_coord={self.healer_coord} "
                        f"a_coord=({atk.x},{atk.y}) a_valid={atk.coord_valid} "
                        f"h_map={self.healer_map!r} a_map={atk.map_name!r}"
                    )
                    self._last_want = want
    
                # 이동 금지 상태: DEAD/DISCONNECTED만. DEAD_RECKON도 맹목 추종 허용.
                move_blocked = state in (FsmState.DEAD, FsmState.DISCONNECTED)
                if move_blocked or not self.armed:
                    if not self.armed:
                        reason = "ARM OFF"
                    else:
                        reason = f"FSM={state.value} 이동금지"
                    if current_dir != "-":
                        if self.armed:
                            keys.release_all()
                            self.log.info(f"[KEY] release_all ({reason})")
                        current_dir = "-"
                else:
                    # 스킬 시전 직후 재hold: 같은 방향이어도 강제 재발송.
                    if self._need_rehold:
                        self._need_rehold = False
                        if want in ("L", "R", "U", "D"):
                            keys.release_all()
                            keys.hold(want)
                            self.log.info(
                                f"[KEY-REHOLD-SKILL] {want} "
                                f"(재hold reason={reason!r})"
                            )
                            current_dir = want
                            self._last_hold_ts = now_sec
                        else:
                            # want=- 상태였으면 그냥 패스 (release_all로 이미 해제됨).
                            pass
                    elif want != current_dir:
                        keys.release_all()
                        if want != "-":
                            keys.hold(want)
                            self.log.info(f"[KEY] hold {want} ({reason})")
                            self._last_hold_ts = now_sec
                        else:
                            self.log.info(f"[KEY] release_all ({reason})")
                        current_dir = want
                    elif want in ("L", "R", "U", "D"):
                        # 같은 방향 1.5초+ 홀드 중이고 힐러 좌표 1초+ 변화 없으면
                        # SendInput DOWN이 유실됐을 가능성 → release+hold 재발송.
                        # 실제 움직이는 중이면 coord 계속 변하므로 재발송 안 함.
                        hold_dur = now_sec - self._last_hold_ts
                        coord_stale = (
                            now_sec - self._h_coord_last_change
                        ) > 1.0
                        if hold_dur > 1.5 and coord_stale:
                            keys.release_all()
                            keys.hold(want)
                            self.log.info(
                                f"[KEY-REHOLD] {want} "
                                f"(hold_dur={hold_dur:.1f}s "
                                f"coord_stale h={self.healer_coord})"
                            )
                            self._last_hold_ts = now_sec
    
                # === 캘리브레이션 수집 (비침습: 관찰+로그 전용) ===
                # 유효조건: 동일 맵 + red only + 좌표 유효 프레임.
                # 기존 동작에 영향 없음. 격수 빨탭 복구 단계 선행 데이터 수집.
                same_map = (
                    bool(self.healer_map)
                    and bool(atk.map_name)
                    and self.healer_map == atk.map_name
                )
                red_px_obs = (
                    (float(det.cx), float(det.cy)) if det is not None else None
                )
                self._calibrator.maybe_sample(
                    now_sec,
                    self.healer_coord,
                    (atk.x, atk.y) if atk.coord_valid else None,
                    red_px_obs,
                    same_map=same_map,
                    red_raw=red_raw,
                    white_raw=white_raw,
                    coord_valid=atk.coord_valid,
                )
    
                # 메인 루프 full iter 시간 (loop_t0 ~ 여기까지) — t_total
                # 측정 위치 이후 블록(FSM/key/emit 등)까지 포함.
                self.t_full_iter = (time.perf_counter() - loop_t0) * 1000
                frames += 1
                pg += self.t_grab; py += self.t_yolo; po += self.t_ocr
                ptot += self.t_total; pn += 1
                if not hasattr(self, "_pfull_sum_1s"):
                    self._pfull_sum_1s = 0.0
                self._pfull_sum_1s += self.t_full_iter
                # 진짜 iter 주기 합계 (fps 역수와 일치). full 과 큰 차이가 나면
                # full 측정 이후 구간(emit/sleep)에 blocking 이 있다는 뜻.
                if not hasattr(self, "_piter_sum_1s"):
                    self._piter_sum_1s = 0.0
                self._piter_sum_1s += float(getattr(self, "t_iter_period", 0.0))
                now = time.time()
                if now - t0 >= 1.0:
                    # 매초 집계는 유지 (누산만). 10초마다 PERF 평균 출력.
                    if pn > 0:
                        fps = frames / (now - t0)
                        self.last_fps = float(fps)
                        self._perf_fps_sum += fps
                        self._perf_grab_sum += pg / pn
                        self._perf_yolo_sum += py / pn
                        self._perf_ocr_sum += po / pn
                        self._perf_total_sum += ptot / pn
                        if not hasattr(self, "_perf_full_sum"):
                            self._perf_full_sum = 0.0
                        if not hasattr(self, "_perf_iter_sum"):
                            self._perf_iter_sum = 0.0
                        _pfull_1s = getattr(self, "_pfull_sum_1s", 0.0)
                        _piter_1s = getattr(self, "_piter_sum_1s", 0.0)
                        self._perf_full_sum += _pfull_1s / pn
                        self._perf_iter_sum += _piter_1s / pn
                        self._perf_samples += 1
                    frames = 0; pg = py = po = ptot = 0.0; pn = 0
                    self._pfull_sum_1s = 0.0
                    self._piter_sum_1s = 0.0
                    t0 = now
    
                    # [PERF] 10초마다 1회 평균 출력.
                    if (
                        self._perf_samples > 0
                        and (now - self._perf_last_emit) >= 10.0
                    ):
                        n = self._perf_samples
                        # full = 메인 루프 full iter ms 평균. total 과 차이가
                        # 클수록 t_total 측정 이후 구간(FSM/키/emit/net) 에
                        # blocking 이 있다는 신호.
                        _full_avg = (getattr(self, "_perf_full_sum", 0.0) / n)
                        self.log.info(
                            f"[PERF] fps={self._perf_fps_sum/n:.1f} avg_ms "
                            f"grab={self._perf_grab_sum/n:.1f} "
                            f"yolo={self._perf_yolo_sum/n:.1f} "
                            f"ocr={self._perf_ocr_sum/n:.1f} "
                            f"total={self._perf_total_sum/n:.1f} "
                            f"full={_full_avg:.1f} "
                            f"yn={self.yolo_every_n} win={n}s"
                        )
                        self._perf_full_sum = 0.0
                        self._perf_fps_sum = 0.0
                        self._perf_grab_sum = 0.0
                        self._perf_yolo_sum = 0.0
                        self._perf_ocr_sum = 0.0
                        self._perf_total_sum = 0.0
                        self._perf_samples = 0
                        self._perf_last_emit = now
    
                    # [STAT] 핵심 상태 튜플 변화 시 이벤트 로그 + 30s heartbeat.
                    # 2026-04-21: 격수 PC 시작 시 atk 좌표가 빠르게 바뀌어
                    # stat_key 매 iter changed=True → 매 iter 40줄 짜리 log.info
                    # file I/O → fps 급락. 0.5초 당 최대 1회로 throttle.
                    stat_key = (
                        state.value, want, current_dir, self.armed,
                        map_neq,
                        'Y' if det else 'N',
                        'Y' if det_white else 'N',
                        fol.direction(), fol.exit_dir(),
                        is_numlock_on(), recv.latest() is not None, fg_ok,
                    )
                    changed = stat_key != self._last_stat_key
                    heartbeat = (now - self._stat_heartbeat_ts) >= 30.0
                    _stat_throttle_ok = (
                        (now - getattr(self, "_last_stat_log_ts", 0.0)) >= 0.5
                    )
                    if (changed and _stat_throttle_ok) or heartbeat:
                        tag = "STAT" if changed else "STAT-HB"
                        self.log.info(
                            f"[{tag}] fsm={state.value} want={want} "
                            f"hold={current_dir} armed={self.armed} "
                            f"map_neq={map_neq} reason={reason!r} "
                            f"red={'Y' if det else 'N'}(raw={red_raw}) "
                            f"red_px={(det.cx,det.cy) if det else None} "
                            f"white={'Y' if det_white else 'N'}(raw={white_raw}) "
                            f"h_coord={self.healer_coord} "
                            f"a_coord=({atk.x},{atk.y}) "
                            f"a_valid={atk.coord_valid} "
                            f"h_map={self.healer_map!r} "
                            f"a_map={atk.map_name!r} "
                            f"a_last_dir={fol.direction()!r} "
                            f"exit_dir={fol.exit_dir()!r} "
                            f"ls={fol.last_seen_in(self.healer_map)} "
                            f"known_maps={list(fol._map_last_coord.keys())} "
                            f"seq={atk.seq} "
                            f"nl={'ON' if is_numlock_on() else 'OFF'} "
                            f"udp={recv.latest() is not None} "
                            f"fg={fg_ok} "
                            f"hwnd={keys.hwnd} method={keys.method}"
                        )
                        self._last_stat_key = stat_key
                        self._stat_heartbeat_ts = now
                if last_state != state:
                    self.log.info(f"[FSM] {last_state.value if last_state else '-'} "
                                  f"→ {state.value}")
                    last_state = state
    
                # --- 쿨다운 OCR + 격수로 역송 (v5) ---
                # OCR은 별도 스레드에서 돌아감. 메인은 submit_frame(비블로킹)
                # + latest() 캐시 조회만. (과거 maybe_read 동기 호출이
                # PaddleOCR.predict 100~500ms 블로킹 → 흰탭 TAB-CONFIRM
                # 프레임 오염 유발 → 2026-04-17 수정.)
                try:
                    if self._cooldown_ocr.ready():
                        _mon = getattr(grab, "mon", {}) or {}
                        origin = (int(_mon.get("left", 0)),
                                  int(_mon.get("top", 0)))
                        # 메인 루프는 참조만 전달 → 백그라운드 스레드가 copy().
                        # 따라가기 경량: cooldown OCR predict 정지 (submit skip).
                        if not self.follow_light:
                            self._cooldown_ocr.submit_frame(frame, origin)
                        cd_read = self._cooldown_ocr.latest()
                        self._last_cooldown = cd_read
                        if not self.follow_light:
                            # 내부 타이머 anchor/검증.
                            self._timer_parlyuk.on_ocr(int(cd_read.cd_parlyuk))
                            self._timer_baekho.on_ocr(int(cd_read.cd_baekho))
                        # XP OCR 동일 frame 제출 — 경험치는 따라가기에도 유지.
                        if self._xp_ocr is not None and self._xp_ocr.ready():
                            self._xp_ocr.submit_frame(frame, origin)
                        # 버프 OCR 동일 frame 제출 (region 지정 시에만 동작).
                        # 따라가기 경량: 정지.
                        if not self.follow_light:
                            try:
                                if self._buff_ocr.ready():
                                    self._buff_ocr.submit_frame(frame, origin)
                            except Exception:
                                pass
                        # HP/MP 픽셀 리더 — 따라가기 경량: 정지 (자힐/공증 미사용).
                        if not self.follow_light:
                            try:
                                self._hpmp.read(frame, origin)
                            except Exception:
                                pass
                        # 1Hz OCR 결과 진단 로그 (ts 변화 = 새 OCR 수행).
                        if (cd_read.ts > 0
                                and cd_read.ts > self._cd_last_log_ts):
                            self._cd_last_log_ts = cd_read.ts
                            raw_snip = (cd_read.raw_text or "").replace(
                                "\n", " | "
                            )[:80]
                            self.log.info(
                                f"[CD-OCR] p={cd_read.cd_parlyuk} "
                                f"b={cd_read.cd_baekho} "
                                f"nick={cd_read.nickname!r} "
                                f"raw={raw_snip!r} "
                                f"diag={self._cooldown_ocr.last_diag()}"
                            )
                        elif (cd_read.ts == 0
                                and (now_sec - self._cd_last_err_ts) >= 3.0):
                            # OCR 결과 아직 없음 → 스레드 상태 진단.
                            self._cd_last_err_ts = now_sec
                            self.log.info(
                                f"[CD-OCR] pending "
                                f"init={self._cooldown_ocr.init_note()!r} "
                                f"diag={self._cooldown_ocr.last_diag()!r} "
                                f"region={self._cooldown_ocr.region()} "
                                f"nick_region={self._cooldown_ocr.nick_region()} "
                                f"origin={origin} "
                                f"frame={frame.shape[1]}x{frame.shape[0]}"
                            )
                        # 격수 IP는 recv의 last_src_addr로 자동 파악.
                        src = recv.last_src_addr()
                        if src is None:
                            if (now_sec - self._no_state_warn_ts) >= 10.0:
                                self._no_state_warn_ts = now_sec
                                self.log.info(
                                    f"[NO-STATE] 격수 State 미수신 "
                                    f"bind={cfg.net.bind_host}:{cfg.net.port} "
                                    f"→ 확인사항: (1) 격수 PC 기동 여부, "
                                    f"(2) 격수 peers 목록에 힐러 IP 포함 여부, "
                                    f"(3) 포트/방화벽. "
                                    f"last_seq={self._last_seq}"
                                )
                        elif ((now_sec - self._cd_last_send)
                                >= self._cd_send_interval):
                            try:
                                from ..net.protocol import (
                                    CooldownReport, now_ms,
                                )
                                # 내부 타이머 기반 남은 초 (OCR은 anchor용).
                                p_rem = self._timer_parlyuk.remaining()
                                b_rem = self._timer_baekho.remaining()
                                xph = 0
                                if self._xp_ocr is not None:
                                    xph = int(self._xp_ocr.xp_per_hour())
                                # 파력무참 버프 지속시간 OCR 결과 — 영역 미지정 시 -1.
                                buff_sec = -1
                                try:
                                    if self._buff_ocr.ready():
                                        br = self._buff_ocr.latest()
                                        buff_sec = int(
                                            (getattr(br, "skills", {}) or {})
                                            .get("파력무참", -1)
                                        )
                                except Exception:
                                    buff_sec = -1
                                # 힐러 자신의 HP/MP OCR — 격수 전용 오버레이용.
                                # 영역 미지정/미관측 시 -1 / 0. 방어: 속성 부재도 안전.
                                h_hp_pct = -1; h_mp_pct = -1
                                h_hp_cur = -1; h_mp_cur = -1
                                h_hp_max = 0; h_mp_max = 0
                                try:
                                    hm_ = self._hpmp.latest()
                                    h_hp_pct = int(getattr(hm_, "hp", -1))
                                    h_mp_pct = int(getattr(hm_, "mp", -1))
                                    h_hp_cur = int(getattr(hm_, "hp_cur", -1))
                                    h_mp_cur = int(getattr(hm_, "mp_cur", -1))
                                    h_hp_max = int(getattr(hm_, "hp_max", 0))
                                    h_mp_max = int(getattr(hm_, "mp_max", 0))
                                except Exception:
                                    pass
                                rep = CooldownReport(
                                    src_idx=int(getattr(
                                        cfg.net, "healer_idx", 0
                                    )),
                                    cd_parlyuk=int(p_rem),
                                    cd_baekho=int(b_rem),
                                    ts_ms=now_ms(),
                                    armed=bool(self.armed),
                                    nickname=str(cd_read.nickname or ""),
                                    xp_per_hour=xph,
                                    buff_parlyuk_sec=int(buff_sec),
                                    hp_pct=h_hp_pct,
                                    mp_pct=h_mp_pct,
                                    hp_cur=h_hp_cur,
                                    mp_cur=h_mp_cur,
                                    hp_max=h_hp_max,
                                    mp_max=h_mp_max,
                                    self_heal_hp_thr=int(
                                        getattr(self, "self_heal_hp_thr", -1)
                                    ),
                                    gyoungryeok_mp_thr=int(
                                        getattr(self, "gyoungryeok_mp_thr", -1)
                                    ),
                                    healer_map=str(
                                        getattr(self, "healer_map", "") or ""
                                    ),
                                    healer_x=(
                                        int(self.healer_coord[0])
                                        if self.healer_coord is not None else 0
                                    ),
                                    healer_y=(
                                        int(self.healer_coord[1])
                                        if self.healer_coord is not None else 0
                                    ),
                                    coord_valid=(self.healer_coord is not None),
                                    state_text=self._compute_state_text(),
                                )
                                atk_port = int(getattr(
                                    cfg.net, "attacker_recv_port", 45455
                                ))
                                ok = self._udp_out.send_to(
                                    src[0], atk_port, rep.to_bytes()
                                )
                                self._cd_last_send = now_sec
                                if ok != self._cd_last_send_ok:
                                    self._cd_last_send_ok = ok
                                    self.log.info(
                                        f"[CD-SEND] to={src[0]}:{atk_port} "
                                        f"ok={ok} idx={rep.src_idx} "
                                        f"p={rep.cd_parlyuk} "
                                        f"b={rep.cd_baekho} "
                                        f"armed={rep.armed} "
                                        f"nick={rep.nickname!r}"
                                    )
                            except Exception as _e:
                                if (now_sec - self._cd_last_err_ts) >= 5.0:
                                    self._cd_last_err_ts = now_sec
                                    self.log.info(
                                        f"[CD-SEND] err "
                                        f"{type(_e).__name__}: {_e}"
                                    )
                except Exception as _e:
                    if (now_sec - self._cd_last_err_ts) >= 5.0:
                        self._cd_last_err_ts = now_sec
                        self.log.info(
                            f"[CD-OCR] err {type(_e).__name__}: {_e}"
                        )
    
                # 저사양: frame_ready emit Hz 제한 (GUI paint 비용 감소).
                # 2026-04-21: 격수 State 수신 시 main loop 가 fps 30→9 로 급락.
                # 원인: frame_ready.emit 의 payload 에 원본 frame(1920x1080x3
                # ≈6MB ndarray) 가 들어가 UI thread marshalling 이 메인 루프
                # 대기시킴. 강제 throttle 10Hz 하한 + frame 제거.
                _emit_now = True
                try:
                    hz_lim = float(self.preview_hz_limit or 0.0)
                    # 미설정 시 기본 30Hz (사용자 지시 2026-04-22).
                    if hz_lim <= 0.1:
                        hz_lim = 30.0
                    _min_dt = 1.0 / hz_lim
                    _t_now = time.monotonic()
                    if (_t_now - self._last_preview_emit_ts) < _min_dt:
                        _emit_now = False
                    else:
                        self._last_preview_emit_ts = _t_now
                except Exception:
                    _emit_now = True
                if not _emit_now:
                    time.sleep(0.001)
                    continue

                self.frame_ready.emit({
                    # frame(원본 1920x1080) 제거 — preview_frame(crop) 만으로 충분.
                    "preview_frame": crop_frame,
                    "preview_offset": (gx_off, gy_off),
                    "det": det,
                    "all_dets": all_dets,
                    "state": state,
                    "hold": current_dir if self.armed else f"(off:{current_dir})",
                    "want": want,
                    "reason": reason,
                    "armed": self.armed,
                    "seq": atk.seq,
                    "udp": recv.latest() is not None,
                    "fps": fps,
                    "W": W, "H": H,
                    "healer_coord": self.healer_coord,
                    "healer_map": self.healer_map,
                    "atk_coord": (atk.x, atk.y) if atk.coord_valid else None,
                    "atk_map": atk.map_name,
                    "numlock": is_numlock_on(),
                    "hwnd_fg": _is_fg_hwnd(keys.hwnd),
                    "perf": (self.t_grab, self.t_yolo, self.t_ocr,
                             self.t_total),
                    "cooldown": self._last_cooldown,
                })
                time.sleep(0.005)
        finally:
            # 2026-04-22: async 3종 stop 누락 → 재시작마다 좀비 스레드 누적
            # (py-spy 로 async-grabber/yolo/ocr 각 2개씩 확인). GIL 경합 폭증.
            try:
                grab.stop()
            except Exception:
                pass
            try:
                yolo_async.stop()
            except Exception:
                pass
            try:
                ocr_async.stop()
            except Exception:
                pass
            try:
                sched.stop()
                sched.join(timeout=1.0)
            except Exception:
                pass
            try:
                cycler.stop()
                cycler.join(timeout=1.0)
            except Exception:
                pass
            try:
                keys.release_all()
                recv.stop()
            except Exception:
                pass
            # 2026-04-22: hpmp_ocr stop 누락 → 재시작마다 좀비 스레드 누적
            # (py-spy 로 hpmp_ocr 2개 동시에 CPU 100% 점유 확인).
            try:
                if self._hpmp is not None:
                    self._hpmp.stop()
            except Exception:
                pass
            try:
                self._cooldown_ocr.stop()
            except Exception:
                pass
            try:
                self._buff_ocr.stop()
            except Exception:
                pass
            try:
                if self._xp_ocr is not None:
                    self._xp_ocr.stop()
            except Exception:
                pass
            try:
                mw = getattr(self, "_map_worker", None)
                if mw is not None:
                    mw.stop()
            except Exception:
                pass
            self.log.info("=== worker stop ===")
            self.log_msg.emit("워커 정지")
            self.stopped.emit()


    def set_skill_enabled(self, name: str, on: bool):
        """런타임 스킬 ON/OFF. 초기화 전/후 모두 안전."""
        self.skill_enabled[name] = on
        if self._skills is None:
            return
        for s in self._skills:
            if s.name == name:
                s.enabled = on

    def set_primary_vk(self, idx: int, vk: int):
        """주력 힐 슬롯 VK 변경. idx=0/1/2 = 봉황/신령/혼마술."""
        if idx < 0 or idx >= len(self.primary_vks):
            return
        self.primary_vks[idx] = vk
        if self._cycler is not None:
            self._cycler.set_slots(list(self.primary_vks))

    def set_cycle_vks(self, vks: list):
        """NumLock 싸이클 슬롯 전체 교체. (선택된 기원 + 혼마술 체크시 추가)"""
        self.primary_vks = list(vks)
        if self._cycler is not None:
            self._cycler.set_slots(list(self.primary_vks))

    def set_skill_vk(self, name: str, vk: int):
        """조건부 스킬 VK 변경.

        "부활" → "자가부활"/"격수부활" 둘 다 전파 (공용 VK).
        (2026-06-07 자힐 스킬 제거로 "메인힐"→"자힐" 전파 삭제.)
        """
        self.skill_vks[name] = vk
        if self._skills is None:
            return
        # 공용 VK 매핑: 이름 → 실제 SkillSpec 이름들.
        _alias = {
            "부활": ("자가부활", "격수부활"),
        }
        target_names = _alias.get(name, (name,))
        for s in self._skills:
            if s.name in target_names:
                s.vk = vk

    def set_parlyuk_offset(self, sec: float):
        """런타임 파력무참 오프셋. 이미 첫 시전 지난 뒤엔 의미 없음."""
        self.parlyuk_offset = sec
        if self._skills is None:
            return
        for s in self._skills:
            if s.name == "파력무참":
                s.offset_sec = sec

    def set_parlyuk_maps(self, text) -> None:
        """파력무참 시전 굴 설정 (2026-06-10). '3,5' → {3,5}.

        빈 문자열/None → set() (전체 굴 허용, 기존 동작). 맵명 끝 '(N)' 의 N 과
        대조해 시전 게이트. predicate(_parlyuk_map_ok)가 매 tick 최신값 조회.
        """
        s = set()
        for tok in str(text or "").replace(" ", "").split(","):
            if tok.isdigit():
                s.add(int(tok))
        self._parlyuk_maps = s
        self.log.info(f"[PARLYUK-MAPS] 시전 굴 설정 → {sorted(s) or '전체'}")

    def _decide_move(self, atk, fol, map_neq: bool) -> tuple:
        """B안 래퍼: 원 decision 결과에 STUCK 감지/언스턱 오버라이드 적용."""
        want, reason = self._decide_move_raw(atk, fol, map_neq)
        return self._apply_stuck_filter(want, reason, atk, fol, map_neq)


    def _blacklist_add(self, map_name: str, coord, direction: str) -> None:
        """STUCK-RESET 발생 시 호출. v5.1: 첫 발생은 용서(도사끼리 막힘 대응).
        10초 내 재발한 경우만 blacklist 등록 + exponential backoff.
        """
        if not map_name or coord is None or direction not in ("L", "R", "U", "D"):
            return
        now = time.time()
        cx, cy = coord[0] // 2, coord[1] // 2
        key = (map_name, cx, cy, direction)
        # 주변 ±1 블록 이력 조회 (정체 후 약간 움직여서 다른 블록에서 재stuck
        # 케이스도 재발로 인정). 도사끼리 막힘은 보통 한 지점에서 풀림 → 진행
        # 후 다시 stuck 아님.
        prev_reset = 0.0
        for (m, bx, by, d), ts in self._reset_history.items():
            if m == map_name and d == direction \
                    and abs(bx - cx) <= 1 and abs(by - cy) <= 1:
                prev_reset = max(prev_reset, ts)
        # 오래된 이력 정리 (메모리 bound).
        if len(self._reset_history) > 100:
            cutoff = now - self._bl_forgive_window
            self._reset_history = {
                k: v for k, v in self._reset_history.items() if v >= cutoff
            }
        self._reset_history[key] = now
        if now - prev_reset > self._bl_forgive_window:
            self.log.info(
                f"[BL-SKIP] first offense — map={map_name!r} "
                f"cell=({cx},{cy}) dir={direction} (도사 충돌 가능성)"
            )
            return
        # 10초 내 재발 → 진짜 벽으로 판정. blacklist 등록 + exponential TTL.
        prev_bl = self._stuck_blacklist.get(key)
        hit = (prev_bl[1] + 1) if prev_bl else 1
        ttl = min(60.0, self._bl_ttl_sec * (2 ** (hit - 1)))
        self._stuck_blacklist[key] = (now + ttl, hit)
        self.log.info(
            f"[BL-ADD] map={map_name!r} cell=({cx},{cy}) dir={direction} "
            f"hit={hit} ttl={ttl:.0f}s (재발 확인)"
        )

    def _blacklist_remove_at(self, map_name: str, coord, direction: str) -> None:
        """해당 방향으로 진행 확인 → blacklist 즉시 해제.
        도사 충돌이었으면 상대 움직이는 순간 다시 길 열림 → 오탐 제거.
        """
        if not map_name or coord is None or direction not in ("L", "R", "U", "D"):
            return
        cx, cy = coord[0] // 2, coord[1] // 2
        for (m, bx, by, d) in list(self._stuck_blacklist.keys()):
            if m == map_name and d == direction \
                    and abs(bx - cx) <= 1 and abs(by - cy) <= 1:
                del self._stuck_blacklist[(m, bx, by, d)]
                self.log.info(
                    f"[BL-REMOVE] 진행 감지 → 차단 해제 "
                    f"map={map_name!r} cell=({bx},{by}) dir={direction}"
                )

    def _blacklist_check(self, map_name: str, coord, direction: str) -> bool:
        """해당 (맵, 현 좌표 ±2칸, 방향) 조합이 차단 중인지. lazy cleanup 포함."""
        if not map_name or coord is None or direction not in ("L", "R", "U", "D"):
            return False
        now = time.time()
        cx, cy = coord[0] // 2, coord[1] // 2
        expired = [k for k, v in self._stuck_blacklist.items() if v[0] <= now]
        for k in expired:
            del self._stuck_blacklist[k]
        for (m, bx, by, d), (exp, _hit) in self._stuck_blacklist.items():
            if m == map_name and d == direction \
                    and abs(bx - cx) <= 1 and abs(by - cy) <= 1:
                return True
        return False

    def _apply_stuck_filter(self, want, reason, atk, fol, map_neq: bool) -> tuple:
        """진행률 기반 STUCK 감지 + 직교축 전용 언스턱 (v3).

        축 매칭 정책: X축(L/R) 막히면 Y축(U/D)로만, Y축 막히면 X축으로만 풀이.
        REV(반대 방향) 폐기 — 뒤로 돌아가면 몹 어그로 그대로 끌고 길 잃음.
        흰탭 감지/맵 전환 중엔 개입 금지 (기존 WHITETAB·trail 로직 우선).
        """
        now = time.time()
        h = self.healer_coord
        # 2026-04-24 map_neq 조건 제거: 맵 전환 중에도 벽 박힘 감지 활성.
        # 이전 로직은 map_neq=True면 STUCK 필터 스킵 → 힐러가 trail 따라
        # U 키 누르다 벽 막히면 30초 무한 대기 (실증: 힐러2 15:03:36~04:06
        # h=(2,10) U 30초 박힘, 맵도 못 따라감).
        # FORCE-EXIT 기간엔 decide_move_raw 자체가 exit_dir 우선 반환하므로
        # 여기까지 오지 않음. 안전.
        if h is None or want not in ("L", "R", "U", "D") \
                or getattr(self, "_whitetab_confirm", 0) >= 1:
            self._run_want = None
            self._run_start_ts = 0.0
            self._run_start_pos = None
            return want, reason
        hx, hy = h
        if self._run_want != want or self._run_start_pos is None:
            self._run_want = want
            self._run_start_ts = now
            self._run_start_pos = (hx, hy)
            return want, reason
        bx, by = self._run_start_pos
        # 2026-04-24: STUCK 진행 판정 맨해튼 기반으로 확장.
        # 기존은 주축(x 또는 y)만 체크 → 힐러가 ORTHO/RETREAT로 부축 이동 중
        # 이거나 target 대각선 추적 중이면 실제 이동하고 있어도 주축 progress=0
        # 로 STUCK 누적 → 엉뚱한 BL-ADD.
        # 어느 축이든 baseline 대비 2칸 이상 이동했으면 "움직이고 있음" 으로
        # 판정 → stuck 해제 + baseline 갱신.
        manhattan_delta = abs(hx - bx) + abs(hy - by)
        if manhattan_delta >= 2:
            self._blacklist_remove_at(self.healer_map, (hx, hy), want)
            self._run_start_ts = now
            self._run_start_pos = (hx, hy)
            return want, reason
        # 주축 진행 체크 (1칸 이동도 가능한 경우 기존 로직 유지)
        if want in ("L", "R"):
            delta = hx - bx
            expected = 1 if want == "R" else -1
        else:
            delta = hy - by
            expected = 1 if want == "D" else -1
        progress = delta * expected
        if progress > 0:
            self._blacklist_remove_at(self.healer_map, (hx, hy), want)
            self._run_start_ts = now
            self._run_start_pos = (hx, hy)
            return want, reason
        dur = now - self._run_start_ts
        if dur < 0.8:
            return want, reason
        # 직교축 1차/2차 결정: X축 막힘 → Y축, Y축 막힘 → X축
        a_valid = bool(atk.coord_valid)
        if want in ("L", "R"):
            # Y축으로 풀이. 1차 = 격수 y 방향, 2차 = 반대
            if a_valid and atk.y != hy:
                ortho1 = "D" if atk.y > hy else "U"
            else:
                ortho1 = "U"  # 격수와 같은 y거나 격수 무효 → 기본 U
            ortho2 = "D" if ortho1 == "U" else "U"
        else:  # U/D
            # X축으로 풀이. 1차 = 격수 x 방향, 2차 = 반대
            if a_valid and atk.x != hx:
                ortho1 = "R" if atk.x > hx else "L"
            else:
                ortho1 = "R"
            ortho2 = "L" if ortho1 == "R" else "R"
        if now - self._stuck_last_log >= 0.5:
            self._stuck_last_log = now
            self.log.warning(
                f"[STUCK] dur={dur:.1f}s h={h} blocked={want} "
                f"base={self._run_start_pos} "
                f"atk=({atk.x},{atk.y}) a_valid={a_valid} "
                f"ortho1={ortho1} ortho2={ortho2}"
            )
        if dur < 2.0:
            return ortho1, (
                f"STUCK-ORTHO1 dur={dur:.1f}s h={h} "
                f"blocked={want} try={ortho1}"
            )
        if dur < 3.5:
            return ortho2, (
                f"STUCK-ORTHO2 dur={dur:.1f}s h={h} "
                f"blocked={want} try={ortho2}"
            )
        # 3.5s 초과 → release + 상태 리셋 + blacklist 등록.
        # 다음 B3 결정 시 해당 방향 회피 → 같은 벽 무한 재시도 방지.
        self._blacklist_add(self.healer_map, h, want)
        self._run_want = None
        self._run_start_ts = 0.0
        self._run_start_pos = None
        return "-", (
            f"STUCK-RESET dur={dur:.1f}s h={h} blocked={want} "
            f"→ BL-ADD map={self.healer_map!r} ttl={self._bl_ttl_sec:.0f}s"
        )

    def _decide_move_raw(self, atk, fol, map_neq: bool) -> tuple:
        """(want, reason) 반환. 사용자 피드백 3: 무조건 격수 찾아가게.

        우선순위:
        1. 맵 불일치(map_neq):
           - 힐러 좌표 O + last_seen_in(healer_map) O → 그 좌표로 이동.
           - 아니면 exit_dir로 맹목 이동.
        2. 맵 일치 + 힐러 좌표 O + 격수 좌표 O → 기존 좌표 Δ 로직.
        3. 맵 일치 + 힐러 좌표 없음 + 격수 좌표 O → 격수 방향(last_dir)으로 맹목.
        4. 격수 좌표 무효 → 격수 마지막 방향(fol.direction())으로 맹목 (dead reckon 보조).
        """
        tol = self.coord_tol
        h = self.healer_coord
        a_valid = bool(atk.coord_valid)

        # 전역 trail 태그 전이(격수 맵 전환 확정) 직후 N초 — exit_dir 강제 홀드.
        # 맵 이름 OCR 지연 케이스에도 격수가 새 맵 태그로 한 프레임이라도
        # 좌표 송신하면 즉시 발동 → 힐러 이동 강제.
        # 2026-06-10 (수정 B): map_neq=False(격수와 같은 맵 도달)면 FORCE-EXIT
        # 즉시 해제. 안 하면 잔여 시간 동안 도사가 격수를 지나쳐 다음 포탈로 또
        # 넘어감(혼자 포탈 #3, 뒤로 복귀 #4). 같은 맵이면 격수 좌표만 추종.
        if hasattr(fol, "force_exit_active") and fol.force_exit_active():
            if not map_neq:
                if hasattr(fol, "cancel_force_exit"):
                    fol.cancel_force_exit()
                self._portal_enter_logged = False  # 새 맵 도달 → 게이트 리셋.
                self.log.info(
                    "[FORCE-EXIT-CANCEL] 격수와 같은 맵 도달 → 해제, 격수 추종"
                )
            elif fol._tab_confirm_active:
                # 2026-06-11 사용자 우선요구: 본인 빨탭 고정(TAB-CONFIRM done)
                # 전엔 포탈 통과 보류. 빨탭 미확정 채 넘어가면 새맵서 본인 힐 →
                # 손으로 넘겨주는 시간 로스가 더 큼. 빨탭 확정 후 exit.
                return "-", "EXIT-HOLD: 본인빨탭 고정 대기(TAB-CONFIRM 중)"
            else:
                # 2026-06-11 사용자 우선요구: 맵 이동 직전 본인 빨탭(red & !white)
                # 확정을 무조건 확인하고 진입. 빨탭 없이 넘어가면 새맵서 본인
                # 타겟/자힐 로스 → 손으로 넘겨야. follow_only(쩔)는 빨탭 자체를
                # 안 쓰므로 게이트 면제(trail/좌표 추종으로 진입).
                if not self.follow_only:
                    red_ok = self._cur_red_raw and not self._cur_white_raw
                    if not red_ok:
                        # 진입 보류 — 0.5s throttle 로그로 대기 사실 기록.
                        _nw = time.time()
                        if _nw - getattr(self, "_portal_wait_log_ts", 0.0) > 0.5:
                            self._portal_wait_log_ts = _nw
                            self.log.info(
                                f"[PORTAL-WAIT] 맵={self.healer_map!r} 빨탭 미확정 "
                                f"→ 진입 보류 (red={self._cur_red_raw} "
                                f"white={self._cur_white_raw} "
                                f"red_px=({self._cur_red_cx:.0f},"
                                f"{self._cur_red_cy:.0f}))"
                            )
                        return "-", (
                            "PORTAL-WAIT 빨탭 미확정 → 진입 보류 "
                            f"(red={self._cur_red_raw} white={self._cur_white_raw})"
                        )
                    if not self._portal_enter_logged:
                        self.log.info(
                            f"[PORTAL-ENTER] 맵={self.healer_map!r} 본인 빨탭 확정 "
                            f"→ 다음맵 진입 "
                            f"(red={self._cur_red_raw} white={self._cur_white_raw} "
                            f"red_px=({self._cur_red_cx:.0f},{self._cur_red_cy:.0f}) "
                            f"h={h} exit={getattr(fol, '_exit_coord', None)})"
                        )
                        self._portal_enter_logged = True
                # 2026-06-11 사용자 명시: 포탈좌표 직행 폐기 → 격수 trail 순서대로.
                # 직행(_exit_coord)은 격수가 우회한 벽에 박힘 (시뻑구(7) 출구
                # (0,6) 직행 L 벽 STUCK 62회). 격수가 밟은 trail = 검증된 통로 →
                # 그대로 따라가면 벽 회피. trail 끝(포탈 직전) 도달하면 exit_dir 밀기.
                if h is not None:
                    wp = fol.next_waypoint(self.healer_map, h, tol=tol,
                                           exit_dash=False)
                    # next_waypoint 반환 형식 = ((wx,wy), dir).
                    if wp is not None and wp[0] is not None:
                        (wx, wy), _wd = wp
                        if abs(wx - h[0]) + abs(wy - h[1]) > tol:
                            dx, dy = wx - h[0], wy - h[1]
                            w = (("R" if dx > 0 else "L")
                                 if abs(dx) >= abs(dy)
                                 else ("D" if dy > 0 else "U"))
                            remain = fol.force_exit_remaining()
                            return w, (
                                f"FORCE-EXIT-TRAIL wp=({wx},{wy}) h={h} "
                                f"remain={remain:.2f}s"
                            )
                # trail 끝 도달 → exit_dir 밀어 맵 전환.
                exit_d = fol.exit_dir()
                if exit_d in ("L", "R", "U", "D"):
                    remain = fol.force_exit_remaining()
                    return exit_d, (
                        f"FORCE-EXIT exit_dir={exit_d!r} "
                        f"remain={remain:.2f}s (global trail map transition)"
                    )

        # F1 수동 예고: 격수가 포털 통과 임박 신호 송신 중.
        # map_neq=False인데 힐러-격수 좌표가 한 맵 범위를 초과하면 스테일 좌표 오발사 위험
        # (힐러 맵 OCR이 새 맵으로 순간 튄 경우) → STAY로 차단.
        # map_neq=True인 경우엔 기존 B1/B2 trail follow 로직이 정상 작동하므로 통과.
        if getattr(atk, "map_change_pending", False) and not map_neq \
                and h is not None and a_valid:
            d_ha = abs(h[0] - atk.x) + abs(h[1] - atk.y)
            if d_ha > 30:
                return "-", (
                    f"F1-PEND stale d={d_ha} h={h} a=({atk.x},{atk.y}) STAY"
                )

        # B1/B2: 맵 불일치 → 격수가 지나간 트레일 따라 이동.
        # 정책(피드백 feedback_route_trail.md): 격수 경로 전체를 순서대로 밟고,
        # 트레일 끝점 도달하면 exit_dir로 밀어 맵 전환 유도.
        if map_neq:
            # 격수 맵 전환 → 정지 latch 해제 (수정 2: 새 맵에선 추종 재개).
            self._follow_parked = False
            # 2026-04-22 trail_tol=1 (각 wp ±1 도달 허용).
            # 2026-06-11 사용자 명시: exit_dash=True(지름길/직행) 폐기 → False.
            # 격수가 벽 피해 간 trail 순서대로 밟아야 (7) 등 벽막힘 회피. 지름길로
            # 직행하면 격수가 우회한 벽에 박혀 STUCK 헤맴(시뻑구(7) 출구 (0,6)
            # 직행 L 벽 STUCK 62회). 격수 경로 = 이미 검증된 통로.
            trail_tol = 1
            wp = fol.next_waypoint(self.healer_map, h, tol=trail_tol,
                                   exit_dash=False)
            # 진단 로그 — wp 반환값 변경시 or 0.5초 스로틀.
            diag = fol.wp_diag()
            if diag is not None:
                now_diag = time.time()
                last_diag = getattr(self, "_last_trail_diag", None)
                key = (diag.get("map"), diag.get("cur"), diag.get("wp"),
                       diag.get("reason"))
                if (last_diag != key or
                        now_diag - getattr(self, "_last_trail_diag_ts", 0.0) >= 0.5):
                    self._last_trail_diag = key
                    self._last_trail_diag_ts = now_diag
                    self.log.info(
                        f"[TRAIL-DIAG] map={diag.get('map')!r} "
                        f"len={diag.get('len')} cur={diag.get('cur')} "
                        f"last_idx={diag.get('last_idx')} wp={diag.get('wp')} "
                        f"h={diag.get('h')} d={diag.get('d')} "
                        f"tol={trail_tol} "
                        f"reason={diag.get('reason')!r} "
                        f"tail={diag.get('tail')}"
                    )
            if wp is not None:
                (wx, wy), ld = wp
                if h is not None:
                    hx, hy = h
                    dx, dy = wx - hx, wy - hy
                    # 도달 감지는 next_waypoint 내부(best_i 진행)로만 처리.
                    # 여기서 '-' 반환하면 트레일 끝점이 아닌 중간 wp에서 정지 버그.
                    if abs(dx) == 0 and abs(dy) == 0:
                        # 완전 동일 좌표 — next_waypoint 다음 호출에 best_i 진행.
                        w = ld if ld in ("L", "R", "U", "D") else fol.exit_dir()
                        w = w if w in ("L", "R", "U", "D") else "-"
                        return w, f"B1a:TRAIL wp{(wx,wy)}동일 dir={w}"
                    if abs(dx) >= abs(dy):
                        w = "R" if dx > 0 else "L"
                    else:
                        w = "D" if dy > 0 else "U"
                    return w, f"B1:TRAIL→{(wx,wy)} d=({dx},{dy})"
                # 힐러 좌표 없음 — 웨이포인트 방향으로 맹목 이동 불가, exit_dir로.
                ed = fol.exit_dir()
                w = ed if ed in ("L", "R", "U", "D") else "-"
                return w, f"B1b:TRAIL h=None exit_dir={ed!r}"
            # 트레일 끝점 도달했거나 트레일 없음 — exit_dir로 맵 전환 유도.
            # 2026-06-11 사용자 우선요구: 본인 빨탭 고정(TAB-CONFIRM done) 전엔
            # 포탈 통과(exit_dir 밀기) 보류. 빨탭 미확정 채 넘어가 본인 힐하면
            # 손으로 넘겨주는 시간 로스가 더 큼. 빨탭 확정 후 넘어가기 우선.
            if fol._tab_confirm_active:
                return "-", "EXIT-HOLD: trail_end 본인빨탭 고정 대기"
            # G안: exit_dir='-'일 때 폴백 단계 추가 — 멈춤(trail_end 정지) 방지.
            #   1) fol.exit_dir() — 이전 맵 이탈 방향
            #   2) fol.direction() — 격수 최근 이동 방향
            #   3) 격수 last_seen_in 격수맵 방향
            #   4) 힐러→격수 last_seen_in 힐러맵 좌표 델타
            ed = fol.exit_dir()
            # 2026-04-22 롤백: 어제(04-21 밤) 추가된 past=STAY/past_unstick 블록 제거.
            # 힐러가 trail_end를 exit_dir 방향으로 1칸 지나쳐 past=True 판정되면
            # STAY 2초 → unstick R → B1 되돌림 L → 다시 past → 무한 핑퐁으로
            # 포탈 진입 실패 (2026-04-22 16:37 제2선비족입구 재현).
            # end_reached 시엔 아래 기본 분기(exit_dir 계속 밀기)로 통과시킴.
            src = "exit_dir"
            w = ed if ed in ("L", "R", "U", "D") else None
            if w is None:
                fd = fol.direction()
                if fd in ("L", "R", "U", "D"):
                    w, src = fd, "fol.direction"
            if w is None:
                # 격수 맵에서 격수 최근 방향.
                a_map = getattr(atk, "map_name", "") or ""
                ls_a = fol.last_seen_in(a_map)
                if ls_a is not None and ls_a[1] in ("L", "R", "U", "D"):
                    w, src = ls_a[1], f"ls_in({a_map})"
            if w is None and h is not None:
                # 힐러맵에서 격수가 마지막 본 좌표로 방향.
                ls_h = fol.last_seen_in(self.healer_map)
                if ls_h is not None:
                    (lx, ly), _ = ls_h
                    hx, hy = h
                    dx, dy = lx - hx, ly - hy
                    if abs(dx) >= abs(dy) and dx != 0:
                        w, src = ("R" if dx > 0 else "L"), "ls_h_delta"
                    elif dy != 0:
                        w, src = ("D" if dy > 0 else "U"), "ls_h_delta"
            if w is None:
                w = "-"
                src = "none"
            return w, (
                f"B2:MAPNEQ exit_dir={ed!r} h={h} trail_end "
                f"fallback={src} dir={w}"
            )

        # B3: 맵 일치 + 양쪽 좌표 OK
        # 2026-04-23 맵 전환 JUMP GATE: 격수가 실제로 맵 넘었는데 맵 이름
        # OCR이 1~2초 늦게 올라오는 구간 있음 → 그 사이 "같은 맵 + 좌표 30칸
        # 점프" 상태 → B3가 엉뚱한 target 만들어 힐러를 반대편으로 빠꾸시킴.
        # 격수 좌표가 이전 프레임 대비 8칸 이상 점프하면 "맵 전환 중" 판정 →
        # exit_dir 있으면 거기로 강제, 없으면 STAY. 2초 hold 적용.
        if h is not None and a_valid:
            now_jg = time.time()
            prev_a = getattr(self, "_prev_atk_coord", None)
            if prev_a is not None:
                jump = abs(atk.x - prev_a[0]) + abs(atk.y - prev_a[1])
                # 2026-04-23 MAP-JUMP는 "같은 맵 내 좌표 급변"이 아니라 "맵
                # 전환"을 잡는 것 → 맵 이름 같고 좌표만 많이 변한 건 ATK-WARP
                # 또는 자힐/자가부활 중 격수가 열심히 이동한 케이스. MAP-JUMP
                # 오탐 방지.
                _same_map = (
                    bool(self.healer_map) and bool(atk.map_name)
                    and self.healer_map == atk.map_name
                )
                if jump >= 8 and not _same_map:
                    # exit_dir 추론: 격수 이전 좌표의 맵 경계 위치 → 포탈 방향.
                    # (38,27) → R, (2,15) → L, (15,35) → D, (15,3) → U
                    # local fol.exit_dir()는 힐러 기준이라 부정확 → 격수 점프
                    # 직전 좌표 기반이 더 신뢰도 높음. 빠꾸 방지.
                    px, py = prev_a
                    inferred_ed = "-"
                    if px >= 30:
                        inferred_ed = "R"
                    elif px <= 5:
                        inferred_ed = "L"
                    elif py >= 30:
                        inferred_ed = "D"
                    elif py <= 5:
                        inferred_ed = "U"
                    self._map_jump_hold_until = now_jg + 2.0
                    self._map_jump_inferred_dir = inferred_ed
                    # 자힐 가드 및 defer_tab_fn 판정용 timestamp 갱신.
                    # OCR-A/OCR-H 업데이트보다 먼저 감지되므로 즉시 기록.
                    self._last_map_change_ts = max(
                        self._last_map_change_ts, now_jg
                    )
                    self.log.warning(
                        f"[MAP-JUMP] 격수 좌표 {prev_a}→({atk.x},{atk.y}) "
                        f"d={jump} inferred_exit={inferred_ed!r} 2s hold."
                    )
            self._prev_atk_coord = (atk.x, atk.y)
            if now_jg < getattr(self, "_map_jump_hold_until", 0.0):
                # 우선순위: 격수 경계 추론 > fol.exit_dir() > STAY
                ed = getattr(self, "_map_jump_inferred_dir", "-")
                src = "inferred"
                if ed not in ("L", "R", "U", "D"):
                    ed = fol.exit_dir() if hasattr(fol, "exit_dir") else "-"
                    src = "fol"
                if ed in ("L", "R", "U", "D"):
                    return ed, (
                        f"MAP-JUMP-HOLD exit_dir={ed!r} src={src} "
                        f"remain={(self._map_jump_hold_until-now_jg):.2f}s"
                    )
                return "-", (
                    f"MAP-JUMP-HOLD STAY "
                    f"remain={(self._map_jump_hold_until-now_jg):.2f}s"
                )
        # 2026-04-23 추종 거리 정책화: atk_dir 복제 대신 "격수 뒤 N칸" 가상
        # 타겟으로 이동. atk_dir 복제는 힐러가 격수와 동일/앞 좌표에서도 같은
        # 방향으로 전진해 앞지름 → 몹 어그로 끌기 (힐러1.txt 10:29:05 재현).
        # 타겟 방식은 격수 진행 방향 반대로 OFFSET 만큼 떨어진 지점을 추적 →
        # 절대 앞지르지 않고 격수 정지 시 자연스럽게 뒤에서 멈춤.
        if h is not None and a_valid:
            hx, hy = h
            ax, ay = atk.x, atk.y
            ald = getattr(atk, "last_dir", "-")
            # 정지 latch (수정 2): tol 충족(at_target) 후 park. 격수가 tol 초과
            # 이동 전까지 정지 유지 → 격수 방향변화/OCR 미세흔들림에 안 따라가
            # "계속 돌아다님" 차단. 격수가 실제 tol 넘게 움직이면 추종 재개.
            if self._follow_parked and self._park_atk is not None:
                if (abs(ax - self._park_atk[0])
                        + abs(ay - self._park_atk[1])) <= tol:
                    return "-", (
                        f"B3-PARK 격수정지 대기 a=({ax},{ay}) "
                        f"park={self._park_atk} tol={tol}"
                    )
                self._follow_parked = False
            FOLLOW_OFFSET = 1  # 격수 뒤 1칸 유지 (tol=1 밀착 추종 정책과 호환)
            if ald == "R":
                tx, ty = ax - FOLLOW_OFFSET, ay
            elif ald == "L":
                tx, ty = ax + FOLLOW_OFFSET, ay
            elif ald == "D":
                tx, ty = ax, ay - FOLLOW_OFFSET
            elif ald == "U":
                tx, ty = ax, ay + FOLLOW_OFFSET
            else:
                # 격수 정지 → 격수 좌표 자체가 타겟 (tol 안에서 정지)
                tx, ty = ax, ay
            tdx, tdy = tx - hx, ty - hy
            if abs(tdx) <= tol and abs(tdy) <= tol:
                # 정지 latch ON: 격수 현재 좌표 기준 park (수정 2).
                self._follow_parked = True
                self._park_atk = (ax, ay)
                return "-", (
                    f"B3a:at_target d_t=({tdx},{tdy}) h={h} "
                    f"t=({tx},{ty}) a=({ax},{ay}) atk_dir={ald} PARK"
                )
            # 주축/부축 후보 산출.
            x_dir = "R" if tdx > 0 else ("L" if tdx < 0 else None)
            y_dir = "D" if tdy > 0 else ("U" if tdy < 0 else None)
            if abs(tdx) >= abs(tdy):
                first, second = x_dir, y_dir
            else:
                first, second = y_dir, x_dir
            reverse_map = {"L": "R", "R": "L", "U": "D", "D": "U"}
            bl_first = bool(first) and self._blacklist_check(
                self.healer_map, h, first
            )
            bl_second = bool(second) and self._blacklist_check(
                self.healer_map, h, second
            )
            chosen = None
            tag = "B3:to_target"
            if first and not bl_first:
                chosen = first
            elif second and not bl_second:
                chosen = second
                tag = "B3:to_target_BL-DETOUR"
            elif first and second and bl_first and bl_second:
                # v5 수정 2026-04-23: RETREAT는 "주축과 부축 둘 다 실제 존재하고
                # 둘 다 blacklist" 인 경우만 허용. 부축이 None인데 RETREAT 하면
                # 격수 반대방향으로 무한히 빠꾸되는 버그(14:32:32 힐러2 재현).
                candidates = [reverse_map[first], reverse_map[second]]
                for c in candidates:
                    if not self._blacklist_check(self.healer_map, h, c):
                        chosen = c
                        tag = "B3:to_target_BL-RETREAT"
                        break
            # else: 주축 차단 + 부축 없음 → chosen=None → STALL (정지)
            if chosen is None:
                return "-", (
                    f"B3:to_target_BL-STALL d_t=({tdx},{tdy}) h={h} "
                    f"t=({tx},{ty}) a=({ax},{ay}) atk_dir={ald} "
                    f"first={first}(bl={bl_first}) second={second}(bl={bl_second})"
                )
            return chosen, (
                f"{tag} d_t=({tdx},{tdy}) h={h} "
                f"t=({tx},{ty}) a=({ax},{ay}) atk_dir={ald}"
            )

        # B4: 힐러 좌표 없음 + 격수 좌표 OK → 격수 방향 맹목
        if h is None and a_valid:
            d = fol.direction()
            w = d if d in ("L", "R", "U", "D") else "-"
            return w, f"B4:h=None 맹목→격수방향={d!r} a=({atk.x},{atk.y})"

        # B5: 격수 좌표 무효 → 마지막 방향 맹목 (dead reckon 보조)
        d = fol.direction()
        w = d if d in ("L", "R", "U", "D") else "-"
        return w, f"B5:a_invalid 맹목→last_dir={d!r} h={h}"


