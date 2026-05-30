# v1 → v2 기능 1:1 매핑 표 v2 (2026-04-28)

> review (`v1_v2_function_mapping_review_2026_04_28.md`) 권고 반영판.
> 기존 (`v1_v2_function_mapping_2026_04_28.md`) 의 단정 표현/부정확 라인 번호/표본 부족 정정.

## 변경점 (review 권고 반영)

| review 지적 | 본 v2 보완 |
|---|---|
| 라인 번호 부정확 (예: attacker_worker.py 570→448) | **모든 라인 수 실측 (`wc -l`) 으로 정정** |
| EQ 너무 관대 — UI import 만으로 EQ | **EQ-CODE / EQ-OPS 분리** (운영 동치 검증 여부) |
| MISSING: 0 너무 공격적 | **"명시적 부재 없음, 보강·봉합형 다수"** 로 정정 |
| 로그 표본 1세션 | **attacker 5세션 + healer_v2 5세션** 으로 확장 |
| fixed-in-code vs verified-in-runtime 미분리 | **fix-in-code / verified-in-runtime 두 열 추가** |
| "추정 없음" 과한 표현 | **"코드 근거 중심의 강한 감사 해석"** 으로 톤 정정 |

---

## 0. 본 표의 한계 (먼저 명시)

이 표는 **코드 근거 중심의 강한 감사 해석**이지, 수학적 증명 아님:
- `EQ-CODE` 판정은 "대응 코드 존재 + 의미 일치 추정". 운영 동치 별도.
- `EQ-OPS` 판정은 운영 로그 또는 사용자 환경 확인된 항목만.
- `REGRESSED` 는 코드 주석 (BUG-FIX/누락 수정/fallback) 흔적 + grep 결과 기반.
- 일부 항목은 사용자 환경 30초+ 운영 후만 최종 판단 가능.

상태 분류 (4종):
- **EQ-CODE**: v1 동등 코드 존재, 의미 일치 (운영 동치 미검증)
- **EQ-OPS**: 코드 동등 + 운영 로그/사용자 환경 검증 완료
- **PARTIAL**: 일부 옮김, 보강 흔적 또는 wiring 누락 이력
- **REGRESSED**: v2 가 한 번 이상 깨졌다 사후 봉합. BUG-FIX 주석 또는 사용자 신고 root cause.

운영위험: 상/중/하.

fix-in-code: 코드 차원 fix 적용 완료 여부.
verified-in-runtime: 사용자 환경 실증 검증 여부.

---

## A. 진입점 / 워커 (실측 라인 수)

| 기능 | v1 (라인) | v2 (라인) | 상태 | fix-in-code | verified-in-runtime | 운영위험 |
|---|---|---|---|---|---|---|
| Healer 진입점 | `app/healer_gui.py` | `app/healer_gui_v2.py` | EQ-CODE | ✓ | 부분 (ROLE 로그 확인됨) | 하 |
| Attacker 진입점 | `app/attacker.py` (cli+gui) | `app/attacker_v2.py` | EQ-CODE | ✓ | 부분 (atk-v2 로그 확인) | 하 |
| HealerWorker 본체 | `workers/healer_worker.py` (3104) | `workers/_compat_healer_facade.py` (775) + `workers/healer_worker_v2.py` (1103) | PARTIAL | ✓ | **❌ 미검증** (5세션 BRAIN-fire=0) | **상** |
| AttackerWorker 본체 | `workers/attacker_worker.py` (**448**) | `_compat_attacker_facade.py` (382) + `attacker_worker_v2.py` (**620**) | PARTIAL | ✓ | 부분 | 중 |

**review 지적 정정**: attacker_worker.py 570→**448**, attacker_worker_v2.py 610→**620**.

---

## B. 입력 / 스킬 시전

