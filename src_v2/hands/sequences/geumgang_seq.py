"""금강불체 시퀀스 — v1 1:1 (skill_blueprints.py:356-366).

v1 SoR: dist_dosa/src/input/skill_blueprints.py:356-366
  SkillSpec("금강불체", VK_NUMPAD0, 0.0, predicate=lambda _c: False,
            enabled=False, burst_sec=0.8, retry_max=1, priority=12, edge_only=True)

특징:
  - 기본 OFF (사용자 토글로 활성)
  - 쿨 없음 (cooldown_sec=0.0)
  - predicate 항상 False — manual request_cast 만으로 발동
  - burst_sec=0.8s — burst 80ms 간격 가정 (기본 SKILL_BURST_INTERVAL)
  - blocks_movement=False (이동 병행)

VK: 0x60 (NUMPAD0). v1_defaults.SKILL_VK_GEUMGANG.
"""
from __future__ import annotations
import time

from ...core.plugin_registry import sequence
from ...config import v1_defaults as V1
from ._common import sleep_ms, SLOT_GEUMGANG
from ..key_transport import KeyTransport, send as kt_send


@sequence("geumgang", description="금강불체 — 수동 활성 burst")
def geumgang(ctx: dict) -> None:
    """v1 burst_sec=0.8s @ 0.1s 간격."""
    dispatcher = ctx["_dispatcher"]
    burst_sec = float(ctx.get("burst_sec", V1.SKILL_BURST_SEC_GEUMGANG))
    interval_sec = float(ctx.get(
        "burst_interval_sec",
        V1.SKILL_BURST_INTERVAL_SEC_DEFAULT,
    ))
    t_end = time.time() + burst_sec
    while time.time() < t_end:
        # P0-3: KeyTransport.NUMPAD_LOCKED 직호출. v1 press_normal_vk 동치.
        kt_send(SLOT_GEUMGANG, KeyTransport.NUMPAD_LOCKED,
                dispatcher=dispatcher, hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
        sleep_ms(int(interval_sec * 1000))
