# Blueprint — Rule (2026-04-28 초안)

> v1 SoR: `dist_dosa/src/input/skill_blueprints.py:186-396`, `skill_scheduler.py:31-195`, `workers/healer_worker.py`
> v2 룰: `dist_dosa/src_v2/brain/rules/*.py`

## 1. RuleSpec 강제 형식

각 룰은 코드/문서에서 다음 4개를 반드시 명시한다:

```
@rule(
    name="<rule_name>",
    priority=<int>,
    topics=["eye.<topic>", ...],   # subscribed
    description="...",
)
def rule_fn(snap, ctx) -> Optional[CastRequest]:
    # required fields = snapshot/ctx 에서 읽는 필드 명시
    # skip reasons = 각 분기마다 [BRAIN-SKIP] rule=<name> reason=<reason>
    # emitted request = CastRequest(name=..., priority=..., ctx=...)
```

`skip reasons` 표준값(고정 vocabulary):
- `in_progress_stuck` — 같은 룰 cast 진행 중
- `cfg_disabled` — `<rule>_enabled=False`
- `buff_active_stuck` — buff active 상태 (gyoungryeok 등)
- `cooldown_remaining` — 게이트 잔여 시간 > 0
- `mp_negative` / `hp_negative` — 미관측 (-1)
- `prev_locked_no_edge` — edge 룰의 prev=True 잠금
- `map_transition` — `_map_transition_in_progress=True`
- `armed_off` / `follow_only` — 워커 비활성

## 2. 룰 인벤토리 (12개)

### 2.1 self_heal
- v1: `skill_blueprints.py:269` `_self_hp_below(c, thr)`
- v2: `src_v2/brain/rules/self_heal.py`
- topics: `eye.hp`
- required: `snap.hp`, `ctx.cfg.self_heal_hp_thr`, `ctx.in_progress`
- gates: `in_progress("self_heal")`, `_map_transition_in_progress`
- emits: `CastRequest("self_heal", priority=2)` (HOME→TAB pre_block, NUMPAD1 burst)
- order: priority 2

### 2.2 self_revive
- v1: `skill_blueprints.py:227` `_self_dead(c)` (hp%==0)
- v2: `src_v2/brain/rules/self_revive.py`
- topics: `eye.hp`
- gates: edge_only, `in_progress("self_revive")`, blocks_movement
- emits: `CastRequest("self_revive", priority=0)` (NUMPAD6 + 자힐 burst)

### 2.3 gyoungryeok (공력증강) — 본 사건 핵심
- v1: `skill_blueprints.py:295` `_self_mp_below(c, thr)`
- v2: `src_v2/brain/rules/gyoungryeok.py`
- topics: `eye.mp`
- gates 순서:
  1. `in_progress("gyoungryeok")`
  2. `cfg.gyoungryeok_enabled` (default True)
  3. `snap.buff_gyoungryeok_active` ← **이번 사건 root: bool 필드가 -1로 reset 되어 항상 True**
  4. `snap.mp >= 0` (mp_negative)
  5. edge: `mp_below_now AND not mp_below_thr_prev`
- emits: `CastRequest("gyoungryeok", priority=20, ctx={allow_hp_drop_sec})`
- post-cast hook: `hpmp.allow_hp_drop_for(5.0)` — HP 60% 감소 허용 윈도우

### 2.4 baekho (백호의희원)
- v1: `skill_blueprints.py:327` `_cd_empty(c, "백호의희원")`
- v2: `src_v2/brain/rules/baekho.py`
- topics: `eye.cooldown` (slot=cd)
- gates: `in_progress`, cooldown OCR 게이트(5s), retry_until_ready=True
- emits: `CastRequest("baekho", priority=11, ctx={verify_kind="cooldown"})`

### 2.5 parlyuk (파력무참)
- v1: `skill_blueprints.py:311` `not _buff_present(c, "파력무참")`
- v2: `src_v2/brain/rules/parlyuk.py`
- topics: `eye.cooldown` (slot=buff)
- gates: in_progress, buff active 시 차단, retry_until_ready
- side effect: parlyuk active 동안 `coord_tol=1` 강제(P1-3)
- emits: `CastRequest("parlyuk", priority=10, ctx={verify_kind="buff", parlyuk_offset_sec})`

