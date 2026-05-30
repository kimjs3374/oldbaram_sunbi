# v1 → v2 심층 분석 / 기능 명세 / 이행 작업서 (2026-05-05)

## 0. 목적

이 문서는 다음 3가지를 한 번에 정리하기 위한 문서다.

1. `src/`(v1) 전체 구조와 핵심 알고리즘을 실제 코드 기준으로 요약
2. `src_v2/`(v2) 구조와 현재 상태를 v1 대비 분석
3. **v1 기능이 v2에서 전부 동작하도록 만들기 위해 무엇을 추가/수정해야 하는지** 작업 명세로 정리

본 문서는 기존 감사 문서를 그대로 복붙하지 않고, 실제 소스 파일을 읽고 정리한 결과를 바탕으로 작성했다.

---

## 1. 검토 범위와 방법

### 1.1 실제로 본 핵심 파일

#### v1 (`src/`)
- `src/workers/healer_worker.py`
- `src/workers/attacker_worker.py`
- `src/app/healer.py`
- `src/app/attacker.py`
- `src/fsm/controller.py`
- `src/input/skill_scheduler.py`
- `src/input/target_sequence.py`
- `src/input/skill_blueprints.py`
- `src/net/protocol.py`

#### v2 (`src_v2/`)
- `src_v2/README.md`
- `src_v2/workers/healer_worker_v2.py`
- `src_v2/workers/attacker_worker_v2.py`
- `src_v2/workers/_compat_healer_facade.py`
- `src_v2/workers/_compat_attacker_facade.py`
- `src_v2/muscle/main_loop.py`
- `src_v2/brain/follower.py`
- `src_v2/brain/rule_engine.py`
- `src_v2/brain/integration_tick.py`
- `src_v2/brain/decision.py`
- `src_v2/brain/rules/*`
- `src_v2/hands/skill_executor.py`
- `src_v2/hands/sequences/*`
- `src_v2/eyes/ocr_watcher.py`
- `src_v2/eyes/udp_watcher.py`
- `src_v2/eyes/cooldown_watcher.py`
- `src_v2/eyes/cooldown_uplink.py`
- `src_v2/core/snapshot.py`
- `src_v2/core/plugin_registry.py`
- `src_v2/app/healer_gui_v2.py`
- `src_v2/app/attacker_v2.py`
- `src_v2/ui/main_window_v2.py`
- `src_v2/ui/v2_main_window.py`
- `src_v2/config/migration_v1_to_v2.py`
- `src_v2/config/v1_defaults.py`
- `src_v2/tests/test_v1_parity_phase1.py`
- `src_v2/tests/test_v1_parity_phase2.py`
- `src_v2/tests/test_v1_compat_facade.py`

### 1.2 코드 규모 비교

실측 결과:

| 구분 | Python 파일 수 | 총 LOC |
|---|---:|---:|
| `src` | 67 | 21,296 |
| `src_v2` | 162 | 25,395 |

즉 v2는 “가벼워진 코드베이스”라기보다는, **분리된 모듈 수가 많아졌고 전체 표면적도 더 커진 상태**다.

### 1.3 검증 한계

- `python -m pytest src_v2/tests -q` 실행 불가: Windows python alias 문제
- `py -m pytest src_v2/tests -q` 실행 불가: **pytest 미설치**

따라서 본 문서는 **코드 정적 분석 중심**이며, 테스트/운영 실증은 별도 후속 검증이 필요하다.

### 1.4 사용자 피드백을 반영한 판단 기준

이번 분석은 단순히 “v2에 코드가 있느냐”보다 **실전에서 v1만큼 실제로 동작하느냐**를 더 높은 기준으로 본다.

사용자 피드백의 요지는 다음과 같다.

- v1은 실제로 잘 쓰고 있었음
- 대규모 리팩토링 후 넘어온 v2는 **안 되는 기능이 너무 많음**
- 실전 기능 체감상 **v1의 반도 못 따라오는 수준**
- v1은 임시 보존본이지, **v2가 런타임에서 의존해야 할 대상이 아님**

