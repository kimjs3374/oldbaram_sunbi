# Blueprint — Transport (2026-04-28 초안)

> v1 SoR: `dist_dosa/src/net/protocol.py`, `udp_receiver.py`, `udp_sender.py`, `workers/control_listener.py`
> v2: `dist_dosa/src_v2/adapters/udp_adapter.py`, `eyes/udp_watcher.py`, `eyes/cooldown_uplink.py`

## 1. 패킷 인벤토리 (3종)

| 패킷 | 방향 | wire format | 빈도 | v1 sender | v1 receiver |
|---|---|---|---|---|---|
| `State` (ver:5, type:"state") | 격수 → 힐러들 | JSON (protocol.py:23-49) | 30Hz broadcast | `src/app/attacker.py:263-268` (UdpSender) | `src/net/udp_receiver.py:UdpReceiver` |
| `ControlCmd` (ver:5, type:"ctrl") | 격수 → 힐러 (특정/전체) | JSON | event-trigger | `src/workers/attacker_worker.py:229-246` `send_control()` | `src/workers/control_listener.py:79-107` 또는 `UdpReceiver._ctrl_handler` |
| `CooldownReport` (ver:5, type:"cd") | 힐러 → 격수 | JSON (protocol.py:110-155) | 1Hz | `src/workers/heartbeat.py` | `udp_receiver.CooldownReceiver` (격수 PC) |

## 2. 포트 표

| 포트 변수 | 기본값 | bind 측 |
|---|---|---|
| `cfg.net.port` | 51900 (typical 9999) | 힐러 PC (UdpReceiver) — State + ControlCmd 동시 수신 |
| `cfg.net.attacker_recv_port` | 45455 (`protocol.py:19 ATTACKER_RECV_PORT`) | 격수 PC (CooldownReceiver) — CooldownReport 수신 |

## 3. 단일 책임 강제 (P2-3 D)

`ControlCmd` / `State` / `CooldownReport` 의 송수신은 **transport service 1곳에서만** 한다.

위반 사례:
- `facade` / `worker` / `UI` 가 각자 socket fallback 보유 — 금지
- 동적 peer 학습 / row resolve 가 여러 곳 산발 — transport service 책임으로 통합

## 4. 송수신 라이프사이클 — Owner 강제

### 4.1 v1 SoR
- `UdpReceiver.__init__` (line 21) → `bind` 즉시 (실패 시 raise)
- `UdpReceiver.start()` (line 50) → recv thread 기동
- `UdpReceiver.stop()` (line 107-112) → `_running=False` + `_sock.close()`
- 동일 process 내 중복 bind 금지 (SO_REUSEADDR 금지 — `udp_receiver.py:21-25` 주석)

### 4.2 v2 owner 표

| 단계 | owner | 호출 시점 |
|---|---|---|
| 생성 | `RealUdpAdapter.__init__` (`adapters/udp_adapter.py:62`) | `build_healer_adapters` (워커 start) |
| start | `r.start()` (`udp_adapter.py:67`) | 생성 직후 |
| 사용 | `UdpWatcher._tick` `adapter.recv()` | 폴링 |
| **stop (현재 누락)** | `RealUdpAdapter.stop()` (`udp_adapter.py:108`) | **워커 stop 시 호출되어야 함 — 현재 BaseWatcher.stop() 이 chain 안 함 (P0-1)** |

### 4.3 stop chain 강제 (P0-1 수정 후)

```
HealerWorkerV2.stop()
  └─ for w in (udp, ...): w.stop(timeout)
       └─ BaseWatcher.stop() 가 self.adapter.stop() (있으면) 호출 ← 추가
            └─ RealUdpAdapter.stop()
                 └─ UdpReceiver.stop()
                      └─ _running=False + _sock.close()
```

위반 결과(현재 v1_compat 경로): 재기동 시 동일 process 의 이전 UdpReceiver 가 socket 보유 중 → bind 30회 실패 → `out["udp"]=None` → `[UDP-RECV] adapter=None — udp 비활성`.

## 5. State 패킷 schema (v5)

`src/net/protocol.py:23-49`:
- `seq: int` — 단조 증가, stale/wrap 검출
- `ts_ms: int` — 격수측 epoch ms
- `map_name: str` — canonical map (knownmaps.txt)
- `coord_valid: bool`, `x: int`, `y: int`
- `last_dir: str` — `U/D/L/R/-`
- `map_seq: int` — 맵 변경 카운트
- `hp_pct / mp_pct: int` (-1=미관측)
- `red_tab: bool`, `map_change_pending: bool`
- buff/debuff 카운트다운: `buff_mujang_sec / buff_boho_sec / debuff_honmasul_sec`

## 6. ControlCmd schema

- `cmd: str` — `start / stop / follow_on / follow_off / armed_on / armed_off / role_swap` 등
- `target_idx: int` — `-1`=broadcast, 0~N=특정 힐러
- `ts_ms: int`

전 명령 4종(start/stop/follow_on/follow_off) 성공률 100% (P0-2 완료 기준).

trace id 강제(P0-2 지시): 송신→수신→적용 3단계 로그가 동일 trace_id 로 연결.

## 7. CooldownReport schema

`src/net/protocol.py:110-155`:
- `src_idx: int` — 힐러 row idx
- `cd_parlyuk / cd_baekho / cd_parhon / cd_revive: int`
- `armed: bool`, `hp_pct / mp_pct: int`
- `nickname: str`
- buff active flags (격수 HUD overlay 용)

## 8. Peer / Row Resolution (P1-4)

### 8.1 v1 동작
- 힐러 → 격수: `recvfrom` 으로 src_addr 자동 획득 (`udp_receiver.py:84` `_last_src=addr`)
- 격수 → 힐러들: cfg.net.peers 리스트로 broadcast unicast
- CooldownReport: `src_idx` 기본, IP fallback (`attacker_worker.py:282-289`)

### 8.2 P1-4 강화
`resolved_row_idx` 도입:
- `reported_idx` (CooldownReport.src_idx) 는 참고값
- 실제 매핑은 `resolved_row_idx = resolve_by(src_addr, peers, reported_idx)`
- peers reorder / wrong idx / same subnet 환경 테스트 통과 강제

## 9. 위반 사례 (현재 발견)

| 위반 | 위치 | 영향 |
|---|---|---|
| BaseWatcher.stop 이 adapter.stop 미체인 | `eyes/base_watcher.py:69-72` | UDP socket 누수, bind 30회 실패 (P0-1) |
| `RealUdpAdapter.__init__` 의 `r.start()` 가 try/except pass | `adapters/udp_adapter.py:66-69` | start 실패가 침묵 — 진단 어려움 |
| ControlCmd send 경로 다중 (facade fallback) | (P0-2 지시 항목) | trace 단절 |

## 10. 다음 단계

- [ ] `BaseWatcher.stop()` 에 adapter.stop 체인 추가 (P0-1 fix 문서 참조)
- [ ] `RealUdpAdapter.__init__` start 실패 시 raise (silent pass 금지)
- [ ] ControlCmd transport service 단일화
- [ ] 20회 stop/start loop 계약 테스트 — bind 실패 0회
- [ ] resolved_row_idx 도입 후 다중 힐러 테스트
