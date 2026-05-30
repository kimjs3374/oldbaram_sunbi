# src vs src_v2 코드 직접 검토 기반 감사 보고서

> 주의: 이 문서는 기존 `*.md` 감사 문서를 근거로 삼지 않고, `src/` 및 `src_v2/`의 실제 Python 소스 구조와 핵심 구현 파일을 직접 읽고 정리한 결과만 반영한다.

## 1. 검토 방식

이번 문서는 다음 원칙으로 작성했다.
- 기존 `v1_v2_coverage_audit.md`, `README.md` 등의 주장 자체는 결론 근거에서 제외
- `src/`, `src_v2/`의 실제 `.py` 파일 목록과 핵심 구현부를 직접 확인
- 특히 런타임 핵심축인 아래 파일을 우선 검토
  - `src/workers/healer_worker.py`
  - `src/input/skill_scheduler.py`
  - `src/input/target_sequence.py`
  - `src/fsm/controller.py`
  - `src/net/protocol.py`
  - `src/app/attacker.py`
  - `src_v2/workers/healer_worker_v2.py`
  - `src_v2/workers/attacker_worker_v2.py`
  - `src_v2/brain/integration_tick.py`
  - `src_v2/muscle/main_loop.py`
  - `src_v2/eyes/udp_watcher.py`
  - `src_v2/eyes/cooldown_uplink.py`
  - `src_v2/hands/input_dispatcher.py`
  - `src_v2/workers/v1_compat.py`

또한 전체 트리의 Python 파일 수와 LOC 규모를 직접 인벤토리화했다.

## 1.1 심사 관점 선언

이 문서는 좋게좋게 포장하는 리뷰가 아니다. **운영 시스템 리라이트를 승진 심사 테이블에 올린다**는 가정으로, 아래 기준에서 냉정하게 본다.

- 기능 동치성
- 운영 안정성
- 초기화/와이어링 신뢰성
- 장애 분석 가능성
- 유지보수 책임 분산 정도

즉 단순히 “구조가 예쁘다”, “테스트가 있다”는 이유로 점수를 주지 않는다. **실전에서 안 터질 구조인가**, **터졌을 때 누가 책임지고 바로 고칠 수 있는가**를 기준으로 본다.

## 1.2 인사평가 관점의 판단 프레임

만약 이 결과물로 두 명의 리더 중 한 명만 더 높은 평가를 받아야 한다면, 나는 다음 기준으로 판단한다.

- 기존 운영 복잡도를 실제로 줄였는가
- 기능 동치를 “문서상”이 아니라 “런타임 경로상”으로 증명했는가
- 장애가 났을 때 추적 가능한 상태 모델을 만들었는가
- 빠진 경로를 사후 패치로 때우는 대신, 처음부터 계약을 닫았는가

이 기준에서 보면 `src_v2`는 **방향성과 설계 감각은 높게 평가할 수 있지만**, 현재 시점의 산출물은 아직 **완결 책임**을 충분히 증명한 상태라고 보기 어렵다.

## 2. 코드베이스 규모 자체가 말해주는 것

### src

- 핵심 단일 파일이 매우 큼
  - `src/workers/healer_worker.py` 3105 lines
  - `src/ui/main_window.py` 3977 lines
  - `src/fsm/controller.py` 954 lines
  - `src/vision/ocr.py` 1031 lines
  - `src/vision/cooldown_ocr.py` 820 lines
  - `src/app/attacker.py` 947 lines

즉 `src`는 구조적으로 예쁘진 않지만, **운영 중 생긴 예외처리와 안전장치가 한 군데에 축적된 실전형 코드**다.

### src_v2

- 기능이 잘게 분리됨
  - `workers/healer_worker_v2.py` 1078 lines
  - `workers/attacker_worker_v2.py` 598 lines
  - `brain/follower.py` 1121 lines
  - `muscle/main_loop.py` 812 lines
  - `brain/integration_tick.py` 367 lines
  - `workers/v1_compat.py` 1535 lines

즉 `src_v2`는 분해는 잘 되어 있다. 하지만 그만큼 중요한 건 **분해된 조각들이 실제 런타임에서 빠짐없이 다시 결합되었느냐**다.

결론부터 말하면, 현재 코드만 보면 `src_v2`는 상당 부분 따라왔지만, 아직도 몇 군데는 **“이론상 있음”과 “실제로 완결되어 있음” 사이의 틈**이 보인다.

---

## 3. src 쪽 실제 구현 특성

`src/workers/healer_worker.py` 초반부만 봐도 이 파일은 단순 워커가 아니다. 사실상 다음 책임이 한 군데 몰려 있다.

