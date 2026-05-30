"""Shared helpers for sequences."""
from __future__ import annotations
import random
import time
from typing import Dict, Optional

from ...config import v1_defaults as V1

# VK map ported from src/input/skill_blueprints.py + feedback_vk_layout_2026_04_20
# (1=메인힐, 2=혼마술, 3=공력증강, 4=백호1, 5=백호2, 6=부활, 7=파혼술, 8=파력무참, 0=금강)
#
# 메인 키보드 숫자 VK. 시전 키는 NumPad 슬롯 의도라 별도 NUMPAD_VK_DIGIT 와
# tap_numpad helper 사용. 일반 텍스트/디지트 입력 용도엔 이 표 그대로.
VK_DIGIT: Dict[str, int] = {
    "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
    "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
}

# NumPad VK 매핑 — 시전 슬롯 (메인힐/백호/파력/파혼/공증/부활/금강) 전용.
# v1 healer_worker.py:131 skill_vks 1:1 (메인힐=NUMPAD1, 백호1=NUMPAD4 등).
# tap_numpad(slot) helper 가 이 표 + press_normal_vk 로 v1 동작 재현.
NUMPAD_VK_DIGIT: Dict[str, int] = {
    "0": 0x60, "1": 0x61, "2": 0x62, "3": 0x63, "4": 0x64,
    "5": 0x65, "6": 0x66, "7": 0x67, "8": 0x68, "9": 0x69,
}
VK_TAB = 0x09
VK_HOME = 0x24
VK_ESC = 0x1B
VK_NUMLOCK = 0x90

# Skill slot symbolic names (matches v1 cfg)
SLOT_MAIN_HEAL = "1"
SLOT_HONMA = "2"
SLOT_GYOUNGRYEOK = "3"
SLOT_BAEKHO_1 = "4"
SLOT_BAEKHO_2 = "5"
SLOT_REVIVE = "6"
SLOT_PARHON = "7"
SLOT_PARLYUK = "8"
SLOT_GEUMGANG = "0"


def sleep_ms(ms: float) -> None:
    """Short blocking sleep — used inside sequences (background thread, not muscle)."""
    if ms > 0:
        time.sleep(ms / 1000.0)


def _resolved_hold_ms(hold_ms: Optional[int]) -> int:
    """v1 _press_vk(min_ms=35, max_ms=60) 1:1 — randint in v1 range.

    명시적 hold_ms 가 V1.SEQ_A_TAP_HOLD_MIN_MS 와 같거나 None 이면 v1 randint
    적용. 그 외(MUJANG/BOHO 50ms 등 별도 의미값)는 그대로 유지.
    """
    if hold_ms is None or hold_ms == V1.SEQ_A_TAP_HOLD_MIN_MS:
        return random.randint(V1.SEQ_A_TAP_HOLD_MIN_MS, V1.SEQ_A_TAP_HOLD_MAX_MS)
    return int(hold_ms)


def tap(dispatcher, slot: str, hold_ms: Optional[int] = None) -> None:
    """Tap a digit-slot key (1..9, 0). v1 _press_vk randint(35,60) 적용."""
    vk = VK_DIGIT.get(slot)
    if vk is None:
        return
    dispatcher.tap(vk, hold_ms=_resolved_hold_ms(hold_ms))


def tap_vk(dispatcher, vk: int, hold_ms: Optional[int] = None) -> None:
    dispatcher.tap(vk, hold_ms=_resolved_hold_ms(hold_ms))


def tap_numpad(dispatcher, slot: str, hold_ms: Optional[int] = None) -> None:
    """NumPad 슬롯 시전 키. v1 SkillScheduler 의 cast_fn=press_normal_vk 동치.

    P0-3 (v1_gap_fix_list): KeyTransport.NUMPAD_LOCKED 단일 entry.
    NumPad VK (0x60-0x69) → press_normal_vk → 메인 키보드 VK 변환 송신.
    NumLockCycler 가 NumPad 슬롯 lock 으로 NumPad 키 자체는 게임에서 무력화.
    """
    from ..key_transport import KeyTransport, send as kt_send
    hms = _resolved_hold_ms(hold_ms)
    sent = kt_send(slot, KeyTransport.NUMPAD_LOCKED, dispatcher=dispatcher, hold_ms=int(hms))
    if not sent:
        # fallback — dispatcher 직 (게임이 NumPad 받게 설정된 환경에서만 동작).
        vk = NUMPAD_VK_DIGIT.get(slot)
        if vk is None:
            return
        dispatcher.tap(int(vk), hold_ms=int(hms))
