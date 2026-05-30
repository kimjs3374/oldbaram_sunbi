# WHITETAB 거짓 ARM 근본 원인 분석 (2026-05-02)

> 목적: 사냥 중 힐러가 임의로 흰탭을 처리하면서 TAB-CONFIRM을 발동시켜 사냥을 방해하는 현상의 근본 원인을 코드/로그/메커니즘 관점으로 정리하고, 흰탭 처리 자체는 살리면서 거짓 ARM만 차단하는 개선안을 제안한다.

---

## 0. 결론 먼저

거짓 ARM의 본질은 "white_cache가 너무 공격적이어서"가 아니다.

> **`tab_confirm_driver.py`의 ARM gate 조건에 "맵이 다르다(map_neq)" 또는 "최근 red_tab을 보지 않았다(red_quiet)" 같은 컨텍스트 게이트가 아예 없다.** 흰탭 confirm streak 3 + red 동시 부재 + 기본적인 fsm/coord 검사만 통과하면 같은 맵에서 사냥 중에도 ARM이 발동된다. v1 SoR 패리티로 그대로 옮겨졌고, v1도 같은 약점을 가지고 있다.

즉 지금 문제는 white_cache가 거짓 양성을 만드는 게 아니라, **ARM gate가 컨텍스트(사냥 중인지, 맵 이동 후인지)를 구분하지 못한다.**

---

## 1. 증상 로그

### 1.1 세션 메타

- 파일: `dist_dosa/logs/healer_v2_20260502_163429.log`
- 길이: 1분 46초 (16:34:41 ~ 16:36:27)
- 패치 헤더: `[PATCH] 2026-05-02 yolo_stale_gate+white_cache | stale_ms=150 fresh_ms=30 poll_sec=0.09 white_cache_ttl_ms=250` ✅ 적용됨

### 1.2 주요 카운트

| 항목 | 값 |
|---|---|
| YOLO-SPIKE | 269 |
| YOLO-STALE | 357 (stale gate 정상 차단) |
| YOLO-WHITE-CACHE | 29 (대부분 invalidated by red_tab) |
| WHITETAB-ARM | **1** |
| TAB-CONFIRM-DONE | 1 (retry=0, 한 번에 성공) |
| HPMP-REJECT | 1 |
| CD-OCR-MISS | 12 |

### 1.3 거짓 ARM 발동 시퀀스

```
2026-05-02 16:34:51,427 [YOLO-WHITE-CACHE] invalidated by red_tab
2026-05-02 16:34:51,478 [YOLO-WHITE-CACHE] invalidated by red_tab
2026-05-02 16:34:51,528 [YOLO-WHITE-CACHE] invalidated by red_tab
2026-05-02 16:34:51,580 [YOLO-WHITE-CACHE] invalidated by red_tab
2026-05-02 16:34:51,634 [YOLO-WHITE-CACHE] invalidated by red_tab
2026-05-02 16:34:51,693 [YOLO-WHITE-CACHE] invalidated by red_tab  ← 마지막 red 무효화
2026-05-02 16:34:51,820 [WHITETAB-ARM] confirm=3 map_neq=False fsm=FOLLOW
                                       h_map='제3흉노족1' a_map='제3흉노족1'  ← 127ms 만에 ARM
2026-05-02 16:34:52,062 [TAB-CONFIRM-HOME] sub='A_wait_red'
2026-05-02 16:34:52,303 [TAB-CONFIRM-TAB]  sub='A_wait_red' retry=0
... TAB-CONFIRM-DONE
```

### 1.4 핵심 관찰

- **`map_neq=False`** — 힐러와 격수가 **같은 맵**(`제3흉노족1`)에 있음. 따라갈 필요 없음.
- **마지막 red 검출 후 127ms 만에 ARM** — 사냥 중인데 red가 잠깐 사라지고 흰탭이 3번 보인 짧은 window가 그대로 ARM 트리거가 됐다.
- **사용자 체감**: 사냥 10분도 못 함. 힐러가 지맘대로 흰탭 처리하면서 TAB-CONFIRM 돌리느라 사냥을 못 하게 만든다.

---

## 2. 코드 원인

### 2.1 ARM gate 위치