따라서 이 문서의 핵심 평가는 다음 질문에 맞춰 재정렬한다.

1. v2가 실제 사냥/추종/시전/복구를 v1 수준으로 수행하는가?
2. 수행하지 못한다면, 그 원인이 단순 버그인지, 아니면 구조적으로 아직 v1 독립이 안 된 것인지?
3. 최종 목표가 “compat 유지”가 아니라 “**v1 없이도 돌아가는 독립 v2**”일 때, 무엇부터 걷어내야 하는가?

---

## 2. 한 줄 결론

### 최종 판단

- **v2는 구조 분해는 잘했지만, 사용자 실전 기준으로는 아직 v1 대체품이 아니다.**
- 현재 상태는 “기능 코드가 일부 존재한다”와 “실전에서 제대로 돈다” 사이 간극이 크다.
- 특히 **실제 운영 경로가 아직 v1/compat에 얽혀 있다는 점 자체가 구조 결함**이다.
- 따라서 현재 v2는 “개선된 차세대 구조”라기보다, **기능 복구도 덜 됐고 독립성도 덜 확보된 과도기 산출물**로 보는 것이 맞다.

---

## 3. v1 전체 알고리즘 분석

## 3.1 v1의 본질

v1은 보기 좋은 구조가 아니라, **운영 중 생긴 예외를 한 파일/한 흐름 안에 계속 누적해 생존시킨 실전형 코드**다.

특히 힐러 측은 아래가 하나의 큰 실행 흐름으로 엮여 있다.

- 화면 캡처
- YOLO 빨탭/흰탭 검출
- 힐러 자기 좌표/맵 OCR
- 격수 UDP 상태 수신
- Follower/FSM 기반 추종 판단
- STUCK 필터 / 블랙리스트 / 맵전환 가드
- 스킬 edge 감지
- 자힐/부활/버프 시퀀스
- 쿨다운 역송 / 원격 제어 / UI 동기화

즉 v1은 모놀리식이지만, **문제 해결 로직이 실제로 다 붙어 있다**는 게 강점이다.

## 3.2 v1 핵심 알고리즘 축

### A. 관측(Observation)
- 캡처: `Grabber` / `AsyncGrabber`
- YOLO: 빨탭/흰탭 감지
- OCR:
  - 힐러 자기 좌표/맵
  - 격수 좌표/맵
  - HP/MP
  - cooldown/buff
  - XP
- UDP:
  - 격수 → 힐러 상태 송신
  - 힐러 → 격수 cooldown report 역송

### B. 추종(Follow)
- `fsm/controller.py::Follower`
- trail 기반 waypoint 추종
- 맵 전환 추정
- force-exit
- MAP-SYNC / jump reject / fresh reject
- tab confirm

### C. 이동 결정(Movement Decision)
- `healer_worker.py` 내부 B1/B2/B3 분기
- 격수 맵 불일치 시 trail / exit_dir 우선
- 같은 맵이면 attacker 뒤 `FOLLOW_OFFSET` 유지
- stuck orthogonal 회피
- blacklist / reset history로 반복 벽박힘 완화

### D. 시전 엔진(Casting)
- `SkillScheduler`
  - edge queue
  - ready gate
  - verify/retry
  - movement lock
- `target_sequence.py`
  - self-target block A
  - return block B
  - 버프 Shift 조합
- `NumLockCycler`
  - 주력힐/혼마술 토글 상태 유지

### E. 격수 측 상태 송신(Attacker Side)
- 자기 좌표/맵 OCR → UDP 30Hz 송신
- F1 수동 맵전환 예고
- warp 감지 → `map_seq` 증가
- buff/debuff/hp/mp/xp/cooldown 관측
- 힐러 cooldown report 역수신

---

## 3.3 v1 기능별 명세서

