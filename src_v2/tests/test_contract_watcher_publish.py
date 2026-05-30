"""계약 테스트: watcher 는 빈 result / 값 무변화 시에도 publish 발화 보장.

audit 8.1 1단계: watcher publish contract 를 코드에 박아 회귀 차단.
이전 버그: cooldown_watcher 가 `if not result: return` 으로 publish skip →
RuleEngine 영원 평가 안 함 → baekho/parlyuk 룰 영구 무반응.
"""
from __future__ import annotations

from src_v2.core.event_bus import EventBus
from src_v2.core.snapshot import SnapshotStore


class _StubAdapter:
    def __init__(self, result):
        self._result = result

    def read(self, frame, origin=(0, 0)):
        return self._result

    def is_available(self):
        return True


def test_cooldown_watcher_publishes_on_empty_result():
    """빈 dict 결과여도 eye.cooldown 발화 — 룰 평가 보장."""
    from src_v2.eyes.cooldown_watcher import CooldownWatcher

    store = SnapshotStore()
    bus = EventBus()
    received = []
    bus.subscribe("eye.cooldown", lambda evt: received.append(getattr(evt, "payload", evt)))

    store.update(last_frame=object())  # any non-None
    w = CooldownWatcher(store, bus, adapter=_StubAdapter({}), slot="cd", poll_sec=999.0)
    w._tick()  # 직접 호출 — thread 안 돌리고

    assert len(received) >= 1, "빈 result 에서도 publish 해야 함 (audit 5.13)"


def test_hpmp_watcher_publishes_every_tick_regardless_of_change():
    """값 변화 무관하게 매 tick publish — 시작 시 임계치 이미 아래 케이스 보장."""
    from src_v2.eyes.hpmp_watcher import HpMpWatcher

    store = SnapshotStore()
    bus = EventBus()
    hp_received = []
    mp_received = []
    bus.subscribe("eye.hp", lambda evt: hp_received.append(getattr(evt, "payload", evt)))
    bus.subscribe("eye.mp", lambda evt: mp_received.append(getattr(evt, "payload", evt)))

    store.update(last_frame=object())
    # 같은 값 (50/50) 반환하는 adapter — 변화 없음.
    class _Hpmp:
        def read(self, frame, origin=(0, 0)):
            return (50, 50, 0, 0, 0, 0)

        def is_available(self):
            return True

    w = HpMpWatcher(store, bus, adapter=_Hpmp(), poll_sec=999.0)
    w._tick()
    w._tick()  # 2회 — 같은 값이지만 매번 publish 해야

    assert len(hp_received) >= 2, "값 무변화여도 매 tick eye.hp publish (audit 5.14)"
    assert len(mp_received) >= 2
