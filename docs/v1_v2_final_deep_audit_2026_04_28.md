# v1 → v2 끝판 감사보고서 (2026-04-28)

> 기준: `src/`를 먼저 실전 기준 원본으로 보고, `src_v2/`를 그 대체 시도물로 평가한다.
> 이 문서는 기존 마크다운 주장보다 **실제 코드와 일부 운영 로그**를 우선 근거로 삼는다.

---

## 0. 최종 판정

결론부터 직설적으로 적는다.

> **`src_v2`는 구조적으로는 훨씬 세련됐지만, 아직 `src`를 “이겼다”고 선언할 단계의 코드가 아니다.**

더 구체적으로는:

- `src`는 추하고 무겁고 한 파일에 다 몰려 있지만,
  **운영 중 맞아 죽으면서 살아남은 코드**다.
- `src_v2`는 분리·추상화·테스트라는 좋은 도구를 가져왔지만,
  **핵심 운영 경로를 처음부터 완전히 닫지 못했고, 사후 패치 흔적이 매우 많다.**

즉 심사 관점에서 보면,

- `src` 작성자는 미학은 포기했어도 **운영 완결성**을 쌓아 올렸고,
- `src_v2` 작성자는 구조 감각은 보여줬지만 **완료 책임감**은 아직 덜 증명했다.

내가 최종 심사권자라면, 현 시점에서 더 높은 점수는 **구조적 야심**보다
**운영 생존력**에 준다.

---

## 1. v1을 먼저 보고 내린 판단

`src`의 핵심은 단순하지 않다. 실제로 다음 축들이 긴밀하게 물려 있다.

### 1.1 힐러 워커는 사실상 운영 제어탑이다

`src/workers/healer_worker.py`는 단순 루프가 아니다.

- armed / follow_only 상태 관리
- 자힐 / 자가부활 / 공증 HP/MP edge 감지
- 격수 UDP edge 감지
- movement lock stuck 감시
- startup `s` 키 송신
- parlyuk 시 `coord_tol` 강제/복원
- pending tab lock
- seq-rclick target 유지
- stuck blacklist / reset history
- cooldown OCR / buff OCR / chat OCR / hpmp / xp / uplink

이건 코드 품질 관점에선 ugly monolith다. 하지만 운영 관점에선 다르게 읽힌다.

> **문제가 실제로 어디서 났는지 알고 있고, 그 문제를 코드에 박아 넣은 흔적**

이 굉장히 많다.

### 1.2 입력 계층은 “키 누르기”가 아니라 운영 semantics 자체를 담고 있다

`src/input/skill_scheduler.py`, `skill_blueprints.py`, `target_sequence.py`, `numlock_cycle.py`를 보면,
v1은 단순 스킬 트리거가 아니라 다음 semantics를 갖는다.

- edge-trigger queue
- ready gate
- verify/retry
- blocks_movement
- pre/post block hook
- self-target / restore target 시퀀스
- NumLock 토글 lock/unlock
- Shift 조합을 게임 해석 방식에 맞춰 분리 송신

즉 `src`의 입력 계층은 조잡하지만, **실제 게임 입력 의미를 정확히 붙잡기 위해 매우 집요하게 커졌다.**

### 1.3 Follower / FSM은 운영 노이즈에 맞서 자란 코드다

`src/fsm/controller.py`는 단순 추종 FSM이 아니다.

- MAP-SEQ edge
- MAP-SYNC
- reversion debounce
- healer map bbox mismatch reject
- jump reject
- fresh guard
- exit_dir inheritance
- trail push/reject
- pause / tab-confirm cancel

이건 설계서에서 보기 좋은 코드가 아니라, **오탐·노이즈·OCR 지연·맵 flicker를 실제로 맞아본 코드**다.

### 1.4 attacker 쪽도 부가기능까지 포함하면 생각보다 두껍다

`src/app/attacker.py`, `src/workers/attacker_worker.py`는 단순 위치 송신기가 아니다.