### 2.6 parhon (파혼술)
- v1: `skill_blueprints.py:347` `_attacker_debuff_present(c, "혼마술")`
- v2: `src_v2/brain/rules/parhon.py`
- topics: `eye.attacker_state` (격수 honma_sec) — 크로스-PC trigger
- gates: in_progress, edge_only
- emits: `CastRequest("parhon", priority=4)` (NUMPAD7 burst, pre_block=cast_parhon_hook)

### 2.7 mujang (무장)
- v1: `skill_blueprints.py:373` `_attacker_buff_missing(c, "무장")`
- v2: `src_v2/brain/rules/mujang.py`
- topics: `eye.attacker_state`
- gates: in_progress, cooldown_sec=15.0
- emits: Shift+Z→Shift+C (main digits, pre_block hook)

### 2.8 boho (보호)
- v1: `skill_blueprints.py:386` `_attacker_buff_missing(c, "보호")`
- v2: `src_v2/brain/rules/boho.py`
- topics: `eye.attacker_state`
- emits: Shift+Z→Shift+X (main digits, pre_block hook), priority 5, cooldown 15s

### 2.9 geumgang (금강불체) — manual
- v1: `skill_blueprints.py:359` `lambda _c: False`
- v2: `src_v2/brain/rules/geumgang.py`
- 기본 disabled. 수동 트리거만.

### 2.10 attacker_revive (격수부활)
- v1: `skill_blueprints.py:245` `_attacker_dead(c)` (atk_hp==0 AND self_hp>0)
- v2: `src_v2/brain/rules/attacker_revive.py`
- topics: `eye.attacker_state`, `eye.hp`
- gates: in_progress, `_map_transition_in_progress` 차단
- emits: `CastRequest("attacker_revive", priority=1)` (NUMPAD6 burst)

### 2.11 tab_lock
- v1: `healer_worker.py:195-210`
- v2: `src_v2/brain/rules/tab_lock.py`
- topics: `eye.attacker_state` (map_change_pending)
- gates: `_pending_tab_lock_until`, `_post_mapchg_grace_sec`
- emits: ESC→TAB→TAB sequence

### 2.12 seq_rclick
- v1: `healer_worker.py:206-211`
- v2: `src_v2/brain/rules/seq_rclick.py`
- topics: `eye.yolo` (red_tab) + `hand.cast_done` (자힐 후 추격)
- gates: in_progress, throttle 0.5s
- emits: RClick @ red_tab abs coord

## 3. 우선순위 표 (작은 값이 먼저)

| priority | rule | 비고 |
|---|---|---|
| 0 | self_revive | 사망 즉시 |
| 1 | attacker_revive | 격수 사망 |
| 2 | self_heal | HP 임계 |
| 4 | parhon | 격수 혼마 디버프 |
| 5 | mujang / boho | 격수 buff 보충 |
| 10 | parlyuk | 본인 buff |
| 11 | baekho | 본인 cooldown |
| 12 | geumgang | manual |
| 20 | gyoungryeok | MP 임계 (낮은 위험) |
| - | tab_lock / seq_rclick | 별도 게이트 |

## 4. ctx.in_progress 생명주기

- 추가: `SkillExecutor._dispatch_request` 직전 (`skill_executor.py:244`)
- 제거: `finally` 블록 (`skill_executor.py:322`)
- 공유 객체: `RuleContextBuilder(in_progress=executor.in_progress)` — 동일 set 인스턴스
- 위반 시 사망: 다른 곳에서 set 을 새로 만들면 룰이 영원히 차단됨

## 5. cfg overlay 경로

- `RuleContextBuilder._overlay()` 가 `PluginRegistry.snapshot_params()` 와 cfg 를 병합
- 학습 시스템(meta-learner)이 `rule.<name>.<param>` 키로 동적 override
- 매핑: `decision.py:18-30` `_CFG_TO_TARGET`

## 6. 위반 사례 (본 사건)

| 위반 | 위치 | 영향 |
|---|---|---|
| skip reason 로그 vocabulary 미통일 | 각 룰 산발 | `[BRAIN-SKIP]` prefix 통일 + reason 표준값 강제 필요 |
| buff bool 필드 reset 오류로 gyoungryeok 영구 차단 | `cooldown_watcher.py:174` | 룰 자체 무결, 신호 계약 위반 |

## 7. 다음 단계

- [ ] 모든 룰 skip reason 을 `[BRAIN-SKIP] rule=<name> reason=<reason>` 로 통일
- [ ] RuleSpec metadata 강제 (subscribed_topics / required_fields / skip_reasons / emits)
- [ ] 12개 룰 contract 테스트 작성 (publish 없을 때 / 임계 미달 / in_progress 등)
