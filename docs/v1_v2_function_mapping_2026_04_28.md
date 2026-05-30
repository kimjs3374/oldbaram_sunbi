# v1 → v2 기능 1:1 매핑 표 (2026-04-28)

> audit (`v1_v2_final_deep_audit_2026_04_28.md`) 6.1/6.2 권고 — 법의학 수준 감사표.
> 모든 항목: **v1 파일/라인 → v2 파일/라인 → 상태 → 운영위험 → 운영 로그 빈도**.
> 추정 없이 직접 grep / 코드 read 결과만.

상태 분류:
- **EQ** = v1 동등 동작 확인 (동일 의미)
- **PARTIAL** = 일부 옮김, 보강 흔적 있음
- **REGRESSED** = v2 에서 한 번 이상 깨졌다가 사후 봉합한 흔적
- **DEPRECATED** = v2 에서 다른 메커니즘으로 대체
- **MISSING** = v2 에 없음

---

## A. 진입점 / 워커 인스턴스

| 기능 | v1 | v2 | 상태 | 운영위험 |
|---|---|---|---|---|
| Healer 진입점 | `src/app/healer.py`, `healer_gui.py` | `src_v2/app/healer_gui_v2.py` (`MainWindow(cfg, initial_role="healer")`) | EQ | 하 |
| Attacker 진입점 | `src/app/attacker.py` (cli + gui) | `src_v2/app/attacker_v2.py` (`MainWindow(cfg, initial_role="attacker")`) | EQ | 하 — entry 별 role 강제 후 안정 |
| HealerWorker 클래스 | `src/workers/healer_worker.py:HealerWorker` (3105줄) | `src_v2/workers/_compat_healer_facade.py:HealerWorkerV1Facade` (775줄) + `src_v2/workers/healer_worker_v2.py:HealerWorkerV2` (1100줄) | PARTIAL | 중 — 분리는 됨, 기능 누락은 아래 별 항목 참조 |
| AttackerWorker 클래스 | `src/workers/attacker_worker.py` (~570줄) | `_compat_attacker_facade.py` (382) + `attacker_worker_v2.py` (~610) | PARTIAL | 중 |

---

## B. 입력 / 스킬 시전