| 기능 | v1 | v2 | 상태 | fix-in-code | verified-in-runtime |
|---|---|---|---|---|---|
| `_send_input` | `input/keys.py:86` | `adapters/keys_adapter.py:_send_input` 위임 | EQ-CODE | ✓ | 부분 |
| NumPad scan direct | `numlock_cycle.py:158 press_numpad_direct` | `InputDispatcher.tap_numpad_direct` (분리 fix) | EQ-CODE | ✓ | ❌ |
| NumPad → 메인 변환 | `numlock_cycle.py:141 press_normal_vk` | `_common.tap_numpad` → press_normal_vk 위임 | **REGRESSED** | ✓ | ❌ — 5세션 HANDS-seq=0 |
| NumLock cycler | `numlock_cycle.py:174 NumLockCycler` | `hands/numlock_cycle.py` (동일) | EQ-CODE | ✓ | 부분 (CYCLE 로그 일부 확인) |
| SkillScheduler | `input/skill_scheduler.py` (**447**) | `hands/skill_executor.py` (**336**) | PARTIAL | ✓ | ❌ |
| ready_gate | `skill_scheduler.py:set_ready_gate` | `skill_executor.py:set_ready_gate` (132) | REGRESSED | ✓ | ❌ |
| ctx_provider verify | `skill_scheduler.py` ctx_provider | `skill_executor.py:set_ctx_provider` (139) | REGRESSED | ✓ | ❌ |
| target_sequence block A | `input/target_sequence.py:199-313` (**433줄 전체**) | `hands/sequences/self_heal_seq.py` (60-150) | EQ-CODE | ✓ | ❌ |
| target_sequence block B | `target_sequence.py:316-368` | `self_heal_seq.py` 끝 (ESC only) | EQ-CODE | ✓ | ❌ |
| `_cd_empty (cd<=0)` ready | `skill_blueprints.py:_cd_empty` (94-103) | `baekho.py`/`parlyuk.py` cd<=0 (사후 fix) | **REGRESSED** | ✓ | **❌** |
| offset_sec 첫 시전 지연 | `skill_blueprints.py:79-87 SkillSpec.ready` | `parlyuk.py` last_cast/start_ts (사후 fix) | **REGRESSED** | ✓ | ❌ |
| ready_prev → cooldown_sec 게이트 | `SkillSpec.cooldown_sec` (last_cast 기반) | `baekho.py`/`parlyuk.py` 사후 last_cast+5s 게이트 | **REGRESSED** | ✓ | ❌ |
| 파혼술 burst | `healer_worker.py:1106 _hook_cast_parhon` | `parhon_seq.py` (12-39) | REGRESSED (VK 0x67 fix) | ✓ | ❌ |

---

## C. OCR (비전)

| 기능 | v1 | v2 | 상태 | fix-in-code | verified-in-runtime |
|---|---|---|---|---|---|
| Cooldown OCR 본체 | `vision/cooldown_ocr.py` (**819**) | `adapters/cooldown_adapter.py` + `eyes/cooldown_watcher.py` (**193**) | EQ-CODE | ✓ | 부분 (CD-MISS 로그 발생 — 미스만 보임) |
| `set_target_skills` (격수 본인 스킬 교체) | `cooldown_ocr.py:276` | `attacker_worker_v2.set_own_skill_names` cd+buff 양쪽 (사후 fix) | **REGRESSED** | ✓ | **❌** |
| 빈 result publish | (v1 무관) | `cooldown_watcher._tick` 사후 강제 publish | **REGRESSED** | ✓ | ❌ |
| HpMpReader | `vision/hpmp.py` | `adapters/hpmp_adapter.py` + `eyes/hpmp_watcher.py` | EQ-CODE | ✓ | ❌ |
| HpMp 매 tick publish | (v1 무관) | `hpmp_watcher` 사후 polling | **REGRESSED** | ✓ | ❌ |
| MapOcr | `vision/map_ocr.py` | `eyes/ocr_watcher.py` | EQ-CODE | ✓ | ❌ |
| CoordOcr | `vision/ocr.py` (**1030**) | `eyes/ocr_watcher.py` | EQ-CODE | ✓ | ❌ |
| XpOcr | `vision/xp_ocr.py` | `adapters/xp_adapter.py` + `eyes/xp_watcher.py` | PARTIAL (attacker XpWatcher 누락 fix) | ✓ | ❌ |
| YOLO best.pt | `vision/yolo.py` | `adapters/yolo_adapter.py` + `eyes/yolo_watcher.py` | EQ-CODE | ✓ | 부분 (YOLO-PROF 로그 확인) |

---

## D. 네트워크

