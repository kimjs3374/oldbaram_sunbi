"""SelfHealingLoop — meta-learner 확장: outcome/anomaly 패턴 → 자동 hot_apply.

action_log 분석 → 자동 hot-apply rule:
  - 자힐 fail 비율 > 10% → self_heal_hp_thr 5%↓
  - 재시도 성공률 측정 → optimal retry_count 학습 (정보만 emit)
  - 회귀 5분 fitness 비교 → 자동 롤백 (HotApply.maybe_rollback 활용)
  - 모든 변경 evolution_log.jsonl 추가

기존 learning/ (alphago + meta_learner + hot_apply) 와 통합:
  - PluginRegistry 의 LearnableSpec 을 사용
  - HotApply 객체 재사용 (rollback window)
  - FitnessRegistry 의 builtin fitness 평가
"""
from __future__ import annotations
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ..core.plugin_registry import PluginRegistry, LearnableSpec
from ..core.types import ActionRecord
from .action_log import ActionLog
from ..learning.hot_apply import HotApply, RollbackToken
from ..learning.fitness import FitnessRegistry

log = logging.getLogger("src_v2.memory.self_healing")


@dataclass
class HealingPolicy:
    """자가 치유 정책 — 어떤 메트릭이 어느 임계 넘으면 어떤 param 을 어떻게 조정할지."""
    name: str
    target_id: str  # PluginRegistry.set_param key
    metric_fn: Callable[[List[ActionRecord]], float]
    threshold: float
    direction: int  # -1: param 감소, +1: 증가
    step: float
    cooldown_sec: float = 600.0


# ---------- builtin metric fns ----------

def metric_self_heal_fail_rate(recs: List[ActionRecord]) -> float:
    """recent self_heal records 의 fail/no_effect/timeout 비율."""
    self_heal_recs = [r for r in recs if r.action == "self_heal"]
    if not self_heal_recs:
        return 0.0
    bad = sum(1 for r in self_heal_recs if r.result not in ("ok", "skipped"))
    return bad / len(self_heal_recs)


def metric_atk_revive_fail_rate(recs: List[ActionRecord]) -> float:
    revs = [r for r in recs if r.action in ("attacker_revive", "격수부활")]
    if not revs:
        return 0.0
    bad = sum(1 for r in revs if r.result not in ("ok", "skipped"))
    return bad / len(revs)


def metric_overall_fail_rate(recs: List[ActionRecord]) -> float:
    if not recs:
        return 0.0
    bad = sum(1 for r in recs if r.result not in ("ok", "skipped"))
    return bad / len(recs)


# ---------- builtin policies ----------

def builtin_policies() -> List[HealingPolicy]:
    return [
        HealingPolicy(
            name="self_heal_fail_high",
            target_id="rule.self_heal.hp_thr",
            metric_fn=metric_self_heal_fail_rate,
            threshold=0.10,  # 10%
            direction=-1,    # hp_thr 낮춤 → 더 빨리 자힐
            step=5.0,
            cooldown_sec=600.0,
        ),
        HealingPolicy(
            name="atk_revive_fail_high",
            target_id="rule.atk_revive.retry_count",
            metric_fn=metric_atk_revive_fail_rate,
            threshold=0.20,  # 20%
            direction=+1,
            step=1.0,
            cooldown_sec=900.0,
        ),
    ]