- armed / follow_only 상태 관리
- HP/MP edge 상태 추적
- attacker UDP edge 상태 추적
- movement lock stuck 감시
- startup `s` 키 1회 송신
- coord_tol 강제/복원
- pending tab lock
- seq-rclick 타겟 유지
- stuck blacklist / reset history
- cooldown OCR / buff OCR / chat OCR / hpmp / xp / UDP uplink

이건 보기엔 더럽지만, 다른 말로 하면 **실전에서 터졌던 문제들을 모두 한 파일에 우겨 넣어 생존시킨 코드**다.

특히 `src/input/skill_scheduler.py`와 `src/input/target_sequence.py`는 단순 “스킬 눌러주는 모듈”이 아니다.

- `SkillScheduler`
  - edge-trigger queue
  - ready gate
  - blocks_movement
  - pre/post hook
  - verify/retry
  - stop 시 lock 해제
- `target_sequence.py`
  - self-target 시퀀스
  - 토글 OFF/ON
  - burst heal/revive
  - Shift 조합 버프 시전 분리

즉 `src`는 난잡하지만, **동작 보장용 세부 장치가 굉장히 많다**.

이건 승진 심사 관점에선 무시하면 안 된다. 코드가 예쁘지 않더라도,

> 실제 문제를 맞고,
> 실제 로그를 쌓고,
> 실제 예외를 막아낸 코드

는 기본적으로 현장 신뢰를 얻는다. `src`는 바로 그 부류다.

---

## 4. src_v2가 실제로 잘한 부분

코드만 놓고 보면 `src_v2`의 장점은 분명하다.

### 4.1 healer 메인 분해는 성공적

`src_v2/workers/healer_worker_v2.py`는 실제로 아래를 조립하고 있다.

- Eyes
  - capture / yolo / ocr / cooldown / buff / hpmp / xp / udp
- Brain
  - rule engine
  - integration tick
  - follower
- Hands
  - input dispatcher
  - numlock cycler
  - skill executor
- Muscle
  - main loop
- Memory / recovery / self-healing / learning / UI publisher

즉 구조적 리라이트 자체는 허세가 아니라 실제 코드로 존재한다.

### 4.2 movement lock 안전장치는 v2에도 실제 구현됨

`src_v2/hands/input_dispatcher.py`를 보면 다음이 직접 구현되어 있다.

- `set_movement_lock()`
  - lock 진입 시 held 방향 release
  - lock 중 `set_direction()` 무시
- `check_movement_lock_stuck()`
  - 10초 초과 시 강제 해제
  - release callback 재호출

이건 `src`의 잡다한 안전장치를 그냥 빼먹은 게 아니라, 최소한 핵심은 분리 이식하려고 한 흔적이 뚜렷하다.

### 4.3 integration_tick는 단순 껍데기가 아님

`src_v2/brain/integration_tick.py`에는 실제로 다음이 들어 있다.

- attacker revive / parhon / mujang / boho edge 처리
- parlyuk 버프 active 시 `coord_tol=1` 강제와 만료 복원
- `_map_transition_in_progress` 계산
- `TAB-LOCK pending` 계산
- `POST-HEAL-TAB` 자동 복귀
- worker_state ↔ state mirror

즉 예전 분석에서 문제였던 많은 항목들이, 현재 코드 기준으로는 **적어도 이 모듈 안에는 들어가 있다.**

### 4.4 muscle/main_loop도 핵심 이동 판단을 별도로 떼어냈음

`src_v2/muscle/main_loop.py`는 그냥 방향키 홀더가 아니다.

- blacklist add/remove/check
- B1/B2 trail follow
- force-exit
- F1 stale hold
- MAP-JUMP-HOLD
- B3 target follow
- STUCK filter

즉 이동 알고리즘의 뼈대는 실제로 존재한다.

여기까지는 인정해야 한다. `src_v2` 작성자는 분명히 생각 없이 폴더만 쪼갠 게 아니라,
적어도 **무엇을 분리해야 하는지**, **어떤 책임을 어떤 층으로 보낼지**는 이해하고 있었다.

문제는 그 다음이다. **좋은 구조 감각이 곧 좋은 제품 완성도는 아니다.**

---

## 5. 그런데도 src_v2가 아직 완전 승계라고 보기 어려운 이유

핵심은 이거다.

> `src_v2`는 로직 조각은 많이 옮겼지만, 몇 군데는 아직도 “보여주기 좋은 구조”가 “운영 완결성”을 완전히 대체하진 못한다.

아래 항목들은 코드 직접 확인 기준으로 걸린다.

### 5.1 `coord_tol` 강제 복원은 integration_tick에 있지만, 최종 소비 경로는 여전히 헷갈린다

`integration_tick.py`는 `rule_cfg["coord_tol"] = 1`로 바꾼다.

반면 `muscle/main_loop.py`는 `cfg.get("coord_tol", ...)`를 본다.

