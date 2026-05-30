# Fix: esc_recover → 흰탭 오감지 → TAB-CONFIRM 루프 (2026-05-02)

## 증상

힐러1.txt 로그에서 아래 패턴이 약 8초 간격으로 무한 반복됨:

```
[RECOVERY] self_heal no_effect → ESC + 재시도
[HANDS] sequence start name=esc_recover
[WHITETAB-ARM] confirm=3 map_neq=False fsm=FOLLOW h_map='제3흉노족1' a_map='제3흉노족1'
[TAB-CONFIRM-HOME] sub='wait_red'
[TAB-CONFIRM-TAB] sub='wait_red' retry=0
[TAB-CONFIRM-DONE] 복귀 확정 route=A
```

실제로는 맵 이동이 전혀 없는데 TAB-CONFIRM Route A가 반복 실행되어
HOME → TAB 키가 주기적으로 게임 창에 입력됨.

---

## 원인 분석

### 원인 1 (근본): `_v_self_heal` HP 오판단

`outcome_verifier.py`의 `_v_self_heal` 검증 조건:
```python
return a["hp"] > b["hp"] + 5   # HP가 5% 이상 올라야 OK
```

HP가 이미 95%~100%인 상태에서 자힐을 시전하면 HP 변화가 5% 미만 → `no_effect` 판정.
→ `RecoveryDispatcher`가 `esc_recover` + `self_heal 재시도` 큐에 올림.
→ 힐이 정상 작동 중인데도 루프가 시작됨.

### 원인 2 (연쇄): ESC 입력 후 YOLO 흰탭 오감지

`esc_recover_seq`: `VK_ESC` 1회 탭 (채팅 팝업 닫기 목적).

ESC 입력 직후 화면이 순간적으로 바뀌면서 YOLO가 흰탭을 3프레임 연속 감지
→ `tab_confirm_driver.py` ARM gate 충족 (`confirm >= 3, map_neq=False, fsm=FOLLOW`)
→ `follower.arm_tab_confirm()` 호출 → TAB-CONFIRM Route A 실행
→ `[TAB-CONFIRM-DONE] 복귀 확정` 후 다시 원인 1 루프로 돌아옴.

---

## 수정 내용

### 수정 1 — `src_v2/memory/outcome_verifier.py`

**HP ≥ 95%이면 힐 효과 있음으로 간주 (no_effect 오판 방지)**

```python
# 변경 전
def _v_self_heal(b, a):
    if b["hp"] < 0 or a["hp"] < 0:
        return False
    return a["hp"] > b["hp"] + 5

# 변경 후
def _v_self_heal(b, a):
    if b["hp"] < 0 or a["hp"] < 0:
        return False
    if a["hp"] >= 95:   # 이미 충분히 찼으면 OK
        return True
    return a["hp"] > b["hp"] + 5
```

---

### 수정 2 — `src_v2/core/snapshot.py`

**ESC suppress 타임스탬프 필드 추가**

```python
# post_self_heal_tab_until 아래에 추가
esc_suppress_tab_until: float = 0.0
```

---

### 수정 3 — `src_v2/brain/recovery.py`

**RecoveryContext에 store 파라미터 추가**

```python
class RecoveryContext:
    def __init__(self, ..., store: Any = None):
        ...
        self.store = store
```

**RecoveryDispatcher 생성자에 store 파라미터 추가 (ctx에 전달)**

```python
class RecoveryDispatcher:
    def __init__(self, ..., store: Any = None):
        self.ctx = RecoveryContext(..., store=store)
```

**`_r_self_heal`: ESC 실행 전 2초 suppress 설정**

```python
@recovery("self_heal", on=["no_effect", "timeout"], cooldown_sec=3.0)
def _r_self_heal(outcome, ctx):
    ctx.log_emit("[RECOVERY] self_heal no_effect → ESC + 재시도")
    if ctx.store is not None:
        try:
            ctx.store.update(esc_suppress_tab_until=time.time() + 2.0)
        except Exception:
            pass
    return [
        CastRequest(name="esc_recover", priority=5),
        CastRequest(name="self_heal", priority=8, ctx={"_retry": True}),
    ]
```

---

### 수정 4 — `src_v2/workers/healer_worker_v2.py`

**RecoveryDispatcher 생성 시 store 전달**

```python
self.recovery = RecoveryDispatcher(
    self.bus, self.hands_api,
    keys_adapter=keys,
    worker_state=self._worker_state,
    log_emit=self._log_emit,
    enabled=True,
    store=self.store,   # 추가
)
```

---

### 수정 5 — `src_v2/eyes/tab_confirm_driver.py`

**ARM gate에 suppress 윈도 체크 추가**

```python
esc_suppress_until = float(getattr(snap, "esc_suppress_tab_until", 0.0) or 0.0)

if (state._whitetab_confirm >= WHITETAB_CONFIRM_STREAK
        and not red_raw
        and not tab_active
        and a_coord_valid
        and arm_ok_fsm
        and not follow_only
        and now >= esc_suppress_until):   # 추가: suppress 해제 후에만 ARM
```

---

## 방어 계층 요약

```
self_heal 시전
  └─ OutcomeVerifier 검증 (5초 deadline)
       ├─ HP ≥ 95% → OK (수정 1) ← 대부분 케이스 여기서 종료
       └─ HP 변화 +5% 이상 → OK
       └─ 아닌 경우 → no_effect → RecoveryDispatcher
            └─ _r_self_heal
                 ├─ esc_suppress_tab_until = now + 2.0 설정 (수정 3)
                 ├─ esc_recover CastRequest
                 └─ self_heal 재시도 CastRequest

ESC 키 입력 (esc_recover_seq)
  └─ YOLO 흰탭 감지 가능성
       └─ tab_confirm_tick ARM gate
            └─ now < esc_suppress_until → ARM 차단 (수정 5) ← 2중 방어
```

---

## C:\oldbaram 복사 필요 파일

| 파일 | 비고 |
|---|---|
| `src_v2/memory/outcome_verifier.py` | HP ≥ 95% OK 조건 |
| `src_v2/core/snapshot.py` | esc_suppress_tab_until 필드 |
| `src_v2/brain/recovery.py` | store 파라미터 + suppress 설정 |
| `src_v2/eyes/tab_confirm_driver.py` | ARM gate suppress 체크 |
| `src_v2/workers/healer_worker_v2.py` | store 전달 + ocr/hpmp poll_sec 변경(이전 세션) |

---

## 검증 포인트 (재기동 후 로그 확인)

| 확인 항목 | 기대 결과 |
|---|---|
| `[RECOVERY] self_heal no_effect` 반복 여부 | HP 정상 구간에서는 발생 안 함 |
| `[WHITETAB-ARM]` 직후 `map_neq=False` 패턴 | suppress 2초 내 발생 없음 |
| `[TAB-CONFIRM-DONE]` 빈도 | 실제 맵이동 시에만 출력 |
| HP 95% 상태에서 자힐 outcome | `status=ok` (no_effect 아님) |
