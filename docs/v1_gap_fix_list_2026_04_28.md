# v1 대비 정확히 구현 안 된 항목 + 수정 방법 총정리 (2026-04-28)

> 목적: `src`(v1)를 기준으로, `src_v2`에서 **의미가 틀어졌거나**, **사후 봉합 흔적이 있거나**, **아직 운영 검증이 덜 된 항목**을 추려서
> 1) 뭐가 정확히 문제인지
> 2) 왜 위험한지
> 3) 어떻게 고쳐야 하는지
> 를 한 번에 정리한다.

---

## 0. 제일 먼저 결론

현재 `src_v2`는 **완전히 구현 안 된 기능(MISSING)** 보다는,

- **한 번 이상 잘못 구현됐다가 수정된 항목**
- **구조는 있는데 운영 의미가 아직 불안한 항목**
- **v1과 동일하다고 단정하기엔 실환경 검증이 덜 된 항목**

이 더 많다.

즉 지금 진짜 해야 할 일은 “새 기능 추가”보다,

> **v1의 운영 의미를 정확히 보존하는 안정화 작업**

이다.

---

## 1. 최우선 수정/재검증 대상 CRITICAL 리스트

아래는 **v1 대비 정확 구현이 흔들렸거나, 운영상 사고가 컸던 항목**이다.

---

### 1-1. NumPad 스킬 송신 경로

## 문제
- v1: `src/input/numlock_cycle.py:press_normal_vk`, `press_numpad_direct`
- v2: 한동안 메인 키보드 숫자 VK를 직접 보내서 NumPad 스킬 전체가 오동작

## 왜 문제인가
- 백호, 파력, 파혼 등 핵심 스킬이 **아예 안 나가는 수준의 회귀**로 이어질 수 있음

## 현재 상태
- 코드상 사후 봉합됨
- 하지만 이건 “고쳤다”가 아니라 “한 번 크게 틀렸던 경로”다

## 해야 할 일
1. `tap_numpad_direct`, `_common.tap_numpad`, sequence들의 VK 사용 경로를 전수 검사
2. 스킬별로
   - 메인 숫자키 송신인지
   - NumPad direct 송신인지
   - NumLock lock 기반인지
   를 표로 확정
3. 회귀 테스트 추가
   - 백호
   - 파력무참
   - 파혼술
   - 부활

## 내가 고친다면
- `skill transport layer`를 하나로 모은다
- sequence마다 제각각 VK 처리하지 않게 하고,
- `MainDigit`, `NumPadLocked`, `NumPadDirect` 3모드 enum으로 강제한다

---

### 1-2. CooldownWatcher 빈 result 처리

## 문제
- v1: 직접 read/poll 기반이라 “이벤트 자체가 안 오는” 문제 구조가 약함
- v2: `cooldown_watcher`가 빈 result에서 publish를 안 하면 rule evaluation이 아예 사라짐

## 왜 문제인가
- ready 상태를 영원히 못 잡음
- 쿨 OCR이 빈 값일 때 룰이 아예 안 도는 구조적 결함 발생 가능

## 현재 상태
- 코드상 publish 보강됨
- 하지만 “빈 이벤트”와 “상태 신선도”가 동치가 아님

## 해야 할 일
1. `cooldown_watcher`에서 빈 result 시 snapshot에 어떤 기본 상태를 남길지 명시
2. `rule_engine`가
   - 이벤트 유무
   - 상태 최신성
   를 구분할 수 있게 해야 함
3. 테스트 추가
   - result `{}` 10회 연속
   - 이후 정상 값 1회
   - 룰 재평가 시점 확인

## 내가 고친다면
- publish payload에 `source_state = empty|observed|stale` 같은 메타 필드 추가
- 단순 `{}` publish 대신 **상태 품질까지 같이 publish**하게 만든다

---

### 1-3. HpMpWatcher 발행 정책

## 문제
- v2는 한동안 값이 바뀔 때만 publish
- 시작 시 이미 임계치 아래면 자힐/공증 룰이 영원히 안 돌 수 있었음

## 왜 문제인가
- 기능이 조용히 죽음
- 사용자는 “왜 안 쓰지?”만 느끼고 원인 추적 어려움

## 현재 상태
- 매 tick publish로 보강됨

