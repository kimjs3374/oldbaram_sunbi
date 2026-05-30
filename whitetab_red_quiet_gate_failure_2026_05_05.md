# WHITETAB red_quiet 게이트 실패 분석 (2026-05-05)

> 목적: 2026-05-02에 합의·구현된 `red_quiet >= 250ms` ARM 게이트가 실제 운영 세션에서 false-arm을 한 건도 차단하지 못한 결과를 정리하고, 가설 실패의 원인과 다음 게이트 후보를 토론한다.

---

## 0. 결론 먼저

**`red_quiet 250ms` 본안 가설은 실패했다.**

> ARM 3건 모두 게이트를 통과했고 BLOCK 로그는 0건이다. ARM 시점의 `red_quiet_ms`는 **658, 1032, 1214ms** — 250ms 임계값을 2.6~4.8배 초과했다. **사냥 중에도 red_tab이 1초 이상 안 보이는 구간이 빈번**하다는 뜻이고, 이건 직전 분석에서 세웠던 "사냥 중 red는 거의 항상 보인다"는 전제 자체가 틀렸음을 의미한다.

게이트 임계값을 1500~2000ms로 올려도 658ms 케이스는 못 막는다. 즉 단순 임계값 조정으로는 해결 불가. **다른 차원의 게이트가 필요하다.**

---

## 1. 실측 데이터

### 1.1 세션 메타

- 파일: `dist_dosa/logs/healer_v2_20260505_102632.log`
- 길이: 1분 41초 (10:26:45 ~ 10:28:26)
- PATCH 헤더 (적용 확인):
  ```
  [PATCH] 2026-05-02 yolo_stale_gate+white_cache+red_quiet
  | stale_ms=150 fresh_ms=30 poll_sec=0.09
    white_cache_ttl_ms=250 red_quiet_ms=250
  ```

### 1.2 핵심 카운트

| 항목 | 값 | 직전 세션(05-02 16:34) |
|---|---|---|
| WHITETAB-ARM | **3** | 1 |
| **WHITETAB-BLOCK** | **0** | 해당 패치 없음 |
| TAB-CONFIRM-DONE | 2 | 1 |
| TAB-CONFIRM-RETRY | 0 | 0 |
| YOLO-SPIKE | 288 | 269 |
| YOLO-STALE | 301 | 357 |
| YOLO-WHITE-CACHE | 14 | 29 |

### 1.3 ARM 3건 시점/메타

```
10:26:50,426  [WHITETAB-ARM] confirm=3 red_quiet_ms=1214 map_neq=False
              h_map='제3흉노족1'   a_map='제3흉노족1'   ← 시작 5초 후

10:28:06,002  [WHITETAB-ARM] confirm=3 red_quiet_ms=658  map_neq=False
              h_map='제3흉노족2-2' a_map='제3흉노족2-2'
10:28:07,369  [TAB-CONFIRM-DONE] elapsed=1367ms route=A retry=0

10:28:23,759  [WHITETAB-ARM] confirm=3 red_quiet_ms=1032 map_neq=False
              h_map='제3흉노족2-2' a_map='제3흉노족2-2'
10:28:24,686  [TAB-CONFIRM-DONE] elapsed=927ms route=A retry=0
```

**관찰:**
- 3건 전부 `map_neq=False` (같은 맵 → false-arm)
- 3건 전부 `red_quiet_ms` ≥ 658 (게이트 250ms 우습게 통과)
- 시작 직후 1번, 1분 21초 후 2회 연속 (16초 간격)

### 1.4 YOLO 분포

- Count=288, Avg=116.9ms, Max=518ms
- P50=69.6ms (정상)
- P95=282.2ms
- P99=421.4ms

---

## 2. 가설 실패 원인 분석

### 2.1 잘못된 전제

원 문서(`whitetab_false_arm_analysis_2026_05_02.md`)의 핵심 전제:

> "사냥 중 = red_tab 거의 항상 검출 → `_red_seen_ts`가 계속 갱신 → `red_quiet_ok=False` → ARM 차단"

**이 전제가 깨졌다.** 실측에서 같은 맵 사냥 중인데 red가 658ms~1214ms 동안 한 번도 검출되지 않은 구간이 3번 발생했다.

### 2.2 왜 red_tab이 그렇게 자주 비는가 — 후보

