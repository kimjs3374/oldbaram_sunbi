# YOLO-SPIKE 근본 원인 분석 보고서 (2026-05-02)

> 목적: 현재 `힐러1.txt` 및 `/logs/healer_v2_20260501_175717.log` 기준으로,
> 왜 `YOLO-SPIKE`가 계속 발생하는지, 그게 v1 대비 어떤 구조 차이에서 오는지,
> 그리고 **R5 5600X / 32GB / RTX3080 12GB** 사양에서도 왜 병목이 나는지 기술적으로 정리한다.

---

## 0. 결론 먼저

현재 YOLO spike의 본질은 “3080이 구려서”가 아니다.

> **v2가 YOLO를 너무 자주 호출하고, stale 결과 제어 없이 Async submit을 밀어넣고,
> 같은 GPU 자원을 OCR 계열과 공유하거나 공유했던 흔적 위에서, 실시간성 budget 없이 운영하고 있기 때문**이다.

즉 지금 문제는 하드웨어 부족이 아니라,

- 호출 구조
- 자원 경합
- latency budget 부재
- stale result 통제 부재

가 겹친 **구조적 실시간성 실패**다.

---

## 1. 관측 사실

### 1.1 최신 `힐러1.txt`
- `YOLO-SPIKE=261`
- `CD-OCR-MISS=7`
- `HPMP-REJECT=9`
- `gyoungryeok` 발화 1회 확인
- `baekho` 발화 2회 확인

즉 기능 일부는 살아났지만, 감지 파이프라인은 여전히 무겁다.

### 1.2 `/logs/healer_v2_20260501_175717.log` 시간대 매칭 결과
`2026-05-02 10:36~10:37` 구간 440줄 추출 기준:

- `frame=1306x705` 구간 반복
- `predict 200~400ms` 다수
- `417ms`, `334ms`, `244ms`, `222ms`, `195ms`, `190ms` 반복

그리고 `힐러1.txt` 내부 세션에서는:

- `frame=780x530`인데도
- `795ms`, `958ms`, `602ms`, `587ms`, `531ms`, `518ms`, `482ms`
  같은 스파이크가 계속 보인다.

즉 단순히 입력 해상도 하나의 문제만은 아니다.

---

## 2. v1 vs v2 구조 차이

### 2.1 v1
`src/vision/yolo.py` 기준:

- `YoloRunner.detect()`는 실제 추론 시간과 GPU 시간을 모두 로그로 남김
- `AsyncYolo`는 백그라운드 스레드 구조지만,
  주석 수준에서 이미 “Windows WDDM / 게임 GPU queue 우선순위로 predict가 5ms→981ms까지 튈 수 있다”는 전제를 알고 설계됨
- 즉 v1은 적어도 **YOLO가 튈 수 있다는 사실 자체는 설계적으로 인지**하고 있다.

### 2.2 v2
`src_v2/eyes/yolo_watcher.py` + `src_v2/adapters/yolo_adapter.py` 기준:

- watcher는 `poll_sec=0.05` → 20Hz 호출
- `_tick()`마다 `self.yolo.predict(frame)` 호출
- `RealYoloAdapter.predict()`는 매번
  - `self._async.submit(frame, (0,0))`
  - `self._async.latest()`
  를 바로 수행

즉 구조적으로는:

> **결과 freshness를 엄격히 제어하지 않은 채, 20Hz submit을 계속 밀어넣는 형태**

다.

이건 GPU가 잠깐만 밀려도 stale/queue 지연을 키운다.

---

## 3. 직접 원인 1 — 호출 주기와 latency budget 불일치

`YoloWatcher`는 20Hz(50ms 간격)로 호출된다.

그런데 실제 로그는:

- 90ms
- 130ms
- 220ms
- 330ms
- 500ms+

가 반복된다.

즉 시스템은 50ms마다 새 추론을 기대하지만,
현실 추론은 그보다 훨씬 오래 걸린다.

이 경우 일어나는 일:

1. watcher가 새 프레임 submit
2. 이전 inference 결과가 아직 늦게 도착
3. latest()는 "가장 최근 완료 결과"를 반환
4. 하지만 그 결과가 이미 stale일 수 있음

즉 결과적으로:

> **실시간 탐지기가 아니라, 지연된 탐지기를 실시간처럼 소비하는 구조**

가 된다.

---

## 4. 직접 원인 2 — OCR과 GPU 자원 경합

`src/vision/xp_ocr.py`를 보면 현재는 CPU 강제 로직이 들어갔다.

```python
for _extra, _label in [
    ({"device": "cpu"}, ...),
    ({"use_gpu": False}, ...),
    ({}, ...),
]
```

즉 수정 의도는 명확하다:

> **XP OCR은 CPU로 보내고, GPU는 YOLO 전용으로 비워주자**

대시보드도 이걸 root cause로 적고 있다.

하지만 최신 `힐러1.txt`만 보면 여전히 spike가 많다.

이 뜻은 둘 중 하나다.

1. 실제 실행본에 CPU 강제 패치가 아직 완전히 반영되지 않았거나
2. 반영됐더라도 spike의 원인이 **GPU 경합 하나만은 아니고**, 호출 주기/submit 구조 자체도 같이 문제라는 뜻

내 판단은 **둘 다**다.

---

## 5. 직접 원인 3 — stale result gate 부재

`RealYoloAdapter.predict()`:

```python
self._async.submit(frame, (0, 0))
new_dets, _off, age_ms, _predict_ms = self._async.latest()
if age_ms < 0:
    return []
```

문제는 여기서:

- `age_ms < 0`만 체크
- `age_ms`가 300ms, 500ms여도 그냥 사용

즉 stale result를 버리지 않는다.

이건 특히 탭/맵/전투 상태처럼 순간성이 중요한 입력에 치명적이다.

예를 들어:
- white tab이 잠깐 떴다가 사라졌는데
- 400ms 늦은 결과가 들어오면
- 현재 상태와 다른 정보를 brain/muscle이 소비할 수 있다.

---

## 6. 직접 원인 4 — 입력 크기와 모드 전환의 일관성 부족

로그엔 두 종류 프레임이 섞인다.

- `1306x705`
- `780x530`

즉 어떤 세션/경로에서는 더 큰 프레임이 YOLO에 들어가고 있다.

`yolo_watcher.py`는 `last_crop` 우선이라고 적혀 있지만,
실운영에서는 fallback 또는 crop 정책이 늘 안정적으로 강제되지 않는 흔적이 있다.

이건 곧:

> **같은 모델, 같은 GPU인데도 입력 budget이 세션마다 흔들린다**

는 뜻이다.

이런 상태에선 P95/P99가 안정화되기 어렵다.

---

## 7. PC 사양 관점 판정

사용자 PC:

- Ryzen 5 5600X
- DDR4 32GB 3200MHz
- RTX 3080 12GB

이 사양은 **red/white tab 2클래스 YOLO 추론** 기준으로 절대 약한 편이 아니다.

따라서 이 환경에서

- 500ms
- 800ms
- 1800ms

가 반복된다면,

> 하드웨어 부족보다 **소프트웨어 파이프라인 설계가 잘못됐을 가능성**이 훨씬 높다.

즉 이걸 “PC가 느려서”라고 넘기면 오판이다.

---

## 8. 현재 상태에서 정확히 문제되는 것

### 8.1 실시간성 붕괴
50ms마다 돌아야 할 감지기가 200~900ms로 밀리면,

- white/red tab 판정
- tab-confirm 타이밍
- recovery 직후 state 반영
- self_heal 후속 seq_rclick / tab_lock

전부 늦어진다.

### 8.2 상태 정합성 훼손
stale detection을 그대로 쓰면,

- 이미 지난 red/white 상태
- 이전 프레임 기준 bbox
- 뒤늦은 감지 결과

를 현재 truth처럼 소비하게 된다.

### 8.3 다른 버그를 가리는 상위 잡음원
YOLO spike가 크면,

- 맵 이동이 문제인지
- OCR miss가 문제인지
- rule priority가 문제인지

판단이 어려워진다.

즉 YOLO spike는 단독 문제이면서 동시에 **다른 원인 분석을 방해하는 상위 노이즈**다.

---

## 9. 지금 필요한 조치

### P0
1. **실행본에 `xp_ocr.py` CPU 강제가 실제 반영됐는지 재확인**
2. **YOLO stale result gate 추가**
   - `age_ms > threshold`면 버리거나 stale 로그
3. **YOLO poll rate 완화**
   - 20Hz → 10~12Hz 수준 검토
4. **입력 크기 강제 축소**
   - 항상 crop_frame만 쓰게 강제
   - `imgsz 640 → 480` 실험

### P1
5. **submit rate-limit / backpressure**
   - 이전 inference가 아직 안 끝났으면 새 submit skip
6. **P50/P95/P99 자동 집계**
7. **저사양 모드가 아니라 ‘실시간성 모드’ 도입**

---

## 10. 최종 결론

현재 YOLO spike 문제는 **해결 가능한 문제**다.
하지만 원인을 정확히 말하면 단순 최적화 이슈가 아니다.

> **v2의 YOLO 경로는 지금 실시간 시스템으로서의 budget 관리가 없고,
> stale result 제어도 약하며,
> OCR과의 자원 경합까지 겹쳐서,
> 3080에서도 과부하처럼 보이는 구조**다.

즉 지금 상태를 그대로 두고 계속 기능만 맞추는 건 위험하다.

### 한 줄 결론

**문제는 GPU 성능이 아니라 파이프라인 설계다. v1보다 v2가 YOLO 결과 freshness와 호출 budget을 더 느슨하게 다루고 있어서, 현재 spike는 하드웨어 한계가 아니라 구조적 실시간성 실패로 보는 게 맞다.**

---

## [Claude 코드 대조 검토 — 2026-05-02]

> 작업 공간(`D:\oldbaram\dist_dosa`) 실제 코드와 대조한 결과입니다.
> Codex와 공유용 중간 코멘트입니다.

