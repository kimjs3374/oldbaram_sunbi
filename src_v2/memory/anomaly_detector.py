"""AnomalyDetector — 시계열 이상치 감지.

action_log + snapshot 60s 윈도 분석 → z-score 기반 이상치:

metrics:
  - ocr_success_rate (snap.healer_coord != None 비율)
  - fps_avg (snap.fps)
  - cast_success_rate (action_log result=ok 비율)
  - coord_change_rate (healer_coord 변화 빈도)
  - yolo_detect_rate (snap.red_tab_present 비율)

z > z_threshold 시 bus.publish("memory.anomaly", {metric, value, baseline, z_score}).

baseline 은 30분 윈도 평균 + 표준편차. 부트스트랩(첫 baseline_min_samples 까지)
은 무조건 normal 로 처리.
"""
from __future__ import annotations
import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Optional

from ..core.event_bus import EventBus
from ..core.snapshot import SnapshotStore
from .action_log import ActionLog

log = logging.getLogger("src_v2.memory.anomaly_detector")


@dataclass
class _Sample:
    ts: float
    healer_coord: Optional[Any]
    fps: float
    red_tab_present: bool


def _mean_std(xs: List[float]) -> tuple:
    n = len(xs)
    if n == 0:
        return 0.0, 0.0
    m = sum(xs) / n
    if n < 2:
        return m, 0.0
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return m, math.sqrt(max(0.0, var))


