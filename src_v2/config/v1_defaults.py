"""v1 SoR — 1:1 추출된 magic number 상수.

추출 위치 / 의미는 docs/02-design/features/v1_magic_numbers.md 참조.
v1 코드 변경 시 두 파일 동시 갱신.

원칙: 추측 금지. 모든 값은 dist_dosa/src/ 의 명시 라인에서 직접 확인.
"""
from __future__ import annotations

# =====================================================================
# 1. 자힐 트리거 (brain/self_heal)
# =====================================================================
# healer_worker.py:119
SELF_HEAL_HP_THR_DEFAULT: int = 50
# hpmp.py:449 — drop_ratio >= 0.5 + cur < hp_max → 의심
HP_DROP_REJECT_RATIO: float = 0.5
# hpmp.py:455 — pending tolerance = max(상수, hp_max // div)
HP_PENDING_TOLERANCE_MIN: int = 100
HP_PENDING_TOLERANCE_DIV: int = 100
# healer_worker.py:198
POST_MAPCHG_GRACE_SEC: float = 5.0

# =====================================================================
# 2. SEQ-A (자힐 블록 A)
# =====================================================================
# target_sequence.py:236 — sleep_s = 0.1 (2026-04-23 단축)
SEQ_A_KEY_GAP_SEC: float = 0.1
# target_sequence.py:145 — _press_vk(min_ms=35, max_ms=60)
SEQ_A_TAP_HOLD_MIN_MS: int = 35
SEQ_A_TAP_HOLD_MAX_MS: int = 60
# target_sequence.py:301 — 부활 burst (2026-04-23 0.5→0.3)
SEQ_A_REVIVE_BURST_SEC: float = 0.3
# target_sequence.py:310 — 자힐 burst (2026-04-23 1.0→0.5)
SEQ_A_HEAL_BURST_SEC: float = 0.5
# target_sequence.py:301,310 — burst 내부 press 간격 = sleep_s
SEQ_A_BURST_INTERVAL_SEC: float = 0.1

# VK 코드 (target_sequence.py:46-56)
VK_NUMPAD1: int = 0x61      # 자힐(메인힐)
VK_NUMPAD6: int = 0x66      # 부활
VK_TAB: int = 0x09
VK_HOME: int = 0x24         # extended key
VK_ESCAPE: int = 0x1B

# =====================================================================
# 3. SEQ-B (ESC만, 2026-04-24)
# =====================================================================
# target_sequence.py:354
SEQ_B_KEY_GAP_SEC: float = 0.1  # deprecated (ESC only)
SEQ_B_BEHAVIOR: str = "ESC_ONLY"
# healer_worker.py:1018
PENDING_TAB_LOCK_SEC: float = 20.0

# =====================================================================
# 4. SEQ-RCLICK
# =====================================================================
# healer_worker.py:1653
SEQ_RCLICK_INTERVAL_SEC: float = 0.5

# =====================================================================
# 5. STUCK 필터
# =====================================================================
# healer_worker.py:2780,2806,2811
STUCK_NORMAL_MAX_SEC: float = 0.8
STUCK_ORTHO1_MAX_SEC: float = 2.0
STUCK_ORTHO2_MAX_SEC: float = 3.5
# healer_worker.py:2761
STUCK_RESET_MANHATTAN_DELTA: int = 2
# healer_worker.py:2798
STUCK_LOG_THROTTLE_SEC: float = 0.5

# Blacklist (healer_worker.py:223-224, 2688)
BL_TTL_SEC_BASE: float = 5.0
BL_FORGIVE_WINDOW_SEC: float = 10.0
BL_TTL_MAX_SEC: float = 60.0
# healer_worker.py:2662 — coord // 2 = cell
BL_CELL_GRID: int = 2
# healer_worker.py:2670 — abs(bx - cx) <= 1
BL_NEIGHBOR_RANGE: int = 1

# =====================================================================
# 6. decide_direction (B1~B5)
# =====================================================================
# healer_worker.py:76
COORD_TOL_DEFAULT: int = 1
# healer_worker.py:3032
FOLLOW_OFFSET: int = 1
# healer_worker.py:2861
F1_PEND_STALE_DIST: int = 30
# healer_worker.py:2980,2995
MAP_JUMP_THRESHOLD: int = 8
MAP_JUMP_HOLD_SEC: float = 2.0
# healer_worker.py:2987-2993
EXIT_BOUNDARY_R: int = 30
EXIT_BOUNDARY_L: int = 5
EXIT_BOUNDARY_D: int = 30
EXIT_BOUNDARY_U: int = 5
# healer_worker.py:2874
TRAIL_TOL: int = 1
# healer_worker.py:2875
TRAIL_EXIT_DASH: bool = True

