"""src_v2.memory — action log + AI hook + outcome verification + anomaly + self-healing.

Phase 7 + 2026-04-25 error-detection module:
  - OutcomeVerifier: 행동 결과 검증 (12 builtin verifiers)
  - AnomalyDetector: 시계열 z-score 이상치
  - SelfHealingLoop: 자동 hot_apply (HotApply 통합)

Design ref: §2.8 + 에러 감지/복구/자가학습 (사용자 요구 2026-04-25)
"""
from .action_log import ActionLog
from .ai_hook import AiHook, NullAiHook
from .outcome_verifier import OutcomeVerifier, OutcomeRecord, BUILTIN_VERIFIERS
from .anomaly_detector import AnomalyDetector
from .self_healing import (
    SelfHealingLoop, HealingPolicy, builtin_policies,
    metric_self_heal_fail_rate, metric_atk_revive_fail_rate,
    metric_overall_fail_rate,
)

__all__ = [
    "ActionLog", "AiHook", "NullAiHook",
    "OutcomeVerifier", "OutcomeRecord", "BUILTIN_VERIFIERS",
    "AnomalyDetector",
    "SelfHealingLoop", "HealingPolicy", "builtin_policies",
    "metric_self_heal_fail_rate", "metric_atk_revive_fail_rate",
    "metric_overall_fail_rate",
]