| 기능 | 입력 | 핵심 처리 | 출력/부수효과 | 대표 코드 |
|---|---|---|---|---|
| 힐러 자기 좌표/맵 인식 | 캡처 프레임 | OCR로 coord/map 추출 | `healer_coord`, `healer_map` 갱신 | `src/vision/ocr.py`, `src/workers/healer_worker.py` |
| 격수 상태 수신 | UDP State | seq/map/coord/buff/hp/mp 반영 | follower 입력값 형성 | `src/net/protocol.py`, `src/net/udp_receiver.py` |
| 빨탭/흰탭 검출 | YOLO 결과 | 크기/신뢰도 필터, sticky, quiet gate | follow/tab-confirm 판단 재료 | `src/vision/yolo.py`, `src/workers/healer_worker.py` |
| Follower FSM | attacker state + healer map | map edge, trail, exit_dir, pause, sync | FOLLOW/COMBAT/PORTAL류 상태 | `src/fsm/controller.py` |
| 이동 결정 | healer/attacker coord/map | B1/B2 trail, B3 attacker 뒤 추종, STUCK | 방향키 hold/release | `src/workers/healer_worker.py` |
| STUCK 회피 | 동일 방향 장시간 정체 | orthogonal1 → orthogonal2 → reset | 이동 복구 | `src/workers/healer_worker.py` |
| 블랙리스트 | reset 이력 | 첫 reset 용서, 재발 시 TTL 차단 | 같은 벽 재진입 억제 | `src/workers/healer_worker.py` |
| 자힐 | self HP edge | block A → heal burst → block B | movement lock, TAB 복귀 pending | `src/input/skill_blueprints.py`, `src/input/target_sequence.py` |
| 자가부활 | self HP=0 edge | self-target 후 revive/heal | post-heal recovery | 동일 |
| 격수부활 | atk HP=0 & self alive | revive burst | 격수 생존 복구 | `src/input/skill_blueprints.py` |
| 공력증강 | self MP<thr edge | burst + HP drop allow | 자기 MP 회복 보조 | `src/input/skill_blueprints.py` |
| 백호의희원/첨 | cooldown ready | verify 성공까지 retry | cooldown 관측 기반 보조 스킬 | `src/input/skill_blueprints.py` |
| 파력무참 | buff 부재 + offset | retry_until_ready, buff active 시 coord_tol=1 | 추종 안정성 보정 | 동일 + `healer_worker.py` |
| 파혼술 | attacker 혼마술 edge | 전용 burst | debuff 해제 | 동일 |
| 무장/보호 | attacker buff 소실 | Shift 조합 시퀀스 | attacker buff 유지 | `src/input/target_sequence.py` |
| 금강불체 | 수동 | manual only burst | 사용자 요청 기반 | `src/input/skill_blueprints.py` |
| TAB lock | 자힐/맵전환 후 pending | 조건 만족 시 TAB×2 + 토글 재ON | attacker 재타겟 | `src/workers/healer_worker.py` |
| cooldown uplink | own OCR + state | CooldownReport 1Hz 송신 | attacker UI/overlay 갱신 | `src/net/protocol.py`, `src/workers/healer_worker.py` |
| attacker 송신 | 자기 coord/map/hpmp/buff | UDP 30Hz + map_seq burst | healer 추종 정보 제공 | `src/app/attacker.py` |

---

## 4. v2 전체 구조 분석

## 4.1 v2의 구조적 설계

v2는 아래 식으로 책임을 나눴다.

- `core/`: snapshot, event_bus, plugin_registry, types
- `eyes/`: capture / yolo / ocr / hpmp / cooldown / udp / xp watcher
- `brain/`: rule engine, follower, integration tick, rules
- `hands/`: dispatcher, skill executor, numlock cycle, sequences
- `muscle/`: 메인 이동 루프
- `workers/`: healer/attacker composition root, compat facade
- `adapters/`: v1 구현 감싸기
- `ui/`: compat UI + 별도 v2 UI
- `learning/`, `memory/`: v1에 없던 확장 계층

