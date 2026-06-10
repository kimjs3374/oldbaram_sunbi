"""좌표 숫자(0~9) 템플릿 매칭 — EasyOCR(PyTorch) 비딥러닝 대체.

forbidden 6번('템플릿 매칭으로 힐러 위치')과 무관: 그건 화면 캐릭 위치 검출,
이건 우하단 좌표 텍스트 숫자(0~9) 인식. 다른 도메인.

해상도 무관 설계 (사용자 요구):
- 각 숫자를 여러 배율로 변형해 멀티 템플릿 등록 → 입력이 어떤 크기든 같은
  배율 템플릿과 매칭. 작은 배율 리샘플 손상(8↔3 혼동)을 배율별 템플릿으로 흡수.
- _normalize: 종횡비 보존 + 정사각 중앙 패딩 + 블러 (stretch 금지).

흐름:
- 빌드: coordnum/{0..9}.png 를 add() → 배율 변형 멀티 템플릿 + 저장.
- 운영: load 후 match() — _segment 박스를 0~9 로 인식. torch 불필요, ~1ms.
"""
import pathlib
from typing import Optional

import cv2
import numpy as np

# 정규화 표준 캔버스 (정사각). 종횡비 보존 + 중앙 패딩.
NORM = 32
# 등록 배율 — 게임 해상도 차이에 따른 숫자 크기 변동 범위를 포괄.
SCALES = (0.5, 0.65, 0.8, 1.0, 1.3, 1.7, 2.2, 3.0)


class CoordDigitMatcher:
    def __init__(self):
        # {0..9: [float32 (NORM,NORM), ...]}  배율별 멀티 템플릿
        self.templates: dict = {}

    @staticmethod
    def _normalize(patch: np.ndarray) -> Optional[np.ndarray]:
        """숫자 patch → 이진화 → tight crop → 종횡비 보존 정사각 + 블러."""
        if patch is None or patch.size == 0:
            return None
        g = (cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
             if patch.ndim == 3 else patch)
        _, bw = cv2.threshold(g, 0, 255,
                              cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        if np.mean(bw) > 127:
            bw = 255 - bw  # 숫자=흰색 보장
        ys, xs = np.where(bw > 0)
        if len(xs) == 0:
            return None
        bw = bw[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        h, w = bw.shape
        sc = float(NORM - 6) / max(h, w)  # 긴 변을 캔버스에 맞춤(여백 3px)
        nh, nw = max(1, int(round(h * sc))), max(1, int(round(w * sc)))
        r = cv2.resize(bw, (nw, nh), interpolation=cv2.INTER_AREA)
        canvas = np.zeros((NORM, NORM), dtype=np.uint8)
        y0, x0 = (NORM - nh) // 2, (NORM - nw) // 2
        canvas[y0:y0 + nh, x0:x0 + nw] = r
        canvas = cv2.GaussianBlur(canvas, (3, 3), 0)
        return canvas.astype(np.float32) / 255.0

    # ---- 빌드 (각 숫자를 여러 배율로 등록) ----
    def add(self, label: int, patch: np.ndarray) -> None:
        label = int(label)
        if not (0 <= label <= 9):
            return
        self.templates.setdefault(label, [])
        h, w = patch.shape[:2]
        for s in SCALES:
            nw, nh = max(1, int(w * s)), max(1, int(h * s))
            interp = cv2.INTER_AREA if s < 1.0 else cv2.INTER_LINEAR
            rs = cv2.resize(patch, (nw, nh), interpolation=interp)
            n = self._normalize(rs)
            if n is not None:
                self.templates[label].append(n)

    def is_ready(self) -> bool:
        return all(d in self.templates and self.templates[d]
                   for d in range(10))

    # ---- 운영 (매칭) ----
    def match(self, patch: np.ndarray) -> Optional[int]:
        n = self._normalize(patch)
        if n is None or not self.templates:
            return None
        best, best_d = 1e18, None
        for label, arrs in self.templates.items():
            for t in arrs:
                dist = float(np.mean((n - t) ** 2))  # SSD
                if dist < best:
                    best, best_d = dist, label
        return best_d

    # ---- 저장/로드 (멀티 템플릿) ----
    def save(self, path) -> None:
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        out = {}
        for label, arrs in self.templates.items():
            for i, t in enumerate(arrs):
                out[f"{label}_{i}"] = t
        np.savez(str(path), **out)

    def load(self, path) -> bool:
        path = pathlib.Path(path)
        if not path.exists():
            return False
        try:
            z = np.load(str(path))
            self.templates = {}
            for k in z.files:
                label = int(k.split("_")[0])
                self.templates.setdefault(label, []).append(z[k])
        except Exception:
            return False
        return all(d in self.templates and self.templates[d]
                   for d in range(10))