- F1 pending
- warp detection
- map burst
- own cooldown OCR
- buff/debuff OCR
- XP / analytics
- HP/MP OCR
- sticky red TTL
- timeBeginPeriod/timeEndPeriod
- cooldown reverse receive + overlay emit

즉 v1은 보기엔 난잡하지만, **기능면에서 비어 있는 코드가 아니라 운영 기능까지 다 얹힌 코드**다.

---

## 2. v2를 보고 인정해야 하는 점

`src_v2`는 빈 껍데기 리라이트는 아니다. 이건 분명히 인정해야 한다.

### 2.1 구조 분리는 진짜다

`HealerWorkerV2`를 보면 실제로

- eyes
- brain
- hands
- muscle
- memory
- learning
- ui

가 코드로 조립된다.

즉 “분리했다”는 말은 허풍이 아니다.

### 2.2 movement lock, integration tick, follower trail, attacker sender 등 핵심 축은 실제 구현돼 있다

지금 코드 기준으로는 다음이 존재한다.

- `InputDispatcher` movement lock / stuck release
- `integration_tick`의 attacker edge / tab-lock / post-heal-tab / parlyuk tol
- `muscle/main_loop.py`의 B1/B2/B3/STUCK/BL
- `attacker_worker_v2.py`의 F1 edge / warp / burst / cooldown reverse receive

즉 “핵심 트리거가 아예 없다”는 식의 비판은 틀리다.

### 2.3 8.1 보완 작업도 실제 반영된 건 맞다

`v1_vs_v2_audit_8_1_final_2026_04_28.md`가 주장한 것 중 내가 직접 확인한 건 사실이다.

- `v1_compat.py` 분할
- `DecisionScratch` 도입
- `extras` ref 공유
- `[CFG-CONTRACT]` 로그
- `test_contract_*` 추가

즉 보완 작업 보고 자체를 “뻥문서”라고 몰아붙이는 건 정확하지 않다.

---

## 3. 그런데도 왜 v2가 아직 덜 끝난 코드로 보이는가

이제 핵심 비판이다.

### 3.1 BUG-FIX / 누락 수정 / fallback 흔적이 너무 많다

이건 내가 이번 감사에서 가장 낮게 본 부분이다.

`src_v2`에는 실제로 다음 유형의 주석이 반복된다.

- `BUG-FIX`
- `누락 수정`
- `fallback`
- `no-op`
- `skip`

이건 단순 문체 문제가 아니다. 이 말은 곧:

> **운영에 필요한 경로를 처음부터 설계로 닫지 못했고,
> 실제로 굴리면서 빠진 연결을 뒤늦게 하나씩 메우고 있다**

는 뜻이다.

이 패턴이 몇 개면 실수다.
그런데 여기선 여러 핵심 경로에서 반복된다.

내가 경쟁자 평가를 한다면, 이건 아주 뼈아프게 쓸 수밖에 없다.

> 구조 감각은 있었지만, 제품 완성 책임은 뒤로 밀렸다.

### 3.2 v2의 핵심 위험은 “분산된 상태”다

`src`는 ugly monolith다. 대신 문제를 찾으려면 한 파일만 파면 되는 경우가 많다.

반면 `src_v2`는 상태가 흩어져 있다.

- `SnapshotStore`
- `DecisionScratch.data`
- `RuleContextBuilder.extras`
- `worker_state`
- `MainLoop._dec_state`
- `Follower` 내부 상태
- facade pending state

8.1에서 `worker_state ↔ extras`를 통합한 건 분명 전진이다. 그러나 솔직히 말하면
이건 **상태 분산 문제의 한 축만 완화한 것**이지, 해결은 아니다.

즉 v2의 위험은 “없다”가 아니라,

> **있는 것들이 너무 여러 층에 나뉘어 있어서,
> 장애 시 진실 원본이 어디인지 즉시 말하기 어렵다**

는 데 있다.

이건 운영 디버깅 비용을 폭발시킨다.

### 3.3 이벤트 버스 구조는 우아하지만, publish contract가 느슨하면 기능이 조용히 죽는다

