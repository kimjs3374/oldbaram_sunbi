# CTO 작업지시서 — v1 / v2 기능 동기화 복구 계획 (2026-04-28)

> 목적: `src`(v1)의 실제 운영 의미를 `src_v2`에 **정확히 동기화**한다.
> 이 문서는 감상문이 아니다. 누가 뭘 언제 어떻게 검증해서 끝낼지 박는 **실행 지시서**다.

---

## 0. 총평

지금 `src_v2`의 상태는 “구조는 그럴듯하게 분해했지만, 운영 의미를 끝까지 책임지지 못한 코드”다.

문제는 단순하다.

- 기능을 옮겼다고 주장했지만, 실제론 의미를 틀리게 옮긴 경로가 있었다.
- 핵심 경로가 죽어 있었고, 사후 봉합 흔적이 반복된다.
- 테스트는 늘었지만, 운영 동치성 검증은 아직 부족하다.

이제부터 해야 할 일은 새 기능 자랑이 아니다.

> **v1의 운영 의미를 항목별로 동결하고, v2가 그 의미를 정확히 재현하는지 계약/시나리오/로그로 닫는 것**

이다.

그리고 더 명확히 말한다.

지금 상황은 단순한 “조금 덜 완성된 리라이트”가 아니다.

- 출시 일정이 실제로 밀렸다.
- 대표와 주주가 일정 신뢰를 잃고 있다.
- 개발 리소스가 신규 가치 창출이 아니라 회귀 봉합에 소모되고 있다.
- 운영 의미를 문서/계약으로 고정하지 않은 채 구조 분해부터 한 대가를 조직이 치르고 있다.

이건 기술적 아쉬움 수준이 아니라 **사업 손실을 만든 실행 실패**다.

즉 이 문서의 목적은 기분 나쁜 평가를 적는 게 아니라,

> **왜 일정이 밀렸는지, 왜 조직이 추가 비용을 치르고 있는지, 그리고 무엇을 멈추고 무엇을 강제로 바로잡아야 하는지 선언하는 것**

이다.

---

## 1. 작업 원칙

1. **“코드가 있다”는 완료 기준이 아니다.**
   - 완료 기준은 `fixed-in-code`가 아니라 `verified-in-runtime`이다.

2. **모든 작업은 v1 SoR(Source of Record) 기준으로 한다.**
   - 해석이 갈리면 v1 실제 동작과 로그를 우선한다.

3. **fallback / no-op / BUG-FIX 주석으로 덮는 방식 금지.**
   - 원인을 구조적으로 제거한다.

4. **모든 수정은 계약 테스트 + 시나리오 테스트 + 로그 근거를 남긴다.**

5. **새 기능 개발 금지.**
   - 동기화 완료 전까지 신규 기능, 미학적 리팩토링, 확장 작업 금지.

6. **주장 금지, 증명만 허용.**
   - “옮겼다”, “동작한다”, “재현 안 된다”는 말은 증거 없으면 무효다.

7. **운영 의미를 모르는 상태에서 분해 금지.**
   - 의미를 먼저 고정하지 않은 분해는 리팩토링이 아니라 손실 전파다.

---

## 1.1 즉시 중단시킬 행동

아래 행동은 즉시 중단한다.

1. **버그를 fallback으로 덮는 습관**
   - primary path가 죽었는데 fallback으로 “대충 되게” 만든 뒤 완료 처리하는 행동 금지.

2. **운영 증거 없이 EQ 판정하는 행동**
   - 파일이 존재한다는 이유로 v1 동치라고 쓰는 행위 금지.

3. **로그 없는 수정**
   - 핵심 경로 수정인데 before/after 로그, 테스트, 재현 조건 없는 보고 금지.

4. **원인 미확정 상태의 광범위 수정**
   - 문제를 모르는데 여기저기 찔러 넣는 식 패치 금지.

5. **성능 비용 무시**
   - YOLO/OCR spike가 명백한데 기능만 맞추려는 시도 금지.

---

## 1.2 이번 실패의 구조적 원인

