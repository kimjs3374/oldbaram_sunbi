# Day 1 실행 명령서 (2026-04-28)

> 전제: Day 0 산출물 8개와 code-fix 2건은 작성 완료.
> 지금부터는 “문서 작성”보다 **Stop-Ship 6개를 verified-in-runtime로 닫는 것**이 우선이다.

---

## 0. 지금 바로 내리는 다음 지시

선택지는 없다. **Day 1은 아래 순서로 강제 실행**한다.

1. **사용자 환경 반영부터 한다**
   - `D:\oldbaram\dist_dosa\src_v2\eyes\base_watcher.py`
   - `D:\oldbaram\dist_dosa\src_v2\eyes\cooldown_watcher.py`
   를 실제 실행 환경 `C:\oldbaram\dist_dosa\src_v2\eyes\`로 복사

2. **P0-1, P0-4를 문서가 아니라 런타임으로 닫는다**
   - UDP bind / recv 검증
   - gyoungryeok 발화 검증

3. **검증이 끝나면 Stop-Ship 대시보드를 즉시 갱신한다**
   - fixed-in-code → verified-in-runtime 전환 여부 반영

4. **그 다음 바로 P0-2 ControlCmd end-to-end에 들어간다**
   - 이건 다음 blocker다.

---

## 1. Day 1 우선순위

### Priority 1 — P0-1 verified-in-runtime 처리

## 목표
- `udp bind 30회 실패` 0회
- `[UDP-RECV] adapter=None — udp 비활성` 0회
- `[UDP-RECV] first State` 20회 연속 확인

## 실행
1. 패치 파일 2개를 C 드라이브 실행본에 복사
2. 힐러 워커 stop/start 20회 반복
3. 각 회차 로그 보관
4. 실패 시
   - 어느 회차에서
   - bind 실패가 났는지
   - socket 점유 프로세스가 뭔지
   즉시 기록

## 완료 기준
- 20/20 성공
- stop 후 3초 이내 포트 잔존 0건

## 실패 시 바로 할 일
- `RealUdpAdapter.__init__`의 start 예외 삼키기 제거
- bind 실패 시 worker 시작 자체 실패로 승격

---

### Priority 2 — P0-4 verified-in-runtime 처리

## 목표
- MP 임계 이하에서 `gyoungryeok` rule fire 확인
- sequence start/done 10회 반복 확인

## 실행
1. 패치 반영 후 힐러 워커 기동
2. MP를 임계 이하로 의도적으로 내림
3. 로그에서 아래 3개를 반드시 캡처
   - `[BRAIN] rule fired name=gyoungryeok`
   - `[HANDS] sequence start name=gyoungryeok`
   - `[HANDS] sequence done name=gyoungryeok`
4. 10회 반복

## 완료 기준
- 10/10 성공
- buff active 중에는 중복 발화 0회

## 실패 시 바로 할 일
- `gyoungryeok.py` skip reason 로그 추가
- `ctx.in_progress`, `cfg overlay`, `buff_active`, `prev edge`를 한 줄에 찍는 진단 로그 추가

---

### Priority 3 — Stop-Ship 대시보드 갱신

## 목표
- Day 0 문서를 Day 1 검증 결과로 즉시 업데이트

## 반영 항목
- P0-1 verified-in-runtime ✅/❌
- P0-4 verified-in-runtime ✅/❌
- 잔여 blocker 6 → 4 또는 유지
- next proof 갱신

---

### Priority 4 — P0-2 ControlCmd end-to-end 착수

## 목표
- start / stop / follow_on / follow_off 4종 명령의 송신→수신→적용 trace 연결

## Day 1 산출물
1. `p0_2_controlcmd_2026_04_28.md`
2. trace_id 설계
3. 송신/수신/적용 로그 포맷 정의
4. 계약 테스트 초안

## Day 1 완료 기준
- 최소한 원인 분석 + 수정 포인트 + trace 포맷까지는 확정
- 가능하면 코드 수정까지 진입

---

## 2. Day 1에 하면 안 되는 것

다음은 금지다.

1. P1/P2로 도망가기
2. 블루프린트 문구 다듬기만 하면서 runtime 검증 미루기
3. 새 문서만 늘리고 Stop-Ship 상태를 안 줄이기
4. 성능 문제를 “나중에 최적화”로 넘기기

지금은 문서 품질이 아니라 **blocker 개수 감소**가 KPI다.

---

## 3. Day 1 종료 시점에 반드시 남아야 하는 것

### 최소 성공선
1. P0-1 검증 결과
2. P0-4 검증 결과
3. Stop-Ship 대시보드 갱신본
4. P0-2 분석 초안

### 이상적 성공선
1. P0-1 verified-in-runtime ✅
2. P0-4 verified-in-runtime ✅
3. Stop-Ship 잔여 6 → 4
4. P0-2 수정 착수

---

## 4. 한 줄 명령

**다음 지시사항은 간단하다: 오늘은 문서 쓰는 날이 아니라, P0-1과 P0-4를 실제 런타임 검증으로 닫고 Stop-Ship 숫자를 줄이는 날이다. 그 다음 바로 P0-2 ControlCmd로 들어간다.**