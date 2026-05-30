# Day 2 실행 명령서 (2026-04-28)

> 현재 상태:
> - P0-1 UDP bind: fixed-in-code, verified-in-runtime 대기
> - P0-4 gyoungryeok: fixed-in-code, verified-in-runtime 대기
> - P0-2 ControlCmd: 분석/trace 설계 완료, 코드 commit 보류

---

## 0. 다음 지시 — 바로 이렇게 움직인다

이제 우선순위는 명확하다.

### 1단계 — 사용자 런타임 검증 회수
사용자에게 먼저 아래 2개 결과를 반드시 받아온다.

1. **P0-1 검증 결과**
   - stop/start 20회
   - `udp bind 30회 실패` 0회
   - `[UDP-RECV] first State` 20회

2. **P0-4 검증 결과**
   - MP 임계 진입 10회
   - `rule fired name=gyoungryeok`
   - `sequence start/done name=gyoungryeok`

이 두 개가 안 오면, Day 2의 본 작업은 **P0-2 코드 commit이 아니라 검증 압박 및 결과 회수**다.

---

## 1. 분기별 실행 계획

### Branch A — 사용자가 P0-1 / P0-4 성공 로그를 보내온 경우

이 경우 즉시 다음 순서로 간다.

#### A-1. Stop-Ship 대시보드 갱신
- P0-1 `verified-in-runtime = ✅`
- P0-4 `verified-in-runtime = ✅`
- 잔여 blocker `6 → 4`

#### A-2. P0-2 ControlCmd 코드 commit 착수
바로 아래 7개 수정 들어간다.

1. `src/net/protocol.py`
   - `ControlCmd.trace_id: str = ""` 추가

2. `src_v2/workers/_compat_attacker_facade.py`
   - trace_id 발급
   - `[CTRL-SEND]` 표준 로그

3. `src_v2/workers/_compat_healer_adapters.py`
   - `[CTRL-RECV]` 표준 로그
   - trace_id 포워딩

4. `src_v2/workers/_compat_healer_facade.py`
   - `apply_remote_control(cmd, trace_id="")`
   - `[CTRL-APPLY]` 표준 로그

5. `src_v2/ui/main_window_v2.py`
   - `_handle_remote_cmd_active` 비즈니스 로직 제거
   - facade 위임으로 슬림화

6. `src_v2/ui/main_window_v2.py`
   - direct socket fallback 제거

7. `ControlListener` handoff race 제거
   - `sleep(0.1)` 제거
   - 명시적 stopped/bound gate 도입

#### A-3. 계약 테스트 작성
- `test_contract_control_cmd_e2e.py`
- `test_ctrl_idle_listener_handoff_no_drop`
- `test_ctrl_send_no_dual_path`

#### A-4. 런타임 검증
- start / stop / follow_on / follow_off 4종
- active / idle listener 상태 모두
- trace_id 3단계 매칭 100%

---

### Branch B — 사용자가 P0-1 / P0-4 실패 로그를 보내온 경우

이 경우 P0-2 들어가지 말고, 실패한 항목부터 다시 판다.

#### B-1. P0-1 실패면
다음부터 한다.

1. `RealUdpAdapter.__init__` 예외 삼키기 제거
2. bind 실패 시 worker start fail-fast
3. stop 후 잔존 소켓/스레드 inventory 로그 추가
4. 필요 시 `netstat` 캡처 자동화

#### B-2. P0-4 실패면
다음부터 한다.

1. `gyoungryeok.py` skip reason 로그 추가
2. `ctx.in_progress / buff_active / mp / prev / cfg_thr` 한 줄 진단 추가
3. MP OCR 노이즈와 edge reset 충돌 여부 확인

---

### Branch C — 사용자가 아직 검증 결과를 안 보낸 경우

이 경우 당장 새 패치 더 넣지 말고, 아래를 먼저 한다.

1. 검증용 체크리스트/로그 수집 양식 전달
2. 필요한 로그 prefix 명시
3. 결과 수신 전까지 P0-2는 설계/테스트 초안까지만 유지

이유:

> verified-in-runtime 없이 다음 transport 변경까지 밀어 넣으면
> fixed-in-code 더미만 늘어난다.

---

## 2. Day 2 금지사항

다음은 금지다.

1. P0-1/P0-4 검증 없이 P0-2를 본격 commit
2. P1, P2 리팩토링으로 도망
3. 새 문서만 만들고 blocker 감소 0건
4. 성능 측정 없이 YOLO 이슈 방치

---

## 3. Day 2 성공 기준

### 최소 성공
- 사용자 런타임 검증 결과 확보
- 분기 A/B/C 중 하나로 명확히 진입

### 이상적 성공
- P0-1 verified-in-runtime ✅
- P0-4 verified-in-runtime ✅
- Stop-Ship 잔여 6 → 4
- P0-2 코드 수정 착수

### 최고 성공
- P0-2까지 fixed-in-code 진입
- contract test 초안 작성
- trace_id 로그 3단계 동작 확인

---

## 4. 한 줄 명령

**다음 지시는 이거다: 사용자한테서 P0-1/P0-4 런타임 검증 로그를 먼저 받아라. 성공이면 바로 P0-2 commit, 실패면 해당 P0 재수리, 미응답이면 검증 양식부터 강제해라.**