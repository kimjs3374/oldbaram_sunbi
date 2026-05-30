"""TAB-LOCK 시퀀스 — v1 1:1 (healer_worker.py:1691-1738).

조건 (worker 가 게이트):
  - now < _pending_tab_lock_until
  - h_map == a_map (같은 맵)
  - manhattan(h, a) <= TAB_LOCK_DIST_THR (=10)
  - now - _last_map_change_ts >= TAB_LOCK_STABILIZE_SEC (=0.5)

시퀀스:
  1) TAB → TAB (격수 빨탭 고정)
  2) PRIMARY_VKS 전체 skill_lock 호출 (cycler.slots 우선)
  3) cycler.resume()
"""
from __future__ import annotations

from ...core.plugin_registry import sequence
from ...config import v1_defaults as V1
from ._common import sleep_ms, tap_vk


@sequence("tab_lock", description="TAB-LOCK pending 일괄 (TAB×2 + 토글 재ON + resume)")
def tab_lock(ctx: dict) -> None:
    dispatcher = ctx["_dispatcher"]
    cycler = ctx.get("_cycler")
    worker_state = ctx.get("_worker_state") or {}

    # (1) TAB → TAB
    tap_vk(dispatcher, V1.VK_TAB, hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
    sleep_ms(int(V1.TAB_LOCK_TAB_GAP_SEC * 1000))
    tap_vk(dispatcher, V1.VK_TAB, hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
    sleep_ms(int(V1.TAB_LOCK_TAB_GAP_SEC * 1000))

    # (2) 토글 재ON: cycler.slots 또는 PRIMARY_VKS — numlock_cycle.skill_lock_vk 직접.
    slots = list(V1.PRIMARY_VKS)
    if cycler is not None:
        try:
            slots = list(getattr(cycler, "slots", slots))
        except Exception:
            pass
    try:
        from ..numlock_cycle import skill_lock_vk as _lock_vk
    except Exception:  # noqa: BLE001
        _lock_vk = None
    locked_set = set()
    for vk in slots:
        try:
            if _lock_vk is not None:
                ok = bool(_lock_vk(int(vk)))
                if ok:
                    locked_set.add(int(vk))
            else:
                dispatcher.tap(int(vk), hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
                locked_set.add(int(vk))
        except Exception:  # noqa: BLE001
            pass

    # cycler internal _locked sync.
    if cycler is not None and locked_set:
        try:
            cycler._locked.clear()
            cycler._locked.update(locked_set)
        except Exception:  # noqa: BLE001
            pass

    # (3) cycler resume.
    if cycler is not None:
        try:
            cycler.resume()
        except Exception:
            pass

    # pending 해제 표시.
    if worker_state is not None:
        worker_state["_pending_tab_lock_until"] = 0.0
