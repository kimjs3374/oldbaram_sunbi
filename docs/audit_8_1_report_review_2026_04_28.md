# `v1_vs_v2_audit_8_1_final_2026_04_28.md` 검증 감사

## 결론 요약

`v1_vs_v2_audit_8_1_final_2026_04_28.md`는 **허위 보고서라고 볼 수준은 아니다.**
내가 직접 대조한 범위에서는, 문서가 주장한 핵심 변경점은 실제 코드에 대부분 반영돼 있다.

다만 이 문서는 성격상 **“보완 작업 진행 보고”**에 가깝기 때문에, 다음 두 가지를 구분해서 읽어야 한다.

1. **무엇이 실제로 코드에 반영되었는가**
2. **그 반영만으로 운영 완결성이 충분히 확보되었는가**

1번은 대체로 맞다. 2번은 아직 아니다.

즉, 이 문서는 **사실관계는 대체로 맞지만, 완료감은 실제보다 조금 앞서 있다.**

---

## 1. 확인 결과 “맞는 주장”

### 1.1 `v1_compat.py` 분할 완료 주장은 사실

문서 주장:
- `v1_compat.py` 1561줄 → 30줄 wrapper
- sibling 모듈 9개로 분할

실제 확인:
- `src_v2/workers/v1_compat.py`는 현재 30줄짜리 re-export wrapper
- `src_v2/workers/` 아래에 다음 모듈이 실제 존재
  - `_compat_logger.py`
  - `_compat_helpers.py`
  - `_compat_uplink.py`
  - `_compat_cd_receiver.py`
  - `_compat_f1_key.py`
  - `_compat_healer_adapters.py`
  - `_compat_attacker_adapters.py`
  - `_compat_healer_facade.py`
  - `_compat_attacker_facade.py`

판정:
- **사실**

### 1.2 `DecisionScratch` 도입 및 `worker_state ↔ extras` 단일 ref 통합 주장은 사실

문서 주장:
- `DecisionScratch` 신설
- `worker_state`와 `ctx_builder.extras`가 동일 ref 공유

실제 확인:
- `src_v2/brain/decision_scratch.py` 존재
- `DecisionScratch.data` 단일 dict 구현
- `src_v2/workers/healer_worker_v2.py`에서
  - `self._scratch = DecisionScratch()`
  - `self._worker_state = self._scratch.data`
  - `RuleContextBuilder(... extras=self._scratch.data)`

판정:
- **사실**

### 1.3 `RuleContextBuilder`가 extras copy를 버리고 ref를 쓰도록 바뀐 주장은 사실

문서 주장:
- 이전 copy 폐기
- extras ref 공유

실제 확인:
- `src_v2/brain/decision.py`
  - `self.extras: Dict[str, Any] = extras if extras is not None else {}`

판정:
- **사실**

### 1.4 `[CFG-CONTRACT]` 로그 추가 주장은 사실

문서 주장:
- `healer_worker_v2.start()` 끝에 `[CFG-CONTRACT]` 로그 추가

실제 확인:
- `src_v2/workers/healer_worker_v2.py` start 구간에
  - `[CFG-CONTRACT] enabled=...`
  - `[CFG-CONTRACT] thresholds ...`
  emit 존재

판정:
- **사실**

### 1.5 `test_contract_*` 추가 주장은 사실

문서 주장:
- 계약 테스트 4개 추가

실제 확인:
- `src_v2/tests/` 아래 실제 존재
  - `test_contract_watcher_publish.py`
  - `test_contract_cfg_setter_propagation.py`
  - `test_contract_pre_start_cfg_sync.py`
  - `test_contract_decision_scratch.py`

판정:
- **사실**

---

## 2. 맞지만, 읽는 사람이 과대해석하면 위험한 주장

### 2.1 “1단계 완료”는 코드 반영 완료이지 운영 안정성 완료가 아님

문서 표현:
- “의미 보존 잠금 완료”

평가:
- 계약 테스트 4개가 추가된 건 맞다.
- 하지만 이건 **특정 계약 몇 개를 테스트로 고정했다**는 뜻이지,
  실운영 race, 멀티스레드 타이밍, GUI-호환층 상호작용, adapter 지연까지 모두
  잠갔다는 뜻은 아니다.

즉 이 표현은 내부 팀 문맥에선 이해 가능하지만, 외부 심사자가 보면
“안정성까지 끝났다”로 오해할 수 있다.

정확한 표현은:

> “핵심 계약 일부를 테스트로 고정했다”

가 더 맞다.

### 2.2 “2단계 부분 완료”는 정직하지만, 해결 범위를 넓게 읽으면 안 됨

문서 표현:
- `DecisionScratch`로 상태 저장소 통합 부분 완료

평가:
- 이건 비교적 정직한 표현이다.
- 실제로 `worker_state ↔ extras`는 통합됐다.
- 그러나 `MainLoop._dec_state`, `Follower`, snapshot 외화, UI cache 분리 등
  더 본질적인 상태 분산 문제는 그대로 남아 있다.

즉 이건 **상태 분산 문제 해결의 시작**이지, 해결 완료가 아니다.

### 2.3 “3단계 완료”는 분할 사실 자체는 맞지만, 복잡도 감소를 자동 보장하진 않음

문서 표현:
- `v1_compat` 분할 완료

평가:
- 파일 분리는 실제로 됐다.
- 하지만 총합 라인은 오히려 늘었다.
- 복잡도가 줄었다기보다, **복잡도가 모듈 단위로 재배치**된 것이다.