이번 일정 지연은 개별 버그 몇 개 때문이 아니다. 구조적으로 아래 6개가 겹쳤다.

1. **운영 contract를 문서화하지 않고 리라이트 착수**
2. **상태 owner를 고정하지 않고 다층 state를 허용**
3. **watcher publish contract를 명시하지 않음**
4. **transport/control 경로를 단일 책임으로 정리하지 않음**
5. **fixed-in-code와 verified-in-runtime를 구분하지 않음**
6. **성능 budget 없이 YOLO/OCR 파이프라인을 얹음**

즉, 이번 실패는 “조금 덜 꼼꼼했다”가 아니라,

> **개발 순서와 통제 방식 자체가 잘못된 결과**

다.

---

## 2. P0 — 즉시 착수 / 완료 전 배포 금지

### P0-0. 출시 차단 선언 (Stop-Ship)

## 선언
아래 중 하나라도 미해결이면 배포 금지다.

- UDP bind 재기동 실패 재현 가능
- ControlCmd end-to-end 100% 미검증
- gyoungryeok 미발화 원인 미확정
- watcher publish contract 미고정
- MP OCR 급반등/급락 노이즈 해석 미고정
- YOLO 지연 P95 목표 미정

## 이유
이 상태에서 출시하는 건 “기능 부족”이 아니라 **운영 실패를 예정된 상태로 배포하는 것**이다.

---

### P0-1. UDP 수신 경로 안정화

## 문제
- 재기동 시 `udp bind 30회 실패`
- `adapter=None — udp 비활성`
- 이 상태에선 attacker state 입력이 통째로 사라짐

## 지시
1. `UdpReceiver`/adapter lifecycle 전수 점검
2. stop 이후 socket release 완료 시점 명확화
3. 재기동 race 제거
4. bind 실패 시 “기능 제한 모드”가 아니라 **worker 기동 실패**로 승격 검토

## 완료 기준
- 20회 연속 stop/start 반복에서 bind 실패 0회
- attacker state first receive 20회 연속 확인
- 로그에 `adapter=None — udp 비활성` 재현 0회

## 산출물
- 계약 테스트: restart bind race
- 시나리오 테스트: healer stop/start loop
- 로그 첨부

---

### P0-2. ControlCmd end-to-end 복구

## 문제
- 과거 `set_control_handler` 누락
- `send_control()` dead path 존재
- UI fallback 없으면 제어 명령 실패 가능

## 지시
1. control 송신/수신/적용 경로를 단일 service로 재정렬
2. facade fallback 제거 또는 명시적 1경로화
3. trace id 붙여 송신→수신→적용 3단계 로그 연결

## 완료 기준
- `start/stop/follow_on/follow_off` 전 명령 4종 성공률 100%
- worker active/inactive/idle listener 상태 모두 통과

## 산출물
- 계약 테스트: control transport
- 시나리오 테스트: UI command matrix

---

### P0-3. watcher publish contract 고정

## 문제
- cooldown 빈 result publish 누락 이력
- hp/mp 값 변화시에만 publish 하던 이력
- 이벤트 기반 구조에서 publish가 멈추면 기능이 조용히 죽음

## 지시
1. `cooldown_watcher`, `hpmp_watcher`에 상태 품질 메타 추가
   - observed / empty / stale / rejected / unconfigured
2. “이벤트 유무”와 “상태 신선도”를 분리
3. rule_engine가 상태 품질을 해석 가능하게 수정

## 완료 기준
- empty result 연속 20회에서도 evaluation heartbeat 유지
- 시작 시 임계치 이하 상태에서 self_heal / gyoungryeok 평가 즉시 발생

## 산출물
- 계약 테스트: publish-on-empty
- 계약 테스트: publish-on-no-change
- 진단 로그 prefix 추가 (`[EYE-CONTRACT]`)

---

### P0-4. MP 기반 공력증강(gyoungryeok) 미발화 문제 해결

## 문제
- 최신 로그에서 MP가 임계 이하로 떨어지는데 `gyoungryeok` rule fire/sequence 흔적 없음

