"""계약 테스트: DecisionScratch 가 worker_state ↔ ctx_builder.extras 단일화.

audit 8.1 2단계: 두 dict 가 같은 ref 공유 보장.
"""
from __future__ import annotations

from src_v2.brain.decision import RuleContextBuilder
from src_v2.brain.decision_scratch import DecisionScratch
from src_v2.core.snapshot import Snapshot


def test_scratch_data_shared_with_ctx_builder_extras():
    """DecisionScratch.data 가 ctx_builder.extras 와 동일 ref."""
    scratch = DecisionScratch()
    cb = RuleContextBuilder(cfg={}, extras=scratch.data)
    assert cb.extras is scratch.data, "extras 가 ref 공유 안 됨"

    # scratch 갱신이 ctx 에 즉시 보임.
    scratch["test_key"] = "value"
    snap = Snapshot()
    ctx = cb.build(snap)
    assert ctx.extras.get("test_key") == "value"

    # 룰이 ctx.extras 에 박은 값이 scratch 에서도 보임.
    ctx.extras["rule_set"] = 123
    assert scratch.get("rule_set") == 123


def test_scratch_data_shared_with_worker_state_pattern():
    """healer_worker_v2 패턴: worker_state = scratch.data 가 단일 ref."""
    scratch = DecisionScratch()
    worker_state = scratch.data  # healer_worker_v2.__init__ 에서 동일하게 함.
    assert worker_state is scratch.data

    # 시퀀스가 worker_state 에 박은 값이 scratch 에서도 보임.
    worker_state["_seq_rclick_target"] = (100, 200)
    assert scratch.get("_seq_rclick_target") == (100, 200)

    # scratch 갱신이 worker_state 에서도 보임 (반대).
    scratch["last_self_heal_ts"] = 12345.0
    assert worker_state.get("last_self_heal_ts") == 12345.0