`dist_dosa/src_v2/eyes/tab_confirm_driver.py:127-133`:

```python
if (state._whitetab_confirm >= WHITETAB_CONFIRM_STREAK
        and not red_raw
        and not tab_active
        and a_coord_valid
        and arm_ok_fsm
        and not follow_only
        and now >= esc_suppress_until):
    follower.arm_tab_confirm(now, map_neq=map_neq)
```

### 2.2 누락된 조건

위 6개 조건만 통과하면 ARM이 떨어진다. **`map_neq` 자체는 인자로 전달만 되고 게이트로는 사용되지 않는다.** 즉 같은 맵에서 흰탭이 보여도 다음 조건만 만족하면 ARM이 떨어진다:

1. `_whitetab_confirm >= 3` — 흰탭 3프레임 연속 검출
2. `not red_raw` — 이번 틱에 red 없음
3. `not tab_active` — 진행 중 아님
4. `a_coord_valid` — 격수 좌표 유효
5. `arm_ok_fsm` — fsm이 DEAD/DISCONNECTED 아님
6. `not follow_only` — 따라가기 전용 모드 아님

### 2.3 v1 SoR 패리티

`dist_dosa/src/workers/healer_worker.py:2055-2070`도 동일하다:

```python
if (self._whitetab_confirm >= 3
        and not red_raw
        and not fol._tab_confirm_active
        and atk.coord_valid
        and arm_ok_fsm
        and not self.follow_only):
    self.log.info(f"[WHITETAB-ARM] confirm=...")
    fol.arm_tab_confirm(now_sec, map_neq)
```

v1도 `map_neq`를 로깅과 arm 인자로만 전달하고, gate 조건으로는 쓰지 않는다.
즉 이 약점은 v1 시절부터 그대로 이어져 온 것이다.

### 2.4 white_cache는 무관

내가 추가한 `WHITE_CACHE_TTL_MS=250` 패치는 흰탭 1회 검출을 250ms 동안 연장한다. 그러나:

- 로그상 cache는 28번 모두 `invalidated by red_tab`으로 즉시 폐기됐다.
- ARM 직전 127ms window 동안 red가 없었고, 그 시간에 흰탭이 자연 검출 3프레임(poll_sec=0.09 기준 약 270ms 필요했어야 함) 채워졌다.
- 즉 cache 없이도 같은 ARM이 떴을 가능성이 높다.

**white_cache는 거짓 ARM의 직접 원인이 아니다.** 굳이 끌 필요는 없다.

---

## 3. 추론 — 왜 같은 맵에서 흰탭이 3번 연속으로 잡히는가

### 3.1 흰탭의 게임 내 의미

- 빨탭(red_tab): 격수가 현재 화면에 있고 이번 맵에서 사냥 중
- 흰탭(white_tab): 격수가 다른 맵으로 이동했음을 알리는 UI

이 모델대로면 사냥 중에는 흰탭이 보일 일이 없어야 한다.
그런데 로그상 같은 맵에서 흰탭이 잡힌다. 가능한 원인:

1. **YOLO 오탐**: red 박스가 일순간 white로 잘못 분류
2. **순간 UI 깜빡**: 격수가 맵 경계 근처에서 잠깐 사라졌다 돌아옴
3. **YOLO stale 결과 유입**: 직전 맵 이동 시점의 흰탭 결과가 늦게 도착 (현재는 stale gate로 차단되지만 완벽하진 않음)

### 3.2 ARM 직전 상태 분석

마지막 red 검출(51,693) 후 127ms 동안 red가 없었다.
poll_sec=0.09 기준 약 1.4틱. 그 사이에 흰탭이 3프레임 연속 잡혔으려면:
- white_tab이 실제로 그 짧은 window 동안 3번 검출됐거나
- white_tab 직전 streak이 이미 쌓여있었고 마지막 1~2개만 더해진 것

즉 사냥 중에도 흰탭 streak이 일정 수준까지 누적되어 있다가, red가 잠깐만 비어도 ARM 임계치를 넘는 구조다.

### 3.3 v1이 이 문제를 어떻게 견뎌왔나

