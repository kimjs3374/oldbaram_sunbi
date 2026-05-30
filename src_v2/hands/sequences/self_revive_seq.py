"""자가부활 시퀀스 — v1 1:1.

v1 SoR (skill_blueprints.py:13,224 + healer_worker.py:1024-1036 _hook_self_resurrect_post):
  - 자가부활은 자힐과 같은 SEQ-A 전체 (TAB→HOME→TAB → 토글 OFF → 부활 burst)
  - post 단계: Block-B (ESC) + 메인힐 1.2s burst (HP=1 직후 한 번 더 살림)
  - pending TAB-LOCK 20s 설정 — worker 가 일괄 TAB×2 + 토글 재ON 시전.
"""
from __future__ import annotations
import logging
import time

from ...core.plugin_registry import sequence
from ...config import v1_defaults as V1
from ._common import sleep_ms, tap_vk

log = logging.getLogger("src_v2.hands.seq.self_revive")


@sequence("self_revive", description="자가부활 — SEQ-A + post(블록B + 메인힐 burst)")
def self_revive(ctx: dict) -> None:
    dispatcher = ctx["_dispatcher"]
    cycler = ctx.get("_cycler")
    worker_state = ctx.get("_worker_state") or {}

    # ----- SEQ-A 전체 (v1 target_sequence.py:199-313) -----
    # cycler suspend (재-lock 차단)
    if cycler is not None:
        try:
            cycler.suspend()
        except Exception:
            pass

    key_gap_ms = int(V1.SEQ_A_KEY_GAP_SEC * 1000)

    # 1) self-target: TAB → HOME → TAB
    tap_vk(dispatcher, V1.VK_TAB, hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
    sleep_ms(key_gap_ms)
    tap_vk(dispatcher, V1.VK_HOME, hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
    sleep_ms(key_gap_ms)
    tap_vk(dispatcher, V1.VK_TAB, hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
    sleep_ms(key_gap_ms)

    # 2) 토글 OFF (NumPad scan 직접)
    try:
        from ..numlock_cycle import press_numpad_scan
    except Exception:  # noqa: BLE001
        press_numpad_scan = None  # noqa: N806
    slots = []
    if cycler is not None:
        try:
            slots = list(getattr(cycler, "slots", None) or [])
        except Exception:
            slots = []
    if not slots:
        slots = list(V1.PRIMARY_VKS)
    for vk in slots:
        try:
            if press_numpad_scan is not None:
                press_numpad_scan(int(vk))
            else:
                dispatcher.tap(int(vk), hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
        except Exception:
            pass
    if cycler is not None:
        try:
            cycler._locked.clear()
        except Exception:
            pass
    sleep_ms(key_gap_ms)

    # 3) 부활 burst — NUMPAD6 0.3s @ 0.1s
    t_end = time.time() + V1.SELF_REVIVE_BURST_SEC
    n_rev = 0
    while time.time() < t_end:
        try:
            dispatcher.tap(V1.VK_NUMPAD6, hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
            n_rev += 1
        except Exception:
            pass
        sleep_ms(int(V1.SELF_REVIVE_BURST_INTERVAL_SEC * 1000))
    log.info("[SELF-REVIVE] revive burst x%d", n_rev)

    # ----- post (v1 _hook_self_resurrect_post: Block-B ESC + 메인힐 1.2s burst) -----
    # Block-B = ESC only (2026-04-24 변경).
    tap_vk(dispatcher, V1.VK_ESCAPE, hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
    sleep_ms(key_gap_ms)

    # 메인힐(NUMPAD1) burst 1.2s @ 0.1s
    mh_vk = V1.VK_NUMPAD1
    try:
        skill_vks = (worker_state or {}).get("skill_vks", {}) or {}
        mh_vk = int(skill_vks.get("메인힐", V1.VK_NUMPAD1))
    except Exception:
        mh_vk = V1.VK_NUMPAD1
    t_end = time.time() + 1.2
    n_mh = 0
    while time.time() < t_end:
        try:
            if press_numpad_scan is not None:
                press_numpad_scan(int(mh_vk))
            else:
                dispatcher.tap(int(mh_vk), hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
            n_mh += 1
        except Exception:
            pass
        sleep_ms(int(V1.SELF_REVIVE_BURST_INTERVAL_SEC * 1000))
    log.info("[SELF-REVIVE-POST] mainheal burst x%d vk=%s", n_mh, hex(mh_vk))

    # pending TAB-LOCK 설정 — worker 가 TAB×2 + 토글 재ON 일괄 처리.
    if worker_state is not None:
        worker_state["_pending_tab_lock_until"] = (
            time.time() + V1.PENDING_TAB_LOCK_SEC
        )
        worker_state["last_self_heal_ts"] = time.time()
        log.info(
            "[TAB-LOCK-PEND] %ds — worker 가 red_raw + 맵동기화 후 일괄 시전",
            int(V1.PENDING_TAB_LOCK_SEC),
        )
