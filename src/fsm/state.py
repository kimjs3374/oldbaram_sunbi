"""힐러 PC 상태 머신.

LEVEL 0  EMERGENCY    : 자기 HP 낮음 → 자힐
LEVEL 1  COMBAT       : 빨탭 존재 → 격수 근처 유지 + 힐/버프
LEVEL 2  FOLLOW       : 빨탭 없음, 좌표 변화 있음 → 격수 따라가기
LEVEL 3  IDLE         : 변화 없음 → 정지

전이 조건은 controller에서 평가. 본 파일은 enum만.
"""
from enum import Enum


class FsmState(Enum):
    IDLE = "IDLE"
    FOLLOW = "FOLLOW"
    COMBAT = "COMBAT"
    EMERGENCY = "EMERGENCY"
    MAP_CHANGE = "MAP_CHANGE"       # 호환용. 3단계로 세분화.
    ENTER_PORTAL = "ENTER_PORTAL"   # 격수 맵 변경 감지 직후
    LOADING = "LOADING"             # 힐러 맵 전환 중 (loading_sec)
    NEW_MAP = "NEW_MAP"             # 전환 완료 직후 일시 안정화
    STUCK = "STUCK"
    DEAD = "DEAD"                   # 힐러 사망/유령
    DISCONNECTED = "DISCONNECTED"   # UDP stale
    DEAD_RECKON = "DEAD_RECKON"     # 좌표 유실 일시 추정