즉 이 단계의 진짜 성과는:

- 단일 거대 파일 리스크 축소
- 책임 위치 분리

이지,

- 시스템이 갑자기 단순해졌다

는 뜻은 아니다.

### 2.4 “4단계 완료”도 로그 추가는 맞지만, 계약 로그 체계 완성은 아님

문서 표현:
- 운영 로그 → 계약 로그 완료

평가:
- `[CFG-CONTRACT]` 추가는 분명 좋은 조치다.
- 하지만 이건 계약 로그 체계의 **시작**이지, 완성은 아니다.

아직 부족한 것:
- 어떤 watcher event가 실제 publish 되었는지
- 어떤 rule이 어떤 cfg 값으로 evaluate 되었는지
- 어떤 fallback/no-op/skip이 발동했는지

까지 체계적으로 남지는 않는다.

즉 지금은 “계약 로그 1개 추가”에 가깝지, “운영 로그가 계약 로그로 전환 완료”라고
보기엔 이르다.

---

## 3. 문서가 상대적으로 약하게 쓴 부분

이 보고서는 자기 작업 성과를 설명하는 문서라서 그런지, 다음 리스크를 상대적으로
약하게 다룬다.

### 3.1 `DecisionScratch`가 들어와도 distributed state 자체가 사라진 건 아님

여전히 상태는 여러 곳에 남아 있다.

- `store`
- `DecisionScratch.data`
- `MainLoop._dec_state`
- `Follower` 내부 상태
- facade 내부 pending 상태

즉 worker_state/extras 통합은 의미 있지만, **상태 기하급수적 분산 문제를 해결했다고
볼 정도는 아니다.**

### 3.2 `v1_compat` 분할이 실제 장애 추적 난이도를 얼마나 낮췄는지는 아직 불명확

파일은 쪼개졌지만,

- start 전 sync
- adapter build
- UDP bind retry
- ControlCmd handler
- uplink sender
- Qt signal bridge

같은 실제 운영 경로는 여전히 복잡하다.

즉 “코드 정리”는 되었어도, “운영 의미가 단순해졌다”고 말하긴 어렵다.

### 3.3 PASS 보고는 신뢰할 만하지만, 여기서 pytest 실행 근거까지 제시하진 않음

문서에는 “전부 PASS”라고 적혀 있다.

이 대화 맥락 기준으로는 **파일 존재와 코드 수정은 확인했지만**, 실제 pytest 실행
결과 로그까지 내가 직접 본 건 아니다.

따라서 이 표현은 다음처럼 읽는 게 안전하다.

> 작성자는 PASS라고 보고했다.
> 코드 구조상 테스트 파일은 존재하고, 내용도 그 주장과 정합적일 가능성이 높다.
> 하지만 실행 로그를 별도 확인하지 않으면 100% 확정 진술로 받아들이진 말아야 한다.

이건 문서가 거짓말을 했다는 뜻이 아니라, **증빙 수준의 문제**다.

---

## 4. 이 문서가 오히려 정직한 부분

반대로, 이 보고서에서 가장 신뢰할 만한 부분도 있다.

### 4.1 5단계 최종 선언을 보류한 건 맞는 태도다

문서가 스스로 다음을 사용자 환경 실증 필요로 남겨 둔 건 타당하다.

- baekho/parlyuk 반복 fire
- stop 명령 즉시 반영
- udp bind retry
- CFG-CONTRACT 로그와 GUI 토글 일치
- ROLE 로그와 라디오 일치

이건 허세 문서였다면 그냥 “완료”로 박았을 부분이다.

따라서 이 문서는 최소한 마지막 선언 단계에선 **무리하게 승리 선언을 하지 않았다**는 점에서,
완전히 무책임한 보고서는 아니다.

---

## 5. 최종 감사 판정

### 판정 1 — 사실관계

`v1_vs_v2_audit_8_1_final_2026_04_28.md`의 핵심 주장 상당수는 **실제 코드와 일치한다.**

즉 이 문서를 보고:

- “아예 뻥문서다”
- “실제로 한 거 하나도 없다”

라고 깎는 건 오히려 틀린 공격이다.

### 판정 2 — 해석 범위

하지만 이 문서를 보고:

- “이제 구조 문제 해결 끝”
- “운영 안정성 확보 끝”
- “src 대체 준비 완료”

라고 읽는 것도 과하다.

정확한 평가는 이렇다.

> **이 문서는 보완 작업 진행 보고로서는 대체로 타당하다.**
> **다만 완료로 적힌 항목들 중 상당수는 ‘코드 반영 완료’이지 ‘실전 검증 완료’는 아니다.**

### 판정 3 — 경쟁 평가 관점

내가 심사권자라면 이렇게 본다.

- 이 보고서를 쓴 사람은 적어도 **무엇을 고쳐야 하는지**는 알고 있다.
- 실제로 몇 가지 핵심 구조 보강도 했다.
- 하지만 이걸로 곧바로 “운영 완결 책임을 다 증명했다”고 보긴 어렵다.

즉 점수는 올라가지만, **아직 승진 확정급 완성도 증명은 아니다.**

---

## 6. 한 줄 결론

**`v1_vs_v2_audit_8_1_final_2026_04_28.md`는 대체로 사실에 맞는 진행 보고서다. 다만 “완료”라는 단어는 구조 보강 완료로 읽어야지, 운영 검증 완료로 읽으면 과대해석이다.**