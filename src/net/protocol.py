"""UDP 패킷 스키마 v5.

메시지 종류 (JSON `type` 필드로 구분):
- "state"  : 격수 → 힐러 (30Hz 상태 브로드캐스트). 기존 v4 State.
- "ctrl"   : 격수 → 힐러 (제어 명령 — 시작/일시정지/정지).
- "cd"     : 힐러 → 격수 (쿨다운 보고 — 파력무참/백호의희원 남은 초).

수신 측은 `type` 없으면 legacy v4 "state"로 간주(호환). 역방향 (힐러→격수)용
포트는 별개 (ATTACKER_RECV_PORT). 힐러는 격수 IP를 recvfrom src에서 자동 획득.
"""
from dataclasses import dataclass, asdict, field
from typing import Optional, Union
import json
import time

PROTOCOL_VER = 5

# 격수가 힐러 쿨다운 보고를 수신할 포트 (송신 포트와 별개).
ATTACKER_RECV_PORT = 45455


@dataclass
class State:
    seq: int = 0
    ts_ms: int = 0
    map_name: str = ""
    coord_valid: bool = False
    x: int = 0
    y: int = 0
    red_tab: bool = False
    red_cx: int = 0
    red_cy: int = 0
    last_dir: str = "-"
    hp: int = -1
    mp: int = -1
    map_seq: int = 0
    map_change_pending: bool = False
    # 2026-06-12: 격수 F1 → 힐러 "빨탭 재고정 시퀀스 즉시 실행" 트리거 카운터.
    # F1 누를 때마다 +1. 힐러는 증가분 감지 시 1회 발동(패킷손실/중복에 견고).
    reanchor_seq: int = 0
    # 2026-06-12: 격수 F2 → 쩔캐(현인) "지폭지술 시퀀스 실행" 트리거 카운터.
    # 몹이 충분히 모였을 때 격수가 F2 → 쩔캐가 증가분 감지 시 1회 발동.
    jipok_seq: int = 0
    # 격수 HP/MP 바율 (0~100 정수 %). -1 = OCR 영역 미지정/미관측.
    # 힐러 SkillScheduler 가 격수부활(hp_pct==0) 트리거로 사용.
    hp_pct: int = -1
    mp_pct: int = -1

    def to_bytes(self) -> bytes:
        d = asdict(self)
        d["ver"] = PROTOCOL_VER
        d["type"] = "state"
        return json.dumps(d, ensure_ascii=False).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> Optional["State"]:
        try:
            d = json.loads(data.decode("utf-8"))
            ver = d.get("ver")
            # v4도 State로 받아들임 (type 없음).
            if ver not in (4, 5):
                return None
            if d.get("type") not in (None, "state"):
                return None
            d.pop("ver", None); d.pop("type", None)
            return cls(**{k: v for k, v in d.items()
                          if k in cls.__dataclass_fields__})
        except Exception:
            return None


@dataclass
class ControlCmd:
    """격수 → 힐러 제어 명령.

    target_idx: peers 인덱스 (힐러1=0, 힐러2=1, ...). -1=전체.
    cmd: "start" | "pause" | "stop" | "follow_on" | "follow_off".
      - start      : armed=True (동작 재개, 워커 없으면 기동).
      - pause      : armed=False (키 주입 중단, 상태 유지).
      - stop       : armed=False + 워커 종료 요청 (self._stop=True).
      - follow_on  : follow_only=True (주력힐/파력무참 OFF, 이동만).
      - follow_off : follow_only=False (전투 복귀).
    """
    target_idx: int = 0
    cmd: str = "start"
    ts_ms: int = 0

    def to_bytes(self) -> bytes:
        d = asdict(self)
        d["ver"] = PROTOCOL_VER
        d["type"] = "ctrl"
        return json.dumps(d, ensure_ascii=False).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> Optional["ControlCmd"]:
        try:
            d = json.loads(data.decode("utf-8"))
            if d.get("ver") != PROTOCOL_VER or d.get("type") != "ctrl":
                return None
            d.pop("ver", None); d.pop("type", None)
            return cls(**{k: v for k, v in d.items()
                          if k in cls.__dataclass_fields__})
        except Exception:
            return None