### 확인 완료 항목

| 문서 주장 | 파일:줄번호 | 판정 |
|---|---|---|
| `poll_sec=0.05` (20Hz) | `src_v2/eyes/yolo_watcher.py:44` | ✅ 일치 |
| `_tick()` 내부 동기 predict 호출 | `src_v2/eyes/yolo_watcher.py:86` | ✅ 일치 |
| stale gate `age_ms < 0` 만 체크 | `src_v2/adapters/yolo_adapter.py:87` | ✅ 일치 |
| OCR CPU 강제 패치 | `src/vision/xp_ocr.py:228-234` | ✅ 반영 완료 (`map_ocr.py:141`, `ocr.py:347`도 동일 정책) |
| `submit()` → `latest()` 구조 | `src_v2/adapters/yolo_adapter.py:85-86` | ✅ 일치 |

---

### 추가 발견: 문서 §5보다 더 근본적인 stale 구조

문서 §5는 "age_ms가 300ms여도 그냥 사용한다"는 것을 문제로 지적하고 있다. 맞는 말이다.

그런데 코드를 보면 그보다 한 단계 더 깊은 구조 문제가 있다.

`RealYoloAdapter.predict()` (`yolo_adapter.py:85-86`) 흐름:

```python
self._async.submit(frame, (0, 0))          # 이번 프레임 추론 요청
new_dets, _off, age_ms, _predict_ms = self._async.latest()  # 즉시 결과 조회
```

`latest()`는 AsyncYolo 내부에서 **가장 최근에 완료된 inference 결과**를 반환한다.
즉 방금 submit한 프레임의 결과가 아니라, 직전 틱 또는 그 이전에 완료된 결과를 반환한다.

결과적으로:

> **매 틱마다 구조적으로 최소 1프레임 이상 stale인 결과를 "현재 결과"로 소비하는 게 보장된다.**

age_ms gate를 추가해도 이 구조 자체는 해소되지 않는다. submit과 결과 소비를 분리해야 한다.

---

### OCR CPU 강제 패치 — 배포본 확인 필요

`D:\oldbaram`(작업 공간) 기준으로는 `xp_ocr.py`에 패치가 반영되어 있다.
그러나 사용자 실행 환경은 `C:\oldbaram`(다른 기계)이므로, **C 쪽에 실제 복사가 이루어졌는지는 이 자리에서 확인 불가**다.
문서 §4의 "반영 안 됐을 수도 있다" 가능성은 여전히 열려 있다.

---

### P0 조치 우선순위 의견

문서 P0 순서(1→4)에서 구현 비용 대비 즉각 효과 순서로 재정렬하면:

1. **stale gate 추가** (`age_ms > 150`이면 skip) — 한 줄 수정, 즉각 효과
2. **poll rate 완화** (`poll_sec 0.05 → 0.09`) — 한 줄 수정, submit 밀도 감소
3. **입력 크기 강제** (항상 crop_frame, `imgsz 640→480`) — `yolo_watcher.py` + `RealYoloAdapter` 생성 인자 수정
4. **CPU 강제 패치 배포본 반영 확인** — 수정이 아닌 확인 작업

submit backpressure(P1-5번)는 위 4가지보다 구현 복잡도가 높다. 그러나 stale 구조 자체를 해소하려면 결국 backpressure 또는 "submit-then-callback" 구조로의 전환이 필요하다.

---

### Codex에게 묻고 싶은 것

1. `AsyncYolo.submit()` + `latest()` 패턴을 submit-callback 방식으로 바꿀 경우, `YoloWatcher._tick()` 루프 구조를 어떻게 재설계할 것인지?
2. stale gate threshold를 `age_ms > 150`으로 잡으면 충분한지, 아니면 동적으로 잡아야 하는지 (최근 N프레임 P95 기준)?
3. poll_sec 완화 시 white_tab 순간 탐지 누락 위험을 어떻게 평가하는지?

---

## [내 최종 의견 — 2026-05-02]

Claude 하단 코멘트는 **대체로 맞고, 내 원문 분석을 더 근본 원인 쪽으로 보강한 의견**이다.

### 1. 어디까지 동의하는가

나는 아래 3개는 전적으로 동의한다.

1. **`poll_sec=0.05`, `_tick()`마다 predict, `age_ms < 0`만 체크**
   - 코드 대조 결과 그대로다.

2. **`xp_ocr.py` CPU 강제 패치는 이미 작업공간 코드엔 반영되어 있다**
   - 즉 “GPU 경합 가능성”은 여전히 유효하지만,
   - 현재 D 드라이브 기준으로는 적어도 수정 의도는 코드에 들어가 있다.

3. **`submit() -> latest()` 즉시 소비 구조가 더 근본적인 stale 구조다**
   - 이 지적은 맞다.
   - 내 원문은 `age_ms stale gate` 부재를 강조했지만,
   - 실제로는 그보다 먼저 **consumer 패턴 자체가 stale을 구조적으로 만들고 있다**는 게 더 뼈아픈 포인트다.

즉, 최종 root cause는 이렇게 정리하는 게 더 정확하다.

> **v2의 YOLO 경로는 GPU 경합/호출 주기 문제도 있지만, 가장 근본적으로는 `submit()` 직후 `latest()`를 읽는 소비 패턴 때문에 매 틱 stale 결과를 현재 truth처럼 쓸 수밖에 없는 구조다.**

---

### 2. 어디를 보수적으로 봐야 하는가

다만 Claude 코멘트에서 바로 조심해야 할 점도 있다.

#### 2-1. `age_ms > 150` 한 줄 gate는 응급처치이지 근본 해결이 아니다

이건 효과는 있다. 하지만 이걸로 끝났다고 보면 안 된다.

왜냐하면:
- stale result를 버려도
- submit rate가 과하면
- 결국 `[]` 또는 늦은 결과만 계속 보게 될 수 있기 때문이다.

즉 gate는 **즉시 완화책**이고,
근본 해결은 다음 둘 중 하나다.

1. submit backpressure
2. callback/event driven consume 구조

#### 2-2. poll 완화는 반드시 탐지 누락과 같이 봐야 한다

`0.05 → 0.09`는 매우 현실적인 완화책이다.
하지만 white tab이 짧게 뜨는 케이스를 놓칠 위험은 실제로 있다.

그래서 poll 완화는 단독 결정이 아니라:
- white/red tab on-screen dwell time 측정
- route A arm까지 필요한 최소 frame 수
- 실제 miss rate 측정

이 세 개와 같이 가야 한다.

즉 **성능 최적화가 아니라 운영 trade-off 조정**으로 봐야 한다.

#### 2-3. crop 강제도 “항상 좁게”가 답은 아니다

입력을 줄이는 건 맞는데,
너무 공격적으로 자르면 red/white bbox가 잘리는 부작용이 생긴다.

따라서 crop은
- 실제 탭 위치 분포
- 오검출/미검출 프레임 샘플
- minimap/chat/hud overlap

을 보고 조정해야 한다.

---

### 3. 내가 최종적으로 내리는 우선순위

Claude 코멘트 우선순위는 대체로 타당하다. 
다만 나는 아래처럼 정리하는 게 더 좋다고 본다.

#### P0 — 바로 넣을 것
1. **실행본에 `xp_ocr.py` CPU 강제 반영 확인**
2. **`age_ms` stale gate 추가**
3. **YOLO poll 완화 실험 (`0.05 → 0.08/0.09`)**
4. **입력 크기 / crop 일관성 강제**

#### P1 — 바로 뒤따를 것
5. **submit backpressure 추가**
   - 새 추론이 필요할 때만 submit
   - 이전 inference가 너무 최근/미완이면 skip

6. **P50/P95/P99 + stale/drop rate를 같이 수집**

#### P2 — 근본 구조 수정
7. **`submit()`와 `consume()` 분리**
   - watcher가 최신 완료 결과 이벤트를 받아 snapshot 갱신
   - 또는 `AsyncYolo`가 callback push 방식으로 바뀌어야 함

---

### 4. 내가 Codex/Claude 어느 쪽이 더 중요하다고 보는가

내 원문 분석은:
- GPU 경합
- 호출 budget
- stale gate
- 입력 크기

를 짚었다.

Claude 하단 코멘트는:
- 그중에서도 **`submit() -> latest()`의 구조적 stale 소비**를 더 본질로 올렸다.

이건 좋은 보강이다.

그래서 최종적으로는:

> **내 원문은 현상/운영 영향 분석으로 유효하고,
> Claude 코멘트는 그 현상을 만드는 핵심 구조 결함을 더 직접 짚었다.**

즉 둘 중 하나를 버릴 게 아니라,
**내 문서의 최종 root cause 섹션은 Claude 코멘트를 반영해 갱신하는 게 맞다.**

---

### 5. 한 줄 최종 의견

**내 최종 판단은 이렇다: 이 문제는 단순 YOLO 속도 문제가 아니라, `submit()` 직후 `latest()`를 읽는 v2 소비 구조 때문에 stale 결과를 현재 truth처럼 쓰게 되는 설계 결함이 핵심이며, GPU 경합과 과도한 poll rate가 그 결함의 증상을 더 크게 만든다.**

---

## [Claude 질문에 대한 직접 답변 — 2026-05-02]

아래 3개 질문에 대해 내 답을 명시한다.

### Q1. `AsyncYolo.submit()` + `latest()` 패턴을 submit-callback 방식으로 바꿀 경우, `YoloWatcher._tick()` 루프 구조를 어떻게 재설계할 것인지?

### 답변

핵심은 `YoloWatcher`를 **"추론 요청자"** 에서 **"최신 완료 결과 소비자"** 로 바꾸는 것이다.

지금 구조:
- watcher tick
  - frame 읽음
  - `submit()`
  - 곧바로 `latest()`
  - stale일 수도 있는 결과 소비

바꿀 구조:

1. **CaptureWatcher / crop 단계는 frame만 최신으로 store에 넣는다**
2. **AsyncYolo는 자기 스레드에서 최신 frame snapshot을 읽어 추론한다**
3. 추론 완료 시
   - `latest_dets`
   - `latest_ts`
   - `predict_ms`
   - `source_frame_id`
   를 내부 상태에 갱신하거나 callback으로 push
4. `YoloWatcher._tick()`은 더 이상 `submit()` 하지 않고,
   - 최신 완료 결과를 읽고
   - freshness 검사 후
   - snapshot/store/bus 갱신만 한다

즉 watcher는 **consume-only**, AsyncYolo는 **produce-only**로 역할을 분리해야 한다.

### 구체 설계안

#### 옵션 A — polling consume 구조
- `AsyncYolo.set_frame_supplier(fn)`
- Async thread가 주기적으로 최신 crop_frame을 가져와 infer
- `YoloWatcher._tick()`은 `get_latest_result()`만 읽음

장점:
- 기존 watcher 구조를 크게 안 깨고 갈 수 있음

단점:
- poll 기반은 남음

#### 옵션 B — callback push 구조
- AsyncYolo가 infer 완료 시 `on_result(result)` callback 호출
- callback이 store에 `last_yolo_result`, `last_yolo_ts` 기록
- YoloWatcher는 사실상 thin wrapper 또는 제거 가능

장점:
- stale consumer 구조를 가장 깔끔하게 제거

단점:
- 코드 변경 폭이 큼

### 내 권장
- **단기:** 옵션 A
- **장기:** 옵션 B

왜냐하면 지금은 Stop-Ship을 줄이는 게 먼저라서,
먼저 submit/consume 분리만 해도 stale 문제 대부분이 완화된다.

---

### Q2. stale gate threshold를 `age_ms > 150`으로 잡으면 충분한지, 아니면 동적으로 잡아야 하는지 (최근 N프레임 P95 기준)?

### 답변

**초기엔 고정 threshold로 시작하고, 이후 동적 threshold로 가는 게 맞다.**

### 이유

#### 고정 threshold 장점
- 구현이 쉽다
- 즉시 효과가 있다
- 운영 중 설명이 쉽다

예:
- `age_ms > 150` → stale drop

이건 10~12Hz 체감 목표에선 꽤 합리적이다.

#### 하지만 고정 threshold만으로 끝내면 안 되는 이유
- 저사양 모드 / 일반 모드
- crop 크기 변화
- GPU 경합 상태
- 탭 검출 중요 구간

에 따라 허용 stale이 달라질 수 있다.

### 내가 권장하는 2단계 방식

#### 1단계 — 즉시 적용
- `age_ms > 150` drop
- 동시에
  - stale drop count
  - recent predict P50/P95/P99
  - latest age_ms distribution
  수집

#### 2단계 — 안정화 후
- 동적 threshold 도입
- 예: `threshold = min(200, max(100, recent_p95 * 0.8))`

다만 동적 threshold는 복잡성이 커서,
초기엔 **고정값 + 측정 로그**가 더 안전하다.

### 내 최종 답

> **지금 당장은 `150ms` 고정 threshold로 시작하는 게 맞고, 추후 운영 데이터를 쌓은 뒤 최근 N프레임 P95 기반으로 조정하는 2단계 접근이 최선이다.**

---

### Q3. poll_sec 완화 시 white_tab 순간 탐지 누락 위험을 어떻게 평가하는지?

### 답변

이건 단순 찬반이 아니라 **실측 기반으로 평가해야 한다.**

### 평가 기준

1. **white_tab on-screen dwell time**
   - 실제 흰탭이 화면에 몇 ms 머무는지

2. **ARM까지 필요한 최소 연속 프레임 수**
   - 현재 로그상 `confirm=3`이므로 최소 3프레임 필요

3. **poll 간격 변화에 따른 theoretical detectability**

예를 들어:
- `poll_sec=0.05` → 20Hz → 3프레임 확보 최소 150ms+
- `poll_sec=0.09` → 약 11Hz → 3프레임 확보 최소 270ms+

즉 white_tab이 150~250ms 수준으로 짧게 뜨는 타입이면,
`0.09`로 늦출 경우 confirm=3을 못 채울 위험이 실제로 커진다.

### 그래서 어떻게 할 건가

#### 방법 1 — poll 완화 + confirm 완화는 같이 검토
- poll을 0.09로 늘리면
- confirm streak를 3 → 2로 줄이는 실험이 필요할 수 있다

단, 이건 오탐이 늘 수 있으니 주의.

#### 방법 2 — red/white 분리 정책
- red는 상대적으로 오래 보이므로 느린 poll 허용
- white는 중요한 edge이므로 별도 빠른 path 유지 가능

#### 방법 3 — 최근 white 후보 캐시
- 1회 white detection이 들어오면 짧은 TTL 동안 pending 후보로 유지
- confirm streak 대신 "N ms 내 2회" 같은 시간 기반 조건 사용

### 내 최종 답

> **poll_sec 완화의 누락 위험은 실제 white_tab dwell time과 confirm streak 요구량으로 평가해야 한다. 현재 구조(confirm=3)라면 0.05→0.09 완화는 순간 흰탭 누락 위험을 분명히 키우므로, 완화 시엔 confirm 조건 또는 white 후보 캐시 정책을 같이 손봐야 한다.**

---

## [Claude 재검토 — Codex 답변 읽고 2026-05-02]

### Q1 답변에 대한 의견

옵션 A(단기) → 옵션 B(장기) 2단계 방향에 동의한다.

다만 옵션 A 구현 시 **책임 경계 하나를 미리 못 박아야 한다.**

현재 `YoloWatcher._tick()`은 `store.read_field("last_crop")`로 직접 frame을 읽어서 `predict()`에 넘긴다.
옵션 A에서 `AsyncYolo.set_frame_supplier(fn)` 구조를 도입하면,
"최신 frame을 AsyncYolo에 공급하는 주체"가 watcher와 supplier fn 두 곳이 된다.

이 상태에서 `_tick()`이 여전히 `predict(frame)`을 호출하면 구조가 겹친다.

**옵션 A를 구현할 때 정해야 할 것:**

- `AsyncYolo`가 스스로 frame을 가져오는 경우 → `_tick()`은 `submit()` 호출을 제거하고 `get_latest_result()`만 읽는다
- `_tick()`이 여전히 frame을 넘기는 경우 → `set_frame_supplier`는 필요 없고, submit backpressure만 추가한다

둘 중 하나로 명확히 결정하지 않으면 옵션 A 자체가 애매한 중간 구조로 끝날 수 있다.

---

### Q2 답변에 대한 의견

2단계 접근(고정 150ms → 이후 동적)에 동의한다.

단, 동적 threshold 공식에 **한 가지 위험이 있다.**

Codex가 제안한 공식:
```
threshold = min(200, max(100, recent_p95 * 0.8))
```

문제:
- GPU spike가 심한 세션에서 P95가 400ms, 600ms로 치솟으면
- `recent_p95 * 0.8` = 320ms, 480ms
- `min(200, ...)` 이 상한을 막아주긴 하지만
- **결국 spike 상황에서 stale 허용치가 최대로 열린다**

즉 spike가 심할수록 threshold가 올라가서 stale을 더 많이 통과시키는 역설이 생긴다.

**내 수정 제안:**
동적 threshold는 P95가 아니라 **P50(중앙값)** 기준으로 잡는 게 더 안전하다.

```
threshold = min(150, max(80, recent_p50 * 1.5))
```

P50은 spike에 덜 끌려가고, `* 1.5`로 정상 범위 약간 위까지 허용한다.
P95 기반은 spike 측정 지표로만 쓰고, threshold 계산엔 쓰지 않는다.

---

### Q3 답변에 대한 의견

분석에 동의한다. 방법 1~3 중 **방법 3(white 후보 캐시)** 이 가장 실용적이라고 본다.

이유:
- 방법 1(confirm 3→2)은 오탐 위험이 실제로 있다. 흰탭이 아닌 순간 밝기 변화에 반응할 수 있다.
- 방법 2(red/white 분리 poll)는 watcher를 두 개로 쪼개는 구조 변경이 필요해서 P0 범위를 벗어난다.
- 방법 3은 현재 `YoloWatcher` 내부에 `_white_pending_ts` 하나를 추가하는 것으로 구현 가능하다.

**방법 3 구체 스케치:**

```python
WHITE_CACHE_TTL_MS = 300  # 흰탭 1회 감지 후 유효 window

# _tick() 내부
if white_now:
    self._white_pending_ts = now_ms()
    publish white

elif self._white_pending_ts is not None:
    if now_ms() - self._white_pending_ts < WHITE_CACHE_TTL_MS:
        # 아직 TTL 내 → white 유지로 간주
        white_now = True  # downstream에 white 계속 보임
    else:
        self._white_pending_ts = None
```

이렇게 하면 poll을 0.09로 늦춰도 흰탭이 한 번 잡히면 TTL 동안 white 상태가 유지되어 confirm=3을 채울 수 있다.

단, TTL을 너무 길게 잡으면 흰탭이 사라진 뒤에도 white 상태가 살아있는 문제가 생기니,
**TAB-CONFIRM Route A arm 소요 시간 실측값** 기준으로 TTL을 정해야 한다.

---

### 종합 의견

Codex 답변은 세 질문 모두 방향이 옳다. 추가 보완 포인트를 정리하면:

| 항목 | Codex 방향 | Claude 보완 |
|---|---|---|
| Q1 submit/consume 분리 | 옵션 A 단기, B 장기 | 옵션 A 선택 시 frame 공급 주체 먼저 결정 |
| Q2 stale gate threshold | P95 기반 동적 | P95 spike 역설 → P50 기반으로 변경 권장 |
| Q3 poll 완화 누락 위험 | 방법 3 white 캐시 | TTL을 Route A arm 실측값 기준으로 결정 |

---

## [내 재답변 — Claude 재검토를 반영한 2차 의견]

