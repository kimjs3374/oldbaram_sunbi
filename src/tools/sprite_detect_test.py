"""스프라이트 탐지 테스트 도구 (독립 실행형).

목적
  사용자가 게임 화면에서 **일부만 잘라낸 작은 크롭**을 넣으면, 현재 화면에서
  그 크롭이 어디 있는지 찾아서 표시. 복구 로직 선행 검증용.

준비물 (매우 간단)
  - 게임 스샷에서 **캐릭터의 구별되는 작은 영역만 사각형으로 크롭**.
  - 예: 머리/모자 부분만, 옷 문양 일부만, 특정 장비 포인트만.
  - 포맷 무관 (PNG/JPG/BMP 전부 OK), **배경 투명화 불필요**.
  - 크기는 작을수록 빠름 (20x20 ~ 40x40 권장).
  - 같은 캐릭터 여러 방향/프레임을 각각 잘라서 다 넣으면 OR로 탐지.

사용법
  py D:/oldbaram/dist_dosa/src/tools/sprite_detect_test.py

  1) Load Sprite(s) → 크롭 이미지 파일 선택 (여러 개 동시 가능).
  2) Capture → 현재 화면 1회 스캔.
  3) Live(0.5s) → 반복 스캔.
  4) 슬라이더 L1 조정하며 오탐 0인 지점 탐색. L2/L3는 보조.

탐지 모드 (상단 "ORB 모드" 체크박스로 전환)
  GRID 모드 (기본): 듀얼 그리드 투표
    - Raw grayscale 4x4 (CCOEFF_NORMED, 밝기 불변) → 일반 가림.
    - Canny edge 4x4 (CCORR_NORMED) → 반투명 오버레이 대응.
    - 조각>=: 각 조각 correlation 임계.
    - 투표>=: 32조각 중 매칭 비율.

  ORB 모드: 특징점 매칭 (**반투명/부분가림/약회전 최강 내성**)
    - 픽셀값이 아닌 지역 gradient 패턴으로 매칭.
    - 같은 "템플릿 원점"에 투표한 keypoint 수로 판정.
    - 슬라이더 재해석:
        "조각>=" → ORB Hamming dist 임계 (값 높이면 엄격).
        "투표>=" → 같은 원점에 모여야 할 최소 keypoint 수 (2~10개 매핑).
    - 단점: 템플릿 너무 작거나 질감 없으면 keypoint 부족해서 실패.
      로그에 kp_t=N 보고 → 10개 이상 뜨는 크롭 필요.

L2 HSV Chi-square: 색 히스토그램 검증 (보조).
L3 Edge diff: Canny 엣지 불일치 비율 (보조).

출력 색
  초록 = L1+L2+L3 전부 통과 (확정 후보).
  주황 = L1만 통과, 2차 필터 탈락 (디버깅용).
  박스 위 숫자 = L1/L2/L3 점수.

팁
  - 크롭이 클수록 오탐 감소, 속도 저하.
  - 구별되는 색/모양이 들어간 영역 선택이 포인트.
  - 배경 포함되어도 OK (같은 크롭을 매번 찾는 거라 자동으로 맞음).
"""
from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import BooleanVar, Button, Canvas, Checkbutton, Frame, Label
from tkinter import Scale, StringVar, filedialog
from typing import List, Optional

import cv2
import numpy as np
from PIL import Image, ImageGrab, ImageTk


# -----------------------------------------------------------------------------
# 데이터
# -----------------------------------------------------------------------------
@dataclass
class Sprite:
    name: str
    bgr: np.ndarray          # HxWx3, uint8
    mask: np.ndarray         # HxW, uint8 (0 or 255)
    hist: np.ndarray         # 레퍼런스 HSV 히스토그램
    edges: np.ndarray        # 레퍼런스 Canny 이진맵


# -----------------------------------------------------------------------------
# 로드
# -----------------------------------------------------------------------------
def load_sprite(path: Path) -> Sprite:
    img = Image.open(path)
    if img.mode == 'RGBA':
        arr = np.array(img)
        bgr = cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2BGR)
        alpha = arr[:, :, 3]
        mask = (alpha > 128).astype(np.uint8) * 255
    else:
        arr = np.array(img.convert('RGB'))
        bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        mask = np.full(bgr.shape[:2], 255, np.uint8)

    # 레퍼런스 히스토그램 (H, S 2D).
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], mask, [16, 16], [0, 180, 0, 256])
    cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)

    # 레퍼런스 엣지.
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)

    return Sprite(name=path.stem, bgr=bgr, mask=mask, hist=hist, edges=edges)