## 지시
1. rule 조건
2. cooldown gate
3. in_progress 차단
4. cfg overlay
5. publish topic 연결
를 전부 역추적

## 완료 기준
- MP threshold 하강 시 `rule fired name=gyoungryeok` 확인
- sequence start/done까지 10회 반복 검증
- OCR 튐/노이즈 환경에서도 최소한 skip reason 로그 남김

## 추가 지시
- 이 항목은 단순 버그가 아니라 **현재 로그상 실제 기능 미작동 의심**이다.
- 따라서 “나중에 보자” 항목으로 내리지 마라. P0 유지.

## 산출물
- 원인 분석 노트
- 회귀 테스트
- skip reason 로그 (`[BRAIN-SKIP]`)

---

### P0-5. NumPad 스킬 transport 표준화

## 문제
- 과거 메인 숫자키 직송신으로 NumPad 스킬 오동작

## 지시
1. 모든 sequence의 VK transport 모드 전수 표 작성
2. transport layer 단일화
3. `MainDigit / NumPadLocked / NumPadDirect` enum 강제

## 완료 기준
- 백호 / 파력 / 파혼 / 부활 / 공증 시퀀스 모두 transport mode 명시
- 회귀 테스트 전부 통과

---

## 3. P1 — 운영 품질 복구

### P1-1. buff OCR 품질 개선

## 문제
- `CD-OCR-MISS` 반복
- raw_lines가 `스`, `금강불체...` 등으로 오염

## 지시
1. buff region 재검증
2. 전처리/라인 분리/키워드 매칭 개선
3. `pending skill list`와 `raw_lines`를 샘플 저장

## 완료 기준
- 파력무참/백호 관련 버프 OCR 성공률 측정
- 최소 5세션 표본에서 miss rate 보고

---

### P1-2. YOLO 과부하 완화

## 문제
- healer/attacker 로그 모두 `YOLO-SPIKE` 과다
- 200~600ms 스파이크 다수

## 지시
1. frame size / imgsz / poll_sec / batching 재조정
2. profiler 로그 기준 병목 분리
3. red/white tab 검출 목표 latency 정의

## 완료 기준
- P95 predict latency 목표치 수립 및 달성
- white/red tab detection miss rate 측정

## 추가 지시
- 성능 목표 없이 기능만 맞췄다고 주장하지 마라.
- 이번 건은 정확도 문제이기도 하지만 **latency 문제**이기도 하다.
- P50/P95/P99를 모두 보고해라.

---

### P1-3. `coord_tol` truth 단일화

## 문제
- copy/ref 혼선 이력
- parlyuk active 시 동적 반영 경로가 불투명

## 지시
1. runtime config store 단일화
2. muscle/rule/integration_tick이 동일 truth를 보게 수정

## 완료 기준
- `parlyuk active → coord_tol=1 → expire restore` 시나리오 테스트 통과

---

### P1-4. attacker row/IP resolved matching 고정

## 문제
- 다중 힐러 환경에서 `src_idx`만 믿으면 row 충돌 위험

## 지시
1. resolved_row_idx 개념 도입
2. `reported_idx`는 참고값으로만 사용

## 완료 기준
- peers reorder / wrong idx / same subnet 환경 테스트 통과

---

### P1-5. stop 이후 잔여 작업 제거

## 문제
- stop 후에도 XP-OCR, CD-OCR miss 로그가 남아 잔여 thread/work item 의심

## 지시
1. stop sequence를 단계별로 분해한다.
2. watcher/thread/queue/timer 잔존 여부를 inventory로 출력한다.
3. stop 이후 추가 emit 0건을 강제한다.

## 완료 기준
- stop 후 3초 이내 watcher/thread 잔존 0개
- stop 후 추가 OCR/YOLO/CD 로그 0건

---

## 4. P2 — 구조 부채 청산

### P2-0. v1 블루프린트 기반 설계 재도입

## 문제
- v2는 모듈은 잘게 쪼갰지만, 정작 **무엇이 핵심 운영 contract인지**를 코드 구조로 못 박지 못했다.
- 그 결과 watcher / rule / sequence / transport / state 동기화가 사람 머릿속 암묵지에 의존했다.
- 즉 지금 상태는 “폴더는 예쁜데 의미는 흩어진 구조”다.