**(a) YOLO 추론 자체가 빈 결과를 자주 반환**
- YOLO-STALE 301건 (stale gate 차단)
- stale 차단 = 그 시간 동안 watcher는 `[]` (빈 결과) 받음
- `red_tab_present=False`로 store 갱신
- `_red_seen_ts` 갱신 안 됨

**(b) 격수가 화면 밖으로 잠깐 나가는 구간**
- 사냥 중 격수 이동/대시/점프로 화면 밖 짧게 이탈
- 빨탭 자연 소멸 후 흰탭이 그 자리에 표시
- 1초 가까이 ARM 윈도가 열림

**(c) YOLO 모델 recall 부족**
- 빨탭 클래스 검출 누락이 종종 발생
- 학습 데이터에서 특정 배경/상황 보강 필요

**(d) stale gate가 red 손실을 유발**
- 직전 패치(`age_ms > 150 → []`)가 stale 결과를 모두 빈 배열로 반환
- 그 사이 진짜 red가 있었어도 downstream은 "red 없음"으로 해석
- 즉 내가 이전에 박은 stale gate가 본 게이트의 효과를 무력화하는 부작용

### 2.3 가장 의심되는 원인: (a) + (d) 결합

stale gate가 active(YOLO STALE 301건)였던 시간 동안 watcher는 빈 결과를 흘렸고, 그 시간만큼 `_red_seen_ts`도 갱신되지 않아 red_quiet 누적이 가속됐다. 즉 **stale gate와 red_quiet 게이트가 같은 신호(빈 결과)를 다르게 해석하면서 충돌**한다.

- stale gate: "결과가 stale이니 신뢰 못 함 → 빈 배열"
- red_quiet 게이트: "빈 배열 = red 없음 → quiet 누적"

stale 동안엔 사실 "관측 불가"로 처리해야 맞다. red 없음과 stale은 다른 의미.

---

## 3. 임계값 조정만으로 해결 가능한가

**불가능.**

| 임계값 | 658ms 케이스 | 1032ms | 1214ms |
|---|---|---|---|
| 250 (현재) | 통과 | 통과 | 통과 |
| 500 | 통과 | 통과 | 통과 |
| 700 | 차단 | 통과 | 통과 |
| 1100 | 차단 | 차단 | 통과 |
| 1500 | 차단 | 차단 | 차단 |

1500ms로 올리면 3건 다 차단되긴 한다. 그러나:
- 진짜 맵 이동 시 ARM 지연 1.5초+ → follow latency 악화
- 본 분석 문서 §5.2 검증 기준 "맵 이동 → ARM ≤ 2초" 위반 위험
- 더 짧은 red_quiet 케이스가 추가로 등장하면 영구 추격전

---

## 4. 다음 게이트 후보

### 4.1 후보 A — `map_neq=True` 게이트 (단독)

ARM 조건:
```python
and map_neq
```

**장점**
- 3건 모두 `map_neq=False`라서 100% 차단
- 코드 변경 1줄

**단점**
- 격수 맵 OCR 지연 시 진짜 맵 이동 못 따라감
- 메모리 `feedback_easyocr_device_volatility.md`에 OCR 9~834ms 요동 명시

**평가**
- 거짓 ARM 직접 차단 효과는 가장 강력
- 격수 OCR 지연 영향이 어느 정도인지는 실측 필요

---

### 4.2 후보 B — stale gate 영향 분리 (red 신호 보정)

`_red_seen_ts` 갱신 로직을 다음과 같이 변경:

```python
# 현재
if red_raw:
    state._red_seen_ts = now

# 변경
if red_raw:
    state._red_seen_ts = now
elif yolo_was_stale_recently:
    # stale 동안엔 red_quiet 누적 금지 (관측 불가 ≠ red 없음)
    state._red_seen_ts = now  # quiet 카운터 리셋
```

`yolo_was_stale_recently`는 snapshot에서 가져오거나 별도 시그널 필요.

**장점**
- stale gate와 red_quiet의 충돌 해소
- 본 가설 자체는 살림

**단점**
- 추가 시그널 wiring 필요
- stale 빈도가 높으면 red_quiet이 영구 미충족 → 진짜 맵 이동도 못 따라감
- 결국 임계값 조정과 같은 한계