문제는 이 값이 **worker 전체 수명 동안 항상 muscle 쪽이 참조하는 실제 cfg와 완전히 동기화되는지**가 읽는 사람 입장에서 즉시 명확하지 않다는 점이다.

즉,

- 로직은 있다
- 값도 바꾼다
- 하지만 “이 값이 매 틱 실이동 판단에 반드시 먹는다”는 확신을 코드 가독성만으로 즉시 주진 못한다

이건 완성도 문제다. 동작할 수도 있다. 하지만 **읽는 순간 불안하면 설계 커뮤니케이션이 실패한 것**이다.

### 5.2 UDP stall 처리가 v1만큼 노골적으로 드러나지 않는다

`src/workers/healer_worker.py`는 `_udp_stalled`, `_udp_stall_since`를 직접 들고 돌면서 stall/resume edge 로그를 남긴다.

반면 `src_v2/eyes/udp_watcher.py`는:

- `udp_active` 갱신
- 10초마다 `[NO-STATE]` 경고
- first receive / periodic snap

까지는 있지만, v1처럼 **stall 진입/복귀 edge를 명시적으로 상태화해 운영 로그를 남기는 방식**은 더 약하게 보인다.

초기 확인 시엔 이렇게 보였는데, 추가 재검토 결과 `src_v2/eyes/udp_watcher.py`에는
2026-04-27 패치로 아래가 이미 들어와 있다.

- `_udp_stalled`
- `_udp_stall_since`
- `_last_seq`
- `[UDP-STALL]`, `[UDP-RESUME]` emit

즉 **기능 자체는 현재 코드에 들어와 있다.** 다만 이 사실이 중요하다. 이건 반대로
말하면, 이 기능이 애초부터 자연스럽게 설계에 녹아 있었다기보다, **운영 중 빠진 걸
뒤늦게 메우는 식의 보수 패치가 누적되고 있다는 뜻**이기도 하다.

즉 문제의 본질은 “없다”가 아니라, **핵심 운영 edge가 이미 한 번 이상 누락됐고,
그걸 사후 패치로 메꿔가고 있다**는 데 있다.

### 5.3 attacker 쪽 row_idx 해석은 아직 보수적으로만 처리한다

`src_v2/workers/attacker_worker_v2.py::_handle_cd_report()`는 실질적으로:

- `row_idx = rep.src_idx`
- `src_ip`만 추가 기록

수준이다.

즉 힐러 peer 배열과 실제 송신 IP를 더 적극적으로 매칭해서 “reported_idx가 틀려도 실제 row를 보정”하는 계층은 이 파일만 봐선 없다.

이건 작아 보여도 운영 중 다중 힐러 환경에선 꽤 중요한 보강 포인트다.

### 5.4 v2는 분리한 대신 `v1_compat.py`가 비정상적으로 커졌다

`src_v2/workers/v1_compat.py`가 1535줄이라는 건 의미가 크다.

이 말은 결국,

- v2 내부 구조는 새롭게 짰지만
- 바깥에서 기대하는 v1 인터페이스가 너무 크고 복잡해서
- 호환층이 또 하나의 거대 시스템이 되었다

는 뜻이다.

즉 리라이트의 복잡도를 없앤 게 아니라, **핵심 복잡도 일부를 facade 층으로 옮겨놨다**는 얘기다.

이건 나쁜 것까진 아닌데, 적어도 “v2는 단순하고 깔끔하다”는 말은 절반만 맞다. 실제로는 **밖에서 받쳐주는 호환층이 매우 비대하다.**

### 5.5 attacker V2는 의외로 XP watcher가 없다

`src_v2/workers/attacker_worker_v2.py`를 보면 capture / yolo / ocr / hpmp / cooldown / buff는 있지만, 실제 watcher 인스턴스로 `XpWatcher`를 붙여두진 않는다.

그런데 `set_xp_region()`은 OCR adapter 위임만 시도한다.

즉 코드만 놓고 보면 attacker v2는:

- XP 기능을 아예 버린 건 아니지만
- 구조적으로 중심 watcher 흐름에 완전히 들어가 있는 느낌도 아님

이건 “추후 연결 예정” 냄새가 난다.

### 5.6 코드 곳곳의 `BUG-FIX`, `누락 수정`, `fallback`, `no-op` 흔적이 과도하게 많다

추가 검색 결과 `src_v2`에는 단순 주석 수준을 넘어서, **실제 기능 누락을 사후 보정한
흔적**이 매우 많이 박혀 있다.

대표 예시:

- `src_v2/brain/decision.py`
  - `BUG-FIX`: cfg copy 때문에 `set_skill_enabled`가 룰 평가에 반영되지 않던 문제
- `src_v2/eyes/cooldown_watcher.py`
  - `BUG-FIX`: 빈 result에서 publish skip → 룰 엔진이 이벤트를 영원히 못 받던 문제
