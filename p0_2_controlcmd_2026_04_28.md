# P0-2 — ControlCmd end-to-end 복구 (2026-04-28 Day 1)

## 1. 문제

- 과거 `set_control_handler` 누락 이력 (`attacker_facade.send_control` 2026-04-27 BUG-FIX 주석)
- v2 에 송신/수신/적용 fallback 경로 다중 존재 → 어느 경로로 처리됐는지 추적 불가
- start/stop/follow_on/follow_off 4종 명령 성공률 100% 미검증
- worker active/inactive/idle listener 상태 모두 통과 미검증

## 2. v1 SoR 경로 (단일 책임)

### 2.1 격수 → 힐러 송신
| 단계 | 위치 | 설명 |
|---|---|---|
| UI 클릭 | `src/ui/main_window.py:1188-1196` | `worker.send_control(target, cmd)` 위임 |
| send | `src/workers/attacker_worker.py:216-246` | `ControlCmd(target_idx=-1, cmd, ts_ms)` → `UdpSender.send_to(peer, port, data)` |
| ping | `src/workers/heartbeat.py:144` | 1Hz `ControlCmd(cmd="ping", target=-1)` |

### 2.2 힐러 수신/적용
| 상태 | 수신자 | 적용 |
|---|---|---|
| 워커 active | `UdpReceiver._ctrl_handler` (`src/net/udp_receiver.py:96-101`) → `healer_worker.py:1267-1270` `_on_ctrl(c)` → `apply_remote_control(c.cmd)` | `healer_worker.apply_remote_control` (`workers/healer_worker.py:508`) — armed/follow_only/role 갱신 |
| 워커 idle | `ControlListener` (`workers/control_listener.py`) Qt thread, 같은 port bind | `cmd_received` signal → `main_window` 가 워커 자동 기동 |

핵심: UdpReceiver 와 ControlListener 는 **같은 port 를 서로 양보**. 시점이 정확히 분리되어 race 없음 (워커 stop 시 UdpReceiver close → ControlListener.start; 워커 start 시 그 반대).

## 3. v2 현재 상태 — 경로 다중화 검출

### 3.1 송신 경로 (3개 — 단일화 필요)

#### path A: `_compat_attacker_facade.send_control` (`workers/_compat_attacker_facade.py:327-374`)
- 정상 경로. 내부 fallback 으로 `RealUdpSenderAdapter._sender.send_to` 직접 호출.
- 2026-04-27 BUG-FIX 주석: 이전엔 `sender.send_control()` 위임만 시도해서 `RealUdpSenderAdapter` 에 메서드 없어 항상 False 였음.
- 로그: `[CTRL-SEND] target=... cmd=... ok=...`

#### path B: `main_window_v2._handle_ctrl_button` 직접 socket (`ui/main_window_v2.py:1254-1297`)
- worker 가 attacker class 가 아닐 때 (HealerWorker, 미시작 등) UI 가 raw socket 으로 ControlCmd 직송신.
- **위반**: facade 우회. 동일 로직 두 곳 구현.
- peers 출처도 다름 (cfg vs UI text edit) — 일관성 깨짐.

#### path C: `heartbeat.py:144` (v1 그대로 사용)
- 1Hz `ControlCmd(cmd="ping")`. 격수 IP 학습 + uplink 활성 신호.
- 본 경로는 유지. cmd="ping" 고정이라 control 명령과 분리.

### 3.2 수신 경로 (2개 — 시점 분리, 핸드오프 race 위험)

#### path X: `UdpReceiver._ctrl_handler` (워커 active)
- 등록: `_compat_healer_adapters.py:113-122` `set_control_handler(_on_remote_cmd)`
- emit: `cmd_emit(str(cmd_obj.cmd), int(cmd_obj.target_idx))`
- consumer: `main_window_v2._handle_remote_cmd_active` (`ui/main_window_v2.py:3649-3689`)

