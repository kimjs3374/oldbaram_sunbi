"""PaddleOCR korean 모델로 좌표(숫자) 이미지를 읽어 정확도 확인.

EasyOCR 제거 후 단일 모듈로 통합 가능한지 판단용.

실행: py -m src.tools.verify_paddle_coord
대상: logs/debug_coord.png, logs/debug_coord_ocr.png, logs/verify_coord_new*.png
"""
import os
import pathlib

os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

import cv2
import numpy as np
from paddleocr import TextRecognition


_ROOT = pathlib.Path(__file__).resolve().parents[2]
_KOREAN_REC_DIR = _ROOT / "models" / "korean_PP-OCRv5_mobile_rec"


def main():
    kwargs = dict(
        model_name="korean_PP-OCRv5_mobile_rec",
        enable_mkldnn=False,
    )
    if _KOREAN_REC_DIR.is_dir():
        kwargs["model_dir"] = str(_KOREAN_REC_DIR)
    rec = TextRecognition(**kwargs)

    logs = _ROOT / "logs"
    targets = [
        "debug_coord.png",
        "debug_coord_ocr.png",
        "verify_coord_new.png",
        "verify_coord_new_ocr.png",
    ]
    for name in targets:
        p = logs / name
        if not p.is_file():
            print(f"[skip] {name} 없음")
            continue
        img = cv2.imdecode(
            np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if img is None:
            print(f"[fail] {name} 로드 실패")
            continue
        # TextRecognition은 한 줄 단위. 원본과 업스케일 둘 다 시도.
        # 1) 원본 그대로
        res1 = rec.predict(img)
        # 2) 이진화 + 4x 업스케일 (현재 debug_coord_ocr.png와 같은 전처리)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, bin_ = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)
        up = cv2.resize(bin_, None, fx=4, fy=4,
                        interpolation=cv2.INTER_LANCZOS4)
        up3 = cv2.cvtColor(up, cv2.COLOR_GRAY2BGR)
        res2 = rec.predict(up3)

        def extract(r):
            out = []
            for item in r:
                t = item.get("rec_text", "") if isinstance(item, dict) else ""
                s = item.get("rec_score", 0.0) if isinstance(item, dict) else 0.0
                out.append((t, float(s)))
            return out

        print(f"\n=== {name} ({img.shape[1]}x{img.shape[0]}) ===")
        print(f"  원본      : {extract(res1)}")
        print(f"  bin+4x up : {extract(res2)}")


if __name__ == "__main__":
    main()