가능성:
- v1 시절엔 OCR 노이즈가 적어 흰탭 오탐이 드물었다
- v2 들어오며 YOLO 모델 변경 또는 입력 크기 변경으로 흰탭 오탐 증가
- 또는 v1 시절에도 사냥이 자주 끊겼지만 사용자가 다른 우선순위로 참아왔다

어느 쪽이든 **현재 v2 환경에선 ARM gate가 너무 느슨하다**.

---

## 4. 개선안

### 4.1 후보 1 — `map_neq=True` 게이트 추가

ARM 조건에:
```python
and map_neq
```
추가.

#### 장점
- 단순 명확
- 같은 맵에서는 절대 ARM 안 됨

#### 단점
- 격수의 맵 OCR이 늦게 갱신되면 진짜 맵 이동에도 못 따라간다
- 격수 PC의 OCR latency가 들쭉날쭉 하는 환경에서는 위험
- 메모리(`feedback_easyocr_device_volatility.md`) 기준 OCR latency가 9~834ms로 요동

#### 결론
- 단독 사용은 위험. 보조 조건으로는 가능.

---

### 4.2 후보 2 — `red_quiet` 게이트 추가 (Claude 제안 본안)

ARM 조건에 "최근 N ms 동안 red_tab을 한 번도 보지 않은 경우"만 통과시킴.

```python
RED_QUIET_MS = 250  # 250ms 동안 red 미검출이어야 ARM 허용

state._red_seen_ts: float = 0.0  # init

if red_raw:
    state._red_seen_ts = now

red_quiet_ok = (now - state._red_seen_ts) >= (RED_QUIET_MS / 1000.0)

if (state._whitetab_confirm >= WHITETAB_CONFIRM_STREAK
        and not red_raw
        and red_quiet_ok            # ← 추가
        and not tab_active
        and a_coord_valid
        and arm_ok_fsm
        and not follow_only
        and now >= esc_suppress_until):
    follower.arm_tab_confirm(now, map_neq=map_neq)
```

#### 동작 시나리오

**사냥 중 (거짓 ARM 케이스):**
- red_tab이 거의 항상 검출 → `_red_seen_ts`가 계속 갱신 → `red_quiet_ok=False`
- ARM 차단 ✅

**진짜 맵 이동:**
- 격수가 다른 맵으로 이동 → 격수 자체가 화면에서 사라짐 → red_tab도 자연 소멸
- 250ms 경과 후 `red_quiet_ok=True`
- 흰탭 3프레임 + red_quiet → ARM 통과 ✅

#### 임계값 검토
- 현재 로그: red 마지막 검출 후 127ms만에 ARM
- 250ms로 막으면 이 케이스 차단됨
- 진짜 맵 이동 시 격수 사라지고 1초 내에 ARM 통과는 충분

#### 진단 로그
- ARM 차단 시 `[WHITETAB-BLOCK red_quiet ms=NN]` 로그
- 이걸 통해 사냥 중 거짓 ARM 시도가 얼마나 일어나는지 측정 가능

#### 단점
- 매 틱 red_raw 갱신 로직 한 줄 추가 — 무거운 변경 아님
- "250ms"가 환경에 따라 부족할 수 있음 (사용자 화면/렌더 상태)

---

### 4.3 후보 3 — `WHITETAB_CONFIRM_STREAK` 증가

3 → 5 또는 6으로 올림.

#### 장점
- 한 줄 변경

#### 단점
- 진짜 맵 이동 시에도 ARM이 늦어짐
- 흰탭이 짧게 보이는 케이스(어떤 게임 상황)에선 영구 미발동 가능
- 임계값 튜닝이 환경 의존적

---

### 4.4 후보 4 — 후보 2 + 후보 1 조합

ARM 조건 = `red_quiet_ok AND (map_neq OR force_attempt)`

같은 맵이면서 red_quiet도 만족하는 희박한 케이스도 차단.
하지만 게이트 두 개라 진짜 맵 이동 케이스에서 격수 OCR 지연 시 못 따라갈 위험.

복잡도 증가에 비해 실익이 명확하지 않음.

---

### 4.5 권장안

**후보 2 (red_quiet 게이트) 단독 적용**

이유:
- 거짓 ARM 직접 차단
- 진짜 맵 이동에는 영향 없음 (격수 사라지면 자연스럽게 통과)
- 격수 OCR latency에 의존하지 않음
- 차단 로그로 효과 측정 가능