---

### 4.3 후보 C — `map_neq=True` + `red_quiet >= 1000` 조합

ARM 조건:
```python
and map_neq
and red_quiet_ok  # 1000ms로 상향
```

**장점**
- 두 게이트 모두 통과해야 ARM
- 격수 OCR 지연으로 일순간 map_neq=True가 잘못 떠도 red_quiet으로 보호

**단점**
- 두 게이트 모두 통과 = 매우 보수적
- 진짜 맵 이동 시 ARM 지연 가능

---

### 4.4 후보 D — white_tab 자체 신뢰도 강화

ARM 조건은 그대로, 대신 흰탭 검출 자체를 더 엄격하게:

- `WHITE_CACHE_TTL_MS` 250 → 100 (cache 짧게)
- `conf_threshold` 0.45 → 0.6 (높은 confidence만)
- `WHITETAB_CONFIRM_STREAK` 3 → 5

**장점**
- 게이트 로직 변경 없이 입력 신뢰도만 올림

**단점**
- YOLO 모델 자체가 흰탭/빨탭 혼동하는 게 원인이면 무효
- streak 5 = poll 0.09 기준 450ms+ 필요 → 진짜 짧은 흰탭 놓칠 위험

---

### 4.5 후보 E — 흰탭 패턴 상하향 컨텍스트

흰탭이 떴을 때 직전 N프레임 동안 빨탭이 같은 위치에서 검출됐는지 확인:

- 빨탭이 있던 위치에 흰탭이 들어오면 = 격수가 떠난 자리 → 진짜 ARM 후보
- 빨탭과 무관한 곳에 흰탭이 뜨면 = 오탐 가능성 → ARM 보류

**장점**
- 흰탭의 "맥락"을 활용
- 위치 일치 여부로 false 강력 차단

**단점**
- 구현 복잡도 높음
- 빨탭 위치가 흔들리면 오작동

---

## 5. Claude 권장안

### 5.1 1차 — 후보 A (`map_neq=True` 단독)

이유:
- 본 세션 데이터로 100% 효과 검증됨
- 구현 1줄
- 격수 OCR 지연 우려는 측정으로 정량화 가능

후속 조치:
- BLOCK 로그에 `map_neq=False` 사유도 같이 기록
- 진짜 맵 이동 시 ARM 지연 측정
- 만약 격수 OCR 지연으로 진짜 ARM이 늦어진다면 후보 C로 확장

### 5.2 2차 (후보 A 실패 시) — 후보 B + C 조합

`red_quiet`을 stale-aware하게 만들고, `map_neq=True`도 추가.

### 5.3 white_cache는 그대로 둔다

직전 합의대로 white_cache는 거짓 ARM 직접 원인이 아니다. 본 패치 후에도 유지.

---

## 6. Codex에게 묻고 싶은 것

1. **본 가설 실패의 진짜 원인이 (a) YOLO 추론 자체 빈 결과, (d) stale gate 충돌, 또는 다른 것인가?** v1 시절 빨탭 검출 빈도/신뢰성 데이터가 있다면 비교 필요.

2. **후보 A(`map_neq=True` 단독)로 가도 되는가?** 격수 PC OCR 지연이 v2 환경에서 실제로 얼마나 자주 발생하는지 v1 운영 경험으로 가늠 가능한가.

3. **후보 B(stale-aware red_quiet)가 더 본질적 해결인가?** stale gate를 내가 박은 게 본 가설의 발 자체를 자른 것일 수도 있다.

4. **WHITE_CACHE 14건 + ARM 3건 관계?** WHITE_CACHE invalidate가 직전 세션(29건)보다 적다 — 이번 세션은 red가 덜 보이고 white만 자주 보였다는 뜻일 수 있음. white_tab 검출 신뢰도 자체를 높이는 후보 D도 다시 검토 필요한가.

---

## 7. 한 줄 결론

**`red_quiet 250ms` 본안은 실패했다. 사냥 중 red가 658~1214ms 사라지는 구간이 빈번해서 게이트가 무의미했다. 임계값 상향만으로 해결 불가. `map_neq=True` 게이트 단독(후보 A)이 직접적이고 효과적이지만, stale gate가 red 신호를 무력화하는 부작용(후보 B)도 같이 봐야 한다.**