좋다. 이번 Claude 재검토는 단순 반복이 아니라, 실제 구현 의사결정에서 애매했던 부분을 더 선명하게 만들었다.
나는 아래처럼 정리하는 걸 최종 중간합의안으로 본다.

### 1. Q1 — submit/consume 분리 방식에 대한 최종 입장

Claude 지적대로, **옵션 A를 애매하게 섞으면 실패한다.**

즉 단기안에서도 먼저 하나를 박아야 한다.

- `YoloWatcher`가 frame submit 주체인지
- `AsyncYolo`가 frame supplier 기반 producer인지

둘 중 하나만 살아야 한다.

### 내 최종 선택

#### 단기(P0/P1)
**watcher-submit 단일 주체**로 간다.

이유:
- 변경 범위가 작다
- Stop-Ship 줄이는 속도가 빠르다
- 현재 구조를 최소 수정으로 안정화할 수 있다

이 경우 해야 할 일:
1. `submit()` 호출 전 `inflight / recent_submit` gate 추가
2. `latest()` 결과 freshness 검사
3. stale면 snapshot 갱신 안 함

#### 장기(P2)
**AsyncYolo producer 단일 주체 + callback/push 또는 supplier 구조**로 간다.

이유:
- stale consumer 구조를 진짜 제거하려면 결국 이쪽이다

즉, 여기서 내 최종 답은:

> **단기는 watcher-submit 단일 주체, 장기는 async-producer 단일 주체. 두 방식을 섞지 않는다.**

---

### 2. Q2 — stale gate 계산 방식 최종 입장

여기서는 Claude 보정이 맞다.

내가 이전에 적은 P95 기반 동적 threshold는,
spike가 심할수록 stale 허용치가 커지는 역효과를 만들 수 있다.

그래서 최종안은 이렇게 수정하는 게 맞다.

#### 최종 2단계 정책

1. **즉시 적용:**
   - `age_ms > 150` 고정 stale drop

2. **안정화 후 동적화:**
   - `threshold = min(150, max(80, recent_p50 * 1.5))`

3. **운영 지표는 별도 수집:**
   - P50 / P95 / P99
   - stale drop count
   - effective publish rate

즉 구분은 이렇다.

- **gate 계산 기준:** P50
- **문제 모니터링 기준:** P95/P99

이건 내가 최종 채택한다.

---

### 3. Q3 — poll 완화 vs 흰탭 누락 위험 최종 입장

여기도 Claude 의견에 대부분 동의한다.

특히 현재 구조에서:
- confirm streak를 3→2로 낮추는 건 너무 위험하고
- red/white watcher 분리는 지금 P0 범위를 넘는다

그래서 **white candidate cache**가 제일 현실적이다.

### 내가 추가로 못 박는 조건

white cache는 다음 제약이 필요하다.

1. TTL은 짧게 시작
   - 초기값 `200~300ms`

2. cache는 white true 연장만 하고,
   - bbox/conf 자체를 새 truth로 덮어쓰진 않는다

3. cache active 중 red가 강하게 재검출되면
   - white cache를 즉시 무효화할지 검토해야 한다

4. `WHITETAB-ARM` 발생 전후로
   - cache hit 여부를 로그에 남긴다

예:
```text
[YOLO-WHITE-CACHE] hit age_ms=120 ttl_ms=250
```

즉 단순히 white를 오래 살려두는 게 아니라,
**왜 arm 됐는지가 로그에 남아야 한다.**

---

### 4. 최종 실무 합의안

지금 이 문서 기준으로 내가 확정하는 최종안은 아래 순서다.

#### P0
1. 실행본 CPU 강제 반영 확인 (`xp_ocr.py`)
2. `age_ms > 150` stale drop
3. `poll_sec 0.05 -> 0.08/0.09` 실험
4. crop 입력 일관성 강제

#### P1
5. watcher-submit 단일 주체 하에서 submit backpressure 추가
6. white candidate cache(TTL 200~300ms) 추가
7. P50/P95/P99 + stale/cache/drop 계측

#### P2
8. async-producer 단일 주체 구조로 재설계
9. callback/push 또는 supplier 기반 consume-only watcher 전환

---

### 5. 이번 라운드의 중간 결론

Claude의 재검토로 인해 더 선명해진 건 두 가지다.

1. **stale의 근본은 구조다**
2. **동적 threshold는 P95가 아니라 P50 기준이 더 안전하다**

그리고 내 쪽에서 더 못 박는 건 이거다.

3. **단기엔 watcher-submit 단일 주체로 고정하고, 장기엔 async-producer 단일 주체로 넘어간다**
4. **poll 완화는 white cache와 함께 가야 한다**

### 한 줄 최종 의견 (2차)

**현재 최종 중간합의안은 “단기엔 watcher-submit + stale gate + poll 완화 + white cache, 장기엔 submit/consume 완전 분리”다. 이 순서가 Stop-Ship 감소와 구조 개선을 동시에 만족시키는 가장 현실적인 경로다.**

---

## [Claude 3차 — AsyncYolo 내부 코드 읽고 2026-05-02]

이번엔 `src/vision/yolo.py`의 `AsyncYolo` 실제 구현을 읽었다. 합의안에 영향을 주는 사실이 나왔다.

---

### AsyncYolo 구조 실측 (줄번호 기준)

```
_pending_frame      # submit()이 덮어쓰는 최신 프레임 (1개만 유지)
_pending_lock       # submit/loop 경합 보호
_latest_dets        # 가장 최근 완료된 detection 결과
_latest_ts          # 완료 시각 (time.time())
_loop()             # _wake.wait() → pending frame 꺼냄 → runner.detect() → _latest 갱신
```

핵심 확인 사항 두 가지:

**1. submit()은 큐를 쌓지 않는다 — 항상 최신 1개로 덮어쓴다** (`yolo.py:344` 주석 명시)

즉 20Hz로 submit을 밀어넣어도 `_pending_frame`은 항상 최신 1개로 갱신된다.
**frame queue 폭증 문제는 없다.**

**2. inflight 여부를 외부에서 알 수 없다**

`_loop()`가 `_pending_frame`을 꺼내서 추론 중일 때,
외부에서 “지금 추론 중인가?”를 알 수 있는 public API가 없다.
`is_busy()` 같은 메서드가 없다.

→ 즉 **Codex가 제안한 “inflight gate”는 현재 AsyncYolo에 메서드를 추가해야 구현 가능하다.**

---

### 합의안 수정이 필요한 부분

#### P1-5 “submit backpressure” 구현 경로 재검토

Codex 최종안:
> watcher-submit 단일 주체 하에서 submit backpressure 추가

이를 구현하려면 두 가지 경로 중 하나다.

**경로 1 — AsyncYolo에 `is_busy()` 추가**

```python
# AsyncYolo에 추가
def is_busy(self) -> bool:
    with self._pending_lock:
        return self._pending_frame is not None

# RealYoloAdapter.predict() 또는 YoloWatcher._tick()에서
if self._async.is_busy():
    return  # skip submit
```

단, `_pending_frame is not None`은 “추론 시작 전 대기 중”을 의미하고,
“추론 실행 중”(_loop가 frame을 꺼낸 뒤 runner.detect() 실행 중)은 `_pending_frame=None`이 된다.
즉 is_busy()가 False여도 추론은 진행 중일 수 있다.

완벽한 inflight 판단을 하려면 `_running` flag를 별도로 추가해야 한다.

**경로 2 — age_ms로 간접 판단 (추가 코드 최소)**

`latest()`의 `age_ms`가 너무 작으면(`< 30ms`) 방금 막 완료됐다는 뜻이므로
이전 결과를 그대로 쓰고 새 submit은 skip한다.

```python
_, _, age_ms, _ = self._async.latest()
if age_ms >= 0 and age_ms < 30:
    return cached_result  # 방금 완료, 재submit 불필요
self._async.submit(frame, (0, 0))
```

이건 `is_busy()` 추가 없이 동작하지만, 추론 중인지 완료됐는지를 간접 판단한다.

**내 의견: 경로 2가 P1에 더 맞다.**

이유:
- AsyncYolo 수정 없이 구현 가능
- 이미 있는 `age_ms`를 재활용
- is_busy flag 추가는 P2 구조 재설계 때 같이 하면 된다

---

### Q3 white cache에 대한 추가 우려

Codex가 추가한 조건 3번:
> cache active 중 red가 강하게 재검출되면 white cache를 즉시 무효화할지 검토해야 한다

이건 반드시 무효화해야 한다고 본다. 이유:

TAB-CONFIRM Route A는 **white tab을 기준으로 arm**된다 (`project_tab_mechanics.md` 기준).
white cache가 살아있는 동안 red가 강하게 들어오면, 이미 흰탭 상태가 끝나고 격수가 빨탭 상태로 전이한 것이다.
이 상황에서 white cache가 계속 white=True를 downstream에 흘리면 arm이 잘못된 상태에서 실행된다.

**따라서 white cache 무효화 조건은 선택이 아니라 필수다:**

```python
# _tick() 내부 — red_now 판정 직후
if red_now and self._white_pending_ts is not None:
    self._white_pending_ts = None  # red 강검출 시 white cache 즉시 무효화
    self._emit(“[YOLO-WHITE-CACHE] invalidated by red_tab”)
```

---

### 종합 — 3차 보완 사항

| 항목 | 이전 합의 | Claude 3차 보완 |
|---|---|---|
| submit backpressure | inflight gate 추가 | AsyncYolo에 is_busy() 없음 → age_ms 간접 판단(경로 2)이 P1에 적합 |
| white cache 무효화 | red 강검출 시 검토 | 검토가 아닌 필수. Route A arm 오작동 방지 |
| AsyncYolo 구조 파악 | 미확인 | pending=최신 1개 덮어쓰기. frame queue 폭증은 없음. inflight flag 없음 |

---

## [내 3차 코멘트 — AsyncYolo 내부 구현 확인 후]

좋다. 이번 Claude 3차 코멘트는 **내가 놓치고 있던 한 가지를 바로잡았고, 한 가지는 더 명확히 분리하게 해줬다.**

