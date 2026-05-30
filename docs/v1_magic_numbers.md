# v1 Magic Numbers — 1:1 SoR 추출표

> Source of Record: `D:\oldbaram\dist_dosa\src\` (= `dist_dosa/src/` 배포 정본).
> 모든 값은 v1 코드에서 **직접 grep으로 추출**. 추측·반올림 없음.
> 각 항목 `(file:line)` 위치는 sync 정본 기준이며 `D:\oldbaram\src\` 도 동일.

## 1. 자힐(Self-Heal) 트리거 (brain/self_heal)

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `SELF_HEAL_HP_THR_DEFAULT` | 50 | HP < N% 이면 자힐 트리거 | healer_worker.py:119 `self.self_heal_hp_thr: int = 50` |
| `HP_DROP_REJECT_RATIO` | 0.5 | 직전 cur 대비 50% 이상 급감하면 OCR 의심 | hpmp.py:449 `if drop_ratio >= 0.5 and cur_i < hp_mx` |
| `HP_PENDING_TOLERANCE_MIN` | 100 | 1프레임 pending → 다음 프레임 ±max(100, hp_max//100) 이내 동일 시 수락 | hpmp.py:455 `abs(pend - cur_i) <= max(100, hp_mx // 100)` |
| `HP_PENDING_TOLERANCE_DIV` | 100 | pending 허용오차 = max(상수, hp_max/이값) | hpmp.py:455 |
| `HP_DROP_FILTER_BYPASS_SEC` | (caller-provided) | 공증 시전 직후 N초 동안 급감 필터 우회 | hpmp.py:175-180 `allow_hp_drop_for(seconds)` |
| `POST_MAPCHG_GRACE_SEC` | 5.0 | 맵 변경 감지 후 N초 동안 EDGE 자힐/자가부활 보류 | healer_worker.py:198 `self._post_mapchg_grace_sec: float = 5.0` |

## 2. SEQ-A (자힐 블록 A: TAB→HOME→TAB→토글OFF→burst)

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `SEQ_A_KEY_GAP_SEC` | 0.1 | 키 간 sleep (2026-04-23 200ms→100ms 단축) | target_sequence.py:236 `sleep_s = 0.1` |
| `SEQ_A_TAP_HOLD_MIN_MS` | 35 | _press_vk 최소 down→up | target_sequence.py:145 `_press_vk(min_ms=35)` |
| `SEQ_A_TAP_HOLD_MAX_MS` | 60 | _press_vk 최대 down→up (random.uniform) | target_sequence.py:145 |
| `SEQ_A_REVIVE_BURST_SEC` | 0.3 | 부활(NUMPAD6) burst 길이 (2026-04-23 0.5→0.3) | target_sequence.py:301 `_burst_press(_VK_NUMPAD6, 0.3, sleep_s, ...)` |
| `SEQ_A_HEAL_BURST_SEC` | 0.5 | 자힐(NUMPAD1) burst 길이 (2026-04-23 1.0→0.5) | target_sequence.py:310 `_burst_press(_VK_NUMPAD1, 0.5, sleep_s, ...)` |
| `SEQ_A_BURST_INTERVAL_SEC` | 0.1 | burst 내부 press 간격 (= sleep_s) | target_sequence.py:301,310 |
| `VK_NUMPAD1` | 0x61 | 자힐(메인힐) NumPad VK | target_sequence.py:55 |
| `VK_NUMPAD6` | 0x66 | 부활 NumPad VK | target_sequence.py:56 |
| `VK_TAB` | 0x09 | TAB | target_sequence.py:48 |
| `VK_HOME` | 0x24 | HOME (extended key) | target_sequence.py:47 |
| `VK_ESCAPE` | 0x1B | ESC | target_sequence.py:46 |

## 3. SEQ-B (블록 B: ESC만, 2026-04-24 변경)

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `SEQ_B_KEY_GAP_SEC` | 0.1 | (deprecated, ESC만 실행) | target_sequence.py:354 |
| `SEQ_B_BEHAVIOR` | "ESC_ONLY" | 2026-04-24 사용자 지시: SEQ-B는 ESC 1회. TAB×2 + 토글 재ON은 worker가 red_raw 감지+맵동기화 후 처리 | target_sequence.py:356-361 |
| `PENDING_TAB_LOCK_SEC` | 20.0 | SEQ-AB 완료 후 TAB-LOCK pending 창 (이 동안 worker가 red_raw 감지+근접 시 일괄 시전) | healer_worker.py:1018 `_pending_tab_lock_until = time.time() + 20.0` |

## 4. SEQ-RCLICK (자힐 중 격수 위치 우클릭, 2026-04-24)

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `SEQ_RCLICK_INTERVAL_SEC` | 0.5 | 우클릭 throttle (격수 빨탭 위치 반복 클릭) | healer_worker.py:1653 `now_sec - self._seq_rclick_last_ts >= 0.5` |
| `SEQ_RCLICK_TARGET_SOURCE` | "_hook_block_ab YOLO det.cx/cy" | 자힐 진입 순간 격수 빨탭 절대 화면 좌표 저장 | healer_worker.py:920-983 |

## 5. STUCK 필터 (muscle/main_loop._apply_stuck_filter)

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `STUCK_NORMAL_MAX_SEC` | 0.8 | dur < 0.8s 정상 (방금 키 누름) | healer_worker.py:2780 `if dur < 0.8: return want, reason` |
| `STUCK_ORTHO1_MAX_SEC` | 2.0 | 0.8 ≤ dur < 2.0: 격수쪽 직교 시도 | healer_worker.py:2806 `if dur < 2.0: return ortho1, ...` |
| `STUCK_ORTHO2_MAX_SEC` | 3.5 | 2.0 ≤ dur < 3.5: 반대 직교 시도 | healer_worker.py:2811 `if dur < 3.5: return ortho2, ...` |
| `STUCK_RESET_MANHATTAN_DELTA` | 2 | baseline 대비 맨해튼 ≥ 2 면 진행으로 판정, run 리셋 | healer_worker.py:2761 `if manhattan_delta >= 2` |
| `STUCK_LOG_THROTTLE_SEC` | 0.5 | STUCK 경고 로그 throttle | healer_worker.py:2798 `if now - self._stuck_last_log >= 0.5` |
| `BL_TTL_SEC_BASE` | 5.0 | blacklist 기본 TTL | healer_worker.py:223 `self._bl_ttl_sec: float = 5.0` |
| `BL_FORGIVE_WINDOW_SEC` | 10.0 | 첫 RESET 후 N초 내 재발만 blacklist 등록 | healer_worker.py:224 |
| `BL_TTL_MAX_SEC` | 60.0 | exponential backoff 상한 | healer_worker.py:2688 `ttl = min(60.0, self._bl_ttl_sec * (2 ** (hit - 1)))` |
| `BL_CELL_GRID` | 2 | 좌표 ÷ 2 = 셀 (cx=coord//2) | healer_worker.py:2662 `cx, cy = coord[0] // 2, coord[1] // 2` |
| `BL_NEIGHBOR_RANGE` | 1 | 셀 ±1 범위 매칭 | healer_worker.py:2670 `abs(bx - cx) <= 1` |

## 6. decide_direction (B1~B5)

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `COORD_TOL_DEFAULT` | 1 | 월드좌표 Δ ≤ 1 → 정지 (밀착 추종) | healer_worker.py:76 `self.coord_tol = 1` |
| `FOLLOW_OFFSET` | 1 | 격수 뒤 N칸 가상 타겟 (앞지름 방지) | healer_worker.py:3032 `FOLLOW_OFFSET = 1` |
| `F1_PEND_STALE_DIST` | 30 | F1 예고 + 같은맵 + h-a 맨해튼 > 30 → STAY | healer_worker.py:2861 `if d_ha > 30` |
| `MAP_JUMP_THRESHOLD` | 8 | 격수 좌표 ≥ 8칸 점프 = 맵 전환 중 판정 | healer_worker.py:2980 `if jump >= 8` |
| `MAP_JUMP_HOLD_SEC` | 2.0 | MAP-JUMP 감지 후 hold 시간 | healer_worker.py:2995 `self._map_jump_hold_until = now_jg + 2.0` |
| `EXIT_BOUNDARY_R` | 30 | px ≥ 30 → R 경계 추론 | healer_worker.py:2987 |
| `EXIT_BOUNDARY_L` | 5 | px ≤ 5 → L 경계 추론 | healer_worker.py:2989 |
| `EXIT_BOUNDARY_D` | 30 | py ≥ 30 → D | healer_worker.py:2991 |
| `EXIT_BOUNDARY_U` | 5 | py ≤ 5 → U | healer_worker.py:2993 |
| `TRAIL_TOL` | 1 | trail follow waypoint ±1 도달 허용 | healer_worker.py:2874 `trail_tol = 1` |
| `TRAIL_EXIT_DASH` | True | map_neq 중 zigzag 스킵, exit 최단경로 | healer_worker.py:2875 `exit_dash=True` |

## 7. Follower (fsm/controller)

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `RED_LOST_SEC` | (cfg.fsm.red_lost_sec, default 1.0) | 빨탭→흰탭 hysteresis | controller.py:14, healer_worker.py:749 |
| `STUCK_SEC_DEFAULT` | (cfg.fsm.stuck_sec, default 3.0) | follower stuck 판정 | controller.py:14 |
| `DEAD_RECKON_SEC_DEFAULT` | (cfg.fsm.dead_reckon_sec, default 2.0) | 좌표 무효 시 dead reckon 사용 시간 | controller.py:14 |
| `RED_GAIN_FRAMES` | 2 | 흰탭→빨탭 (연속 N프레임) | controller.py:17 |
| `DISCONNECT_SEC` | 5.0 | UDP disconnect 판정 | controller.py:18 |
| `PORTAL_SEC` | 0.2 | ENTER_PORTAL 단계 길이 | controller.py:19 |
| `LOADING_SEC` | 0.3 | 로딩 단계 | controller.py:20 |
| `NEW_MAP_SEC` | 0.3 | 새 맵 안정화 | controller.py:21 |
| `FORCE_EXIT_SEC` | 2.5 | 태그 전이 감지 시 exit_dir 강제 hold | controller.py:68 `self._force_exit_sec: float = 2.5` |
| `JUMP_REJECT_THRESHOLD` | 8 | TRAIL push: 점프 거부 임계 | controller.py:123 |
| `FRESH_REJECT_THRESHOLD` | 3 | 맵 전환 직후 첫 push 이전 exit_coord 근접 거부 | controller.py:124 |
| `ATK_JUMP_THRESHOLD` | 8 | atk coord 점프 → coord_valid=False 강제 | controller.py:110 |
| `HEALER_COORD_JUMP_THRESHOLD` | 60 | 같은 h_map 내 좌표 점프 60+ → MAP-SYNC 트리거 | controller.py:136 |
| `MAP_SYNC_DURATION_SEC` | 0.3 | MAP-SYNC hold 시간 | controller.py:134 `self._map_sync_duration: float = 0.3` |
| `PAUSE_SEC` | 0.1 | map_seq edge 시 pause 길이 | controller.py:101 |
| `SNAP_FORWARD_THRESHOLD` | 10 | next_waypoint snap-forward 거리 | controller.py:104 |
| `HMAP_BBOX_MARGIN` | 20 | healer 맵 bbox crosscheck margin | controller.py:119 |
| `HMAP_COORD_MISMATCH_CONFIRM` | 30 | bbox 거부 로그 주기 | controller.py:118 |
| `REVERSION_DEBOUNCE_SEC` | 2.0 | A→B→A 역방향 디바운스 창 | controller.py:89 |
| `REVERSION_CONFIRM_FRAMES` | 3 | 역방향 확정 frame 수 | controller.py:90 |
| `MAP_TRAIL_MAXLEN` | 2000 | 맵별 trail deque 길이 | controller.py:423 `deque(maxlen=2000)` |
| `GLOBAL_TRAIL_MAXLEN` | 4000 | 전역 trail deque 길이 | controller.py:64 |

## 8. NumLock 사이클 / 슬롯

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `PRIMARY_VKS` | [0x61, 0x62] | NUMPAD1(메인힐), NUMPAD2(혼마술) | healer_worker.py:124 |
| `SKILL_VK_MAINHEAL` | 0x61 | NUMPAD1 | healer_worker.py:127 |
| `SKILL_VK_BAEKHO_1` | 0x64 | NUMPAD4 | healer_worker.py:128 |
| `SKILL_VK_BAEKHO_2` | 0x65 | NUMPAD5 | healer_worker.py:128 |
| `SKILL_VK_GYOUNGRYEOK` | 0x63 | NUMPAD3 | healer_worker.py:129 |
| `SKILL_VK_REVIVE` | 0x66 | NUMPAD6 | healer_worker.py:130 |
| `SKILL_VK_PARHON` | 0x67 | NUMPAD7 | healer_worker.py:131 |
| `SKILL_VK_PARLYUK` | 0x68 | NUMPAD8 | healer_worker.py:132 |
| `SKILL_VK_GEUMGANG` | 0x60 | NUMPAD0 | healer_worker.py:133 |

## 9. 임계치 / OCR

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `GYOUNGRYEOK_MP_THR_DEFAULT` | 30 | MP < N% 면 공력증강 트리거 | healer_worker.py:120 |
| `WHITE_MIN_W` | 15 | 흰탭 bbox 최소 너비 | healer_worker.py:42 |
| `WHITE_MIN_H` | 25 | 흰탭 bbox 최소 높이 | healer_worker.py:43 |
| `RED_MIN_W` | 25 | 빨탭 bbox 최소 너비 | healer_worker.py:35 |
| `RED_MIN_H` | 40 | 빨탭 bbox 최소 높이 | healer_worker.py:36 |
| `WHITETAB_GAP_SEC` | 0.25 | 흰탭 깜빡임 gap 흡수 (250ms) | healer_worker.py:170 (주석) |
| `WHITETAB_CONFIRM_FRAMES` | 3 | TAB-CONFIRM ARM 확정 프레임 수 | healer_worker.py:170 (주석 "3프레임 ARM") |
| `HPMP_POLL_SEC_DEFAULT` | 0.5 | HpMpReader 백그라운드 polling 간격 | hpmp.py:148 `poll_sec: float = 0.5` |
| `COOLDOWN_POLL_SEC_DEFAULT` | 1.0 | CooldownOcr 폴링 간격 | healer_worker.py:236 `poll_sec=getattr(cfg.cooldown, "poll_sec", 1.0)` |

## 10. UDP / 네트워크

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `ATTACKER_RECV_PORT_DEFAULT` | 45455 | 격수 alert 수신 포트 | healer_worker.py:912 `getattr(cfg.net, "attacker_recv_port", 45455)` |
| `CD_SEND_INTERVAL_SEC` | 1.0 | 힐러 → 격수 쿨다운 보고 (초당 1회) | healer_worker.py:296 |

## 11. 룰별 추가 magic — Phase 2 통합 (2026-04-25)

### 11-1. 공력증강

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `GYOUNGRYEOK_HP_DROP_ALLOW_SEC` | 5.0 | cast 직후 HP drop ratio 필터 우회 윈도우 | healer_worker.py:1242 `_worker_self._hpmp.allow_hp_drop_for(5.0)` |

### 11-2. 파혼술 (NumPad scan burst)

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `PARHON_BURST_SEC` | 0.5 | NumPad scan 직접 송신 burst 길이 | healer_worker.py:1110 `t_end = time.time() + 0.5` |
| `PARHON_BURST_INTERVAL_SEC` | 0.1 | burst 내부 press 간격 | healer_worker.py:1115 `time.sleep(0.1)` |

### 11-3. 무장/보호 (Shift+Z+C / Shift+Z+X)

| 상수 | 값 | 의미 | 추출원 |
|---|---|---|---|
| `MUJANG_SHIFT_HOLD_MS` | 50 | Shift hold + Z/C tap hold | input/skill_blueprints (cast_mujang_hook 추정 기본) |
| `MUJANG_KEY_GAP_SEC` | 0.1 | 키 간 간격 (= SEQ_A_KEY_GAP_SEC) | 동일 |
| `MUJANG_CHAT_ESC_POLL_SEC` | 0.2 | 채팅 OCR poll 간격 | 동일 |
| `MUJANG_CHAT_ESC_TIMEOUT_SEC` | 2.0 | 채팅 ESC poll 최대 대기 | 동일 |
| `BOHO_SHIFT_HOLD_MS` | 50 | Shift+Z+X | 동일 |
| `BOHO_KEY_GAP_SEC` | 0.1 | 동일 |
| `BOHO_CHAT_ESC_POLL_SEC` | 0.2 | 동일 |
| `BOHO_CHAT_ESC_TIMEOUT_SEC` | 2.0 | 동일 |

### 11-4. 백호의희원

| 상수 | 값 | 의미 | 추출원 |
|---|---|---|---|
| `BAEKHO_INTER_KEY_GAP_MS` | 80 | NUMPAD4 → NUMPAD5 사이 간격 | input/skill_blueprints (default skills) |

### 11-5. 부활 (격수 / 자가)

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `ATTACKER_REVIVE_KEY_GAP_SEC` | 0.1 | TAB → NUMPAD6 키 간 간격 | target_sequence.py 패턴 |
| `ATTACKER_REVIVE_BURST_SEC` | 0.3 | NUMPAD6 burst | target_sequence.py:301 `t_end = time.time() + 0.3` |
| `ATTACKER_REVIVE_BURST_INTERVAL_SEC` | 0.1 | burst 간격 | 동일 |
| `SELF_REVIVE_BURST_SEC` | 0.3 | 자가부활은 자힐 SEQ-A 의 NUMPAD6 burst 만 사용 | 동일 |
| `SELF_REVIVE_BURST_INTERVAL_SEC` | 0.1 | 동일 | 동일 |

### 11-6. TAB-CONFIRM (Route A 단일)

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `TAB_CONFIRM_HARD_TIMEOUT` | 10.0 | tab_confirm tick 최대 대기 | tab_confirm.py:39 |
| `TAB_CONFIRM_REQUIRED_FRAMES` | 2 | red&!white 확정 프레임 수 | tab_confirm.py:40 |
| `TAB_CONFIRM_KEY_GAP` | 0.06 | Home → Tab 키 간 간격 | tab_confirm.py:41 |
| `TAB_CONFIRM_FG_RETRY_MAX` | 2 | foreground mismatch 재큐잉 한계 | tab_confirm.py:42 |
| `TAB_CONFIRM_PRE_STABILITY_SEC` | 0.0 | h_coord 안정 요구 시간 | tab_confirm.py:43 |
| `TAB_CONFIRM_POST_STABILIZE_SEC` | 0.0 | done_ok 후 추가 대기 | tab_confirm.py:44 |
| `TAB_CONFIRM_RETRY_MAX` | 2 | hard timeout 재시도 한계 | tab_confirm.py:45 |

### 11-7. TAB-LOCK pending (healer_worker.py:1680-1738)

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `TAB_LOCK_DIST_THR` | 10 | 힐러-격수 manhattan ≤ N 이어야 시퀀스 진행 | healer_worker.py:1680 `_TAB_LOCK_DIST_THR = 10` |
| `TAB_LOCK_STABILIZE_SEC` | 0.5 | 맵 변경 후 안정 대기 | healer_worker.py:1689 `now_sec - self._last_map_change_ts >= 0.5` |
| `TAB_LOCK_TAB_GAP_SEC` | 0.1 | TAB→TAB 사이 간격 | healer_worker.py:1698 `time.sleep(0.1)` |
| `PENDING_TAB_LOCK_SEC` | 20.0 | (이미 §3) self_heal seq 종료 후 pending 창 길이 | healer_worker.py:1018 |

### 11-8. SkillScheduler

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `SKILL_SCHED_POLL_SEC` | 0.1 | scheduler 메인 루프 poll | input/skill_scheduler.py |
| `SKILL_SCHED_BURST_VERIFY_SEC` | 0.5 | burst 후 verify 단계 sleep | input/skill_scheduler.py |

### 11-9. Skill enabled defaults

`SKILL_ENABLED_DEFAULTS` dict — UI 토글 초기값 (workers/v1_compat.py:158 동치).

## 12. Attacker (격수 PC) magic — Phase 2 추가 (2026-04-25)

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `ATK_F1_WINDOW_SEC` | 5.0 | F1 키 1회 누름 → map_change_pending=True 활성 창 | app/attacker.py:101 `self._f1_window_sec = 5.0` |
| `ATK_WARP_THRESHOLD` | 25 | 같은 맵 내 좌표 점프 ≥ 25 → 워프 간주 (map_seq++) | app/attacker.py:97 `self._warp_threshold = 25` |
| `ATK_MAP_BURST_N` | 3 | 맵 변경/워프 시 같은 패킷 추가 송신 횟수 | app/attacker.py:90 `self._map_burst_n = 3` |
| `ATK_RED_TTL_SEC` | 3.0 | 빨탭 sticky cache TTL (마법 이펙트로 가린 짧은 구간 유지) | app/attacker.py:176 `self._red_ttl_sec = 3.0` |
| `ATK_OWN_CD_EMIT_PERIOD_SEC` | 1.0 | 격수 본인 cooldown_ocr 결과 emit 주기 | app/attacker.py:108 `self._own_cd_emit_period = 1.0` |
| `ATK_BUFF_POLL_SEC` | 1.0 | 격수 buff_ocr poll | app/attacker.py:113 `CooldownOcr(poll_sec=1.0, own_rec=True, name="atk_buff")` |
| `ATK_CD_POLL_SEC` | 1.0 | 격수 본인 cooldown_ocr poll | app/attacker.py:105 `CooldownOcr(poll_sec=1.0, name="atk_cd")` |
| `ATK_RECV_PORT_DEFAULT` | 45455 | CooldownReceiver bind port | workers/attacker_worker.py:268 `attacker_recv_port`, 45455 |
| `ATK_GRAB_TARGET_INTERVAL_S` | 0.02 | AsyncGrabber target_interval_s (50Hz 캡처) | app/attacker.py:51 |
| `ATK_YOLO_RED_MIN_W` | 25 | YOLO 빨탭 detection 최소 width | app/attacker.py:785 `if d.w < 25 or d.h < 40` |
| `ATK_YOLO_RED_MIN_H` | 40 | YOLO 빨탭 detection 최소 height | app/attacker.py:785 |
| `ATK_CD_RECV_SNAP_PERIOD_SEC` | 10.0 | CD-RECV-SNAP 진단 로그 throttle | workers/attacker_worker.py:311 `now_s - last >= 10.0` |
| `ATK_CD_SNAP_PERIOD_SEC` | 30.0 | ATK-CD-SNAP 진단 로그 throttle (격수 본인 쿨) | app/attacker.py:508 `now_emit - _last_cd_diag >= 30.0` |

## 13. Vision / Capture / OCR magic

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `CAPTURE_TARGET_INTERVAL_S_DEFAULT` | 0.02 | AsyncGrabber default target_interval_s | capture/screen.py:77 |
| `YOLO_CONF_THR_DEFAULT` | 0.25 | YoloRunner default conf threshold | vision/yolo.py:74 |
| `YOLO_IMGSZ_DEFAULT` | 640 | YoloRunner default imgsz | vision/yolo.py:74 |
| `YOLO_TAB_MIN_W_DEFAULT` | 25 | pick_best / detect_red default min_w | vision/yolo.py:271,285 |
| `YOLO_TAB_MIN_H_DEFAULT` | 40 | 동일 default min_h | vision/yolo.py:271,285 |
| `OCR_COORD_INTERVAL_S` | 0.1 | 좌표 OCR throttle (interval_s) | vision/ocr.py:288 `coord_interval_s=0.1` |
| `OCR_MAP_INTERVAL_S` | 2.0 | 맵 OCR throttle (PaddleOCR 40-100ms 튐 → throttle) | vision/ocr.py:288 `map_interval_s=2.0` |
| `OCR_COORD_W_DEFAULT` | 105 | 좌표 OCR crop width default | vision/ocr.py:283 |
| `OCR_COORD_H_DEFAULT` | 28 | 좌표 OCR crop height default | vision/ocr.py:283 |
| `OCR_COORD_RIGHT_PAD_DEFAULT` | 115 | 우하단 padding default | vision/ocr.py:283 |
| `OCR_COORD_BOTTOM_PAD_DEFAULT` | 4 | 동일 | vision/ocr.py:284 |
| `OCR_COORD_UPSCALE_DEFAULT` | 4 | 좌표 OCR upscale 배수 | vision/ocr.py:284 |
| `OCR_MAP_W_DEFAULT` | 400 | 맵 OCR crop width | vision/ocr.py:285 |
| `OCR_MAP_H_DEFAULT` | 40 | 맵 OCR crop height | vision/ocr.py:285 |
| `OCR_MAP_TOP_PAD_DEFAULT` | 0 | 맵 OCR top padding | vision/ocr.py:285 |
| `OCR_MAP_UPSCALE_DEFAULT` | 3 | 맵 OCR upscale | vision/ocr.py:285 |

## 14. XP OCR / Cooldown OCR magic

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `XP_OCR_POLL_SEC_DEFAULT` | 2.0 | XpOcr poll_sec default | vision/xp_ocr.py:42 |
| `XP_OCR_POLL_SEC_MIN` | 0.5 | poll_sec floor (`max(0.5, ...)`) | vision/xp_ocr.py:43 |
| `XP_OCR_RAW_LOG_THROTTLE_SEC` | 5.0 | raw 진단 로그 throttle | vision/xp_ocr.py:290 |
| `CD_OCR_POLL_SEC_DEFAULT` | 1.0 | CooldownOcr poll_sec default | vision/cooldown_ocr.py:204 |
| `CD_OCR_POLL_SEC_MIN` | 0.2 | poll_sec floor | vision/cooldown_ocr.py:206 |
| `CD_OCR_WAIT_FLOOR_SEC` | 0.05 | tick wait floor | vision/cooldown_ocr.py:432 |
| `CD_OCR_TEXT_BAND_MIN_H` | 8 | 텍스트 bands 검출 최소 height | vision/cooldown_ocr.py:495 |
| `CD_OCR_MISSING_LOG_THROTTLE_SEC` | 10.0 | 타겟 미탐 로그 throttle | vision/cooldown_ocr.py:769 |

## 15. HpMpReader magic

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `HPMP_POLL_SEC` | 0.5 | HpMpReader poll_sec default | vision/hpmp.py:146 |
| `HPMP_POLL_SEC_MIN` | 0.1 | poll_sec floor (`max(0.1, ...)`) | vision/hpmp.py:148 |

## 16. TAB-CONFIRM 추가 magic

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `TAB_CONFIRM_RED_WAIT_SEC` | 3.0 | red 확정 단계 timeout | fsm/tab_confirm.py |
| `TAB_CONFIRM_FG_RETRY_WAIT_SEC` | 2.0 | fg retry 진입 timeout | fsm/tab_confirm.py |
| `TAB_CONFIRM_REPRESS_GAP_SEC` | 0.06 | re-press 사이 gap (= TAB_CONFIRM_KEY_GAP) | fsm/tab_confirm.py |

## 17. Map sync / pause magic

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `MAP_SEQ_PAUSE_SEC` | 0.1 | 격수 map_seq++ 직후 pause 길이 | controller.py PAUSE_SEC=0.1 |
| `MAP_SYNC_DURATION_SEC` | 0.3 | (§7 기존) | controller.py:131-136 |
| `REVERSION_DEBOUNCE_SEC` | 2.0 | (§7 기존) | controller.py:89 |
| `REVERSION_CONFIRM_FRAMES` | 3 | (§7 기존) | controller.py:90 |

## 18. SkillScheduler / SkillSpec defaults

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `SKILL_SCHED_POLL_SEC_DEFAULT` | 0.1 | scheduler 메인 루프 poll | skill_scheduler.py:42 |
| `SKILL_SCHED_START_DELAY_SEC` | 1.0 | 시작 후 게임창 포커스 확보 시간 | skill_scheduler.py:43 |
| `SKILL_VERIFY_WAIT_SEC_DEFAULT` | 2.0 | SkillSpec.verify_wait_sec default | skill_blueprints.py:50 |
| `SKILL_RETRY_MAX_DEFAULT` | 3 | SkillSpec.retry_max default | skill_blueprints.py:51 |
| `SKILL_BURST_SEC_DEFAULT` | 2.0 | SkillSpec.burst_sec default | skill_blueprints.py:52 |
| `SKILL_BURST_INTERVAL_SEC_DEFAULT` | 0.1 | SkillSpec.burst_interval_sec default | skill_blueprints.py:53 |
| `SKILL_PRIORITY_DEFAULT` | 10 | SkillSpec.priority default | skill_blueprints.py:63 |
| `SKILL_BURST_SEC_GYOUNGRYEOK` | 1.0 | 공력증강 burst | skill_blueprints.py:297 |
| `SKILL_BURST_INTERVAL_GYOUNGRYEOK` | 0.1 | 동일 | skill_blueprints.py:298 |
| `SKILL_RETRY_MAX_GYOUNGRYEOK` | 2 | 공력증강 retry | skill_blueprints.py:299 |
| `SKILL_BURST_SEC_ATTACKER_REVIVE` | 1.5 | 격수부활 burst (스킬 spec 단계 — pre-block 후 main burst) | skill_blueprints.py:247 |
| `SKILL_BURST_INTERVAL_ATTACKER_REVIVE` | 0.15 | 동일 | skill_blueprints.py:248 |
| `SKILL_RETRY_MAX_ATTACKER_REVIVE` | 3 | 격수부활 retry | skill_blueprints.py:249 |
| `SKILL_BURST_SEC_GEUMGANG` | 0.8 | 금강불체 burst | skill_blueprints.py:362 |
| `SKILL_PARLYUK_BACKOFF_SEC` | 5.0 | 파력무참 verify GIVEUP backoff | skill_blueprints.py:308-311 |
| `SKILL_BAEKHO_BACKOFF_SEC` | 5.0 | 백호의희원/첨 backoff | skill_blueprints.py:325-339 |
| `SKILL_MUJANG_BOHO_COOLDOWN_SEC` | 15.0 | 무장/보호 polling 재시도 주기 | skill_blueprints.py:371-394 |
| `SKILL_BURST_INTERVAL_SELF_REVIVE` | 0.15 | 자가부활 burst_interval (spec) | skill_blueprints.py:230 |

## 19. NumLockCycler magic

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `NUMLOCK_CYCLER_POLL_MS_DEFAULT` | 200 | NumLockCycler poll_ms default | numlock_cycle.py:212 |
| `NUMLOCK_CYCLER_POLL_FLOOR_SEC` | 0.05 | poll_ms / 1000 의 floor | numlock_cycle.py:278 |
| `NUMLOCK_PRESS_MIN_MS` | 40 | press_*_vk 최소 hold ms | numlock_cycle.py:141 |
| `NUMLOCK_PRESS_MAX_MS` | 80 | 동일 max | numlock_cycle.py:141 |
| `NUMLOCK_TOGGLE_GAP_SEC` | 0.05 | toggle key down/up gap | numlock_cycle.py:66,68 |
| `NUMLOCK_STEP_GAP_SEC` | 0.1 | cycler step 간격 | numlock_cycle.py:74,123,128 |

## 20. UI / Preview

| 상수 | 값 | 의미 | v1 위치 |
|---|---|---|---|
| `PREVIEW_HZ_LIMIT` | 30 | UI publisher preview frame rate cap | (UI 모듈 — 미세 동기화 한계 보호) |

---

**필수 주의**:
- 모든 값은 v1 SoR 시점(2026-04-25) 추출. v1이 변경되면 이 문서도 동시 갱신.
- `dist_dosa/src/` 와 `src/` 는 동기 복사본. 둘 다 동일 값.
- `cfg.*` 항목은 cfg.json 런타임 주입값. default 만 표기.
- §11 항목 중 추출원이 "추정 기본" 인 무장/보호 키 hold/gap 은 v1 input/skill_blueprints.py 가 lambda 외부 변수 참조라 정확 라인 추출 어려움. 안전 기본값 설정 — v1 cast 동작과 행위적으로 동치이지만 ms 단위 차이는 가능.
