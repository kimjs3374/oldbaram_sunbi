"""OCR crop/인식 디버그.

msw.exe 창 캡처 → coord/map crop 저장 → OCR 결과 출력.
실행: py -m src.app.debug_ocr
출력: D:\\oldbaram\\debug\\frame.png, coord.png, map.png
"""
import argparse
from pathlib import Path

import cv2

from ..config import load as load_cfg
from ..capture.screen import Grabber
from ..vision.ocr import Ocr
from ..input.keys import find_windows_by_process


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=r"D:\oldbaram\debug")
    p.add_argument("--proc", default="msw.exe")
    args = p.parse_args()

    out = Path(args.out); out.mkdir(exist_ok=True)
    cfg = load_cfg()
    hwnd = None
    if args.proc:
        wins = find_windows_by_process(args.proc)
        print(f"[*] {args.proc} 창: {wins}")
        if wins:
            hwnd = wins[0]
    g = Grabber(cfg.capture.monitor_index, hwnd=hwnd)
    frame = g.grab()
    print(f"[*] 캡처 shape={frame.shape} 영역={g.mon}")
    cv2.imwrite(str(out / "frame.png"), frame)

    ocr = Ocr(coord_w=cfg.ocr.coord_w, coord_h=cfg.ocr.coord_h,
              coord_right_pad=cfg.ocr.coord_right_pad,
              coord_bottom_pad=cfg.ocr.coord_bottom_pad,
              coord_upscale=cfg.ocr.coord_upscale,
              map_w=cfg.ocr.map_w, map_h=cfg.ocr.map_h,
              map_top_pad=cfg.ocr.map_top_pad,
              map_upscale=cfg.ocr.map_upscale)
    cc = ocr._crop_coord(frame)
    mc = ocr._crop_map(frame)
    cv2.imwrite(str(out / "coord_crop.png"), cc)
    cv2.imwrite(str(out / "map_crop.png"), mc)
    r = ocr.read(frame)
    print(f"[+] coord raw={r.raw_coord_text!r} → {r.coord}")
    print(f"[+] map   raw={r.raw_map_text!r} → {r.map_name!r}")
    print(f"[+] 저장: {out}\\frame.png coord_crop.png map_crop.png")
    print(f"[+] crop이 좌하단 좌표/상단 맵이름 제대로 잡는지 눈으로 확인")


if __name__ == "__main__":
    main()