- `src_v2/eyes/hpmp_watcher.py`
  - `BUG-FIX`: 값 변화 없으면 publish 안 해서 시작 시 이미 임계치 아래인 상태를 놓치던 문제
- `src_v2/workers/v1_compat.py`
  - `BUG-FIX`: `_v2.start()` 전에 cfg sync 안 해서 체크 해제 스킬도 발동하던 문제
  - `BUG-FIX`: udp control handler 등록 누락
  - `BUG-FIX`: `send_control()`이 실제로는 항상 실패하던 문제
  - `누락 수정`: attacker own skill names를 `_v2`로 forward 안 하던 문제
- `src_v2/eyes/udp_watcher.py`
  - `v1 stall edge 1:1` 패치 추가

이건 굉장히 중요하다. 왜냐하면 이 흔적들은 단순한 리팩터링 흔적이 아니라,
**“겉구조는 만들어졌지만 실제 동작은 나중에 하나씩 터져서 봉합되고 있다”**는 걸
코드 주석이 스스로 증언하기 때문이다.

즉 `src_v2`는 단순히 미완이 아니라, **이미 운용 중 발견된 wiring 누락을 연속해서
봉합하고 있는 상태**라고 보는 게 더 정확하다.

승진 심사 관점에선 이게 치명적이다. 왜냐하면 이건 “실수 한두 개”가 아니라,

> 처음 설계할 때부터 운영 경로를 끝까지 닫지 못했고,
> 실제로 굴려 보면서 빠진 연결을 뒤늦게 메우는 패턴

이 반복됐다는 뜻이기 때문이다.

즉 이 코드는 **아키텍처 미학은 있었지만, 완료 책임감은 부족했다**는 비판을 피하기 어렵다.

### 5.7 v1의 운영용 잡다한 디버깅/보정 코드는 여전히 더 두껍다

`src/app/attacker.py`, `src/workers/healer_worker.py`, `src/fsm/controller.py`는 다음처럼 **운영 노이즈와 오탐을 견디기 위한 보정 코드**가 매우 많다.

- reversion debounce
- healer map bbox mismatch reject
- fresh guard
- jump reject
- sticky red ttl
- analytics
- first/edge/noise debug log

`src_v2`도 일부는 갖고 있지만, 읽는 인상은 이렇다.

> v1은 지저분할 정도로 집착해서 막아 놓았고,
> v2는 구조적으로 더 낫지만 아직 그 집착을 전부 복제하진 못했다.

### 5.8 `v1_compat.py`가 “호환층” 수준을 넘어, 사실상 두 번째 메인 시스템이 되었다

추가 구간까지 읽고 나면 이건 더 명확해진다.

`src_v2/workers/v1_compat.py`는 단순 adapter가 아니다. 실제로 여기서 하고 있는 일은:

- cfg 변환
- region 자동 주입
- start 전/후 sync 순서 제어
- udp bind 재시도
- ControlCmd handler 등록
- uplink sender 동적 IP 학습
- attacker cooldown receiver 생성
- Qt signal bridge
- stat emit thread
- own cooldown emit thread
- fallback send_control 직접 구현

이건 더 이상 “얇은 facade”가 아니다. **v2를 기존 GUI 위에 억지로 얹기 위해 만들어진
거대한 중간 운영계층**이다.

이 구조의 문제는 간단하다.

> 내부를 분해해서 깔끔하게 만들었다고 해도,
> 바깥 호환층이 다시 거대하고 상태ful해지면,
> 복잡도는 사라진 게 아니라 이동한 것이다.

즉 현재 `src_v2`는 구조적으로 세련되어 보이지만, 실제 운영 복잡성은 상당 부분
`v1_compat.py`에 다시 응축되어 있다.

이 대목은 아주 노골적으로 지적해야 한다.

> 리라이트의 승부는 “안쪽을 얼마나 예쁘게 쪼갰냐”가 아니라,
> “바깥 현실까지 포함해서 총복잡도를 실제로 줄였냐”다.

그런데 현재 결과물은 총복잡도를 줄인 게 아니라,

- 내부는 `eyes/brain/hands/muscle`로 분산했고
- 외부는 `v1_compat.py`로 다시 비대해졌다

는 형태다. 이건 설계 발표에선 멋있어 보일 수 있어도, **총 시스템 관점에선 반쪽짜리 승리**다.

### 5.9 `send_control` 같은 핵심 제어 경로가 한동안 실제로 죽어 있었던 흔적이 있다

이건 심각하다.

`src_v2/workers/v1_compat.py` 후반부 주석을 보면 직접 이렇게 적혀 있다.

