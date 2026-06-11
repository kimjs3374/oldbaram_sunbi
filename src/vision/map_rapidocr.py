"""맵바 OCR — RapidOCR(korean PP-OCR ONNX). PaddleOCR/CRNN 대체.

일반 OCR이라 숫자 조합 무관(x,y,z 학습 불필요), 한글 base도 정확.
실측: 한글+숫자 ~100%(끝 영문 G 후처리). onnxruntime CPU.
모델: korean_rec.onnx(13MB) + korean_dict.txt (같은 폴더).
"""
import re
import pathlib

_DIR = pathlib.Path(__file__).resolve().parent
_REC = _DIR / "korean_rec.onnx"
_DICT = _DIR / "korean_dict.txt"
_engine = None
_init_failed = False

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
        _engine = RapidOCR(rec_model_path=str(_REC),
                           rec_keys_path=str(_DICT))
    except Exception:
        _init_failed = True
        _engine = None
    return _engine


def read_map(crop) -> str:
    """맵바 crop -> "선비족X-Y(Z)" (G 등 노이즈 후처리). 실패 시 ''.

    rec-only(use_det=False): 맵바는 _crop_map 으로 위치 고정이라 글자검출(det)
    불필요. 벤치 실측 det+rec 407ms → rec-only 12ms(34배), PaddleOCR 163ms/875MB
    대비 13배 빠르고 7배 가벼움. 정확도 100% 동일.
    """
    eng = _get_engine()
    if eng is None or crop is None:
        return ""
    try:
        r, _ = eng(crop, use_det=False, use_cls=False, use_rec=True)
        if not r:
            return ""
        text = "".join(
            x[0] for x in r
            if isinstance(x, (list, tuple)) and x and isinstance(x[0], str))
        return _KEEP.sub("", text)
    except Exception:
        return ""