추가 권장: `RED_QUIET_MS`를 `[PATCH]` 헤더에 같이 찍어서 로그 헤더만 봐도 임계값 확인 가능하게.

---

## 5. 패치 계획 (적용 시)

### 5.1 변경 파일

| 파일 | 변경 |
|---|---|
| `src_v2/eyes/tab_confirm_driver.py` | `RED_QUIET_MS` 상수, `state._red_seen_ts` 필드, ARM 조건에 `red_quiet_ok` 추가, 차단 시 `[WHITETAB-BLOCK red_quiet]` 로그 |
| `src_v2/workers/healer_worker_v2.py` | `[PATCH]` 헤더에 `red_quiet_ms` 추가 |

### 5.2 검증 방법

1. 패치 적용 후 동일 사냥 환경에서 5분 이상 세션
2. 로그에서 `[WHITETAB-BLOCK red_quiet]` 횟수 확인 — 거짓 ARM 시도 카운트
3. `[WHITETAB-ARM]` 횟수 확인 — 사냥 중 거짓 ARM 0건이 되는지
4. 진짜 맵 이동 시 ARM이 정상 발동하는지 — 한 번이라도 맵 이동 발생 시 동작 확인

### 5.3 회귀 위험

- 진짜 맵 이동 시 격수가 다른 맵으로 옮긴 후 250ms 이상 red_tab이 화면에서 안 보여야 ARM 통과
- 격수가 맵 경계에서 머뭇거리면(잠깐 돌아오기 등) red_quiet이 깨질 수 있음 → ARM 지연
- 단, 이 경우 사용자가 격수를 명확히 다른 맵으로 보낸 후엔 자연스럽게 통과되므로 실 사용 영향 작음

---

## 6. Codex에게 묻고 싶은 것

1. **`red_quiet_ms=250` 임계값이 적절한가?** v1 시절 사냥 중 red 검출 빈도 통계가 있다면 참고하고 싶다.
2. **후보 1(map_neq) + 후보 2 조합을 굳이 안 쓰는 게 맞나?** 격수 OCR 지연 케이스를 본 적 있는지.
3. **white_cache는 그대로 둬도 되는가?** 본 분석에서는 거짓 ARM의 직접 원인이 아니지만, 짧은 흰탭 깜빡 보존 효과가 진짜 맵 이동 시 도움 되는지 v1 경험 의견 필요.
4. **추가로 잠재적 회귀 케이스가 있는가?** 격수가 죽어서 사라진 경우, 채팅창 가린 경우, 부활 직전 등에서 red_tab이 일시적으로 사라지는 케이스가 있다면 ARM 오발동 가능성 있음.

---

## 7. 한 줄 요약

**거짓 ARM은 white_cache 탓이 아니라 ARM gate가 사냥 컨텍스트(red_tab 활성)를 무시한 탓이다. ARM 조건에 `red_quiet >= 250ms` 게이트를 추가하면, 흰탭 처리 자체는 살린 채로 사냥 중 거짓 ARM만 차단할 수 있다.**

---

## [내 코멘트 — 문서 검토 결과]

전체적으로 이 문서는 **방향이 맞다.**
특히 지금 로그와 코드 상태를 놓고 보면, 문제를 `white_cache` 자체로 오진하지 않고 **ARM gate의 컨텍스트 부재**로 본 점이 핵심적으로 타당하다.

### 1. 내가 동의하는 부분

#### 1-1. `map_neq`가 실제 gate에 안 쓰인다는 지적

이건 코드상 사실이다.

`tab_confirm_driver.py`의 ARM 조건에는:
- `not red_raw`
- `a_coord_valid`
- `arm_ok_fsm`
- `not follow_only`

같은 기본 조건만 있고,
정작 `map_neq`는 **로깅과 `arm_tab_confirm(..., map_neq=...)` 인자로만 전달**된다.

즉 같은 맵이어도:
- red가 잠깐 비고
- white streak가 3 쌓이면
- ARM이 그대로 떨어질 수 있다.

이 문서의 문제 정의는 맞다.

#### 1-2. `white_cache`가 직접 원인이 아니라는 판단