설계 방향 자체는 좋다. 특히 **관측 / 판단 / 입력 / 메인루프 분리**는 v1보다 훨씬 낫다.

---

## 4.2 v2에서 잘된 점

### 1) 메인루프 분리가 실제 코드로 구현됨
- `src_v2/muscle/main_loop.py`
- B1/B2/B3, force-exit, jump hold, stuck, blacklist가 별도 모듈로 분리됨

### 2) Follower 이식 수준이 높음
- `src_v2/brain/follower.py`
- trail, pause, map edge, tab confirm, sync guard 등 핵심 개념이 유지됨

### 3) SkillExecutor가 v1 시전엔진의 핵심 특성을 가져옴
- `ready_gate`
- `verify/retry`
- `dedup`
- `movement_lock`
- `in_progress`

즉 단순 queue worker가 아니라, **v1의 운영 성질을 의식한 포팅**이다.

### 4) watcher 진단성이 좋아짐
- `OcrWatcher`, `UdpWatcher`, `CooldownWatcher`가 상태/실패/heartbeat 로그를 낸다.
- `UdpWatcher`에는 `[UDP-STALL]`, `[UDP-RESUME]` edge까지 들어가 있다.

### 5) cooldown stale 문제를 v2에서 더 명시적으로 다룸
- `CooldownWatcher`가 empty 결과도 publish
- known field를 매 tick 초기화 후 result로 덮어씀

이건 v1의 “암묵적 최신값 교체”보다 디버깅이 쉽다.

### 6) attacker 측 row resolution 보강이 들어감
- `src_v2/workers/attacker_worker_v2.py::_handle_cd_report()`
- reported idx만 쓰는 것이 아니라 peer IP 매칭으로 row를 resolve한다.

이건 다중 힐러 환경에서 **v1 대비 오히려 더 명시적**이다.

---

## 4.3 중요한 현실: 현재 실제 런타임 경로는 “순수 v2”가 아니며, 이건 받아들일 현실이 아니라 제거해야 할 부채다

문서/주석과 실제 코드 사이에 중요한 차이가 있다.

### 기대한 설명
- `src_v2/app/healer_gui_v2.py`: facade 폐기, v2 자체 GUI라고 설명
- `src_v2/app/attacker_v2.py`: facade 폐기, v2 자체 GUI라고 설명

### 실제 코드 경로
- `healer_gui_v2.py::main()` → `src_v2.ui.main_window_v2.MainWindow`
- `attacker_v2.py::_run_gui()` → `src_v2.ui.main_window_v2.MainWindow`

그런데 `src_v2/ui/main_window_v2.py`는:
- `src.ui.*` 컴포넌트를 그대로 import
- `src_v2.workers.v1_compat`의 **HealerWorkerV1Facade / AttackerWorkerV1Facade** 를 사용

즉 현재 운영 경로는 실제로 아래다.

```text
app/*_v2.py
  -> ui/main_window_v2.py
    -> src.ui.* 재사용
    -> v1_compat facade
      -> *_worker_v2
        -> eyes/brain/hands/muscle
```

반면 `src_v2/ui/v2_main_window.py`라는 별도 순수 v2 UI가 존재하지만, **현재 엔트리포인트에서는 사용되지 않는다**.

### 해석

이건 단순 스타일 문제가 아니다.

- “v2로 갈아탔다”는 팀 인식과
- “실제 배포 경로는 compat 기반이다”는 코드 현실이

다르다는 뜻이다.

하지만 여기서 더 중요한 건, **이 compat 경로를 운영 표준으로 인정하면 안 된다는 점**이다.

사용자 요구는 명확하다.

- v1은 임시로 남겨둔 것
- v2는 독립 스크립트가 되어야 함
- 즉 최종 상태는 `src_v2` 런타임에서 `src/*`를 실질 의존하지 않아야 함

