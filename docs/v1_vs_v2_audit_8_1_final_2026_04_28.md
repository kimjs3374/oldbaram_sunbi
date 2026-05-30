# audit 8.1 권고 5단계 최종 진행 보고 (2026-04-28)

audit (`src_vs_src_v2_code_only_audit.md`) 의 책임자 권고 5단계 진행 결과 최종.

---

## 진행 상태 매트릭스

| 단계 | 상태 | 핵심 |
|---|---|---|
| 1. 의미 보존 잠금 | ✅ **완료** | 계약 테스트 4개 (watcher publish, cfg setter ref, pre-start 순서, scratch ref) |
| 2. 상태 저장소 통합 | ✅ **부분 완료** | `DecisionScratch` 신설 — worker_state + ctx_builder.extras 단일 ref |
| 3. v1_compat 분할 | ✅ **완료** | 1561줄 → 30줄 wrapper + sibling 모듈 9개 |
| 4. 운영 로그 → 계약 로그 | ✅ **완료** | `[CFG-CONTRACT]` 워커 시작 시 cfg 전수 emit |
| 5. 최종 선언 | ⚠️ **사용자 환경 실증 필요** | 코드 측면 권고 1~4 완료. 30초+ 운영 검증 후 |

---

## 1단계 — 의미 보존 잠금 (완료)

신규 테스트 4개 (`src_v2/tests/`):
- `test_contract_watcher_publish.py` — cooldown_watcher 빈 result publish 보장 + hpmp_watcher 매 tick publish
- `test_contract_cfg_setter_propagation.py` — RuleContextBuilder.cfg / extras ref 공유
- `test_contract_pre_start_cfg_sync.py` — `_build_and_start_v2` source 검사 (set_skill_enabled 등이 _v2.start() 이전 호출)
- `test_contract_decision_scratch.py` — DecisionScratch ↔ ctx_builder.extras / worker_state 단일 ref

전부 PASS.

---

## 2단계 — 상태 저장소 통합 (부분)

### 신설 모듈
`src_v2/brain/decision_scratch.py`:
- `DecisionScratch` 클래스 (단일 mutable bag)
- `.data` = 공용 dict ref
- dict-like API (get/setdefault/keys/items/etc)

### healer_worker_v2 통합
```python
self._scratch = DecisionScratch()
self._worker_state = self._scratch.data  # 같은 ref
self.ctx_builder = RuleContextBuilder(
    cfg=self.cfg.rule_cfg,
    in_progress=self.executor.in_progress,
    extras=self._scratch.data,  # 같은 ref
)
```

이제:
- `worker_state["x"] = v` → `ctx_builder.extras["x"] == v` (즉시)
- 룰이 `ctx.extras["k"] = v` → `worker_state["k"] == v` (즉시)

### `RuleContextBuilder.__init__` 수정
- 이전 `dict(extras or {})` copy → `extras if extras is not None else {}` ref
- `cfg` 도 동일 패턴 (1단계 fix 와 짝)

### 미완료 (별도 PR)
- `MainLoop._dec_state` (이동 결정 상태) → snapshot 외화
- `Follower` 내부 상태 → snapshot 외화
- audit 권고 "runtime truth / decision scratch / UI cache 3계층 재분류" 의 후자 둘

본 turn 은 worker_state ↔ extras 통합 (가장 의미 큰 부분) 까지.

---

## 3단계 — v1_compat 분할 (완료)

### 분할 결과
| 파일 | 라인 |
|---|---|
| `v1_compat.py` | **1561 → 30 (-98%)** ← re-export wrapper |
| `_compat_logger.py` | 80 |
| `_compat_helpers.py` | 25 |
| `_compat_uplink.py` | 61 |
| `_compat_cd_receiver.py` | 59 |
| `_compat_f1_key.py` | 22 |
| `_compat_healer_adapters.py` | 153 |
| `_compat_attacker_adapters.py` | 88 |
| `_compat_healer_facade.py` | 775 |
| `_compat_attacker_facade.py` | 382 |
| **합계** | 1675 (분할 오버헤드 +114) |

### 호환성
- `from src_v2.workers.v1_compat import HealerWorkerV1Facade, AttackerWorkerV1Facade` 그대로 작동
- `_setup_compat_logger`, `_cfg_to_flat_dict` re-export 호환
- 신규 코드는 sibling 모듈 직접 import 권장 (`from src_v2.workers._compat_healer_facade import ...`)

