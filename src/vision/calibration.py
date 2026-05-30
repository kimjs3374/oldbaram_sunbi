"""world (a_coord-h_coord) → screen px 캘리브레이션 수집기.

비침습. 관찰+로그 전용. 기존 동작에 영향 없음.

[원리]
- 빨탭(red_px)은 격수 머리 위에 뜸. 격수가 힐러 좌표와 같은 타일(a==h)에
  있으면 red_px ≈ 힐러 머리 px.
- 각 샘플: (dx=a.x-h.x, dy=a.y-h.y, red_px.x, red_px.y).
- 선형 회귀 (x/y 독립):
    red_px.x = scale_x * dx + offset_x
    red_px.y = scale_y * dy + offset_y
  offset은 "동일 타일일 때 빨탭 픽셀 위치" (힐러 머리 기준점).

[복구 로직에서 사용]
- predict_px(h, a) → 격수 빨탭 예상 screen px.
- 관측 red_px와의 거리가 임계값 이상이면 "빨탭이 다른 대상에 걸림"으로 판정.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class _Sample:
    dx: int
    dy: int
    rx: float
    ry: float
    ts: float


class Calibrator:
    """비침습 캘리브레이션 수집기.

    사용법:
      cal = Calibrator(log)
      # 프레임마다:
      cal.maybe_sample(now, h_coord, a_coord, red_px,
                        same_map, red_raw, white_raw, coord_stable)
      # 예측:
      px = cal.predict_px(h_coord, a_coord)  # None이면 미준비
    """

    # 샘플 유효 조건·수집 파라미터.
    MIN_SAMPLE_INTERVAL_S = 0.4      # 같은 상황 중복 방지.
    MAX_SAMPLES = 300                # 롤링 윈도우.
    MIN_SAMPLES_READY = 30           # ready 최소 샘플.
    MIN_DX_SPREAD = 3                # dx 다양성 최소 범위 (타일 단위).
    MIN_DY_SPREAD = 3                # dy 다양성 최소 범위.
    LOG_INTERVAL_S = 30.0            # 통계 주기 로그.

    def __init__(self, log: logging.Logger):
        self.log = log
        self._samples: List[_Sample] = []
        self._scale_x: Optional[float] = None
        self._scale_y: Optional[float] = None
        self._offset_x: Optional[float] = None
        self._offset_y: Optional[float] = None
        self._resid_x: Optional[float] = None   # RMS 잔차 (안정도 판정).
        self._resid_y: Optional[float] = None
        self._last_sample_ts: float = 0.0
        self._last_log_ts: float = 0.0
        self._ready_logged: bool = False

    # ---------------------------------------------------------------
    # 수집
    # ---------------------------------------------------------------
    def maybe_sample(
        self,
        now: float,
        h_coord: Optional[Tuple[int, int]],
        a_coord: Optional[Tuple[int, int]],
        red_px: Optional[Tuple[float, float]],
        *,
        same_map: bool,
        red_raw: bool,
        white_raw: bool,
        coord_valid: bool,
    ) -> None:
        """유효 프레임이면 샘플 추가, 아니면 무시."""
        # 필수 조건: 동일 맵 + red only + 좌표 유효 + 값 존재.
        if not (same_map and red_raw and (not white_raw) and coord_valid):
            return
        if h_coord is None or a_coord is None or red_px is None:
            return
        if (now - self._last_sample_ts) < self.MIN_SAMPLE_INTERVAL_S:
            return

        dx = int(a_coord[0]) - int(h_coord[0])
        dy = int(a_coord[1]) - int(h_coord[1])
        # 동일 (dx,dy) 최근 샘플 다수면 pass (다양성 확보).
        recent_same = sum(
            1 for s in self._samples[-20:] if s.dx == dx and s.dy == dy
        )
        if recent_same >= 3:
            return

        self._samples.append(
            _Sample(dx=dx, dy=dy, rx=float(red_px[0]), ry=float(red_px[1]),
                    ts=now)
        )
        if len(self._samples) > self.MAX_SAMPLES:
            self._samples = self._samples[-self.MAX_SAMPLES:]
        self._last_sample_ts = now

        self._fit()
        self._maybe_log(now)

    # ---------------------------------------------------------------
    # 피팅 (OLS)
    # ---------------------------------------------------------------
    def _fit(self) -> None:
        n = len(self._samples)
        if n < self.MIN_SAMPLES_READY:
            return
        dxs = [s.dx for s in self._samples]
        dys = [s.dy for s in self._samples]
        if (max(dxs) - min(dxs) < self.MIN_DX_SPREAD
                or max(dys) - min(dys) < self.MIN_DY_SPREAD):
            return  # 다양성 부족 → 지난 추정 유지.

        sx = self._ols_1d([(s.dx, s.rx) for s in self._samples])
        sy = self._ols_1d([(s.dy, s.ry) for s in self._samples])
        if sx is None or sy is None:
            return
        self._scale_x, self._offset_x, self._resid_x = sx
        self._scale_y, self._offset_y, self._resid_y = sy

    @staticmethod
    def _ols_1d(
        points: List[Tuple[int, float]],
    ) -> Optional[Tuple[float, float, float]]:
        """단순 선형 회귀. 반환: (slope, intercept, rms_residual)."""
        n = len(points)
        if n < 3:
            return None
        sx = sum(p[0] for p in points)
        sy = sum(p[1] for p in points)
        sxx = sum(p[0] * p[0] for p in points)
        sxy = sum(p[0] * p[1] for p in points)
        denom = n * sxx - sx * sx
        if denom == 0:
            return None
        slope = (n * sxy - sx * sy) / denom
        intercept = (sy - slope * sx) / n
        # RMS 잔차.
        resid_sq = 0.0
        for x, y in points:
            pred = slope * x + intercept
            resid_sq += (y - pred) ** 2
        rms = (resid_sq / n) ** 0.5
        return slope, intercept, rms

    # ---------------------------------------------------------------
    # 질의
    # ---------------------------------------------------------------
    def ready(self) -> bool:
        return (
            self._scale_x is not None
            and self._scale_y is not None
            and len(self._samples) >= self.MIN_SAMPLES_READY
        )

    def predict_px(
        self,
        h_coord: Optional[Tuple[int, int]],
        a_coord: Optional[Tuple[int, int]],
    ) -> Optional[Tuple[float, float]]:
        if not self.ready():
            return None
        if h_coord is None or a_coord is None:
            return None
        dx = int(a_coord[0]) - int(h_coord[0])
        dy = int(a_coord[1]) - int(h_coord[1])
        assert self._scale_x is not None and self._scale_y is not None
        assert self._offset_x is not None and self._offset_y is not None
        return (self._scale_x * dx + self._offset_x,
                self._scale_y * dy + self._offset_y)

    def stats(self) -> dict:
        return {
            'n': len(self._samples),
            'scale_x': self._scale_x,
            'scale_y': self._scale_y,
            'offset_x': self._offset_x,
            'offset_y': self._offset_y,
            'resid_x': self._resid_x,
            'resid_y': self._resid_y,
        }

    # ---------------------------------------------------------------
    # 로그
    # ---------------------------------------------------------------
    def _maybe_log(self, now: float) -> None:
        is_ready = self.ready()
        if is_ready and not self._ready_logged:
            self._ready_logged = True
            s = self.stats()
            self.log.info(
                f"[CAL-READY] n={s['n']} "
                f"scale=({s['scale_x']:.2f},{s['scale_y']:.2f}) "
                f"offset=({s['offset_x']:.1f},{s['offset_y']:.1f}) "
                f"resid=({s['resid_x']:.1f},{s['resid_y']:.1f})"
            )
            self._last_log_ts = now
            return
        if (now - self._last_log_ts) >= self.LOG_INTERVAL_S:
            s = self.stats()
            if is_ready:
                self.log.info(
                    f"[CAL] n={s['n']} "
                    f"scale=({s['scale_x']:.2f},{s['scale_y']:.2f}) "
                    f"offset=({s['offset_x']:.1f},{s['offset_y']:.1f}) "
                    f"resid=({s['resid_x']:.1f},{s['resid_y']:.1f})"
                )
            else:
                self.log.info(
                    f"[CAL-WAIT] n={s['n']} (need "
                    f"{self.MIN_SAMPLES_READY}+ with spread "
                    f">={self.MIN_DX_SPREAD}/{self.MIN_DY_SPREAD})"
                )
            self._last_log_ts = now
