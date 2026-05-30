"""YOLO adapter — wraps src/vision/yolo.py inference object.

Translates whatever the src yolo wrapper returns into the v2 prediction
tuple format: (cls_name, x1, y1, x2, y2, conf).
"""
from __future__ import annotations
import logging
from typing import Any, List, Tuple

log = logging.getLogger("src_v2.adapters.yolo")


class SrcYoloAdapter:
    def __init__(self, src_yolo: Any) -> None:
        self._y = src_yolo

    def predict(self, frame: Any) -> List[Tuple[str, int, int, int, int, float]]:
        if self._y is None or frame is None:
            return []
        try:
            # Try common method names
            for m in ("predict", "infer", "detect", "run"):
                fn = getattr(self._y, m, None)
                if callable(fn):
                    raw = fn(frame)
                    return self._normalize(raw)
            return []
        except Exception:  # noqa: BLE001
            log.exception("yolo predict fail")
            return []

    def _normalize(self, raw: Any) -> List[Tuple[str, int, int, int, int, float]]:
        out: List[Tuple[str, int, int, int, int, float]] = []
        if raw is None:
            return out
        # raw could be a list of dicts, list of tuples, ultralytics Results, etc.
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    cls = item.get("cls") or item.get("name") or item.get("label")
                    bbox = item.get("bbox") or item.get("xyxy")
                    conf = item.get("conf") or item.get("confidence") or 0.0
                    if cls and bbox is not None and len(bbox) >= 4:
                        x1, y1, x2, y2 = [int(v) for v in bbox[:4]]
                        out.append((str(cls), x1, y1, x2, y2, float(conf)))
                elif isinstance(item, (tuple, list)) and len(item) >= 6:
                    cls, x1, y1, x2, y2, conf = item[:6]
                    out.append((str(cls), int(x1), int(y1), int(x2), int(y2), float(conf)))
        return out

    def is_available(self) -> bool:
        return self._y is not None

    def stop(self) -> None:
        try:
            fn = getattr(self._y, "stop", None)
            if callable(fn):
                fn()
        except Exception:  # noqa: BLE001
            log.exception("yolo stop fail")


class RealYoloAdapter(SrcYoloAdapter):
    """Production adapter — wraps src.vision.yolo.AsyncYolo.

    detect outputs are translated into v2 YOLO predictions via _normalize.

    age_ms 4-zone freshness policy (2026-05-02):
      < 0          : no result yet  → submit, return []
      0 ~ FRESH_MS : just completed → skip submit, return cached (P1-low)
      FRESH_MS ~ STALE_MS : normal  → submit + return result
      > STALE_MS   : stale drop     → submit, return [], log YOLO-STALE

    2026-05-05 Cycle 2 (Task 9) STALE_MS 150 → 250ms 상향:
      5/5 11:21 baseline 로그 측정값 [YOLO-PROF] predict=200~310ms.
      150ms 임계는 매 tick stale drop 발생 → red/white 검출 결과 빈번 무효화.
      250ms 로 상향하면 정상 zone (FRESH_MS~STALE_MS) 대부분의 prediction 수용.
      300ms+ 인 outlier 만 drop. white_cache_ttl_ms=250 과 일관.
    """

    STALE_MS: float = 250.0
    FRESH_MS: float = 30.0

    def __init__(self, weights: str, imgsz: int = 640, conf: float = 0.25,
                 iou: float = 0.45, half: bool = False, device: str = "cuda:0",
                 log_fn=None) -> None:
        from src.vision.yolo import YoloRunner, AsyncYolo  # lazy
        runner = YoloRunner(
            weights, imgsz=imgsz, conf=conf, iou=iou,
            half=half, device=device, log_fn=log_fn,
        )
        self._async = AsyncYolo(runner)
        super().__init__(self._async)
        self._weights = weights
        self._log_fn = log_fn
        self._last_result: list = []
        self._last_meta: dict = {}

    def predict(self, frame):
        if frame is None:
            return []
        try:
            # 단일 읽기 — latest() 이중 호출 race 방지
            new_dets, _off, age_ms, predict_ms = self._async.latest()

            if age_ms < 0:
                self._async.submit(frame, (0, 0))
                return []

            if age_ms > self.STALE_MS:
                self._async.submit(frame, (0, 0))
                self._log_stale(age_ms)
                return []

            if age_ms < self.FRESH_MS:
                # 방금 완료 — 재submit 불필요, 캐시 재소비
                return self._last_result

            # 정상 window
            self._async.submit(frame, (0, 0))
            result = self._build_result(new_dets)
            self._last_result = result
            self._last_meta = {
                "age_ms": age_ms,
                "predict_ms": predict_ms,
                "frame_shape": getattr(frame, "shape", None),
            }
            return result
        except Exception:  # noqa: BLE001
            log.exception("real yolo predict fail")
            return []

    def _build_result(self, dets) -> list:
        out = []
        for d in dets:
            cls = "red_tab" if getattr(d, "tab_color", "") == "RED" else (
                "white_tab" if getattr(d, "tab_color", "") == "WHITE" else "obj"
            )
            out.append((cls, int(d.x1), int(d.y1), int(d.x2), int(d.y2),
                        float(getattr(d, "conf", 0.0))))
        return out

    def _log_stale(self, age_ms: float) -> None:
        log.debug("YOLO-STALE age_ms=%.0f drop", age_ms)
        if callable(self._log_fn):
            try:
                self._log_fn(f"[YOLO-STALE] age_ms={age_ms:.0f}ms drop")
            except Exception:
                pass

    def stop(self) -> None:
        try:
            self._async.stop()
        except Exception:  # noqa: BLE001
            pass
