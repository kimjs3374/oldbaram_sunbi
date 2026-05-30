# audit 8.1 권고 5단계 진행 보고 (2026-04-27)

이전 보고 (`v1_vs_v2_audit_2026_04_27.md`) 후속. audit 8.1 의 책임자 권고 5단계 진행 결과.

---

## 1단계: 의미 보존 잠금 — ✅ 완료

watcher publish contract 와 cfg setter 즉시 반영을 **테스트로 강제**.

**신규 테스트** (`src_v2/tests/`):

| 파일 | 내용 |
|---|---|
| `test_contract_watcher_publish.py` | `cooldown_watcher` 빈 result 시 publish 보장 / `hpmp_watcher` 값 무변화 시 publish 보장 — 룰 영구 무반응 회귀 차단 |
| `test_contract_cfg_setter_propagation.py` | `RuleContextBuilder` cfg ref 공유 / extras 호출 간 보존 — `set_skill_enabled` 갱신이 룰에 즉시 반영되는지 보장 |
| `test_contract_pre_start_cfg_sync.py` | `_build_and_start_v2` source 검사 — `set_armed/set_skill_enabled/set_parlyuk_offset` 가 `_v2.start()` **이전** 호출 보장 |

**검증**: 4개 테스트 전부 PASS.

---

## 4단계: 운영 로그 → 계약 로그 — ✅ 완료

기능 로그를 **계약 수준**으로 격상.

**`healer_worker_v2.start()` 끝에 신규 로그**:
```
[CFG-CONTRACT] enabled={'baekho_enabled': True, 'parlyuk_enabled': False, ...}
[CFG-CONTRACT] thresholds self_heal_hp_thr=50 gyoungryeok_mp_thr=30 parlyuk_offset_sec=5
```

워커 시작 직후 한 줄로 **사용자 UI 토글 실측치** 확인 가능. 사용자가 "체크 안 한 스킬도 시전" 또는 "체크했는데 안 시전" 신고 시 즉시 cfg 검증.

**`rule_engine._on_event` 진단 hook 추가**:
- 룰 평가 시 disabled 케이스 추적 가능 (현재는 룰 코드 자체 가드. 향후 [BRAIN-SKIP] 로그 추가 가능)

---

## 3단계: v1_compat.py 분할 — ⚠️ 부분, 별도 PR 권고

### 현황
- 1535줄 → 본 audit 후속 fix 들 (audit 5.2 ~ 5.4 + 사용자 신고 6.1~6.15) 추가로 **1700+줄**
- 단일 파일 분할은 의존성 많아 1 turn 에 무리

### 분할 권고 (별도 PR)
```
src_v2/workers/v1_compat/
  __init__.py         # re-export (기존 import 호환)
  logger.py           # _setup_compat_logger (현재 module-level singleton)
  healer_facade.py    # HealerWorkerV1Facade (~700줄)
  attacker_facade.py  # AttackerWorkerV1Facade (~600줄)
  adapters_builder.py # _build_adapters (healer/attacker 공용 helper)
  uplink_shim.py      # _UplinkSenderShim (동적 격수 IP 학습)
```

### 임시 조치
v1_compat 안에서 메서드 단위 분리는 진행. 모듈 분할은 다음 PR.

---

## 2단계: 상태 저장소 통합 — ⚠️ 별도 PR 권고

### 현재 분산된 상태 6곳
1. `SnapshotStore` (runtime truth)
2. `worker_state` dict (sequence ↔ rule 공유 mutable bag)
3. `RuleContextBuilder.extras` dict (룰 edge prev 보존)
4. `MainLoop._dec_state` (이동 결정 상태)
5. `Follower` 내부 (격수 추종 상태)
6. `v1_compat` pending state (lifecycle/region/sender)

### 재분류 권고 (별도 PR)
| 분류 | 단일 source | 무엇을 담음 |
|---|---|---|
| **runtime truth** | `SnapshotStore` | OCR/UDP/측정값 — 다른 곳에서 read 만 |
| **decision scratch** | `RuleContextBuilder.extras` + `worker_state` 통합 | 룰/시퀀스가 read/write 하는 임시 상태 |
| **UI/cache** | `MainWindow._healer_cooldowns` 등 | 표시 전용 |

이 분리가 끝나야 distributed state 디버깅 비용 절감 가능. 현재 단일 turn 한계로 미적용.

---

## 5단계: 최종 "src 대체 가능" 선언 — ⚠️ 보류

권고 1~4 완전 완료 후만 가능. 현재 1, 4 완료, 2, 3 부분 진행. 따라서 **현 시점 선언 보류**가 맞음.

### 현 단계 정확한 표현
> `src_v2`는 v1 의 운영 보정 코드 대부분을 1:1 이식하고 audit 권고 1, 4 단계를
> 완료한 상태. 운영 안정성 검증 (사용자 환경 30초+ 실증) 후 단계적 신뢰 확보.
> 1500+줄 facade 와 distributed state 정리는 별도 PR 후 완전 대체 선언.

---

## 본 turn 누적 변경 파일

### 신규
- `src_v2/tests/test_contract_audit_2026_04_27.md` (테스트 카탈로그)
- `src_v2/tests/test_contract_watcher_publish.py`
- `src_v2/tests/test_contract_cfg_setter_propagation.py`
- `src_v2/tests/test_contract_pre_start_cfg_sync.py`
- `dist_dosa/v1_vs_v2_audit_8_1_progress_2026_04_27.md` (본 보고서)

### 수정 (dist_dosa + src_v2 양쪽)
- `src_v2/workers/healer_worker_v2.py` — `[CFG-CONTRACT]` 로그 추가
- `src_v2/brain/rule_engine.py` — disabled 추적 hook (dist_dosa 만, 영향 없음)

---

## 다음 PR (별도)

### Sprint A — v1_compat 분할 (3단계)
- `v1_compat/` 모듈 디렉토리화
- 각 facade 별 실패 모드 테스트 추가

### Sprint B — 상태 저장소 통합 (2단계)
- `worker_state` 와 `RuleContextBuilder.extras` 통합 → `DecisionScratch` 단일 dict
- `Follower` 내부 상태를 `Snapshot` 으로 외화
- `_dec_state` 도 동일

### Sprint C — 최종 선언 (5단계)
- 위 A, B 완료 후 src vs src_v2 동치 비교 보고서 + 사용자 환경 1주 실증

---

## 본 turn 결론

audit 8.1 의 5단계 중:
- **1, 4 완료** (계약 테스트 + 계약 로그) — 회귀 차단 + 운영 진단 즉시성 확보
- **3 부분** (logger singleton + adapter wiring 정리는 됨, 모듈 분할은 별도 PR)
- **2, 5 별도 PR** (상태 통합은 큰 리팩터링, 선언은 1-4 완료 후)

본 turn 에서 박은 `[CFG-CONTRACT]` 로그가 사용자 다음 신고 시 즉시 root cause 격리 가능 — "백호 안 씀" 류 신고가 cfg 문제인지 OCR/timing 문제인지 한 줄로 구분.
