"""ESC 복구 시퀀스 — 채팅 팝업/UI 오버레이 닫기.

recovery.py 의 self_heal no_effect / chat_popup 핸들러가 요청.
ESC 1회 탭 + 100ms 대기 (UI dismiss 보장).
"""
from __future__ import annotations

from ...core.plugin_registry import sequence
from ._common import VK_ESC, sleep_ms


@sequence("esc_recover", description="ESC 1회 — 팝업/UI 닫기")
def esc_recover(ctx: dict) -> None:
    dispatcher = ctx["_dispatcher"]
    dispatcher.tap(VK_ESC, hold_ms=50)
    sleep_ms(100)
