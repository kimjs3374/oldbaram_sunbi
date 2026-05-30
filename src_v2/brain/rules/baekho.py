"""백호의희원 룰 — v1 1:1 (cooldown ready + skill_enabled).

v1 트리거:
  cd_baekho == 0 (OCR 쿨 0) + buff_baekho_active=False + enabled=True
  ARM 안정 1초 (timer 기반).

타이머 기반 ready_gate 는 SkillScheduler 의 _is_ready 가 처리 — 룰은 단순 edge.

ctx.extras key: baekho_ready_prev (bool)
"""
from __future__ import annotations
from typing import Optional

from ...core.plugin_registry import rule
from ...core.snapshot import Snapshot
from ...core.types import CastRequest, RuleContext


@rule(
    name="baekho",
    priority=30,
    topics=["eye.cooldown"],
    description="백호의희원 (cooldown ready edge)",
)
def baekho(snap: Snapshot, ctx: RuleContext) -> Optional[CastRequest]:
    if "baekho" in ctx.in_progress:
        return None
    if not ctx.cfg.get("baekho_enabled", True):
        return None
    if snap.buff_baekho_active:
        return None

    # v1 SoR (SkillSpec.ready): last_cast 기반 cooldown 게이트.
    # 이전엔 ready_prev edge 사용 → 한 번 fire 후 cd OCR 가 cd>0 잡아야 reset.
    # 사용자 환경에서 OCR 가 raw_lines='숫자' 만 잡고 "백호의희원" 텍스트 못 찾음
    # → cd_baekho 영원 -1 → prev=True 영원 → 영원 fire 안 함.
    # 해결: last_cast + cooldown_sec 만 게이트 (cd OCR 무관).
    import time as _t
    cd = int(snap.cd_baekho)
    if cd > 0:
        return None  # cd OCR 가 양수 잡으면 명백히 not ready.
    last_cast = float(ctx.last_cast.get("baekho", 0.0))
    # v1 SkillSpec.cooldown_sec=5 (verify GIVEUP backoff). 백호 실제 쿨 60s+
    # 인데 OCR 가 잡으면 cd>0 분기로 차단됨. OCR 못 잡으면 5s backoff.
    BAEKHO_COOLDOWN_SEC = 5.0
    if last_cast > 0.0 and (_t.monotonic() - last_cast) < BAEKHO_COOLDOWN_SEC:
        return None
    return CastRequest(name="baekho", priority=30)