class AnomalyDetector:
    """시계열 이상치 감지 워커.

    self._samples: 최근 N분 snap sample
    각 metric 마다 short window (60s) value 와 long window (1800s) baseline 비교.
    """

    def __init__(self,
                 store: SnapshotStore,
                 action_log: ActionLog,
                 bus: EventBus,
                 sample_sec: float = 1.0,
                 short_window_sec: float = 60.0,
                 long_window_sec: float = 1800.0,
                 z_threshold: float = 2.0,
                 emit_min_interval_sec: float = 30.0,
                 baseline_min_samples: int = 30,
                 enabled: bool = True) -> None:
        self.store = store
        self.action_log = action_log
        self.bus = bus
        self.sample_sec = float(sample_sec)
        self.short_window_sec = float(short_window_sec)
        self.long_window_sec = float(long_window_sec)
        self.z_threshold = float(z_threshold)
        self.emit_min_interval_sec = float(emit_min_interval_sec)
        self.baseline_min_samples = int(baseline_min_samples)
        self.enabled = enabled
        # 샘플 deque — long_window_sec / sample_sec 만큼 보유
        cap = int(self.long_window_sec / max(0.1, self.sample_sec)) + 16
        self._samples: Deque[_Sample] = deque(maxlen=cap)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_emit: Dict[str, float] = {}
        self._counts: Dict[str, int] = {"emitted": 0, "checks": 0}
        self.history: List[Dict[str, Any]] = []
        self._history_cap = 128

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="anomaly_detector", daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def stats(self) -> Dict[str, Any]:
        return dict(
            self._counts,
            samples=len(self._samples),
        )

    def _loop(self) -> None:
        while not self._stop.wait(self.sample_sec):
            try:
                self._sample()
                self._check()
            except Exception:
                log.exception("anomaly tick fail")

    def _sample(self) -> None:
        s = self.store.read()
        self._samples.append(_Sample(
            ts=time.monotonic(),
            healer_coord=getattr(s, "healer_coord", None),
            fps=float(getattr(s, "fps", 0.0) or 0.0),
            red_tab_present=bool(getattr(s, "red_tab_present", False)),
        ))

    def _check(self) -> None:
        if not self.enabled:
            return
        if len(self._samples) < self.baseline_min_samples:
            return
        now = time.monotonic()
        short_cut = now - self.short_window_sec
        long_cut = now - self.long_window_sec
        short = [s for s in self._samples if s.ts >= short_cut]
        long_ = [s for s in self._samples if s.ts >= long_cut]
        if len(long_) < self.baseline_min_samples or not short:
            return

        # ---- metric 계산 ----
        # ocr_success_rate
        short_ocr = sum(1 for s in short if s.healer_coord is not None) / max(1, len(short))
        long_ocr_series = [1.0 if s.healer_coord is not None else 0.0 for s in long_]
        m, sd = _mean_std(long_ocr_series)
        self._test("ocr_success_rate", short_ocr, m, sd, lower_bad=True)

        # fps_avg
        short_fps_vals = [s.fps for s in short if s.fps > 0]
        if short_fps_vals:
            short_fps = sum(short_fps_vals) / len(short_fps_vals)
            long_fps_series = [s.fps for s in long_ if s.fps > 0]
            m, sd = _mean_std(long_fps_series)
            self._test("fps_avg", short_fps, m, sd, lower_bad=True)

        # yolo_detect_rate
        short_yolo = sum(1 for s in short if s.red_tab_present) / max(1, len(short))
        long_yolo_series = [1.0 if s.red_tab_present else 0.0 for s in long_]
        m, sd = _mean_std(long_yolo_series)
        # yolo_detect_rate 는 "급락" 만 이상치 (lower_bad)
        self._test("yolo_detect_rate", short_yolo, m, sd, lower_bad=True)

        # coord_change_rate — 직전 샘플 대비 좌표 변화 비율
        changes = 0
        prev = None
        for s in short:
            if prev is not None and s.healer_coord and prev:
                if s.healer_coord != prev:
                    changes += 1
            prev = s.healer_coord
        short_change = changes / max(1, len(short) - 1)
        # baseline 도 동일 방식
        long_changes = 0
        prev = None
        for s in long_:
            if prev is not None and s.healer_coord and prev:
                if s.healer_coord != prev:
                    long_changes += 1
            prev = s.healer_coord
        long_rate = long_changes / max(1, len(long_) - 1)
        # 변화율 너무 낮으면 (stuck) 이상치
        # std 는 포아송 근사 sqrt(p*(1-p)/n)
        n = max(1, len(long_) - 1)
        sd = math.sqrt(max(1e-6, long_rate * (1 - long_rate) / n))
        self._test("coord_change_rate", short_change, long_rate, sd, lower_bad=True)

        # cast_success_rate (action_log 기반)
        try:
            recs = self.action_log.recent(200)
        except Exception:
            recs = []
        if recs:
            cuts = [r for r in recs if r.ts >= short_cut]
            if cuts:
                short_ok = sum(1 for r in cuts if r.result == "ok") / len(cuts)
                long_recs = [r for r in recs if r.ts >= long_cut]
                if len(long_recs) >= 10:
                    long_series = [1.0 if r.result == "ok" else 0.0 for r in long_recs]
                    m, sd = _mean_std(long_series)
                    self._test("cast_success_rate", short_ok, m, sd, lower_bad=True)

        self._counts["checks"] += 1

    def _test(self, metric: str, value: float, baseline: float, sd: float, lower_bad: bool) -> None:
        if sd <= 1e-6:
            return  # baseline degenerate — skip
        z = (baseline - value) / sd if lower_bad else (value - baseline) / sd
        if z <= self.z_threshold:
            return
        # rate-limit emit
        now = time.monotonic()
        last = self._last_emit.get(metric, 0.0)
        if now - last < self.emit_min_interval_sec:
            return
        self._last_emit[metric] = now
        self._counts["emitted"] += 1
        ev = {
            "ts": now,
            "metric": metric,
            "value": value,
            "baseline": baseline,
            "sd": sd,
            "z_score": z,
        }
        self._record(ev)
        try:
            self.bus.publish("memory.anomaly", ev)
        except Exception:
            log.exception("anomaly publish fail metric=%s", metric)

    def _record(self, e: Dict[str, Any]) -> None:
        self.history.append(e)
        if len(self.history) > self._history_cap:
            self.history = self.history[-self._history_cap:]
