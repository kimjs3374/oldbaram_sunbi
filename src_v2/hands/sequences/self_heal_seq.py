"""Self-heal sequence — 1:1 ported from v1 (dist_dosa/src/input/target_sequence.py).

v1 SoR:
  - target_sequence.py:199-313  block_a_self_target
      1) TAB → HOME → TAB (self-target, sleep_s=0.1 사이)
      2) cycler.suspend() + slots 전체 press_numpad_scan → 토글 OFF
      3) NUMPAD6 burst 0.3s @ 0.1s 간격 (부활)
      4) NUMPAD1 burst 0.5s @ 0.1s 간격 (자힐)
  - target_sequence.py:316-368  block_b_return_to_attacker (2026-04-24 변경)
      1) ESC 1회만 (TAB×2/재ON 폐기 — worker 가 red_raw + 맵동기화 후 처리)
  - healer_worker.py:1018  _pending_tab_lock_until = time.time() + 20.0
  - healer_worker.py:920-983  _seq_rclick_target 저장 (자힐 진입 시점 빨탭 좌표)

burst_count 가 ctx 로 주입돼도 v1 시간 기반 burst (0.3s/0.5s) 가 SoR.
ctx.burst_count 가 v1 환산값(5회) 으로 들어오면 동치.
"""
from __future__ import annotations
import logging
import time

from ...core.plugin_registry import sequence
from ...config import v1_defaults as V1
from ._common import sleep_ms, tap_vk

log = logging.getLogger("src_v2.hands.seq.self_heal")