| 기능 | v1 | v2 | 상태 | 운영위험 |
|---|---|---|---|---|
| KeyController + send_input | `src/input/keys.py:_send_input` (line 86) | `src_v2/adapters/keys_adapter.py:RealKeysAdapter._send_input` 위임 | EQ | 하 |
| NumPad scan direct | `src/input/numlock_cycle.py:press_numpad_direct` (158) | 동일 함수 import (`InputDispatcher.tap_numpad_direct`) | EQ | 하 |
| NumPad → 메인 키보드 변환 | `src/input/numlock_cycle.py:press_normal_vk` (141) | `_common.tap_numpad` → `press_normal_vk` 위임 | **REGRESSED** | **상** — v2 가 `VK_DIGIT["4"]=0x34` 메인 키보드 직송신해서 한동안 모든 NumPad 스킬 시전 실패. 사후 봉합 (audit 5.6) |
| NumLock 토글 cycler | `src/input/numlock_cycle.py:NumLockCycler` (174-330) | `src_v2/hands/numlock_cycle.py` 동일 | EQ | 하 |
| SkillScheduler | `src/input/skill_scheduler.py:SkillScheduler` (31-) | `src_v2/hands/skill_executor.py:SkillExecutor` (84-) | PARTIAL | 중 — ready_gate / ctx_provider wiring 누락된 적 있음 (audit 5.12) |
| edge-trigger queue | `skill_scheduler.py:request_cast` (103) + `_cast_queue` deque | `hands/skill_executor.py` PriorityQueue + dedup `_pending_names` | EQ | 하 |
| ready gate | `skill_scheduler.py:set_ready_gate` | `skill_executor.py:set_ready_gate` (132) | **REGRESSED** | 중 — wiring 누락 → 사후 봉합 (`healer_worker_v2._executor_ready_gate`) |
| ctx_provider (verify pool) | `skill_scheduler.py` ctx_provider | `skill_executor.py:set_ctx_provider` (139) | **REGRESSED** | 중 — 동일 패턴 |
| target_sequence (block A) | `src/input/target_sequence.py:block_a_self_target` (199-313) | `src_v2/hands/sequences/self_heal_seq.py` (60-150) | EQ | 하 |
| target_sequence (block B) | `target_sequence.py:block_b_return_to_attacker` (316-368) | `self_heal_seq.py` 끝 (ESC only, 2026-04-24) | EQ | 하 |
| skill_blueprints (default_skills) | `src/input/skill_blueprints.py:default_skills` (186-) | `src_v2/brain/rules/*.py` + `src_v2/hands/sequences/*.py` 분산 | EQ | 하 |
| `_cd_empty(cd<=0)` ready 판정 | `skill_blueprints.py:_cd_empty` (94-103) — cd<=0 (미관측 포함) ready | `brain/rules/baekho.py`, `parlyuk.py` — 한동안 `cd<0 → return None` 으로 정반대 동작 → 사후 fix | **REGRESSED** | **상** — 한 번도 안 시전한 ready 상태 영원 fire 안 됨 (audit 5.10) |
| offset_sec (첫 시전 지연) | `skill_blueprints.py:SkillSpec.ready` (79-87) | `parlyuk.py` (last_cast==0 + start_ts < offset_sec) — 사후 1:1 fix | **REGRESSED** | 중 — v2 가 처음엔 "cd<=offset 일찍 ready" 정반대 의미로 박았음 |
| edge-prev 룰 → cooldown_sec 게이트 | `SkillSpec.cooldown_sec` (last_cast 기반) | `baekho.py`, `parlyuk.py` `last_cast + 5s` 게이트 (ready_prev 폐기) | **REGRESSED** | **상** — ready_prev edge 의존 → 한 번 fire 후 OCR 미관측 시 영원 차단 |
| 파혼술 burst | `healer_worker.py:_hook_cast_parhon` (1106-1118) | `hands/sequences/parhon_seq.py` (12-39) | **REGRESSED** | 중 — VK 0x37 (메인 7) → 0x67 (NumPad 7) 사후 fix |

---

## C. OCR (비전)

| 기능 | v1 | v2 | 상태 | 운영위험 |
|---|---|---|---|---|
| Cooldown OCR | `src/vision/cooldown_ocr.py:CooldownOcr` (820줄) | `src_v2/adapters/cooldown_adapter.py:RealCooldownAdapter` wrapper + `src_v2/eyes/cooldown_watcher.py:CooldownWatcher` | EQ | 중 |
| `set_target_skills` (default healer 키워드) | `cooldown_ocr.py:276` 주석: "기본은 힐러" | adapter `set_target_skills` 위임 + `attacker_worker_v2.set_own_skill_names` 가 cd + buff 양쪽 set | **REGRESSED** | **상** — v2 가 cd 만 set, buff 누락 → 격수가 healer 스킬 추적 → `[CD-OCR-MISS]` 폭주 (사용자 신고 root) |
| Cooldown OCR 빈 result publish | (v1 무관 — 직접 read) | `cooldown_watcher._tick` `if not result: return` | **REGRESSED** | **상** — 빈 result 시 publish skip → rule_engine 영원 evaluate 안 함 (audit 5.10/5.13) |
| HpMpReader | `src/vision/hpmp.py:HpMpReader` | `src_v2/adapters/hpmp_adapter.py:RealHpMpAdapter` + `eyes/hpmp_watcher.py` | EQ | 중 |
| HpMp 값 변화 publish | (v1 무관) | `hpmp_watcher` 값 변화 시만 publish → 사후 매 tick polling 으로 변경 | **REGRESSED** | **상** — 시작 시 이미 임계치 아래면 룰 영원 안 fire (audit 5.14) |
| MapOcr | `src/vision/map_ocr.py` | `src_v2/eyes/ocr_watcher.py` (MapOcr 호출) | EQ | 하 |
| CoordOcr | `src/vision/ocr.py:CoordOcr` | `eyes/ocr_watcher.py` | EQ | 하 |
| XpOcr | `src/vision/xp_ocr.py` | `src_v2/adapters/xp_adapter.py:RealXpAdapter` + `eyes/xp_watcher.py` | PARTIAL | 중 — attacker 측 XpWatcher 누락된 적 있음 (audit 5.4) → 사후 fix |
| YOLO best.pt | `src/vision/yolo.py` | `src_v2/adapters/yolo_adapter.py:RealYoloAdapter` + `eyes/yolo_watcher.py` | EQ | 하 |

