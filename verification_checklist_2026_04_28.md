# 런타임 검증 체크리스트 — P0-1 / P0-4 (2026-04-28)

> **사용자가 직접 `C:\oldbaram` 환경에서 실행**하고, 결과를 본 문서 양식대로 회신.
> 답변 없이 다음 P0-2 commit 진행 불가.

---

## 0. 사전 준비 (필수, 1회)

다음 두 파일이 `C:\oldbaram\dist_dosa\src_v2\eyes\` 에 반영되어 있는지 확인:

```cmd
fc /B C:\oldbaram\dist_dosa\src_v2\eyes\base_watcher.py D:\oldbaram\dist_dosa\src_v2\eyes\base_watcher.py
fc /B C:\oldbaram\dist_dosa\src_v2\eyes\cooldown_watcher.py D:\oldbaram\dist_dosa\src_v2\eyes\cooldown_watcher.py
```

두 명령 모두 `FC: no differences encountered` 가 나와야 진행. 아니면 D→C 복사부터.

회신란:
- [ ] base_watcher.py 동기화 ✅/❌
- [ ] cooldown_watcher.py 동기화 ✅/❌

---

## 1. P0-1 검증 — UDP bind 재기동

### 1.1 시나리오
힐러 GUI 기동 → 워커 start → 1초 대기 → stop → 즉시 start. **20회 반복**.

### 1.2 회차별 회신 양식

각 회차 끝에 로그에서 다음 두 줄을 grep:

```cmd
findstr /C:"[UDP-RECV]" /C:"[UDP-BIND]" /C:"udp bind" C:\oldbaram\dist_dosa\logs\<오늘날짜>\*.log
```

각 회차마다 다음 표 한 줄:

| 회차 | bind 30회 실패 발생? | first State 수신? | 비고 |
|---|---|---|---|
| 1 | (Y/N) | (Y/N) | |
| 2 | | | |
| ... | | | |
| 20 | | | |

### 1.3 합격 기준
- bind 30회 실패: **0/20**
- first State 수신: **20/20**
- stop 후 3초 이내 port 51900(또는 cfg.net.port) 잔존 0건:
  ```cmd
  netstat -ano | findstr :51900
  ```
  결과 0줄

### 1.4 실패 시 추가 회수
실패 회차에서 다음 항목 모두 첨부:
- 해당 회차 facade 로그 마지막 50줄
- `netstat -ano | findstr :51900` 결과
- `tasklist /v /fi "imagename eq python*.exe"` 결과 (점유 PID 식별용)

---

## 2. P0-4 검증 — gyoungryeok 발화

### 2.1 시나리오
- 힐러 워커 정상 기동 (P0-1 통과 후)
- 격수 PC 가동 → 사냥 시작 → MP 임계 이하로 자연 진입 (또는 의도적 MP 소모)
- MP 회복 → 다시 임계 이하 진입
- **10회 반복**

### 2.2 회차별 회신 양식

각 사이클 (MP 임계 진입 1회) 마다 로그 grep:

```cmd
findstr /C:"[BRAIN] rule fired name=gyoungryeok" /C:"[HANDS] sequence start name=gyoungryeok" /C:"[HANDS] sequence done name=gyoungryeok" /C:"[GYOUNG-DIAG]" C:\oldbaram\dist_dosa\logs\<오늘날짜>\*.log
```

표:

| 사이클 | rule fired | seq start | seq done | GYOUNG-DIAG block reason | 비고 |
|---|---|---|---|---|---|
| 1 | (Y/N) | (Y/N) | (Y/N) | (없음/in_progress_stuck/buff_active_stuck/mp_negative/prev_locked_no_edge) | |
| 2 | | | | | |
| ... | | | | | |
| 10 | | | | | |

### 2.3 합격 기준
- rule fired: **10/10**
- seq start + done 매칭: **10/10**
- buff active 윈도우 동안 중복 fire: **0회**
- `[GYOUNG-DIAG] block reason=buff_active_stuck` 가 buff active 시점이 아닌데 emit: **0회**

### 2.4 실패 시 추가 회수
- 해당 사이클 직전 5초 로그 전체 (facade + worker)
- 해당 시점 buff slot 결과: `findstr /C:"[CD-OCR] slot=buff" C:\oldbaram\dist_dosa\logs\...`
- 해당 시점 hpmp 출력: `findstr /C:"[HPMP-H]" C:\oldbaram\dist_dosa\logs\...`
- snapshot dump 가능하면 그 시점 `buff_gyoungryeok_active`, `mp`, `mp_below_thr_prev` 값

---

## 3. 회신 형식 (복사해서 채워주세요)

```
=== P0-1 결과 ===
파일 동기화: ✅/❌
bind 실패 회차: <목록 또는 "없음">
first State 누락 회차: <목록 또는 "없음">
netstat stop 후 0건: ✅/❌
종합: PASS/FAIL

=== P0-4 결과 ===
fired 누락 사이클: <목록 또는 "없음">
seq done 누락 사이클: <목록 또는 "없음">
중복 fire 회수: <개수>
GYOUNG-DIAG block 사유 분포: <reason: 횟수>
종합: PASS/FAIL

=== 추가 메모 ===
<예상 못한 로그, 새 에러 등>
```

---

## 4. 다음 단계 매핑

| 결과 조합 | 다음 작업 |
|---|---|
| P0-1 PASS + P0-4 PASS | **Branch A**: Stop-Ship 갱신 + P0-2 7개 수정 commit + 계약 테스트 |
| P0-1 FAIL | **Branch B-1**: `RealUdpAdapter.__init__` 예외 삼키기 제거 + worker start fail-fast + stop 후 inventory 로그 |
| P0-4 FAIL | **Branch B-2**: gyoungryeok skip reason 한 줄 진단 추가 + MP OCR 노이즈 vs edge reset 충돌 분석 |
| 양쪽 일부 PASS | 실패 항목만 Branch B 처리 후 PASS 항목 Branch A 진행 |

---

## 5. 검증 미수행 시 본 사이클 작업

- 본 체크리스트 외 신규 P0 변경 commit **금지** (CTO 명령 §0)
- 사용자 회신 받기 전 P0-2 코드 수정 진입 **금지**
- 회신 양식 제공 + 검증 체크리스트 강제만 수행
