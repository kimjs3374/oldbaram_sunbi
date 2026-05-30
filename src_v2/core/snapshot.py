"""Snapshot — atomic field store (lock-free read).

Design ref: §2.2

State truth 계약 (P2-1, v1_gap_fix_list):
- Follower 객체: force_exit_active / is_paused / exit_dir 의 truth.
- DecisionState (muscle.main_loop): trail / want_dir 의 worker-local 휘발 state.
- Snapshot 의 force_exit_*, map_paused, current_dir 는 read-only mirror.
  integration_tick 이 매 tick Follower 에서 복사. 외부 코드는 mirror 만 read.
- Snapshot.coord_tol_override (P0-4): cfg vs override 의 truth. integration_tick
  set, muscle.main_loop._decide_move_raw read.
- Snapshot 다른 필드 (hp/mp/coord/cd_*/buff_* 등): 각 watcher 가 truth.
  read-only consumers 는 절대 store.update 로 mutation 금지.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field, fields, replace
from typing import Any, Optional, Tuple

from .types import AttackerState, Detection


@dataclass
class Snapshot:
    # === 좌표 ===
    healer_coord: Optional[Tuple[int, int]] = None
    healer_map: str = ""
    healer_map_seq: int = 0
    attacker_coord: Optional[Tuple[int, int]] = None
    attacker_coord_valid: bool = False
    attacker_map: str = ""
    attacker_map_seq: int = 0
    attacker_last_dir: str = "-"

    # === HP/MP ===
    hp: int = -1
    mp: int = -1
    hp_cur: int = -1
    mp_cur: int = -1
    hp_max: int = -1
    mp_max: int = -1

    # === 쿨다운/버프 (요약) ===
    cd_parlyuk: int = -1
    cd_baekho: int = -1
    cd_parhon: int = -1
    cd_revive: int = -1
    buff_parlyuk_active: bool = False
    # 2026-05-05 P0-4: 파력무참 버프 잔여(초). -1 = 미관측.
    # CooldownReport.buff_parlyuk_sec 송신용. cd_parlyuk(쿨다운) 과 분리.
    # buff OCR 가 채울 때까지 -1 유지 (uplink 가 -1 안전 송신).
    buff_parlyuk_sec: int = -1
    buff_baekho_active: bool = False
    buff_gyoungryeok_active: bool = False

    # === 격수 상태 (UDP) ===
    attacker_hp: int = -1
    attacker_honma_sec: int = -1
    attacker_mujang_sec: int = -1
    attacker_boho_sec: int = -1
    attacker_state: Optional[AttackerState] = None

    # === 격수 PC 자기 측 buff/debuff (격수 자체 OCR — UDP State 송신용) ===
    self_debuff_honma_sec: int = -1
    self_buff_mujang_sec: int = -1
    self_buff_boho_sec: int = -1
    # 격수 PC 자기 측 빨탭 (자기 캐릭터 위치 — 스킬범위 오버레이용)
    self_red_box: Optional[Tuple[int, int, int, int]] = None
    self_red_cx: int = 0
    self_red_cy: int = 0

    # === 빨탭/흰탭 (YOLO) ===
    red_tab_present: bool = False
    red_tab_pos: Optional[Tuple[int, int]] = None
    red_tab_detection: Optional[Detection] = None
    white_tab_present: bool = False
    white_tab_detection: Optional[Detection] = None

    # === 프레임 (워처들이 공유) ===
    last_frame: Any = None  # numpy ndarray (avoid hard dep here)
    last_frame_ts: float = 0.0
    last_frame_origin: Tuple[int, int] = (0, 0)
    frame_w: int = 0
    frame_h: int = 0
    # 2026-04-25 game_region crop 추가 (v1 healer_worker.py:1342-1366 동일).
    last_crop: Any = None  # game_region crop frame
    last_crop_origin: Tuple[int, int] = (0, 0)  # crop offset (rx, ry)
    game_region_abs: Optional[Tuple[int, int, int, int]] = None  # 설정된 영역 (x,y,w,h)
    # mss frame 의 monitor origin (left, top). 화면 절대 좌표 region 을 frame
    # 좌표로 변환할 때 사용. v1 healer_worker.py grab.mon["left"/"top"] 동일.
    monitor_origin: Tuple[int, int] = (0, 0)

    # === 플래그 ===
    numlock_cycle_due: bool = False
    seq_in_progress: bool = False
    tab_lock_pending: bool = False
    numlock_on: bool = False
    armed: bool = False
    follow_only: bool = False
    udp_active: bool = False
    hwnd_fg: bool = False

    # === v1 1:1 통합 분기 신호 (integration_tick 가 갱신) ===
    # FORCE-EXIT (Follower.force_exit_active 미러)
    force_exit_active: bool = False
    force_exit_dir: str = "-"
    force_exit_remaining: float = 0.0
    # F1-PEND (격수 map_change_pending edge → 힐러 B3 차단)
    f1_pend_active: bool = False
    # MAP-PAUSE (Follower.is_paused 미러 — fresh_map_guard / new_map / loading)
    map_paused: bool = False
    map_pause_remaining: float = 0.0
    # 자힐 후 TAB 자동 복귀 윈도 (15초)
    post_self_heal_tab_until: float = 0.0
    last_self_heal_ts: float = 0.0
    # ESC 입력 후 흰탭 오감지 방지 suppress 윈도 (esc_recover 직후 recovery.py 가 설정)
    esc_suppress_tab_until: float = 0.0
    # cooldown 1Hz UDP 역송용 force_coord_tol (격수 측에서 emit).
    force_coord_tol: int = -1
    # parlyuk 버프 활성 → coord_tol=1 강제 동작용.
    parlyuk_buff_active: bool = False
    # P0-4 (v1_gap_fix_list): coord_tol single source of truth.
    # -1 = override 없음 (cfg.coord_tol 사용), >=0 = 강제값 (parlyuk 등).
    # integration_tick 이 set, muscle.main_loop 이 read. rule_cfg dict mutation
    # 의존 종료. SnapshotStore thread-safe 보장으로 race 차단.
    coord_tol_override: int = -1

    # === 검출 (publisher 가 직접 사용) ===
    all_detections: list = field(default_factory=list)

    # === FSM / 이동 (muscle 가 채움) ===
    fsm_state: str = "FOLLOW"
    current_dir: str = "-"
    want_dir: str = "-"
    move_reason: str = ""

    # === UDP / 시퀀스 ===
    attacker_seq: int = 0

    # === 성능 ===
    fps: float = 0.0
    perf_tuple: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    # === Cooldown 원시값 (publisher 가 그대로 전달) ===
    cooldown_reading: Any = None

    # === Cooldown UDP 역송용 메타 (cooldown_uplink._build_payload read) ===
    # v1 healer_worker.py:1995-2070 1:1.
    src_idx: int = 0
    nickname: str = ""
    xp_per_hour: int = 0
    event_text: str = ""
    self_heal_hp_thr: int = -1
    gyoungryeok_mp_thr: int = -1

    # === 메타 ===
    last_eye_update_ts: float = 0.0
    update_count: int = 0

    # === alias property — 의미 구분용 (2026-05-05 P1-2) ===
    # attacker_worker_v2 는 동일 SnapshotStore 의 healer_coord/healer_map
    # 슬롯에 자기 PC 좌표/맵을 실어 사용한다. (양쪽 워커가 같은 dataclass
    # 인스턴스를 공유하므로 슬롯 분리는 파괴적 변경.) 코드 가독성 회복을
    # 위해 attacker 측 의미를 명시적으로 노출하는 alias 만 추가한다.
    # 기존 healer_coord/healer_map 직접 read 도 동일하게 동작.
    @property
    def attacker_self_coord(self) -> Optional[Tuple[int, int]]:
        return self.healer_coord

    @property
    def attacker_self_map(self) -> str:
        return self.healer_map


class SnapshotStore:
    """Watchers update fields atomically. Muscle reads without lock.

    Atomicity guarantee:
    - CPython attribute write/read on a single instance is GIL-atomic.
    - For tuple/composite values, the watcher must construct the new value
      first then setattr — readers will see either old or new, never partial.
    """

    __slots__ = ("_snap",)

    def __init__(self, snap: Optional[Snapshot] = None):
        self._snap = snap or Snapshot()

    def read(self) -> Snapshot:
        """Lock-free read. Returns the live ref — consumers MUST treat as RO."""
        return self._snap

    def read_field(self, name: str, default: Any = None) -> Any:
        return getattr(self._snap, name, default)

    def update(self, **fields_kv: Any) -> None:
        """Atomic per-field setattr. Bumps update_count + last_eye_update_ts."""
        snap = self._snap
        for k, v in fields_kv.items():
            setattr(snap, k, v)
        snap.last_eye_update_ts = time.monotonic()
        snap.update_count = (snap.update_count + 1) & 0x7FFFFFFF

    def replace_all(self, snap: Snapshot) -> None:
        """Replace the whole snapshot ref (atomic)."""
        self._snap = snap

    def to_dict(self) -> dict:
        """Debug helper — copy of all primitive fields."""
        out = {}
        for f in fields(self._snap):
            v = getattr(self._snap, f.name)
            # skip frame (large)
            if f.name == "last_frame":
                out[f.name] = (v is not None)
                continue
            try:
                # sanity: only put hashable / repr-safe
                out[f.name] = v
            except Exception:
                out[f.name] = repr(v)
        return out
