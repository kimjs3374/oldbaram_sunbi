"""Shared dataclasses for src_v2.

Design ref: §4.1
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional, Tuple, Set, Dict
import time


@dataclass(frozen=True)
class CastRequest:
    """Brain -> Hands: 시전 요청. priority 낮을수록 먼저."""
    name: str
    priority: int = 100
    ctx: Dict[str, Any] = field(default_factory=dict)
    requested_at: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class CastResult:
    """Hands -> Brain/Memory: 시전 결과."""
    request: CastRequest
    status: str  # "ok" | "skipped" | "failed"
    detail: str = ""
    finished_at: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class CastError:
    """Hands -> Brain/Memory: 시전 실패."""
    request: CastRequest
    reason: str
    happened_at: float = field(default_factory=time.monotonic)


@dataclass
class RuleContext:
    """룰 핸들러에 전달되는 부가 컨텍스트."""
    cfg: Dict[str, Any] = field(default_factory=dict)
    cooldowns: Dict[str, float] = field(default_factory=dict)
    last_cast: Dict[str, float] = field(default_factory=dict)
    in_progress: Set[str] = field(default_factory=set)
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionRecord:
    """Memory에 기록되는 1행."""
    ts: float
    action: str
    snapshot_at_decision: Dict[str, Any]
    result: str
    latency_ms: float
    detail: str = ""


@dataclass
class Detection:
    """YOLO 검출 결과 1건. v1 main_window 호환 .x1/.y1/.x2/.y2/.cx/.cy/.w/.h 노출."""
    cls: str  # "red_tab" | "white_tab" | ...
    bbox: Tuple[int, int, int, int]  # (x1, y1, x2, y2)
    conf: float
    center: Optional[Tuple[int, int]] = None

    def __post_init__(self):
        if self.center is None:
            x1, y1, x2, y2 = self.bbox
            object.__setattr__(self, "center", ((x1 + x2) // 2, (y1 + y2) // 2))

    # v1 호환 속성 (main_window._on_frame 가 직접 인덱싱).
    @property
    def x1(self) -> int: return int(self.bbox[0])
    @property
    def y1(self) -> int: return int(self.bbox[1])
    @property
    def x2(self) -> int: return int(self.bbox[2])
    @property
    def y2(self) -> int: return int(self.bbox[3])
    @property
    def cx(self) -> int: return int(self.center[0]) if self.center else 0
    @property
    def cy(self) -> int: return int(self.center[1]) if self.center else 0
    @property
    def w(self) -> int: return int(self.bbox[2] - self.bbox[0])
    @property
    def h(self) -> int: return int(self.bbox[3] - self.bbox[1])
    @property
    def tab_color(self) -> str:
        """v1 호환: 'RED' | 'WHITE'."""
        c = (self.cls or "").lower()
        if "red" in c: return "RED"
        if "white" in c: return "WHITE"
        return "RED"


@dataclass
class AttackerState:
    """UDP로 받는 격수 상태 (요약). v1 net.protocol.State 1:1."""
    coord: Optional[Tuple[int, int]] = None
    coord_valid: bool = False
    map_name: str = ""
    map_seq: int = 0
    hp: int = -1
    last_dir: str = "-"  # "U" | "D" | "L" | "R" | "-"
    honma_sec: int = -1
    mujang_sec: int = -1
    boho_sec: int = -1
    received_at: float = 0.0
    # v1 1:1 추가 필드 (net.protocol.State)
    mp: int = -1
    seq: int = 0
    map_change_pending: bool = False
    red_tab: bool = False

    # v1 호환 alias — 일부 코드가 hp_pct/mp_pct/buff_*_sec/debuff_honmasul_sec 사용.
    @property
    def hp_pct(self) -> int: return self.hp
    @property
    def mp_pct(self) -> int: return self.mp
    @property
    def buff_mujang_sec(self) -> int: return self.mujang_sec
    @property
    def buff_boho_sec(self) -> int: return self.boho_sec
    @property
    def debuff_honmasul_sec(self) -> int: return self.honma_sec