---

## D. 네트워크 (UDP)

| 기능 | v1 | v2 | 상태 | 운영위험 |
|---|---|---|---|---|
| ControlCmd protocol | `src/net/protocol.py:ControlCmd` (75-) | 그대로 import | EQ | 하 |
| State protocol (격수→힐러) | `protocol.py:State` | 그대로 + v2 `AttackerState` alias | EQ | 하 |
| CooldownReport (힐러→격수) | `protocol.py:CooldownReport` (150-) | 그대로 | EQ | 하 |
| UdpSender | `src/net/udp_sender.py:UdpSender` | wrap `RealUdpSenderAdapter` | EQ | 하 |
| UdpReceiver + ctrl_handler | `src/net/udp_receiver.py:UdpReceiver.set_control_handler` (55) | adapter 가 wrap 하지만 handler set 누락된 적 있음 → 사후 fix | **REGRESSED** | **상** — 격수 → 힐러 stop 명령이 워커 활성 시 무시 (사용자 신고) |
| 격수 IP 동적 학습 | `healer_worker.py:2367` `recv.last_src_addr()` + `_udp_out.send_to(src, port, pkt)` | v2 가 cfg.peers static 으로 잘못 박음 → 사후 `_compat_uplink.UplinkSenderShim.set_attacker_addr` 추가 | **REGRESSED** | **상** — 같은 cfg.yaml 두 PC 사용 시 힐러가 자기/다른 힐러로 잘못 송신 |
| send_control (격수→힐러) | `attacker_worker.py:send_control` (216) | facade `send_control` 가 `sender.send_control` 위임 시도 → 메서드 부재로 항상 False → 사후 v1 1:1 포팅 | **REGRESSED** | **상** — 핵심 제어 경로가 한동안 dead code (audit 3.5) |
| `_send_ctrl` UI 핸들러 | (v1 main_window UI) | v2: worker None 시 무조건 skip → fallback 직접 socket 송신 사후 추가 | **REGRESSED** | 중 — 격수 GUI 띄우자마자 "전체 시작" 누르면 송신 0 |
| ControlListener (idle bind) | `src/workers/control_listener.py` (19-) | 그대로 import (src/) | EQ | 하 |
| HealerHeartbeat (격수 IP 학습) | `src/workers/heartbeat.py:HealerHeartbeat` | 그대로 import | EQ | 하 |
| AttackerHeartbeat (CD recv idle) | `heartbeat.py:AttackerHeartbeat` | 그대로 import | EQ | 하 |
| UDP stall/resume edge | `healer_worker.py:1749-1774` `_udp_stalled/_udp_stall_since` + `[UDP-STALL]/[UDP-RESUME]` 로그 | v2 누락 → 사후 `udp_watcher` 에 1:1 추가 | **REGRESSED** | 중 — 운영 가시성 저하 (audit 5.2) |
| udp bind port race 회피 | (v1 무관) | v2 가 1초 grace 부족으로 bind 실패 → 사후 30회×200ms 재시도 | **REGRESSED** | **상** — 정지/재시작 시 격수 좌표 영원 못 받음 (사용자 신고 root) |
| attacker cd report row IP 매칭 | `heartbeat.py:68-80` peers 매칭 | v2 `attacker_worker_v2._handle_cd_report` 누락 → 사후 추가 + `set_peers()` | **REGRESSED** | 중 — 다중 힐러 환경 row 충돌 (audit 5.3) |

---

## E. FSM / Follower