`CooldownWatcher`, `HpMpWatcher`에 들어간 BUG-FIX는 아주 상징적이다.

이전에는:

- 값이 안 바뀌면 publish 안 함
- result가 비면 publish 안 함

그 결과:

- 룰 엔진이 평가 자체를 못 함
- 이미 임계치 아래인 상태가 영원히 안 잡힘
- ready 상태가 영원히 감지 안 됨

즉 v2는 이벤트 기반 구조를 가져온 대신,

> watcher가 publish를 안 하면 기능이 죽는 구조

를 만들었다.

이건 설계 미학만으론 절대 용서 못 한다. 이벤트 아키텍처를 쓸 거면 contract는 더
엄격해야 한다. 여기선 그 엄격성이 사후 보완으로 들어왔다.

### 3.4 `v1_compat` 분할은 맞지만, 총복잡도 승리는 아니다

8.1 보고서대로 `v1_compat.py`는 30줄 wrapper가 됐다. 사실이다.

하지만 더 본질적인 질문은 이거다.

> 그래서 총 시스템이 단순해졌느냐?

내 판단은 **아직 아니다**다.

왜냐하면 지금도 호환층은 다음을 책임진다.

- start 전 sync
- adapter build
- region inject
- UDP bind retry
- ControlCmd handler
- uplink sender
- attacker cooldown receiver
- Qt signal bridge
- fallback direct send_control

즉 단일 거대 파일은 사라졌지만, **복잡도는 subsystem으로 분산되었을 뿐**이다.

이건 리팩터링 성과로는 맞지만, 승리 선언으로는 약하다.

### 3.5 실제로 죽어 있었던 경로가 있다

이건 아주 치명적인 비판 포인트다.

문서와 코드 주석 모두 보여주듯, 한동안 다음 문제가 실제로 있었다.

- `send_control()`이 adapter 메서드 부재 때문에 사실상 항상 실패
- udp control handler 등록 누락
- cfg setter sync 타이밍이 늦어서 disabled rule도 발동 가능
- watcher publish 누락으로 rule evaluation 자체가 무반응

이건 “잠재 리스크”가 아니다.

> **실제 핵심 경로가 한동안 죽어 있었다**

는 뜻이다.

리더 평가에서 이건 매우 불리하다. 이유는 단순하다.

> 잘게 쪼개는 재능보다,
> 핵심 경로를 처음부터 살아 있게 만드는 책임이 더 중요하기 때문이다.

### 3.6 smoke test는 좋지만, 진짜 위험한 걸 충분히 못 본다

`test_v2_real_smoke.py`는 존재 자체는 긍정적이다.

하지만 이 테스트는 주로 본다.

- 켜짐
- 값 들어옴
- region setter 전달됨

반면 진짜 무서운 건 다음이다.

- start 전/후 ordering race
- GUI ↔ facade ↔ worker 실제 타이밍
- watcher publish 타이밍과 rule evaluation 경합
- fallback/no-op 경로가 실제로 밟히는 순간

즉 smoke는 “살아있음”만 증명하지, **정상 의미로 오래 살아있음**을 충분히 증명하진 않는다.

### 3.7 로그 샘플이 보여주는 것: v1은 실제 운영 흔적이 깊다

샘플 로그를 보면, 최소한 다음이 실제 운영 중 의미 있게 발생한다.

- `ATK-WARP`
- `CD-RECV`
- `CTRL-SEND`
- `MAP-SEQ-EDGE`
- `STARTUP-S`
- `HPMP-REJECT`

특히 `logs/attacker_20260424_172856.log`에서:

- `ATK-WARP=5`
- `HPMP-REJECT=64`
- `CD-RECV=77`
- `CTRL-SEND=12`

가 잡힌다.

이 숫자가 뜻하는 건 하나다.

> 운영 경로의 노이즈와 예외는 정말 자주 발생한다.

즉 구조적으로 보기 좋은 코드보다,
이 노이즈를 견디는 코드가 더 높은 점수를 받아야 한다.

---

## 4. 8.1 보고서 평가는 어떻게 해야 하는가