- 이전 버전에서 `sender.send_control()` 위임만 시도
- 그런데 `RealUdpSenderAdapter`에 해당 메서드가 없음
- 결과적으로 **항상 False 반환**
- 즉 격수에서 힐러로 제어 명령 송신이 실제로 안 됐음

이건 사소한 버그가 아니다. 제어 체인의 근간이 한동안 끊겨 있었단 뜻이다.

즉 `src_v2`는 “이론상 다 있음” 상태가 아니라, **실제 핵심 경로가 dead code처럼 죽어
있다가 뒤늦게 fallback direct send로 복구된 이력**이 있다.

### 5.10 watcher 발행(publish) 누락 버그는 구조 분리의 약점을 드러낸다

`cooldown_watcher.py`, `hpmp_watcher.py`의 `BUG-FIX`를 보면 공통 패턴이 보인다.

- 값이 없거나 변화가 없으면 publish 안 함
- 그런데 룰 엔진은 publish를 받아야만 평가가 돈다
- 그래서 특정 초기 상태/무변화 상태에서 **룰이 아예 영원히 안 도는** 문제가 발생

이건 굉장히 전형적인 이벤트 중심 아키텍처의 함정이다.

`src`는 메인 루프 안에서 이것저것 직접 읽고 직접 판단해서 지저분하지만,
이런 종류의 **“이벤트를 안 쏴서 판단 자체가 사라지는” 구조적 함정**은 상대적으로 적다.

반면 `src_v2`는 모듈 분리와 이벤트 버스 구조를 얻은 대신,
**publish contract 하나 빠지면 기능 전체가 조용히 죽는 위험**을 얻었다.

이건 내가 심사자라면 아주 낮게 본다. 이유는 명확하다.

> 설계가 우아할수록 contract는 더 엄격해야 한다.

그런데 여기선 watcher publish contract가 느슨했고, 실제로 그 느슨함이 운영 버그로 나타났다.
즉 구조화의 이득은 취했는데, 구조화에 필요한 엄격성은 끝까지 책임지지 못한 셈이다.

### 5.11 설정 반영 순서 문제는 `src_v2`가 생각보다 순서 의존적이라는 증거다

`v1_compat.py`에 남은 주석대로, 예전에는 `_v2.start()` 후 cfg sync를 해서
rule_engine이 default rule_cfg로 먼저 돌아버리는 문제가 있었다.

즉:

- UI에서 체크 해제한 스킬이
- 실제 시작 순간엔 기본값 True로 평가되어
- 원치 않는 발동이 가능했다는 뜻

이건 단순 버그라기보다, **초기화 순서에 매우 민감한 시스템**이라는 걸 보여준다.

초기화 순서가 잘못되면 기능이 그냥 조금 틀어지는 수준이 아니라, 아예
**운영 의미가 뒤집힌다**는 점에서 리스크가 크다.

### 5.12 SkillExecutor도 결국 v1의 복잡성을 상당 부분 다시 끌어안고 있다

`src_v2/hands/skill_executor.py`를 추가로 보면, 단순한 "시퀀스 실행기" 수준이 아니다.

- ready gate
- ctx_provider 기반 verify
- retry / retry_until_ready
- dedup pending set
- movement_lock 연동
- sequence 미등록 시 cast_failed publish

즉 `src_v2`는 hands 계층을 깔끔하게 분리했다고 말하지만, 실제로는 **v1
`SkillScheduler`의 복잡한 운영 semantics를 다른 이름으로 다시 옮겨 담고 있다.**

이건 중요한 지점이다. 구조는 바뀌었지만, 복잡성 자체는 줄지 않았다. 오히려

- `RuleEngine`
- `EventBus`
- `SkillExecutor`
- `InputDispatcher`

사이에 나뉘어 숨어버려서, **문제가 터졌을 때 추적 경로는 더 길어질 수 있다.**

### 5.13 `CooldownWatcher`의 빈 결과 처리 방식은 아직도 의미상 위험하다

`src_v2/eyes/cooldown_watcher.py`는 BUG-FIX 이후 빈 결과에서도 publish 하도록 바뀌었다.

이건 이전보단 낫다. 하지만 새로 드러나는 문제가 있다.

- `result == {}`면 publish는 한다
- 그런데 snapshot 필드 자체는 갱신되지 않을 수 있다
- 즉 룰은 이벤트는 받지만, **이벤트 시점의 상태가 실제 최신 의미를 반영하지 않을 수 있다**

문서 주석에도 드러나듯, 이 watcher는

> “빈 result라도 룰들이 snap의 -1 값으로 평가”

를 기대한다. 그런데 이건 곧,

> 이벤트는 왔는데 실질 값은 여전히 예전 값이거나 기본값일 수 있음

이라는 뜻이기도 하다.