## 해야 할 일
1. 시작 직후 임계치 아래인 상황을 명시 시나리오 테스트로 고정
2. hp/mp 둘 다 -1인 상태와 임계치 상태를 분리해 로그화
3. `publish happened`와 `meaningful reading happened`를 구분

## 내가 고친다면
- `eye.hp`, `eye.mp` 외에 `eye.hpmp_state`를 추가
  - observed
  - stale
  - unconfigured
  - rejected
  를 함께 보낸다

---

### 1-4. ControlCmd handler / send_control 경로

## 문제
- v2에서 한동안
  - control handler 등록 누락
  - `send_control()` dead path
  - UI fallback 추가 전에는 실제 송신 실패

## 왜 문제인가
- 격수 → 힐러 제어 명령이 아예 안 먹을 수 있음
- 이건 운영 제어의 근간이 죽는 것

## 현재 상태
- 코드상 봉합됨
- 하지만 핵심 경로가 한 번 죽어 있었다는 사실 자체가 리스크

## 해야 할 일
1. `worker active` / `worker inactive` / `idle listener only` 3상태에서
   control path를 시나리오 테스트화
2. `send_control()`가 adapter 메서드에 기대는지, fallback direct send인지 명시
3. UI에서 “송신 성공” 로그와 실제 수신측 반영 로그를 같은 trace id로 묶기

## 내가 고친다면
- ControlCmd 송신/수신을 별도 service로 분리
- facade와 worker가 각자 fallback 갖지 않게 함
- `control transport contract test`를 추가해
  “보냄/받음/적용됨” 3단계를 강제 검증한다

---

### 1-5. 격수 IP 동적 학습 / uplink sender

## 문제
- v1은 `recv.last_src_addr()` 기반 동적 학습
- v2는 한동안 static peers 의존으로 잘못 송신 가능

## 왜 문제인가
- 같은 cfg 재사용 환경에서 엉뚱한 힐러/자기 자신으로 송신 가능
- 힐러→격수 상태 표시가 비정상

## 현재 상태
- `_compat_uplink.UplinkSenderShim.set_attacker_addr` 보강됨

## 해야 할 일
1. peers 비어있음 / 잘못됨 / 동적 학습 성공 / 동적 학습 실패 시나리오별 테스트
2. uplink가 실제로 어느 IP로 보내는지 계약 로그 추가
3. `static peer fallback`이 발동하면 경고 로그를 남기게 변경

## 내가 고친다면
- uplink sender는 **dynamic learned addr 우선**을 설계상 강제하고,
- static peers는 bootstrap 용도라고 명확히 제한한다

---

### 1-6. `coord_tol` 동적 반영 경로

## 문제
- v1은 worker가 같은 ref를 직접 만지며 의미가 단순했음
- v2는 한동안 cfg copy 때문에 파력무참 버프 중 `coord_tol=1` 강제가 제대로 반영 안 될 위험

## 왜 문제인가
- 추종 정밀도가 버프 시점에 틀어짐
- 맵 전환/근접 조정 같은 민감 구간에서 체감 버그 유발 가능

## 현재 상태
- 수정 흔적 있음
- 하지만 여전히 읽는 사람 입장에서 경로가 투명하지 않음

## 해야 할 일
1. `coord_tol`의 진실 원본이 어디인지 단일화
2. 바뀐 값이 언제 muscle에 보이는지 테스트화
3. `parlyuk active → coord_tol=1 → expire → restore`를 end-to-end 시나리오로 고정

## 내가 고친다면
- `coord_tol`을 `SnapshotStore`나 전용 runtime config store에 올려
  worker/rule/muscle이 같은 truth를 보게 만든다

---

## 2. 중요하지만 CRITICAL 바로 아래급인 항목

### 2-1. ready_gate / ctx_provider wiring

## 문제
- `SkillExecutor`는 기능이 있지만 wiring 누락 시 v1 semantics가 깨짐

## 해야 할 일
- start 직후 cycler 초기 lock 완료 전까지 cast 유예가 실제 보장되는지 테스트
- verify pool이 비어 있을 때 경고만 찍고 끝나는지, 재시도 정책이 맞는지 검증

## 내가 고친다면
- executor 생성자에 required dependency로 강제
- `None이면 fallback` 같은 느슨한 패턴 제거