# =====================================================================
# 7. Follower (fsm/controller)
# =====================================================================
# controller.py:14, healer_worker.py:749
RED_LOST_SEC_DEFAULT: float = 1.0
STUCK_SEC_DEFAULT: float = 3.0
DEAD_RECKON_SEC_DEFAULT: float = 2.0
# controller.py:17-21
RED_GAIN_FRAMES: int = 2
DISCONNECT_SEC: float = 5.0
PORTAL_SEC: float = 0.2
LOADING_SEC: float = 0.3
NEW_MAP_SEC: float = 0.3
# controller.py:68
FORCE_EXIT_SEC: float = 2.5
# controller.py:123-124
JUMP_REJECT_THRESHOLD: int = 8
FRESH_REJECT_THRESHOLD: int = 3
# controller.py:110
ATK_JUMP_THRESHOLD: int = 8
# controller.py:136
HEALER_COORD_JUMP_THRESHOLD: int = 60
# controller.py:134
MAP_SYNC_DURATION_SEC: float = 0.3
# controller.py:101
PAUSE_SEC: float = 0.1
# controller.py:104
SNAP_FORWARD_THRESHOLD: int = 10
# controller.py:119
HMAP_BBOX_MARGIN: int = 20
# controller.py:118
HMAP_COORD_MISMATCH_CONFIRM: int = 30
# controller.py:89-90
REVERSION_DEBOUNCE_SEC: float = 2.0
REVERSION_CONFIRM_FRAMES: int = 3
# controller.py:423,64
MAP_TRAIL_MAXLEN: int = 2000
GLOBAL_TRAIL_MAXLEN: int = 4000

# =====================================================================
# 8. NumLock 사이클 / 슬롯
# =====================================================================
# healer_worker.py:124
PRIMARY_VKS: tuple = (0x61, 0x62)  # NUMPAD1, NUMPAD2
# healer_worker.py:127-133
SKILL_VK_MAINHEAL: int = 0x61
SKILL_VK_BAEKHO_1: int = 0x64
SKILL_VK_BAEKHO_2: int = 0x65
SKILL_VK_GYOUNGRYEOK: int = 0x63
SKILL_VK_REVIVE: int = 0x66
SKILL_VK_PARHON: int = 0x67
SKILL_VK_PARLYUK: int = 0x68
SKILL_VK_GEUMGANG: int = 0x60

# =====================================================================
# 9. 임계치 / OCR
# =====================================================================
# healer_worker.py:120
GYOUNGRYEOK_MP_THR_DEFAULT: int = 30
# healer_worker.py:42-43
WHITE_MIN_W: int = 15
WHITE_MIN_H: int = 25
# healer_worker.py:35-36
RED_MIN_W: int = 25
RED_MIN_H: int = 40
# healer_worker.py:170 (주석)
WHITETAB_GAP_SEC: float = 0.25
WHITETAB_CONFIRM_FRAMES: int = 3
# hpmp.py:148
HPMP_POLL_SEC_DEFAULT: float = 0.5
# healer_worker.py:236
COOLDOWN_POLL_SEC_DEFAULT: float = 1.0

# =====================================================================
# 10. UDP / 네트워크
# =====================================================================
# healer_worker.py:912
ATTACKER_RECV_PORT_DEFAULT: int = 45455
# healer_worker.py:296
CD_SEND_INTERVAL_SEC: float = 1.0

# =====================================================================
# 11. 룰별 추가 magic — Phase 2 통합
# =====================================================================

# healer_worker.py:1242 — gyoungryeok 후 hpmp.allow_hp_drop_for(5.0)
GYOUNGRYEOK_HP_DROP_ALLOW_SEC: float = 5.0

# healer_worker.py:1110-1115 — 파혼술 NumPad scan burst
PARHON_BURST_SEC: float = 0.5
PARHON_BURST_INTERVAL_SEC: float = 0.1

# 무장/보호 cast (input.skill_blueprints.py)
# Shift+Z+C / Shift+Z+X 시퀀스 + 채팅 OCR ESC poll.
MUJANG_SHIFT_HOLD_MS: int = 50
MUJANG_KEY_GAP_SEC: float = 0.1
MUJANG_CHAT_ESC_POLL_SEC: float = 0.2
MUJANG_CHAT_ESC_TIMEOUT_SEC: float = 2.0
BOHO_SHIFT_HOLD_MS: int = 50
BOHO_KEY_GAP_SEC: float = 0.1
BOHO_CHAT_ESC_POLL_SEC: float = 0.2
BOHO_CHAT_ESC_TIMEOUT_SEC: float = 2.0