#### path Y: `ControlListener` (워커 idle, v1 그대로)
- 등록: `main_window_v2.py:35` `from src.workers.control_listener import ControlListener`
- emit: Qt signal `cmd_received(cmd, target_idx)` + `attacker_seen(ip, port)`
- handoff: `_ctrl_listener` lifecycle (`main_window_v2.py:3640-3647`)
  ```
  _ctrl_listener = None
  time.sleep(0.1)   # ← race window
  ```

### 3.3 적용 경로 (2개 — 한쪽이 dead path)

#### apply A (alive): `main_window_v2._handle_remote_cmd_active` (line 3649-3689)
- 처리 cmd: ping(skip) / stop / start / pause / follow_on / follow_off
- start/pause: `chk_arm.setChecked` + `worker.armed = on`
- stop: `stop_worker()`
- follow: `chk_follow_only.setChecked` + `worker.follow_only = on`

#### apply B (dead?): `_compat_healer_facade.apply_remote_control` (line 706-725)
- v1 1:1 의도였으나 cmd_emit 체인이 UI signal 만 emit → facade.apply_remote_control 호출하는 경로 부재
- **검증 필요**: 코드 grep 으로 호출자 0건이면 dead path. 있으면 apply 가 두 번 일어남(중복).

## 4. 식별된 위반 사례

| 위반 | 위치 | 영향 |
|---|---|---|
| 송신 경로 다중 (facade vs UI direct socket) | `main_window_v2.py:1254-1297` | trace 단절, peers 출처 분기, race |
| ControlListener vs UdpReceiver handoff race | `main_window_v2.py:3640-3647` (sleep 0.1) | port 양도 시점에 ControlCmd drop 가능 |
| apply 경로 중복 가능성 | `_compat_healer_facade.apply_remote_control` 호출 추적 미확인 | 중복 적용 또는 dead code |
| trace_id 부재 | 전체 | 송신→수신→적용 3단계 로그 연결 불가 |
| 수신 fallback 미검증 | (전체) | worker idle 시 명령 처리 검증 0회 |

## 5. 수정 포인트 (코드 변경 안내)

### 5.1 송신 단일화
**원칙**: `attacker_facade.send_control` 만 진입점. UI / heartbeat 외 다른 곳에서 직접 ControlCmd send 금지.

수정:
1. `main_window_v2.py:1254-1297` direct socket fallback 제거
2. worker 미시작 상태에서도 `attacker_facade.send_control` 호출 가능하도록 facade 가 `_adapters` 없을 때 임시 socket 생성/cleanup 자체 처리
3. peers 출처 단일화 — `cfg.net.peers` only (UI text edit override 는 cfg 갱신 후 facade 가 읽도록)

### 5.2 수신/적용 단일화
**원칙**: 적용은 `_compat_healer_facade.apply_remote_control` 한 곳에서. UI 의 `_handle_remote_cmd_active` 는 UI 동기화 (체크박스 갱신) 만 담당.

수정:
1. `_handle_remote_cmd_active` 의 비즈니스 로직(armed/follow/stop)을 facade 로 위임
2. `_compat_healer_adapters._on_remote_cmd` cmd_emit 체인을 `facade.apply_remote_control(cmd)` 호출로 교체. UI 동기화는 facade 의 `remote_control_applied.emit` signal 로 받음

### 5.3 ControlListener / UdpReceiver 핸드오프 race 제거
**원칙**: port 양도 시점에 명시적 동기화. `time.sleep(0.1)` 같은 마법 숫자 제거.

수정:
1. ControlListener.stop() 가 socket close 완료까지 동기 대기 (현재는 close + thread alive 체크 부재)
2. UdpReceiver bind 전에 ControlListener 가 완전히 stopped 됐음을 확인 (별도 `joined()` 같은 명시적 게이트)
3. P0-1 fix 의 `BaseWatcher.stop()` adapter chain 과 동일한 패턴 적용

### 5.4 trace_id 도입 — 신규 표준 로그 포맷