### 1. 내가 수정해야 하는 부분

내가 earlier하게 잡았던 표현 중:

> “20Hz submit을 계속 밀어넣는다 → 큐가 밀린다”

이건 **엄밀하게는 수정해야 한다.**

`AsyncYolo` 실제 구현을 보면:
- `_pending_frame`은 1개만 유지
- 새 submit은 이전 pending을 덮어쓴다
- 즉 **frame queue 폭증 자체는 없다**

이건 중요한 정정이다.

따라서 이제 더 정확한 표현은 이렇다.

> **문제는 frame queue가 무한히 쌓이는 게 아니라, “최신 1개 overwrite 구조 + 즉시 latest 소비 패턴” 때문에 현재 프레임과 결과 소비 시점이 느슨하게 분리되어 stale truth가 구조적으로 생긴다는 것**이다.

즉 병목의 정체는 “queue backlog”라기보다,

- 느린 inference
- overwrite pending
- 즉시 latest 소비

이 3개가 합쳐진 **비동기-동기 접합부 설계 문제**다.

---

### 2. Q1 / backpressure에 대한 내 수정 입장

Claude가 맞다.

내가 말한 “inflight gate”는 현재 `AsyncYolo` public API만으로는 바로 구현되지 않는다.

왜냐하면:
- `_pending_frame is not None`는 pending만 의미하고
- 실제 `runner.detect()` 수행 중인 inflight 상태는 외부에서 모른다.

그래서 **P1에서 바로 넣을 단기 해법**은 다음처럼 바꾸는 게 맞다.

### 단기(P1) 최종안

1. **`is_busy()` 추가 없이 간접 gate 사용**
2. `age_ms`를 이용해 너무 최근 완료 결과가 있으면 재submit을 skip
3. stale drop과 함께 “submit 밀도”를 낮춤

즉 P1에선:

> **backpressure = inflight flag 기반 정밀 제어**가 아니라,
> **age_ms 기반 submit suppression**

으로 가는 게 현실적이다.

이건 내가 채택한다.

### 장기(P2) 최종안

그 다음 P2에서:
- `_running_infer` 같은 명시 플래그를 AsyncYolo에 추가하거나
- callback push 구조로 완전히 바꿔서
- 진짜 inflight-aware producer로 전환

이 순서가 맞다.

---

### 3. Q3 / white cache 무효화에 대한 내 최종 입장

여긴 Claude 의견을 **그대로 채택**한다.

내가 earlier하게 “red 강검출 시 검토”라고 둔 건 약했다.

정확히는:

> **red 강검출 시 white cache 무효화는 선택이 아니라 필수**다.

이유는 단순하다.

- white cache는 tab-confirm arm을 유지하기 위한 임시 보정인데
- red가 강하게 들어왔다는 건 현재 state가 이미 white 종료 쪽으로 넘어갔다는 뜻이다
- 이때 white cache가 계속 살아있으면 arm 조건을 왜곡한다

따라서 최종 조건은 이렇게 박는 게 맞다.

1. white 1회 감지 → TTL 캐시 시작
2. TTL 내 추가 white 없더라도 잠시 유지
3. **red 강검출 발생 시 즉시 white cache 폐기**
4. 그 폐기를 로그로 남김

즉 white cache는 “흰탭 짧음 보정”이지,
red와 white를 동시에 참이라고 우기는 완충층이 되면 안 된다.

---

### 4. 이번 3차 라운드에서 정리된 최종안

이제 합의안을 더 정확히 다시 쓰면 이렇다.

#### P0
1. 실행본 CPU 강제 반영 확인
2. `age_ms > 150` stale drop
3. `poll_sec 0.05 -> 0.08/0.09` 실험
4. crop 입력 일관성 강제

#### P1
5. **age_ms 기반 submit suppression**
   - inflight flag 없는 상태에서 현실적 단기안
6. **white cache(TTL 200~300ms) + red 강검출 즉시 무효화**
7. P50/P95/P99 + stale/cache/drop 계측

#### P2
8. AsyncYolo에 명시적 inflight state 도입 또는 callback push 구조 전환
9. watcher는 consume-only 구조로 정리

---

### 5. 최종 3차 한 줄 의견

**Claude 3차 코멘트로 인해 한 가지는 정정하고(큐 폭증이 아니라 overwrite+stale 소비), 한 가지는 더 강하게 확정됐다(white cache는 red 강검출 시 즉시 무효화 필수). 따라서 단기안은 “age_ms 기반 submit suppression + white cache/즉시 무효화”, 장기안은 “명시적 inflight state 또는 callback push 전환”으로 정리하는 게 맞다.**

---

## [Claude 4차 — _loop() 동작 정밀 확인 후 2026-05-02]

이번엔 `_loop()` 전체 흐름을 줄 단위로 읽었다. 합의안 P1의 핵심 항목에 수정이 필요하다.

---

### _loop() 동작 실측 (`yolo.py:376-407`)

```
_wake.wait(timeout=0.2)   # submit() 신호 또는 200ms 타임아웃 대기
_wake.clear()
pending_frame 꺼냄         # 꺼내면서 _pending_frame = None
runner.detect(frame)       # 이 동안 블로킹 (200~500ms)
_latest 갱신               # 완료 후 결과 저장
```

**핵심 동작 두 가지:**

**1. 추론 중 submit()은 자연스럽게 큐잉된다**

`_loop()`가 `runner.detect(frame)` 실행 중일 때 `_pending_frame = None`이다.
이 상태에서 20Hz로 submit이 들어오면 각각 `_pending_frame`을 덮어쓰기만 한다.
추론이 끝난 뒤 loop는 다시 돌아서 당시 최신 pending을 꺼내 다음 추론을 시작한다.

즉 `_loop()`는 **자연적인 single-worker backpressure를 이미 갖고 있다.**
submit이 몇 번 들어와도 한 번에 하나씩, 항상 최신 frame만 처리한다.

**2. submit backpressure를 따로 추가해도 추론 속도는 안 빨라진다**

추론 중 submit을 skip해도 `_loop()`가 처리하는 frame 수는 변하지 않는다.
추론이 완료된 시점에 pending이 있으면 그 frame을 처리하고, 없으면 타임아웃(0.2s) 후 재시도할 뿐이다.

---

### 결론: P1 “age_ms 기반 submit suppression”의 실제 효과는 제한적

Codex와 나 모두 P1에 “submit suppression”을 넣기로 했다. 하지만 `_loop()` 구조를 보면:

> **submit 빈도를 줄여도 inference 처리 속도는 변하지 않는다.**

submit suppression의 실제 효과는:
- watcher `_tick()` → `submit()` 호출 횟수 감소 → `_wake.set()` 횟수 감소
- CPU overhead 약간 절감
- 그게 전부다

반면 **stale gate(age_ms > 150 drop)** 의 효과는:
- downstream(brain/muscle)이 오래된 detection을 truth로 소비하는 것을 차단
- tab-confirm 오작동 직접 방지

즉 **P0 stale gate가 P1 submit suppression보다 훨씬 중요하다.**

---

### age_ms 4-zone 정책 제안

지금까지 논의를 정리하면 `age_ms`를 4구간으로 나누는 정책이 더 명확하다.

```
age_ms < 0          : 결과 없음 → return []
age_ms 0~30         : 방금 완료 → 그대로 소비, submit skip (선택적)
age_ms 30~150       : 정상 freshness window → 소비 + submit
age_ms > 150        : stale → drop (return []), submit은 계속
```

이 4구간을 하나의 정책으로 `RealYoloAdapter.predict()`에 적용하면
지금처럼 `age_ms < 0` 한 가지 체크만 있는 것보다 훨씬 명확해진다.

---

### P1 우선순위 재조정 제안

이 분석을 반영하면 P0/P1 경계를 다시 보는 게 맞다.

현재 합의:
- P0: stale gate, poll 완화, crop 강제, CPU 패치 확인
- P1: submit suppression, white cache, 계측

**수정 제안:**

- **P0에 white cache를 올린다.**
  - poll을 0.09로 완화하면 white cache 없이는 confirm=3 누락 위험이 바로 생긴다.
  - 즉 poll 완화와 white cache는 **세트**로 같이 P0에 가야 한다.

- **submit suppression은 P2로 내린다.**
  - `_loop()` 구조상 추론 속도에 영향 없음이 확인됐다.
  - 측정 데이터 쌓은 뒤 CPU overhead가 유의미하게 크면 그때 추가해도 늦지 않다.

---

### 최종 재정렬 제안

#### P0 (바로 넣기)
1. CPU 강제 패치 배포본 확인
2. `age_ms > 150` stale drop (4-zone 정책 전체 적용)
3. `poll_sec 0.05 → 0.09`
4. **white cache(TTL 200~300ms) + red 즉시 무효화** ← poll 완화와 세트
5. crop 입력 일관성 강제

#### P1 (P0 검증 후)
6. P50/P95/P99 + stale/cache/drop 계측
7. 계측 결과 기반 threshold 조정

#### P2 (근본 구조)
8. submit suppression(age_ms 간접 또는 inflight flag)
9. async-producer / consume-only watcher 전환

---

### 한 줄 정리

**`_loop()`는 이미 자연적 single-worker backpressure를 갖고 있어서 submit 빈도를 줄여도 추론 속도는 변하지 않는다. 따라서 P1 submit suppression의 우선순위를 낮추고, poll 완화와 세트인 white cache를 P0으로 올리는 게 더 현실적인 순서다.**

---

## [내 4차 코멘트 — Claude 4차 재정렬안 검토]

이번 4차 코멘트는 **핵심 하나를 정확히 바로잡았다.**

내가 이전 라운드에서 P1에 두었던 `submit suppression`은, “추론 속도 자체를 낮출 수 있는가” 기준으로 보면 우선순위를 낮춰야 한다는 지적이 맞다.

### 1. 내가 동의하는 부분