class SelfHealingLoop:
    """주기적 분석 + 자가 치유 적용.

    poll_sec 마다:
      1. action_log.recent(window) 수집
      2. 각 policy 의 metric_fn 평가
      3. threshold 초과 → PluginRegistry.set_param + cooldown 등록
      4. 보류 token 의 maybe_rollback 시도
      5. evolution_log 에 행 추가
    """

    def __init__(self,
                 action_log: ActionLog,
                 fitness: Optional[FitnessRegistry] = None,
                 hot_apply: Optional[HotApply] = None,
                 policies: Optional[List[HealingPolicy]] = None,
                 poll_sec: float = 300.0,
                 window_records: int = 200,
                 evolution_log_path: Optional[str] = None,
                 enabled: bool = False) -> None:
        self.action_log = action_log
        self.fitness = fitness or FitnessRegistry()
        self.hot_apply = hot_apply or HotApply(self.fitness)
        self.policies = policies or builtin_policies()
        self.poll_sec = float(poll_sec)
        self.window_records = int(window_records)
        self.enabled = enabled
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_apply: Dict[str, float] = {}
        self._counts: Dict[str, int] = {"applied": 0, "rolled_back": 0, "checks": 0}
        # evolution log
        self._evo_path = evolution_log_path
        self._evo_fp = None
        if self._evo_path:
            try:
                os.makedirs(os.path.dirname(self._evo_path) or ".", exist_ok=True)
                self._evo_fp = open(self._evo_path, "a", encoding="utf-8")
            except Exception:
                log.exception("evolution_log open fail")

    def start(self) -> None:
        if not self.enabled:
            log.info("SelfHealingLoop disabled — not starting")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="self_healing_loop", daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        if self._evo_fp:
            try:
                self._evo_fp.close()
            except Exception:
                pass

    def stats(self) -> Dict[str, Any]:
        return dict(self._counts, policies=len(self.policies),
                    pending_tokens=len(self.hot_apply.pending_tokens()))

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_sec):
            try:
                self.tick()
            except Exception:
                log.exception("self_healing tick fail")

    def tick(self) -> None:
        """공개 — 테스트에서 동기 호출 가능."""
        self._counts["checks"] += 1
        recs = self.action_log.recent(self.window_records)
        # 1) 정책 평가 + 적용
        for policy in self.policies:
            try:
                value = policy.metric_fn(recs)
            except Exception:
                log.exception("policy metric fail name=%s", policy.name)
                continue
            if value < policy.threshold:
                continue
            now = time.monotonic()
            last = self._last_apply.get(policy.name, 0.0)
            if now - last < policy.cooldown_sec:
                continue
            self._apply_policy(policy, value, recs)
            self._last_apply[policy.name] = now
        # 2) 회귀 롤백 검사
        for token in list(self.hot_apply.pending_tokens()):
            try:
                if self.hot_apply.maybe_rollback(token, self.action_log.all()):
                    self._counts["rolled_back"] += 1
                    self._evo_emit({
                        "ts": time.monotonic(),
                        "kind": "rollback",
                        "target_id": token.target_id,
                        "from": token.new_value,
                        "to": token.prev_value,
                    })
            except Exception:
                log.exception("rollback check fail token=%s", token.target_id)

    def _apply_policy(self, policy: HealingPolicy, metric_value: float,
                      recs: List[ActionRecord]) -> None:
        # LearnableSpec 가 등록돼있어야 hot_apply 가 작동. 없으면 직접 set_param.
        spec = PluginRegistry.get_learnable(policy.target_id) if hasattr(PluginRegistry, "get_learnable") else None
        prev = PluginRegistry.get_param(policy.target_id, default=None) if hasattr(PluginRegistry, "get_param") else None
        if prev is None:
            log.warning("self_healing: no current param for %s — skipping", policy.target_id)
            return
        try:
            new_val = float(prev) + policy.direction * policy.step
        except (TypeError, ValueError):
            log.warning("self_healing: param %s not numeric (%r) — skip", policy.target_id, prev)
            return
        applied: Optional[RollbackToken] = None
        if spec is not None:
            try:
                applied = self.hot_apply.apply(spec, new_val, recs)
            except Exception:
                log.exception("hot_apply.apply fail spec=%s", policy.target_id)
        if applied is None:
            # spec 없거나 hot_apply 실패 — 직접 set (force=True 로 spec 미등록도 허용)
            try:
                PluginRegistry.set_param(policy.target_id, new_val, force=True)
            except Exception:
                log.exception("set_param fallback fail target=%s", policy.target_id)
                return
        self._counts["applied"] += 1
        self._evo_emit({
            "ts": time.monotonic(),
            "kind": "apply",
            "policy": policy.name,
            "target_id": policy.target_id,
            "metric": float(metric_value),
            "threshold": float(policy.threshold),
            "from": prev,
            "to": new_val,
        })
        log.info("self_healing apply: %s metric=%.3f thr=%.3f %s: %r → %r",
                 policy.name, metric_value, policy.threshold,
                 policy.target_id, prev, new_val)

    def _evo_emit(self, e: Dict[str, Any]) -> None:
        if not self._evo_fp:
            return
        try:
            self._evo_fp.write(json.dumps(e, default=str, ensure_ascii=False) + "\n")
            self._evo_fp.flush()
        except Exception:
            log.exception("evolution_log write fail")