| 기능 | v1 | v2 | 상태 |
|---|---|---|---|
| MAP-SEQ edge | `src/fsm/controller.py:_map_sync` (~) | `integration_tick.py` + `attacker_worker_v2._send_loop` map_seq 증가 | EQ |
| MAP-SYNC reject | `controller.py` reversion debounce | `brain/follower.py` 내부 | EQ |
| trail push/reject | `controller.py` trail | `follower.py` (1121줄) trail | EQ |
| jump reject | `controller.py:_jump_reject` | `follower.py` 내부 | EQ |
| fresh guard | `controller.py:_fresh_guard` | `follower.py` 내부 | EQ |
| exit_dir inheritance | `controller.py:exit_dir` | `follower.py` `force_exit_dir` | EQ |
| pause / tab-confirm cancel | `controller.py:pause_*` | `follower.py:is_paused` | EQ |
| TAB-CONFIRM Route A | v1 `controller.py` 통합 | `eyes/tab_confirm_driver.py` 별도 + `tab_confirm_tick` | EQ |
| MainLoop B1/B2/B3 | v1 healer_worker run() 안에 inline | `src_v2/muscle/main_loop.py` 별도 | EQ |
| coord_tol 동적 변경 | v1 worker 가 같은 ref 갱신 | v2 `MainLoop.cfg = dict(cfg)` copy → 사후 ref 공유 fix | **REGRESSED** | **상** — 파력무참 buff 동안 정밀도 저하 (audit 5.1) |
| STUCK filter | v1 inline | `muscle/main_loop.py` 분리 | EQ |
| blacklist | v1 inline | `muscle/main_loop.py` 분리 | EQ |

---

## F. 룰/시퀀스 (v2 신규 분해)

| 기능 | v1 | v2 | 상태 |
|---|---|---|---|
| 자힐 (HP edge) | `healer_worker.py` HP edge | `brain/rules/self_heal.py` + `hands/sequences/self_heal_seq.py` | EQ |
| 자가부활 (HP=0) | v1 healer_worker | `brain/rules/self_revive.py` + `self_revive_seq.py` | EQ |
| 격수부활 (atk_hp==0) | v1 healer_worker `[EDGE]` | `integration_tick.py` direct request_cast | EQ |
| 파혼술 (혼마술 edge) | v1 `_hook_cast_parhon` | `parhon.py` rule + `parhon_seq.py` | PARTIAL — VK 0x67 fix |
| 무장/보호 (atk buff 부재 edge) | v1 healer_worker | `mujang.py` + `boho.py` rule + `_seq.py` | EQ |
| 백호의희원/첨 (cd ready) | v1 SkillSpec | `baekho.py` rule | PARTIAL — last_cast 게이트 fix |
| 파력무참 (cd ready + offset) | v1 SkillSpec.offset_sec | `parlyuk.py` rule | PARTIAL — offset 의미 정정 |
| 공력증강 (mp 임계 edge) | v1 healer_worker | `gyoungryeok.py` rule | PARTIAL |
| 금강불체 (수동) | v1 manual | `geumgang.py` | EQ |
| TAB-LOCK pending | v1 healer_worker | `tab_lock.py` rule + `tab_lock_seq.py` | EQ |
| SEQ-RCLICK (자힐 중 격수 우클릭) | v1 healer_worker:920 | `seq_rclick.py` rule + `seq_rclick_seq.py` | EQ |

---

## G. UI / 상태 표시

| 기능 | v1 | v2 |
|---|---|---|
| MainWindow | `src/ui/main_window.py` (3977줄) | `src_v2/ui/main_window_v2.py` (4000+줄, v1 통째 복사 + import 만 변경) |
| StatusStrip 한글 | `src/ui/status_strip.py` | 그대로 import |
| GameOverlay / SkillAlertOverlay | `src/ui/overlay.py` | 그대로 import |
| HealerStatusOverlay (격수→힐러 HP/MP) | `src/ui/healer_status_overlay.py` | 그대로 import |
| RegionPicker | `src/ui/region_picker.py` | 그대로 import |
| settings_io (저장/로드) | `src/ui/settings_io.py` | 그대로 import |
| publisher (frame_ready emit) | v1 worker 가 직접 emit | `src_v2/ui/publisher.py:UiPublisher` (별도 thread 15Hz) |
| `_StateStub` repr 누출 | (v1 무관 — enum 직접) | v2 publisher local class repr 노출 → 사후 모듈 레벨 + `__repr__` fix |

