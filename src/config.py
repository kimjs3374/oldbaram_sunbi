"""전역 설정. config.yaml 로드 + 기본값."""
from pathlib import Path
from dataclasses import dataclass, field
import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CFG = ROOT / "config.yaml"


@dataclass
class CaptureCfg:
    monitor_index: int = 1
    fps_target: int = 60


@dataclass
class VisionCfg:
    # ROOT 상대경로. dist 폴더 그대로 C:\oldbaram으로 옮겨도 동작.
    weights: str = str(ROOT / "dataset" / "runs" / "full_v3" / "weights" / "best.pt")
    imgsz: int = 640
    conf: float = 0.25
    iou: float = 0.5
    half: bool = True
    device: int = 0


@dataclass
class OcrCfg:
    coord_w: int = 105
    coord_h: int = 28
    coord_right_pad: int = 115
    coord_bottom_pad: int = 4
    coord_upscale: int = 4
    map_w: int = 400
    map_h: int = 40
    map_top_pad: int = 0
    # -1이면 (W-map_w)/2 중앙정렬. 양수면 그 값을 x1로 고정.
    # 옛바처럼 우측 UI 패널이 있는 화면에선 맵이름이 진짜 중앙이 아님 → 절대값 지정.
    map_left_pad: int = -1
    map_upscale: int = 3
    ocr_every_n_frames: int = 10
    # 맵 이름 OCR 스로틀 (초). PaddleOCR korean(CPU) 호출이 ~230ms 걸림.
    # ocr.read() 호출 주기(매 ocr_every_n_frames 프레임)보다 크게 잡아야
    # 매 호출마다 PaddleOCR 재실행을 막을 수 있음.
    # 2026-04-22: 맵 전환 지연으로 좌표-맵 불일치 창 길어짐 → 2.0s→0.5s (초당 2회).
    map_interval_s: float = 0.5


@dataclass
class NetCfg:
    port: int = 54545
    send_rate_hz: int = 30
    peers: list = field(default_factory=lambda: ["127.0.0.1"])
    bind_host: str = "0.0.0.0"
    # v5: 힐러 본인 인덱스 (peers 순서와 일치). 격수 GUI에서 힐러1/2/3 구분용.
    healer_idx: int = 0
    # v5: 격수 쿨다운 보고 수신 포트 (힐러→격수 역방향). 송신 포트와 별개.
    attacker_recv_port: int = 45455


@dataclass
class CooldownCfg:
    """힐러 화면 쿨다운 OCR 설정.

    region: (x, y, w, h) 스크린 좌표. -1이면 미지정(OCR 비활성).
    GUI "쿨 영역 지정" 모드에서 드래그로 설정 → _collect/_load_settings로 저장.
    nick_region_*: 닉네임 OCR 영역. 미지정 시 격수 GUI에 "힐러N" 표시.
    """
    region_x: int = -1
    region_y: int = -1
    region_w: int = 0
    region_h: int = 0
    poll_sec: float = 1.0  # OCR 주기 (1초 권장).
    nick_region_x: int = -1
    nick_region_y: int = -1
    nick_region_w: int = 0
    nick_region_h: int = 0
    # 버프창 영역 — 힐러/격수 공용 (PC마다 각자 지정).
    # - 힐러: 파력무참 지속시간 OCR → CooldownReport.buff_parlyuk_sec 로 송신.
    # - 격수: 혼마술 디버프 감시 → State.debuff_honmasul_sec 로 힐러 송신 →
    #   힐러 SkillScheduler 가 파혼술 자동 시전 (감지=격수, 시전=힐러 크로스오버).
    # 버프영역과 혼마술영역은 동일 영역 (사용자 2026-04-20 지시).
    buff_region_x: int = -1
    buff_region_y: int = -1
    buff_region_w: int = 0
    buff_region_h: int = 0
    # 2026-04-21: 채팅창 하단 "자리 바꿀 아이템[a-Z,a-Z ?]" 감지 영역.
    # 보호/무장 Shift+Z+C/X 시전 후 이 영역에 글자 감지되면 자리 바꾸기
    # 모드 진입으로 판정 → ESC 1회 자동 송신해 복구.
    chat_region_x: int = -1
    chat_region_y: int = -1
    chat_region_w: int = 0
    chat_region_h: int = 0
    # HP/MP 바 OCR 영역 (힐러/격수 공용 per-PC).
    # 격수 HP/MP 는 State.hp_pct/mp_pct 로 힐러에 전송 → 힐러 SkillScheduler
    # 가 격수부활 트리거에 사용. 힐러 자신 HP/MP 는 자힐/공력증강 트리거.
    hp_region_x: int = -1
    hp_region_y: int = -1
    hp_region_w: int = 0
    hp_region_h: int = 0
    mp_region_x: int = -1
    mp_region_y: int = -1
    mp_region_w: int = 0
    mp_region_h: int = 0
    # 사용자 입력 최대값 — OCR 이 "cur+max" 붙여서 읽는 걸 분리하는 용도 +
    # pct 환산 기준. 격수/힐러 PC 각각 자기 자신의 값을 지정.
    hp_max: int = 0
    mp_max: int = 0


@dataclass
class InputCfg:
    target_window: str = "msw.exe"   # .exe로 끝나면 프로세스명 매칭
    keydown_ms_min: int = 30
    keydown_ms_max: int = 70
    jitter_ms: int = 50
    method: str = "postmessage"  # sendinput | postmessage


@dataclass
class FsmCfg:
    red_lost_sec: float = 1.0          # 빨탭 1초 미검출 → 흰탭 판정
    stuck_sec: float = 3.0             # 좌표 3초 고정 → stuck
    dead_reckon_sec: float = 2.0       # 좌표 유실 → dead reckon 허용 시간


@dataclass
class Cfg:
    capture: CaptureCfg = field(default_factory=CaptureCfg)
    vision: VisionCfg = field(default_factory=VisionCfg)
    ocr: OcrCfg = field(default_factory=OcrCfg)
    net: NetCfg = field(default_factory=NetCfg)
    input: InputCfg = field(default_factory=InputCfg)
    fsm: FsmCfg = field(default_factory=FsmCfg)
    cooldown: CooldownCfg = field(default_factory=CooldownCfg)


def load(path: Path = DEFAULT_CFG) -> Cfg:
    if not path.exists():
        return Cfg()
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    cfg = Cfg()
    for key in ("capture", "vision", "ocr", "net", "input", "fsm", "cooldown"):
        if key in raw:
            sub = getattr(cfg, key)
            for k, v in raw[key].items():
                if hasattr(sub, k):
                    setattr(sub, k, v)
    return cfg
