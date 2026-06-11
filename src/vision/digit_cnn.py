"""좌표 숫자(0~9) 경량 CNN 추론 — ONNX Runtime CPU (torch 불필요).

해상도 무관: 학습 시 scale/blur/noise augment → 형태 특징 학습.
템플릿 매칭(해상도 종속)을 대체. 전처리는 coord_template._normalize 공통.

스레드 캡(2026-06-11): YOLO(onnx)·RapidOCR·digit_cnn 3개가 각자 intra_op=
전체코어로 가동되어 CPU 과구독→YOLO predict spike. 경량 모델(0.2MB)이라
2스레드면 충분 → 코어를 YOLO에 양보(경합 완화). env OB_OCR_INTRA_THREADS 로 조정.
"""
import os
import pathlib
from typing import List

import numpy as np

try:
    import onnxruntime as ort
except Exception:
    ort = None

from .coord_template import CoordDigitMatcher  # _normalize 재사용


def _intra_threads() -> int:
    """OCR 계열 onnx 세션 intra_op 스레드 수 (기본 2, env 조정 가능)."""
    try:
        return max(1, int(os.environ.get("OB_OCR_INTRA_THREADS", "2")))
    except Exception:
        return 2


class DigitCnn:
    def __init__(self, onnx_path):
        self.sess = None
        self._name = None
        try:
            p = pathlib.Path(onnx_path)
            if ort is not None and p.exists():
                so = ort.SessionOptions()
                # 전체코어 점유 방지(YOLO 와 경합 완화). 경량 CNN이라 2면 충분.
                so.intra_op_num_threads = _intra_threads()
                so.inter_op_num_threads = 1
                self.sess = ort.InferenceSession(
                    str(p), sess_options=so,
                    providers=["CPUExecutionProvider"])
                self._name = self.sess.get_inputs()[0].name
        except Exception:
            self.sess = None

    def ready(self) -> bool:
        return self.sess is not None

    def predict(self, patches: List[np.ndarray]) -> List[int]:
        """숫자 patch 리스트 → 0~9 라벨 리스트 (배치 추론)."""
        if self.sess is None or not patches:
            return []
        xs = []
        for p in patches:
            n = CoordDigitMatcher._normalize(p)
            xs.append(n if n is not None else np.zeros((32, 32), np.float32))
        x = np.stack(xs)[:, None, :, :].astype(np.float32)  # (N,1,32,32)
        out = self.sess.run(None, {self._name: x})[0]       # (N,10)
        return out.argmax(1).tolist()
