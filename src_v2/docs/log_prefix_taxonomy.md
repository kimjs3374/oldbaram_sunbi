# 운영 로그 prefix 분류 (v1_gap_fix_list P1-3 / 2-4)

목적: 기능 로그 vs 계약 로그 분리. grep 한 줄로 사고 트리거 식별.

## CONTRACT (계약 위반/체결)

설정/통신/수신 계약의 경계 사건. **반드시 모니터링.**

| Prefix              | Where                                | When                                          |
|---------------------|--------------------------------------|-----------------------------------------------|
| `[CFG-CONTRACT]`    | `healer_worker_v2.py`                | worker start 시 enabled flags 스냅샷.         |
| `[UPLINK-CONTRACT]` | `_compat_uplink.py`                  | bootstrap peers=[], LEARN-IP, fallback 발생.  |
| `[CD-RECV-MISMATCH]`| `attacker_worker_v2.py`              | resolved_row_idx != reported_idx (1회/IP).    |
| `[CD-RECV-WARN]`    | `ui/main_window_v2.py`               | src_ip 비어있음 → reported_idx fallback (1회).|

## EDGE (룰 트리거 사건)

상태 전이로 룰이 발화한 시점. 빈도 높음.

| Prefix             | Where                       | When                              |
|--------------------|-----------------------------|-----------------------------------|
| `[EDGE]`           | `brain/integration_tick.py` | atk 사망/honma/mujang/boho edge.  |
| `[PARLYUK-TOL]`    | `brain/integration_tick.py` | parlyuk 버프 → coord_tol 강제/복원.|

## OPS (정상 운영)

영역/OCR/입력 운영 관찰. 디버깅/실측용.

| Prefix       | Where                       | When                             |
|--------------|-----------------------------|----------------------------------|
| `[CD-OCR]`   | `eyes/cooldown_watcher.py`  | adapter pending / 첫 read.       |
| `[HPMP-H]`   | `eyes/hpmp_watcher.py`      | adapter 비활성 / 첫 publish 등.  |
| `[CD-RECV]`  | `workers/attacker_worker_v2.py` | 첫 보고 / 10s SNAP.          |
| `[CTRL]`     | `ui/main_window_v2.py`      | ControlCmd 송신 결과.            |

## STATE (publish source state 메타)

P0-2 추가. 룰/UI 가 freshness 분기 가능.

| Topic                | source_state                                    |
|----------------------|-------------------------------------------------|
| `eye.cooldown_state` | unconfigured / empty / observed / rejected     |
| `eye.hpmp_state`     | unconfigured / empty / observed / rejected     |
| `eye.xp_state`       | unconfigured / empty / observed / rejected      |

## grep 빠른 진단

```bash
# 계약 위반만 추출
grep -E "\[(CFG|UPLINK|CD-RECV-MISMATCH)" logs/dosa.log

# 룰 발화 흐름
grep -E "\[(EDGE|PARLYUK-TOL)" logs/dosa.log

# OCR/통신 운영
grep -E "\[(CD-OCR|HPMP-H|CD-RECV|CTRL)" logs/dosa.log
```

## 운영 가드레일

- `[*-CONTRACT]` 는 **빈도가 낮아야 정상**. 분당 5건 이상 등장하면 설정/통신
  계약 자체가 깨지고 있는 것 — 즉시 점검.
- `[CD-RECV-MISMATCH]` 는 다중 힐러 환경에서 healer_idx 오설정 신호.
  peers 정렬과 healer_idx 가 맞는지 확인.
- `[UPLINK-CONTRACT] bootstrap peers=[]` 가 LEARN-IP 없이 5초 이상 지속되면
  격수가 힐러를 못 찾는 상태 — Tailscale/방화벽 점검.