# 백호 burst (skill_blueprints) — 4 → 5 (간격 80ms 추정 — v1 시퀀스에선 즉시 후속).
BAEKHO_INTER_KEY_GAP_MS: int = 80

# 격수부활 시퀀스 (HOME → NUMPAD6).
ATTACKER_REVIVE_KEY_GAP_SEC: float = 0.1
ATTACKER_REVIVE_BURST_SEC: float = 0.3
ATTACKER_REVIVE_BURST_INTERVAL_SEC: float = 0.1

# 자가부활 = 자힐 SEQ-A 와 동일 burst (별도 entry point 만 다름).
SELF_REVIVE_BURST_SEC: float = 0.3
SELF_REVIVE_BURST_INTERVAL_SEC: float = 0.1

# TAB-CONFIRM (fsm/tab_confirm.py:39-48)
TAB_CONFIRM_HARD_TIMEOUT: float = 10.0
TAB_CONFIRM_REQUIRED_FRAMES: int = 2
TAB_CONFIRM_KEY_GAP: float = 0.06
TAB_CONFIRM_FG_RETRY_MAX: int = 2
TAB_CONFIRM_PRE_STABILITY_SEC: float = 0.0
TAB_CONFIRM_POST_STABILIZE_SEC: float = 0.0
TAB_CONFIRM_RETRY_MAX: int = 2

# TAB-LOCK pending sub-thread (healer_worker.py:1680-1750).
TAB_LOCK_DIST_THR: int = 10
TAB_LOCK_STABILIZE_SEC: float = 0.5
TAB_LOCK_TAB_GAP_SEC: float = 0.1

# Skill scheduler poll (skill_scheduler.py).
SKILL_SCHED_POLL_SEC: float = 0.1
SKILL_SCHED_BURST_VERIFY_SEC: float = 0.5

# =====================================================================
# 12. Movement lock 안전장치 (healer_worker.py:1516-1539)
# =====================================================================
# blocks_movement=True 시퀀스(자힐/자가부활 SEQ-A) 중 방향키 press 잠금
# + 10초 stuck 시 강제 해제 + lock True→False edge 시 재hold 예약.
MOVEMENT_LOCK_STUCK_SEC: float = 10.0
# blocks_movement 인 시퀀스 이름 (v1 SkillSpec.blocks_movement=True 와 동치)
BLOCKS_MOVEMENT_SEQUENCES: tuple = ("self_heal", "self_revive", "attacker_revive")

# =====================================================================
# 13. SEQ-RCLICK (자힐 중 격수 위치 우클릭, healer_worker.py:1653+)
# =====================================================================
# rclick interval (0.5s) 는 이미 SEQ_RCLICK_INTERVAL_SEC.
# 추가: rclick 지속시간 — 자힐 burst 동안만 (SEQ_A_HEAL_BURST_SEC = 0.5s) +
# Block-B(ESC) 까지 = ~ 0.6~1.5s. 보수적으로 1.5s default.
SEQ_RCLICK_DURATION_MS_DEFAULT: int = 1500
SEQ_RCLICK_INTERVAL_MS_DEFAULT: int = 500  # = SEQ_RCLICK_INTERVAL_SEC * 1000

# =====================================================================
# 14. Startup 's' 키 (healer_worker.py:75)
# =====================================================================
# 워커 시작 후 첫 fg_ok=True 순간 's' 키 1회 송신 (사용자 지시 2026-04-21).
STARTUP_S_VK: int = 0x53  # 'S' key

# =====================================================================
# 15. UDP stall (healer_worker.py:144-146)
# =====================================================================
# UDP seq 무변화 N초 → stall 진입 edge. 진입/복귀 1회씩 로그.
UDP_STALL_SEC: float = 5.0  # disconnect 와 동일 default

# =====================================================================
# 16. 따라가기 전용 모드 (healer_worker.py:47-48)
# =====================================================================
# True면 스킬 완전 OFF, 이동/TAB-CONFIRM만. 빨탭 무시 → COMBAT 진입 차단.
FOLLOW_ONLY_DEFAULT: bool = False

# Skill enabled defaults (UI 토글 기준)
SKILL_ENABLED_DEFAULTS: dict = {
    "백호의희원": True,
    "백호의희원첨": True,
    "공력증강": True,
    "부활": True,
    "파혼술": True,
    "파력무참": True,
    "금강불체": False,
    "무장": True,
    "보호": True,
    "자힐": True,
}