def grab_screen_bgr() -> np.ndarray:
    """전체(기본 모니터) 화면 → BGR ndarray."""
    img = ImageGrab.grab()
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


# -----------------------------------------------------------------------------
# 탐지 레이어
# -----------------------------------------------------------------------------
def layer1_match(screen_bgr: np.ndarray, sprite: Sprite, patch_thresh: float,
                 vote_ratio: float):
    """듀얼 그리드 투표 매칭 (가림 + 반투명 내성).

    - Raw grayscale 4x4 조각 x CCOEFF_NORMED (밝기 불변): 일반 가림 대응.
    - Canny edge 4x4 조각 x CCORR_NORMED: 반투명 오버레이 대응.
    - 총 투표 풀 = 16 + 16 = 32. 한쪽 깨져도 다른쪽 보완.
    - 점수 기준: 각 조각 correlation >= patch_thresh → 1표.
    - 투표 합계가 total * vote_ratio 이상인 위치만 후보.

    반환: [(x, y, avg_score, vote_ratio_obs), ...].
    """
    sg = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
    tg = cv2.cvtColor(sprite.bgr, cv2.COLOR_BGR2GRAY)
    th, tw = tg.shape
    sh, sw = sg.shape
    if sh < th or sw < tw:
        return []

    se = cv2.Canny(sg, 50, 150)
    te = cv2.Canny(tg, 50, 150)

    # 그리드 결정: 가능한 4x4, 단 패치 최소 6x6 유지.
    gh = max(1, min(4, th // 6))
    gw = max(1, min(4, tw // 6))
    ph = th // gh
    pw = tw // gw

    vh = sh - th + 1
    vw = sw - tw + 1
    votes = np.zeros((vh, vw), dtype=np.int32)
    score_sum = np.zeros((vh, vw), dtype=np.float32)
    total = 0

    # (source_big, template_big, metric, min_template_sum)
    modes = [
        (sg, tg, cv2.TM_CCOEFF_NORMED, None),       # raw gray
        (se, te, cv2.TM_CCORR_NORMED, 4 * 255),     # canny edge
    ]

    for src, tmpl, metric, min_sum in modes:
        for i in range(gh):
            for j in range(gw):
                y0 = i * ph
                x0 = j * pw
                y1 = y0 + ph if i < gh - 1 else th
                x1 = x0 + pw if j < gw - 1 else tw
                patch = tmpl[y0:y1, x0:x1]
                pph, ppw = patch.shape
                if pph < 4 or ppw < 4:
                    continue
                # 엣지 조각이 거의 비어 있으면 상관계수 정의 불안정 → 스킵.
                if min_sum is not None and int(patch.sum()) < min_sum:
                    continue
                # 정규화 계열은 조각 내 분산이 0이면 nan/inf → 스킵.
                if float(patch.std()) < 1e-6:
                    continue
                try:
                    res = cv2.matchTemplate(src, patch, metric)
                except cv2.error:
                    continue
                res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
                sub = res[y0:y0 + vh, x0:x0 + vw]
                # CCOEFF/CCORR_NORMED: 값 높을수록 유사 (범위 ~ -1..1).
                hit = (sub >= patch_thresh).astype(np.int32)
                votes += hit
                score_sum += np.clip(sub, 0.0, 1.0)
                total += 1

    if total == 0:
        return []
    min_votes = max(1, int(round(total * vote_ratio)))
    ys, xs = np.where(votes >= min_votes)
    if len(ys) == 0:
        return []
    avg_score = score_sum / total
    cands = [(int(x), int(y), float(avg_score[y, x]),
              float(votes[y, x]) / total)
             for y, x in zip(ys, xs)]
    # 투표 많은 순, 동률이면 상관 높은 순.
    cands.sort(key=lambda c: (-c[3], -c[2]))
    # NMS.
    kept = []
    for x, y, s, r in cands:
        if any(abs(x - kx) < tw * 0.5 and abs(y - ky) < th * 0.5
               for kx, ky, _, _ in kept):
            continue
        kept.append((x, y, s, r))
        if len(kept) >= 30:
            break
    return kept


def layer1_orb(screen_bgr: np.ndarray, sprite: Sprite,
               min_matches: int, dist_thresh: int):
    """ORB 특징점 매칭 (반투명/회전/부분가림 내성).

    각 keypoint descriptor는 지역 gradient 패턴 → 픽셀값 블렌딩에 둔감.
    매칭된 keypoint들이 같은 "템플릿 원점"으로 모이면 (공간 투표) 후보.

    반환: [(x, y, score, ratio), ...]
      score = 1 - avg_distance/dist_thresh (0~1, 높을수록 유사)
      ratio = matched / template_keypoints (템플릿 대비 매칭 비율)
    """
    sg = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
    tg = cv2.cvtColor(sprite.bgr, cv2.COLOR_BGR2GRAY)
    th, tw = tg.shape

    # 작은 픽셀아트 템플릿에 맞춘 파라미터.
    orb = cv2.ORB_create(
        nfeatures=1000,
        scaleFactor=1.2,
        nlevels=4,
        edgeThreshold=3,
        patchSize=7,
        fastThreshold=5,
    )
    try:
        kp_t, des_t = orb.detectAndCompute(tg, None)
    except cv2.error:
        return [], 0
    if des_t is None or len(kp_t) < min_matches:
        return [], len(kp_t) if kp_t else 0

    try:
        kp_s, des_s = orb.detectAndCompute(sg, None)
    except cv2.error:
        return [], len(kp_t)
    if des_s is None or len(kp_s) == 0:
        return [], len(kp_t)

    try:
        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        raw_matches = bf.match(des_t, des_s)
    except cv2.error:
        return [], len(kp_t)

    matches = [m for m in raw_matches if m.distance < dist_thresh]
    if len(matches) < min_matches:
        return [], len(kp_t)

    # 같은 "템플릿 원점"에 투표한 매칭 수집 (공간 클러스터링).
    cell = max(8, min(tw, th) // 4)
    bins: dict = {}
    for m in matches:
        tp = kp_t[m.queryIdx].pt
        sp = kp_s[m.trainIdx].pt
        ox = sp[0] - tp[0]
        oy = sp[1] - tp[1]
        key = (int(ox // cell), int(oy // cell))
        bins.setdefault(key, []).append((ox, oy, m.distance))

    sh, sw = sg.shape
    cands = []
    for entries in bins.values():
        if len(entries) < min_matches:
            continue
        ax = sum(e[0] for e in entries) / len(entries)
        ay = sum(e[1] for e in entries) / len(entries)
        ad = sum(e[2] for e in entries) / len(entries)
        if ax < 0 or ay < 0:
            continue
        if ax + tw > sw or ay + th > sh:
            continue
        score = max(0.0, 1.0 - ad / max(dist_thresh, 1))
        ratio = len(entries) / max(len(kp_t), 1)
        cands.append((int(ax), int(ay), score, ratio))

    cands.sort(key=lambda c: (-c[3], -c[2]))
    # NMS.
    kept = []
    for x, y, s, r in cands:
        if any(abs(x - kx) < tw * 0.5 and abs(y - ky) < th * 0.5
               for kx, ky, _, _ in kept):
            continue
        kept.append((x, y, s, r))
        if len(kept) >= 20:
            break
    return kept, len(kp_t)


def layer2_hist(patch_bgr: np.ndarray, sprite: Sprite) -> float:
    """HSV H,S 히스토그램 chi-square. 낮을수록 유사."""
    hsv = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2HSV)
    ph = cv2.calcHist([hsv], [0, 1], sprite.mask, [16, 16], [0, 180, 0, 256])
    cv2.normalize(ph, ph, 0, 1, cv2.NORM_MINMAX)
    return float(cv2.compareHist(ph, sprite.hist, cv2.HISTCMP_CHISQR_ALT))


def layer3_edge(patch_bgr: np.ndarray, sprite: Sprite) -> float:
    """Canny 엣지 mask 영역 내 불일치 비율. 낮을수록 유사."""
    pg = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2GRAY)
    pe = cv2.Canny(pg, 50, 150)
    mbin = sprite.mask > 0
    diff = int(np.sum((pe != sprite.edges) & mbin))
    total = int(mbin.sum())
    return diff / max(total, 1)


# -----------------------------------------------------------------------------
# GUI
# -----------------------------------------------------------------------------
class App:
    CANVAS_W = 1280
    CANVAS_H = 720

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Sprite Detect Test")
        self.sprites: List[Sprite] = []
        self.live = BooleanVar(value=False)
        self.use_orb = BooleanVar(value=False)
        self.last_screen: Optional[np.ndarray] = None
        self.photo: Optional[ImageTk.PhotoImage] = None

        # 상단 버튼 줄.
        top = Frame(self.root)
        top.pack(side='top', fill='x', padx=4, pady=4)
        Button(top, text="Load Sprite(s)", command=self.on_load,
               width=14).pack(side='left', padx=2)
        Button(top, text="Capture", command=self.on_capture,
               width=10).pack(side='left', padx=2)
        Checkbutton(top, text="Live (0.5s)", variable=self.live,
                    command=self.on_live).pack(side='left', padx=6)
        Checkbutton(top, text="ORB 모드(반투명/가림 강함)",
                    variable=self.use_orb).pack(side='left', padx=6)

        self.status = StringVar(value="스프라이트 먼저 로드하세요.")
        Label(self.root, textvariable=self.status, anchor='w',
              font=('Consolas', 10)).pack(side='top', fill='x', padx=4)

        # 임계값 슬라이더. (CCOEFF/CCORR 기반 → 값 클수록 유사)
        th = Frame(self.root)
        th.pack(side='top', fill='x', padx=4, pady=2)
        Label(th, text="L1 조각>=", width=12, anchor='w').pack(side='left')
        self.l1 = Scale(th, from_=0.00, to=1.00, resolution=0.02,
                        orient=tk.HORIZONTAL, length=300)
        self.l1.set(0.60)
        self.l1.pack(side='left')

        th_r = Frame(self.root)
        th_r.pack(side='top', fill='x', padx=4, pady=2)
        Label(th_r, text="L1 투표>=", width=12, anchor='w').pack(side='left')
        self.l1r = Scale(th_r, from_=0.0, to=1.0, resolution=0.05,
                         orient=tk.HORIZONTAL, length=300)
        self.l1r.set(0.40)
        self.l1r.pack(side='left')

        th2 = Frame(self.root)
        th2.pack(side='top', fill='x', padx=4, pady=2)
        Label(th2, text="L2 HSV Chi<", width=12, anchor='w').pack(side='left')
        self.l2 = Scale(th2, from_=0.0, to=5.0, resolution=0.1,
                        orient=tk.HORIZONTAL, length=300)
        self.l2.set(1.5)
        self.l2.pack(side='left')

        th3 = Frame(self.root)
        th3.pack(side='top', fill='x', padx=4, pady=2)
        Label(th3, text="L3 Edge<", width=12, anchor='w').pack(side='left')
        self.l3 = Scale(th3, from_=0.00, to=1.00, resolution=0.01,
                        orient=tk.HORIZONTAL, length=300)
        self.l3.set(0.30)
        self.l3.pack(side='left')

        # 캔버스.
        self.canvas = Canvas(self.root, width=self.CANVAS_W,
                             height=self.CANVAS_H, bg='black',
                             highlightthickness=1)
        self.canvas.pack(side='top', padx=4, pady=4)

        # 로그.
        self.log = StringVar(value="")
        Label(self.root, textvariable=self.log, anchor='w',
              font=('Consolas', 9), fg='#333').pack(
            side='top', fill='x', padx=4, pady=2)

        # 안내.
        hint = ("반투명/두꺼운 가림엔 ORB 모드 체크. "
                "GRID=픽셀상관(깔끔한 환경), ORB=gradient패턴(가림 강함). "
                "ORB에선 크롭을 살짝 더 크게(30+px), 질감 있는 부분으로.")
        Label(self.root, text=hint, anchor='w', fg='#555',
              font=('Malgun Gothic', 9)).pack(
            side='top', fill='x', padx=4, pady=2)

    # -------------------------------------------------------------------------
    # 이벤트
    # -------------------------------------------------------------------------
    def on_load(self) -> None:
        paths = filedialog.askopenfilenames(
            title="크롭 이미지 선택 (PNG/JPG/BMP, 배경 투명화 불필요)",
            filetypes=[("Image", "*.png *.jpg *.jpeg *.bmp"),
                       ("All", "*.*")])
        if not paths:
            return
        try:
            self.sprites = [load_sprite(Path(p)) for p in paths]
        except Exception as e:
            self.status.set(f"로드 실패: {e}")
            return
        names = ", ".join(s.name for s in self.sprites)
        self.status.set(f"로드 {len(self.sprites)}개: {names}")

    def on_capture(self) -> None:
        if not self.sprites:
            self.status.set("먼저 Load Sprite.")
            return
        self._scan()

    def on_live(self) -> None:
        if self.live.get():
            self._tick()

    def _tick(self) -> None:
        if not self.live.get():
            return
        if self.sprites:
            self._scan()
        self.root.after(500, self._tick)

    # -------------------------------------------------------------------------
    # 스캔
    # -------------------------------------------------------------------------
    def _scan(self) -> None:
        try:
            screen = grab_screen_bgr()
        except Exception as e:
            self.status.set(f"캡처 실패: {e}")
            return
        self.last_screen = screen

        t1 = float(self.l1.get())
        t1r = float(self.l1r.get())
        t2 = float(self.l2.get())
        t3 = float(self.l3.get())
        use_orb = bool(self.use_orb.get())

        overlay = screen.copy()
        per_sprite = []
        total_l1 = 0
        total_ok = 0

        for sp in self.sprites:
            if use_orb:
                # ORB 모드: L1 임계/투표율 재해석.
                # - dist_thresh: Hamming 거리 한계 (낮을수록 엄격).
                #   슬라이더 t1(0~1) → dist_thresh 80~10 매핑.
                # - min_matches: 모여야 하는 매칭 최소 수.
                #   슬라이더 t1r(0~1) → 2~10 매핑.
                dist_thresh = int(max(10, 80 - t1 * 70))
                min_matches = max(2, int(round(t1r * 10)))
                cands, kp_count = layer1_orb(screen, sp, min_matches,
                                             dist_thresh)
            else:
                cands = layer1_match(screen, sp, t1, t1r)
                kp_count = 0
            h, w = sp.bgr.shape[:2]
            verified = 0
            for x, y, s1, ratio in cands:
                patch = screen[y:y + h, x:x + w]
                if patch.shape[:2] != (h, w):
                    continue
                s2 = layer2_hist(patch, sp)
                s3 = layer3_edge(patch, sp)
                ok = (s2 < t2) and (s3 < t3)
                color = (0, 255, 0) if ok else (0, 165, 255)
                cv2.rectangle(overlay, (x, y), (x + w, y + h), color, 2)
                if use_orb:
                    label = (f"{sp.name[:8]} orb={ratio*100:.0f}% "
                             f"{s1:.2f}/{s2:.1f}/{s3:.2f}")
                else:
                    label = (f"{sp.name[:8]} v={ratio*100:.0f}% "
                             f"{s1:.2f}/{s2:.1f}/{s3:.2f}")
                cv2.putText(overlay, label, (x, max(y - 5, 12)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1,
                            cv2.LINE_AA)
                if ok:
                    verified += 1
            if use_orb:
                per_sprite.append(
                    f"{sp.name}: ORB kp_t={kp_count} L1={len(cands)} "
                    f"OK={verified}")
            else:
                per_sprite.append(
                    f"{sp.name}: L1={len(cands)} OK={verified}")
            total_l1 += len(cands)
            total_ok += verified

        mode = "ORB" if use_orb else "GRID"
        if use_orb:
            dist_thresh = int(max(10, 80 - t1 * 70))
            min_matches = max(2, int(round(t1r * 10)))
            self.status.set(
                f"[{mode}] 후보={total_l1}  최종(3층)={total_ok}  "
                f"[dist<{dist_thresh} min_kp={min_matches} "
                f"L2<{t2:.2f} L3<{t3:.2f}]")
        else:
            self.status.set(
                f"[{mode}] 후보={total_l1}  최종(3층)={total_ok}  "
                f"[조각>={t1:.2f} 투표>={t1r:.2f} "
                f"L2<{t2:.2f} L3<{t3:.2f}]")
        self.log.set(" | ".join(per_sprite))

        self._render(overlay)

    def _render(self, overlay_bgr: np.ndarray) -> None:
        oh, ow = overlay_bgr.shape[:2]
        scale = min(self.CANVAS_W / ow, self.CANVAS_H / oh)
        nw, nh = int(ow * scale), int(oh * scale)
        resized = cv2.resize(overlay_bgr, (nw, nh),
                             interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        self.photo = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.canvas.delete('all')
        self.canvas.create_image(self.CANVAS_W // 2, self.CANVAS_H // 2,
                                 image=self.photo)

    def run(self) -> None:
        self.root.mainloop()


# -----------------------------------------------------------------------------
if __name__ == '__main__':
    App().run()