## 지시
1. v1의 핵심 운영 의미를 아래 5개 블루프린트로 재정의한다.
   - **Signal Blueprint**: 어떤 입력 신호가 언제 publish/consume 되는가
   - **Rule Blueprint**: 어떤 조건에서 어떤 action request가 생성되는가
   - **Sequence Blueprint**: action request가 실제 어떤 키 입력 절차로 실행되는가
   - **Transport Blueprint**: UDP / ControlCmd / CooldownReport 가 어떤 계약으로 오가는가
   - **State Blueprint**: 어떤 상태가 단일 truth이며, 누가 owner 인가
2. 각 블루프린트는 코드보다 먼저 Markdown/YAML 표준본으로 고정한다.
3. v2 코드는 반드시 이 블루프린트의 field/contract를 따라가게 리팩토링한다.

## 완료 기준
- 블루프린트 문서 5종 작성 완료
- 신규/기존 코드가 임의 필드명/임의 흐름을 만들지 못하게 schema 수준으로 고정
- 테스트명이 블루프린트 항목명을 그대로 반영

---

### P2-1. distributed state 축소

현재 흩어진 상태:
- SnapshotStore
- DecisionScratch
- MainLoop._dec_state
- Follower 내부 상태
- facade pending state

## 지시
1. 상태를 `runtime truth / derived state / ephemeral scratch` 3층으로 재분류
2. 각 키마다 소유자(owner) 1개 지정
3. 중복 캐시 제거

## 완료 기준
- 상태 사전(key inventory) 문서화
- owner 없는 상태키 0개

---

### P2-2. fixed-in-code / verified-in-runtime 분리 관리

## 지시
모든 항목을 아래 두 열로 관리한다.

- fixed-in-code
- verified-in-runtime

코드 수정만 끝난 항목을 완료 처리하지 마라.

---

### P2-3. 리팩토링 지시 — 유지보수 가능 구조로 재편

## 문제
- 지금 v2는 기능 추가보다 **기능 의미 보존**이 더 중요하다.
- 그런데 현재 구조는 유지보수자가 코드를 읽을 때
  - truth가 어디인지
  - fallback이 언제 발동하는지
  - 어떤 rule이 어떤 watcher에 의존하는지
  즉시 말하기 어렵다.

## 지시

### A. watcher 계층 리팩토링
1. 모든 watcher는 공통 베이스를 상속한다.
   - `poll()`
   - `normalize()`
   - `publish_contract()`
   - `quality_state()`
2. 각 watcher는 payload 외에 품질 메타를 반드시 포함한다.
3. publish skip 금지. skip이 필요하면 이유를 이벤트로 남긴다.

### B. rule 계층 리팩토링
1. 각 rule은 다음 4개를 명시 선언한다.
   - subscribed topics
   - required fields
   - skip reasons
   - emitted request
2. `if 조건 덩어리`를 함수로 쪼개지 말고, `RuleSpec` 데이터로 드러낸다.
3. 모든 rule skip은 `[BRAIN-SKIP] rule=<name> reason=<reason>` 로그를 남긴다.

### C. sequence 계층 리팩토링
1. sequence는 transport mode를 반드시 선언한다.
   - MainDigit
   - NumPadLocked
   - NumPadDirect
2. precondition / body / verify / cleanup 4단계 구조를 강제한다.
3. ESC 복귀, target restore, movement lock 해제 같은 후처리를 공통 훅으로 뺀다.

### D. network 계층 리팩토링
1. `ControlCmd`, `State`, `CooldownReport`는 transport service에서만 송수신한다.
2. facade/worker/UI가 각자 socket fallback 갖는 구조 금지.
3. 동적 peer 학습/row resolve는 transport service의 책임으로 올린다.

