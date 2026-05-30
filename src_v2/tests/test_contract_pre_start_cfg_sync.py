"""계약 테스트: facade 가 cfg setter 를 _v2.start() 이전에 호출.

audit 8.1 1단계: 초기화 순서 회귀 차단.
이전 버그: start 후 sync → race 윈도우에 default rule_cfg (전부 enabled=True) 로 fire.
"""
from __future__ import annotations

import inspect


def test_build_and_start_v2_calls_setters_before_start():
    """healer facade _build_and_start_v2 의 source 검사:
    set_skill_enabled / set_parlyuk_offset 등 cfg setter 가 self._v2.start() 보다
    위 (먼저 등장)에 와야 함.
    """
    from src_v2.workers.v1_compat import HealerWorkerV1Facade

    src = inspect.getsource(HealerWorkerV1Facade._build_and_start_v2)
    # 핵심 setter 들 위치.
    pos_set_armed = src.find("self._v2.set_armed(")
    pos_set_skill_enabled = src.find("self._v2.set_skill_enabled(")
    pos_set_parlyuk_offset = src.find("self._v2.set_parlyuk_offset(")
    pos_start = src.find("self._v2.start()")

    assert pos_start > 0, "self._v2.start() 호출 위치 못 찾음"
    assert pos_set_armed > 0 and pos_set_armed < pos_start, (
        "set_armed 가 start 이전에 와야 (audit 5.11)"
    )
    assert pos_set_skill_enabled > 0 and pos_set_skill_enabled < pos_start, (
        "set_skill_enabled 가 start 이전에 와야"
    )
    assert pos_set_parlyuk_offset > 0 and pos_set_parlyuk_offset < pos_start, (
        "set_parlyuk_offset 가 start 이전에 와야"
    )
