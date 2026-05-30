# Blueprint — Signal (2026-04-28 초안)

> 본 문서는 v1 SoR(`dist_dosa/src/`)에서 추출한 입력 신호 publish/consume 계약이다.
> v2(`dist_dosa/src_v2/`)는 본 계약을 schema 수준으로 따른다.
> P2-0 지시에 따라 코드 수정 전 본 문서 diff 가 선행조건이다.

## 1. 신호 스트림 인벤토리

| 스트림 | v1 producer | v1 consumer | v2 producer | v2 topic |
|---|---|---|---|---|
| Cooldown OCR | `src/vision/cooldown_ocr.py` | `src/workers/healer_worker.py:820` (cd.skills) | `src_v2/eyes/cooldown_watcher.py` (slot=cd) | `eye.cooldown` + `eye.cooldown_state` |
| Buff OCR | `src/workers/healer_worker.py:258-270` | `healer_worker.py:825` (bf.skills) | `cooldown_watcher.py` (slot=buff) | `eye.cooldown` + `eye.cooldown_state` |
| Chat OCR | (별도 region) | (chat 텍스트) | `cooldown_watcher.py` (slot=chat) | `eye.cooldown` |
| HP/MP OCR | `src/vision/hpmp.py:99-100` | `healer_worker.py:847-849` (`_hpmp.latest()`) | `src_v2/eyes/hpmp_watcher.py` | `eye.hp` + `eye.mp` + `eye.hpmp_state` |
| Coord/Map OCR | `src/vision/ocr.py:41-51` | `src/app/attacker.py:56-67` (격수) / healer_worker:700-710 | `src_v2/eyes/ocr_watcher.py` | `eye.ocr` (구체 토픽 별도) |
| Red/White Tab YOLO | `src/vision/yolo.py` (Detection) | `healer_worker.py:688-696` (YoloRunner) | `src_v2/eyes/yolo_watcher.py` | `eye.yolo` |
| UDP State recv | `src/net/udp_receiver.py:69-84` | `healer_worker.py:748-749` | `src_v2/eyes/udp_watcher.py` | `eye.attacker_state` |
| UDP ControlCmd recv | `udp_receiver.py:96-101` | `src/workers/control_listener.py:79-107` | `udp_watcher`(handler) → `cmd_emit` | (handler 콜백, topic 없음) |
| CooldownReport recv (격수 PC) | `udp_receiver.py:145-166` (CooldownReceiver) | `src/workers/attacker_worker.py:270-368` | `src_v2/eyes/cooldown_uplink.py` | `eye.cooldown_uplink` |
| XP OCR | `src/vision/xp_ocr.py:78-93` | `src/app/attacker.py:72-74` | `src_v2/eyes/xp_watcher.py` | `eye.xp` |
| Game frame (capture) | DXcam/MSS | (모든 vision watcher 의 입력) | `src_v2/eyes/capture.py` | snapshot field `last_frame` |

## 2. Publish 계약 (모든 watcher 공통)

### 2.1 빈도 모델 — polling, edge 금지

워커 시작 시점에 이미 임계 미만이면 edge 가 절대 생성되지 않아 룰이 영원히 평가되지 않는다(이번 사건의 원인 중 하나). 따라서:

- 모든 OCR watcher 는 **매 tick publish** 강제. 값 변화 여부 무관.
- 빈 결과(`{}`/`-1`)도 publish 한다. consumer 가 stale 여부를 판단할 수 있게.

### 2.2 publish payload 구조

```
publish(topic, payload)
publish(topic + "_state", {
    "source_state": "unconfigured" | "empty" | "observed" | "rejected",
    ...payload sample...
})
```

`source_state` 정의:
- `unconfigured` — adapter `is_available()=False` (영역 미설정 / 모델 없음)
- `empty` — adapter read 결과가 비어있음 (영역은 있으나 OCR 미탐지)
- `observed` — 비어있지 않은 결과 1개 이상
- `rejected` — adapter read 가 예외 throw

### 2.3 Snapshot 갱신 규칙

- **bool 필드 reset 값 = `False`**, **int 필드 reset 값 = `-1`**.
- 이번 사건(공력증강 미발화) 이후 추가된 강제 사항. 절대 bool 필드를 `-1`로 박지 말 것.
- 매 tick `field_map.values()` 에 대해 reset → `result` 로 덮어쓰기.

## 3. 스트림별 필드 표

### 3.1 cooldown (slot=cd)
- payload: `Dict[skill_name(KO), seconds_remaining_int]`
- 한국어 키 SoR: `파력무참 / 백호의희원 / 파혼술 / 부활`
- 영문 alias: `parlyuk / baekho / parhon / revive`
- snapshot 필드: `cd_parlyuk / cd_baekho / cd_parhon / cd_revive` (모두 int, default `-1`)
- v2 publish 빈도: poll_sec=1.0
- 연결 룰: `baekho`, `parlyuk`(verify), `parhon`, `attacker_revive`

