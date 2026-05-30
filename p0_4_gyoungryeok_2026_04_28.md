# P0-4 — 공력증강(gyoungryeok) 미발화 (2026-04-28)

## 1. 문제

**증상**: MP 가 임계 이하로 떨어지는데 `[BRAIN] rule fired name=gyoungryeok` 로그 0건. sequence start/done 흔적 없음.

## 2. v1 SoR

### 2.1 룰 트리거 조건
`src/input/skill_blueprints.py:295`:
```python
return _self_mp_below(c, thr_mp)
```
- `thr_mp = self.gyoungryeok_mp_thr` (`workers/healer_worker.py:1228-1245`)
- `mp_below_now = (0 <= mp < thr_mp)`
- `prev=False → cast + prev=True`
- cast 직후 `hpmp.allow_hp_drop_for(5.0)` 호출 (HP 60% 감소 허용)

### 2.2 v1 buff 평가
`_buff_present(c, name)`: `c["buffs"]` (dict) 에 `name` 키 존재하면 True. 키 없으면 False.

## 3. v2 현재 상태 — 5개 가능성 역추적

`src_v2/brain/rules/gyoungryeok.py:42-75` 차단 분기:

| 분기 | line | 조건 | 진단 결과 |
|---|---|---|---|
| 1 | 43-44 | `"gyoungryeok" in ctx.in_progress` | `executor.in_progress` 와 ref 공유 (`healer_worker_v2.py:217-219`) — 정상 |
| 2 | 46-48 | `not ctx.cfg.get("gyoungryeok_enabled", True)` | default True — 정상 |
| 3 | 49-51 | `snap.buff_gyoungryeok_active` | **❌ root cause — bool 필드가 -1 reset 으로 항상 truthy** |
| 4 | 53-56 | `mp < 0` | 정상 (publish-on-empty 로 -1 publish 도 정상 평가) |
| 5 | 58-74 | edge: `mp_below_now AND not prev` | 정상 |

### 3.1 root cause 확정

**위치**: `src_v2/eyes/cooldown_watcher.py:172-186` (수정 전)

**버그 코드**:
```python
updates: Dict[str, Any] = {}
for field in set(self._field_map.values()):
    updates[field] = -1     # ← bool 필드도 -1 로 reset
for k, v in result.items():
    field = self._field_map.get(k)
    if field:
        updates[field] = int(v) if isinstance(v, (int, float)) else v
```

**검증 흐름**:
1. buff slot `_FIELD_MAP_BUFF` 의 value set 에 `buff_gyoungryeok_active` 포함 (Snapshot bool 필드)
2. 매 tick 위 코드가 `updates["buff_gyoungryeok_active"] = -1` 박음
3. result(`{}`)면 덮어쓰기 안 일어남 → store 에 `-1` 저장
4. `Snapshot` 은 dataclass — `buff_gyoungryeok_active: bool = False` 타입 hint 있으나 `SnapshotStore.update()` 가 강제하지 않음
5. 룰 line 49 `if snap.buff_gyoungryeok_active:` → Python: `if -1:` → True → return None (`buff_active_stuck`)
6. 결과: 룰 영원히 차단, `[GYOUNG-DIAG] block reason=buff_active_stuck` 가 1회 emit 되어야 했음 (`gyoungryeok.py:50`)

### 3.2 재현 메모

```
초기 상태: buff result = {} (공력증강 buff 없음, 정상)
↓
cooldown_watcher buff slot tick:
  for field in {buff_parlyuk_active, buff_baekho_active,
                buff_gyoungryeok_active,
                self_debuff_honma_sec, ...}:
    updates[field] = -1
  store.update(buff_gyoungryeok_active=-1, ...)
↓
hpmp_watcher tick (매 0.5s): publish eye.mp = (예) 30%
↓
rule_engine on eye.mp:
  ctx = builder.build(snap)
  for rule in [gyoungryeok, ...]:
    if "gyoungryeok" in ctx.in_progress: continue  # OK
    handler(snap, ctx) →
      "gyoungryeok" in ctx.in_progress  → False (OK)
      ctx.cfg.get("gyoungryeok_enabled", True)  → True (OK)
      snap.buff_gyoungryeok_active = -1  → if -1: True → 차단
    return None
↓
영원 fire 안 됨
```

## 4. 수정 내용 (fixed-in-code)

### 4.1 `src_v2/eyes/cooldown_watcher.py:172-200` 수정

