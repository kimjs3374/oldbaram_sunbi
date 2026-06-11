"""맵바 CRNN(CTC) 추론 — onnxruntime CPU (torch/paddle 불필요).

맵바 crop -> "선비족X-Y(Z)" 한 번에. PaddleOCR(한글 base 누락/숫자 오독)
대체. 학습: _train_map_crnn.py. 모델: map_crnn.onnx + map_crnn_charset.txt.
"""
import pathlib
from typing import Optional

import numpy as np
import cv2

try:
    import onnxruntime as ort
except Exception:
    ort = None

IMG_H, IMG_W = 32, 160


class MapCrnn:
    def __init__(self, onnx_path, charset_path):
        self.sess = None
        self._name = None
        self.charset = []
        try:
            op = pathlib.Path(onnx_path)
            cp = pathlib.Path(charset_path)
            if ort is not None and op.exists() and cp.exists():
                self.sess = ort.InferenceSession(
                    str(op), providers=["CPUExecutionProvider"])
                self._name = self.sess.get_inputs()[0].name
                self.charset = cp.read_text(
                    encoding="utf-8").splitlines()
        except Exception:
            self.sess = None

    def ready(self) -> bool:
        return self.sess is not None and len(self.charset) > 0

    def _decode(self, logits) -> str:
        ids = logits.argmax(2)[0]  # (T,)
        out, prev = [], -1
        for i in ids.tolist():
            if i != prev and i != 0 and i < len(self.charset):
                out.append(self.charset[i])
            prev = i
        return "".join(out)

    def predict(self, bgr) -> Optional[str]:
        """맵바 crop(BGR) -> 인식 텍스트. 실패 시 None."""
        if self.sess is None:
            return None
        try:
            g = (cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
                 if bgr.ndim == 3 else bgr)
            g = cv2.resize(g, (IMG_W, IMG_H)).astype(np.float32) / 255.0
            x = g[None, None]  # (1,1,H,W)
            out = self.sess.run(None, {self._name: x})[0]  # (1,T,C)
            return self._decode(out)
        except Exception:
            return None
