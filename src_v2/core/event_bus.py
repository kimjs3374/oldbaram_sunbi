"""Event Bus — lock-free pub/sub.

Design ref: §2.1
"""
from __future__ import annotations
import collections
import threading
import time
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("src_v2.event_bus")


@dataclass(frozen=True)
class Event:
    topic: str
    payload: Any
    ts: float


HandlerT = Callable[[Event], None]


class EventBus:
    """Synchronous in-process pub/sub.

    - subscribe/publish 시 짧은 lock으로 핸들러 리스트 race 방지.
    - 핸들러 호출은 lock 밖 (다른 publish 차단 안 함).
    - 핸들러 예외는 격리 — 다른 핸들러에 영향 없음.
    - publish 자체는 1us 이하 목표 (핸들러 시간 제외).
    """

    def __init__(self) -> None:
        self._subs: Dict[str, List[HandlerT]] = collections.defaultdict(list)
        self._lock = threading.Lock()
        self._publish_count: int = 0
        self._handler_err_count: int = 0

    def subscribe(self, topic: str, handler: HandlerT) -> None:
        if not callable(handler):
            raise TypeError("handler must be callable")
        with self._lock:
            self._subs[topic].append(handler)

    def unsubscribe(self, topic: str, handler: HandlerT) -> bool:
        with self._lock:
            lst = self._subs.get(topic)
            if not lst:
                return False
            try:
                lst.remove(handler)
                return True
            except ValueError:
                return False

    def publish(self, topic: str, payload: Any = None) -> None:
        # 핸들러 스냅샷
        with self._lock:
            handlers = list(self._subs.get(topic, ()))
            self._publish_count += 1
        if not handlers:
            return
        evt = Event(topic, payload, time.monotonic())
        for h in handlers:
            try:
                h(evt)
            except Exception as e:  # noqa: BLE001
                self._handler_err_count += 1
                log.exception("handler error topic=%s err=%s", topic, e)

    def topics(self) -> List[str]:
        with self._lock:
            return list(self._subs.keys())

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "topics": len(self._subs),
                "subs_total": sum(len(v) for v in self._subs.values()),
                "publish_count": self._publish_count,
                "handler_err_count": self._handler_err_count,
            }

    def clear(self) -> None:
        """테스트용 — 모든 구독 제거."""
        with self._lock:
            self._subs.clear()
