"""숫자 CNN 학습 데이터 수집 (HP/MP/XP 영역). 2026-06-10.

좌표 CNN(digit_cnn.onnx)을 HP/MP/XP로 확장하기 위한 실게임 patch 수집기.
좌표 _collect_digits.py 와 동일 철학: 각 영역 전처리 이미지에서 숫자 박스를
세그 분할 + (인식 자릿수와 일치 시) 라벨링해 logs/digit_collect/{label}/
에 patch 저장. 라벨 불확실(박스수≠자릿수, 예: HP 슬래시 포함)이면 전체 crop을
_raw/{src}/ 에 저장 — 폰트가 좌표 숫자와 동일한지 육안 확인용.

env OB_COLLECT_DIGITS=1 일 때만 활성 (평소 no-op, 성능 영향 0).
클래스당 cap + throttle 로 디스크 폭주 방지.
수집 후 `py -m src.tools.cloud_digit_upload` 로 클라우드 업로드.
"""
from __future__ import annotations

import os
import pathlib
import threading
import time

import cv2

_ENABLED = os.environ.get("OB_COLLECT_DIGITS", "") == "1"
_ROOT = pathlib.Path.cwd() / "logs" / "digit_collect"
_CAP_PER_LABEL = 400       # 라벨(0~9)당 최대 patch
_RAW_CAP_PER_SRC = 150     # src(hp/mp/xp)당 최대 raw crop
_THROTTLE_SEC = 0.3        # src별 최소 저장 간격

_lock = threading.Lock()
_counts: dict = {}
_raw_counts: dict = {}
_last_ts: dict = {}
_seg_fn = None


def enabled() -> bool:
    return _ENABLED


def _seg():
    global _seg_fn
    if _seg_fn is None:
        from .ocr import _segment_digit_boxes
        _seg_fn = _segment_digit_boxes
    return _seg_fn


def collect(src: str, proc_bgr, label_digits) -> None:
    """전처리 이미지에서 숫자 patch 수집. enabled 아니면 즉시 반환.

    src: 'hp'|'mp'|'xp' (파일명 prefix).
    proc_bgr: OCR에 넣는 전처리 BGR 이미지(좌표 _crop_coord / hpmp _preprocess_for_ocr).
    label_digits: 같은 프레임 OCR 결과 숫자열(라벨 후보). 박스수와 길이가
                  일치할 때만 박스별 라벨로 채택.
    """
    if not _ENABLED or proc_bgr is None or getattr(proc_bgr, "size", 0) == 0:
        return
    now = time.monotonic()
    with _lock:
        if now - _last_ts.get(src, 0.0) < _THROTTLE_SEC:
            return
        _last_ts[src] = now
    try:
        boxes = _seg()(proc_bgr)
        lab = "".join(ch for ch in str(label_digits or "") if ch.isdigit())
        if boxes and lab and len(boxes) == len(lab):
            for (x, y, w, h), ch in zip(boxes, lab):
                d = _ROOT / ch
                d.mkdir(parents=True, exist_ok=True)
                with _lock:
                    n = _counts.get(ch)
                    if n is None:
                        n = len(list(d.glob("*.png")))
                    if n >= _CAP_PER_LABEL:
                        _counts[ch] = n
                        continue
                    _counts[ch] = n + 1
                cv2.imwrite(str(d / f"{src}_{n:05d}.png"),
                            proc_bgr[y:y + h, x:x + w])
        else:
            # 라벨 불확실 → 전체 crop 보관 (폰트 동일성 확인용).
            d = _ROOT / "_raw" / src
            d.mkdir(parents=True, exist_ok=True)
            with _lock:
                n = _raw_counts.get(src)
                if n is None:
                    n = len(list(d.glob("*.png")))
                if n >= _RAW_CAP_PER_SRC:
                    return
                _raw_counts[src] = n + 1
            cv2.imwrite(str(d / f"{n:05d}.png"), proc_bgr)
    except Exception:
        pass
