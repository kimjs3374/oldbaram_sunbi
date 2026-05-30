# Blueprint — State (2026-04-28 초안)

> v1 SoR: `dist_dosa/src/workers/healer_worker.py`, `app/attacker.py`, `vision/hpmp.py`, `net/udp_receiver.py`
> v2: `dist_dosa/src_v2/core/snapshot.py`, `brain/decision_scratch.py`, `muscle/main_loop.py`, facade

## 1. 4계층 상태 분류 (P2-3 E 강제)

```
RuntimeSnapshot     — 관측 truth (vision/network 직접 결과)
DecisionState       — 이동/판단용 파생 상태 (룰 prev/edge, follower)
SequenceScratch     — 단기 실행 scratch (sequence 도중 임시값)
ConfigRuntime       — 동적 cfg truth (UI 토글, 학습 override, runtime overlay)
```

같은 의미를 두 곳에 저장하는 구조 금지.

## 2. 계층별 owner 표

### 2.1 RuntimeSnapshot — owner: `SnapshotStore` (`core/snapshot.py`)

| 필드 | 타입 | 갱신 | 읽기 | 비고 |
|---|---|---|---|---|
| `last_frame` | ndarray | `capture_watcher` | 모든 vision watcher | |
| `monitor_origin` | (int,int) | UI/region 설정 | watcher origin 변환 | |
| `game_region_abs` | (x,y,w,h) | `set_game_region` | yolo + ocr | |
| `hp / mp / hp_cur / mp_cur / hp_max / mp_max` | int | `hpmp_watcher._tick` | 룰 self_heal/self_revive/gyoungryeok | -1=미관측 |
| `cd_parlyuk / cd_baekho / cd_parhon / cd_revive` | int | `cooldown_watcher` (slot=cd) | 룰 baekho/parlyuk/parhon | -1=미관측, 0=ready |
| `buff_parlyuk_active / buff_baekho_active / buff_gyoungryeok_active` | **bool** | `cooldown_watcher` (slot=buff) | gyoungryeok rule etc. | **bool 필드 reset=False 강제 (현재 -1 reset 버그 P0-4)** |
| `self_debuff_honma_sec / self_buff_mujang_sec / self_buff_boho_sec` | int | buff watcher | (참고) | -1=미관측 |
| `attacker_state / attacker_coord / attacker_map / attacker_hp / attacker_seq` | mixed | `udp_watcher._tick` | 룰 격수 관련 | UDP State 결과 |
| `attacker_honma_sec / attacker_mujang_sec / attacker_boho_sec` | int | udp_watcher | 룰 parhon/mujang/boho | 격수 buff |
| `udp_active` | bool | udp_watcher | 워커 활성 게이트 | 5초 grace |
| `cooldown_reading` | obj | cooldown_watcher | UI overlay (legacy v1 호환) | |

### 2.2 DecisionState — owner: `RuleContextBuilder.extras` + Follower

| 필드 | 위치 | 갱신 |
|---|---|---|
| `mp_below_thr_prev` | `ctx.extras` | gyoungryeok rule edge tracking |
| `self_dead_prev / hp_below_thr_prev / atk_dead_prev` | ctx.extras | 자힐/자가부활/격수부활 edge |
| `last_cast_done_name` | ctx.extras | rule_engine `_on_cast_done` |
| `last_seq_rclick_target` | ctx.extras | seq_rclick 타겟 mirror |
| `_seq_rclick_target` | `worker_state` | self_heal_seq 가 저장 |
| `_parlyuk_buff_active / _coord_tol_saved` | follower 내부 | buff edge 시 coord_tol override |
| `_pending_tab_lock_until / _last_map_change_ts` | worker_state | tab_lock 게이트 |

**핵심 강제**: `RuleContextBuilder(in_progress=executor.in_progress, extras=worker_state)` 처럼 같은 ref 공유. 새 dict 생성 금지(`decision.py:55-61` 주석 참조).

### 2.3 SequenceScratch — owner: `ctx` dict (sequence 함수 인자)

매 sequence 호출마다 새로 만들어지는 dict. 다른 sequence 와 공유 금지.
- `_dispatcher`, `_request`, `_worker_state`(공유 ref), `_cycler`
- `_attempt`, `allow_hp_drop_sec`, `verify_kind`, `retry_until_ready`

### 2.4 ConfigRuntime — owner: 단일 `ConfigStore` 필요 (현재 산발)

| 필드 | 현재 위치 | 문제 |
|---|---|---|
| `gyoungryeok_mp_thr / self_heal_hp_thr / parlyuk_offset_sec` | `cfg` dict + `PluginRegistry` overlay | overlay 경로 OK |
| `coord_tol` | `runtime` 다중 (muscle / rule / integration_tick) — P1-3 truth 단일화 필요 |
| `<rule>_enabled` | UI 토글 → cfg dict | OK (decision.py:55 ref 보존) |
| `_map_transition_in_progress` | `rule_cfg` (`integration_tick.py:256`) | OK |
| `target_window / hwnd` | cfg.input | OK |

P1-3 지시: `coord_tol` 같은 동적 cfg 가 muscle / rule / integration_tick 에서 동일 truth 를 보게 단일 store 필요.

## 3. 위반 사례 (검출됨)

| 위반 | 위치 | 영향 |
|---|---|---|
| `buff_*_active` bool 필드를 `-1` int 로 reset | `cooldown_watcher.py:172-174` | gyoungryeok 영구 차단 (P0-4 root) |
| `coord_tol` truth 다중 | muscle / rule / integration_tick | parlyuk active 시 동기 깨짐 (P1-3) |
| `worker_state` vs `ctx.extras` ref 분리 사례 산발 | (잠재) | 룰 edge prev 보존 실패 가능 |

## 4. 상태 사전(key inventory) — 다음 단계

P2-1 완료 기준: owner 없는 상태키 0개.

다음 작업:
1. `RuntimeSnapshot` 모든 필드 일람 + owner watcher 명시 (자동 생성 가능)
2. `DecisionState` extras 키 일람 + 룰 owner 명시
3. `ConfigRuntime` 키 일람 + UI/learner/runtime override 경로 명시
4. 중복 캐시(cd_parlyuk vs cooldown_reading.cd_parlyuk 등) 1개로 통합

## 5. snapshot.py 타입 강제 (P0-4 fix 후 강화)

```python
# core/snapshot.py — bool 필드 명시
buff_parlyuk_active: bool = False
buff_baekho_active: bool = False
buff_gyoungryeok_active: bool = False

# int 필드 (-1=미관측, 0=ready)
cd_parlyuk: int = -1
cd_baekho: int = -1
self_buff_mujang_sec: int = -1
```

`SnapshotStore.update()` 에서 type 검증 추가 검토:
- `buff_*_active` 에 int 들어오면 reject 또는 bool() 변환

## 6. 다음 단계

- [ ] `cooldown_watcher.py` bool/int 필드 분리 reset (P0-4 fix)
- [ ] `coord_tol` 단일 ConfigStore 도입 (P1-3)
- [ ] state inventory 자동 생성 스크립트 (`core/snapshot.py` 파싱)
- [ ] 상태 type 위반 회귀 테스트
