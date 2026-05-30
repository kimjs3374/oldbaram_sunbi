# Stop-Ship Dashboard (2026-04-28 ~ 2026-05-01)

> CTO 지시(`v1_v2_sync_cto_workorder_2026_04_28.md` §P0-0).
> 본 표의 모든 항목이 `verified-in-runtime` 으로 닫히기 전까지 배포 금지.

## 일일 요약 (Day 2 — 2026-05-01 런타임 검증 결과 반영)

| 메트릭 | 수치 |
|---|---|
| 총 차단 항목 | 8 (신규 2 추가) |
| fixed-in-code 완료 | 5 |
| verified-in-runtime 완료 | 2 (P0-1, P0-4) |
| 신규 blocker 추가 | 2 (esc_recover 미등록, YOLO 과부하) |
| 잔여 blocker | 6 |

## 차단 항목 표

### P0-1: UDP bind 재기동 실패
- **현 상태**: 힐러1.txt 2026-05-01 런타임 검증 — `udp bind 실패` 0건 + `[UDP-RECV] first State` 5회 확인
- **fixed-in-code**: ✅ 2026-04-28 (`base_watcher.py` adapter.stop chain)
- **verified-in-runtime**: ✅ 2026-05-01

### P0-2: ControlCmd end-to-end 100% 미검증
- **현 상태**: 분석 완료, 코드 commit 대기 (P0-1/P0-4 verified 조건 충족 → commit 가능)
- **next proof**: trace_id 3단계 매칭 100% + start/stop/follow_on/follow_off 4종 성공
- **fixed-in-code**: ⚠️ 분석/포맷 확정, 7개 수정 포인트 도출. commit 착수 가능
- **verified-in-runtime**: ❌

### P0-3: watcher publish contract 미고정
- **현 상태**: 부분 완료 (publish 강제됨, 계약 테스트 부재)
- **fixed-in-code**: ⚠️
- **verified-in-runtime**: ❌

### P0-4: gyoungryeok 미발화
- **현 상태**: 힐러1.txt 2026-05-01 — `rule fired name=gyoungryeok` + `sequence done` 3회 확인 (thr=90% 기준)
- **root cause**: `@rule` 데코레이터가 `_diag_once` 에 잘못 위치 → 2026-05-01 수정 완료
- **fixed-in-code**: ✅ 2026-05-01 (`gyoungryeok.py` 데코레이터 이동)
- **verified-in-runtime**: ✅ 2026-05-01

### P0-5: esc_recover sequence 미등록 ← 신규 blocker
- **현 상태**: `[HANDS] sequence MISSING name=esc_recover` 다발 — recovery 경로 실제로 비어있음
- **재현 조건**: self_heal no_effect 발생 시 → recovery → esc_recover 호출 → MISSING → cycle suspended → 이후 rule 평가 전체 정지
- **root cause**: `esc_recover_seq.py` 파일 자체 누락. `__init__.py` 미등록
- **owner**: Muscle Owner
- **fixed-in-code**: ✅ 2026-05-01 (파일 생성 + `__init__.py` 등록)
- **verified-in-runtime**: ❌

### P0-6: YOLO latency 과부하 ← 신규 blocker
- **현 상태**: 힐러1.txt 341개 샘플 실측
  - P50=99ms / P95=886ms / P99=996ms / max=1817ms
  - 스파이크 원인: XP-OCR(PaddleOCR)가 GPU 공유 → YOLO 큐 경합
- **root cause**: `xp_ocr.py` TextRecognition GPU 기본값. `cooldown_ocr.py`/`map_ocr.py`는 CPU 강제되어 있으나 `xp_ocr.py`만 누락
- **owner**: Vision Owner
- **fixed-in-code**: ✅ 2026-05-01 (`xp_ocr.py` CPU 강제 추가)
- **verified-in-runtime**: ❌
- **목표**: P95 ≤ 200ms / P99 ≤ 400ms

### P0-7: CD/Buff OCR miss 다발
- **현 상태**: `CD-OCR-MISS=24` / raw_lines `EOON`, `으일부서`, `회` 등 깨짐 반복
- **재현 조건**: 쿨다운/버프 OCR 판단 불안정 → 스킬 발화 타이밍 오판 가능
- **owner**: Vision Owner
- **fixed-in-code**: ❌
- **verified-in-runtime**: ❌

### P0-8: MP OCR 노이즈
- **현 상태**: `HPMP-REJECT=9` / MP 0% ↔ 90%대 급변 다수. HP reject 방어는 있으나 MP 동등 수준 미흡
- **owner**: Vision Owner
- **fixed-in-code**: ❌
- **verified-in-runtime**: ❌

## fixed-in-code vs verified-in-runtime 추적표

| 항목 | fixed-in-code | verified-in-runtime | 비고 |
|---|---|---|---|
| P0-1 UDP bind | ✅ 2026-04-28 | ✅ 2026-05-01 | 힐러1.txt 실증 |
| P0-2 ControlCmd | ⚠️ 분석 완료 | ❌ | P0-1/P0-4 verified → commit 가능 |
| P0-3 publish contract | ⚠️ | ❌ | 계약 테스트 부재 |
| P0-4 gyoungryeok | ✅ 2026-05-01 | ✅ 2026-05-01 | 힐러1.txt 실증 |
| P0-5 esc_recover missing | ✅ 2026-05-01 | ❌ | 파일 생성 완료, 재기동 후 검증 필요 |
| P0-6 YOLO latency | ✅ 2026-05-01 | ❌ | xp_ocr CPU 강제. P95 목표 ≤200ms |
| P0-7 CD/Buff OCR miss | ❌ | ❌ | 분석 필요 |
| P0-8 MP OCR 노이즈 | ❌ | ❌ | HP 동등 수준 reject 정책 필요 |

## 복사 필요 파일 (D → C:\oldbaram)

| 파일 | 변경 내용 |
|---|---|
| `src_v2/brain/rules/gyoungryeok.py` | @rule 데코레이터 위치 수정 |
| `src_v2/hands/sequences/esc_recover_seq.py` | 신규 생성 |
| `src_v2/hands/sequences/__init__.py` | esc_recover_seq import 추가 |
| `src/vision/xp_ocr.py` | TextRecognition CPU 강제 |