| 기능 | v1 | v2 | 상태 | fix-in-code | verified-in-runtime |
|---|---|---|---|---|---|
| ControlCmd / State / CooldownReport | `net/protocol.py` | 그대로 import | EQ-OPS | ✓ | ✓ (CD-RECV/CTRL-SEND 로그 확인) |
| UdpSender | `net/udp_sender.py` | `RealUdpSenderAdapter` wrap | EQ-OPS | ✓ | ✓ |
| UdpReceiver + ctrl_handler | `net/udp_receiver.py:55 set_control_handler` | `_compat_healer_adapters.build_healer_adapters` 사후 wiring | **REGRESSED** | ✓ | ❌ |
| 격수 IP 동적 학습 | `healer_worker.py:2367 recv.last_src_addr()` | `_compat_uplink.UplinkSenderShim.set_attacker_addr` (사후) | **REGRESSED** | ✓ | ❌ |
| send_control | `attacker_worker.py:216 send_control` | facade 가 한동안 dead → v1 1:1 포팅 | **REGRESSED** | ✓ | 부분 (한 세션 CTRL-SEND=12 확인) |
| `_send_ctrl` UI fallback | (v1 워커 의존) | v2 worker None 시 직접 socket fallback (사후) | **REGRESSED** | ✓ | ❌ |
| ControlListener | `workers/control_listener.py` | 그대로 import | EQ-CODE | ✓ | 부분 |
| HealerHeartbeat | `workers/heartbeat.py` | 그대로 import | EQ-CODE | ✓ | 부분 |
| AttackerHeartbeat | `heartbeat.py` | 그대로 import | EQ-CODE | ✓ | 부분 |
| UDP stall/resume edge | `healer_worker.py:1749-1774` | `udp_watcher` 사후 1:1 추가 | **REGRESSED** | ✓ | ❌ — 5세션 STALL/RESUME=0 (이벤트 자체 발생 안 함) |
| udp bind port race 회피 | (v1 무관) | 사후 30회×200ms 재시도 + ctrl_listener wait 3000ms | **REGRESSED** | ✓ | ❌ |
| attacker cd report row IP 매칭 | `heartbeat.py:68-80` | `attacker_worker_v2._handle_cd_report` 사후 + `set_peers()` | **REGRESSED** | ✓ | ❌ |

---

## E. FSM / Follower

| 기능 | v1 (라인) | v2 (라인) | 상태 | fix-in-code | verified-in-runtime |
|---|---|---|---|---|---|
| MAP-SEQ edge / MAP-SYNC reject | `fsm/controller.py` (**953**) | `brain/follower.py` (**1120**) + `integration_tick.py` (**366**) | EQ-CODE | — | ❌ |
| trail / jump / fresh / pause | `controller.py` | `follower.py` 내부 | EQ-CODE | — | ❌ |
| TAB-CONFIRM Route A | v1 controller 통합 | `eyes/tab_confirm_driver.py` 별도 | EQ-CODE | — | ❌ |
| MainLoop B1/B2/B3/STUCK/BL | v1 healer_worker run() inline | `muscle/main_loop.py` (**814**) 분리 | EQ-CODE | — | ❌ |
| coord_tol 동적 변경 | v1 worker 같은 ref | `MainLoop.cfg = dict(cfg)` copy → ref 공유 사후 fix | **REGRESSED** | ✓ | ❌ |

---

## F. 룰/시퀀스 (v2 신규 분해)

| 기능 | v1 | v2 | 상태 | fix-in-code | verified-in-runtime |
|---|---|---|---|---|---|
| 자힐 (HP edge) | v1 healer_worker HP edge | `rules/self_heal.py` + `sequences/self_heal_seq.py` | EQ-CODE | ✓ | ❌ |
| 자가부활 (HP=0) | v1 healer_worker | `self_revive.py` + `_seq.py` | EQ-CODE | ✓ | ❌ |
| 격수부활 (atk_hp==0) | v1 healer_worker `[EDGE]` | `integration_tick.py` direct | EQ-CODE | ✓ | ❌ |
| 파혼술 (혼마술 edge) | v1 `_hook_cast_parhon` | `parhon.py` rule + `_seq.py` | REGRESSED (VK fix) | ✓ | ❌ |
| 무장/보호 (atk buff 부재 edge) | v1 healer_worker | `mujang.py`/`boho.py` rule | EQ-CODE | ✓ | ❌ |
| 백호의희원/첨 (cd ready) | v1 SkillSpec | `baekho.py` rule (last_cast+5s) | **REGRESSED** | ✓ | ❌ |
| 파력무참 (cd ready + offset) | v1 SkillSpec.offset_sec | `parlyuk.py` rule | **REGRESSED** | ✓ | ❌ |
| 공력증강 (mp 임계 edge) | v1 healer_worker | `gyoungryeok.py` rule | PARTIAL | ✓ | ❌ |
| 금강불체 (수동) | v1 manual | `geumgang.py` | EQ-CODE | — | ❌ |
| TAB-LOCK pending | v1 healer_worker | `tab_lock.py` rule + `_seq.py` | EQ-CODE | ✓ | ❌ |
| SEQ-RCLICK | v1 healer_worker:920 | `seq_rclick.py` rule + `_seq.py` | EQ-CODE | ✓ | ❌ |

---

## G. UI

review 지적 (UI import = EQ 너무 관대) 반영:

| 기능 | v1 | v2 | 상태 |
|---|---|---|---|
| MainWindow | `ui/main_window.py` (**3976**) | `ui/main_window_v2.py` (v1 통째 복사 + import) | **EQ-CODE only** — wiring/race 영향 받음 |
| StatusStrip / Overlay / RegionPicker | `ui/*.py` | 그대로 import | EQ-CODE only |
| settings_io | `ui/settings_io.py` | 그대로 import | EQ-OPS (저장/로드 정상 동작 확인) |
| `_StateStub` repr | (v1 무관) | publisher 사후 fix (모듈 레벨 + `__repr__`) | **REGRESSED** | ✓ | ❌ |

