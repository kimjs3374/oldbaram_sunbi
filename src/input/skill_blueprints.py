"""스킬 블루프린트 (선언 전용 모듈).

`SkillScheduler` 가 해석하는 `SkillSpec` 선언들을 엔진과 분리.
새로운 조건부 스킬 추가 시 이 파일만 수정하면 됨 (엔진 미변경).

등록 스킬 (2026-04-20 기준):
- 파력무참       : 180s 고정 쿨, buff OCR "파력무참" 으로 시전 검증.
- 백호의희원      : 쿨 OCR 기반, 쿨 관측될 때까지 burst 반복.
- 백호의희원첨     : 위와 동일.
- 파혼술         : 쿨 없음. buff OCR 에 "혼마술" 보이면 즉시 burst.
- 자힐           : self HP% < 임계치. pre=블록A, post=블록B. 메인힐 vk 재사용.
- 공력증강        : self MP% < 임계치. 자체 VK. A/B 불필요.
- 자가부활        : self HP% == 0. pre=블록A, post=블록B → 자힐.
- 격수부활        : attacker HP% == 0 AND self HP%>0. A/B 불필요.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Union

from .numlock_cycle import VK_NUMPAD7, VK_NUMPAD8


# NumPad0~9 VK 코드 (0x60 ~ 0x69). blueprints 기본 VK 지정용.
_VK_NUMPAD0 = 0x60
_VK_NUMPAD1 = 0x61
_VK_NUMPAD2 = 0x62
_VK_NUMPAD3 = 0x63
_VK_NUMPAD4 = 0x64
_VK_NUMPAD5 = 0x65
_VK_NUMPAD6 = 0x66
_VK_NUMPAD7 = 0x67
_VK_NUMPAD8 = 0x68
_VK_NUMPAD9 = 0x69


@dataclass
class SkillSpec:
    name: str
    vk: int
    cooldown_sec: float
    predicate: Callable[[dict], bool]
    offset_sec: float = 0.0
    enabled: bool = True
    last_cast: float = 0.0
    locked: bool = False  # legacy, 미사용.
    # v4 OCR 검증. None 이면 burst 1회 후 무조건 성공 간주.
    verify_kind: Optional[str] = None       # "buff" | "cooldown" | None.
    verify_target: Optional[str] = None     # ctx 풀 키 (보통 name 과 동일).
    verify_wait_sec: float = 2.0            # burst 직후 OCR 반영 대기.
    retry_max: int = 3                       # burst 최대 시도 수.
    burst_sec: float = 2.0                   # burst 지속.
    burst_interval_sec: float = 0.1          # burst 내부 press 간격.
    # 쿨이 관측될 때까지 burst 무한 반복 (백호의희원류).
    retry_until_ready: bool = False
    # 2026-04-20 추가: burst 전/후 실행할 커스텀 훅.
    # 예: 자힐/자가부활 → pre=블록A (self-target), post=블록B (격수 복귀).
    # scheduler 엔진이 cast_fn 주입하기 전후로 호출. 인자: ctx dict.
    pre_block: Optional[Callable[[dict], None]] = None
    post_block: Optional[Callable[[dict], None]] = None
    # 2026-04-20 추가: 우선순위. 값이 작을수록 먼저 평가/시전.
    # 자가부활(0) > 격수부활(1) > 자힐(2) > 기타(기본 10).
    priority: int = 10
    # 2026-04-20 추가: 동적 임계치 (인스턴스 외부에서 주입). 자힐/공력증강용.
    # predicate 가 ctx 에서 값을 읽어 threshold 와 비교. -1 이면 비활성.
    threshold: int = -1
    # 2026-04-20 Patch 2.14: 시전 중 방향키 이동을 완전히 차단할지 여부.
    # True → scheduler 가 _cast_with_retry 진입/종료 시 keys.set_movement_lock(on/off)
    # 을 통해 메인 루프 이동을 멈춤. 자힐/자가부활처럼 TAB/HOME 쓰는 A+B
    # 시퀀스만 True. 공력증강/파력무참/백호/파혼술/격수부활은 False (단순 burst,
    # 방향키와 병행 가능).
    blocks_movement: bool = False
    # 2026-04-20 v6-edge: edge-triggered 전용 스킬 표시.
    # True → scheduler.request_cast() 경로로만 시전. polling 루프에서는 skip.
    # 자힐/자가부활/공력증강 등 OCR 값 변화 순간 1회 시전하려는 스킬. 쿨 개념
    # 없음 — 상태가 또 cross-down 되면 즉시 재시전. 사용자 원 설계.
    edge_only: bool = False

    def ready(self, now: float, ctx: dict) -> bool:
        if not self.enabled:
            return False
        if now - self.last_cast < self.cooldown_sec:
            return False
        if self.last_cast == 0.0 and (
            now - ctx.get("start_time", now)
        ) < self.offset_sec:
            return False
        try:
            return bool(self.predicate(ctx))
        except Exception:
            return False


def _cd_empty(ctx: dict, key: str) -> bool:
    """`ctx["cooldowns"][key]` 가 0 이하(=준비됨) 이거나 미관측(-1)이면 True.

    백호의희원/첨용.
    """
    try:
        pool = ctx.get("cooldowns") or {}
        return int(pool.get(key, -1)) <= 0
    except Exception:
        return False


def _buff_present(ctx: dict, key: str) -> bool:
    """`ctx["buffs"][key]` 가 0 초과(=해당 버프 관측됨)면 True."""
    try:
        pool = ctx.get("buffs") or {}
        return int(pool.get(key, -1)) > 0
    except Exception:
        return False


def _attacker_debuff_present(ctx: dict, key: str) -> bool:
    """`ctx["attacker_buffs"][key]` 가 0 초과면 True.

    감지는 격수 PC, 시전은 힐러 PC. 격수가 UDP State.debuff_honmasul_sec 로
    혼마술 잔여 초를 실어 보내고 힐러 워커가 ctx["attacker_buffs"] 로 주입.
    """
    try:
        pool = ctx.get("attacker_buffs") or {}
        return int(pool.get(key, -1)) > 0
    except Exception:
        return False


def _attacker_buff_missing(ctx: dict, key: str) -> bool:
    """격수 버프 key 가 **확정적으로** 없으면 True.
    - 0 = OCR 돌았고 keyword 없음 → True (시전)
    - 1 = OCR 돌았고 keyword 있음 → False (시전 X)
    - -1 = OCR 미수행 → False (모름, 시전 X, 안전)
    """
    try:
        pool = ctx.get("attacker_buffs") or {}
        return int(pool.get(key, -1)) == 0
    except Exception:
        return False


def _self_hp_below(ctx: dict, thr_getter) -> bool:
    """힐러 본인 HP% < threshold. HP 미관측(-1)이면 False.

    thr_getter: () -> int 또는 직접 int. 동적 임계치 지원.
    """
    try:
        hp = int((ctx.get("self_hp_pct", -1)))
        if hp < 0:
            return False
        thr = int(thr_getter() if callable(thr_getter) else thr_getter)
        return hp < thr
    except Exception:
        return False


def _self_mp_below(ctx: dict, thr_getter) -> bool:
    try:
        mp = int((ctx.get("self_mp_pct", -1)))
        if mp < 0:
            return False
        thr = int(thr_getter() if callable(thr_getter) else thr_getter)
        return mp < thr
    except Exception:
        return False


def _self_dead(ctx: dict) -> bool:
    """self HP% == 0 (정확히 0일 때만)."""
    try:
        hp = int(ctx.get("self_hp_pct", -1))
        return hp == 0
    except Exception:
        return False


def _attacker_dead(ctx: dict) -> bool:
    """attacker HP% == 0 AND self HP%>0 (힐러 먼저 살아야 격수 부활 가능)."""
    try:
        atk = int(ctx.get("attacker_hp_pct", -1))
        self_hp = int(ctx.get("self_hp_pct", -1))
        return atk == 0 and self_hp > 0
    except Exception:
        return False


def default_skills(parlyuk_offset_sec: float = 0.0,
                   vk_map: Optional[dict] = None,
                   self_heal_hp_thr: Union[int, Callable[[], int]] = 50,
                   gyoungryeok_mp_thr: Union[int, Callable[[], int]] = 30,
                   pre_block_a: Optional[Callable[[dict], None]] = None,
                   post_block_b: Optional[Callable[[dict], None]] = None,
                   post_self_resurrect: Optional[Callable[[dict], None]] = None,
                   pre_block_ab: Optional[Callable[[dict], None]] = None,
                   cast_mujang_hook: Optional[Callable[[dict], None]] = None,
                   cast_boho_hook: Optional[Callable[[dict], None]] = None,
                   cast_parhon_hook: Optional[Callable[[dict], None]] = None,
                   ) -> list:
    """힐러 조건부 스킬.

    Args:
      parlyuk_offset_sec: 파력무참 시작 오프셋.
      vk_map: 스킬명 → VK 매핑 (main_window 에서 SkillDialog 설정 반영).
      self_heal_hp_thr: 자힐 발동 HP% 임계치. int 또는 () -> int 콜러블
                       (스피너 실시간 반영용).
      gyoungryeok_mp_thr: 공력증강 발동 MP% 임계치. int 또는 () -> int.
      pre_block_a: 자가부활 burst 전 실행 훅 (블록A self-target+burst).
      post_block_b: 자힐/자가부활 시 burst 후 실행 훅 (블록B 격수 복귀).
      post_self_resurrect: 자가부활 후 실행 훅 (블록B + 자힐 burst).
      pre_block_ab: 자힐 시 실행되는 A+B 통합 훅. 방향키 release 후 A→B.
                   (자힐 spec 은 burst_sec=0 으로 이 훅이 전체 담당.)
    """
    vk_map = dict(vk_map or {})
    mainheal_vk = int(vk_map.get("메인힐", _VK_NUMPAD1))

    skills = []

    # 자가부활 — 최우선. self HP==0. pre_block_ab 가 전체 A+B 담당.
    # 블록 A 안에 부활 burst 0.5s + 자힐 burst 1.0s 이미 포함 → 살아난 뒤 자힐까지
    # 한 시퀀스로 처리. main burst 불필요 (burst_sec=0). 자힐 spec 과 동일 구조.
    # 2026-04-20 Patch 2.18: cooldown_sec 3.0 → 0.0. 죽은 상태 유지되면
    # 매 tick predicate 재평가 → 즉시 재부활 시도. blocks_movement=True 가
    # 시전 중 재진입 자동 차단 (동일 스레드 _cast_with_retry).
    skills.append(SkillSpec(
        "자가부활",
        int(vk_map.get("부활", _VK_NUMPAD6)),
        0.0,
        _self_dead,
        verify_kind=None,
        burst_sec=0,
        burst_interval_sec=0.15,
        retry_max=1,
        pre_block=pre_block_ab,
        post_block=None,
        priority=0,
        blocks_movement=True,
        edge_only=True,
    ))

    # 격수부활 — self 살아있고 attacker HP==0. A/B 불필요 (격수에게 빨탭).
    # edge-triggered: attacker HP 가 0 으로 cross-down 되는 순간 1회. 쿨 없음.
    skills.append(SkillSpec(
        "격수부활",
        int(vk_map.get("부활", _VK_NUMPAD6)),
        0.0,
        _attacker_dead,
        verify_kind=None,
        burst_sec=1.5,
        burst_interval_sec=0.15,
        retry_max=3,
        priority=1,
        edge_only=True,
    ))

    # 자힐 — self HP% < 임계치. pre_block_ab 가 A+B 통합 시퀀스 실행.
    # block A 안에 자힐 burst 포함되어 있으므로 main burst 불필요 (burst_sec=0).
    # 임계치는 callable 이면 매 predicate 호출 시 실시간 값 조회 (스피너 반영).
    # 2026-04-20 Patch 2.18: cooldown_sec 4.0 → 0.0. HP 가 임계치 아래로
    # 유지되면 매 tick predicate 재평가 → 즉시 재발동. HpMpReader event push
    # (poll_sec 0.1s + notify_tick) 로 실제 지연은 OCR 시간 수준.
    # blocks_movement=True 가 시전 중 재진입 자동 차단 (동일 스레드).
    _self_heal_thr_initial = (
        int(self_heal_hp_thr()) if callable(self_heal_hp_thr)
        else int(self_heal_hp_thr)
    )
    skills.append(SkillSpec(
        "자힐",
        mainheal_vk,
        0.0,
        lambda c, _g=self_heal_hp_thr: _self_hp_below(c, _g),
        verify_kind=None,
        burst_sec=0,  # pre_block_ab 가 전체 시퀀스 담당.
        burst_interval_sec=0.1,
        retry_max=1,
        pre_block=pre_block_ab,
        post_block=None,
        priority=2,
        threshold=_self_heal_thr_initial,
        blocks_movement=True,
        edge_only=True,
    ))

    # 공력증강 — MP% < 임계치. 조건부 전환 (기존 NumLock 싸이클 제외).
    # 임계치 callable 지원.
    # 2026-04-20 리뉴얼: 공력증강은 쿨 없음. cooldown_sec=0.0 → 매 tick
    # predicate 재평가. MP 가 임계치 아래로 있는 한 연속 burst.
    # tick 간격은 scheduler.poll_sec(0.1) + HpMpReader event push 로 결정.
    _gyoung_thr_initial = (
        int(gyoungryeok_mp_thr()) if callable(gyoungryeok_mp_thr)
        else int(gyoungryeok_mp_thr)
    )
    skills.append(SkillSpec(
        "공력증강",
        int(vk_map.get("공력증강", _VK_NUMPAD3)),
        0.0,
        lambda c, _g=gyoungryeok_mp_thr: _self_mp_below(c, _g),
        verify_kind=None,
        burst_sec=1.0,
        burst_interval_sec=0.1,
        retry_max=2,
        priority=3,
        threshold=_gyoung_thr_initial,
        edge_only=True,
    ))

    # 파력무참: buff OCR 에서 "파력무참" 관측될 때까지 재시전. 백호의희원 방식.
    # 2026-04-22 사용자 요청: retry_until_ready=True. predicate 는 buff 부재시만
    # ready → 시전 성공(버프 뜸) 후 predicate False 로 차단. 버프 만료되면 다시
    # 부재 → 재진입. cooldown_sec=5 는 verify GIVEUP 시 backoff (백호 방식).
    skills.append(SkillSpec(
        "파력무참", int(vk_map.get("파력무참", _VK_NUMPAD8)),
        5.0, lambda c: not _buff_present(c, "파력무참"),
        offset_sec=parlyuk_offset_sec,
        verify_kind="buff",
        verify_target="파력무참",
        retry_until_ready=True,
        priority=10,
    ))

    # 백호의희원 / 첨 — 쿨 OCR 기반. retry_until_ready 로 verify 성공까지 반복.
    # cooldown_sec=0: verify 성공 후 OCR 쿨 관측되면 predicate 로 차단됨.
    # cooldown_sec=5.0 은 "verify 실패 포기 후 재진입 금지" backoff 용.
    # 정상 시전 + verify 성공 시엔 OCR 쿨 값으로 predicate 가 차단하므로
    # 이 값이 실질 영향 없음. OCR 인식 실패 등으로 MAX 시도 초과 → last_cast
    # 갱신 → 5초 후 재진입 허용 (그 사이 자힐/공증 등 edge 스킬 처리).
    skills.append(SkillSpec(
        "백호의희원", int(vk_map.get("백호의희원", _VK_NUMPAD4)),
        5.0, lambda c: _cd_empty(c, "백호의희원"),
        verify_kind="cooldown",
        verify_target="백호의희원",
        retry_until_ready=True,
        priority=11,
    ))
    skills.append(SkillSpec(
        "백호의희원첨", int(vk_map.get("백호의희원첨", _VK_NUMPAD5)),
        5.0, lambda c: _cd_empty(c, "백호의희원첨"),
        verify_kind="cooldown",
        verify_target="백호의희원첨",
        retry_until_ready=True,
        priority=11,
    ))

    # 파혼술 — 혼마술 감지 시 즉시 burst. 크로스-PC 트리거.
    # 2026-04-21: press_normal_vk 가 NumPad→main 숫자 변환해 게임이 안 먹던
    # 버그 수정. pre_block=cast_parhon_hook 로 NumPad scan 직접 송신.
    skills.append(SkillSpec(
        "파혼술", int(vk_map.get("파혼술", _VK_NUMPAD7)),
        0.0, lambda c: _attacker_debuff_present(c, "혼마술"),
        verify_kind=None,
        burst_sec=0,  # pre_block 이 burst 담당.
        retry_max=1,
        pre_block=cast_parhon_hook,
        priority=4,
        edge_only=True,
    ))

    # 금강불체 — 옵션 (기본 off). 쿨 없음. 활성화 시 edge 트리거 or 수동 호출.
    skills.append(SkillSpec(
        "금강불체", int(vk_map.get("금강불체", _VK_NUMPAD0)),
        0.0, lambda _c: False,
        enabled=False,
        verify_kind=None,
        burst_sec=0.8,
        retry_max=1,
        priority=12,
        edge_only=True,
    ))

    # 무장 — 격수 버프 "무장" 없을 때 Shift+Z → Shift+C 시퀀스.
    # edge_only=False + cooldown_sec=15 로 edge (즉시) + polling (15s 재시도)
    # 이중 동작. 격수 buff OCR 이 한 번 실패해도 다음 polling 때 재시전.
    skills.append(SkillSpec(
        "무장", 0, 15.0,
        lambda c: _attacker_buff_missing(c, "무장"),
        verify_kind=None,
        burst_sec=0,
        retry_max=1,
        pre_block=cast_mujang_hook,
        post_block=None,
        priority=5,
        edge_only=False,
    ))

    # 보호 — 격수 버프 "보호" 없을 때 Shift+Z → Shift+X 시퀀스.
    skills.append(SkillSpec(
        "보호", 0, 15.0,
        lambda c: _attacker_buff_missing(c, "보호"),
        verify_kind=None,
        burst_sec=0,
        retry_max=1,
        pre_block=cast_boho_hook,
        post_block=None,
        priority=5,
        edge_only=False,
    ))

    return skills
