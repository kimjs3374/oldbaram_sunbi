"""맵바/텍스트 OCR — RapidOCR(korean PP-OCR ONNX). 단일 OCR 엔진.

일반 OCR이라 숫자 조합 무관(x,y,z 학습 불필요), 한글 base도 정확.
실측: 한글+숫자 ~100%(끝 영문 G 후처리). onnxruntime CPU.
모델: korean_rec.onnx(13MB) + korean_dict.txt (같은 폴더).

맵명 보정 (2026-06-11 사용자 지시):
- 노이즈 제거: 맵명 유효 문자(한글/숫자/괄호/하이픈) 외 전부 삭제.
- 선비족 X-Y(Z) 구조 정규화: 격수/힐러 맵키가 글자 단위로 일치해야 trail/
  MAP-SEQ 가 동작. RapidOCR raw 는 정확하나 끝 영문(횃불→G)·괄호 한쪽 탈락
  같은 구조 노이즈가 가끔 끼므로 여기서만 정리. base 글자는 절대 바꾸지 않음
  (knownmaps 기반 base 보정은 ocr.py 가 담당 — 과보정 방지 책임 분리).
"""
import os
import re
import pathlib

_DIR = pathlib.Path(__file__).resolve().parent
_REC = _DIR / "korean_rec.onnx"
_DICT = _DIR / "korean_dict.txt"
_engine = None
_init_failed = False


def _intra_threads() -> int:
    """RapidOCR intra_op 스레드 수 (기본 2, env OB_OCR_INTRA_THREADS 조정).

    YOLO(onnx)·RapidOCR·digit_cnn 가 각자 전체코어를 잡으면 CPU 과구독으로
    YOLO predict spike → OCR 계열은 2스레드로 캡해 코어를 YOLO에 양보.
    rec-only(use_det=False)는 2스레드로도 ~14ms (전체코어 12ms와 사실상 동일).
    """
    try:
        return max(1, int(os.environ.get("OB_OCR_INTRA_THREADS", "2")))
    except Exception:
        return 2

# 맵명 유효 문자: 한글/숫자/괄호/하이픈. 끝 영문(횃불 오인 G 등) 제거.
_KEEP = re.compile(r"[^가-힣0-9()\-]")


def ready() -> bool:
    return _REC.exists() and _DICT.exists()


def _get_engine():
    global _engine, _init_failed
    if _engine is not None or _init_failed:
        return _engine
    try:
        from rapidocr_onnxruntime import RapidOCR
        # intra_op 스레드 캡 — det/cls/rec 세션 전부 동일 적용(전체코어 점유 방지).
        _nt = _intra_threads()
        _engine = RapidOCR(rec_model_path=str(_REC),
                           rec_keys_path=str(_DICT),
                           intra_op_num_threads=_nt,
                           inter_op_num_threads=1)
    except Exception:
        _init_failed = True
        _engine = None
    return _engine


def read_text(crop) -> str:
    """범용 rec-only 인식 (후처리 없음). cooldown/buff/xp 등 공용.

    rec-only(use_det=False): crop 영역 고정이라 글자검출(det) 불필요.
    벤치 12ms. torch/paddle 의존 0 (onnxruntime CPU 전용).
    """
    eng = _get_engine()
    if eng is None or crop is None:
        return ""
    try:
        r, _ = eng(crop, use_det=False, use_cls=False, use_rec=True)
        if not r:
            return ""
        return "".join(
            x[0] for x in r
            if isinstance(x, (list, tuple)) and x and isinstance(x[0], str))
    except Exception:
        return ""


def _normalize_struct(s: str) -> str:
    """선비족 X-Y(Z) 구조 노이즈 정리 (base 글자는 불변).

    1) 양끝 잉여 하이픈 제거 ('-선비족' / '선비족-' → '선비족').
    2) 괄호 밸런스 복원: '(' > ')' 면 부족분 뒤에 ')' 보충
       ('선비족2-4(1' → '선비족2-4(1)'). lap suffix 가 격수/힐러 간 글자 단위로
       일치해야 trail 키가 맞물림.
       반대(고립 ')')는 끝에서 초과분만 제거(정상 lap suffix 보존).
    옛바 맵명에 중첩 괄호 없음 → 단순 카운트로 충분.
    """
    if not s:
        return s
    s = s.strip("-")
    open_n = s.count("(")
    close_n = s.count(")")
    if open_n > close_n:
        s = s + ")" * (open_n - close_n)
    elif close_n > open_n:
        m = re.search(r"\)+$", s)
        if m:
            excess = close_n - open_n
            keep = max(0, len(m.group(0)) - excess)
            s = s[:m.start()] + ")" * keep
    return s


def read_map(crop) -> str:
    """맵바 crop -> "선비족X-Y(Z)" (노이즈 제거 + 구조 정규화)."""
    return _normalize_struct(_KEEP.sub("", read_text(crop)))
