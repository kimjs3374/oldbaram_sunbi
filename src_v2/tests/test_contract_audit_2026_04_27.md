# 계약 테스트 (audit 8.1 1단계)

본 디렉토리의 신규 테스트 — audit 8.1 의 "의미 보존 잠금" 권고:

- `test_contract_watcher_publish.py` — watcher 가 빈 result/값 무변화 시에도 publish 강제 보장 (룰 평가 영구 무반응 재발 방지)
- `test_contract_cfg_setter_propagation.py` — set_skill_enabled / set_parlyuk_offset 등이 룰 평가 ctx 에 즉시 반영 (RuleContextBuilder dict copy 회귀 차단)
- `test_contract_pre_start_cfg_sync.py` — facade._build_and_start_v2 가 cfg setter 를 _v2.start() 이전에 호출 (start 후 sync race 차단)
