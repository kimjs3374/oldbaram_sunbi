"""좌표/맵이름 OCR. 좌표=EasyOCR(GPU, 숫자), 맵=PaddleOCR TextRecognition(한국어).

속도 제약 때문에 두 모듈 사용:
- PaddleOCR CPU 단일 통합 시도했으나 predict 100ms → FPS 7 한계.
  CUDA 지원 paddle 빌드 없음(compiled_with_cuda=False).
- 좌표: EasyOCR GPU로 10-20ms.
- 맵: PaddleOCR(korean_PP-OCRv5_mobile_rec)로 정확도 확보. 0.5s throttle.
- 맵 crop은 _find_map_bar()로 tight crop → detection 불필요.
  PP-OCRv5_server_det 캐시 손상 문제 우회 위해 TextRecognition만 사용.
보정 레이어:
1) 숫자 분할 검증: contour 기반 digit count를 OCR 결과와 교차.
2) 연속성 필터: 같은 맵에서 Δ > jump_max 면 reject (이전 값 유지).
"""
import os
import pathlib
import re
import threading
import time
from dataclasses import dataclass
from typing import Optional, Tuple, List

# paddlepaddle onednn 관련 내부 버그 회피 (ConvertPirAttribute2RuntimeAttribute).
os.environ.setdefault("FLAGS_use_mkldnn", "0")
# 모델 소스 체크 스킵 (느림 + 오프라인 환경 대비).
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

import cv2
import numpy as np
from paddleocr import TextRecognition

# 프로젝트 루트 아래 models/에 동봉한 정상 모델을 직접 쓴다.
# huggingface 자동 다운로드는 xet 경로에서 빈 inference.json을 내려주는 경우가
# 있어 create_predictor가 parse_error로 실패. 로컬 파일 직지정으로 우회.
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
_KOREAN_REC_DIR = _PROJECT_ROOT / "models" / "korean_PP-OCRv5_mobile_rec"
# EasyOCR 모델을 배포에 동봉. 사용자 PC에서 첫 실행 시 인터넷 다운로드 방지.
# craft_mlt_25k.pth (detection) + english_g2.pth (recognition, 숫자용).
_EASYOCR_DIR = _PROJECT_ROOT / "models" / "easyocr"

COORD_RE = re.compile(r"(\d{3,4})\D+(\d{3,4})")
# 맵이름 앞에 들어가는 UI 아이콘/영문 노이즈 제거 (예: "lf) 대방성" → "대방성").
# 옛바 맵이름은 한글로 시작함. 첫 한글 글자 앞 모든 문자 잘라냄.
_MAP_PREFIX_RE = re.compile(r"^[^\uac00-\ud7a3]+")
# 맵이름 뒤 꼬리 UI 노이즈(영문) 제거.
# 괄호/하이픈은 lap suffix `(1)`, 서브맵 구분 `-2` 등 **의미 있는 문자** →
# 제거 금지. 이걸 제거하면 `선비족2-4(1)` → `선비족2-4` 로 탈락 → hunt_analytics
# 의 `_MAP_DUNGEON_RE` 매치 실패 → 바퀴(lap) 이벤트 생성 안 됨 → 리포트에
# laps=[] 고착 (2026-04-19 사용자 보고).
_MAP_SUFFIX_RE = re.compile(r"[^\uac00-\ud7a3\s\d\(\)\-]+$")


# PP-OCRv5 mobile rec 한국어 모델이 "흉" 같은 희귀 한글 1자 탈락시키는 케이스
# 실증(2026-04-20: "제4흉노족1" → "제4노족1"). 맵 이름 고정 집합을 seed 로
# 주입해 `_is_ocr_noise` 기반 canonical 복원이 첫 프레임부터 발동하도록.
# 새 맵 생기면 여기에 추가. 서브맵 suffix 는 1~9 까지 전개.
_DEFAULT_KNOWN_MAP_BASES = [
    "흉노족",
    "제2흉노족", "제3흉노족", "제4흉노족", "제5흉노족",
    "제6흉노족", "제7흉노족", "제8흉노족", "제9흉노족", "제10흉노족",
]


def _build_default_known_maps() -> set:
    out = set()
    for base in _DEFAULT_KNOWN_MAP_BASES:
        out.add(base)
        for i in range(1, 10):
            out.add(f"{base}{i}")
    return out


DEFAULT_KNOWN_MAPS = _build_default_known_maps()


# 2026-04-23 사용자 요청: knownmaps.txt 기반 canonical 복원.
# OCR이 한글자 누락(예: '흉'→공백)한 raw_m의 base 부분을 사용자 정의 리스트
# 에서 fuzzy 매칭 후 치환. 전체 full-name 사전 없이 base만으로 동작 → 서브맵
# suffix('5-5', '(1)', '입구', '적굴', 숫자 N) 임의 조합 모두 커버.
_USER_KNOWN_BASES_FILE = _PROJECT_ROOT / "knownmaps.txt"


def _load_user_known_bases(path: pathlib.Path) -> List[str]:
    """knownmaps.txt 파싱. 헤더([...]) / 번호섹션(1. 제목) / 빈줄 스킵.
    순수 base 이름만 리스트로 반환 (순서 유지 → 긴 이름 우선 매칭 용이하도록
    길이 내림차순 정렬).
    """
    bases: List[str] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                if s.startswith("["):
                    continue
                if re.match(r"^\d+\.", s):
                    continue
                bases.append(s)
    except FileNotFoundError:
        return []
    # 길이 내림차순 → '흑해골굴' 이 '해골굴' 보다 먼저 시도 (부분문자열 오매칭 방지).
    bases.sort(key=lambda b: (-len(b), b))
    return bases


_USER_KNOWN_BASES = _load_user_known_bases(_USER_KNOWN_BASES_FILE)

# 꼬리 패턴: 숫자(N), N-N, (N), 입구, 적굴 등 suffix 추출용.
# 맵 이름 = [제N 접두]? + base + [tail]?
_MAP_TAIL_RE = re.compile(r"(\d+(?:-\d+)?|\(\d+\)|입구|적굴)$")
_MAP_PREFIX_NUM_RE = re.compile(r"^(제\d+)(.*)$")


def _canonicalize_via_user_bases(raw: str) -> Optional[str]:
    """raw 맵 이름을 knownmaps.txt base로 canonicalize.

    분해: raw = [제N]? + base_raw + [tail]?
    base_raw를 _USER_KNOWN_BASES와 exact → edit-distance-1 순으로 매칭.
    매칭 성공 시 canonical base로 치환해 재조립. 실패 시 None.

    예:
      '제4노족5-5'  → pre='제4', base_raw='노족', tail='5-5'
                     → base 후보 '흉노족' edit-dist-1 매칭 → '제4흉노족5-5'
      '선비5-5(1)' → pre='',  base_raw='선비', tail='(1)' (5-5가 아닌 맨끝만)
                     → 한 번의 tail만으론 부족 → 재귀적으로 앞에서 5-5까지 잘라봄
    """
    if not raw or not _USER_KNOWN_BASES:
        return None
    # 접두 (제N) 추출
    pre_m = _MAP_PREFIX_NUM_RE.match(raw)
    if pre_m:
        pre, rest = pre_m.group(1), pre_m.group(2)
    else:
        pre, rest = "", raw
    # 꼬리(들) 순차 추출: N-N, (N), 입구, 적굴, 숫자 N 순으로 여러 번.
    tails: List[str] = []
    cursor = rest
    while True:
        m = _MAP_TAIL_RE.search(cursor)
        if not m or m.end() != len(cursor):
            break
        tails.append(m.group(0))
        cursor = cursor[: m.start()]
        if not cursor:
            break
    tails.reverse()
    tail = "".join(tails)
    base_raw = cursor
    if not base_raw:
        return None
    # exact
    if base_raw in _USER_KNOWN_BASES:
        return pre + base_raw + tail
    # edit distance 1 (기존 _is_ocr_noise 헬퍼 사용 — 한글 1자 sub/ins/del)
    for kb in _USER_KNOWN_BASES:
        if _is_ocr_noise(base_raw, kb):
            return pre + kb + tail
    return None