@sequence("self_heal", description="자힐 — v1 SEQ-A burst + SEQ-B (ESC only)")
def self_heal(ctx: dict) -> None:
    """v1 1:1 자힐 시퀀스.

    ctx keys:
      _dispatcher: InputDispatcher (필수)
      _cycler: NumlockCycler (suspend/_locked.clear 호출용, optional)
      _worker_state: dict — _seq_rclick_target, _pending_tab_lock_until 기록용
      _yolo_red_det: 자힐 진입 시점 YOLO 빨탭 detection (cx, cy 절대 화면 좌표)
      burst_count, burst_gap_ms, enable_block_b, key_gap_ms
    """
    dispatcher = ctx["_dispatcher"]
    cycler = ctx.get("_cycler")
    worker_state = ctx.get("_worker_state") or {}

    burst_count = int(ctx.get(
        "burst_count",
        int(V1.SEQ_A_HEAL_BURST_SEC / V1.SEQ_A_BURST_INTERVAL_SEC),
    ))
    burst_gap_ms = int(ctx.get(
        "burst_gap_ms",
        int(V1.SEQ_A_BURST_INTERVAL_SEC * 1000),
    ))
    key_gap_ms = int(ctx.get(
        "key_gap_ms",
        int(V1.SEQ_A_KEY_GAP_SEC * 1000),
    ))
    enable_block_b = bool(ctx.get("enable_block_b", True))

    # ------ 사전: SEQ-RCLICK 타겟 저장 (v1 healer_worker.py:920-983) ------
    yolo_det = ctx.get("_yolo_red_det")
    rclick_target = None
    if yolo_det is not None and worker_state is not None:
        try:
            ax = int(getattr(yolo_det, "cx", 0))
            ay = int(getattr(yolo_det, "cy", 0))
            worker_state["_seq_rclick_target"] = (ax, ay)
            rclick_target = (ax, ay)
            log.info("[SEQ-RCLICK-TARGET] saved (%d,%d)", ax, ay)
        except Exception:
            worker_state["_seq_rclick_target"] = None

    # 2026-05-05 Cycle 2 (Task 11) — SEQ-RCLICK inline spawn:
    # v1 SoR (healer_worker.py 메인루프 is_movement_locked + 0.5s 간격 우클릭):
    # 자힐 burst 진행 동안 격수 좌표에 우클릭 sub-loop. 격수 자동 회복 보조.
    # 이전엔 rule_engine wiring(cast_done → seq_rclick rule fire) 만 있어
    # self_heal sequence 끝난 후에야 우클릭 시작 → v1 대비 타이밍 차이.
    # → sequence 시작 시점에 sub-thread 즉시 spawn (rule wiring 은 보조 유지).
    if rclick_target is not None:
        try:
            from .seq_rclick_seq import seq_rclick as _spawn_rclick
            _spawn_rclick({
                "_dispatcher": dispatcher,
                "red_tab_pos": rclick_target,
                "duration_ms": 1500,  # v1 patch 메모리 4-25: 자힐 burst 동안.
                "interval_ms": 500,
            })
            log.info("[SEQ-RCLICK-INLINE] sub-thread spawned target=(%d,%d)",
                     rclick_target[0], rclick_target[1])
        except Exception:
            log.exception("[SEQ-RCLICK-INLINE] spawn fail")

    # ------ Block A (v1 target_sequence.py:199-313) ------
    # cycler suspend (재-lock 차단) — v1 244-249
    if cycler is not None:
        try:
            cycler.suspend()
        except Exception:
            pass

    # 1) self-target: TAB → HOME → TAB
    log.debug("[SEQ-A] 1) TAB → HOME → TAB")
    tap_vk(dispatcher, V1.VK_TAB, hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
    sleep_ms(key_gap_ms)
    tap_vk(dispatcher, V1.VK_HOME, hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
    sleep_ms(key_gap_ms)
    tap_vk(dispatcher, V1.VK_TAB, hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
    sleep_ms(key_gap_ms)

    # 2) 토글 OFF — v1 1:1: NumPad scan 직접 송신 (press_numpad_scan).
    # cycler.slots 가 동적이므로 우선 cycler.slots, 없으면 V1.PRIMARY_VKS.
    log.debug("[SEQ-A] 2) toggle OFF (NumPad scan)")
    try:
        from ..numlock_cycle import press_numpad_scan
    except Exception:
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
            cycler._locked.clear()  # v1 291-293
        except Exception:
            pass
    sleep_ms(key_gap_ms)

    # 3) 부활 burst (NUMPAD6, 0.3s @ 0.1s)  — v1 300-303
    log.debug("[SEQ-A] 3) revive burst")
    t_end = time.time() + V1.SEQ_A_REVIVE_BURST_SEC
    while time.time() < t_end:
        dispatcher.tap(V1.VK_NUMPAD6, hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
        sleep_ms(int(V1.SEQ_A_BURST_INTERVAL_SEC * 1000))

    # 4) 자힐 burst (NUMPAD1, 0.5s @ 0.1s)  — v1 309-312
    log.debug("[SEQ-A] 4) heal burst")
    t_end = time.time() + V1.SEQ_A_HEAL_BURST_SEC
    while time.time() < t_end:
        dispatcher.tap(V1.VK_NUMPAD1, hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
        sleep_ms(burst_gap_ms)

    # ------ Block B (v1 target_sequence.py:316-368, 2026-04-24 ESC only) ------
    if enable_block_b:
        log.debug("[SEQ-B] ESC only (TAB×2/재ON 보류 — worker 가 처리)")
        tap_vk(dispatcher, V1.VK_ESCAPE, hold_ms=V1.SEQ_A_TAP_HOLD_MIN_MS)
        sleep_ms(key_gap_ms)

    # ------ pending TAB-LOCK 설정 (v1 healer_worker.py:1018) ------
    # last_self_heal_ts 도 함께 — integration_tick 의 post_self_heal_tab 15s 창 활성화.
    if worker_state is not None:
        _now = time.time()
        worker_state["_pending_tab_lock_until"] = (
            _now + V1.PENDING_TAB_LOCK_SEC
        )
        worker_state["last_self_heal_ts"] = _now
        log.info(
            "[TAB-LOCK-PEND] %ds — worker 가 red_raw + 맵동기화 후 일괄 시전",
            int(V1.PENDING_TAB_LOCK_SEC),
        )