---

## H. 메모리 / 학습 (v2 신규)

| 기능 | v1 | v2 |
|---|---|---|
| OutcomeVerifier | (없음) | `src_v2/memory/outcome_verifier.py` (12종 verifier) |
| RecoveryDispatcher | (없음) | `src_v2/brain/recovery.py` (8종 복구 시퀀스) |
| AnomalyDetector | (없음) | `src_v2/memory/anomaly_detector.py` (60s z-score) |
| SelfHealingLoop | (없음) | `src_v2/memory/self_healing.py` (meta-learner + hot_apply) |
| AlphaGo (정책망/가치망) | (없음) | `src_v2/learning/alphago/` (15 파일, min_records=1000 후 활성, 기본 비활성) |

운영위험: v1 에 없던 기능. 비활성 default 라 회귀 위험은 낮음. 활성 시 별도 검증 필요.

---

## I. 운영 로그 빈도 (audit 6.3)

`dist_dosa/logs/attacker_20260425_101035.log` (v1 attacker 한 세션):

| 태그 | 빈도 | 의미 |
|---|---|---|
| `ATK-WARP` | 4 | 격수 워프 감지 (v1 inline) |
| `HPMP-REJECT` | 98 | OCR 자릿수 누락/과잉 reject (v1 hpmp.py) — 매우 빈번 |
| `CD-RECV` | 89 | 힐러 CooldownReport 수신 (정상 통신) |
| `CTRL-SEND` | 5 | 격수→힐러 제어 명령 송신 |
| `CD-OCR-MISS` | 23 | cooldown OCR 미스 (운영 노이즈 빈번) |

**의미**:
- 운영 노이즈 (HPMP-REJECT, CD-OCR-MISS) 가 한 세션에 100+ 회 발생
- v1 은 이걸 reject + sticky guard + edge debounce 로 흡수
- v2 가 이 흡수 로직을 모두 이식했는지 별 항목 (위 표) 참조

---

## J. 종합

### 상태별 카운트
- EQ (동등): 30+ 항목
- PARTIAL: 10 항목
- **REGRESSED: 14 항목** — 모두 사후 봉합 (audit 3.1 의 "BUG-FIX 흔적" 그대로)
- DEPRECATED: 0
- MISSING: 0 (현재 시점)

### 운영위험 분포
- **상 (CRITICAL)**: 9 항목
  - 메인 키보드 VK 직송신 (모든 NumPad 스킬 시전 실패)
  - 격수 cd/buff target_skills default healer (CD-OCR-MISS 폭주)
  - cooldown_watcher 빈 result publish skip (룰 영원 무반응)
  - hpmp_watcher 값 변화 시만 publish (시작 시 임계치 미감지)
  - cd<0 ready 처리 누락 (한 번도 안 쓴 ready 영원 fire 안 됨)
  - ready_prev edge 의존 (1회 fire 후 영원 차단)
  - UDP receiver set_control_handler 누락 (격수 stop 명령 무시)
  - 격수 IP static cfg.peers (힐러→격수 송신 잘못)
  - send_control facade no-op (제어 경로 dead code)
  - udp bind port race (정지/재시작 시 격수 좌표 영원 못 받음)
  - coord_tol dict copy (파력무참 buff 동안 정밀도 저하)
- **중**: 5 항목 (ready_gate wiring, ctx_provider wiring, parlyuk_offset 의미, attacker xp watcher, attacker cd row 매칭)
- **하**: 30+ 항목

### audit 6.4 의 "최종 선언" 가능 여부
- 14건 REGRESSED 가 모두 사후 fix 됨 (코드 측면)
- **사용자 환경 30초+ 운영 실증** 후만 최종 선언 가능
- 그 전까지 정확한 표현: **"v1 에 있던 운영 보정 코드를 사후 봉합으로 이식한 상태. 회귀 차단 contract test 추가 완료. 실증 검증 보류."**