def _is_hangul(c: str) -> bool:
    return '\uac00' <= c <= '\ud7a3'


def _is_ocr_noise(a: str, b: str) -> bool:
    """두 맵명이 편집거리 1 이하인 OCR 노이즈/표기흔들림으로 간주.

    허용 1자 편집:
      - 한글 1자 치환 또는 삽입/삭제 (예: '선비5-5' ↔ '선비족5-5').
      - 하이픈 '-' 삽입/삭제 (예: '선비족2-4' ↔ '선비족24').
      - 공백 1자 삽입/삭제 (OCR이 공백 넣다/빼다 하는 케이스).
    숫자/괄호 차이는 실제 맵 전환 단서라 False.
    """
    if not a or not b or a == b:
        return a == b
    la, lb = len(a), len(b)
    # 길이차 >1이면 단일 글자 편집 불가
    if abs(la - lb) > 1:
        return False
    # substitution (같은 길이): 1자리 차이 + 한쪽 한글
    if la == lb:
        diff = [(ca, cb) for ca, cb in zip(a, b) if ca != cb]
        if len(diff) != 1:
            return False
        ca, cb = diff[0]
        return _is_hangul(ca) or _is_hangul(cb)
    # insertion/deletion: 긴 쪽이 한글/하이픈/공백 1자 삽입
    long_s, short_s = (a, b) if la > lb else (b, a)
    for i in range(len(long_s)):
        if long_s[:i] + long_s[i+1:] == short_s:
            ch = long_s[i]
            return _is_hangul(ch) or ch in ("-", " ")
    return False


def _clean_map_text(s: str) -> str:
    s = s.strip()
    s = _MAP_PREFIX_RE.sub("", s)
    s = _MAP_SUFFIX_RE.sub("", s)
    # 꼬리 단일 한글자 제거: "선비족입구 예" → "선비족입구".
    # PaddleOCR이 맵바 옆 UI 글자 1자(예/가/는 등)를 덧붙이는 케이스 대응.
    # 2자 이상 꼬리는 정상 맵 일부로 간주 유지 ("제2선비족 입구"의 "입구").
    # 본체(앞부분) 3자 이상일 때만 적용 → 짧은 맵 보호.
    parts = s.split()
    if len(parts) >= 2 and len(parts[-1]) == 1:
        head_len = sum(len(p) for p in parts[:-1])
        if head_len >= 3:
            s = " ".join(parts[:-1])
    s = s.strip()
    # 괄호 밸런스 복원: OCR이 끝 ')' 자주 탈락 → 격수/힐러 간 lap suffix 불일치.
    # 예) 격수 '선비족2-4(1)' vs 힐러 '선비족2-4(1' → trail 키 영구 미스매치.
    # '(' 개수 > ')' 개수면 부족한 만큼 뒤에 ')' 보충.
    # 반대(고립 ')')는 노이즈로 간주 끝에서만 제거.
    # 옛바 맵이름에 중첩 괄호 없음 → 단순 카운트로 충분.
    open_n = s.count('(')
    close_n = s.count(')')
    if open_n > close_n:
        s = s + ')' * (open_n - close_n)
    elif close_n > open_n:
        # 뒤쪽 연속 ')' 에서 초과분만큼만 제거 (정상 lap suffix 보존).
        m = re.search(r'\)+$', s)
        if m:
            excess = close_n - open_n
            tail_len = len(m.group(0))
            keep = max(0, tail_len - excess)
            s = s[:m.start()] + ')' * keep
    return s


def _map_similar(a: str, b: str) -> bool:
    """OCR 흔들림 허용 비교. 공통 글자 비율 >= 60%면 같은 맵으로 간주."""
    if not a or not b:
        return False
    if a == b:
        return True
    sa, sb = set(a), set(b)
    longer = max(len(sa), len(sb))
    if longer == 0:
        return False
    return len(sa & sb) / longer >= 0.6


@dataclass
class OcrResult:
    coord: Optional[Tuple[int, int]]  # (x, y) 또는 None
    map_name: str                      # 빈 문자열 가능
    raw_coord_text: str
    raw_map_text: str


def _segment_digit_boxes(patch_bgr: np.ndarray) -> List[Tuple[int, int, int, int]]:
    """좌표 crop → 이진화 → contour → digit bbox(x순)."""
    gray = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2GRAY) if patch_bgr.ndim == 3 else patch_bgr
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    if np.mean(bw) > 127:
        bw = 255 - bw  # 글자를 흰색으로
    contours, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    H = bw.shape[0]
    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if h < H * 0.35 or w < 2 or h < 6:
            continue
        boxes.append((x, y, w, h))
    boxes.sort(key=lambda b: b[0])
    return boxes


def _split_two_groups(boxes):
    """x-gap 최대 지점에서 두 그룹(x, y 좌표 자리수)으로 분리."""
    if len(boxes) < 2:
        return len(boxes), 0
    gaps = [(boxes[i + 1][0] - (boxes[i][0] + boxes[i][2]), i)
            for i in range(len(boxes) - 1)]
    g, idx = max(gaps)
    if g < 5:
        return len(boxes), 0
    return idx + 1, len(boxes) - idx - 1


