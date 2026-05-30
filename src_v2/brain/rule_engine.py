"""Rule engine — subscribes to topics, evaluates rules, pushes CastRequest.

Design ref: §2.5
"""
from __future__ import annotations
import logging
from typing import Callable, Dict, List, Optional

from ..core.event_bus import Event, EventBus
from ..core.plugin_registry import PluginRegistry, RuleSpec
from ..core.snapshot import SnapshotStore
from ..core.types import CastRequest, RuleContext
from .decision import RuleContextBuilder

log = logging.getLogger("src_v2.brain.engine")


class RuleEngine:
    """Listens to eye.* topics, evaluates registered rules per topic in priority order.

    First rule that returns a CastRequest wins (per topic).
    """

    def __init__(self,
                 store: SnapshotStore,
                 bus: EventBus,
                 hands_api,  # HandsAPI
                 ctx_builder: Optional[RuleContextBuilder] = None,
                 log_callback: Optional[Callable[[str], None]] = None) -> None:
        self.store = store
        self.bus = bus
        self.hands = hands_api
        self.ctx_builder = ctx_builder or RuleContextBuilder()
        self._by_topic: Dict[str, List[RuleSpec]] = {}
        self._active = False
        # 진단 로그 — [BRAIN] rule fired ...
        self._log_emit = log_callback if callable(log_callback) else None
        # rule fired 첫 emit 1회씩 (동일 name 중복 emit 방지).
        self._fired_once: set = set()

    def _emit(self, s: str) -> None:
        if self._log_emit:
            try:
                self._log_emit(s)
            except Exception:
                pass

    def reload_rules(self) -> None:
        """Re-index rules from PluginRegistry."""
        self._by_topic.clear()
        for spec in PluginRegistry.get_rules():
            for t in spec.topics:
                self._by_topic.setdefault(t, []).append(spec)
        for t, lst in self._by_topic.items():
            lst.sort(key=lambda s: s.priority)
        log.info("rule_engine indexed %d topics, %d rules total",
                 len(self._by_topic),
                 sum(len(v) for v in self._by_topic.values()))

    def start(self) -> None:
        if self._active:
            return
        self.reload_rules()
        for topic in self._by_topic:
            self.bus.subscribe(topic, self._on_event)
        # also listen to hand.cast_done to mark last_cast time
        self.bus.subscribe("hand.cast_done", self._on_cast_done)
        self._active = True
        log.info("rule_engine start")
        # 진단 — 등록된 토픽/룰 수.
        rule_count = sum(len(v) for v in self._by_topic.values())
        topics = sorted(self._by_topic.keys())
        self._emit(
            f"[BRAIN] rule_engine 시작 topics={topics} rules={rule_count}"
        )

    def _on_event(self, evt: Event) -> None:
        rules = self._by_topic.get(evt.topic, ())
        if not rules:
            return
        snap = self.store.read()
        ctx = self.ctx_builder.build(snap)
        for spec in rules:
            if not spec.enabled:
                continue
            if spec.handler is None:
                continue
            # Don't re-trigger if already in progress
            if spec.name in ctx.in_progress:
                continue
            # audit 8.1 4단계 계약 로그: 평가 시점 cfg enabled 키 진단.
            # 룰별 *_enabled 키 매핑 (문제 추적용 - 사용자가 체크 해제했는데 fire
            # 되거나, 체크했는데 fire 안 되는 케이스).
            try:
                _ek_map = {
                    "baekho": "baekho_enabled", "parlyuk": "parlyuk_enabled",
                    "parhon": "parhon_enabled", "gyoungryeok": "gyoungryeok_enabled",
                    "mujang": "mujang_enabled", "boho": "boho_enabled",
                    "self_heal": "self_heal_enabled",
                    "self_revive": "self_revive_enabled",
                }
                _ek = _ek_map.get(spec.name)
                if _ek and _ek in ctx.cfg and not bool(ctx.cfg.get(_ek, True)):
                    # disabled 인데 룰이 호출됨 — 디버깅용. spec 차단 안 하면 룰
                    # 자체가 cfg 체크 (룰 코드 안에서 disabled 면 None 반환).
                    pass
            except Exception:
                pass
            try:
                req: Optional[CastRequest] = spec.handler(snap, ctx)
            except Exception as e:  # noqa: BLE001
                log.exception("rule %s exception: %s", spec.name, e)
                continue
            if req is not None:
                # 첫 fire 시 진단 로그 (각 룰당 1회).
                if spec.name not in self._fired_once:
                    self._fired_once.add(spec.name)
                    self._emit(
                        f"[BRAIN] rule fired name={spec.name} "
                        f"topic={evt.topic} priority={spec.priority} "
                        f"→ request_cast"
                    )
                self.hands.request_cast(req)
                break  # first matching rule wins per topic

    def _on_cast_done(self, evt: Event) -> None:
        try:
            payload = evt.payload
            req = getattr(payload, "request", None)
            if req is not None:
                self.ctx_builder.mark_cast(req.name)
                # SEQ-RCLICK 룰이 직전 cast 이름으로 게이트.
                self.ctx_builder.extras["last_cast_done_name"] = req.name
                # self_heal_seq 가 저장한 빨탭 좌표 mirror (있으면).
                ws = getattr(self, "worker_state", None)
                if ws is None:
                    # ctx_builder.extras 에서 worker_state 참조 시도.
                    ws = self.ctx_builder.extras.get("_worker_state")
                if ws is not None:
                    tgt = ws.get("_seq_rclick_target")
                    if tgt is not None:
                        self.ctx_builder.extras["last_seq_rclick_target"] = tgt
        except Exception:  # noqa: BLE001
            pass
