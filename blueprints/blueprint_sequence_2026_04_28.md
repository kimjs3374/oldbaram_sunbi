# Blueprint — Sequence (2026-04-28 초안)

> v1 SoR: `dist_dosa/src/input/skill_scheduler.py`, `skill_blueprints.py`, `workers/healer_main_loop.py`
> v2: `dist_dosa/src_v2/hands/sequences/*.py`

## 1. 4단계 구조 강제

모든 sequence 함수는 다음 4단계로 분해된다 (P2-3 C 지시):

```
def <name>_seq(ctx: dict) -> None:
    # 1) precondition — release_all / pre-toggle (HOME/ESC/TAB) / target lock
    # 2) body — VK 송신 (transport mode 필수 명시)
    # 3) verify — verify_kind 검증 (cooldown OCR / buff OCR / None)
    # 4) cleanup — ESC / TAB×2 / movement_lock 해제
```

## 2. Transport Mode 표

세 종류만 허용. 각 sequence 가 반드시 명시.

| Mode | 의미 | VK 예 |
|---|---|---|
| `MainDigit` | 메인 숫자키(1-9, 0). pre_block hook 으로 차량 체인 등 | `0x31`(1) … `0x30`(0), `Shift+Z` 등 |
| `NumPadLocked` | NumLock ON 상태에서 NUMPAD 키 송신 (cycler 동기) | `0x60`(NUMPAD0) … `0x69`(NUMPAD9) |
| `NumPadDirect` | NumLock 무관 직송신 (skill 시퀀스 도중 lock 안 풀고 직접 넣음) | NUMPAD scan code 직접 |

**위반 금지**: NumPad 스킬을 메인 숫자키로 송신하는 패턴(과거 버그 재발 방지).

## 3. Sequence 인벤토리

### 3.1 self_heal (자힐)
- v2: `src_v2/hands/sequences/self_heal_seq.py`
- precondition: `release_all` → HOME → TAB (pre_block_ab 패턴)
- body: NUMPAD1 burst (메인힐), `transport=NumPadLocked`
- verify: 없음 (HP 자체가 시퀀스 결과; HP edge 룰이 재시도)
- cleanup: ESC on fail; movement_lock 해제 (blocks_movement=True)
- duration: ~2.0s

### 3.2 self_revive (자가부활)
- v2: `self_revive_seq.py`
- precondition: HOME→TAB (pre_block_ab) → 이어서 자힐 burst
- body: NUMPAD6 (부활) `NumPadLocked` → 자힐 NUMPAD1 후속
- cleanup: ESC → TAB×2 (post_block)
- duration: 1.5s + 자힐
- blocks_movement=True

### 3.3 gyoungryeok (공력증강)
- v2: `gyoungryeok_seq.py`
- precondition: 없음
- body: NUMPAD3 single tap, `transport=NumPadLocked`, hold=`SEQ_A_TAP_HOLD_MIN_MS`
- verify: 없음 (edge 룰)
- post-hook: `hpmp.allow_hp_drop_for(allow_sec)` — 5초 HP drop 허용
- duration: 1.0s

### 3.4 baekho (백호의희원)
- v2: `baekho_seq.py`
- body: NUMPAD4 burst, `NumPadLocked`
- verify_kind: `cooldown` (cooldown OCR 5초 게이트)
- retry_until_ready=True, MAX_UNTIL_READY 회까지 재시도
- duration: 2.0s

### 3.5 parlyuk (파력무참)
- v2: `parlyuk_seq.py`
- body: NUMPAD8 burst, `NumPadLocked`
- verify_kind: `buff` (파력무참 buff active)
- side: parlyuk active 시 `coord_tol=1` 강제 (P1-3 — runtime cfg 단일화 필요)
- duration: 2.0s

### 3.6 parhon (파혼술)
- v2: `parhon_seq.py`
- precondition: cast_parhon_hook (격수 타겟 lock)
- body: NUMPAD7 direct scan (`NumPadDirect`)
- duration: 0s burst (즉발)

### 3.7 mujang (무장)
- v2: `mujang_seq.py`
- precondition: Shift+Z (차량 토글)
- body: Shift+C, `transport=MainDigit`
- cleanup: 차량 복귀
- duration: ~0.5s
- cooldown 15s

### 3.8 boho (보호)
- v2: `boho_seq.py`
- precondition: Shift+Z (차량)
- body: Shift+X, `transport=MainDigit`
- duration: ~0.5s
- cooldown 15s

### 3.9 geumgang (금강불체)
- v2: `geumgang_seq.py`
- body: NUMPAD0, `NumPadLocked`
- duration: 0.8s
- 기본 disabled (manual)

### 3.10 attacker_revive (격수부활)
- v2: `attacker_revive_seq.py`
- body: NUMPAD6, `NumPadLocked`
- duration: 1.5s

### 3.11 tab_lock
- v2: `tab_lock_seq.py`
- precondition: ESC (release all)
- body: TAB → TAB (white tab → red tab 전환 보장)
- duration: 3~5s grace
- transport: SendInput 직접

### 3.12 seq_rclick
- v2: `seq_rclick_seq.py`
- body: RClick @ `(abs_x, abs_y)` (격수 머리 위 빨탭 추격)
- duration: 0.5s throttle
- background sub-loop while `heal_in_progress=True`

## 4. 공통 후처리 훅 (P2-3 C 지시)

다음을 sequence 별로 매번 인라인하지 말고 공통 훅으로 추출:

- `cleanup_movement_lock(ctx)` — `blocks_movement=True` 인 시퀀스 종료시 `dispatcher.set_movement_lock(False)`
- `restore_target(ctx)` — TAB self-target 복귀
- `ensure_numlock_off(ctx)` — NumLockCycler 와 동기

위 3개를 `hands/sequences/_common.py` 에 통합 (이미 일부 존재 — `sleep_ms`, `SLOT_*`).

## 5. retry / verify 정책

- `retry_until_ready=True` + `verify_kind=<cooldown|buff>` 조합만 허용
- 검증 실패 시 `MAX_UNTIL_READY` 회까지 burst 반복 (`skill_executor.py:255`)
- `retry_max` 직접 지정 시 fixed N 회
- 시퀀스 자체 예외는 verify_kind 무관 retry 대상

## 6. 위반 사례 (검출됨)

| 위반 | 위치 | 영향 |
|---|---|---|
| transport mode 명시 없음 | 12개 sequence 전체 | NumPad 오송신 위험 (P0-5) |
| precondition/body/verify/cleanup 4단계 미강제 | 여러 sequence | 후처리 누락 시 movement_lock 잔류 |

## 7. 다음 단계

- [ ] 각 sequence 함수 시그니처에 `transport_mode: TransportMode` 강제
- [ ] `Precondition / Body / Verify / Cleanup` 4단계 데코레이터 도입 검토
- [ ] 회귀 테스트: 모든 sequence 가 movement_lock 잔류 0건
