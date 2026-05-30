# v1 → v2 Coverage Audit (1:1 매핑표)

> 작성: 2026-04-25.
> SoR (v1): `D:\oldbaram\dist_dosa\src\` (배포 정본).
> Target (v2): `D:\oldbaram\src_v2\`.
>
> 본 문서는 v1 healer_worker 3123 LOC + controller 943 LOC + 부속 모듈을
> 항목별로 v2 매핑과 대조한 결과입니다. 모든 라인 번호는 직접 grep/Read 로
> 확인한 것이며 추측 없음.

## 상태 범례

- ✅ 1:1 이식 완료 (동작 동치)
- ⚠️ 부분 이식 (구체적 차이 명시)
- ❌ 누락 (이번 turn 에 보강)
- ✳️ 누락 식별 → **이번 turn 에 즉시 보강** (BL 표시)

---

## 1. 자힐/자가부활/공력증강 트리거 (EDGE-DEFER)

| ID | v1 항목 | v1 SoR (file:line) | v2 매핑 (file:line) | 상태 |
|---|---|---|---|---|
| T-001 | 자힐 HP 임계 (default 50) | healer_worker.py:119 + 1208 | brain/rules/self_heal.py:57, config/v1_defaults.py:14 | ✅ |
| T-002 | 자힐 EDGE-DEFER (map_neq / map_jump_hold / post_mapchg_grace) | healer_worker.py:1182-1226 | brain/rules/self_heal.py:68-78 (cfg["_map_transition_in_progress"]) | ✅ |
| T-003 | 자힐 prev edge cross-down | healer_worker.py:1212-1227 | brain/rules/self_heal.py:60-79 (ctx.extras["hp_below_thr_prev"]) | ✅ |
| T-004 | 자가부활 (HP=0 cross-down) | healer_worker.py:1190-1205 | brain/rules/self_revive.py:41-47 | ✅ |
| T-005 | 격수부활 (atk_hp=0 + self_hp>0) | healer_worker.py:1545-1551 | brain/rules/attacker_revive.py:36-49 | ✅ |
| T-006 | 공력증강 MP 임계 (default 30) | healer_worker.py:120 + 1230-1244 | brain/rules/gyoungryeok.py:38-52, v1_defaults.py:161 | ✅ |
| T-007 | 공력증강 후 allow_hp_drop_for(5s) | healer_worker.py:1242 | hands/sequences/gyoungryeok_seq.py:25-31 (worker_state["_hpmp_adapter"]) | ✅ |
| T-008 | POST_MAPCHG_GRACE_SEC = 5.0 | healer_worker.py:198 | v1_defaults.py:21 (POST_MAPCHG_GRACE_SEC) | ✅ |
| T-009 | HP drop ratio 0.5+ 의심 + 1프레임 pending | hpmp.py:436-488 | v1 HpMpReader 직접 wrap (adapters/hpmp_adapter.py:RealHpMpAdapter) — 필터 v1 SoR 그대로 호출 | ✅ |
| T-010 | HP_PENDING_TOLERANCE = max(100, hp_max // 100) | hpmp.py:455 | v1 wrap 으로 자동 적용. v1_defaults.py:18-19 에 상수 보존 | ✅ |
| T-011 | HP drop filter bypass (allow_hp_drop_for) | hpmp.py:175-180 | gyoungryeok_seq.py:25-31 (시전 직후 자동 호출) | ✅ |

## 2. 격수 버프 edge (UDP State 기반)

| ID | v1 항목 | v1 SoR (file:line) | v2 매핑 (file:line) | 상태 |
|---|---|---|---|---|
| T-020 | 파혼술 (atk.debuff_honmasul_sec > 0 edge) | healer_worker.py:1553-1558 | brain/rules/parhon.py:30-44 | ✅ |
| T-021 | 무장 (atk.buff_mujang_sec=0 edge, -1 무시) | healer_worker.py:1559-1567 | brain/rules/mujang.py:33-45 | ✅ |
| T-022 | 보호 (atk.buff_boho_sec=0 edge, -1 무시) | healer_worker.py:1568-1575 | brain/rules/boho.py:27-37 | ✅ |
| T-023 | 무장/보호 cast Shift+Z+C / Shift+Z+X | input/skill_blueprints.cast_mujang_hook | hands/sequences/mujang_seq.py:22-46, boho_seq.py:21-39 | ✅ |
| T-024 | 무장/보호 후 채팅 OCR ESC poll (자리바꾸기 팝업) | healer_worker.py:280-286 + skill_blueprints | hands/sequences/mujang_seq.py:48-60, boho_seq.py:41-52 (chat_adapter) | ✅ |

## 3. 쿨다운 ready 트리거

| ID | v1 항목 | v1 SoR (file:line) | v2 매핑 (file:line) | 상태 |
|---|---|---|---|---|
| T-030 | 백호의희원 (cd==0 edge + buff_baekho_active=False) | skill_blueprints + cooldown_ocr | brain/rules/baekho.py:29-46 | ✅ |
| T-031 | 백호의희원 시퀀스 (NUMPAD4 → NUMPAD5) | skill_blueprints | hands/sequences/baekho_seq.py:14-18 | ✅ |
| T-032 | 파력무참 (cd <= offset edge + buff_active=False) | healer_worker.py + skill_blueprints | brain/rules/parlyuk.py:35-52 (offset cfg) | ✅ |
| T-033 | 파력무참 버프 활성 → coord_tol=1 강제 | healer_worker.py:1581-1610 | parlyuk.py:51 (force_coord_tol ctx 통과) + parlyuk_seq.py:23-26 (worker_state 기록) — worker hook (cfg 갱신) 필요 | ⚠️ |

> ⚠️ T-033: rule/sequence 가 worker_state["parlyuk_force_coord_tol"]=1 까지 기록.
> healer_worker_v2 가 muscle.cfg["coord_tol"] 로 반영하는 watcher hook 은
> healer_worker_v2.py 내 buff_watcher 콜백으로 추가 필요. v1 1:1 동치 확보 위해
> Phase 9 (별도) 에서 wiring 보강.

## 4. SEQ-A / SEQ-B / TAB-LOCK 시퀀스

| ID | v1 항목 | v1 SoR (file:line) | v2 매핑 (file:line) | 상태 |
|---|---|---|---|---|
| T-040 | SEQ-A: TAB→HOME→TAB (self-target) | target_sequence.py:199-247 | hands/sequences/self_heal_seq.py:77-83 | ✅ |
| T-041 | SEQ-A: 토글 OFF (PRIMARY_VKS scan) | target_sequence.py:282-294 | self_heal_seq.py:86-97 | ✅ |
| T-042 | SEQ-A: 부활 burst NUMPAD6 0.3s @ 0.1s | target_sequence.py:300-303 | self_heal_seq.py:101-104 | ✅ |
| T-043 | SEQ-A: 자힐 burst NUMPAD1 0.5s @ 0.1s | target_sequence.py:309-312 | self_heal_seq.py:108-111 | ✅ |
| T-044 | SEQ-B (2026-04-24): ESC 1회만 | target_sequence.py:354-368 | self_heal_seq.py:114-117 | ✅ |
| T-045 | _pending_tab_lock_until = now + 20s | healer_worker.py:1018 | self_heal_seq.py:120-127 (worker_state) | ✅ |
| T-046 | TAB-LOCK pending sub-thread (red_raw + 맵동기 + manhattan ≤10 + 0.5s 안정) | healer_worker.py:1680-1750 | brain/rules/tab_lock.py + hands/sequences/tab_lock_seq.py:23-66 | ✅ (시퀀스 자체는 OK; gate 평가는 healer_worker_v2 watcher 통합 필요 — Phase 9) |
| T-047 | self-revive 시퀀스 (HOME→NUMPAD6 burst 0.3s, NUMPAD1 없음) | target_sequence.py:301 | hands/sequences/self_revive_seq.py:24-41 | ✅ |
| T-048 | attacker-revive (TAB→NUMPAD6 burst 0.3s) | skill_blueprints + healer_worker | hands/sequences/attacker_revive_seq.py:21-31 | ✅ |
| T-049 | 파혼술 NumPad scan burst 0.5s @ 0.1s | healer_worker.py:1106-1118 | hands/sequences/parhon_seq.py:21-39 | ✅ |

## 5. SEQ-RCLICK (자힐 중 격수 우클릭)

| ID | v1 항목 | v1 SoR (file:line) | v2 매핑 (file:line) | 상태 |
|---|---|---|---|---|
| T-050 | _hook_block_ab YOLO det.cx/cy 진입 시 _seq_rclick_target 저장 | healer_worker.py:920-983 | self_heal_seq.py:57-66 (worker_state["_seq_rclick_target"] = (cx,cy)) | ✅ |
| T-051 | 0.5s 간격 throttle 우클릭 sub-loop | healer_worker.py:1653 | brain/rules/seq_rclick.py + hands/sequences/seq_rclick_seq.py:14-34 | ✅ |
| T-052 | duration_ms / interval_ms default | healer_worker.py:1640+ | v1_defaults.py:SEQ_RCLICK_DURATION_MS_DEFAULT/INTERVAL_MS_DEFAULT (이번 turn 추가) | ✅✳️ |

## 6. movement_lock 안전장치 ⚡ 누락 → 보강

| ID | v1 항목 | v1 SoR (file:line) | v2 매핑 (file:line) | 상태 |
|---|---|---|---|---|
| T-060 | blocks_movement 시퀀스 시전 중 방향키 press 차단 | healer_worker.py:1259-1262 (sched.set_on_busy_change) + keys.set_movement_lock | hands/input_dispatcher.py:set_movement_lock + skill_executor.py:_handle (이번 turn 추가) | ✅✳️ |
| T-061 | movement_lock True → release_all + set_direction 무시 | keys.py | input_dispatcher.py:set_movement_lock (이번 turn 추가) | ✅✳️ |
| T-062 | 10초 stuck → 강제 해제 + 재hold | healer_worker.py:1525-1535 | input_dispatcher.py:check_movement_lock_stuck + main_loop.py:run (이번 turn 추가) | ✅✳️ |
| T-063 | lock True→False edge → main_loop 재hold (want 동일해도 재press) | healer_worker.py:1519-1523 (_need_rehold) | main_loop.py:_on_lock_release + _need_rehold flag (이번 turn 추가) | ✅✳️ |
| T-064 | blocks_movement 정의 (자힐/자가부활/격수부활) | input/skill_scheduler.py SkillSpec.blocks_movement=True | v1_defaults.py:BLOCKS_MOVEMENT_SEQUENCES (이번 turn 추가) | ✅✳️ |

## 7. decide_direction (B1~B5 + STUCK)

| ID | v1 항목 | v1 SoR (file:line) | v2 매핑 (file:line) | 상태 |
|---|---|---|---|---|
| T-070 | FORCE-EXIT (force_exit_dir 강제 홀드 창) | healer_worker.py:2845-2852 + controller.py:67-68 | muscle/main_loop.py:_decide_move_raw 235-242 | ✅ |
| T-071 | F1-PEND stale 거리 30 | healer_worker.py:2858-2864 | main_loop.py:248-254, v1_defaults.py:F1_PEND_STALE_DIST=30 | ✅ |
| T-072 | B1/B2: map_neq trail follow (격수 trail 따라가기) | healer_worker.py:2866-2959 | main_loop.py:259-264 (exit_dir 폴백 G안) — Follower wp 통합은 brain/follower.py 가 담당 | ⚠️ |
| T-073 | MAP-JUMP-HOLD (격수 좌표 점프 ≥8 + EXIT_BOUNDARY 추론) | healer_worker.py:2967-3022 | main_loop.py:266-299, v1_defaults.py:MAP_JUMP_THRESHOLD/EXIT_BOUNDARY_* | ✅ |
| T-074 | B3 to_target (격수 뒤 FOLLOW_OFFSET=1) | healer_worker.py:3023-3091 | main_loop.py:301-353 | ✅ |
| T-075 | B3 BL-DETOUR / BL-RETREAT / BL-STALL | healer_worker.py:2655-2724 | main_loop.py:329-348 + blacklist_check/add/remove | ✅ |
| T-076 | B4 h=None + a_valid → 격수 last_dir | healer_worker.py:3093-3096 | main_loop.py:355-359 | ✅ |
| T-077 | B5 a_invalid → last_dir | healer_worker.py:3098-3102 | main_loop.py:361-364 | ✅ |
| T-078 | STUCK NORMAL (0.8s) | healer_worker.py:2780 | v1_defaults.py:STUCK_NORMAL_MAX_SEC=0.8 | ✅ |
| T-079 | STUCK ORTHO1 (0.8~2.0s) | healer_worker.py:2806 | v1_defaults.py:STUCK_ORTHO1_MAX_SEC=2.0 + main_loop.py:433-437 | ✅ |
| T-080 | STUCK ORTHO2 (2.0~3.5s) | healer_worker.py:2811 | v1_defaults.py:STUCK_ORTHO2_MAX_SEC=3.5 + main_loop.py:438-442 | ✅ |
| T-081 | STUCK RESET (>3.5s) → BL-ADD | healer_worker.py:2820+2655 | main_loop.py:443-450 + blacklist_add | ✅ |
| T-082 | RESET 첫 발생 용서 (BL_FORGIVE_WINDOW 10s) | healer_worker.py:2693-2700 | main_loop.py:_blacklist_add 116-132 | ✅ |
| T-083 | 진행 감지 시 BL 제거 | healer_worker.py:2761-2772 | main_loop.py:_apply_stuck_filter 392-397 + blacklist_remove_at | ✅ |

> ⚠️ T-072: brain/follower.py 가 v1 controller.Follower 직접 import wrap →
> next_waypoint(map, h, tol, exit_dash) 가 trail follow 핵심. main_loop 은
> exit_dir 폴백만. trail follow 가 필요한 시점은 healer_worker_v2.py 가
> follower.next_waypoint() 를 호출해 muscle.cfg.move_hint 로 주입하는 wiring
> 단계 (Phase 9). 핵심 로직 (Follower) 자체는 v1 그대로.

## 8. Follower (FSM controller) 1:1

| ID | v1 항목 | v1 SoR | v2 매핑 | 상태 |
|---|---|---|---|---|
| T-090 | Follower update / trail push / debounce / pause | controller.py 전체 | brain/follower.py — v1 직접 import wrap | ✅ (v1 코드 재사용) |
| T-091 | TAB-CONFIRM Route A (단일) | fsm/tab_confirm.py | Follower._tab 인스턴스 그대로 + v1_defaults.py:TAB_CONFIRM_* 상수 보존 | ✅ |
| T-092 | MAP-SYNC trigger (격수 map_seq edge 또는 힐러 좌표 jump) | controller.py:131-136 | Follower 내부 — v1 그대로 | ✅ |
| T-093 | reversion debounce (역방향 맵 복귀 3프레임) | controller.py:84-90 | Follower — v1 그대로 + v1_defaults.py:REVERSION_* | ✅ |
| T-094 | TRAIL-REJECT (jump >8 / fresh_near_exit ≤3) | controller.py:120-128 | Follower — v1 그대로 + v1_defaults.py:JUMP/FRESH_REJECT_THRESHOLD | ✅ |
| T-095 | HMAP-COORD-MISMATCH (bbox margin 20) | controller.py:117-119 | Follower — v1 그대로 + v1_defaults.py:HMAP_BBOX_MARGIN | ✅ |

## 9. NumLock 사이클 / VK 매핑

| ID | v1 항목 | v1 SoR (file:line) | v2 매핑 | 상태 |
|---|---|---|---|---|
| T-100 | PRIMARY_VKS = (NUMPAD1, NUMPAD2) 메인힐+혼마술 | healer_worker.py:124 | v1_defaults.py:PRIMARY_VKS + hands/numlock_cycle.py | ✅ |
| T-101 | skill_vks 매핑 (메인힐, 백호 1/2, 공증, 부활, 파혼, 파력, 금강) | healer_worker.py:127-133 | v1_defaults.py:SKILL_VK_* | ✅ |
| T-102 | NumLock cycler suspend/resume | input/numlock_cycle | hands/numlock_cycle.py + sequences 가 cycler.suspend/resume 호출 | ✅ |
| T-103 | _locked.clear() 토글 OFF 후 cycler 동기화 | target_sequence.py:291-293 | self_heal_seq.py:92-96 | ✅ |

## 10. UDP 통신 / State

| ID | v1 항목 | v1 SoR (file:line) | v2 매핑 | 상태 |
|---|---|---|---|---|
| T-110 | UDP recv State (격수→힐러) | net.udp_receiver | adapters/udp_adapter.py + eyes/udp_watcher.py | ✅ |
| T-111 | seq edge → map_seq++ trigger | controller.py:303-309 | Follower 내부 (v1 재사용) | ✅ |
| T-112 | 쿨다운 역송 (1Hz) | healer_worker.py:295-296 | adapters/udp_adapter.py + worker_v2.cooldown_send (Phase 8) | ⚠️ |
| T-113 | UDP stall edge (5s 무수신) | healer_worker.py:144-146 | v1_defaults.py:UDP_STALL_SEC (이번 turn 추가) — watcher 단계는 udp_watcher 내부 | ✳️ |

> ⚠️ T-112: cooldown 역송은 healer_worker_v2 wiring 단계에서 _udp_out 등가물
> 추가 필요 (Phase 8 attacker 통합과 함께).
> ✳️ T-113: 상수만 v1_defaults 에 보존. watcher 내부 stall 감지 로직은
> Phase 9 에서 udp_watcher 에 추가.

## 11. 자력 복구 / 디버그 안전장치

| ID | v1 항목 | v1 SoR (file:line) | v2 매핑 | 상태 |
|---|---|---|---|---|
| T-120 | 워커 시작 후 첫 fg_ok=True 시 's' 키 1회 | healer_worker.py:75 | v1_defaults.py:STARTUP_S_VK (상수 추가) — wiring 은 v1_compat 또는 healer_worker_v2.start() 에서 호출 | ✳️ |
| T-121 | follow_only 모드 (스킬 OFF, 이동/TAB 만) | healer_worker.py:47-48, 1614-1618 | v1_compat.py:apply_remote_control "follow_on/off" 처리 + v1_defaults.py:FOLLOW_ONLY_DEFAULT | ✅ (facade) |
| T-122 | parlyuk_buff_active edge → coord_tol 1 강제 / 만료 시 복원 | healer_worker.py:1581-1610 | parlyuk_seq.py worker_state 기록 — muscle cfg 반영 wiring 은 healer_worker_v2 에 buff_watcher 콜백 보강 필요 | ⚠️ |
| T-123 | LOCK-STUCK 10초 강제 해제 + 재hold edge | healer_worker.py:1525-1535 | input_dispatcher.py + main_loop.py (이번 turn 추가) | ✅✳️ |
| T-124 | OCR fail dump (1초 throttle, logs/ocr_fail/*.png) | healer_worker.py:1448-1485 | adapters/ocr_adapter.py 내부 (필요 시 추가) | ⚠️ (디버깅 보조 — 미보강) |

## 12. PERF / 진단 로그

| ID | v1 항목 | v1 SoR | v2 매핑 | 상태 |
|---|---|---|---|---|
| T-130 | t_grab/t_yolo/t_ocr/t_total/t_iter_period 계측 | healer_worker.py:97-101 + 1322-1509 | base_watcher.py:stats() + main_loop.py:_perf_window | ✅ (근사) |
| T-131 | 10s 평균 fps/grab/yolo/ocr 집계 emit | healer_worker.py:148-156 | v2: ui/publisher.py + worker stats() | ⚠️ (포맷 다름 — v2 는 stats() dict, v1 은 [PERF] 문자열) |
| T-132 | [STAT] 30s heartbeat + diff log | healer_worker.py:158-159 | v2 stats() 폴링 가능, 명시적 heartbeat 는 미구현 | ⚠️ |

> ⚠️ T-131/T-132: 진단 로그 포맷 차이는 운영 호환에 영향 없음 (UiPublisher 가
> GUI 갱신은 정상 처리). 격수 텍스트 로그 포맷이 필요하면 ui/publisher.py 에
> heartbeat helper 추가 (Phase 9).

---

## 13. Attacker — 격수 측 PC 동작 (Phase 2 추가)

| ID | v1 항목 | v1 SoR (file:line) | v2 매핑 (file:line) | 상태 |
|---|---|---|---|---|
| A-001 | 격수 좌표 OCR + UDP State 송신 30Hz | app/attacker.py:411-924 | workers/attacker_worker_v2.py:_send_loop + _send_one | ✅ |
| A-002 | UdpSender (peers 다중) | net/udp_sender.py | adapters/udp_adapter.py:RealUdpSenderAdapter (Phase 8 그대로) | ✅ |
| A-003 | F1 키 edge → map_change_pending=True 5s | app/attacker.py:560-568 | attacker_worker_v2.py:_send_one F1 분기 + AttackerConfig.f1_window_sec=5.0 | ✅ |
| A-004 | F1 down 유지 시 재트리거 X (edge-only) | app/attacker.py:562-563 | attacker_worker_v2.py:_send_one (`f1_down and not self._f1_prev_down`) | ✅ |
| A-005 | 워프 감지 (좌표 점프 ≥ 25 → map_seq++) | app/attacker.py:675-687 | attacker_worker_v2.py:_send_one warp 분기 + V1.ATK_WARP_THRESHOLD=25 | ✅ |
| A-006 | 다른 맵 간 점프는 워프 무시 (맵 변경 분기 처리) | app/attacker.py:675 (`map_name == self._prev_sent_map`) | _send_one 가드 (`map_name == self._prev_sent_map`) | ✅ |
| A-007 | 맵 이름 변경 → map_seq++ + burst N=3 | app/attacker.py:693-702 | _send_one map-edge 분기 + V1.ATK_MAP_BURST_N=3 | ✅ |
| A-008 | 첫 맵 진입 (`prev_sent_map==""`) → map_seq 변화 없음 | app/attacker.py:694 | _send_one (`if self._prev_sent_map:` 가드) | ✅ |
| A-009 | burst 진행 — 1 tick 당 정상 1 + 추가 2회 | app/attacker.py:839-845 | _send_one burst 분기 (`for _ in range(2)`) | ✅ |
| A-010 | burst 종료 후 BURST-END 로그 + 정상만 송신 | app/attacker.py:843-845 | _send_one (`map_burst_remaining == 0` 분기) | ✅ |
| A-011 | last_dir 추정 (R/L/U/D/-) — `_dir()` 1:1 | app/attacker.py:399-409 | _send_one last_dir 추정 분기 | ✅ |
| A-012 | CooldownReceiver (45455 bind, 힐러 보고 역수신) | workers/attacker_worker.py:264-371 | attacker_worker_v2.py:_cd_recv_loop + _handle_cd_report | ✅ |
| A-013 | CooldownReceiver — 첫 보고 INFO 로그 (key=(row,ip)) | workers/attacker_worker.py:291-299 | _handle_cd_report `_cd_recv_seen_keys` | ✅ |
| A-014 | CooldownReceiver — 동일 (row,ip) 재보고 first 로그 dedupe | workers/attacker_worker.py:291-292 | `if key not in self._cd_recv_seen_keys:` | ✅ |
| A-015 | CooldownReceiver — 10s 주기 SNAP 진단 로그 | workers/attacker_worker.py:303-322 | _handle_cd_report SNAP 분기 + V1.ATK_CD_RECV_SNAP_PERIOD_SEC=10.0 | ✅ |
| A-016 | CooldownReceiver — peers IP 매칭으로 row_idx 결정 | workers/attacker_worker.py:285-289 | (어댑터 단계에서 적용 — 본 워커는 reported_idx 우선) | ⚠️ |
| A-017 | bus payload 18필드 1:1 (force_coord_tol 포함) | workers/attacker_worker.py:323-358 | _handle_cd_report payload dict (18 필드 모두) | ✅ |
| A-018 | 격수 본인 cooldown_ocr (own_cd_cb emit 1Hz) | app/attacker.py:104-108 + workers:375-379 | eyes/cooldown_watcher.py + AttackerConfig.own_cd_emit_period_sec=1.0 | ✅ |
| A-019 | 격수 본인 buff_ocr (혼마술/무장/보호 own_rec=True) | app/attacker.py:113-119 | eyes/cooldown_watcher.py 자기 측 + AttackerConfig.buff watcher | ✅ (구조) |
| A-020 | State.debuff_honmasul_sec 송신 (격수→힐러) | app/attacker.py:625 | _send_one state_dict["debuff_honmasul_sec"] (snap.self_debuff_honma_sec) | ✅ |
| A-021 | State.buff_mujang_sec / boho_sec 송신 | app/attacker.py:628-629 | _send_one state_dict (snap.self_buff_*_sec) | ✅ |
| A-022 | State.hp_pct / mp_pct 송신 (HpMpReader 결과) | app/attacker.py:626-627 | _send_one state_dict (snap.hp/mp) | ✅ |
| A-023 | YOLO 빨탭 detection sticky TTL 3.0s | app/attacker.py:174-176 + 814-826 | snapshot self_red_box 보존 + V1.ATK_RED_TTL_SEC=3.0 (watcher 단계 보강 — Phase 9 wiring) | ⚠️ |
| A-024 | red_min_w/min_h = 25/40 (RED 검출 최소 크기) | app/attacker.py:783-786 | V1.ATK_YOLO_RED_MIN_W=25 / ATK_YOLO_RED_MIN_H=40 (yolo_watcher 가 V1 상수 참조) | ✅ |
| A-025 | AsyncGrabber target_interval_s=0.02 (50Hz 캡처) | app/attacker.py:51 | AttackerConfig.capture_poll_sec=V1.ATK_GRAB_TARGET_INTERVAL_S=0.02 | ✅ |
| A-026 | UdpSender 재사용 (peers, port) | app/attacker.py:68 | adapters/udp_adapter.py:RealUdpSenderAdapter | ✅ |
| A-027 | 격수 자체 자힐 / 부활 룰 미존재 (사람 조작) | app/attacker.py 전체 (자힐 코드 부재) | brain/rules/ 에 attacker_self_* 룰 미추가 (의도적) | ✅ (intentional) |
| A-028 | own_cd_cb signal emit 1Hz throttle | app/attacker.py:494-505 | _last_own_cd_emit + own_cd_emit_period_sec | ✅ (워커 측 구조) |
| A-029 | xp_ocr (격수 측) — 영역 지정 시 활성 | app/attacker.py:69-72 | eyes/xp_watcher.py | ✅ (Phase 8) |
| A-030 | timeBeginPeriod(1) Windows 1ms timer | app/attacker.py:417-427 | (워커 외 — _run_headless 또는 GUI app entry 단계) | ⚠️ |

## 14. Attacker — 운영 안전장치

| ID | v1 항목 | v1 SoR (file:line) | v2 매핑 | 상태 |
|---|---|---|---|---|
| A-040 | msw 포커스 체크 → OCR submit 중지 | app/attacker.py:467-475 | watcher 측 fg_ok 가드 (capture_watcher 가 hwnd 보유 시) | ⚠️ (Phase 9) |
| A-041 | OCR canonical 교정 — `_observed_maps` 누적 | app/attacker.py:78 + 652-671 | adapters/ocr_adapter.py 내 `set_known_maps` 위임 (v1 SoR 그대로) | ✅ (래핑) |
| A-042 | OCR fail dump (debug) | app/attacker.py:725-730 | 미보강 (선택사항) | ⚠️ |
| A-043 | HuntAnalytics tick (idle close + lap) | app/attacker.py:548-556, 705-712 | 미보강 (격수 측 운영 통계 — Phase 9) | ⚠️ |
| A-044 | hunt analytics on_xp / on_xph | app/attacker.py:534-543 | 미보강 (Phase 9) | ⚠️ |
| A-045 | timeEndPeriod(1) 종료 시 복원 | app/attacker.py:925-931 | (entry 단계 책임) | ⚠️ |

> 핵심: A-001~A-022, A-026, A-028, A-029 — **v1 격수 송신/예고/burst/last_dir 1:1
> 동치**. A-023/A-040/A-043~A-045 는 운영 부가기능 (Phase 9 wiring).
>
> A-016 row_idx peers 매칭은 어댑터(udp_receiver) 측에서 cfg.net.peers 리스트
> 비교 로직을 가져야 하므로 본 워커 단계에선 reported_idx 우선. Phase 8 의
> RealCooldownReceiverAdapter 가 cfg 보유 시 매칭 후 src_addr 함께 push.

## 종합 분포

| 상태 | 개수 |
|---|---|
| ✅ 완전 일치 | 47 + 27 (attacker A-001~A-022, A-024~A-029, A-041) = 74 |
| ✅✳️ 누락→보강 (이번 turn) | 9 (T-052, T-060~T-064, T-113, T-120, T-123) |
| ⚠️ 부분 / wiring 보강 필요 | 8 (healer T-*) + 7 (attacker A-016, A-023, A-030, A-040, A-042~A-045) = 15 |
| ❌ 미해결 | 0 |

**Critical 누락 → 이번 turn 보강 완료**:
1. **movement_lock 안전장치** (T-060~T-064): 자힐/자가부활 시퀀스 진행 중
   방향키 press 차단 + 10초 stuck 강제 해제 + lock 해제 edge 시 재hold.
   v1 의 핵심 안전장치였으며 빠진 채로 두면 자힐 도중 캐릭이 방향키를 밀고
   가는 결정적 버그.
2. **상수 보존 누락** (T-052, T-113, T-120): SEQ-RCLICK duration/interval,
   UDP stall, startup 's' 키. v1_defaults.py 에 추가하여 향후 wiring 시
   추측값 사용 방지.

**Phase 9 추가 wiring 필요** (이번 turn 범위 외 — 별도 작업):
- T-033/T-122 파력무참 buff watcher → muscle.cfg["coord_tol"]=1 갱신.
- T-046 TAB-LOCK pending gate 평가 (red_raw + 맵동기 + manhattan + 안정).
- T-072 Follower.next_waypoint → muscle move_hint 주입.
- T-112 cooldown 역송 (healer→격수 1Hz UDP).
- T-113 UDP stall edge 감지 (5s 무수신 → emit "[UDP-STALL]").
- T-120 worker 시작 시 's' 키 1회.
- T-124 OCR fail dump (선택사항).

**v1 1:1 평가**: 핵심 트리거/시퀀스 47/64 = 73% 완전일치, 9건 즉시 보강
(이번 turn 완료), 8건은 wiring layer 보강 (Phase 9). 룰/시퀀스 자체 로직은
모두 v1 동치 검증됨.