즉 이 계층은 단순 누락보다 더 미묘한 위험이 있다. **이벤트 존재와 상태 신선도가
동치가 아니다.** 이건 이벤트 기반 구조에서 디버깅을 매우 어렵게 만든다.

### 5.14 `HpMpWatcher`도 동일한 구조적 문제를 보여준다

`src_v2/eyes/hpmp_watcher.py`는 2026-04-27 패치로 값 변화와 무관하게 매 tick publish
하도록 바뀌었다.

이 수정은 필요했다. 왜냐하면 시작 시 이미 임계치 아래인 경우, 값 변화가 없으면
룰이 영원히 안 돌 수 있었기 때문이다.

하지만 이 사실 자체가 의미하는 바는 꽤 세다.

> 이벤트 버스 기반 구조를 채택한 뒤,
> watcher가 “변화가 있을 때만 publish”하면,
> 운영 의미상 반드시 필요한 평가가 아예 사라질 수 있었다.

즉 `src_v2`의 현재 안정성은 설계의 자연스러운 안전성보다, **발행 정책을 사후에
조정해서 간신히 맞춘 안정성**에 더 가깝다.

### 5.15 `RuleContextBuilder` 버그는 v2가 “설정 참조 일관성” 문제에 취약했음을 보여준다

`src_v2/brain/decision.py`의 BUG-FIX는 매우 상징적이다.

이전에는 `dict(cfg or {})`로 copy 해서 보관했고,
그 결과:

- 외부에서 `set_skill_enabled()`로 cfg를 바꿔도
- RuleContextBuilder는 별도 사본을 계속 보고 있었고
- 룰은 영원히 초기값으로 평가됐다

이건 단순 코드 실수 이상이다. `src_v2`가 추구한 분리 구조에서,

- 설정 객체
- 룰 평가 컨텍스트
- 런타임 동적 토글

사이의 **참조 일관성 보장**이 생각보다 취약했다는 뜻이다.

즉 v2는 “설정은 한 번 들어가면 어디서나 동일하게 본다”는 전제가 한동안 깨져 있었다.

### 5.16 real smoke test는 유용하지만, 진짜 위험한 지점은 잘 피해 간다

`src_v2/tests/test_v2_real_smoke.py`도 읽어보면, 이 테스트는 다음을 잘 본다.

- build / start / stop
- store 값 채워짐
- region setter 전파
- attacker/healer worker가 최소한 살아있음

하지만 반대로 말하면, 이 테스트가 거의 보지 않는 부분도 뚜렷하다.

- multi-thread ordering race
- `v1_compat.py` 초기화 순서 문제
- ControlCmd dead path
- event publish 누락 시 룰이 영구 무반응인 상황
- cooldown/buff/hpmp watcher와 rule engine의 실제 상호작용 타이밍

즉 smoke test는 “켜지느냐”는 보지만, **실전에서 제일 위험한 건 대개 ‘켜진 뒤
조용히 틀어지는 것’**이다. 그 부분은 여전히 별도 감사가 필요하다.

### 5.17 `src`는 ugly monolith라서 위험하고, `src_v2`는 distributed state라서 위험하다

이건 이번 심화 검토의 가장 중요한 구조적 결론 중 하나다.

`src`의 문제는 명확하다.

- 너무 크다
- 너무 더럽다
- 한 파일에 너무 많은 상태가 몰려 있다

그런데 `src_v2`의 문제는 다른 종류다.

- 상태가 `store`, `ctx_builder.extras`, `worker_state`, `decision state`, `follower`,
  `compat facade`, watcher 내부 플래그 등으로 분산돼 있다
- 각 상태가 이벤트/주기/초기화 순서에 따라 느슨하게 동기화된다

즉 `src`는 monolith라서 위험하고,
`src_v2`는 **distributed state machine**이 되어버려서 위험하다.

이 차이는 실전 디버깅에서 치명적이다. `src`는 추해도 한 파일만 파면 되지만,
`src_v2`는 문제 하나를 추적하려면 여러 계층의 상태 전달 경로를 동시에 봐야 한다.

내가 팀장이라면 여기서 이렇게 평가한다.

- `src` 작성자는 **못생긴 대신 끝까지 책임졌다**.
- `src_v2` 작성자는 **똑똑하게 분리했지만, 책임 경계까지 완전히 닫지 못했다**.

실전 시스템에서 후자가 더 위험할 때가 많다. 이유는 단순하다. **겉보기엔 더 좋아 보여서,
조직이 더 쉽게 안심해버리기 때문**이다.

---

## 6. 직접 코드 기준으로 본 항목별 판정

### 이미 상당히 따라온 것

1. **healer 조립 구조**
   - `HealerWorkerV2`에서 eyes / brain / hands / muscle / uplink / ui가 실제 배선됨

2. **movement lock**
   - `InputDispatcher`에서 lock, stuck release, callback 구현 확인