이 부분도 현재 로그와 부합한다.

`healer_v2_20260502_163429.log`에서:
- `YOLO-WHITE-CACHE=29`
- `invalidated by red_tab=28`

즉 대부분의 cache는 red가 뜨자마자 바로 무효화되고 있다.
따라서 “cache가 길게 살아남아서 오발동했다”기보다,

> **원래 gate 자체가 red가 잠깐 사라진 짧은 공백과 white streak 누적을 너무 쉽게 ARM으로 연결한다**

고 보는 쪽이 더 맞다.

#### 1-3. `red_quiet`를 본안으로 미는 판단

이것도 현실적이다.

`map_neq` 단독 게이트는 공격자 OCR/전송 지연에 더 민감하다.
반면 `red_quiet`는:
- same-map 사냥 중엔 red가 자주 보인다는 운영 현실
- 진짜 맵 이동 시엔 red가 자연스럽게 사라진다는 메커니즘

둘을 직접 활용한다.

즉 **OCR 동기화보다 화면 현상 자체에 기대는 게이트**라서 더 robust할 가능성이 높다.

---

### 2. 내가 보완하고 싶은 부분

#### 2-1. `red_quiet_ms=250`은 타당한 시작값이지만, 고정 정답처럼 쓰면 안 된다

문서가 250ms를 잘 제안하긴 했는데,
이 값은 **정책값이 아니라 실험 시작점**으로 적는 게 더 안전하다.

이유:
- 현재 poll은 0.09s
- red 검출 빈도와 UI 깜빡임은 PC/해상도/사냥 패턴에 따라 달라질 수 있음

그래서 표현은 이렇게 가는 게 좋다.

> `red_quiet_ms 250`으로 시작하되, 200/250/300ms 중 false-arm 0건 + 진짜 이동 성공률 기준으로 확정

즉 내 의견은:
- **250ms 시작**은 찬성
- **250ms 확정**은 보류

#### 2-2. `red_quiet` 단독 적용은 좋지만, 차단 로그 외에 “왜 통과됐는지” 로그도 있어야 한다

문서엔 `[WHITETAB-BLOCK red_quiet]` 로그 제안이 있다. 이건 좋다.

그런데 운영에선 차단보다 **통과 케이스**를 같이 봐야 한다.

예를 들면:
```text
[WHITETAB-ARM] confirm=3 red_quiet_ms=287 map_neq=False ...
```

처럼,
- ARM 당시 red quiet 시간이 몇 ms였는지
- map_neq가 true/false였는지

를 같이 박아두면,
나중에 “왜 이건 통과했나?”를 더 빨리 볼 수 있다.

즉 **BLOCK 로그만 아니라 ARM 로그에도 red_quiet 메타를 같이 남기는 걸 추천**한다.

#### 2-3. 잠재 회귀 케이스를 문서에 조금 더 구체화할 필요가 있다

문서 마지막 질문 4는 좋다.
실제로 아래 케이스는 꼭 봐야 한다.

1. **격수 사망 직전/직후**
   - red가 잠깐 사라질 수 있음
2. **self_heal / revive 시퀀스 중 화면 가림**
   - red quiet가 길어질 수 있음
3. **맵 경계/문턱 근처 흔들림**
   - red와 white가 짧게 교대할 수 있음
4. **채팅/UI 팝업으로 tab 영역이 가려지는 순간**
   - white streak 누적 가능

즉 `red_quiet`는 매우 유력하지만,
**combat/revive/ui occlusion 케이스에서 false-arm 또는 false-block이 없는지**를 검증 계획에 더 명시하면 좋다.

---

### 3. 내 최종 판단

이 문서의 큰 줄기는 맞다.

정리하면:
- `white_cache`가 범인이 아니라는 판단 ✅
- `map_neq`가 게이트가 아니라는 지적 ✅
- `red_quiet`를 핵심 게이트로 제안한 것 ✅

그리고 내가 추가하는 보완은:
1. `red_quiet_ms=250`은 **실험 시작값**으로 표현
2. ARM 로그에도 `red_quiet_ms` 메타 포함
3. 사망/부활/UI 가림/맵경계 케이스를 회귀 검증 항목으로 명시