#### 1-1. `_loop()`가 이미 natural single-worker backpressure를 가진다는 점

이건 중요하다.

`AsyncYolo`는:
- pending 1개만 유지
- detect()는 worker thread 1개에서만 실행
- 새 submit은 최신 pending 덮어쓰기

즉 **추론 파이프라인 자체는 이미 single-worker**다.

그래서:

> submit 호출 횟수를 줄인다고 해서 GPU가 처리하는 detect 횟수 자체가 즉시 줄어드는 건 아니다.

이건 맞는 정정이다.

#### 1-2. stale gate가 submit suppression보다 훨씬 직접적인 효과를 갖는다는 점

이것도 동의한다.

실제 운영 문제는:
- 느린 결과가 downstream으로 흘러 들어가
- white/red tab truth를 왜곡하고
- tab-confirm / recovery / muscle 판단을 오염시키는 것

이기 때문에,

> **우선순위는 “더 적게 submit”보다 “오래된 결과를 truth로 안 쓰게 막는 것”**

이다.

즉 `age_ms > 150 drop`은 P0이 맞다.

#### 1-3. white cache를 P0으로 올려야 한다는 점

이것도 맞다.

만약 poll을 `0.05 -> 0.09`로 늦춘다면,
white cache 없이 confirm=3을 그대로 유지할 경우 누락 위험이 바로 커진다.

그래서:
- poll 완화
- white cache
- red 강검출 즉시 무효화

이 셋은 **세트로 움직여야 한다.**

이건 내가 채택한다.

---

### 2. 내가 보완하고 싶은 부분

#### 2-1. submit suppression을 완전히 P2로 내리는 건 조금 이르다

Claude 4차는 submit suppression 우선순위를 낮추는 데는 맞다.
하지만 **완전히 P2로 내려버리는 건 약간 과감하다.**

왜냐하면 submit suppression은 추론 속도를 빠르게 하진 못해도,

- `_wake.set()` 빈도 감소
- watcher CPU 오버헤드 감소
- 불필요한 latest/read 패턴 감소

같은 부수 효과는 여전히 있다.

따라서 내 최종 입장은:

- **추론 속도 개선책으로서의 submit suppression** → 우선순위 낮음
- **watcher 부하 억제용 마이크로 최적화로서의 submit suppression** → P1 하단 정도는 유지 가능

즉 완전히 P2로 내리기보단,

> **“성능 핵심축은 아니지만, 측정 후 값이 있으면 넣을 수 있는 P1-low”**

정도로 두는 게 더 현실적이다.

#### 2-2. 4-zone 정책은 좋지만, 0~30ms 구간의 “submit skip”은 선택사항으로 분리해야 한다

Claude 제안:

```text
age_ms 0~30 : 방금 완료 → 그대로 소비, submit skip (선택적)
```

이건 좋은 정책이다. 다만 문서상 더 명확히 써야 한다.

왜냐하면 여기서 핵심은 둘이다.

1. **truth 소비 정책**
2. **submit 여부 정책**

이 둘을 한 줄에 같이 쓰면 구현자가 헷갈릴 수 있다.

그래서 나는 4-zone을 아래처럼 분리해서 쓰는 게 더 좋다고 본다.

##### consume policy
- `< 0` : 없음
- `0~150` : 소비 가능
- `> 150` : stale drop

##### submit policy
- `0~30` : 새 submit skip 가능
- `30+` : submit 허용

즉 4-zone 개념은 유지하되,
**consume과 submit 결정을 분리 표기**해야 구현 실수가 줄어든다.

---

### 3. 내가 확정하는 재정렬안

Claude 4차를 반영해, 나는 최종 순서를 이렇게 정리한다.

#### P0
1. 실행본 CPU 강제 반영 확인
2. `age_ms` 4-zone 정책 도입
   - 핵심은 `>150ms stale drop`
3. `poll_sec 0.05 -> 0.08/0.09` 완화
4. **white cache(TTL 200~300ms) + red 즉시 무효화**
5. crop 입력 일관성 강제

#### P1
6. P50/P95/P99 + stale/cache/drop 계측
7. threshold 재조정
8. submit suppression은 **측정 결과 보고 선택 적용**

#### P2
9. AsyncYolo inflight state 노출 또는 callback push 구조 전환
10. watcher consume-only 구조 정리

---

### 4. 최종 4차 한 줄 의견

**Claude 4차 코멘트의 핵심은 맞다: 지금은 submit suppression보다 stale drop과 white cache가 더 중요하다. 다만 submit suppression은 완전 폐기보다 P1 저우선 최적화로 남겨두고, 구현 문서에서는 consume 정책과 submit 정책을 분리해서 적는 게 더 안전하다.**

---

## [Claude 5차 — 정책 구현 위치 정리 2026-05-02]

Codex 4차에서 consume/submit 정책 분리 표기를 제안했다. 동의한다.
이번엔 그 정책이 **코드 어느 위치에 들어가야 하는지**를 정리한다.

---

### 정책별 구현 위치

현재 호출 체인:

```
YoloWatcher._tick()
  └─ self.yolo.predict(frame)          # yolo_watcher.py:86
       └─ RealYoloAdapter.predict()    # yolo_adapter.py:81
            ├─ self._async.submit()    # yolo_adapter.py:85
            └─ self._async.latest()   # yolo_adapter.py:86
```

#### consume 정책 (stale drop) — `RealYoloAdapter.predict()` 에 넣는다

`age_ms > 150` → `return []` 로 처리하면 `YoloWatcher._tick()`은 빈 리스트를 받는다.
watcher는 `preds = []` → `red_now = False`, `white_now = False`로 해석한다.

**이 상태에서 white cache 동작:**
- `white_now = False` + `_white_pending_ts` 살아있음 → TTL 체크
- TTL 내면 `white_now = True`로 유지 → downstream에 white 계속 흘림

즉 stale drop(`[]` 반환)과 white cache(watcher 내 TTL 연장)는 **자연스럽게 맞물린다.**
stale인 동안 새 정보 없음 → 캐시가 마지막 검출 상태를 유지 → 흰탭 누락 방지.

#### submit 정책 (suppression) — 같은 `RealYoloAdapter.predict()` 에 넣는다

```python
def predict(self, frame):
    _, _, age_ms, _ = self._async.latest()
    if age_ms >= 0 and age_ms < 30:
        # 방금 완료 — 재submit 불필요, 캐시 소비
        return self._cached_result
    self._async.submit(frame, (0, 0))
    new_dets, _off, age_ms, _ = self._async.latest()
    if age_ms < 0:
        return []
    if age_ms > 150:
        return []   # stale drop
    ...
```

두 정책 모두 adapter 안에 격리되므로 `YoloWatcher`는 수정하지 않아도 된다.

---

### white cache 구현 위치 — `YoloWatcher._tick()` 에 넣는다

white cache는 **감지 결과의 시간적 연장**이므로 adapter가 아닌 watcher가 적합하다.

이유:
- adapter는 단일 프레임 단위 처리 (`predict(frame) → list`)
- cache TTL은 여러 틱에 걸친 시간 상태 — watcher 레벨 책임

즉 최종 위치는:

| 정책 | 위치 |
|---|---|
| stale drop (`age_ms > 150 → []`) | `RealYoloAdapter.predict()` |
| submit skip (`age_ms 0~30 → skip`) | `RealYoloAdapter.predict()` |
| white cache TTL | `YoloWatcher._tick()` |
| red 강검출 → white cache 무효화 | `YoloWatcher._tick()` |

이렇게 나누면 두 클래스의 책임이 명확하다.
- adapter: 단일 추론 결과의 freshness 판단
- watcher: 시간적 상태 연장 및 무효화

---

### submit suppression 위치 — P1 저우선 유지에 동의

Codex 4차 결론("P1-low로 유지")에 동의한다. 단 구현 시 주의사항:

`age_ms < 30` submit skip은 "방금 완료 결과가 있을 때 재submit을 미루는 것"이다.
이때 `_cached_result`를 들고 있어야 하는데, `predict()`가 매번 `latest()`를 호출하면 cached_result가 없다.

따라서 P1에서 넣을 때:
- `self._last_result` 필드를 `RealYoloAdapter`에 추가
- `age_ms < 30`이면 `self._last_result` 반환, submit skip

이 변경은 adapter 내부로만 국한되어 watcher 수정 없이 가능하다.

---

### 수렴 신호

1차부터 5차까지 논의를 통해 다음이 확정됐다:

| 항목 | 확정 결론 |
|---|---|
| spike 원인 | 하드웨어 아님. overwrite+즉시소비 구조 + stale gate 부재 |
| `_loop()` 구조 | natural single-worker. 별도 backpressure 불필요 |
| stale drop | `age_ms > 150 → []`, adapter에 위치 |
| white cache | `YoloWatcher._tick()` 내 TTL, red 즉시 무효화 필수 |
| poll 완화 | `0.05 → 0.09`, white cache와 세트 |
| submit suppression | P1-low, adapter 내부 `_last_result` 캐시로 구현 |
| P2 | async-producer + consume-only watcher |

분석은 충분히 됐다고 본다. **P0 구현으로 넘어가도 될 시점**이다.

---

## [내 5차 코멘트 — 정책 구현 위치 정리안 검토]

이번 Claude 5차 정리는 꽤 좋다. 
특히 **어떤 정책을 어느 계층에 넣을지 분리한 것**은 이제 구현으로 넘어가기 전에 꼭 필요한 정리다.

### 1. 내가 동의하는 부분

#### 1-1. stale drop은 adapter 책임이라는 점

이건 맞다.

`age_ms > 150`는
- 추론 결과의 freshness 판정
- 단일 inference 결과의 유효성 판단

이기 때문에 watcher보다 `RealYoloAdapter.predict()` 안에 두는 게 맞다.

즉:

> **adapter는 “이 결과를 써도 되나”를 결정하고, watcher는 “이 결과를 시간축에서 어떻게 유지/무효화하나”를 결정한다**

이 분리가 맞다.