따라서 이 사실의 올바른 해석은 다음이다.

1. 지금 장애가 나면 먼저 **실제로 타는 compat 경로**를 고쳐야 한다.
2. 그러나 그 경로를 “앞으로도 공식 경로”로 굳히면 안 된다.
3. 최종 목표는 **compat 제거 + 순수 v2 경로 단일화**다.

즉 현재 상태는 “현실적으로 먼저 디버깅해야 하는 경로”와 “장기적으로 반드시 없애야 하는 경로”가 **같은 곳**인 상황이다.

### 4.3.1 현재 남아 있는 v1 런타임 의존성 유형

아래는 단순 참조가 아니라, **v2 독립성을 깨는 런타임 의존성**으로 봐야 한다.

1. **UI 의존성**
   - `src_v2/ui/main_window_v2.py` → `src.ui.*` 직접 import

2. **Worker facade 의존성**
   - `src_v2/workers/v1_compat*`
   - 실제 시작/중지/영역주입/시그널 경로가 compat를 통함

3. **Adapter 의존성**
   - `RealGrabberAdapter`, `RealYoloAdapter`, `RealOcrAdapter`, `RealCooldownAdapter`, `RealHpMpAdapter`, `RealXpAdapter`, `RealKeysAdapter`, `RealUdpAdapter`가 대부분 `src/*` 구현을 wrap

4. **프로토콜/유틸 재사용 의존성**
   - `src.net.protocol`, `src.input.keys`, `src.utils.*` 등 재사용 흔적

5. **엔트리포인트-설명 불일치**
   - 문서는 독립 v2처럼 말하지만, 실제로는 compat+src 경로를 탐

이 상태에서는 v2가 일부 동작하더라도, **실패 원인이 v2 자체인지 src 래핑층인지 compat glue인지 즉시 분리되지 않는다.**

---

## 4.4 v2에서 잘못된 점 / 회귀 / 리스크

| 우선순위 | 항목 | 내용 | 영향 |
|---|---|---|---|
| P0 | 실전 기능 완성도 미달 | 사용자 실전 피드백 기준으로 v2는 아직 v1 기능 체감의 절반 이하 수준 | 리팩토링 결과물이 운영 가치를 못 만듦 |
| P0 | v1 런타임 의존성 잔존 | UI, facade, adapter, protocol 레벨에서 `src/*` 의존이 남아 있음 | v2 독립 배포 불가, 장애 원인층 혼탁 |
| P0 | 엔트리/문서 불일치 | `healer_gui_v2.py`, `attacker_v2.py`는 facade 폐기라고 쓰지만 실제로는 compat UI/compat facade 경로를 탐 | 운영 경로 착각, 디버깅 위치 혼선 |
| P0 | 금강불체 토글 매핑 버그 | `healer_worker_v2.set_skill_enabled()`에서 **`"금강불체": "boho_enabled"`** 로 잘못 매핑 | 금강불체 토글이 보호 토글과 엮일 수 있음 |
| P0 | PluginRegistry 호출 버그 | 동일 함수에서 `PluginRegistry.instance()` 호출하지만 registry 클래스에 `instance()`가 없음 | 예외가 삼켜져 설정 반영 일부가 silently no-op |
| P1 | 책임 중복 | attacker_revive/parhon/mujang/boho가 **integration_tick와 rule plugin 양쪽에 동시에 존재** | 유지보수 시 한쪽만 수정하면 의미 분기 가능 |
| P1 | parlyuk buff seconds 정밀도 부족 | `cooldown_uplink.py`에서 `buff_parlyuk_sec`를 실제 버프 시간 대신 `cd_parlyuk` 값으로 근사 | attacker UI/판정 정확도 저하 가능 |
| P1 | 상태명 혼란 | attacker worker가 자기 좌표/맵을 snapshot의 `healer_coord`, `healer_map` 필드에 실어 사용 | 코드를 읽는 사람이 맥락을 놓치기 쉬움 |
| P1 | UI 스택 이중화 | `main_window_v2.py`와 `v2_main_window.py`가 공존하나 실제 사용 경로가 분리됨 | 유지보수 비용 증가 |
| P2 | 코드 표면적 증가 | v2 LOC가 v1보다 큼 (25,395 > 21,296) | 단순 리라이트 이상의 관리부담 |