### E. state 계층 리팩토링
1. 상태를 아래 4개로 강제 분리한다.
   - `RuntimeSnapshot`: 관측 truth
   - `DecisionState`: 이동/판단용 파생 상태
   - `SequenceScratch`: 단기 실행 scratch
   - `ConfigRuntime`: 동적 cfg truth
2. 동일 의미 상태를 두 군데 저장하는 구조 금지.
3. 상태 owner와 갱신 지점을 문서화한다.

### F. UI / facade 계층 리팩토링
1. UI는 “명령 발행자”만 하고, 운영 의미 해석 금지.
2. facade는 호환층 역할만 하며 business logic 금지.
3. v1_compat는 재사용 경로이지, 신규 의미 누더기 patch 적치장이 되어선 안 된다.

### G. blueprint 우선 개발 프로세스 강제
1. 기능 추가/수정 전 Blueprint diff 먼저 작성
2. 코드 diff는 blueprint diff 승인 후만 진행
3. 테스트 명세는 blueprint 항목 번호를 따라간다

## 완료 기준
- watcher / rule / sequence / transport / state 책임이 코드 구조로 식별 가능
- 신규 인력이 파일 1~2개 읽고도 흐름을 역추적 가능
- fallback/no-op 경로가 문서 없이도 코드에서 바로 보임

---

## 4.1 경영 리스크 관점 추가 지시

기술팀은 아래 항목을 매일 보고한다.

1. **출시 차단 항목 잔여 개수**
2. **P0 완료율**
3. **재현 가능한 blocker 수**
4. **성능 지표(P50/P95/P99)**
5. **fixed-in-code 대비 verified-in-runtime 비율**

이유는 간단하다.

> 지금 필요한 건 “열심히 하고 있다”는 감정 보고가 아니라,
> **출시 가능성이 매일 올라가고 있는지 숫자로 보여주는 것**

이다.

---

## 4.2 책임 분리 지시

한 사람이 다 한다고 아무도 책임지지 않는 상태를 금지한다.

- Vision Owner: YOLO/OCR/HPMP/CD/buff
- Transport Owner: UDP/ControlCmd/CooldownReport/peer resolve
- Brain Owner: rule/priority/skip reason/contract
- Muscle Owner: follower/main_loop/coord_tol/stuck
- Runtime Owner: thread lifecycle/stop/restart/resource cleanup
- QA Owner: scenario/golden/perf/runtime verification

각 owner는 자기 영역의 P0/P1 blocker를 숫자로 보고한다.

---

## 5. 기능별 세부 체크리스트

### healer 핵심
- [ ] self_heal
- [ ] self_revive
- [ ] gyoungryeok
- [ ] baekho
- [ ] parlyuk
- [ ] parhon
- [ ] tab_lock
- [ ] seq_rclick

### 추가 기능 리스크 지적 및 지시

아래 항목들은 “당장 죽어 있는 건 아닐 수 있지만”, 런칭 직전 기준으로 **조용히 사고를 만들 가능성이 높은 기능 리스크**다.
이건 전부 따로 확인하고 닫아라.

#### 5-A. startup-s / foreground 의존성
- 문제: 시작 직후 `'s'` 키 1회 송신은 들어가 있지만, fg 감지 실패/지연 시 침묵 실패 가능
- 지시:
  1. fg mismatch / hwnd invalid / delayed focus 시나리오 테스트 추가
  2. 송신 성공/실패를 명시 로그로 남겨라
  3. 재시도 정책이 있으면 횟수와 종료 조건을 문서화해라

#### 5-B. red/white tab 판정 불균형
- 문제: 최신 로그에서 red는 계속 잡히는데 white는 장시간 0건일 수 있음
- 지시:
  1. white detection threshold / class mapping / frame timing 재검증
  2. red-only 지속 상황에서 tab-confirm / pause 경로가 어떻게 되는지 명시
  3. `red_present`, `white_present`, `red_count`, `white_count`를 통합 메트릭으로 남겨라

