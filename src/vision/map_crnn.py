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

    def _decode_conf(self, logits):
        """CTC greedy decode + confidence(글자별 softmax 최소값).

        학습된 맵 = 모든 글자 prob 높음 → conf 높음(min). 미학습/새 맵 =
        한 글자라도 불확실 → conf 낮음 → 수집 트리거.
        """
        z = logits[0]  # (T,C)
        e = np.exp(z - z.max(1, keepdims=True))
        prob = e / e.sum(1, keepdims=True)
        ids = z.argmax(1)
        out, confs, prev = [], [], -1
        for t, i in enumerate(ids.tolist()):
            if i != prev and i != 0 and i < len(self.charset):
                out.append(self.charset[i])
                confs.append(float(prob[t, i]))
            prev = i
        conf = min(confs) if confs else 0.0
        return "".join(out), conf

    def predict(self, bgr):
        """맵바 crop(BGR) -> (텍스트, confidence). 실패 시 (None, 0.0).

        confidence = 글자별 최소 softmax. 학습 맵 ~0.99, 미학습 낮음.
        """
        if self.sess is None:
            return None, 0.0
        try:
            g = (cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
                 if bgr.ndim == 3 else bgr)
            g = cv2.resize(g, (IMG_W, IMG_H)).astype(np.float32) / 255.0
            x = g[None, None]  # (1,1,H,W)
            out = self.sess.run(None, {self._name: x})[0]  # (1,T,C)
            return self._decode_conf(out)
        except Exception:
            return None, 0.0
