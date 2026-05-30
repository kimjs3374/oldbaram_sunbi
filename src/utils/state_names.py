"""FSM 상태·액션 한글 디스플레이 매핑 (UI 전용, 로그는 영어 유지)."""
from __future__ import annotations

FSM_STATE_KR = {
    "FOLLOW":       "따라가는 중",
    "COMBAT":       "사냥 중",
    "LOADING":      "맵 이동 중",
    "STUCK":        "멈춤 감지",
    "NEW_MAP":      "새 맵 진입",
    "ENTER_PORTAL": "포탈 진입",
    "IDLE":         "대기",
    "TAB_CONFIRM":  "빨탭 동기화 중",
}

TAB_ACTION_KR = {
    "send_home": "Home(셀프타겟) 송신",
    "send_tab":  "Tab(빨탭 확정) 송신",
    "wait_red":  "빨탭 확인 대기",
    "done_ok":   "동기화 완료",
    "retry_arm": "재시도",
}


def fsm_kr(state: str) -> str:
    if not state:
        return "-"
    return FSM_STATE_KR.get(state, state)


def tab_kr(action: str) -> str:
    if not action:
        return "-"
    return TAB_ACTION_KR.get(action, action)
