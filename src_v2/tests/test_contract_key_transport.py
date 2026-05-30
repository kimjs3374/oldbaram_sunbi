"""KeyTransport enum 회귀 테스트 (v1_gap_fix_list P0-3).

VK 직값 혼용 차단 — 호출처는 (slot, KeyTransport) 만 명시해야 함.
"""
from __future__ import annotations
from src_v2.hands.key_transport import KeyTransport, vk_for


def test_main_digit_range():
    """MAIN_DIGIT 은 0x30-0x39."""
    for s in "0123456789":
        vk = vk_for(s, KeyTransport.MAIN_DIGIT)
        assert vk is not None and 0x30 <= vk <= 0x39, f"slot {s} VK={vk:#x}"


def test_numpad_locked_range():
    """NUMPAD_LOCKED 은 0x60-0x69 (press_normal_vk 가 메인키보드로 변환)."""
    for s in "0123456789":
        vk = vk_for(s, KeyTransport.NUMPAD_LOCKED)
        assert vk is not None and 0x60 <= vk <= 0x69, f"slot {s} VK={vk:#x}"


def test_numpad_direct_range():
    """NUMPAD_DIRECT 도 0x60-0x69 (scan 직송)."""
    for s in "0123456789":
        vk = vk_for(s, KeyTransport.NUMPAD_DIRECT)
        assert vk is not None and 0x60 <= vk <= 0x69, f"slot {s} VK={vk:#x}"


def test_invalid_slot_returns_none():
    assert vk_for("a", KeyTransport.MAIN_DIGIT) is None
    assert vk_for("", KeyTransport.NUMPAD_LOCKED) is None


def test_main_and_numpad_distinct():
    """같은 슬롯이라도 MAIN/NUMPAD 는 반드시 다른 VK (회귀 가드)."""
    for s in "0123456789":
        m = vk_for(s, KeyTransport.MAIN_DIGIT)
        n = vk_for(s, KeyTransport.NUMPAD_LOCKED)
        assert m != n, f"slot {s} 동일 VK ({m:#x}) — 회귀 발생"
