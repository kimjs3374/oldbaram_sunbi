"""SEQ-RCLICK — right-click on attacker red_tab during heal.

Background sub-loop runs while ctx['heal_in_progress'] is True.
Implementation: spawned by self_heal_seq or directly by brain.
"""
from __future__ import annotations
import threading
import time

from ...core.plugin_registry import sequence


@sequence("seq_rclick", description="자힐 중 격수 우클릭 sub-loop")
def seq_rclick(ctx: dict) -> None:
    """Spawn sub-thread that right-clicks at red_tab_pos while heal_in_progress."""
    dispatcher = ctx["_dispatcher"]
    pos = ctx.get("red_tab_pos")
    duration_ms = int(ctx.get("duration_ms", 1500))
    interval_ms = int(ctx.get("interval_ms", 500))
    if pos is None:
        return
    x, y = pos
    deadline = time.monotonic() + duration_ms / 1000.0

    def _loop():
        while time.monotonic() < deadline:
            try:
                dispatcher.click(x, y, button="right")
            except Exception:  # noqa: BLE001
                pass
            time.sleep(interval_ms / 1000.0)

    t = threading.Thread(target=_loop, daemon=True, name="seq_rclick_sub")
    t.start()
