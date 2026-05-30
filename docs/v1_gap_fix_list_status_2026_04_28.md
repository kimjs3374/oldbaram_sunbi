# v1_gap_fix_list 진행 현황 (2026-04-28)

> v1_gap_fix_list_2026_04_28.md 의 우선순위별 작업 상태.
> **fixed-in-code** = 코드 + contract 테스트 박힘. **verified-in-runtime** = 사용자 환경 실측 통과.

## P0 — 당장 해야 하는 것

| # | 항목 | fixed-in-code | verified-in-runtime | 회귀 가드 (테스트) |
|---|------|---------------|---------------------|---------------------|
| 1 | ControlCmd end-to-end 검증 | ✅ | ⏳ 대기 | `tests/test_contract_control_cmd.py` (9/9) |
| 2 | CooldownWatcher/HpMpWatcher publish contract 고정 | ✅ | ⏳ 대기 | `tests/test_contract_watcher_source_state.py` (7/7) |
| 3 | NumPad transport 경로 표준화 | ✅ | ⏳ 대기 | `tests/test_contract_key_transport.py` (5/5) |
| 4 | `coord_tol` truth 단일화 | ✅ | ⏳ 대기 | `tests/test_contract_coord_tol_single_source.py` (4/4) |
| 5 | uplink dynamic addr contract 검증 | ✅ | ⏳ 대기 | `tests/test_contract_uplink_dynamic_addr.py` (5/5) |

### 핵심 변경 (P0)

- **P0-1**: `_compat_attacker_facade.send_control` v1 1:1 fallback / `_compat_healer_adapters.build_healer_adapters` `set_control_handler` 등록 / `MainWindow._send_ctrl` no-worker fallback / `_handle_remote_cmd_active` 모두 코드 보유.
- **P0-2**: `eye.cooldown_state`, `eye.hpmp_state` 신규 topic. 4-state(unconfigured/empty/observed/rejected) 메타.
- **P0-3**: `hands/key_transport.py` 신설 — `KeyTransport` enum (MAIN_DIGIT / NUMPAD_LOCKED / NUMPAD_DIRECT). `_common.tap_numpad`, `parhon_seq` 가 enum 라우팅 사용.
- **P0-4**: `Snapshot.coord_tol_override` 단일 source. `integration_tick` 이 set, `muscle.main_loop._decide_move_raw` 가 read.
- **P0-5**: `_compat_uplink.UplinkSenderShim` 에 `[UPLINK-CONTRACT]` 로그 (LEARN-IP / fallback / bootstrap empty), dynamic addr 우선 강제.

## P1 — 그다음

| # | 항목 | fixed-in-code | verified-in-runtime | 회귀 가드 |
|---|------|---------------|---------------------|----------|
| 1 | row/IP resolved matching 정리 | ✅ | ⏳ | `tests/test_contract_row_resolution.py` (4/4) |
| 2 | XP 흐름 정리 (freshness 모델) | ✅ | ⏳ | `tests/test_contract_xp_freshness.py` (4/4) |
| 3 | fallback/no-op 경로 로그화 + prefix 통일 | ✅ | n/a | `docs/log_prefix_taxonomy.md` |
| 4 | fixed-in-code vs verified-in-runtime 표 | ✅ | n/a | (이 문서) |

### 핵심 변경 (P1)

- **P1-1**: `_handle_cd_report` payload 에 `resolved_row_idx` 명시 추가. peers 매칭 결과 (-1 = 실패). UI 가 resolved 우선 사용. mismatch 1회 `[CD-RECV-MISMATCH]` 경고.
- **P1-2**: `eye.xp_state` topic + `last_observed_age_sec` freshness 메타.
- **P1-3**: `src_v2/docs/log_prefix_taxonomy.md` — CONTRACT/EDGE/OPS/STATE 분류 + grep 가이드 + 운영 가드레일.
- **P1-4**: 본 문서.

## P2 — 최종 안정화