class Ocr:
    def __init__(self, coord_w=105, coord_h=28, coord_right_pad=115,
                 coord_bottom_pad=4, coord_upscale=4,
                 map_w=400, map_h=40, map_top_pad=0, map_upscale=3,
                 map_left_pad=-1, gpu=True, coord_jump_max=4,
                 coord_reject_max=3, map_change_coord_hint=20,
                 map_interval_s=2.0, coord_interval_s=0.1):
        self.coord_w = coord_w
        self.coord_h = coord_h
        self.coord_right_pad = coord_right_pad
        self.coord_bottom_pad = coord_bottom_pad
        self.coord_upscale = coord_upscale
        self.map_w = map_w
        self.map_h = map_h
        self.map_top_pad = map_top_pad
        self.map_left_pad = map_left_pad
        self.map_upscale = map_upscale
        self.coord_jump_max = coord_jump_max
        # 연속 reject N회 초과 시 강제 수락: 이동 큰 상황에서 계속 reject되어
        # 좌표가 과거 값에 고착되는 현상 방지 (실측: 58,34 고착 12초 버그).
        self.coord_reject_max = coord_reject_max
        # 맵 OCR 지연 보정(2026-06-08): 좌표가 직전 대비 이 칸수 이상 점프 +
        # pending 에 새 known 맵 후보 존재 = 맵 경계 통과 신호 → pending 즉시 승격.
        # 근거: 같은맵 정상 이동 0~8칸(2016건)/21칸+ 전환(72건). 20이면 안전 분리.
        self.map_change_coord_hint = map_change_coord_hint
        self._reject_count = 0
        # 맵 OCR 스로틀: paddleocr가 40~100ms 튀어 루프 FPS를 깎으므로
        # 이 간격 안이면 이전 raw 텍스트 재사용. 맵 전환은 초 단위라 충분.
        self.map_interval_s = map_interval_s
        self._last_map_t = 0.0
        self._last_raw_map: str = ""
        self._last_coord: Optional[Tuple[int, int]] = None
        self._last_map: str = ""
        # OCR 디버그: OLDBARAM_OCR_DEBUG=1이면 coord crop을 N프레임마다 저장.
        # 사용자 PC에서 실제 crop이 어떻게 잘리는지 눈으로 확인용.
        self._debug_save = os.environ.get("OLDBARAM_OCR_DEBUG") == "1"
        self._debug_dir = pathlib.Path("logs") / "ocr_debug"
        self._debug_count = 0
        self._debug_every = 30
        self._last_coord_box = None
        # 맵 전환 확정용: 직전 OCR 결과가 새 값이었는지 기억.
        # 2회 연속 같은 새 값 → 교체 (1프레임 튄 OCR 무시).
        self._pending_map: str = ""
        # v5.16: 격수 UDP로 받는 known_maps 집합을 주입받아
        # 맵 전환 첫 프레임부터 canonical 강제 (OCR이 "족" 계속 놓치는 케이스 방어).
        # v5.17: DEFAULT_KNOWN_MAPS 로 seed. PP-OCRv5 mobile 한글 희귀자 탈락
        # ("흉"→공백) 방어. set_known_maps 는 DEFAULT 와 union 하여 유지.
        self._known_maps: set = set(DEFAULT_KNOWN_MAPS)
        # 좌표: EasyOCR. 2026-04-21 GPU 경합으로 9ms→800ms 로 튀는 현상 →
        # healer_worker 에서 gpu=False 로 CPU 강제 가능.
        # 진단 로그에 실제 device 명시 (사용자 복사 반영 여부 검증용).
        # 좌표 인식: 경량 CNN(digit_cnn.onnx) 우선. 있으면 EasyOCR(PyTorch)
        # 미로딩 → torch 회피 + 해상도 무관(augment 학습). 없으면 fallback.
        from .digit_cnn import DigitCnn
        _cnn_path = (pathlib.Path(__file__).resolve().parent
                     / "digit_cnn.onnx")
        self._digit_cnn = DigitCnn(_cnn_path)
        self._use_coord_cnn = self._digit_cnn.ready()
        self._easy_gpu = bool(gpu)
        if self._use_coord_cnn:
            self.digit = None
            self._easy_device_note = "cnn-onnx(no-torch)"
        else:
            import easyocr
            easy_kwargs = dict(gpu=gpu, verbose=False)
            if _EASYOCR_DIR.is_dir() and (_EASYOCR_DIR / "craft_mlt_25k.pth").exists():
                easy_kwargs["model_storage_directory"] = str(_EASYOCR_DIR)
                easy_kwargs["download_enabled"] = False
            self.digit = easyocr.Reader(["en"], **easy_kwargs)
            self._easy_device_note = (
                "GPU" if bool(gpu) else "CPU(forced)"
            )
        # 맵: PaddleOCR korean recognition. tight crop이라 detection 불필요.
        # 2026-04-21: CPU 강제. GPU 는 YOLO 전용.
        map_kwargs = dict(
            model_name="korean_PP-OCRv5_mobile_rec",
            enable_mkldnn=False,
        )
        if _KOREAN_REC_DIR.is_dir():
            map_kwargs["model_dir"] = str(_KOREAN_REC_DIR)
        self.map = None
        for _extra in [{"device": "cpu"}, {"use_gpu": False}, {}]:
            try:
                self.map = TextRecognition(**_extra, **map_kwargs)
                break
            except Exception:
                continue
        if self.map is None:
            self.map = TextRecognition(**map_kwargs)
        # warmup
        if self.digit is not None:
            self.digit.readtext(np.zeros((60, 400, 3), dtype=np.uint8),
                                allowlist="0123456789 ", detail=0)
        list(self.map.predict(np.zeros((48, 320, 3), dtype=np.uint8)))
        # 맵 CRNN (PaddleOCR 대체, 게임폰트 학습). 있으면 sync 경로에서 우선.
        try:
            from .map_crnn import MapCrnn
            _cd = pathlib.Path(__file__).resolve().parent
            _c = MapCrnn(_cd / "map_crnn.onnx", _cd / "map_crnn_charset.txt")
            self.map_crnn = _c if _c.ready() else None
        except Exception:
            self.map_crnn = None
        # 맵 OCR 비동기 워커 (선택). attach_map_worker 로 붙이면 read() 의
        # 맵 블록이 비블로킹으로 전환 (메인 루프에서 PaddleOCR predict 를
        # 호출하지 않음). sync fallback 은 _async_map is None 일 때 유지.
        self._async_map = None  # Optional[MapOcrWorker]
        # 프로파일: 매 read() 호출 분해 로그 (2026-04-20 FPS 10 원인 진단).
        # coord(EasyOCR) 1차 + retry N회 + map(PaddleOCR) 각 ms.
        self._prof_log_fn = None
        self._prof_every = 10   # N번 호출당 1회 로그 (10 FPS 기준 10초에 1회).
        self._prof_tick = 0

    def attach_map_worker(self, worker) -> None:
        """맵 OCR 을 비동기 워커로 위임. worker 는 MapOcrWorker 인스턴스.

        설정 후 read() 는 워커에 frame 을 submit 하고 latest() 로 최근 결과만
        폴링. 메인 루프에서 PaddleOCR predict 호출 없음 (블로킹 0).
        """
        self._async_map = worker

    def detach_map_worker(self) -> None:
        self._async_map = None

    def set_profile_log(self, fn, every: int = 10) -> None:
        """OCR 1회 호출당 단계별 ms 를 every 호출마다 fn(str) 으로 출력."""
        self._prof_log_fn = fn
        self._prof_every = max(1, int(every))
        self._prof_tick = 0

    def set_known_maps(self, names) -> None:
        """격수 UDP로 수신한 맵 이름 집합을 주입.
        OCR resolver가 첫 프레임부터 canonical 강제 교정에 사용.
        매 프레임 호출해도 set 연산만이라 부담 없음.

        DEFAULT_KNOWN_MAPS 는 항상 보존 (runtime 관측값이 덮어쓰지 않도록).
        """
        try:
            observed = set(names) if names else set()
        except Exception:
            observed = set()
        self._known_maps = set(DEFAULT_KNOWN_MAPS) | observed

    def _find_coord_box(self, img) -> Optional[Tuple[int, int, int, int]]:
        """우하단 좌표 영역을 자동 탐색.

        옛바 UI: 우하단에 "XXXX YYYY" 4+4 자리 숫자가 가장 아래 행에 배치.
        HP/MP 같은 다른 숫자도 같은 영역 위쪽에 있음 → '가장 아래의 숫자 행'만 선택.
        해상도 변경 대응: 우하단 H*0.15, W*0.22 영역 내에서 탐색.
        """
        H, W = img.shape[:2]
        search_h = max(60, int(H * 0.15))
        search_w = max(200, int(W * 0.22))
        x0 = W - search_w
        y0 = H - search_h
        roi = img[y0:, x0:]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        # 좌표 "XXXX YYYY"는 주황색 작은 글자(gray~150-190). HP/MP "1807"는
        # 하얀색 큰 글자(gray>230). 과거 > 200 단일 임계는 주황 좌표 누락 →
        # 다중 임계 스캔 후 박스 개수 많은 것 채택.
        boxes = []
        min_h = max(4, int(H * 0.005))
        max_h = max(20, int(H * 0.03))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        seen = set()
        # 도사 UI: HP/MP "510133"(흰색 큰 글자, gray>220) 위쪽,
        # 좌표 "0136 0022"(주황색 작은 글자, gray~130-170) 아래쪽.
        # 낮은 임계까지 스캔해서 주황 좌표 반드시 포함.
        for thr in (200, 170, 140, 110, 85):
            bright = (gray > thr).astype(np.uint8)
            closed = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, kernel)
            contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL,
                                            cv2.CHAIN_APPROX_SIMPLE)
            for c in contours:
                x, y, w, h = cv2.boundingRect(c)
                if h < min_h or h > max_h or w < 2 or w > max_h:
                    continue
                if w > h * 1.3:
                    continue
                if h > w * 4:
                    continue
                # 중복 박스 제거 (임계 달라도 같은 영역 중복 포함 방지).
                key = (x // 2, y // 2, w // 2, h // 2)
                if key in seen:
                    continue
                seen.add(key)
                boxes.append((x, y, w, h))
        if len(boxes) < 4:
            return None
        # 행 그룹핑: y 중심값이 가까운 박스들을 같은 행으로 묶음.
        # 과거엔 "최하단 행 중 >=4개 첫 행" 선택이라 HP/MP 같은 4자리 숫자가
        # 실제 좌표 "XXXX YYYY"(8자리) 위나 아래에 있으면 잘못 잡음.
        # (실측: 1306x705에서 "1807"(4개) 우선 선택되어 "0132 0074"(8개)가 밀림)
        rows: List[list] = []
        for b in boxes:
            cy = b[1] + b[3] / 2
            placed = False
            for row in rows:
                ref_cy = sum(rb[1] + rb[3] / 2 for rb in row) / len(row)
                ref_h = max(rb[3] for rb in row)
                if abs(cy - ref_cy) <= max(5, ref_h * 0.8):
                    row.append(b)
                    placed = True
                    break
            if not placed:
                rows.append([b])
        # 좌표는 항상 화면 최하단. HP/MP 숫자는 그 위쪽에 있음.
        # 우선순위: (1) 박스 6+ 인 행 중 최하단 (좌표 4+4=8, 최소 6),
        #           (2) 없으면 박스 4+ 행 중 최하단.
        # 박스 개수만 기준으로 하면 저임계에서 HP 행에 노이즈 섞여 역전 가능.
        by_bottom = lambda r: -max(b[1] + b[3] for b in r)
        cand6 = [r for r in rows if len(r) >= 6]
        if cand6:
            cand6.sort(key=by_bottom)
            chosen = cand6[0]
        else:
            cand4 = [r for r in rows if len(r) >= 4]
            if not cand4:
                return None
            cand4.sort(key=by_bottom)
            chosen = cand4[0]
        chosen.sort(key=lambda b: b[0])
        xs = [b[0] for b in chosen]
        xe = [b[0] + b[2] for b in chosen]
        ys = [b[1] for b in chosen]
        ye = [b[1] + b[3] for b in chosen]
        pad = 4
        # 얇은 숫자(1/7 등) contour 누락 대비: chosen row의 y 범위에서
        # 밝은 픽셀 실제 x 범위를 projection으로 재계산해 x min/max 보강.
        py1 = max(0, min(ys) - 2)
        py2 = min(search_h, max(ye) + 2)
        col_bright = (gray[py1:py2] > 200).any(axis=0)
        avg_h = int(sum(b[3] for b in chosen) / len(chosen))
        scan_lo = max(0, min(xs) - avg_h * 3)
        scan_hi = min(search_w, max(xe) + avg_h * 3)
        scan = col_bright[scan_lo:scan_hi]
        px_idx = np.where(scan)[0]
        if len(px_idx) > 0:
            proj_x1 = scan_lo + int(px_idx.min())
            proj_x2 = scan_lo + int(px_idx.max()) + 1
            x_min = min(min(xs), proj_x1)
            x_max = max(max(xe), proj_x2)
        else:
            x_min = min(xs)
            x_max = max(xe)
        rx1 = max(0, x_min - pad)
        ry1 = max(0, min(ys) - pad)
        rx2 = min(search_w, x_max + pad)
        ry2 = min(search_h, max(ye) + pad)
        return (x0 + rx1, y0 + ry1, x0 + rx2, y0 + ry2)

    def _crop_coord(self, img, thresh: int = 170):
        H, W = img.shape[:2]
        box = self._find_coord_box(img)
        if box is not None:
            x1, y1, x2, y2 = box
            c = img[y1:y2, x1:x2]
            self._last_coord_box = ("auto", x1, y1, x2, y2)
        else:
            x1 = W - self.coord_right_pad - self.coord_w
            y1 = H - self.coord_bottom_pad - self.coord_h
            c = img[y1:y1 + self.coord_h, x1:x1 + self.coord_w]
            self._last_coord_box = ("manual", x1, y1,
                                    x1 + self.coord_w, y1 + self.coord_h)
        if c.size == 0:
            c = img[H - 32:H, W - 160:W]
            self._last_coord_box = ("empty_fallback", W - 160, H - 32, W, H)
        # 숫자만 남기는 이진화 → UI 아이콘/나무잎 배경 제거. 번짐 방지.
        # thresh 파라미터: 1차 170 실패 시 140/200 등으로 재시도 가능.
        gray = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY) if c.ndim == 3 else c
        _, binary = cv2.threshold(gray, thresh, 255, cv2.THRESH_BINARY)
        # LANCZOS4: CUBIC보다 샤프함. 작은 숫자 확대에 적합.
        up = cv2.resize(binary, None,
                        fx=self.coord_upscale, fy=self.coord_upscale,
                        interpolation=cv2.INTER_LANCZOS4)
        return cv2.cvtColor(up, cv2.COLOR_GRAY2BGR)

    def _find_map_bar(self, img) -> Optional[Tuple[int, int, int, int]]:
        """상단에서 '맵이름 검정 띠'를 자동 탐색.

        조건:
          - 화면 상단 H*0.1 영역 내
          - 수평으로 연속된 검정(<30) 구간 폭 ≥ 80px
          - 좌상단/우상단 UI 영역 제외 (x가 W*0.15~W*0.75 사이에 중심)
          - 검정 띠 내부에 밝은(>150) 픽셀 존재 (텍스트가 있다는 신호)
        반환: (x1, y1, x2, y2) 또는 None.
        """
        # 옛바 맵 bar 구조: 검정 bar 양쪽이 어둡고 가운데 한글(흰색) 글자.
        # 한 행에서 "연속 검정 run"만 찾으면 글자 사이 공백이 바 전체를 끊어
        # 잘못된 위치를 잡음 → x-컬럼별 검정 비율로 글자 공백 포함해 전체 범위를
        # 하나로 인식한다 (실측 기반).
        H, W = img.shape[:2]
        search_h = max(40, int(H * 0.1))
        top = img[0:search_h]
        gray = cv2.cvtColor(top, cv2.COLOR_BGR2GRAY) if top.ndim == 3 else top
        dark = gray < 30
        # y 상단 1/3 구간을 컬럼별 검정 비율 측정 영역으로 사용.
        # y=0은 화면 테두리일 수 있으므로 1부터.
        y_probe_bot = max(8, search_h // 3)
        col_ratio = dark[1:y_probe_bot].mean(axis=0)
        mask = col_ratio >= 0.5
        # 연속 x 컬럼 세그먼트 추출.
        segs = []
        i = 0
        while i < W:
            if not mask[i]:
                i += 1
                continue
            s = i
            while i < W and mask[i]:
                i += 1
            segs.append((s, i))
        # 근접 세그먼트 병합 (글자 공백 <= 5% 화면 폭)
        max_gap = max(40, int(W * 0.05))
        merged = []
        for s, e in segs:
            if merged and s - merged[-1][1] <= max_gap:
                merged[-1] = (merged[-1][0], e)
            else:
                merged.append((s, e))
        # 중앙 prior 25~75% 내에서 최소 폭 8% 이상 후보만.
        cx_center = W // 2
        cx_lo, cx_hi = int(W * 0.25), int(W * 0.75)
        min_bar_w = max(80, int(W * 0.08))
        # 최대 폭: 화면 테두리(너비 >= 90%)는 제외.
        max_bar_w = int(W * 0.55)
        candidates = [(s, e) for s, e in merged
                      if cx_lo <= (s + e) // 2 <= cx_hi
                      and min_bar_w <= (e - s) <= max_bar_w]
        if not candidates:
            return None
        # 중앙에 가장 가까운 것 선택.
        candidates.sort(key=lambda se: abs((se[0] + se[1]) // 2 - cx_center))
        x1, x2 = candidates[0]
        # y 범위: 선택한 x 구간 내 평균 검정 비율 >= 0.4 인 y 연속 구간 중
        # 가장 긴 것.
        y_mean = dark[:, x1:x2].mean(axis=1)
        valid = y_mean >= 0.4
        y_segs = []
        i = 0
        while i < search_h:
            if not valid[i]:
                i += 1
                continue
            s = i
            while i < search_h and valid[i]:
                i += 1
            y_segs.append((s, i))
        if not y_segs:
            return None
        y_segs.sort(key=lambda se: -(se[1] - se[0]))
        y_top, y_bot = y_segs[0]
        # 여유 + 최소 높이 보장 (텍스트 전체 포함).
        y_top = max(0, y_top - 2)
        y_bot = min(H - 1, y_bot + 2)
        if y_bot - y_top < 18:
            y_bot = min(H - 1, y_top + 22)
        return (x1, y_top, x2, y_bot)

    def _crop_map(self, img):
        H, W = img.shape[:2]
        bar = self._find_map_bar(img)
        if bar is not None:
            x1, y1, x2, y2 = bar
            c = img[y1:y2, x1:x2]
        else:
            # fallback: 수동 설정 사용
            if self.map_left_pad is not None and self.map_left_pad >= 0:
                x1 = self.map_left_pad
            else:
                x1 = (W - self.map_w) // 2
            y1 = self.map_top_pad
            c = img[y1:y1 + self.map_h, x1:x1 + self.map_w]
        if c.size == 0:
            c = img[0:40, 0:400]
        # PaddleOCR은 원본 BGR이 가장 정확. 이진화는 "방"→"발" 같은 획 손상 유발.
        # 단, 컴팩트 게임창(격수 PC)에서 bar height < 48px 이면 PP-OCRv5 mobile
        # rec 권장(48px) 미달로 복잡획 글자 탈락("흉"→공백, 2026-04-20 실측).
        # LANCZOS4 업스케일 + unsharp mask 로 획 보존. 큰 창(힐러 PC)에선 no-op.
        try:
            h0 = int(c.shape[0])
            self._last_map_crop_h = h0
            self._last_map_crop_scale = 1
            if h0 < 48:
                scale = max(3, 96 // max(1, h0))
                new_w = int(c.shape[1] * scale)
                new_h = int(c.shape[0] * scale)
                c = cv2.resize(c, (new_w, new_h),
                               interpolation=cv2.INTER_LANCZOS4)
                self._last_map_crop_scale = scale
                try:
                    blur = cv2.GaussianBlur(c, (0, 0), 1.0)
                    c = cv2.addWeighted(c, 1.6, blur, -0.6, 0)
                except Exception:
                    pass
        except Exception:
            pass
        # 디버그: 매 20회 1장씩 crop 저장 (logs/map_crops/). 원인 진단용.
        try:
            self._map_dbg_n = getattr(self, "_map_dbg_n", 0) + 1
            if self._map_dbg_n % 20 == 1:
                dbg_dir = _PROJECT_ROOT / "logs" / "map_crops"
                dbg_dir.mkdir(parents=True, exist_ok=True)
                stamp = time.strftime("%H%M%S")
                h_ = int(getattr(self, "_last_map_crop_h", 0))
                s_ = int(getattr(self, "_last_map_crop_scale", 1))
                fn = f"{stamp}_n{self._map_dbg_n:04d}_h{h_}_x{s_}.png"
                cv2.imwrite(str(dbg_dir / fn), c)
        except Exception:
            pass
        return c

    @staticmethod
    def _extract_texts(preds) -> List[str]:
        """PaddleOCR TextRecognition 결과에서 rec_text 문자열만 뽑기."""
        out: List[str] = []
        for r in preds:
            texts = None
            if hasattr(r, "get"):
                texts = r.get("rec_texts")
                if texts is None:
                    t = r.get("rec_text")
                    texts = [t] if t else []
            if not texts:
                try:
                    t = r["rec_text"]
                    texts = [t] if t else []
                except Exception:
                    texts = []
            out.extend([x for x in texts if x])
        return out

    def _read_digits_cnn(self, cc) -> Tuple[str, str]:
        """좌표 숫자를 경량 CNN으로 인식 (해상도 무관, torch 불필요, 배치).

        _segment_digit_boxes 로 숫자 분리 → 각 박스를 CNN(onnx)으로 0~9 분류.
        """
        boxes = _segment_digit_boxes(cc)  # x순 정렬
        patches = [cc[y:y + h, x:x + w] for (x, y, w, h) in boxes]
        labels = self._digit_cnn.predict(patches)
        s = "".join(str(d) for d in labels)
        return s, s

    def _read_digits_template(self, cc) -> Tuple[str, str]:
        """좌표 숫자를 템플릿 매칭으로 인식 (EasyOCR 대체, ~1ms, torch 불필요).

        _segment_digit_boxes 로 숫자 분리 → 각 박스를 0~9 템플릿과 SSD 매칭.
        """
        boxes = _segment_digit_boxes(cc)  # x순 정렬
        out = []
        for (x, y, w, h) in boxes:
            patch = cc[y:y + h, x:x + w]
            d = self._coord_matcher.match(patch)
            if d is not None:
                out.append(str(d))
        s = "".join(out)
        return s, s

    def _read_digits(self, cc) -> Tuple[str, str]:
        """EasyOCR 숫자 읽기. (raw_text, digits_only).

        readtext = detection(CRAFT) + recognition 2단 풀 파이프라인.
        좌표 crop은 이미 tight 하므로 detection 재실행 불필요. 그런데도
        readtext 쓰면 CRAFT가 랜덤하게 150-260ms 튀는 스파이크 발생
        (실측 2026-04-20 힐러1.txt: coord1=173.7/262.2ms 스파이크).
        recognize() 에 horizontal_list 로 전체 박스 지정해 detection 스킵
        → 평상 속도가 곧 최악 속도 (수십 ms 내).
        EasyOCR API 변경 대비 readtext fallback.
        """
        if getattr(self, "_use_coord_cnn", False):
            return self._read_digits_cnn(cc)
        try:
            H, W = cc.shape[:2]
            gray = cv2.cvtColor(cc, cv2.COLOR_BGR2GRAY) if cc.ndim == 3 else cc
            ct = self.digit.recognize(
                gray,
                horizontal_list=[[0, W, 0, H]],
                free_list=[],
                allowlist="0123456789 ",
                detail=0,
                paragraph=False,
            )
        except Exception:
            ct = self.digit.readtext(cc, allowlist="0123456789 ",
                                     detail=0, paragraph=False)
        raw = " ".join(ct) if ct else ""
        digits = re.sub(r"\D", "", raw)
        return raw, digits

    def _filter_coord_jump(self, coord, cmp_m):
        """같은맵 좌표 점프 필터 — 축별 독립 클램프.

        반환: 보정된 좌표(또는 reject 시 None). 부수효과로 _reject_count/
        _last_coord 갱신. coord/last 둘 중 None이면 그대로 통과.

        축별 클램프 (2026-06-10 v11): 좌표 OCR ~10-15Hz(65~100ms 주기)라
        같은맵 정상 프레임간 이동은 측정상 ≤7타일(99%가 ≤3). 한 축만 jump_max
        초과 = 그 축 CNN 숫자 오독(다른 축은 정상 단조). 튀는 축만 직전값
        유지하고 정상 축은 수락 → x↔진동 제거하며 추종 유지.
        (healer-37 2026-06-10 15:20 x 1↔18 진동·y 정상단조 16s 정체 대응.
        기존 60은 same_map에 무의미하게 커 d=17 진동이 통과했음.)
        양축 동시 초과만 reject(좌표계 변화 가능) + 연속이면 강제수락(고착 방지).
        """
        if coord is None or self._last_coord is None:
            return coord
        same_map = (bool(cmp_m) and bool(self._last_map)
                    and cmp_m == self._last_map)
        if not same_map:
            # 맵이 다름(또는 OCR 맵 미상) — jump 검사 스킵, 새 맵 좌표 수락.
            # 이전 맵 _last_coord는 무효 → 리셋.
            self._reject_count = 0
            if bool(cmp_m) and bool(self._last_map):
                self._last_coord = None
            return coord
        # 맵 OCR 지연 보호: 같은맵명인데 pending에 새 정상맵 후보 존재 =
        # 맵 경계 통과 중(옛 맵명 + 새 맵 좌표) → jump 필터 스킵해 새 좌표
        # 보존. 아래 map_change_coord_hint 블록이 pending 승격 처리.
        # (jump_max를 10으로 낮추면서 (22,1)→(9,28) 류 맵전환 점프가
        # 막히지 않도록. v11. v13: 멤버십→_is_admissible_map(base 검증)로 완화,
        # 격수 새맵 닭-달걀 회귀 대응.)
        if (self._pending_map and self._pending_map != self._last_map
                and self._is_admissible_map(self._pending_map)):
            self._reject_count = 0
            return coord
        lx, ly = self._last_coord
        nx, ny = coord
        jx = abs(nx - lx) > self.coord_jump_max
        jy = abs(ny - ly) > self.coord_jump_max
        if jx or jy:
            # 옛바는 1칸씩 이동 → 같은맵 좌표 OCR 프레임간 정상 d는 작음
            # (측정 99%가 ≤3). jump_max 초과 = CNN 좌표 자릿수 오독.
            # 실증(2026-06-11 02:19:31): raw='00010004' — 정상 '0019xxxx'를
            # CNN이 19→01, 9→04 로 오독해 (1,4) 유입. 직전 (19,9) 대비 y=5
            # 점프였는데 jump_max=10 이라 새 통과 → (19,4) 가짜좌표 송신.
            # → 급변 축만 직전값 클램프해 노이즈 제거(양축 동시면 둘 다 유지).
            #   N프레임 연속 초과면 실제 이동(빠른이동/좌표계 변화)으로 강제수락
            #   (고착 방지). 양축도 None 대신 클램프 → 추종 끊김 없이 옛 좌표 유지.
            self._reject_count += 1
            if self._reject_count <= self.coord_reject_max:
                return (lx if jx else nx, ly if jy else ny)
            self._reject_count = 0
            return coord
        self._reject_count = 0
        return coord

    def _is_admissible_map(self, raw_m: str) -> bool:
        """raw_m 을 새 맵으로 승격해도 되는 정상 맵명인지 (v13 2026-06-10).

        ① known_maps 미설정(standalone) → 허용(기존 동작).
        ② known_maps 멤버 → 허용 (빠른 경로).
        ③ knownmaps.txt base 로 canonical 복원 가능 → 허용. 격수가 처음 보는
           맵도 base 만 정상이면 인정 — 격수는 known_maps를 자기 OCR로 채우므로
           멤버십만 요구하면 새 맵을 영영 못 읽는 닭-달걀에 빠짐(v10 회귀).
           오독('전미국22')은 base 매칭 실패라 여기서 거부됨.
        """
        if not self._known_maps:
            return True
        if raw_m in self._known_maps:
            return True
        if _USER_KNOWN_BASES and _canonicalize_via_user_bases(raw_m) is not None:
            return True
        return False

    def _gate_map_name(self, raw_m: str) -> str:
        """맵 이름 pending 게이트 — raw OCR을 채택맵으로 확정/거부.

        반환: 채택된 맵 이름(_last_map). 부수효과로 _last_map/_pending_map 갱신.

        화이트리스트 거부 (2026-06-10 v10): 격수 known_maps는 신뢰 정답집합.
        그 밖의 값은 2프레임 연속이어도 새 맵으로 승격하지 않고 직전 맵 유지.
        교정(이름 바꿔치기)이 아닌 거부라 새 오류를 못 만든다 —
        오독('전미국22(1) (공)' 등)은 정의상 known_maps 밖이라 100% 차단.
        (healer-37 2026-06-10 15:05 오독 9연발이 2프레임 게이트를 뚫고
        healer_map으로 채택돼 trail 토글·MAP-SEQ 가짜발화 유발한 사고 대응.)
        _known_maps가 비면(standalone/격수 데이터 미수신) 기존 동작 유지.
        """
        if not self._last_map:
            self._last_map = raw_m
            self._pending_map = ""
        elif raw_m == self._last_map:
            self._pending_map = ""
        elif _is_ocr_noise(raw_m, self._last_map):
            # OCR 1글자 한글 노이즈(예: '선비5-5(5' ↔ '선비족5-5(5').
            # 가짜 MAP-SEQ-EDGE 차단 + canonical 매핑.
            # v5.17: known_maps 멤버십 우선. 한쪽만 known_maps 에 있으면
            # 그쪽으로 정렬(격수 정답 우선). 둘 다 있거나 둘 다 없으면
            # 긴 쪽 채택(한글 OCR 글자 누락 ≫ 삽입).
            r_known = raw_m in self._known_maps
            l_known = self._last_map in self._known_maps
            if r_known and not l_known:
                self._last_map = raw_m
            elif l_known and not r_known:
                raw_m = self._last_map
            elif len(raw_m) > len(self._last_map):
                self._last_map = raw_m
            else:
                raw_m = self._last_map
            self._pending_map = ""
        elif raw_m == self._pending_map and self._is_admissible_map(raw_m):
            # v13 (2026-06-10): 2프레임 연속 + "정상 맵명"이면 승격.
            # 정상 = known_maps 멤버 OR knownmaps.txt base 복원가능.
            # v10은 멤버십만 봐서 격수(known_maps를 자기 OCR로 채움)가 처음
            # 가는 맵을 영영 거부 → '선비족입구' 고착 회귀(닭-달걀). base 검증
            # 추가로 정상 새맵('선비족2')은 통과, 오독('전미국22')은 base 탈락.
            self._last_map = raw_m
            self._known_maps.add(raw_m)  # 자동 학습 → 다음부턴 멤버(빠른경로)
            self._pending_map = ""
        else:
            # 미확인 후보(오독 포함) — 직전 맵 유지(hold). pending에만 기록해
            # 진짜 새 맵이면 다음 프레임에 멤버십 통과 시 승격.
            self._pending_map = raw_m
            raw_m = self._last_map
        return raw_m

    def read(self, frame: np.ndarray) -> OcrResult:
        # 방어: 좌표 OCR은 밝기/오버레이 간섭에 민감 → 1차 실패 시 다른 threshold로
        # 자동 재시도. 자릿수 많은 쪽 채택. (실측: (58,34) 고착 재현 방지)
        _prof_t0 = time.perf_counter()
        _prof_coord_retry_n = 0
        _prof_coord_retry_ms = 0.0
        _prof_map_ms = 0.0
        _prof_map_hit = False
        _prof_t_c0 = time.perf_counter()
        cc = self._crop_coord(frame, thresh=170)
        raw_c, digits = self._read_digits(cc)
        _prof_coord_primary_ms = (time.perf_counter() - _prof_t_c0) * 1000
        # 디버그 크롭 저장 (OLDBARAM_OCR_DEBUG=1 + 30프레임 간격)
        if self._debug_save:
            self._debug_count += 1
            if self._debug_count % self._debug_every == 1:
                try:
                    self._debug_dir.mkdir(parents=True, exist_ok=True)
                    stamp = time.strftime("%H%M%S")
                    box = self._last_coord_box or ("?",)
                    fn = (f"{stamp}_n{self._debug_count:04d}_"
                          f"{box[0]}_raw{raw_c or 'EMPTY'}.png")
                    fn = re.sub(r"[^0-9A-Za-z_\.\-]", "_", fn)
                    cv2.imwrite(str(self._debug_dir / fn), cc)
                    # 원본 프레임 우하단도 한 번 저장 (전체 맥락 확인용).
                    if self._debug_count == 1:
                        H, W = frame.shape[:2]
                        full = frame[int(H * 0.8):, int(W * 0.6):]
                        cv2.imwrite(
                            str(self._debug_dir / f"{stamp}_fullBR.png"), full)
                except Exception:
                    pass
        if len(digits) < 6:
            for alt_thresh in (140, 200):
                _prof_t_r0 = time.perf_counter()
                cc_alt = self._crop_coord(frame, thresh=alt_thresh)
                raw_alt, digits_alt = self._read_digits(cc_alt)
                _prof_coord_retry_ms += (time.perf_counter() - _prof_t_r0) * 1000
                _prof_coord_retry_n += 1
                if len(digits_alt) > len(digits):
                    cc, raw_c, digits = cc_alt, raw_alt, digits_alt
                if len(digits) >= 6:
                    break
        # 맵 OCR: 워커 attached 면 비동기(메인 블로킹 0). 아니면 기존 sync.
        if self._async_map is not None:
            # async: 매 read() 마다 최신 프레임 submit + 최근 완료 결과 poll.
            # 워커 predict 는 별도 스레드 → 메인 루프는 약 <1ms 로 통과.
            try:
                self._async_map.submit_frame(frame)
            except Exception:
                pass
            try:
                rd = self._async_map.latest()
                worker_ts = float(getattr(rd, "ts", 0.0))
                worker_raw = str(getattr(rd, "raw", "") or "")
                worker_cycle = float(getattr(rd, "cycle_ms", 0.0))
            except Exception:
                worker_ts = 0.0
                worker_raw = ""
                worker_cycle = 0.0
            if worker_ts > self._last_map_t:
                _prof_map_hit = True
                _prof_map_ms = worker_cycle
                raw_m = _clean_map_text(worker_raw)
                self._last_raw_map = raw_m
                self._last_map_t = worker_ts
            else:
                raw_m = self._last_raw_map
        else:
            # sync fallback: 구버전/워커 없을 때. map_interval_s 스로틀.
            now = time.time()
            if now - self._last_map_t >= self.map_interval_s:
                _prof_map_hit = True
                _prof_t_m0 = time.perf_counter()
                mc = self._crop_map(frame)
                # CRNN 우선 (게임폰트 학습). 빈값이면 PaddleOCR fallback.
                _raw = ""
                if getattr(self, "map_crnn", None) is not None:
                    try:
                        _raw = self.map_crnn.predict(mc) or ""
                    except Exception:
                        _raw = ""
                if not _raw:
                    _raw = " ".join(self._extract_texts(self.map.predict(mc)))
                _prof_map_ms = (time.perf_counter() - _prof_t_m0) * 1000
                raw_m = _clean_map_text(_raw)
                self._last_raw_map = raw_m
                self._last_map_t = now
            else:
                raw_m = self._last_raw_map
        # 디버그용: pending 로직 적용 전 원본 fresh OCR. attacker/healer 로그로 노출.
        raw_m_fresh = raw_m
        # 맵 전환 판정: _clean_map_text가 꼬리 단일 한글자 노이즈를 이미 제거.
        # 남은 접두사 확장(예: "대방성" → "대방성입구")은 진짜 전환으로 간주,
        # pending 메커니즘으로 1프레임 확인 후 교체.
        if raw_m:
            # 2026-04-23 knownmaps.txt 기반 canonical 복원 (v5.18).
            # user base 사전으로 raw_m의 base 부분 복구. suffix(5-5, (1), 입구 등)
            # 와 무관하게 base edit-dist-1 매칭으로 canonical 강제.
            # attacker/healer OCR이 같은 글자(흉) 동시 탈락 케이스도 이 층에서 교정.
            if _USER_KNOWN_BASES:
                canon = _canonicalize_via_user_bases(raw_m)
                if canon and canon != raw_m:
                    raw_m = canon
            # v5.16: 격수 known_maps 우선 canonical 교정.
            # raw_m이 known_maps에 없고, known_maps 중 raw_m의 "한글자 누락본"
            # 관계(=_is_ocr_noise True)인 더 긴 후보가 있으면 즉시 치환.
            # 맵 전환 첫 프레임부터 h_map = canonical 보장 → no_trail 고착 원천 차단.
            # v5.15는 정상 OCR이 "이후에" 와야 self-heal이었지만 OCR이 영구
            # 글자 누락하는 케이스(11:17:58 실증)에선 발동 못 함.
            if self._known_maps and raw_m not in self._known_maps:
                # v5.17: 같은 길이 치환('흡'↔'흉' 등)도 처리. 길이 무관하게
                # _is_ocr_noise (편집거리 ≤1) 후보를 찾고, 긴 쪽 우선 + 동률은
                # 첫 매칭. OCR 원문 어떤 형태든 격수 known_maps 이름으로 강제
                # 귀속. 2026-04-20 '제4흡노족1' → '제4흉노족1' 실증.
                best = ""
                for km in self._known_maps:
                    if _is_ocr_noise(raw_m, km):
                        if len(km) > len(best) or not best:
                            best = km
                if best:
                    raw_m = best
            raw_m = self._gate_map_name(raw_m)
        # 좌표 파싱: EasyOCR이 "0076"을 "007 6"처럼 공백 삽입하는 케이스 있어
        # regex 단독으론 마지막 자리 손실. contour 개수로 직접 분할.
        coord: Optional[Tuple[int, int]] = None
        boxes = _segment_digit_boxes(cc)
        n_x, n_y = _split_two_groups(boxes)
        total_boxes = n_x + n_y
        digits = re.sub(r"\D", "", raw_c)
        # 1순위: contour 분할(좌 n_x자리, 우 n_y자리)이 3-4자리씩이고
        #        OCR 자릿수가 일치하면 그대로 분할.
        if n_x >= 3 and n_y >= 3 and len(digits) == total_boxes:
            coord = (int(digits[:n_x]), int(digits[n_x:]))
        else:
            # 2순위: regex fallback (공백 정상일 때).
            m = COORD_RE.search(raw_c)
            if m:
                coord = (int(m.group(1)), int(m.group(2)))
        # 검증: OCR 자릿수가 contour 개수보다 1개 이상 적으면 누락 의심 → reject.
        if coord is not None and total_boxes >= 4:
            if len(digits) < total_boxes - 1:
                coord = None

        # 연속성 필터: 같은 맵에서 한 프레임에 너무 큰 점프면 reject.
        # 단, 연속 reject가 coord_reject_max 초과하면 강제 수락(좌표 고착 방지).
        #
        # 버그 수정(2026-04-14 2차): 위쪽 pending 메커니즘이 raw_m을 _last_map으로
        # **덮어씌움** → 맵 전환 첫 프레임에 same_map=True 오판 → 새 맵 좌표
        # reject → stale 고착 → 왔다갔다 지속. 해결: 덮어쓰기 전 원본 raw_m_fresh로
        # 판정. raw_m_fresh가 _last_map과 다르면 실제 맵 전환이므로 jump 검사 스킵.
        # 또한 맵 달라진 경우 _last_coord를 None으로 리셋 (다음 프레임에 새 좌표로
        # 재설정) — 이전 맵 좌표가 계속 비교 기준으로 살아있지 않도록.
        coord = self._filter_coord_jump(
            coord, raw_m_fresh if raw_m_fresh else raw_m)

        # 맵 OCR 지연 보정(2026-06-08): 격수 맵 이름 OCR이 좌표보다 늦게 따라오는
        # 경우, pending 게이트(2프레임 연속 요구)가 1~수초간 옛 맵명을 유지 →
        # 힐러가 '옛 맵명 + 새 맵 좌표'를 받아 같은-맵 거짓 점프로 오판
        # (healer 213226 로그: 선비족2 (22,1)→(9,28) d=40 jump reject 다발).
        # 좌표가 직전 대비 크게 점프 + pending 에 새 정상 맵 후보가 동시에 존재 =
        # 맵 경계 통과 신호 → pending 즉시 승격해 좌표와 같은 프레임에 새 맵명 송신.
        # v13: 멤버십 → _is_admissible_map(base 검증)로 완화 (격수 새맵 닭-달걀).
        if (coord is not None and self._last_coord is not None
                and self._pending_map
                and self._pending_map != self._last_map
                and self._is_admissible_map(self._pending_map)):
            mv = (abs(coord[0] - self._last_coord[0])
                  + abs(coord[1] - self._last_coord[1]))
            if mv >= self.map_change_coord_hint:
                self._last_map = self._pending_map
                self._known_maps.add(self._pending_map)
                raw_m = self._pending_map
                self._pending_map = ""

        if coord is not None:
            self._last_coord = coord
        # [OCR-PROF] 단계별 ms: every 호출마다 1회 로그.
        # coord1=EasyOCR 1차, retry=재시도 N회+총ms, map=PaddleOCR (스킵 가능).
        try:
            if self._prof_log_fn is not None:
                self._prof_tick += 1
                if (self._prof_every > 0
                        and self._prof_tick % self._prof_every == 0):
                    _prof_total_ms = (time.perf_counter() - _prof_t0) * 1000
                    map_str = (f"{_prof_map_ms:.1f}ms"
                               if _prof_map_hit else "skip")
                    self._prof_log_fn(
                        f"[OCR-PROF] coord1={_prof_coord_primary_ms:.1f}ms "
                        f"retry={_prof_coord_retry_n}+"
                        f"{_prof_coord_retry_ms:.1f}ms "
                        f"map={map_str} total={_prof_total_ms:.1f}ms "
                        f"digits={len(digits)}"
                    )
        except Exception:
            pass
        # OCR 실패 시 None 반환. 호출자(attacker.py 등)가 이전 값 유지 결정.
        # 과거엔 _last_coord 숨김 반환이라 호출자가 실패를 인지 못 해
        # "좌표 고착" 버그 발생.
        return OcrResult(coord=coord, map_name=raw_m,
                         raw_coord_text=raw_c, raw_map_text=raw_m_fresh)


class AsyncOcr:
    """Ocr 를 백그라운드 스레드로 감쌈.

    2026-04-21: EasyOCR 좌표 OCR 이 9ms → 885ms 로 튀어 메인 루프를 직접
    블로킹 → FPS 27 → 5 로 급락. AsyncOcr 는 메인 루프를 차단하지 않고
    백그라운드에서 read() 를 돌려 FPS 를 고정한다.
    - submit(frame): 최신 frame 덮어쓰기 (큐 쌓지 않음).
    - latest(): 마지막 OcrResult 반환 (없으면 None).
    - known_maps / profile_log 는 래퍼가 위임.
    """

    def __init__(self, ocr: "Ocr"):
        self._ocr = ocr
        self._pending: Optional = None
        self._pending_lock = threading.Lock()
        self._latest: Optional[OcrResult] = None
        self._latest_lock = threading.Lock()
        self._last_predict_ms: float = 0.0
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name="async-ocr", daemon=True
        )
        self._thread.start()

    def set_known_maps(self, keys):
        try:
            self._ocr.set_known_maps(keys)
        except Exception:
            pass

    def set_profile_log(self, fn, every: int = 10):
        try:
            self._ocr.set_profile_log(fn, every)
        except Exception:
            pass

    def attach_map_worker(self, worker):
        try:
            self._ocr.attach_map_worker(worker)
        except Exception:
            pass

    def submit(self, frame):
        """최신 frame 을 백그라운드 큐에 전달 (덮어쓰기)."""
        if frame is None:
            return
        with self._pending_lock:
            self._pending = frame
        self._wake.set()

    def latest(self) -> Optional[OcrResult]:
        with self._latest_lock:
            return self._latest

    def last_predict_ms(self) -> float:
        return float(self._last_predict_ms)

    def stop(self):
        self._stop.set()
        self._wake.set()
        try:
            self._thread.join(timeout=1.0)
        except Exception:
            pass

    def _loop(self):
        while not self._stop.is_set():
            self._wake.wait(timeout=0.2)
            if self._stop.is_set():
                break
            self._wake.clear()
            with self._pending_lock:
                frame = self._pending
                self._pending = None
            if frame is None:
                continue
            t0 = time.perf_counter()
            try:
                r = self._ocr.read(frame)
            except Exception:
                r = None
            dt_ms = (time.perf_counter() - t0) * 1000.0
            self._last_predict_ms = dt_ms
            if r is not None:
                with self._latest_lock:
                    self._latest = r