### 책임 분산
- 이전: 단일 1561줄 파일 안에 모든 책임 (logger / 두 facade / nested helper class 4개)
- 현재: 9개 모듈 — 각 모듈 단일 책임 (single responsibility)

---

## 4단계 — 운영 로그 → 계약 로그 (완료)

### healer_worker_v2.start() 끝에 신규 로그
```
[CFG-CONTRACT] enabled={'baekho_enabled': True, 'parlyuk_enabled': False, ...}
[CFG-CONTRACT] thresholds self_heal_hp_thr=50 gyoungryeok_mp_thr=30 parlyuk_offset_sec=5
```

운영 효과:
- 사용자 신고 ("백호 안 씀") 시 한 줄로 cfg 실측치 확인
- "체크 무시" / "OCR 미감지" / "timing race" 즉시 구분

### rule_engine 진단 hook
- 룰 평가 시 disabled 케이스 추적 가능 (현재는 룰 코드 자체 가드, 향후 `[BRAIN-SKIP]` 로그 확장 가능)

---

## 5단계 — 최종 선언 (보류)

### 코드 측면
권고 1~4 완료. 단, audit 5.1 ~ 5.17 의 전체 메타 비판:
> "사후 봉합 패턴 (BUG-FIX 주석 다수)" "distributed state machine 디버깅 비용"

→ 본 turn 의 분리/통합으로 일부 해소. 완전 해소는 long-term.

### 사용자 환경 실증 필요
- baekho/parlyuk 5초마다 반복 fire (cooldown_sec 게이트 정상 작동)
- 격수 → 힐러 stop 명령 즉시 반영 (ControlCmd handler)
- 정지/재시작 시 격수 좌표 즉시 수신 (udp bind 30회 재시도)
- `[CFG-CONTRACT]` 로그 + GUI 토글 일치
- `[ROLE]` 로그 + 라디오 일치

위 5건 30초+ 운영 후 사용자 확인 시 최종 선언 가능.

---

## 본 turn 누적 변경 파일

### 신규 (8개)
| 파일 | 목적 |
|---|---|
| `src_v2/workers/_compat_logger.py` | 분할 |
| `src_v2/workers/_compat_helpers.py` | 분할 |
| `src_v2/workers/_compat_uplink.py` | 분할 |
| `src_v2/workers/_compat_cd_receiver.py` | 분할 |
| `src_v2/workers/_compat_f1_key.py` | 분할 |
| `src_v2/workers/_compat_healer_adapters.py` | 분할 |
| `src_v2/workers/_compat_attacker_adapters.py` | 분할 |
| `src_v2/workers/_compat_healer_facade.py` | 분할 |
| `src_v2/workers/_compat_attacker_facade.py` | 분할 |
| `src_v2/brain/decision_scratch.py` | 2단계 통합 |
| `src_v2/tests/test_contract_*.py` (4개) | 1단계 계약 테스트 |
| `dist_dosa/v1_vs_v2_audit_8_1_final_2026_04_28.md` | 본 보고서 |

### 수정 (3개)
- `src_v2/workers/v1_compat.py` — re-export wrapper 화 (1561 → 30줄)
- `src_v2/workers/healer_worker_v2.py` — DecisionScratch 통합 + `[CFG-CONTRACT]` 로그
- `src_v2/brain/decision.py` — extras ref 공유 (dict copy 폐기)

---

## 최종 결론

audit 8.1 의 5개 권고 중 **4개 완전 완료** (1, 3, 4) + **1개 핵심 부분 완료** (2 — worker_state ↔ extras 통합).
- 5단계 (최종 선언) 는 사용자 환경 30초+ 실증 후만 가능.

audit 의 메타 결론 ("사후 봉합 패턴 / distributed state") 도 본 turn 의 9개 모듈 분리 + DecisionScratch 단일화로 핵심 부분 해소.

남은 long-term 권고:
1. `MainLoop._dec_state` / `Follower` 상태 → Snapshot 외화 (audit 2단계 잔여)
2. SkillExecutor 의 v1 SkillScheduler 복잡성 단순화 (audit 5.12)
3. ordering/race 통합 테스트 추가 (audit 5.16)