#### 1-2. white cache는 watcher 책임이라는 점

이것도 맞다.

white cache는 한 프레임 결과가 아니라
- 여러 tick에 걸친 유지 시간
- red 검출 시 무효화
- arm 직전 캐시 hit 로그

같은 **시간적 상태 관리**다.

그래서 `YoloWatcher._tick()` 쪽 책임으로 두는 게 맞다.

#### 1-3. `_loop()`가 natural single-worker라는 결론

이건 이제 거의 합의로 봐도 된다.

즉 내가 이전에 가졌던 “submit suppression이 추론 throughput 자체를 유의미하게 바꿀 수 있나?” 의문은,
이번 정리로 거의 정리됐다.

> **throughput 핵심은 suppress보다 stale drop / poll budget / crop budget 쪽이다**

---

### 2. 내가 보완하고 싶은 부분

#### 2-1. `predict()` 안에서 `latest()`를 두 번 읽는 구조는 조심해야 한다

Claude 예시 코드는:

```python
_, _, age_ms, _ = self._async.latest()
if age_ms >= 0 and age_ms < 30:
    return self._cached_result
self._async.submit(frame, (0, 0))
new_dets, _off, age_ms, _ = self._async.latest()
```

이 방향 자체는 이해된다. 하지만 구현 시 주의해야 한다.

왜냐하면 `latest()`를 두 번 읽는 순간:
- 첫 번째 읽기와
- 두 번째 읽기 사이에
background thread가 완료 상태를 바꿀 수 있기 때문이다.

즉 아주 미세하지만 race window가 있다.

그래서 내가 추천하는 건:

1. `latest()` 결과를 한 번 읽고
2. 그 값 기준으로
   - skip할지
   - submit할지
   - 캐시를 쓸지
3. submit 후엔 **이번 tick에 굳이 second latest를 다시 읽지 말고**, 다음 tick 소비로 넘기는 방식을 우선 검토

즉 단기안에서도 가능하면:

> **1 tick = 1 latest read**

원칙으로 가는 게 더 안전하다.

#### 2-2. `_last_result` 캐시는 결과뿐 아니라 메타도 같이 가져야 한다

Claude가 `self._last_result`를 두자고 한 건 맞다.
근데 결과만 들고 있으면 나중에 디버깅이 힘들다.

내가 넣고 싶은 건 최소 이거다.

```python
self._last_result
self._last_age_ms
self._last_predict_ms
self._last_frame_shape
self._last_source_ts
```

그래야
- 왜 skip됐는지
- 왜 stale drop됐는지
- 어떤 입력 크기에서 그랬는지
바로 로그로 남길 수 있다.

즉 cached_result는 단순 detections가 아니라,
**결과+메타 묶음**이어야 한다.

#### 2-3. `age_ms > 150 -> []`는 state reset 효과를 낳는다는 점을 문서에 더 강조해야 한다

이건 구현상 중요하다.

`[]`를 반환하면 watcher는:
- `red_now = False`
- `white_now = False`

로 해석한다.

white cache가 없는 구간에선 이게 곧
- red/white 상태 reset
- downstream false edge

로 이어질 수 있다.

따라서 stale drop은 반드시:
- white cache
- red cache는 둘지 말지 정책
- `source_state=stale_drop` 진단 로그

와 함께 가야 한다.

즉 `return []`는 단순 skip이 아니라,
**상태를 false로 밀어버리는 강한 동작**이라는 점을 구현자가 알아야 한다.

---

### 3. 내가 확정하는 구현 원칙

이번 5차 논의까지 반영해서, 구현 원칙을 더 짧게 정리하면 이렇다.

#### Adapter (`RealYoloAdapter.predict`)
- freshness 판정
- stale drop
- 선택적 submit skip
- 결과/메타 캐시 보관

#### Watcher (`YoloWatcher._tick`)
- red/white best selection 소비
- white cache TTL 유지
- red 검출 시 white cache 무효화
- bus/store 반영

즉:

> **Adapter는 “프레임 결과의 품질 판정”, Watcher는 “시간축 상태 관리”**

이걸 최종 책임 경계로 잡는 게 맞다.

---

### 4. 최종 5차 한 줄 의견

**Claude 5차 정리는 구현 위치 분리에 있어 타당하다. 다만 실제 패치에선 `latest()` 다중 읽기 race를 피하고, `_last_result`를 메타 포함 캐시로 확장하며, `stale -> []`가 downstream state reset을 유발한다는 점을 함께 문서화해야 안전하다.**

---

## [Claude 6차 — race 해결 + red cache 정책 + P0 패치 시그니처 2026-05-02]

Codex 5차에서 두 가지 중요한 포인트가 나왔다.
1. `latest()` 이중 읽기 race window
2. `stale → []` state reset + red cache 정책 질문

둘 다 P0 구현 전에 확정해야 한다. 정리한다.

---

### 1. `1 tick = 1 latest read` 원칙 — 구체 코드

Codex가 지적한 이중 읽기 race를 피하려면 `latest()`를 틱당 1회만 읽고 그 값으로 모든 분기를 처리해야 한다.

현재 내 5차 예시 코드엔 `latest()` 두 번 호출이 있었다. 이를 수정한 단일 읽기 구조:

```python
def predict(self, frame):
    if frame is None:
        return []

    # ── 단일 읽기 ──────────────────────────────
    new_dets, _off, age_ms, predict_ms = self._async.latest()
    # ────────────────────────────────────────────

    if age_ms < 0:
        # 아직 결과 없음 → submit만 하고 빈 반환
        self._async.submit(frame, (0, 0))
        return []

    if age_ms > 150:
        # stale → submit(다음 추론 큐잉) + 빈 반환
        self._async.submit(frame, (0, 0))
        self._log_stale(age_ms)
        return []

    if 0 <= age_ms < 30:
        # 방금 완료 → 재submit 불필요, 직전 결과 재소비 (P1-low)
        return self._last_result or []

    # 정상 window (30~150ms) → submit + 결과 반환
    self._async.submit(frame, (0, 0))
    result = self._build_result(new_dets, _off)
    self._last_result = result
    self._last_meta = {
        "age_ms": age_ms, "predict_ms": predict_ms,
        "frame_shape": getattr(frame, "shape", None)
    }
    return result
```

**핵심:** submit은 항상 결과 판정 이후에 호출된다. latest() 읽기와 submit() 사이에 background thread가 결과를 바꿔도 이미 snapshot은 확보한 상태다.

---

### 2. red cache — 명시적 NO, 이유 있음

Codex 5차에서 red cache "둘지 말지 정책"을 열어뒀다. 내 답은 **no cache**다.

이유:
- **red_tab은 격수 위치 추종의 트리거다.** (`project_hunting_mechanism.md`, `project_redbox_target.md`)
- red_tab 캐시가 살아있으면 격수가 이미 이동한 뒤에도 이전 위치로 힐을 보낸다.
- red의 오래된 bbox를 현재 truth로 쓰면 **힐 실패가 stale detection 때문인지 격수 이동 때문인지 구분 불가**해진다.

**white cache는 arm 유지를 위한 임시 연장이고, red cache는 위치 오염이다.**

따라서:
- white_tab: TTL 캐시 허용 (arm 누락 방지)
- red_tab: 캐시 없음, 매 틱 fresh detection 요구

stale 구간 동안 red가 `False`로 보이면 그건 정보 없음이지 red 없음이 아니다. 이 구분은 진단 로그로만 남기고 downstream에는 흘리지 않는다.

---

### 3. `stale → []` state reset 완화 — 진단 로그 추가

Codex 5차 지적대로 `return []`는 watcher 입장에서 `red=False, white=False`다.
white cache가 없는 구간에서 stale drop이 반복되면 downstream이 false edge를 계속 받는다.

이미 P0에 white cache가 들어가므로 white false-edge는 막힌다.
red false-edge는 설계적으로 허용(위 §2 이유)한다.

단 **stale drop임을 표시하는 진단 로그는 필수**다:

```python
def _log_stale(self, age_ms: float) -> None:
    log.debug("YOLO-STALE age_ms=%.0f → drop", age_ms)
    if self._log_emit:
        self._log_emit(f"[YOLO-STALE] age_ms={age_ms:.0f}ms drop")
```

이렇게 하면 로그에서 `YOLO-STALE`이 연속으로 나오면 "GPU가 밀리고 있다"는 신호로 바로 읽힌다.

---

### 4. P0 패치 파일 목록 확정

이제 구현으로 넘어가기 위한 변경 대상 파일과 변경 내용을 확정한다.

| 파일 | 변경 내용 |
|---|---|
| `src_v2/adapters/yolo_adapter.py` | `RealYoloAdapter.predict()` 4-zone 정책 + 단일 latest read + `_last_result/_last_meta` 필드 추가 + `_log_stale()` |
| `src_v2/eyes/yolo_watcher.py` | `poll_sec=0.05 → 0.09`, `_white_pending_ts` 필드 추가, white cache TTL 로직, red 강검출 무효화, `[YOLO-WHITE-CACHE]` 로그 |
| 배포본 확인 | `C:\oldbaram\src\vision\xp_ocr.py` CPU 강제 패치 반영 여부 |

외부 변경 없음: `AsyncYolo`, `YoloRunner`, `BaseWatcher`, brain/muscle 하위 모듈 전부 수정 없이 가능.

---

### 5. 최종 수렴 확인

이번 6차까지 양측이 확정한 사항:

- race 방지: `1 tick = 1 latest read` ✅
- red cache: 명시적 NO ✅  
- stale log: `YOLO-STALE` 진단 로그 추가 ✅
- 변경 파일: 2개 (`yolo_adapter.py`, `yolo_watcher.py`) ✅

**이견 없으면 P0 구현 시작해도 된다고 본다.**

---

## [내 6차 코멘트 — Claude 6차 최종 수렴안 검토]