#### trace_id 형식
```
trace_id = "<src_role><src_idx>-<ts_ms>-<rand4>"
예: a0-1714280123456-7f3a (격수, 보낸 ts, 4자리 hex random)
```
- src_role: `a`(attacker) | `h<idx>`(healer idx)
- ts_ms: 송신 시각 epoch ms (`now_ms()`)
- rand4: 4자리 hex (충돌 무시 가능)

#### ControlCmd schema 확장
`src/net/protocol.py:75` ControlCmd dataclass 에 `trace_id: str = ""` 추가. JSON 직렬화 호환(미설정 시 빈 문자열).

#### 3단계 로그 표준 prefix
| 단계 | prefix | 필드 |
|---|---|---|
| 송신 | `[CTRL-SEND]` | `trace=<trace_id> target=<idx>(<tag>) cmd=<cmd> ok=<bool> peers=<list> port=<int>` |
| 수신 | `[CTRL-RECV]` | `trace=<trace_id> from=<ip:port> cmd=<cmd> target=<idx> via=<udp_recv\|control_listener>` |
| 적용 | `[CTRL-APPLY]` | `trace=<trace_id> cmd=<cmd> result=<applied\|skipped\|failed> reason=<...>` |

같은 trace_id 가 3개 prefix 모두 나오면 end-to-end 연결 검증.
누락 단계가 있으면 그 단계가 dead/race.

#### 송신측 wiring
```python
# attacker_facade.send_control
trace_id = f"a{self_idx}-{now_ms()}-{secrets.token_hex(2)}"
c = ControlCmd(target_idx=-1, cmd=cmd, ts_ms=now_ms(), trace_id=trace_id)
data = c.to_bytes()
ok_any = False
for p in peers:
    if underlying.send_to(p, port, data):
        ok_any = True
self._emit_log(
    f"[CTRL-SEND] trace={trace_id} target={target_idx}({tag}) "
    f"cmd={cmd} ok={ok_any} peers={peers} port={port}"
)
```

#### 수신측 wiring
```python
# _compat_healer_adapters._on_remote_cmd
def _on_remote_cmd(cmd_obj):
    tid = getattr(cmd_obj, "trace_id", "") or "no-trace"
    log_cb(
        f"[CTRL-RECV] trace={tid} cmd={cmd_obj.cmd} "
        f"target={cmd_obj.target_idx} via=udp_recv"
    )
    cmd_emit(str(cmd_obj.cmd), int(cmd_obj.target_idx), tid)  # ← tid 전달
```

#### 적용측 wiring
```python
# facade.apply_remote_control(cmd, trace_id="")
def apply_remote_control(self, cmd: str, trace_id: str = "") -> None:
    c = str(cmd or "").lower()
    result = "applied"
    reason = ""
    if c == "ping":
        result, reason = "skipped", "ping"
    elif c == "start":
        self.armed = True
        if not self._running:
            self.start()
    elif c == "stop":
        if self._running:
            self.stop()
        else:
            result, reason = "skipped", "not_running"
    # ...
    self._emit_log(
        f"[CTRL-APPLY] trace={trace_id or 'no-trace'} "
        f"cmd={c} result={result} reason={reason}"
    )
```

## 6. 계약 테스트 초안

`src_v2/tests/test_contract_control_cmd_e2e.py` (TODO):
```python
def test_ctrl_4cmd_end_to_end_trace_match():
    """4종 명령 송신→수신→적용 trace_id 매칭 100%."""
    log = LogCapture()
    facade_a = AttackerFacade(...)  # in-process
    facade_h = HealerFacade(...)
    facade_h.start()
    for cmd in ("start", "follow_on", "follow_off", "stop"):
        ok = facade_a.send_control(-1, cmd)
        assert ok
        wait_for_log(f"[CTRL-RECV] cmd={cmd}", log, timeout=1.0)
        wait_for_log(f"[CTRL-APPLY] cmd={cmd}", log, timeout=1.0)
    sends = log.match(r"\[CTRL-SEND\] trace=(\S+)")
    recvs = log.match(r"\[CTRL-RECV\] trace=(\S+)")
    applies = log.match(r"\[CTRL-APPLY\] trace=(\S+)")
    assert sorted(sends) == sorted(recvs) == sorted(applies)

def test_ctrl_idle_listener_handoff_no_drop():
    """워커 stop → idle listener 시점에 보낸 cmd 가 drop 되지 않음."""
    facade_h.stop()  # → ControlListener active
    ok = facade_a.send_control(-1, "start")
    wait_for_log("[CTRL-RECV] via=control_listener", log, timeout=2.0)
    wait_for_log("[CTRL-APPLY] cmd=start", log, timeout=3.0)
    # ControlListener 가 받고 facade.start() 자동 기동
    assert facade_h._running

def test_ctrl_send_no_dual_path():
    """UI direct socket fallback 제거 후, 송신은 facade.send_control 외 경로 0."""
    # main_window_v2._handle_ctrl_button 호출 시 facade.send_control 만 호출됨을 mock 으로 확인
```