### 한 줄 코멘트

**이 문서는 타당하다. 내 판단으로도 거짓 ARM의 주원인은 white_cache가 아니라 ARM gate의 컨텍스트 부재이고, 1차 해결책은 `red_quiet` 게이트가 맞다. 다만 `250ms`는 확정값보다 실험 시작값으로 두고, ARM/BLOCK 양쪽 로그에 `red_quiet_ms`를 남겨 검증 가능성을 더 높이는 게 좋다.**

---

## [Claude 2차 — Codex 의견 검토 후]

Codex 보완 3가지 모두 동의한다. 추가로 못 박을 점들 정리한다.

### 1. 동의

- **임계값 250ms는 시작점, 확정은 데이터 기반** ✅
- **ARM 로그에도 red_quiet_ms 메타** ✅
- **사망/부활/UI 가림/맵경계 회귀 케이스 명시** ✅

이 셋은 그대로 패치 시 반영한다.

---

### 2. 보완할 부분

#### 2-1. 임계값 결정의 데이터 기반 절차 명시

Codex가 "200/250/300 중 false-arm 0건 + 진짜 이동 성공률 기준"이라고 했는데, **데이터를 어떻게 모을지**가 빠져 있다. 명시한다.

**1라운드 (250ms 시작값)**
- BLOCK 로그에 `red_quiet_ms`를 같이 박는다 → block 당시 red quiet가 몇 ms였는지 분포 수집
- ARM 로그에도 `red_quiet_ms`를 같이 박는다
- 5분 사냥 + 의도적 맵 이동 1~2회 시도

**2라운드 (분포 기반 조정)**
- BLOCK 로그 분포에서 P95가 NN ms이면, 임계값을 P95 + 안전마진 50ms로 조정
- ARM 로그에서 진짜 맵 이동 케이스의 red_quiet_ms 평균을 보고 임계값 상한 결정

**확정 기준**
- 사냥 중 BLOCK 0~소수 (false-arm 0건)
- 진짜 맵 이동 시 ARM 1회 정상 발동
- 두 조건 동시 만족이면 그 값 확정

이 절차를 본문 §5.2 검증 방법에 반영하자.

---

#### 2-2. 사망/부활 케이스는 단순 회귀가 아니라 별도 게이트가 필요할 수 있다

Codex가 "self_heal/revive 중 화면 가림"을 위험 케이스로 지적했다. 코드 보면:

```python
arm_ok_fsm = fsm_state not in ("DEAD", "DISCONNECTED")
```

이 게이트는 **DEAD/DISCONNECTED만** 막는다.
`REVIVING`이나 self_heal 시퀀스 중간 상태는 허용된다.
그 시간 동안:
- self_heal로 화면 일부 가려져 red 검출 안 됨
- red_quiet 시간 누적
- 그 사이 white tab이 깜빡 → ARM 통과

즉 `red_quiet` 단독으론 이 케이스 못 막는다.

**추가 보완 제안**

ARM gate에 `not is_self_heal_in_progress`도 추가하는 걸 검토해야 한다. 단 이건 별도 상태 노출이 필요해서 본 패치 범위 밖이다. 일단 **회귀 검증 시 self_heal 발생 시점의 ARM 발동 여부를 별도 추적 항목으로 본다**.

---

#### 2-3. 로그 형식 확정 제안

Codex가 ARM 로그 메타 추가 제안했는데, 양쪽 형식을 명시적으로 통일하자.

```text
[WHITETAB-ARM]   confirm=3 red_quiet_ms=287 map_neq=False fsm=FOLLOW ...
[WHITETAB-BLOCK] reason=red_quiet ms=120 confirm=3 fsm=FOLLOW
```

둘 다 `confirm`, `red_quiet_ms`(or `ms`), `fsm`을 공통으로 박아둔다.
나중에 grep + 정규식으로 분포 뽑기 쉽다.

BLOCK 로그는 rate-limit 필요 — 매 틱마다 찍히면 로그 폭주. ARM 로그처럼 0.5초 간격으로 throttle.

---

### 3. 패치 계획 갱신

§5.1 변경 파일을 다음과 같이 수정한다:

| 파일 | 변경 |
|---|---|
| `src_v2/eyes/tab_confirm_driver.py` | `RED_QUIET_MS=250` 상수, `state._red_seen_ts` 필드, `state._last_block_log_ts` (rate-limit), ARM 조건에 `red_quiet_ok` 추가, ARM 로그에 `red_quiet_ms` 메타, BLOCK 로그(throttle 0.5s) |
| `src_v2/workers/healer_worker_v2.py` | `[PATCH]` 헤더에 `red_quiet_ms=250` 추가 |

§5.2 검증 방법에 다음 추가:

```
5. self_heal 발생 시점의 ARM 발동 여부 별도 추적
6. BLOCK 로그의 red_quiet_ms 분포 P50/P95 확인 → 2라운드 임계값 결정 근거
7. 의도적 맵 이동 케이스에서 ARM 시점의 red_quiet_ms 측정 → 임계값 상한 결정 근거
```

---

### 4. 한 줄 결론 (2차)

**Codex 보완 3가지 모두 채택. 추가로 (a) 임계값 결정 절차를 1/2라운드로 명시화, (b) self_heal/revive 중간 상태는 본 패치 외 별도 검증 항목으로 분리, (c) ARM/BLOCK 로그 형식을 통일·throttle 적용한다. 이 상태로 가면 패치 적용 후 데이터 기반으로 임계값을 닫을 수 있다.**

---

## [내 2차 코멘트 — Claude 2차 보완안 재검토]

이번 2차 보완은 **전반적으로 좋고, 문서를 “패치 아이디어”에서 “운영 검증 가능한 실행안”으로 끌어올렸다.**

### 1. 내가 동의하는 부분

#### 1-1. 임계값 결정을 1라운드/2라운드로 나눈 점

이건 좋다.

내가 이전에 말한 “250ms는 시작값이지 확정값은 아니다”를,
이번엔 실제 운영 수집 절차로 구체화했다.

특히:
- BLOCK 로그 분포
- ARM 로그의 실제 맵 이동 케이스

를 따로 모아서 임계값을 닫겠다는 건 맞는 접근이다.

즉 이제 `red_quiet_ms`는 감이 아니라 **분포 기반 튜닝 대상**이 됐다.

#### 1-2. ARM/BLOCK 로그 형식 통일 제안

이것도 매우 좋다.

```text
[WHITETAB-ARM]   confirm=3 red_quiet_ms=287 map_neq=False fsm=FOLLOW
[WHITETAB-BLOCK] reason=red_quiet ms=120 confirm=3 fsm=FOLLOW
```

이렇게 통일되면:
- grep이 쉬워지고
- 분포 통계 추출이 쉬워지고
- false-arm / true-arm 비교가 쉬워진다.

이건 실제 운영 로그를 다뤄본 입장에서 매우 중요하다.

#### 1-3. BLOCK 로그 throttle 제안

이것도 맞다.

`red_quiet_ok=False` 상태는 연속 tick에서 계속 유지될 수 있어서,
매 tick BLOCK를 찍으면 로그 폭주가 난다.

0.5초 스로틀 정도면 충분히 합리적이다.

---

### 2. 내가 보완하고 싶은 부분

#### 2-1. self_heal/revive 중간 상태는 “별도 검증 항목”으로만 두지 말고, 패치 이후 실패 시 바로 2차 게이트 후보로 승격해야 한다

Claude가 이걸 본 패치 범위 밖이라고 정리한 건 이해된다.
하지만 운영적으로는 이렇게 못 박는 게 좋다.

> **P0 red_quiet 패치 후에도 self_heal/revive 시점 ARM이 재현되면, 다음 라운드 후보 게이트 1순위는 `seq_in_progress / self_heal_in_progress` 차단이다.**

즉 지금은 검증 항목으로 두되,
재현 시 바로 후속 패치 후보가 명확해야 한다.

문서상 이 escalation path를 적어두면 더 좋다.

#### 2-2. `map_neq`를 완전히 버릴 필요는 없고, ARM 로그 메타로는 계속 남겨야 한다

이건 현재도 로그엔 남기고 있어서 방향은 맞다.
다만 운영 판단상 중요해서 더 못 박고 싶다.

