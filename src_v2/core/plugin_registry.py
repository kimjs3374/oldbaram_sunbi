"""Plugin Registry — rules / sequences / watchers / learnable params.

Design ref: §2.3 + §7 (self-evolving)
"""
from __future__ import annotations
import copy
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

log = logging.getLogger("src_v2.plugin_registry")


@dataclass
class RuleSpec:
    name: str
    priority: int  # 낮을수록 먼저 평가
    topics: List[str] = field(default_factory=list)
    handler: Optional[Callable] = None
    enabled: bool = True
    description: str = ""


@dataclass
class SequenceSpec:
    name: str
    handler: Callable
    description: str = ""


@dataclass
class WatcherSpec:
    name: str
    cls: Type
    description: str = ""


@dataclass
class LearnableSpec:
    """A target parameter that meta-learning is allowed to tune.

    target_id: dotted key — e.g. "rule.self_heal.hp_thr"
    range: (lo, hi) — clamps applied during set_param
    safety: optional (lo, hi) hard bounds (more conservative than range)
    fitness: name of fitness function in fitness module — e.g. "lower_death_rate"
    """
    target_id: str
    range: Tuple[float, float]
    fitness: str = ""
    safety: Optional[Tuple[float, float]] = None
    description: str = ""
    default: Optional[Any] = None


class PluginRegistry:
    """Class-level singleton registry.

    Decorators register on import; engines query at startup.
    Thread-safe for concurrent register + read.
    """

    _lock = threading.Lock()
    _rules: Dict[str, RuleSpec] = {}
    _sequences: Dict[str, SequenceSpec] = {}
    _watchers: Dict[str, WatcherSpec] = {}
    # learnable params: target_id -> LearnableSpec
    _learnables: Dict[str, LearnableSpec] = {}
    # current param values (copy-on-write swap dict)
    _params: Dict[str, Any] = {}

    # ----- rules -----
    @classmethod
    def register_rule(cls, spec: RuleSpec) -> None:
        with cls._lock:
            if spec.name in cls._rules:
                log.warning("rule '%s' already registered — overwriting", spec.name)
            cls._rules[spec.name] = spec

    @classmethod
    def get_rules(cls) -> List[RuleSpec]:
        with cls._lock:
            return list(cls._rules.values())

    @classmethod
    def get_rule(cls, name: str) -> Optional[RuleSpec]:
        with cls._lock:
            return cls._rules.get(name)

    @classmethod
    def set_rule_enabled(cls, name: str, enabled: bool) -> bool:
        with cls._lock:
            spec = cls._rules.get(name)
            if spec is None:
                return False
            spec.enabled = enabled
            return True

    # ----- sequences -----
    @classmethod
    def register_sequence(cls, name: str, fn: Callable, description: str = "") -> None:
        with cls._lock:
            if name in cls._sequences:
                log.warning("sequence '%s' already registered — overwriting", name)
            cls._sequences[name] = SequenceSpec(name=name, handler=fn, description=description)

    @classmethod
    def get_sequence(cls, name: str) -> Optional[Callable]:
        with cls._lock:
            spec = cls._sequences.get(name)
            return spec.handler if spec else None

    @classmethod
    def list_sequences(cls) -> List[str]:
        with cls._lock:
            return list(cls._sequences.keys())

    # ----- watchers -----
    @classmethod
    def register_watcher(cls, name: str, watcher_cls: Type, description: str = "") -> None:
        with cls._lock:
            if name in cls._watchers:
                log.warning("watcher '%s' already registered — overwriting", name)
            cls._watchers[name] = WatcherSpec(name=name, cls=watcher_cls, description=description)

    @classmethod
    def get_watcher(cls, name: str) -> Optional[Type]:
        with cls._lock:
            spec = cls._watchers.get(name)
            return spec.cls if spec else None

    @classmethod
    def list_watchers(cls) -> List[str]:
        with cls._lock:
            return list(cls._watchers.keys())

    # ----- learnable params -----
    @classmethod
    def register_learnable(cls, spec: LearnableSpec) -> None:
        """Register a target parameter that meta-learning is allowed to tune.

        Idempotent — overwriting same target_id keeps existing value.
        """
        with cls._lock:
            if spec.target_id in cls._learnables:
                log.warning("learnable '%s' already registered — overwriting spec",
                            spec.target_id)
            cls._learnables[spec.target_id] = spec
            # seed param value if not set
            if spec.target_id not in cls._params and spec.default is not None:
                cls._params[spec.target_id] = spec.default

    @classmethod
    def get_learnable(cls, target_id: str) -> Optional[LearnableSpec]:
        with cls._lock:
            return cls._learnables.get(target_id)

    @classmethod
    def list_learnables(cls) -> List[LearnableSpec]:
        with cls._lock:
            return list(cls._learnables.values())

    @classmethod
    def get_param(cls, target_id: str, default: Any = None) -> Any:
        """Lock-free read after dict reference snapshot."""
        params = cls._params  # snapshot ref (rebound on swap)
        return params.get(target_id, default)

    @classmethod
    def set_param(cls, target_id: str, value: Any, *, force: bool = False) -> bool:
        """Set param with copy-on-write swap.

        Returns False if target not registered (unless force=True).
        Range/safety are clamped by hot_apply, not here — registry stays neutral.
        """
        with cls._lock:
            spec = cls._learnables.get(target_id)
            if spec is None and not force:
                log.warning("set_param: '%s' not in learnables (use force=True)",
                            target_id)
                return False
            new_dict = dict(cls._params)
            new_dict[target_id] = value
            cls._params = new_dict  # atomic ref swap
            return True

    @classmethod
    def snapshot_params(cls) -> Dict[str, Any]:
        """Return a copy of current params dict (read-only use)."""
        return dict(cls._params)

    @classmethod
    def restore_params(cls, snapshot: Dict[str, Any]) -> None:
        """Restore from snapshot (used by rollback). Atomic swap."""
        with cls._lock:
            cls._params = dict(snapshot)

    # ----- testing -----
    @classmethod
    def reset(cls) -> None:
        """Test helper. Clears all registrations."""
        with cls._lock:
            cls._rules.clear()
            cls._sequences.clear()
            cls._watchers.clear()
            cls._learnables.clear()
            cls._params.clear()


