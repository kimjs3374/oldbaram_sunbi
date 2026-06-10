"""좌표 숫자(0~9) 경량 CNN 추론 — ONNX Runtime CPU (torch 불필요).

해상도 무관: 학습 시 scale/blur/noise augment → 형태 특징 학습.
템플릿 매칭(해상도 종속)을 대체. 전처리는 coord_template._normalize 공통.
"""
import pathlib
from typing import List

import numpy as np

try:
    import onnxruntime as ort
except Exception:
    ort = None

from .coord_template import CoordDigitMatcher  # _normalize 재사용


class DigitCnn:
    def __init__(self, onnx_path):
        self.sess = None
        self._name = None
        try:
            p = pathlib.Path(onnx_path)
            if ort is not None and p.exists():
                self.sess = ort.InferenceSession(
                    str(p), providers=["CPUExecutionProvider"])
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