---

## 5. v1 대비 v2 기능 상태 평가

| 기능 영역 | 상태 | 판단 |
|---|---|---|
| UDP 프로토콜 | 부분 | 양방향 처리 코드는 있으나 아직 `src.net.protocol` 의존 |
| healer OCR/YOLO/UDP watcher | 부분 양호 | 구조는 좋지만 실전 동작 증명이 충분하지 않음 |
| follower / trail / map transition | 부분 양호 | 핵심 개념은 이식됐으나 운영 체감 복구는 별도 검증 필요 |
| self_heal / self_revive | 부분 양호 | rule/sequence 존재하나 “실전에서 v1만큼 안정적”이라고 단정하기 이름 |
| attacker_revive / parhon / mujang / boho | 취약 | 기능은 있으나 중복 책임과 와이어링 리스크가 있음 |
| baekho / parlyuk / gyoungryeok | 부분 양호 | 로직은 있으나 cooldown/buff/토글 경계에서 회귀 소지가 남음 |
| geumgang | 취약 | sequence는 있으나 토글 매핑 버그와 정책 불명확성 존재 |
| cooldown uplink | 부분 | 역송 자체는 있으나 `buff_parlyuk_sec` 정밀도 저하 |
| attacker 측 송신/역수신 | 부분 양호 | F1/warp/burst/row resolve 존재하나 필드 네이밍 혼란이 큼 |
| compat facade | 과도기용 | 기능 보완 수단일 뿐 최종 구조로 보면 안 됨 |
| 순수 v2 GUI | 미완 | `V2MainWindow`는 존재하지만 실제 운영 경로 아님 |
| v2 독립성 | 부족 | 현재는 `src` 없이 단독 운영 가능한 상태가 아님 |
| 테스트 준비도 | 부족 | parity test는 많지만 현재 환경에선 pytest 미설치로 실행 불가 |

### 5.1 사용자 실전 체감 기준 재평가

코드 존재 여부만 보면 “대부분 이식됐다”고 말할 수 있다. 하지만 사용자 실전 피드백을 기준으로 보면 해석은 달라진다.

- v1: 실제 사냥/추종/시전이 이미 되는 코드
- v2: 구조는 좋아졌지만 **실제로 막히는 기능이 많아 운영 효용이 크게 떨어짐**

따라서 실전 기준 평가는 아래처럼 보는 것이 더 맞다.

- **코드 이식률:** 높음
- **구조 개선도:** 높음
- **실전 기능 복원도:** 낮음
- **독립 실행 가능성:** 낮음

즉 v2의 현재 문제는 “코드가 아예 없다”가 아니라,

> 코드가 있어도 실전에서 v1처럼 믿고 돌릴 수준으로 닫히지 않았다

는 것이다.

---

## 6. “v1 기능 전부가 v2에서 동작”하도록 만들기 위한 작업 명세

## 6.1 P0 — 바로 고쳐야 하는 것

### P0-1. 목표를 “compat 유지”가 아니라 “v1 런타임 의존성 제거”로 다시 고정

여기서 가장 중요한 수정은 분석 방향 자체다.

이전 문서의 “compat 경로를 당분간 공식 운영 경로로 인정” 같은 접근은 **사용자 목표와 맞지 않는다.**

사용자 목표는 다음이다.

- v1은 참조/백업용
- v2는 최종적으로 **독립 스크립트**여야 함

따라서 P0 최우선 과제는 아래처럼 바뀌어야 한다.