`red_quiet` 단독 게이트가 본안이어도,
`map_neq`는 여전히:
- 진짜 이동이었다는 보조 증거
- false-arm이 same-map인지 cross-map인지 구분하는 축

역할을 한다.

즉 내 의견은:
- **gate는 red_quiet 본안**
- **메타는 red_quiet + map_neq 둘 다 유지**

이렇게 분리하는 게 좋다.

#### 2-3. 검증 방법에 “false-arm 0건”뿐 아니라 “follow latency 악화 없음”도 넣는 게 좋다

red_quiet 게이트는 false-arm을 줄이는 대신,
진짜 이동 follow가 늦어질 수 있다.

그래서 검증 항목은:
1. 사냥 중 false-arm 0건
2. 진짜 이동 시 ARM 정상 발동
3. **follow 복귀 latency 체감 악화 없음**

까지 같이 봐야 한다.

숫자화하면 더 좋다.

예:
- 맵 이동 후 `WHITETAB-ARM`까지 2초 이내
- `TAB-CONFIRM-DONE`까지 4초 이내

이런 식의 운영 기준이 있으면 좋다.

---

### 3. 내 최종 판단

이번 2차 보완으로 문서는 훨씬 좋아졌다.

내 최종 입장은:
- **`red_quiet` 단독 게이트 본안** 유지
- **250ms는 시작값** 유지
- **ARM/BLOCK 로그 통일 + throttle** 채택
- **self_heal/revive 케이스는 재현 시 차기 게이트 후보로 승격** 경로까지 문서화 권장
- **검증 항목에 false-arm 0뿐 아니라 follow latency 기준도 추가 권장**

### 한 줄 코멘트

**Claude 2차 보완안은 타당하고 실무적으로 더 좋아졌다. 여기에 self_heal/revive 재현 시 다음 후보 게이트로 바로 승격하는 경로와, false-arm 0건뿐 아니라 follow latency 악화 없음까지 검증 기준으로 명시하면 거의 실행 문서 수준으로 닫힌다.**

---

## [Claude 3차 — Codex 2차 보완 채택 + 합의 마감]

Codex 보완 3가지 모두 채택한다. 이걸로 합의는 닫혔다고 본다.

### 채택 사항 정리

#### 1. self_heal/revive escalation path 문서화

§5.4 신설:

> **escalation path**: P0 red_quiet 패치 후에도 self_heal/revive 시점 ARM이 재현되면, 차기 후보 게이트는 `not seq_in_progress` (또는 `not self_heal_in_progress`) 차단이다. 이건 본 패치 범위 밖이지만, 재현 시 즉시 후속 패치 1순위로 승격한다.

#### 2. map_neq를 메타로 유지

- gate에는 미사용 (격수 OCR 지연 회피)
- ARM/BLOCK 로그 메타에는 계속 출력
- 운영 분석 시 same-map false-arm vs cross-map true-arm 구분 가능

#### 3. follow latency 기준 정량값 추가

§5.2 검증 방법에 다음 추가:

```
8. follow latency 기준:
   - 맵 이동 직후 → WHITETAB-ARM 까지 ≤ 2초
   - WHITETAB-ARM → TAB-CONFIRM-DONE 까지 ≤ 4초
   둘 중 하나라도 초과하면 임계값 250→200ms 하향 검토
```

---

### 최종 합의 요약

| 항목 | 합의 |
|---|---|
| 거짓 ARM 본질 | white_cache 무관, ARM gate의 컨텍스트 부재가 원인 |
| 본안 게이트 | `red_quiet >= 250ms` (단독) |
| 보조 메타 | `map_neq` (gate 미사용, 로그만) |
| 임계값 | 250ms 시작 → BLOCK 분포 P95+50ms로 2라운드 조정 |
| 로그 형식 | ARM/BLOCK 통일, BLOCK 0.5s throttle |
| 변경 파일 | `tab_confirm_driver.py`, `healer_worker_v2.py` |
| 검증 기준 | false-arm 0건 + 진짜 이동 ARM 정상 + follow latency 기준 |
| escalation | self_heal/revive 재현 시 `seq_in_progress` 차단 차기 후보 |

---

### 한 줄 (3차 마감)

**합의 닫힘. 본 패치 구현으로 넘어가도 될 시점이다.**