@dataclass
class CooldownReport:
    """힐러 → 격수 쿨다운 보고.

    src_idx: 힐러 인덱스 (peers 순서와 일치). 힐러가 config에서 읽어 송신.
    cd_* : 남은 초. -1 = 미측정 / OCR 실패.
    armed: 힐러 armed 상태 (UI 동기화용, 격수 GUI 뱃지에 반영).
    nickname: 힐러 캐릭터 닉네임 (OCR 결과). 빈 문자열 = 미설정/실패.
    """
    src_idx: int = 0
    cd_parlyuk: int = -1
    cd_baekho: int = -1
    # 2026-06-12: 쩔캐(현인) 지폭지술 남은 쿨 (시전시각 타이머 기반).
    # -1=미해당(현인 아님/쩔캐 아님), 0=준비됨, >0=남은 초.
    cd_jipok: int = -1
    ts_ms: int = 0
    armed: bool = False
    nickname: str = ""
    # 시간당 예상 경험치 (가산형 추정치). 0 = 미측정/부족.
    xp_per_hour: int = 0
    # 파력무참 버프 지속시간 OCR 결과 (초). -1 = OCR 영역 미지정/실패.
    # 격수는 이 값 >= 0 이면 우선 사용, -1이면 cd_parlyuk로 역산 폴백.
    buff_parlyuk_sec: int = -1
    # 2026-04-21: 힐러 → 격수 알림 이벤트. 힐러가 특정 상태 진입 시 전송.
    # 격수 overlay.push_alert 로 표시. event_seq 증가 여부로 새 이벤트 판정.
    # 예: "공력증강 임박", "자힐 하는중".
    event_text: str = ""
    event_seq: int = 0
    # 2026-04-22: 힐러 자신의 HP/MP OCR 결과 (격수 전용 힐러 상태 오버레이용).
    # -1 = OCR 미관측/영역 미지정. 0~100 = 백분율. cur/max 는 원시값 (표시용).
    # 격수는 CooldownReport 수신 시 HealerStatusOverlay 로 전달.
    hp_pct: int = -1
    mp_pct: int = -1
    hp_cur: int = -1
    mp_cur: int = -1
    hp_max: int = 0
    mp_max: int = 0
    # 2026-04-22: 힐러 임계치 (자힐 HP%, 공력증강 MP%). 워커 시작 후 매
    # CD-SEND 에 실려옴 → 격수가 "공증 임박" 판정 기준값으로 사용.
    # -1 = 힐러 워커 미기동/미주입 (heartbeat 빈 패킷).
    self_heal_hp_thr: int = -1
    gyoungryeok_mp_thr: int = -1
    # 2026-04-22: 힐러 자신의 맵/좌표/상태 (격수 UI 힐러 행 표시용).
    # healer_map 빈 문자열 = 미측정. coord_valid=False 면 healer_x/y 무효.
    # state_text: "전투중" / "따라가기만" / "일시정지" / "정지" / "맵전환중" 등.
    healer_map: str = ""
    healer_x: int = 0
    healer_y: int = 0
    coord_valid: bool = False
    state_text: str = ""

    def to_bytes(self) -> bytes:
        d = asdict(self)
        d["ver"] = PROTOCOL_VER
        d["type"] = "cd"
        return json.dumps(d, ensure_ascii=False).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> Optional["CooldownReport"]:
        try:
            d = json.loads(data.decode("utf-8"))
            if d.get("ver") != PROTOCOL_VER or d.get("type") != "cd":
                return None
            d.pop("ver", None); d.pop("type", None)
            return cls(**{k: v for k, v in d.items()
                          if k in cls.__dataclass_fields__})
        except Exception:
            return None


def parse_packet(data: bytes) -> Optional[Union[State, ControlCmd, CooldownReport]]:
    """type 분기 통합 파서. 알 수 없는 메시지는 None."""
    try:
        d = json.loads(data.decode("utf-8"))
    except Exception:
        return None
    t = d.get("type")
    if t == "ctrl":
        return ControlCmd.from_bytes(data)
    if t == "cd":
        return CooldownReport.from_bytes(data)
    # type 없음 or "state" → legacy State.
    return State.from_bytes(data)


def now_ms() -> int:
    return int(time.time() * 1000)
