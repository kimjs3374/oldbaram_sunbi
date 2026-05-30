"""파력무참 룰 — v1 1:1 (cooldown ready + offset + ARM 안정).

v1 트리거 (healer_worker.py + skill_blueprints):
  - cd_parlyuk == 0 + parlyuk_offset (UI 보정) + ARM 1초 안정 + enabled
  - 버프 active 인 동안 coord_tol = 1 강제 (worker 가 처리)

룰 입장에선 cooldown ready edge 만 본다. coord_tol 강제는 healer_worker_v2
의 별도 watcher hook 으로 처리됨 (snap.buff_parlyuk_active 변경 → muscle cfg).

ctx.extras key: parlyuk_ready_prev (bool)
"""
from __future__ import annotations
from typing import Optional

from ...core.plugin_registry import rule
from ...core.snapshot import Snapshot
from ...core.types import CastRequest, RuleContext


@rule(
    name="parlyuk",
    priority=30,
    topics=["eye.cooldown"],
    description="파력무참 (cooldown ready edge + offset 적용)",
)
def parlyuk(snap: Snapshot, ctx: RuleContext) -> Optional[CastRequest]:
    if "parlyuk" in ctx.in_progress:
        return None
    if not ctx.cfg.get("parlyuk_enabled", True):
        return None
    if snap.buff_parlyuk_active:
        return None

    # v1 SoR (SkillSpec.ready): last_cast 기반 cooldown + 첫 시전 offset.
    # 이전엔 ready_prev edge 사용 → cd OCR 못 잡으면 영원 fire 안 됨.
    # 해결: last_cast + cooldown_sec 게이트 + 첫 시전 시 start_ts 부터 offset 대기.
    import time as _t
    cd = int(snap.cd_parlyuk)
    offset = float(ctx.cfg.get("parlyuk_offset_sec", 0))
    if cd > 0:
        return None  # cd OCR 양수면 명백히 not ready.
    last_cast = float(ctx.last_cast.get("parlyuk", 0.0))
    PARLYUK_COOLDOWN_SEC = 5.0
    if last_cast > 0.0 and (_t.monotonic() - last_cast) < PARLYUK_COOLDOWN_SEC:
        return None
    # 첫 시전 offset 게이트 (워커 시작 후 N초 지연).
    if last_cast == 0.0 and offset > 0:
        st_key = "parlyuk_start_ts"
        start_ts = float(ctx.extras.get(st_key, 0.0))
        if start_ts <= 0.0:
            ctx.extras[st_key] = _t.monotonic()
            return None
        if (_t.monotonic() - start_ts) < offset:
            return None
    ready_now = True
    prev = False  # ready_prev 사용 안 함 (cooldown 게이트로 대체).

    if not ready_now:
        ctx.extras["parlyuk_ready_prev"] = False
        return None

    if ready_now and not prev:
        ctx.extras["parlyuk_ready_prev"] = True
        return CastRequest(
            name="parlyuk",
            priority=30,
            ctx={"force_coord_tol": 1},
        )
    return None
