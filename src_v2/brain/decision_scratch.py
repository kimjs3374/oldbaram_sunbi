"""DecisionScratch — 룰/시퀀스/integration_tick 가 공유하는 mutable 상태.

audit 8.1 2단계 (상태 저장소 통합):
이전엔 `worker_state` (dict) 와 `RuleContextBuilder.extras` (dict) 둘로 분리.
- worker_state: 시퀀스가 read/write (예: _seq_rclick_target, _pending_tab_lock_until)
- extras: 룰이 read/write (예: baekho_ready_prev, parlyuk_start_ts)

문제:
- 두 dict 사이 동기화 누락 시 룰 ↔ 시퀀스 통신 깨짐
- 디버깅 시 어느 dict 에 무엇이 있는지 추적 비용

본 모듈: 단일 dict ref 를 공유하도록 명시 API 제공.
healer_worker_v2 가 DecisionScratch 인스턴스 1개 만들고,
ctx_builder.extras 와 worker_state 둘 다 그 인스턴스의 .data 를 가리키게 함.
이후 모든 read/write 가 같은 메모리.

향후 확장 가능 API:
- typed accessor (get_seq_rclick_target() 등)
- audit log (어떤 키가 언제 누가 set 했는지)
"""
from __future__ import annotations

from typing import Any, Dict


class DecisionScratch:
    """단일 mutable bag — 룰/시퀀스/tick 공용.

    .data 가 dict ref. 외부 (RuleContextBuilder.extras / worker_state) 가
    이 ref 를 공유. 외부 코드 변경 없이 ref 통합만으로 단일 source 보장.
    """

    def __init__(self) -> None:
        self.data: Dict[str, Any] = {}

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self.data

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def setdefault(self, key: str, default: Any) -> Any:
        return self.data.setdefault(key, default)

    def pop(self, key: str, default: Any = None) -> Any:
        return self.data.pop(key, default)

    def keys(self):
        return self.data.keys()

    def items(self):
        return self.data.items()

    def values(self):
        return self.data.values()

    def update(self, other: Dict[str, Any]) -> None:
        self.data.update(other)

    def __len__(self) -> int:
        return len(self.data)

    def __repr__(self) -> str:
        return f"DecisionScratch(keys={list(self.data.keys())})"
