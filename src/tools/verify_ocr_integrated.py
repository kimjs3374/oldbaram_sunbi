"""통합 PaddleOCR(EasyOCR 제거)로 실제 frame 읽어 좌표+맵 동작 확인.

실행: py -m src.tools.verify_ocr_integrated
대상: logs/debug_frame.png (이미 저장된 격수 프레임)
"""
import os
import pathlib
import time

os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

import cv2
import numpy as np

from src.vision.ocr import Ocr


def main():
    root = pathlib.Path(__file__).resolve().parents[2]
    fp = root / "logs" / "debug_frame.png"
    if not fp.is_file():
        print(f"[!] {fp} 없음 — capture_debug 먼저 실행")
        return
    img = cv2.imdecode(np.fromfile(str(fp), dtype=np.uint8),
                       cv2.IMREAD_COLOR)
    if img is None:
        print("[!] 이미지 로드 실패")
        return
    print(f"frame: {img.shape[1]}x{img.shape[0]}")

    o = Ocr()
    # 30번 연속 호출 → throttle 히트율과 평균 시간 측정.
    N = 30
    times = []
    t_start = time.time()
    for i in range(N):
        t0 = time.time()
        r = o.read(img)
        dt = (time.time() - t0) * 1000
        times.append(dt)
    t_total = (time.time() - t_start) * 1000
    fps = N / (t_total / 1000) if t_total > 0 else 0
    miss = sum(1 for t in times if t > 50)
    hit = len(times) - miss
    print(f"  coord={r.coord}  map='{r.map_name}'  "
          f"raw_c='{r.raw_coord_text}'")
    print(f"  N={N}  total={t_total:.0f}ms  fps={fps:.1f}  "
          f"avg={sum(times)/N:.1f}ms")
    print(f"  hit={hit}(캐시)  miss={miss}(OCR 실행)  "
          f"miss_avg={sum(t for t in times if t>50)/max(miss,1):.0f}ms")
    print(f"  시간 샘플: {[f'{t:.0f}' for t in times[:10]]}")


if __name__ == "__main__":
    main()