## 7. Day 1 코드 수정 진입 (옵션 — runtime 검증 후 진행)

P0-1 / P0-4 verified-in-runtime 닫히기 전에는 추가 transport 변경 위험. 따라서 Day 1 본 항목은 **분석/포맷 확정까지** 만 commit. 코드 수정은 다음 사이클(Day 2 새벽 또는 verified 후) 에서:

1. ControlCmd schema 에 `trace_id` 필드 추가 (`src/net/protocol.py`) — wire format 호환 (default `""` JSON skip)
2. `attacker_facade.send_control` trace_id 발급 + `[CTRL-SEND]` 표준 로그
3. `_compat_healer_adapters._on_remote_cmd` `[CTRL-RECV]` 로그 + trace_id 포워딩
4. `_compat_healer_facade.apply_remote_control(cmd, trace_id="")` 시그니처 확장 + `[CTRL-APPLY]`
5. `main_window_v2._handle_remote_cmd_active` 를 `facade.apply_remote_control` 위임으로 슬림화
6. `main_window_v2._handle_ctrl_button` direct socket fallback 제거 (facade 단일화)
7. ControlListener handoff race 게이트 (sleep 0.1 제거)

## 8. 완료 기준

- [ ] 4종 명령(start/stop/follow_on/follow_off) trace_id end-to-end 매칭 100%
- [ ] 워커 active/idle listener 양쪽 모드에서 모두 통과
- [ ] direct socket fallback 0개
- [ ] apply 중복 0건 (facade 1곳만)
- [ ] handoff race 시나리오에서 drop 0건

## 9. 남은 리스크

1. **wire format 변경**: `trace_id` 필드 추가는 하위호환 (JSON 누락 OK) 이지만 양 PC 가 동시에 업데이트되어야 trace_id 가 채워짐. 한쪽이 구버전이면 trace 비어있어도 정상 작동해야 함.
2. **ControlListener race 근본 해결**: 현재 같은 port 를 두 컴포넌트가 양도하는 구조 자체가 fragile. 장기적으로 단일 listener (UdpReceiver 가 idle/active 모두 처리) 로 통합 검토.
3. **heartbeat ping 구분**: `ControlCmd(cmd="ping")` 도 `[CTRL-RECV]` 로그를 발생시키면 1Hz × peers 만큼 로그 폭주. ping 은 trace 로그 skip 하거나 별 prefix.
4. **target_idx 라우팅**: 현재 `target_idx=-1` broadcast. 특정 힐러 지정(`target_idx=0/1`) 시 수신측이 자기 idx 와 비교해 무시 — 이 결정도 `[CTRL-RECV] result=ignored_target` 로그로 가시화 필요.

## 10. Day 1 산출물 체크

- [x] 송신/수신/적용 경로 매핑 완료 (§3)
- [x] dead path / fallback 중복 식별 완료 (§4)
- [x] trace_id 포맷 확정 (§5.4)
- [x] 송신/수신/적용 표준 로그 prefix 확정 (§5.4 표)
- [x] 계약 테스트 초안 (§6)
- [x] 수정 포인트 7개 도출 (§7)
- [ ] 코드 수정 — 다음 사이클로 deferred (P0-1/P0-4 runtime verified 후)