bool 필드 집합을 분리하여 reset 시 `False`, 갱신 시 키 존재 여부로 판정:

```python
_BOOL_FIELDS = {
    "buff_parlyuk_active",
    "buff_baekho_active",
    "buff_gyoungryeok_active",
}
updates: Dict[str, Any] = {}
for field in set(self._field_map.values()):
    updates[field] = False if field in _BOOL_FIELDS else -1
for k, v in result.items():
    field = self._field_map.get(k)
    if not field:
        continue
    if field in _BOOL_FIELDS:
        # v1 _buff_present(c, name): 키 존재하면 active.
        if isinstance(v, bool):
            updates[field] = v
        else:
            updates[field] = True
    else:
        updates[field] = int(v) if isinstance(v, (int, float)) else v
```

### 4.2 효과
- buff result 가 `{}` 일 때 `buff_gyoungryeok_active=False` 박힘 → 룰 line 49 차단 해제
- buff result 가 `{"공력증강": <truthy>}` 들어오면 `True` → 룰 차단 (정상)
- v1 `_buff_present(c, name)` 동치: 키 존재 == active

## 5. 계약 테스트 (작성 필요)

`src_v2/tests/test_contract_buff_bool_reset.py` (TODO):
```python
def test_buff_empty_result_resets_bool_to_false():
    """buff slot 빈 result 시 bool 필드는 False, int 필드는 -1."""
    store = SnapshotStore()
    bus = EventBus()
    fake = FakeBuffAdapter(read_result={})
    w = CooldownWatcher(store, bus, adapter=fake, slot="buff", poll_sec=0.001)
    w.start(); time.sleep(0.05); w.stop()
    snap = store.read()
    assert snap.buff_gyoungryeok_active is False
    assert snap.buff_parlyuk_active is False
    assert snap.buff_baekho_active is False
    assert snap.self_buff_mujang_sec == -1

def test_buff_present_sets_bool_true():
    fake = FakeBuffAdapter(read_result={"공력증강": 30})
    ...
    assert snap.buff_gyoungryeok_active is True

def test_gyoungryeok_rule_fires_on_mp_below_thr():
    """MP 하강 시 gyoungryeok rule fire — 10회 반복."""
    # buff dict empty + mp 30% < thr 50%
    # rule_engine on eye.mp → CastRequest('gyoungryeok')
```

## 6. 런타임 검증 결과

- ❌ 미수행 (사용자 환경 검증 필요)
- 검증 시나리오:
  1. 힐러 PC 워커 start
  2. MP 임계 이하 진입 (격수 PC 에서 sustained MP 소모)
  3. 로그에서 다음 항목 확인:
     - `[BRAIN] rule fired name=gyoungryeok topic=eye.mp ...`
     - `[HANDS] sequence start name=gyoungryeok`
     - `[HANDS] sequence done name=gyoungryeok latency_ms=...`
  4. 10회 반복 검증 (P0-4 완료 기준)
  5. 부정 케이스: 공력증강 buff 발동 후 활성 동안 fire 차단 확인

## 7. 남은 리스크

1. **buff OCR 키 인식 실패**: 한국어 키 OCR 오인식(예: "공력증감"으로 읽힘) 시 active=False 로 잘못 판정 → 중복 시전. P1-1 buff OCR 품질 개선 필요.
2. **`snap.buff_*_active` type 보장 부재**: `SnapshotStore.update(**updates)` 가 type 검증 안 함. 향후 다른 watcher 가 잘못된 타입 박을 수 있음 → blueprint_state §5 후속 작업 필요.
3. **edge prev 잠금**: 한 번 fire 후 mp 회복(>thr)이 OCR 미관측(-1) 으로 보이면 prev 가 안 풀림. `_diag_once(prev_locked_no_edge)` 로그가 1회 emit 되므로 진단 가능.
4. **MP OCR 노이즈**: P0-0 항목 5 (MP smoothing) 와 연동. MP 0↔100% 튐이 있으면 prev oscillate.

## 8. 후속 권장

- `core/snapshot.py:50` `buff_gyoungryeok_active: bool = False` type 강제 위해 `SnapshotStore.update` 에 dataclass field 타입 검증 추가
- `[BRAIN-SKIP] rule=<name> reason=<reason>` 표준 로그를 모든 룰에 적용 (rule_engine 에서 일괄 emit 가능)
