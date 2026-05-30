"""금강불체 룰 — v1 1:1 (skill_blueprints.py:356-366).

v1 SoR: dist_dosa/src/input/skill_blueprints.py:356-366
  SkillSpec(name="금강불체", enabled=False, predicate=lambda _c: False,
            edge_only=True, priority=12)

특징:
  - 기본 OFF (사용자가 cfg에서 geumgang_enabled=True 활성화 시 동작)
  - predicate 항상 False — 룰 자체는 cast 요청 안 함
  - manual request_cast("금강불체") 또는 GUI 버튼으로만 발동

이 룰 모듈은 plugin registration 만 담당 (실제 발동은 외부 트리거).
v2 healer_worker_v2._request_cast_by_name 의 prio_map 에 "금강불체"=45 등록 됨.
"""
from __future__ import annotations
from typing import Optional

from ...core.plugin_registry import rule
from ...core.snapshot import Snapshot
from ...core.types import CastRequest, RuleContext


@rule(
    name="geumgang",
    priority=45,
    topics=[],  # 어떤 이벤트도 자동 트리거 안 함
    description="금강불체 — 수동 트리거 전용 (v1 enabled=False)",
)
def geumgang(snap: Snapshot, ctx: RuleContext) -> Optional[CastRequest]:
    """v1 1:1: predicate 항상 False. 자동 발동 안 함."""
    return None