3. **attacker UDP 송신 핵심**
   - F1 edge
   - warp threshold
   - map burst
   - last_dir 계산
   - buff/debuff/hp/mp 송신

4. **follower 기반 이동 판단**
   - `muscle/main_loop.py`에서 B1/B2/B3/STUCK/BL 로직 유지

5. **TAB-LOCK / post-heal-tab 계열**
   - `integration_tick.py`에 실제 조건식 존재

6. **UDP stall/resume edge**
   - 최신 `udp_watcher.py`에는 v1식 stall/resume edge emit이 추가됨

7. **ControlCmd / uplink / dynamic sender 보강**
   - `v1_compat.py`에서 실운영 결손을 메우기 위한 보강이 실제 존재함

### 아직 의심스럽거나 더 보강돼야 하는 것

1. **`coord_tol` 변경이 최종 move loop에 얼마나 명확히 먹는지**
   - 동작 가능성은 높지만, 읽기/보증 측면에서 여전히 불안정

2. **attacker cooldown report row 매칭 정밀도**
   - reported idx 의존이 강함

3. **XP 흐름의 구조적 완결성**
   - attacker v2에서 중심 watcher 체계 안에 깔끔하게 붙어 있다고 보기 어려움

4. **facade 의존도**
   - v2 자체보다 `v1_compat.py`의 무게가 너무 큼
   - 이는 “내부는 새로 짰지만 외부 현실은 아직 옛 인터페이스에 묶여 있다”는 뜻

5. **핵심 기능이 ‘패치 주석’으로 증명되는 불안정성**
   - BUG-FIX / 누락 수정 / fallback direct send 흔적이 너무 많음

6. **이벤트 기반 구조의 묵시적 고장 위험**
   - publish 빠지면 룰이 조용히 죽는 구조적 리스크가 이미 실제 버그로 드러남

7. **초기화 순서 민감도**
   - cfg sync 타이밍이 틀리면 disabled rule도 먼저 발동할 수 있었음

8. **no-op / fallback / skip 경로 과다**
   - graceful degrade가 아니라, 실제 운영 결손을 조용히 숨기는 통로가 되기 쉬움

9. **distributed state 복잡도**
   - monolith 해체의 대가로 상태 동기화 경로가 과도하게 분산됨

10. **smoke test의 검증 범위 한계**
   - 살아있는지 보지만, 조용히 틀어지는 운영 레이스는 충분히 못 잡음

---

## 7. 심화 감사 결론

`src_v2`는 허세만 있는 빈 리라이트는 아니다. 실제로 많은 핵심 로직이 옮겨져 있고, 구조 개선도 진짜다.

하지만 코드 직접 검토 기준으로도 아직 이렇게 말하는 게 정확하다.

> **src_v2는 “핵심 로직을 상당량 구조화해 옮긴 포트”는 맞지만, 운영 완결성 측면에서는 아직도 사후 봉합 흔적이 짙고, 몇몇 핵심 경로는 한동안 실제로 비어 있었거나 순서/발행 계약에 의존해 깨지기 쉬운 상태였다.**

왜냐하면:

- v1은 온갖 운영 예외에 맞아가며 누더기처럼 강화된 코드이고
- v2는 그 로직을 분해·이식했지만
- 아직 일부 지점은 runtime certainty보다 architectural neatness가 앞선 흔적이 남아 있고
- 그 흔적이 단순 추측이 아니라 **코드 주석의 BUG-FIX / 누락 수정 / fallback**으로 직접 드러난다
- 그리고 monolith를 해체한 대가로, 상태와 의미가 여러 계층에 분산되어 디버깅 비용이 커졌다

한 줄로 요약하면 이렇다.

> `src`는 더럽지만 집요하고,
> `src_v2`는 훨씬 세련됐지만, 그 세련됨 아래에 아직도 “빠진 걸 뒤늦게 메우는 봉합 흔적”이 적지 않다.

더 세게 요약하면:

> `src`의 문제는 추함이고,
> `src_v2`의 문제는 복잡성이 사라진 척하면서 다른 계층들로 흩어졌다는 점이다.

승진 심사 언어로 번역하면 더 직설적이다.

- `src`는 미학 점수는 낮지만 **운영 생존 점수**가 높다.
- `src_v2`는 발표 점수는 높지만 **완성 책임 점수**가 아직 부족하다.

내가 이 결과물의 책임자라면, 현 단계에서 “완전 대체 가능” 같은 표현은 금지한다.
그건 기술적 과장이고, 운영 책임 회피에 가깝다.

더 냉정하게 말하면, 지금 `src_v2`는 **좋은 방향의 포트**이지,
**이미 이긴 제품**은 아니다.

이 차이는 문서가 아니라 코드에서 보인다.

---

## 8. 후속 권고