# =====================================================================
# 17. Attacker 측 magic numbers (Phase 2/3, app/attacker.py + workers/attacker_worker.py)
# =====================================================================
# attacker.py:101 — F1 수동 맵전환 예고 활성 창
ATK_F1_WINDOW_SEC: float = 5.0
# attacker.py:97 — 같은 맵 좌표 급변 → 워프 간주, map_seq++ 트리거
ATK_WARP_THRESHOLD: int = 25
# attacker.py:90 — 맵 변경 burst 송신 횟수
ATK_MAP_BURST_N: int = 3
# attacker.py:176 — 빨탭 sticky TTL (마법 이펙트로 가린 짧은 구간)
ATK_RED_TTL_SEC: float = 3.0
# attacker.py:108 — own_cd_cb emit 주기
ATK_OWN_CD_EMIT_PERIOD_SEC: float = 1.0
# attacker.py:113 — 격수 buff_ocr poll
ATK_BUFF_POLL_SEC: float = 1.0
# attacker.py:105 — 격수 본인 cooldown_ocr poll
ATK_CD_POLL_SEC: float = 1.0
# attacker_worker.py:268 — UDP recv (cooldown 역수신) bind port
ATK_RECV_PORT_DEFAULT: int = 45455
# attacker.py:51 — AsyncGrabber target_interval_s (격수 측 capture 주기)
ATK_GRAB_TARGET_INTERVAL_S: float = 0.02
# attacker.py:783-786 — YOLO 빨탭 detection 최소 박스 크기
ATK_YOLO_RED_MIN_W: int = 25
ATK_YOLO_RED_MIN_H: int = 40
# attacker.py:311 — CD-RECV-SNAP 진단 로그 throttle
ATK_CD_RECV_SNAP_PERIOD_SEC: float = 10.0
# attacker.py:508 — ATK-CD-SNAP 진단 로그 throttle
ATK_CD_SNAP_PERIOD_SEC: float = 30.0

# =====================================================================
# 18. Vision / Capture / OCR magic (vision/, capture/screen.py)
# =====================================================================
# capture/screen.py:77 — AsyncGrabber default target_interval_s
CAPTURE_TARGET_INTERVAL_S_DEFAULT: float = 0.02
# vision/yolo.py:74 — YoloRunner default conf_thr
YOLO_CONF_THR_DEFAULT: float = 0.25
# vision/yolo.py:74 — default imgsz
YOLO_IMGSZ_DEFAULT: int = 640
# vision/yolo.py:271,285 — pick_best / detect_red default min_w/min_h
YOLO_TAB_MIN_W_DEFAULT: int = 25
YOLO_TAB_MIN_H_DEFAULT: int = 40
# vision/ocr.py:288 — coord OCR throttle (interval_s)
OCR_COORD_INTERVAL_S: float = 0.1
# vision/ocr.py:288 — map OCR throttle
OCR_MAP_INTERVAL_S: float = 2.0
# vision/ocr.py:283-287 — OCR crop default geometry
OCR_COORD_W_DEFAULT: int = 105
OCR_COORD_H_DEFAULT: int = 28
OCR_COORD_RIGHT_PAD_DEFAULT: int = 115
OCR_COORD_BOTTOM_PAD_DEFAULT: int = 4
OCR_COORD_UPSCALE_DEFAULT: int = 4
OCR_MAP_W_DEFAULT: int = 400
OCR_MAP_H_DEFAULT: int = 40
OCR_MAP_TOP_PAD_DEFAULT: int = 0
OCR_MAP_UPSCALE_DEFAULT: int = 3

# =====================================================================
# 19. XP OCR / Cooldown OCR (vision/xp_ocr.py + cooldown_ocr.py)
# =====================================================================
# vision/xp_ocr.py:42 — XpOcr poll_sec default
XP_OCR_POLL_SEC_DEFAULT: float = 2.0
# vision/xp_ocr.py:43 — poll_sec floor
XP_OCR_POLL_SEC_MIN: float = 0.5
# vision/xp_ocr.py:290 — raw 로그 throttle
XP_OCR_RAW_LOG_THROTTLE_SEC: float = 5.0
# vision/cooldown_ocr.py:204 — CooldownOcr poll_sec default
CD_OCR_POLL_SEC_DEFAULT: float = 1.0
# vision/cooldown_ocr.py:206 — poll_sec floor
CD_OCR_POLL_SEC_MIN: float = 0.2
# vision/cooldown_ocr.py:432 — wait floor
CD_OCR_WAIT_FLOOR_SEC: float = 0.05
# vision/cooldown_ocr.py:495 — text band detection min_h
CD_OCR_TEXT_BAND_MIN_H: int = 8
# vision/cooldown_ocr.py:769 — 타겟 미탐 로그 throttle
CD_OCR_MISSING_LOG_THROTTLE_SEC: float = 10.0

