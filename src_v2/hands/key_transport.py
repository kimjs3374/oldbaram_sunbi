"""키 송신 transport 표준화 (v1_gap_fix_list P0-3).

v1 실측: 게임은 같은 "1" 라도 송신 경로에 따라 다른 입력으로 인식.

| Mode             | VK 영역      | 메서드                 | 사용처                          |
|------------------|--------------|------------------------|---------------------------------|
| MainDigit        | 0x30-0x39    | dispatcher.tap         | 일반 텍스트/디지트 입력         |
| NumPadLocked     | 0x60-0x69    | press_normal_vk        | 통상 시전 (NumLockCycler 잠금)  |
| NumPadDirect     | 0x60-0x69    | press_numpad_direct    | 파혼술 burst (NumPad scan 직송) |

회귀 이력:
- 백호/파력/메인힐 시전키가 한때 MainDigit 으로 나가 게임 미인식 → tap_numpad 신설
- 파혼술이 NumPadLocked 로 빠지면 burst 미동작 → press_numpad_direct 직호출 보존
- VK 0x60-0x69 / 0x30-0x39 같은 의미로 혼용되어 한 줄 실수로 회귀 위험

본 모듈은 enum 으로 의도 명시 강제. 함수 이름 vs VK 직값 갈등 차단.
"""
from __future__ import annotations
from enum import Enum
from typing import Optional


class KeyTransport(Enum):
    """키 송신 경로 — 의도를 enum 으로 명시."""
    MAIN_DIGIT = "main_digit"          # 0x30-0x39, dispatcher.tap
    NUMPAD_LOCKED = "numpad_locked"    # 0x60-0x69, press_normal_vk (NumLockCycler 잠금 상태)
    NUMPAD_DIRECT = "numpad_direct"    # 0x60-0x69, press_numpad_direct (scan 직송, 파혼술 burst)


# 슬롯 → VK 표
_MAIN_DIGIT_VK = {
    "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
    "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
}
_NUMPAD_VK = {
    "0": 0x60, "1": 0x61, "2": 0x62, "3": 0x63, "4": 0x64,
    "5": 0x65, "6": 0x66, "7": 0x67, "8": 0x68, "9": 0x69,
}


def vk_for(slot: str, transport: KeyTransport) -> Optional[int]:
    """슬롯 + transport → VK. 잘못된 조합은 None."""
    if transport is KeyTransport.MAIN_DIGIT:
        return _MAIN_DIGIT_VK.get(slot)
    if transport in (KeyTransport.NUMPAD_LOCKED, KeyTransport.NUMPAD_DIRECT):
        return _NUMPAD_VK.get(slot)
    return None


def send(slot: str, transport: KeyTransport,
         dispatcher=None, hold_ms: int = 50) -> bool:
    """단일 송신 entry. transport 에 따라 라우팅.

    회귀 가드: VK 직값 호출 금지 — 호출처는 (slot, transport) 만 명시.
    """
    vk = vk_for(slot, transport)
    if vk is None:
        return False
    try:
        if transport is KeyTransport.MAIN_DIGIT:
            if dispatcher is None:
                return False
            dispatcher.tap(vk, hold_ms=hold_ms)
            return True
        if transport is KeyTransport.NUMPAD_LOCKED:
            from src.input.numlock_cycle import press_normal_vk  # type: ignore
            press_normal_vk(int(vk), min_ms=int(hold_ms), max_ms=int(hold_ms))
            return True
        if transport is KeyTransport.NUMPAD_DIRECT:
            from src.input.numlock_cycle import press_numpad_direct  # type: ignore
            return bool(press_numpad_direct(int(vk)))
    except Exception:
        return False
    return False