이번 6차 답변은 꽤 좋다. 
이제는 “무엇이 문제냐” 수준을 넘어서, **P0 패치를 어디에 어떻게 넣을지**까지 거의 구현 가능한 해상도로 내려왔다.

### 1. 내가 동의하는 부분

#### 1-1. `1 tick = 1 latest read` 원칙 확정

이건 전적으로 동의한다.

이전 라운드에서 내가 가장 찝었던 위험이 바로
- `latest()` 이중 읽기
- 그 사이 background completion race

였는데, 이번 6차에서 단일 읽기 구조로 정리한 건 맞다.

특히 이 문장:

> submit은 항상 결과 판정 이후에 호출된다. latest() 읽기와 submit() 사이에 background thread가 결과를 바꿔도 이미 snapshot은 확보한 상태다.

이건 실제 구현 안정성 측면에서 핵심이다.

#### 1-2. red cache를 명시적으로 금지한 점

이것도 맞다.

white cache는 arm 누락 보정용이지만,
red cache는 stale bbox를 현재 격수 위치처럼 쓰는 순간 오히려 더 위험해진다.

즉:
- white는 “짧게 살아도 되는 상태 신호”
- red는 “지금 당장의 위치 truth”

라서 취급이 달라야 한다.

따라서 **red cache 없음**을 명시적으로 못 박은 건 옳다.

#### 1-3. 변경 파일 2개로 P0를 제한한 점

이것도 매우 좋다.

지금 Stop-Ship 단계에서
- `AsyncYolo`
- `YoloRunner`
- brain/muscle 전체

까지 건드리기 시작하면 범위가 다시 커진다.

이번 정리처럼:
- `src_v2/adapters/yolo_adapter.py`
- `src_v2/eyes/yolo_watcher.py`

두 파일로 P0를 제한하는 건 현실적이다.

---

### 2. 내가 보완하고 싶은 부분

#### 2-1. `age_ms < 30`에서 `_last_result or []`는 첫 warmup 구간에 빈 결과 연속을 만들 수 있다

이건 큰 문제는 아니지만 구현 시 체크해야 한다.

현재 제안은:

```python
if 0 <= age_ms < 30:
    return self._last_result or []
```

그런데 초기 warmup 직후나 첫 valid detection 직전엔 `_last_result`가 아직 비어 있을 수 있다.
이 경우 `[]`가 계속 나갈 수 있다.

물론 기능적으로 치명적이진 않다. 다만 구현자는 아래를 분리해서 봐야 한다.

1. **결과가 진짜 없음**
2. **fresh하지만 아직 캐시가 없음**

그래서 내 권장은:
- `_last_result is None`이면 `[]` 반환은 허용
- 대신 로그를 남겨서
  - `[YOLO-SKIP] fresh_no_cache age_ms=...`
  - `[YOLO-SKIP] fresh_cached age_ms=...`
  정도로 구분해주면 좋다.

즉 P0 필수는 아니지만, **디버그 로그 구분은 추천**이다.

#### 2-2. stale 구간의 red false-edge는 “설계적으로 허용”이지만, downstream 영향 관찰 포인트를 더 적어야 한다

Claude는 red false-edge를 설계적으로 허용한다고 정리했다. 이건 논리적으로 맞다.

하지만 운영 검증에서는 아래를 반드시 봐야 한다.

- `seq_rclick`
- red 기반 target tracking
- self_heal 중 red_pos 의존 분기

이 stale drop 때문에 과도하게 끊기지 않는지.

즉 red cache는 두지 않더라도,
**red false-edge 허용의 운영 비용**은 검증 항목으로 분리해야 한다.

내 제안:
- P0 검증 로그 체크 항목에
  - `YOLO-STALE` 발생 시 `seq_rclick miss` 급증 여부
  - red 기반 action 누락 여부
  를 추가

이건 문서 하단에 다음 라운드 검증 포인트로 붙이면 좋다.

#### 2-3. `poll_sec=0.09`는 고정 확정이 아니라 실험값이라는 표현을 더 강하게 해야 한다

문서상 지금은 `0.05 -> 0.09`가 거의 확정처럼 읽힐 수 있다.

하지만 정확히는:
- 0.08일 수도 있고
- 0.09일 수도 있고
- 0.10은 과할 수도 있다.

즉 이 값은 **정책값이 아니라 실험 시작점**이다.

그래서 표현은 이렇게 두는 게 좋다.

> `poll_sec 0.08~0.09 범위 실험 시작, white cache와 세트 검증 후 확정`

이게 더 안전하다.

---

### 3. 내가 확정하는 최종 코멘트

이번 6차까지 오면서 이제 핵심은 거의 수렴했다.

내 최종 입장은 다음과 같다.

1. **P0 구현 범위는 2파일로 제한한다**
   - `yolo_adapter.py`
   - `yolo_watcher.py`

2. **adapter 책임**
   - 단일 latest read
   - stale drop
   - optional fresh skip
   - result/meta cache

3. **watcher 책임**
   - white cache TTL
   - red 검출 시 white cache 즉시 무효화
   - publish/store 반영

4. **red cache는 두지 않는다**

5. **poll 값은 0.09 고정이 아니라 0.08~0.09 실험값으로 본다**

6. **P0 검증 항목에 red false-edge 비용도 넣어야 한다**

---

### 4. 최종 6차 한 줄 의견

**Claude 6차 답변은 구현 착수 가능한 수준까지 수렴했다. 나는 여기에 ‘fresh_no_cache 로그 구분’, ‘red false-edge 운영 검증’, ‘poll 0.08~0.09 실험값 명시’ 세 가지만 보완하면, 바로 P0 패치로 넘어가도 된다고 본다.**

---

## [내 7차 코멘트 — 구체 패치안 타당성 검토]

제시된 패치안은 **전체적으로 타당하다.**
특히 지금까지 수렴된 논의를 실제 수정 포인트로 잘 압축했다.

### 1. `yolo_adapter.py` 제안 검토

#### 타당한 점
- `STALE_MS=150`, `FRESH_MS=30` 상수화
  - 매직넘버를 코드 본문에서 제거한다는 점에서 좋다.
- `predict()` 4-zone 정책
  - 지금까지 합의한 핵심을 그대로 구현에 옮기는 방식이라 맞다.
- `단일 latest() 읽기`
  - race window 축소 관점에서 필수에 가깝다.
- `_build_result()` 헬퍼 분리
  - `predict()` 안의 freshness 정책과 result 변환 로직을 분리해서 가독성이 좋아진다.
- `_log_stale()` / `[YOLO-STALE]`
  - 운영 로그에서 stale 연속 발생 구간을 즉시 식별할 수 있어 유효하다.
- `_last_result`, `_last_meta`
  - submit skip / 디버깅 / 사후 분석에 필요하다.

#### 보완 코멘트
1. **`_last_meta`에는 최소한 아래 4개가 들어가야 한다.**
   - `age_ms`
   - `predict_ms`
   - `frame_shape`
   - `source_ts` 또는 latest 완료 시각

2. **`FRESH_MS=30`은 정책값이지 진리가 아니다.**
   - 첫 배포는 30ms로 가도 되지만,
   - 검증 결과에 따라 20/40ms 조정 가능성을 열어둬야 한다.

3. **`age_ms < 30` + `_last_result is None` 구간 로그를 분리하는 게 좋다.**
   - 예: `[YOLO-SKIP] fresh_no_cache ...`
   - 지금 안 넣어도 되지만 디버그엔 도움이 크다.

4. **`stale -> []`는 강한 state reset 효과가 있으므로 주석을 꼭 달아야 한다.**
   - 단순 skip이 아니라 downstream에 `red=False`, `white=False`로 보일 수 있다는 점.

### 2. `yolo_watcher.py` 제안 검토

#### 타당한 점
- `poll_sec 0.05 → 0.09`
  - 현재 spike 상황 완화용 기본값 조정으로 타당하다.
- `WHITE_CACHE_TTL_MS = 250.0`
  - 200~300ms 범위에서 시작하자는 합의와 맞는다.
- `_white_pending_ts`, `_white_cached_det`
  - TTL 캐시 구현에 필요한 최소 필드다.
- red 강검출 즉시 무효화 + invalidated 로그
  - 이건 필수다. 아주 맞다.
- TTL 만료 시 expired 로그
  - 캐시가 왜 꺼졌는지 운영 로그로 구분 가능해서 좋다.

#### 보완 코멘트
1. **`poll_sec=0.09`는 확정값이라기보다 실험 기본값으로 주석을 남겨야 한다.**
   - 표현상 `0.08~0.09 검증 시작점`이라는 의미가 코드 주석에 남는 게 좋다.

2. **`_white_cached_det`는 bbox/conf를 그대로 재사용하되, fresh det와 구분 가능한 표식이 필요하다.**
   - 예: 로그에 `source=cached` 또는 `cache_hit=1`
   - 그래야 arm이 실제 검출인지 캐시 연장인지 구분 가능하다.

3. **expired 로그는 너무 자주 찍히지 않게 1회성 또는 상태 전이 시점에만 찍는 게 좋다.**
   - 매 tick 만료 확인마다 찍히면 로그가 지저분해질 수 있다.

4. **white cache는 red 무효화 외에도 fresh white 재검출 시 ts 갱신 정책을 명확히 해야 한다.**
   - fresh white가 다시 잡히면 TTL 리셋/연장된다는 점을 코드 주석으로 남겨야 구현 혼선이 없다.

### 3. 최종 판단

이 패치안은 **P0 구현안으로 충분히 타당하다.**

우선순위 관점에서도 적절하다.
- adapter: freshness/stale 정책
- watcher: white cache/무효화 정책

책임 분리도 지금까지 논의와 일치한다.

### 4. 최종 코멘트 한 줄

**제시된 패치안은 타당하며 바로 P0 구현에 들어가도 된다. 다만 `stale -> []`의 state reset 효과, `FRESH_MS=30`의 실험값 성격, 그리고 white cache가 fresh 검출인지 cached 유지인지 로그로 구분되는 점만 같이 보강하면 더 안전하다.**