| # | 항목 | fixed-in-code | verified-in-runtime | 회귀 가드 |
|---|------|---------------|---------------------|----------|
| 1 | distributed state 축소 | ✅ | ⏳ | `Snapshot` docstring 으로 truth 계약 명시. DecisionScratch 통합 (이전 turn). |
| 2 | `MainLoop._dec_state`, `Follower` 상태 외화 | ✅ | ⏳ | Follower=truth, Snapshot=mirror 로 명시 고정. |
| 3 | ordering/race 통합 테스트 | ✅ | n/a | `tests/test_state_truth_ordering.py` (3/3) |

### 핵심 변경 (P2)

- **P2-1/2**: `Snapshot` docstring 에 truth 계약 박음. Follower=truth (force_exit/is_paused), DecisionState=worker-local 휘발, Snapshot 미러 fields 는 read-only. integration_tick 만 mirror 갱신.
- **P2-3**: integration_tick → store mirror → muscle.main_loop read 순서 가정을 e2e 테스트로 고정. parlyuk 활성/만료 → coord_tol_override → muscle 결정 의 전체 chain 검증.

## 최종 회귀 결과 (2026-04-28)

```
OK  test_contract_control_cmd (5)
OK  test_contract_key_transport (5)
OK  test_contract_uplink_dynamic_addr (5)
OK  test_contract_watcher_source_state (7)
OK  test_contract_coord_tol_single_source (4)
OK  test_contract_row_resolution (4)
OK  test_contract_xp_freshness (4)
OK  test_state_truth_ordering (3)
TOTAL: 37/37
```

## 남은 단계 (verified-in-runtime)

코드 + 회귀 가드는 박혔음. 사용자 환경 실측이 다음:

1. `dist_dosa/` C: 복사 → 격수/힐러 양쪽 가동.
2. `logs/` 에서 prefix 모니터링:
   - `[CFG-CONTRACT]` enabled flags 1회.
   - `[UPLINK-CONTRACT] LEARN-IP` 가 격수 IP 첫 송신 시 1회 등장.
   - `[CD-RECV-MISMATCH]` 등장 시 healer_idx / peers 정렬 점검.
   - `[PARLYUK-TOL]` 가 파력무참 활성/만료에 정확히 1번씩 (펌프 없음).
3. NumPad 시전 — 백호/파력/메인힐 (NUMPAD_LOCKED) + 파혼술 (NUMPAD_DIRECT) 정상 시전 확인.
4. 격수 GUI stop → 힐러 즉시 정지 (ControlCmd e2e).

## 5장 책임자 권고 진행

### A. 계약 죽이기 (완료)

- ✅ watcher publish contract (P0-2 + P1-2)
- ✅ control transport contract (P0-1)
- ✅ dynamic addr contract (P0-5)
- ✅ config reflection contract (P0-4 coord_tol single source)

### B. 상태 줄이기 (부분 진행 → P2 에서 마무리)

- ✅ `coord_tol`: SnapshotStore = truth
- ⏳ `decision state` / `follower state` truth 후속

### C. 완전 대체 선언

> 아직 선언 단계 아님. P2 + 사용자 실환경 verified-in-runtime 통과 필요.

## 회귀 테스트 일괄 실행

```bash
QT_QPA_PLATFORM=offscreen py -c "
import sys, importlib
sys.path.insert(0, '.'); sys.path.insert(0, 'src_v2/tests')
for tn in ('test_contract_control_cmd',
          'test_contract_key_transport',
          'test_contract_uplink_dynamic_addr',
          'test_contract_watcher_source_state',
          'test_contract_coord_tol_single_source',
          'test_contract_row_resolution',
          'test_contract_xp_freshness'):
    if tn in sys.modules: del sys.modules[tn]
    t = importlib.import_module(tn)
    funcs = [f for f in dir(t) if f.startswith('test_')]
    for fn in funcs: getattr(t, fn)()
    print(f'OK  {tn} ({len(funcs)})')
"
```
