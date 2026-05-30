"""격수/힐러 PC에서 한 프레임을 캡처해 OCR ROI 위치를 눈으로 확인.

실행:  py -m src.tools.capture_debug
출력:  logs/debug_frame.png, logs/debug_coord.png, logs/debug_map.png

OCR이 엉뚱한 곳을 읽을 때 해상도/ROI가 맞는지 확인용. 격수 PC처럼
창 크기가 힐러 PC와 다르면 config.yaml 의 ocr.* 값 재조정 필요.
"""
import pathlib
import cv2

from ..config import load as load_cfg
from ..capture.screen import Grabber
from ..input.keys import find_windows_by_process


def main():
    cfg = load_cfg()
    hwnd = None
    if cfg.input.target_window.lower().endswith(".exe"):
        wins = find_windows_by_process(cfg.input.target_window)
        if wins:
            hwnd = wins[0]
            print(f"hwnd={hwnd} ({cfg.input.target_window})")
        else:
            print(f"[!] {cfg.input.target_window} 창 없음 → monitor fallback")
    g = Grabber(cfg.capture.monitor_index, hwnd=hwnd)
    print(f"capture region={g.mon}")
    f = g.grab()
    H, W = f.shape[:2]
    print(f"frame: {W} x {H}")

    # 프로젝트 루트/logs 밑에 저장 (hardcoded path 제거).
    here = pathlib.Path(__file__).resolve().parents[2]
    out = here / "logs"
    out.mkdir(parents=True, exist_ok=True)

    cv2.imwrite(str(out / "debug_frame.png"), f)

    # 좌표 ROI: 자동 탐색 먼저, 실패 시 config fallback
    import numpy as np
    c_search_h = max(60, int(H * 0.15))
    c_search_w = max(200, int(W * 0.22))
    c_x0 = W - c_search_w
    c_y0 = H - c_search_h
    c_roi_area = f[c_y0:, c_x0:]
    c_gray = cv2.cvtColor(c_roi_area, cv2.COLOR_BGR2GRAY)
    c_bright = (c_gray > 200).astype(np.uint8)
    c_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    c_closed = cv2.morphologyEx(c_bright, cv2.MORPH_CLOSE, c_kernel)
    c_contours, _ = cv2.findContours(c_closed, cv2.RETR_EXTERNAL,
                                      cv2.CHAIN_APPROX_SIMPLE)
    c_boxes = []
    min_h = max(5, int(H * 0.006))
    max_h = max(20, int(H * 0.03))
    for cc_ in c_contours:
        bx, by, bw, bh = cv2.boundingRect(cc_)
        if bh < min_h or bh > max_h or bw < 2 or bw > max_h:
            continue
        if bw > bh * 1.3:
            continue
        if bh > bw * 4:
            continue
        c_boxes.append((bx, by, bw, bh))
    auto_coord = None
    if len(c_boxes) >= 4:
        c_boxes.sort(key=lambda b: -(b[1] + b[3] / 2))
        chosen = None
        for ref in c_boxes:
            ref_cy = ref[1] + ref[3] / 2
            tol = max(5, ref[3] * 0.8)
            row = [b for b in c_boxes
                   if abs((b[1] + b[3] / 2) - ref_cy) <= tol]
            if len(row) >= 4:
                chosen = row
                break
        if chosen is not None:
            chosen.sort(key=lambda b: b[0])
            xs = [b[0] for b in chosen]
            xe = [b[0] + b[2] for b in chosen]
            ys = [b[1] for b in chosen]
            ye = [b[1] + b[3] for b in chosen]
            pad = 4
            # 얇은 숫자 contour 누락 대비: projection으로 x 범위 보강.
            py1 = max(0, min(ys) - 2)
            py2 = min(c_search_h, max(ye) + 2)
            col_bright = (c_gray[py1:py2] > 200).any(axis=0)
            avg_h = int(sum(b[3] for b in chosen) / len(chosen))
            scan_lo = max(0, min(xs) - avg_h * 3)
            scan_hi = min(c_search_w, max(xe) + avg_h * 3)
            scan = col_bright[scan_lo:scan_hi]
            px_idx = np.where(scan)[0]
            if len(px_idx) > 0:
                proj_x1 = scan_lo + int(px_idx.min())
                proj_x2 = scan_lo + int(px_idx.max()) + 1
                x_min = min(min(xs), proj_x1)
                x_max = max(max(xe), proj_x2)
            else:
                x_min = min(xs)
                x_max = max(xe)
            rx1 = max(0, x_min - pad)
            ry1 = max(0, min(ys) - pad)
            rx2 = min(c_search_w, x_max + pad)
            ry2 = min(c_search_h, max(ye) + pad)
            auto_coord = (c_x0 + rx1, c_y0 + ry1,
                          c_x0 + rx2, c_y0 + ry2)
    if auto_coord is not None:
        cx1, cy1, cx2, cy2 = auto_coord
        coord_roi = f[cy1:cy2, cx1:cx2].copy()
        print(f"coord ROI (자동) = ({cx1},{cy1})..({cx2},{cy2}) "
              f"size={cx2-cx1}x{cy2-cy1} boxes={len(c_boxes)}")
    else:
        cw, ch = cfg.ocr.coord_w, cfg.ocr.coord_h
        crp, cbp = cfg.ocr.coord_right_pad, cfg.ocr.coord_bottom_pad
        cx1 = W - crp - cw
        cy1 = H - cbp - ch
        cx2 = cx1 + cw
        cy2 = cy1 + ch
        coord_roi = f[cy1:cy2, cx1:cx2].copy()
        print(f"coord ROI (fallback) = ({cx1},{cy1}) size={cw}x{ch} "
              f"boxes={len(c_boxes)}")
    # OCR 들어가는 실제 이미지(이진화 + 4배 LANCZOS)도 저장해서 확인.
    coord_gray = cv2.cvtColor(coord_roi, cv2.COLOR_BGR2GRAY)
    _, coord_bin = cv2.threshold(coord_gray, 170, 255, cv2.THRESH_BINARY)
    coord_up = cv2.resize(coord_bin, None,
                          fx=cfg.ocr.coord_upscale, fy=cfg.ocr.coord_upscale,
                          interpolation=cv2.INTER_LANCZOS4)
    cv2.imwrite(str(out / "debug_coord.png"), coord_roi)
    cv2.imwrite(str(out / "debug_coord_ocr.png"), coord_up)

    # 옛바 맵 bar 구조: 검정 bar 양쪽에 한글 글자(흰색) 배치.
    # 한 행 연속 검정 run은 글자 공백에서 끊겨 엉뚱한 위치를 잡으므로
    # x-컬럼별 검정 비율로 전체 범위를 한 덩어리로 인식 (ocr.py와 동일 알고리즘).
    search_h = max(40, int(H * 0.1))
    top = f[0:search_h]
    gray = cv2.cvtColor(top, cv2.COLOR_BGR2GRAY)
    dark = gray < 30
    y_probe_bot = max(8, search_h // 3)
    col_ratio = dark[1:y_probe_bot].mean(axis=0)
    mask = col_ratio >= 0.5
    segs = []
    i = 0
    while i < W:
        if not mask[i]:
            i += 1
            continue
        s = i
        while i < W and mask[i]:
            i += 1
        segs.append((s, i))
    max_gap = max(40, int(W * 0.05))
    merged = []
    for s, e in segs:
        if merged and s - merged[-1][1] <= max_gap:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    cx_center = W // 2
    cx_lo, cx_hi = int(W * 0.25), int(W * 0.75)
    min_bar_w = max(80, int(W * 0.08))
    max_bar_w = int(W * 0.55)
    candidates = [(s, e) for s, e in merged
                  if cx_lo <= (s + e) // 2 <= cx_hi
                  and min_bar_w <= (e - s) <= max_bar_w]
    best = None
    if candidates:
        candidates.sort(key=lambda se: abs((se[0] + se[1]) // 2 - cx_center))
        mx1, mx2 = candidates[0]
        y_mean = dark[:, mx1:mx2].mean(axis=1)
        valid = y_mean >= 0.4
        y_segs = []
        i = 0
        while i < search_h:
            if not valid[i]:
                i += 1
                continue
            s = i
            while i < search_h and valid[i]:
                i += 1
            y_segs.append((s, i))
        if y_segs:
            y_segs.sort(key=lambda se: -(se[1] - se[0]))
            y_top, y_bot = y_segs[0]
            my1 = max(0, y_top - 2)
            my2 = min(H - 1, y_bot + 2)
            if my2 - my1 < 18:
                my2 = min(H - 1, my1 + 22)
            best = (mx1, my1, mx2, my2)
    if best is not None:
        mx1, my1, mx2, my2 = best
        map_roi = f[my1:my2, mx1:mx2].copy()
        mw_eff = mx2 - mx1
        mh_eff = my2 - my1
        cv2.imwrite(str(out / "debug_map.png"), map_roi)
        print(f"map  ROI (자동) = ({mx1},{my1})..({mx2},{my2}) size={mw_eff}x{mh_eff}")
    else:
        mw, mh = cfg.ocr.map_w, cfg.ocr.map_h
        mtp = cfg.ocr.map_top_pad
        mlp = getattr(cfg.ocr, "map_left_pad", -1)
        mx1 = mlp if (mlp is not None and mlp >= 0) else (W - mw) // 2
        my1 = mtp
        mx2 = mx1 + mw
        my2 = my1 + mh
        map_roi = f[my1:my2, mx1:mx2].copy()
        cv2.imwrite(str(out / "debug_map.png"), map_roi)
        print(f"map  ROI (fallback) = ({mx1},{my1}) size={mw}x{mh}")

    # 프레임 위에 ROI 박스 오버레이도 저장.
    over = f.copy()
    cv2.rectangle(over, (cx1, cy1), (cx2, cy2), (0, 255, 255), 2)
    cv2.putText(over, "COORD", (cx1, cy1 - 4), cv2.FONT_HERSHEY_SIMPLEX,
                0.5, (0, 255, 255), 1)
    cv2.rectangle(over, (mx1, my1), (mx2, my2), (0, 255, 0), 2)
    cv2.putText(over, "MAP", (mx1, my2 + 16), cv2.FONT_HERSHEY_SIMPLEX,
                0.5, (0, 255, 0), 1)
    cv2.imwrite(str(out / "debug_overlay.png"), over)

    print(f"\n저장 완료: {out}")
    print("  debug_frame.png      원본 프레임")
    print("  debug_overlay.png    ROI 박스 표시")
    print("  debug_coord.png      좌표 crop (우하단)")
    print("  debug_map.png        맵이름 crop (상단 중앙)")


if __name__ == "__main__":
    main()