---

### 2-2. attacker cooldown report row/IP 매칭

## 문제
- `src_idx` 의존이 강하면 다중 힐러 환경에서 row 충돌 가능

## 해야 할 일
- peers 기반 row matching을 기본으로 올리고
- `reported_idx`는 참고값으로만 남겨야 함

## 내가 고친다면
- payload에 `resolved_row_idx`, `reported_idx`, `src_ip`를 모두 남기고
- UI는 항상 resolved 기준으로만 그리게 한다

---

### 2-3. attacker XP 흐름

## 문제
- 구조상 붙어는 있지만 watcher 체계 안에서 일관되게 보장되는지 약함

## 해야 할 일
- attacker 쪽 xp read / publish / overlay 반영을 독립 시나리오 테스트로 만들기

## 내가 고친다면
- xp도 다른 watcher처럼 명시 bus topic / freshness model을 부여한다

---

### 2-4. contract 로그 부족

## 문제
- `[CFG-CONTRACT]`는 좋아졌지만 아직 부족

## 해야 할 일
- publish contract
- control contract
- fallback activation
- dynamic addr resolution
까지 로그화

## 내가 고친다면
- 기능 로그와 계약 로그를 분리하고 prefix 체계를 통일한다

---

## 3. “코드상 있다”와 “운영상 끝났다”를 구분해야 하는 항목

아래는 존재 자체는 있지만, **운영 동치 선언 전 실증이 꼭 필요한 항목**이다.

- TAB-LOCK pending / post-heal-tab
- Follower trail follow / exit dash
- MAP-SEQ / MAP-SYNC / reversion debounce
- seq-rclick
- startup-s
- hpmp reject filter
- cooldown reverse receive

이 항목들은 코드로는 있어도,

> 30초~수분 단위 실환경에서 실제로 v1만큼 덜 흔들리는가

를 별도로 봐야 한다.

---

## 4. 우선순위별 액션 플랜

## P0 — 당장 해야 하는 것

1. ControlCmd end-to-end 검증
2. CooldownWatcher/HpMpWatcher publish contract 고정
3. NumPad transport 경로 표준화
4. `coord_tol` truth 단일화
5. uplink dynamic addr contract 검증

## P1 — 그다음

1. row/IP resolved matching 정리
2. XP 흐름 정리
3. fallback/no-op 경로 로그화
4. fixed-in-code vs verified-in-runtime 표 추가

## P2 — 최종 안정화

1. distributed state 축소
2. `MainLoop._dec_state`, `Follower` 상태 외화
3. ordering/race 통합 테스트 추가

---

## 5. 내가 책임자라면 구현 방식은 이렇게 간다

### A. 먼저 계약을 죽인다
- watcher publish contract
- control transport contract
- dynamic addr contract
- config reflection contract

### B. 다음에 상태를 줄인다
- `store`
- `scratch`
- `decision state`
- `follower state`

중 어디가 truth인지 고정

### C. 마지막에야 완전 대체 선언

그 전에는 정확한 표현은 이것뿐이다.

> **v2는 많이 따라왔지만, 아직 v1의 운영 의미를 100% 안전하게 대체했다고 선언할 단계는 아니다.**

---

## 6. 최종 정리 — v1 대비 정확 구현 안 된 리스트 핵심본

### 정확히 구현 안 됐거나, 한 번 이상 의미가 틀어졌던 핵심 리스트

1. NumPad 스킬 transport 경로
2. CooldownWatcher 빈 result publish semantics
3. HpMpWatcher 매 tick publish semantics
4. ControlCmd handler / send_control 실제 송수신 경로
5. 격수 IP 동적 학습 기반 uplink sender
6. `coord_tol` 동적 반영 경로
7. ready_gate / ctx_provider wiring
8. attacker row/IP resolved matching
9. attacker XP 흐름 일관성
10. 계약 로그/운영 로그 분리 부족

### 한 줄 결론

**v1 대비 “완전히 없다”기보다, “의미를 한 번 이상 틀렸고 사후 봉합으로 맞춘 항목들”이 핵심 문제다.**

그래서 지금 필요한 건 새 기능이 아니라, **운영 의미 보존을 끝까지 잠그는 안정화 작업**이다.