### 3.2 cooldown (slot=buff) — 본 사건 핵심
- payload (KO 키): `파력무참 / 백호의희원 / 공력증강 / 혼마술 / 무장 / 보호`
- snapshot bool 필드: `buff_parlyuk_active`, `buff_baekho_active`, `buff_gyoungryeok_active`
- snapshot int 필드: `self_debuff_honma_sec`, `self_buff_mujang_sec`, `self_buff_boho_sec`
- **bool vs int 필드 구분 강제** — reset 값 분리.
- v2 publish 빈도: poll_sec=1.0
- 연결 룰: `parlyuk`, `gyoungryeok`(buff 차단 게이트), `parhon`, `mujang`, `boho`

### 3.3 hpmp
- payload: `(hp_pct, mp_pct, hp_cur, mp_cur, hp_max, mp_max)` 모두 int. 미관측 = `-1`.
- snapshot: `hp / mp / hp_cur / mp_cur / hp_max / mp_max`
- v2 topic: `eye.hp` + `eye.mp` 분리 publish (값 변화 무관 매 tick).
- 연결 룰: `self_heal`(hp 임계), `self_revive`(hp==0 edge), `gyoungryeok`(mp 임계 edge), `attacker_revive`(atk_hp==0)

### 3.4 ocr (coord / map)
- payload: `{x: int, y: int, map_name: str, seq: int}`
- v1 throttle: map 2s, coord 30Hz
- jump_max filter + canonical map repair (knownmaps.txt) 필수
- 연결: 격수 attacker.py 가 본인 좌표 publish; 힐러는 UDP recv 로 격수 좌표 수신

### 3.5 yolo (red / white tab)
- payload (per detection): `{cls: 0(red)/1(white), cx, cy, conf, w, h}`
- 30Hz per-frame
- min_w / min_h / min_conf 필터 적용
- 연결 룰: `seq_rclick`(red 추격), `tab_lock`(white 미발견 시 ESC→TAB×2)

### 3.6 udp.attacker_state (힐러측 수신)
- v1 packet schema: `src/net/protocol.py:23-49` (State dataclass)
- 30Hz broadcast. seq 기반 stale 검출.
- consumer: 힐러 룰 전체 (격수 hp/buff/coord/map_change_pending 등)
- v2 추가 메타: `udp_active`(5초 grace), `attacker_seq`(누적 카운트), `[UDP-STALL]` / `[UDP-RESUME]` edge 로그

### 3.7 udp.cooldown_uplink (격수측 수신)
- packet schema: `src/net/protocol.py` (CooldownReport)
- 1Hz from each healer
- src_idx 기반 row 매핑 + IP fallback
- consumer: 격수 PC HUD overlay, 우선순위 스케줄링

### 3.8 xp
- payload: `{xp_per_hour, level_gained}`
- poll_sec=2.0
- consumer: hunt_reports 누적, level_gained edge → 빨탭 우선순위 게이트

## 4. 품질 메타 강제 항목

모든 watcher 는 `*_state` 토픽을 함께 publish 해 rule_engine 이 다음을 식별 가능하게 한다:
1. 영역 설정 누락 (`unconfigured`)
2. OCR 영역은 있으나 글자 미탐지 (`empty`)
3. OCR 영역 깨짐 (`rejected`)
4. 정상 관측 (`observed`)

`empty` / `unconfigured` / `rejected` 상태는 룰이 fire 결정에 사용할 수 없는 것으로 간주한다.

## 5. v2 위반 사례 (이번 발견)

| 위반 항목 | 위치 | 결과 | 수정안 |
|---|---|---|---|
| buff 슬롯 reset 이 bool 필드를 `-1`로 박음 | `src_v2/eyes/cooldown_watcher.py:172-174` | `buff_gyoungryeok_active` 항상 truthy → 룰 영구 차단 | bool/int 필드 분리 reset (별도 fix 문서 참고) |
| BaseWatcher.stop() 이 adapter.stop() 미호출 | `src_v2/eyes/base_watcher.py:69-72` | UDP socket 누수 → 재기동 bind 30회 실패 | stop chain 추가 |

## 6. 다음 단계

- [ ] 본 문서 schema 를 `src_v2/core/types.py` PublishContract dataclass 로 코드화
- [ ] watcher 단위 계약 테스트 추가: publish-on-empty / publish-on-no-change / source_state 일치
- [ ] PublishContract 위반 시 CI 차단