# =====================================================================
# 20. HpMp Reader (vision/hpmp.py)
# =====================================================================
# hpmp.py:146 — poll_sec default
HPMP_POLL_SEC: float = 0.5
# hpmp.py:148 — poll_sec floor
HPMP_POLL_SEC_MIN: float = 0.1

# =====================================================================
# 21. TAB-CONFIRM 추가 (fsm/tab_confirm.py 잔여)
# =====================================================================
# tab_confirm.py — red wait timeout (TAB-CONFIRM 단계)
TAB_CONFIRM_RED_WAIT_SEC: float = 3.0
# tab_confirm.py — fg retry 진입 timeout
TAB_CONFIRM_FG_RETRY_WAIT_SEC: float = 2.0
# tab_confirm.py — re-press 사이 gap (= TAB_CONFIRM_KEY_GAP)
TAB_CONFIRM_REPRESS_GAP_SEC: float = 0.06

# =====================================================================
# 22. Map sync / pause (fsm/controller.py 잔여)
# =====================================================================
# controller.py — MAP-SEQ pause sec (격수 map_seq++ → pause 길이)
MAP_SEQ_PAUSE_SEC: float = 0.1
# controller.py — MAP-SYNC duration (B3 force resume 막는 hold)
# = MAP_SYNC_DURATION_SEC 기존 (0.3)
# controller.py:89-90 — REVERSION_DEBOUNCE_SEC + REVERSION_CONFIRM_FRAMES (이미 정의)

# =====================================================================
# 23. SkillScheduler / SkillSpec (skill_scheduler.py + skill_blueprints.py)
# =====================================================================
# skill_scheduler.py:42 — poll_sec default
SKILL_SCHED_POLL_SEC_DEFAULT: float = 0.1
# skill_scheduler.py:43 — start_delay_sec default
SKILL_SCHED_START_DELAY_SEC: float = 1.0
# skill_blueprints.py SkillSpec defaults
SKILL_VERIFY_WAIT_SEC_DEFAULT: float = 2.0
SKILL_RETRY_MAX_DEFAULT: int = 3
SKILL_BURST_SEC_DEFAULT: float = 2.0
SKILL_BURST_INTERVAL_SEC_DEFAULT: float = 0.1
SKILL_PRIORITY_DEFAULT: int = 10

# Skill 별 burst (skill_blueprints.py 확정값)
SKILL_BURST_SEC_GYOUNGRYEOK: float = 1.0    # 공력증강
SKILL_BURST_INTERVAL_GYOUNGRYEOK: float = 0.1
SKILL_RETRY_MAX_GYOUNGRYEOK: int = 2
SKILL_BURST_SEC_ATTACKER_REVIVE: float = 1.5
SKILL_BURST_INTERVAL_ATTACKER_REVIVE: float = 0.15
SKILL_RETRY_MAX_ATTACKER_REVIVE: int = 3
SKILL_BURST_SEC_GEUMGANG: float = 0.8       # 금강불체
SKILL_PARLYUK_BACKOFF_SEC: float = 5.0      # 파력무참 backoff
SKILL_BAEKHO_BACKOFF_SEC: float = 5.0       # 백호의희원/첨 backoff
SKILL_MUJANG_BOHO_COOLDOWN_SEC: float = 15.0  # 무장/보호 polling 재시도
# Self-revive (자가부활) burst_interval (skill_blueprints.py:230)
SKILL_BURST_INTERVAL_SELF_REVIVE: float = 0.15

# =====================================================================
# 24. Preview / UI (오버레이 / publisher rate)
# =====================================================================
# UI publisher preview frame rate cap
PREVIEW_HZ_LIMIT: int = 30

# =====================================================================
# 25. NumLockCycler 사이클 / pacing (input/numlock_cycle.py)
# =====================================================================
# numlock_cycle.py:212 — NumLockCycler poll_ms default
NUMLOCK_CYCLER_POLL_MS_DEFAULT: int = 200
# numlock_cycle.py:278 — poll_ms / 1000 의 floor
NUMLOCK_CYCLER_POLL_FLOOR_SEC: float = 0.05
# numlock_cycle.py:141,158,176 — press_*_vk min/max hold ms
NUMLOCK_PRESS_MIN_MS: int = 40
NUMLOCK_PRESS_MAX_MS: int = 80
# numlock_cycle.py:66,68 — toggle key down/up gap
NUMLOCK_TOGGLE_GAP_SEC: float = 0.05
# numlock_cycle.py:74,123,128 — cycler step gap
NUMLOCK_STEP_GAP_SEC: float = 0.1