#### 5-C. HP/MP OCR 급반등/급락 노이즈
- 문제: MP 값이 0 근처 ↔ 100%로 튀는 경우가 있음. HP는 reject가 있으나 MP는 상대적으로 취약
- 지시:
  1. MP reject / smoothing / pending 정책을 HP 수준으로 끌어올려라
  2. 1프레임 오탐과 실제 고갈을 구분하는 state machine을 넣어라
  3. 공증/자힐 룰이 노이즈를 어떻게 해석하는지 테스트로 잠가라

#### 5-D. cooldown/buff OCR 상호오염
- 문제: buff OCR이 금강불체 등 다른 텍스트를 읽으면서 핵심 버프 추적 실패
- 지시:
  1. cooldown slot / buff slot을 독립 진단하라
  2. `pending skill`별 raw crop dump를 자동 저장하게 해라
  3. OCR miss를 단순 카운트가 아니라 skill별 miss rate로 집계해라

#### 5-E. self_heal / self_revive 동시성
- 문제: HP edge / 죽음 edge / movement lock / white tab / map pause가 겹칠 때 실제 우선순위가 안전한지 불명확
- 지시:
  1. HP 0 진입 직전/직후 자힐과 자가부활 경쟁 시나리오 작성
  2. revive 중 self_heal 재진입 금지 확인
  3. sequence cleanup 누락 시 movement lock 잔류 여부 확인

#### 5-F. parhon / revive / tab_lock 우선순위 충돌
- 문제: 여러 rule이 같은 시점에 fire 가능할 때 지금 우선순위가 v1 운영 의미와 완전히 같은지 확증 부족
- 지시:
  1. 동시 trigger matrix를 작성해라
  2. priority 충돌 시 실제 선택 결과를 golden test로 고정해라
  3. “왜 이 규칙이 이긴 건지”를 로그에서 보이게 해라

#### 5-G. follow_only / armed / role 전환
- 문제: UI 토글/라디오와 worker 내부 상태가 미묘하게 어긋나면 조용한 오동작 가능
- 지시:
  1. role change / armed off / follow_only on-off 전환 시 상태 반영 타이밍 검증
  2. UI 표시와 snapshot/runtime state 일치 여부를 계약 로그로 남겨라

#### 5-H. xp/analytics 부가기능의 부작용
- 문제: xp/analytics는 핵심 기능은 아니지만 OCR/IO 부하를 추가해 본체 타이밍에 악영향 가능
- 지시:
  1. xp watcher on/off 성능 차이 측정
  2. analytics write가 main 감지 루프를 방해하지 않는지 확인
  3. 부가기능은 default-off 또는 degrade-safe 설계 검토

#### 5-I. stop 이후 잔여 스레드/잔여 OCR 작업
- 문제: worker stop 뒤에도 XP-OCR / CD-OCR miss 로그가 남는 경우가 보임
- 지시:
  1. stop 이후 watcher/thread 완전 정지 여부 확인
  2. stop 후 추가 emit 0건을 완료 기준으로 잡아라
  3. lingering thread inventory를 로그로 남겨라

#### 5-J. 로그 역할 분리 실패
- 문제: healer_v2 로그에 attacker 성격 로그가 섞이면 장애 분석 비용 폭증
- 지시:
  1. healer / attacker / shared transport / OCR 를 prefix와 파일 단위로 분리
  2. 최소한 role, worker_id, session_id를 전 로그에 박아라

#### 5-K. fallback 경로의 침묵 성공/침묵 실패
- 문제: fallback이 있는 건 나쁘지 않지만, 어떤 경로가 실제 실행됐는지 모르면 디버깅 불가
- 지시:
  1. fallback activation은 무조건 계약 로그 남겨라
  2. primary path / fallback path 성공률을 따로 집계해라

#### 5-L. config 값과 실동작 괴리
- 문제: `CFG-CONTRACT` 로그는 좋아졌지만, 실제 runtime에서 그 값이 정말 소비되는지까지는 보장 안 됨
- 지시:
  1. 주요 파라미터는 “configured value / effective runtime value” 둘 다 찍어라
  2. `coord_tol`, `self_heal_hp_thr`, `gyoungryeok_mp_thr`, `parlyuk_offset_sec`는 필수 추적 대상

