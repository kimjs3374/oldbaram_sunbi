"""계약 테스트: cfg setter 가 룰 평가 ctx 에 즉시 반영.

audit 8.1 1단계: RuleContextBuilder 의 dict copy 회귀 차단.
이전 버그: dict(cfg or {}) copy → set_skill_enabled 갱신 무시 → 룰 default 로 평가.
"""
from __future__ import annotations

from src_v2.brain.decision import RuleContextBuilder
from src_v2.core.snapshot import Snapshot


def test_rule_context_builder_shares_cfg_ref():
    """cfg ref 공유 — 외부 갱신이 build 결과 ctx 에 즉시 반영."""
    cfg = {"baekho_enabled": True, "parlyuk_enabled": True}
    cb = RuleContextBuilder(cfg=cfg)

    # 초기 ctx — True
    snap = Snapshot()
    ctx = cb.build(snap)
    assert ctx.cfg.get("baekho_enabled") is True

    # 외부 갱신.
    cfg["baekho_enabled"] = False

    # 새 ctx 빌드 — False 반영되어야 함 (ref 공유).
    ctx2 = cb.build(snap)
    assert ctx2.cfg.get("baekho_enabled") is False, (
        "RuleContextBuilder 가 cfg ref 공유 안 하면 set_skill_enabled 갱신이 룰에 반영 안 됨 (audit 5.15)"
    )


def test_rule_context_builder_extras_persist_across_builds():
    """extras 도 ref 공유 — edge prev 같은 룰 상태가 호출 간 보존."""
    cb = RuleContextBuilder(cfg={})
    snap = Snapshot()
    ctx1 = cb.build(snap)
    ctx1.extras["test_key"] = "value1"
    ctx2 = cb.build(snap)
    assert ctx2.extras.get("test_key") == "value1", (
        "extras dict 가 호출 간 ref 공유돼야 룰 edge prev 보존됨"
    )