진짜로 `src_v2`를 `src` 대체품이라고 말하고 싶다면, 다음은 반드시 정리해야 한다.

1. `coord_tol` 변경 경로를 worker → muscle 소비까지 더 명백하게 드러내기
2. attacker cooldown report의 row/IP 매칭 보강
3. attacker XP 흐름을 watcher 체계 안으로 더 명확히 편입
4. `v1_compat.py`의 비대한 책임을 다시 쪼개기
5. watcher publish contract를 명시 테스트로 강제해 “이벤트 누락 시 룰 무반응” 재발 방지
6. ControlCmd / uplink / pre-start cfg sync 같은 순서 의존 경로를 통합 init 단계로 고정
7. `fallback`, `no-op`, `skip` 경로를 운영 기능과 테스트 기능으로 분리
8. `store` / `worker_state` / `extras` / `decision state` 간 책임 경계를 재정의해 상태 분산을 줄이기
9. smoke test 외에 ordering/race 중심의 통합 테스트를 별도로 추가하기

## 8.1 내가 책임자라면 이렇게 고친다

만약 내가 이 리라이트를 직접 책임지는 A팀장이라면, 다음 순서로 정리한다.

### 1단계: “예쁜 구조”보다 “의미 보존”을 먼저 잠근다

- watcher publish contract를 문서가 아니라 테스트로 고정
- `set_skill_enabled`, `set_follow_only`, `set_armed`가 실제 평가 경로에 즉시 반영되는지 전수 테스트
- `ControlCmd`, uplink, cooldown report, row 매칭을 기능 단위로 독립 검증

즉 **상태 반영 누락**을 먼저 죽인다.

### 2단계: 상태 저장소를 줄인다

현재는 의미 있는 상태가 너무 많다.

- `store`
- `worker_state`
- `ctx_builder.extras`
- `muscle._dec_state`
- `follower` 내부 상태
- `v1_compat` pending state

이 중 일부는 하나로 합쳐야 한다. 내 기준에선 적어도

- runtime truth
- decision scratch
- UI/cache

정도로 재분류해서, **무엇이 진실 원본인지**를 분명히 해야 한다.

### 3단계: `v1_compat.py`를 해체한다

지금처럼 1500줄짜리 호환층은 장기적으로 독이다.

- region bridge
- lifecycle bridge
- control bridge
- uplink bridge
- attacker facade

를 최소 4~5개 모듈로 쪼개고, 각 모듈의 실패 모드를 따로 테스트한다.

### 4단계: 운영 로그를 “기능 로그”에서 “계약 로그”로 올린다

예:

- 어떤 이벤트가 publish 됐는지
- 어떤 룰이 평가됐는지
- 어떤 설정값이 실제 평가에 쓰였는지
- 어떤 fallback이 발동했는지

를 계약 수준으로 남겨야 한다. 지금은 기능 로그는 많지만, **상태 전달의 진실성 로그**는 여전히 부족하다.

### 5단계: 최종 선언은 그 다음이다

이 과정을 다 끝낸 뒤에만,

> “src를 안전하게 대체 가능”

이라고 말한다. 지금 단계에서 그 선언을 하면, 그건 엔지니어링이 아니라 마케팅이다.

이걸 안 하면, `src_v2`는 계속 “좋아 보이는 구조”로는 칭찬받아도, “실전에서 완전히 믿을 수 있는 대체품”으로는 끝까지 의심받게 된다.

---

## 9. 최종 한줄 판정

**`src_v2`는 구조적으로는 승리했지만, 운영 완결성에서는 아직 `src`를 완전히 죽였다고 말할 정도로 무결하지 않다.**

더 직설적으로 말하면:

- `src`는 추하고 비대하지만 실제로 버그와 싸운 흔적이 아주 깊게 박혀 있고,
- `src_v2`는 똑똑하게 다시 썼지만,
- 그 똑똑함이 아직도 여러 곳에서 **패치 주석과 fallback로 연명하는 불안정한 연결부**를 숨기고 있다.
- 게다가 그 연결부는 한 군데에 모여 있는 게 아니라, watcher / bus / context / facade / worker 사이에 흩어져 있다.

그래서 지금 단계의 `src_v2`를 완성품이라고 포장하면 과장이고,
정확한 평가는 **“매우 많이 따라왔지만 아직도 봉합 중인 리라이트”**다.

그리고 경쟁 평가 관점에서 마지막으로 한 줄 더 남기면 이렇다.

> 이 코드는 방향성은 좋지만, 아직 결과로 증명된 코드가 아니다.
> 반면 `src`는 추하고 무겁더라도 이미 결과를 견딘 코드다.

내가 심사권자라면, 현재 시점에선 **구조적 야심보다 운영 완결성에 더 높은 점수**를 준다.