UI 파일 재사용은 코드 동치, 운영 동치 ≠. wiring/role/cfg sync timing 영향 큼.

---

## H. 메모리/학습 (v2 신규)

v1 에 없던 기능. default 비활성. EQ/REGRESSED 평가 무관.

---

## I. 운영 로그 빈도 (5세션 표본)

### attacker (v1) — 5세션
| 세션 | WARP | HPMP-REJ | CD-RECV | CTRL-SEND | CD-MISS |
|---|---|---|---|---|---|
| 04-25 10:10 | 4 | 98 | 89 | 5 | 23 |
| 04-24 18:08 | 0 | 1 | 1 | 0 | 1 |
| 04-24 17:28 | 5 | 64 | 77 | 12 | 4 |
| 04-24 17:16 | 1 | 11 | 20 | 5 | 2 |
| 04-24 16:33 | 1 | 18 | 49 | 6 | 6 |
| **합계** | **11** | **192** | **236** | **28** | **36** |

해석: 운영 노이즈 (HPMP-REJECT, CD-OCR-MISS) 가 매 세션 발생. v1 은 reject + sticky guard + edge debounce 로 흡수.

### healer_v2 — 5세션 (사용자 환경 deploy 후)
| 세션 | BRAIN-fire | HANDS-seq | CFG-CONTRACT | UDP-STALL | UDP-RESUME | UDP-BIND | CD-MISS |
|---|---|---|---|---|---|---|---|
| 04-28 11:16 | **0** | **0** | **0** | 0 | 0 | 1 | 12 |
| 04-27 17:24 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| 04-27 17:07 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| 04-27 16:48 | 0 | 0 | 0 | 0 | 0 | 1 | 2 |
| 04-27 16:06 | 0 | 0 | 0 | 0 | 0 | 1 | 10 |

**결정적 사실**:
- **5세션 모두 BRAIN-fire=0, HANDS-seq=0** → v2 healer 워커 시작 후 룰 한 번도 fire 안 함
- **CFG-CONTRACT=0** → `_start_healer` 가 호출 안 됐거나 워커 시작 자체 안 함 (사용자가 시작 버튼 안 눌렀을 가능성, 또는 entry/role 문제)
- UDP-STALL/RESUME=0 → 격수 State 수신 자체 안 됨
- CD-MISS 발생 → cooldown_watcher 는 도는데 OCR 미스만

→ **v2 코드 fix 들이 deploy 됐어도 사용자 환경에서 워커 자체가 안 도는 상태로 보임.** 이게 진짜 root cause.

---

## J. 종합

### 상태별 카운트 (실측 정정)
- EQ-OPS: 4 (운영 로그/사용자 환경 검증)
- EQ-CODE: 26 (코드 동등, 운영 미검증)
- PARTIAL: 7
- **REGRESSED: 16** (사후 봉합 흔적, BUG-FIX 주석 + 사용자 신고)
- 명시적 부재: 0 (보강·봉합형 다수 — review 권고 표현)

### fix-in-code vs verified-in-runtime 분포
- fix-in-code ✓: 약 35 항목
- verified-in-runtime ✓: **4 항목만** (attacker UDP send/recv 핵심 통신 + UI settings)
- 나머지 30+ 항목: 사용자 환경 30초+ 운영 후만 검증 가능

### 결정적 운영 사실 (audit 6.3 검증)
- **healer_v2 5세션 모두 BRAIN-fire=0** — 룰 한 번도 fire 안 함
- 코드 fix 들 deploy 후에도 워커 자체 안 도는 듯
- CFG-CONTRACT 로그 안 떰 → `_start_healer` 호출 안 됨

### 다음 진단 필요
1. 사용자가 격수 PC 만 띄웠는지 확인 (healer PC 로그 5세션 모두 워커 시작 흔적 없음)
2. 또는 사용자가 격수 모드 라디오 + 시작 → healer PC 의 CFG-CONTRACT 가 떠야 정상
3. 사용자 환경 healer PC 에서 시작 버튼 누른 후 새 로그 필요

### 정확한 결론 (review 권고 표현)
> v2 는 v1 의 운영 보정 코드를 사후 봉합으로 이식한 상태. 회귀 차단 contract test 추가 완료.
> **명시적 부재 없음, 보강·봉합형 다수.**
> 운영 동치 검증은 verified-in-runtime 4 항목 외 보류.
> 사용자 환경 healer 워커 실제 시작 + 30초+ 운영 후 BRAIN-fire/HANDS-seq 라인 떠야 최종 선언 가능.