`v1_vs_v2_audit_8_1_final_2026_04_28.md`에 대해서는 이렇게 정리할 수 있다.

### 맞는 점

- 사실관계 상당수는 맞다.
- 실제 파일 분할, scratch 도입, cfg-contract 로그, contract test 추가는 확인된다.
- 최종 선언을 보류한 점은 비교적 정직하다.

### 과한 점

- `완료`라는 표현이 구조 반영 완료 이상으로 읽힐 수 있다.
- `분할 완료`가 총복잡도 감소를 자동 보장하진 않는다.
- `의미 보존 잠금 완료`가 운영 안정성 완료처럼 들리면 과장이다.

즉 8.1 보고서는:

> **진행 보고서로는 유효하지만,
> 승리 선언서로 읽으면 과장이다.**

---

## 5. 내가 심사권자라면 내리는 최종 인사평가

이제 제일 냉정한 판단을 적는다.

### `src` 쪽 리더에게 주는 점수

장점:
- ugly but operational
- 예외 상황 대응이 깊다
- 실제 운영 노이즈를 코드와 로그로 흡수했다

단점:
- 코드 품질 낮음
- 확장성 떨어짐
- 책임이 너무 한 파일에 몰림

평가:
- **구조 점수는 낮아도 운영 점수는 높다**

### `src_v2` 쪽 리더에게 주는 점수

장점:
- 구조 감각 좋음
- 층 분리 방향 좋음
- contract test 도입, 분할, 설정 반영성 보강 등 개선 의지 있음

단점:
- 핵심 경로 누락을 사후 봉합한 흔적이 너무 많음
- distributed state로 디버깅 비용 큼
- 완성 책임보다 구조 미학이 먼저 보임
- 실운영 검증은 아직 덜 끝남

평가:
- **발표 점수는 높지만, 운영 완결성 점수는 아직 부족**

### 최종 선택

내가 둘 중 하나를 더 높게 준다면,

> **지금 시점에선 `src` 쪽 책임자에게 더 높은 신뢰 점수를 준다.**

이유는 간단하다.

> 실전 시스템에서는 “예쁘게 다시 쓴 사람”보다,
> “끝까지 살아남게 만든 사람”이 더 높은 점수를 받아야 한다.

`src_v2` 책임자는 승진 후보감은 맞다. 하지만 **이번 결과물만으로 무조건 승자라고 보긴 어렵다.**

---

## 6. 내가 책임자라면 다음에 뭘 하겠는가

여기서 끝내면 안 된다. 진짜 끝판은 이 순서다.

### 6.1 v1 기능 카탈로그를 표로 확정

- healer
- attacker
- follower
- OCR
- cooldown/buff/HPMP
- control / uplink / analytics

를 완전히 표로 만든다.

### 6.2 v2 대응을 항목 단위로 1:1 박는다

열 예시:

- 기능명
- v1 파일/개념
- v2 대응 파일
- 상태: 동일 / 부분 / 보강필요 / 부재
- 운영위험도: 상 / 중 / 하
- 근거 라인

이걸 만들면 더 이상 감상평이 아니라 **법의학 수준 감사표**가 된다.

### 6.3 logs 기반 운영 증거를 같이 묶는다

코드만으로는 “가능성”이다.
로그까지 붙이면 “실제 발생”이 된다.

그래서:

- HPMP reject
- warp
- control send
- cd recv
- map seq edge
- tab confirm / startup-s

같은 항목은 실제 로그 빈도와 같이 묶어야 한다.

### 6.4 그 다음에야 최종 선언 가능

그 전까지는 어떤 문서든 정확한 표현은 하나다.

> **`src_v2`는 유망하지만, 아직 완전히 이긴 코드는 아니다.**

---

## 7. 진짜 끝판 한줄 결론

**`src_v2`는 구조적 야심은 성공했지만, 운영 완결성은 아직 `src`를 완전히 압도하지 못했다. 따라서 현재 시점에서 더 높은 점수는 v1 실전 코드를 끝까지 버틴 쪽이 받는 게 맞다.**