#### 독립화 완료 조건
1. 운영 엔트리포인트가 `src_v2` 내부 경로만 사용
2. 운영 중 `from src...` import 0건
3. `v1_compat` / `main_window_v2.py`가 운영 경로에서 제거됨
4. adapter가 `src` wrapper가 아니라 `src_v2` native 구현으로 대체됨

#### 즉시 해야 할 일
- `app/healer_gui_v2.py`, `app/attacker_v2.py`를 **순수 v2 경로**로 재배선
- `ui/main_window_v2.py`는 legacy/transition으로 격하하거나 제거 계획 수립
- `workers/v1_compat*`는 운영 의존 경로에서 제외

### P0-2. 금강불체 토글 버그 수정
- `healer_worker_v2.set_skill_enabled()`의 잘못된 매핑 수정
- `geumgang_enabled`를 독립 키로 둘지, manual-only로 고정할지 정책 확정

### P0-3. `PluginRegistry.instance()` 잘못된 호출 제거
- classmethod 방식으로 수정하거나, registry accessor를 실제로 구현
- 현재는 예외가 삼켜져서 “된 줄 알았는데 실제론 안 된 상태”가 발생 가능

### P0-4. 실제 buff duration 전달 복원
- `buff_parlyuk_active` bool만 보내지 말고
- 가능하면 snapshot/store/uplink에 **실제 `buff_parlyuk_sec`** 필드 추가

### P0-5. v2 native 구현으로 대체해야 할 대상 목록 고정

아래는 “나중에 보면 됨”이 아니라, 독립화를 위해 반드시 치워야 할 목록이다.

1. UI: `src.ui.*`
2. compat worker: `src_v2/workers/v1_compat*`
3. adapter 내부 `src.*` 래핑
4. 입력/캡처/OCR/UDP 핵심 경로의 `src.*` 직접 import

이 목록을 backlog가 아니라 **제거 대상 레지스터**로 관리해야 한다.

---

## 6.2 P1 — parity를 안정화하는 작업

### P1-1. attacker edge 계층 단일화

현재 아래 기능은 **integration_tick**와 **brain rules** 양쪽에 걸쳐 있다.

- attacker_revive
- parhon
- mujang
- boho

둘 중 하나로 정리해야 한다.

#### 권고안
- **integration_tick는 v1 호환 edge mirror 전용**
- **실제 cast 요청은 rule layer 한 곳에서만**

또는 반대로,

- **이 4개는 integration_tick 직결 기능**으로 선언하고
- 중복 rule 등록을 제거

핵심은 “한 기능의 truth가 한 군데만 있게 만들 것”이다.

### P1-2. attacker snapshot 필드명 정리
- attacker 측 자기 좌표/맵을 `healer_coord`, `healer_map`에 얹는 현재 convention은 혼동을 키운다.
- 최소한 wrapper/helper를 두어 의미를 분명히 해야 한다.

### P1-3. UI 경로 이중화 정리
- `main_window_v2.py`와 `v2_main_window.py` 중 어느 쪽이 운영용인지 결정
- 나머지는 experimental/legacy로 표기하거나 제거 계획 수립

### P1-4. 실전 기능 복구 우선순위를 v1 사용자 체감 순서로 재정렬

아키텍처보다 먼저 복구해야 하는 건 사용자가 바로 체감하는 운영 기능이다.

#### 1군: 추종 핵심
- attacker state 수신
- healer 좌표/맵 OCR
- follower / trail / map transition
- force-exit / jump-hold / stuck 회피

#### 2군: 생존 핵심
- self_heal
- self_revive
- attacker_revive
- tab_lock

#### 3군: 유지 스킬 핵심
- mujang
- boho
- parhon
- gyoungryeok
- baekho
- parlyuk

#### 4군: 부가 기능
- cooldown uplink
- xp 통계
- overlay / helper UI
- learning / alphago

즉 v2 복구 작업은 “레이어 순서”가 아니라 **실전 사냥에서 먼저 아픈 순서**로 진행해야 한다.