### network
- [ ] State recv
- [ ] ControlCmd recv
- [ ] ControlCmd send
- [ ] CooldownReport uplink
- [ ] row/ip resolve

### vision
- [ ] coord/map OCR
- [ ] hpmp OCR
- [ ] cooldown OCR
- [ ] buff OCR
- [ ] red/white tab YOLO

### movement/follower
- [ ] MAP-SEQ
- [ ] MAP-SYNC
- [ ] trail follow
- [ ] force-exit
- [ ] stuck blacklist
- [ ] coord_tol dynamic reflect

### 추가 위험 기능 체크
- [ ] startup-s foreground 의존성 검증
- [ ] red/white tab 검출 불균형 해소
- [ ] MP reject / smoothing 정책 보강
- [ ] cooldown/buff OCR slot 분리 검증
- [ ] self_heal / self_revive 동시성 검증
- [ ] parhon / revive / tab_lock 우선순위 충돌 검증
- [ ] follow_only / armed / role 전환 동기화 검증
- [ ] xp/analytics 부하기여도 측정
- [ ] stop 후 잔여 thread / emit 0건 보장
- [ ] fallback activation 로그화
- [ ] configured vs effective runtime value 검증
- [ ] stop 후 잔여 watcher/thread 0건 보장
- [ ] P50/P95/P99 latency 리포트
- [ ] release blocker dashboard 작성

### blueprint / refactoring
- [ ] Signal Blueprint 문서화
- [ ] Rule Blueprint 문서화
- [ ] Sequence Blueprint 문서화
- [ ] Transport Blueprint 문서화
- [ ] State Blueprint 문서화
- [ ] watcher base class 도입
- [ ] rule skip reason 표준화
- [ ] sequence transport mode 표준화
- [ ] network transport service 단일화
- [ ] runtime state owner 표 정리

---

## 6. 보고 형식 강제

앞으로 보고는 아래 형식만 허용한다.

### 형식
1. 문제
2. v1 SoR
3. v2 현재 상태
4. 수정 내용
5. 계약 테스트 결과
6. 런타임 검증 결과
7. 남은 리스크

### 금지 표현
- “아마 됨”
- “구조상 문제 없어 보임”
- “일단 돌아감”
- “대충 맞는 듯”

이딴 표현은 금지다. 증거 없는 낙관은 보고가 아니라 방해다.

---

## 7. 최종 완료 정의

다음이 충족되기 전까지 `v2가 v1과 동기화 완료`라고 말하지 마라.

1. P0 전부 완료
2. P1 핵심 지표 수집 완료
3. 기능별 체크리스트 전 항목 fixed-in-code 완료
4. 그중 핵심 운영 항목 runtime verification 완료
5. 30초+ 반복 세션에서 재현 실패 사례 0건
6. Stop-Ship 항목 0개
7. fixed-in-code / verified-in-runtime 괴리 항목 0개

---

## 8. 마지막 지시

이 작업의 핵심은 멋있게 다시 짜는 게 아니다.

> **v1이 왜 현장에서 버텼는지 이해하고, 그 운영 의미를 v2에서 한 항목도 빼먹지 않고 복구하는 것**

이다.

지금 필요한 건 포장, 변명, 미학이 아니다.

**증거, 계약, 재현성, 운영 동치성. 이 네 개로만 말해라.**

그리고 하나 더 추가한다.

**리팩토링은 폴더 정리 놀이가 아니다. 블루프린트 없이 쪼개놓고 운영 의미를 잃어버리는 리팩토링은 개선이 아니라 손실이다.**

이번엔 다르다. 

**v1의 의미를 블루프린트로 먼저 박고, 그다음 코드가 그걸 따르게 만들어라.**

마지막으로 분명히 한다.

이번 일은 “조금 불편한 야근”으로 끝나는 문제가 아니다.

- 일정이 밀렸고
- 신뢰가 깎였고
- 회사가 비용을 냈다.

그러니 앞으로는 “수정했다”가 아니라,

> **왜 이 문제가 다시는 회사 비용으로 돌아오지 않는지**

를 증명하는 수준으로 일해라.