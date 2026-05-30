"""파혼술 시퀀스 — v1 1:1 (healer_worker.py:1106-1118).

v1 동작:
  press_numpad_direct(vk) burst
  t_end = time.time() + 0.5  (PARHON_BURST_SEC)
  while time.time() < t_end:
      press_numpad_direct(vk)
      time.sleep(0.1)  (PARHON_BURST_INTERVAL_SEC)

NumPad scan code 직접 송신: nvk 변환 안 함 — InputDispatcher.tap_numpad_direct
어댑터 hook 가 있으면 그걸 호출, 없으면 일반 tap fallback.
"""
from __future__ import annotations
import time

from ...core.plugin_registry import sequence
from ...config import v1_defaults as V1
from ._common import sleep_ms
from ..key_transport import KeyTransport, send as kt_send

# v1 healer_worker.py:131 — 파혼술 default slot = NumPad7.
# P0-3 단일화: KeyTransport.NUMPAD_DIRECT (scan code 직송) 강제.
PARHON_SLOT = "7"


@sequence("parhon", description="파혼술 NumPad scan burst (0.5s @ 0.1s)")
def parhon(ctx: dict) -> None:
    dispatcher = ctx["_dispatcher"]
    slot = str(ctx.get("parhon_slot", PARHON_SLOT))

    t_end = time.time() + V1.PARHON_BURST_SEC
    n = 0
    while time.time() < t_end:
        # P0-3: KeyTransport.NUMPAD_DIRECT 단일 entry. 실패 시 dispatcher fallback.
        sent = kt_send(slot, KeyTransport.NUMPAD_DIRECT, dispatcher=dispatcher, hold_ms=30)
        if not sent:
            try:
                if hasattr(dispatcher, "tap_numpad_direct"):
                    dispatcher.tap_numpad_direct(0x60 + int(slot))
                else:
                    dispatcher.tap(0x60 + int(slot), hold_ms=30)
            except Exception:
                pass
        n += 1
        sleep_ms(int(V1.PARHON_BURST_INTERVAL_SEC * 1000))
    ctx["_parhon_burst_count"] = n
