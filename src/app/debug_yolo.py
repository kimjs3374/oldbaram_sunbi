"""힐러 PC 화면에서 YOLO가 뭘 보는지 저장/출력.

1. mss 캡처 1장 → 해상도 출력 + debug_frame.png 저장
2. YOLO conf=0.01로 돌려 모든 후보 출력 (크기, conf)
3. 검출 결과 박스 그린 이미지 → debug_annotated.png 저장
"""
import argparse
from pathlib import Path
import cv2

from ..config import load as load_cfg
from ..capture.screen import Grabber
from ..vision.yolo import YoloRunner
from ..input.keys import find_windows_by_process


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--conf", type=float, default=0.01)
    parser.add_argument("--out", default=r"D:\oldbaram\debug")
    parser.add_argument("--proc", default="msw.exe",
                         help="캡처할 프로세스 이름. 비우면 monitor_index 사용")
    parser.add_argument("--monitor", type=int, default=None)
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(exist_ok=True)

    cfg = load_cfg()
    hwnd = None
    if args.proc and args.monitor is None:
        wins = find_windows_by_process(args.proc)
        print(f"[*] {args.proc} 창 목록: {wins}")
        if wins:
            hwnd = wins[0]
    mon_idx = args.monitor if args.monitor is not None else cfg.capture.monitor_index
    g = Grabber(mon_idx, hwnd=hwnd)
    frame = g.grab()
    print(f"[*] 캡처 shape={frame.shape}  영역={g.mon}  hwnd={hwnd}")
    cv2.imwrite(str(out / "frame.png"), frame)

    print(f"[*] YOLO 로드: {cfg.vision.weights}")
    y = YoloRunner(cfg.vision.weights, imgsz=cfg.vision.imgsz,
                    conf=args.conf, iou=cfg.vision.iou,
                    half=cfg.vision.half, device=cfg.vision.device)
    dets = y.detect(frame)
    print(f"[+] conf>={args.conf} 검출 수: {len(dets)}")
    for i, d in enumerate(dets):
        print(f"  #{i}: ({d.x1},{d.y1})-({d.x2},{d.y2})  "
              f"{d.w}x{d.h}  conf={d.conf:.3f}  cls={d.cls}")

    ann = frame.copy()
    for d in dets:
        cv2.rectangle(ann, (d.x1, d.y1), (d.x2, d.y2), (0, 255, 255), 2)
        cv2.putText(ann, f"{d.conf:.2f}", (d.x1, d.y1 - 3),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    cv2.imwrite(str(out / "annotated.png"), ann)
    print(f"[+] 저장: {out}\\frame.png, annotated.png")
    print(f"[+] 탐색기로 frame.png 열어서 실제 게임 화면 잡혔는지 확인")


if __name__ == "__main__":
    main()