---

## 6.3 P2 — 검증 체계 닫기

### P2-1. pytest 실행 환경 먼저 복구
- `py -m pip install pytest`
- 이후 `py -m pytest src_v2/tests -q`

### P2-2. 코드 테스트 + 런타임 스모크를 분리

#### 코드 테스트
- parity phase1/phase2
- compat facade
- attacker parity
- contract tests

#### 런타임 스모크
- healer 시작 후 `[CFG-CONTRACT]` 로그 확인
- attacker 시작 후 UDP send/recv 확인
- BRAIN first fire 확인
- HANDS sequence first done 확인
- self_heal / parhon / mujang / boho / tab_lock 최소 1회 로그 검증

### P2-4. 독립화 검증 체크리스트 추가

테스트가 통과해도 아래가 안 되면 “독립 v2”라고 부르면 안 된다.

- 운영 엔트리포인트에서 `src_v2` 외 import 제거 여부
- 실행 중 compat facade 비경유 여부
- `src` 디렉터리를 치워도 최소 스모크가 가능한지 여부
- `src` 없이도 UI/worker/adapter가 뜨는지 여부

### P2-3. learning/alphago는 parity 닫힌 뒤 활성

현재 v2에는 memory/learning/alphago가 들어가 있다.

이건 미래 방향으로는 좋다. 하지만 parity가 닫히기 전에는 오히려 디버깅 표면적만 늘린다.

**권고:**
- v1 parity 100% 확인 전까지는 default disabled 유지
- 운영 이슈가 남아있는 동안은 코어 경로 진단이 우선

---

## 7. 추천 실행 순서

1. **목표 재정의**
   - compat 유지가 아니라 `src` 런타임 의존성 제거를 목표로 고정

2. **즉시 버그 수정**
   - geumgang toggle mapping
   - `PluginRegistry.instance()` 오류
   - parlyuk buff seconds 전달

3. **운영 경로 독립화**
   - `app/*_v2.py` → 순수 v2 UI/worker 경로 연결
   - `main_window_v2.py` / `v1_compat*` 운영 경로 제거
   - adapter의 `src.*` runtime import 제거 계획 수립

4. **책임 정리**
   - integration_tick vs rule 중복 제거
   - attacker snapshot naming 정리

5. **실전 기능 복구**
   - 추종 핵심 → 생존 핵심 → 유지 스킬 → 부가 기능 순서로 복구

6. **테스트 환경 복구**
   - pytest 설치
   - parity / contract / facade 테스트 실행

7. **운영 스모크**
   - healer/attacker 각각 30초 이상 가동
   - 로그 기반 기능별 발화 확인
   - 마지막으로 `src` 비의존 실행 여부 확인

---

## 8. 최종 요약

### v1 평가
v1은 구조는 지저분하지만, **운영 예외와 생존 로직이 모두 응축된 실전형 코드**다.

### v2 평가
v2는 구조 분해, watcher/brain/hands/muscle 분리, parity test 작성 등에서 **기술적으로 좋은 방향**을 갖고 있다.
하지만 사용자 실전 기준으로 보면 아직 **안 되는 기능이 너무 많고, 독립 실행 구조도 완성되지 않았다.**

즉 지금의 v2는:

- 설계 의도는 좋지만
- 기능 복구는 덜 됐고
- v1 의존성도 아직 남아 있는

**미완성 전환본**에 더 가깝다.

### CTO 관점 결론
지금 필요한 건 새 기능 추가가 아니라,

- 실전 기능 복구 우선순위 재정렬
- v1 런타임 의존성 제거
- 토글/와이어링 버그 수정
- 책임 중복 제거
- pytest + 런타임 스모크로 parity 닫기

이 4가지다.

그걸 끝내야만 v2는 “겉만 분리된 리라이트”가 아니라,
**v1을 실제로 대체하는 독립 운영 스크립트**가 될 수 있다.