# ===== Decorators =====

def rule(name: str, priority: int = 100, topics: Optional[List[str]] = None,
         description: str = ""):
    """@rule decorator. Registers a rule handler.

    Handler signature: (snapshot, ctx) -> Optional[CastRequest]
    """
    def deco(fn: Callable):
        PluginRegistry.register_rule(RuleSpec(
            name=name,
            priority=priority,
            topics=list(topics or []),
            handler=fn,
            description=description,
        ))
        return fn
    return deco


def sequence(name: str, description: str = ""):
    """@sequence decorator. Registers an action sequence.

    Handler signature: (ctx: dict) -> None
    """
    def deco(fn: Callable):
        PluginRegistry.register_sequence(name, fn, description=description)
        return fn
    return deco


def watcher(name: str, description: str = ""):
    """@watcher decorator. Registers a watcher class."""
    def deco(cls: Type):
        PluginRegistry.register_watcher(name, cls, description=description)
        return cls
    return deco


def learnable(target_id: str,
              range: Tuple[float, float],
              fitness: str = "",
              safety: Optional[Tuple[float, float]] = None,
              default: Optional[Any] = None,
              description: str = ""):
    """@learnable — declare a parameter as tunable by meta-learner.

    Use as a function decorator that returns the function untouched, OR
    as a top-level call (without wrapping a function) to register-only.

    Example::

        # Function form: registers and seeds default if provided.
        @learnable("rule.self_heal.hp_thr", range=(30, 70), fitness="lower_death_rate", default=50)
        def _seed():
            return 50

        # Or call directly at module top:
        learnable("rule.self_heal.hp_thr", range=(30, 70),
                  fitness="lower_death_rate", default=50)(None)
    """
    spec = LearnableSpec(
        target_id=target_id,
        range=range,
        fitness=fitness,
        safety=safety,
        description=description,
        default=default,
    )

    def deco(fn):
        PluginRegistry.register_learnable(spec)
        return fn

    return deco
