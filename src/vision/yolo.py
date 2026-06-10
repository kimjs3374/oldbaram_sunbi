"""YOLOv8 탭 검출기 (nc=2: red_tab=0, white_tab=1).

색상 구분은 YOLO 클래스 id로 직판정. 과거 `_classify_tab_color()` 후처리
(top25%V + R_bias)는 플리커 원인이라 제거 — full_v2 재학습으로 대체.
"""
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Tuple

import numpy as np
from ultralytics import YOLO

try:
    import torch  # ultralytics 내부 의존성. device 확인용.
except Exception:
    torch = None


# nc=2 클래스 매핑
CLS_RED = 0
CLS_WHITE = 1


@dataclass
class Detection:
    x1: int; y1: int; x2: int; y2: int
    conf: float
    cls: int
    tab_color: str = "RED"   # "RED" | "WHITE" — cls id에서 도출

    @property
    def cx(self) -> int:
        return (self.x1 + self.x2) // 2

    @property
    def cy(self) -> int:
        return (self.y1 + self.y2) // 2

    @property
    def w(self) -> int:
        return self.x2 - self.x1

    @property
    def h(self) -> int:
        return self.y2 - self.y1


try:
    _nvml = None
    import pynvml as _nvml  # type: ignore
    try:
        _nvml.nvmlInit()
    except Exception:
        _nvml = None
except Exception:
    _nvml = None


def _gpu_util_pct(device: int = 0) -> int:
    """NVML 있으면 GPU 사용률(%) 반환. 없거나 실패면 -1."""
    if _nvml is None:
        return -1
    try:
        h = _nvml.nvmlDeviceGetHandleByIndex(int(device))
        u = _nvml.nvmlDeviceGetUtilizationRates(h)
        return int(u.gpu)
    except Exception:
        return -1


class YoloRunner:
    def __init__(self, weights: str, imgsz: int = 640, conf: float = 0.25,
                 iou: float = 0.5, half: bool = True, device: int = 0,
                 log_fn=None):
        w = Path(weights)
        if not w.exists():
            raise FileNotFoundError(f"weights not found: {w}")
        # 백엔드 자동 선택 (2026-06-09): CUDA 가용 → PyTorch+GPU.
        # CUDA 없음(또는 device='cpu'/-1) → 형제 .onnx 를 ONNX Runtime CPU 로.
        # GPU 없는 저사양 PC(i5-6600 등) 지원. ONNX CPU 는 PyTorch CPU 대비
        # 9~10배 빠름 (실측 2026-06-09: yolov8s imgsz320 PyTorch 129ms→ONNX 13ms).
        cuda_ok = bool(torch.cuda.is_available()) if torch is not None else False
        want_cpu = (not cuda_ok) or (str(device).strip().lower() in ("cpu", "-1"))
        chosen = w
        self._backend = "pytorch"
        if want_cpu:
            if w.suffix.lower() == ".onnx":
                chosen, self._backend = w, "onnx"
            else:
                onnx_sib = w.with_suffix(".onnx")
                if onnx_sib.exists():
                    chosen, self._backend = onnx_sib, "onnx"
                else:
                    # .onnx 동봉 안 됨 → PyTorch CPU 폴백 (느림). 경고로 노출.
                    self._backend = "pytorch-cpu"
        # cudnn.benchmark: CUDA(PyTorch GPU) 경로에서만 의미. 입력 크기 고정 시
        # 최적 kernel 캐시 (fp16 JIT 반복 감소). CPU/ONNX 경로에선 무관.
        # Patch 2.19: TensorRT engine 자동탐지 폐기 유지 (기계종속 스파이크).
        if torch is not None and not want_cpu:
            try:
                torch.backends.cudnn.benchmark = True
            except Exception:
                pass
        self.model = YOLO(str(chosen))
        if log_fn is not None:
            try:
                log_fn(f"[YOLO-INIT] backend={self._backend} weights={chosen.name} "
                       f"cuda_ok={cuda_ok} want_cpu={want_cpu}")
            except Exception:
                pass
        self.imgsz = imgsz
        self.conf = conf
        self.iou = iou
        # CPU 경로: half(fp16) 무의미 → False, device 'cpu' 정규화.
        if want_cpu:
            self.half = False
            self.device = "cpu"
        else:
            self.half = half
            self.device = device
        # device 실제 적용 결과 확인. CUDA 미설치/드라이버 문제면 CPU 폴백 발생.
        # yolo 추론이 100ms+ 찍히는 주범 진단용 (2026-04-20).
        self._log_device(log_fn)
        # warmup
        self._predict(np.zeros((720, 1280, 3), dtype=np.uint8))
        self._log_device_post_warmup(log_fn)
        # 레이턴시 프로파일 (N회마다 1회 상세 분해 로그).
        self._profile_log_fn = log_fn
        self._profile_every = 30
        self._profile_tick = 0
        # 스파이크 로그: predict+post 합이 이 ms 초과하면 프레임마다 기록.
        # [PERF] yolo 평균이 [YOLO-PROF] predict 개별값과 크게 어긋날 때
        # 안 찍힌 프레임 스파이크를 잡기 위함. 로그 폭주 방지 위해 최소 100ms 스로틀.
        self._spike_threshold_ms = 25
        self._last_spike_log_t = 0.0

    def _log_device(self, log_fn) -> None:
        if log_fn is None:
            return
        try:
            cuda_avail = bool(torch.cuda.is_available()) if torch else False
            cuda_count = int(torch.cuda.device_count()) if torch else 0
            cuda_name = (
                torch.cuda.get_device_name(self.device)
                if (torch and cuda_avail and isinstance(self.device, int)
                    and cuda_count > self.device)
                else "N/A"
            )
            log_fn(
                f"[YOLO-INIT] requested device={self.device} half={self.half} "
                f"torch_cuda_avail={cuda_avail} cuda_count={cuda_count} "
                f"cuda_name='{cuda_name}'"
            )
        except Exception as e:
            log_fn(f"[YOLO-INIT] device probe 예외: {e}")

    def _log_device_post_warmup(self, log_fn) -> None:
        """warmup 후 실제 model 이 올라간 디바이스 확인.
        ultralytics 는 device 인자 실패 시 자동 CPU 폴백 + 경고만 띄움.
        실제 런타임 device 는 model.device 속성으로 확인 가능.
        """
        if log_fn is None:
            return
        try:
            dev = getattr(self.model, "device", None)
            dev_type = None
            if dev is not None:
                try:
                    dev_type = str(dev)
                except Exception:
                    dev_type = repr(dev)
            log_fn(f"[YOLO-INIT] runtime model.device={dev_type}")
        except Exception as e:
            log_fn(f"[YOLO-INIT] runtime device probe 예외: {e}")

    def _predict(self, frame):
        return self.model.predict(frame, imgsz=self.imgsz, conf=self.conf,
                                  iou=self.iou, half=self.half,
                                  device=self.device, verbose=False)[0]

    def detect(self, frame: np.ndarray) -> List[Detection]:
        # 30 프레임마다 1회 상세 분해 타이머. 총시간·프레임크기·GPU% 확인용.
        profile = (
            self._profile_log_fn is not None
            and self._profile_every > 0
            and (self._profile_tick % self._profile_every == 0)
        )
        self._profile_tick += 1

        # GPU 이벤트 타이머 — predict() wall-clock 과 실제 GPU 시간 분리.
        # wall_ms 크고 gpu_ms 작으면 → Python/스레드 스케줄링 지연(GIL·preempt).
        # wall_ms ≈ gpu_ms 이면 → GPU 자체가 느림. 진단 목적이라 실패해도 무시.
        gpu_start_evt = None
        gpu_end_evt = None
        if torch is not None and torch.cuda.is_available():
            try:
                gpu_start_evt = torch.cuda.Event(enable_timing=True)
                gpu_end_evt = torch.cuda.Event(enable_timing=True)
                gpu_start_evt.record()
            except Exception:
                gpu_start_evt = None
                gpu_end_evt = None

        t0 = time.perf_counter()
        r = self._predict(frame)
        t1 = time.perf_counter()

        gpu_ms = -1.0
        if gpu_start_evt is not None and gpu_end_evt is not None:
            try:
                gpu_end_evt.record()
                gpu_end_evt.synchronize()
                gpu_ms = float(gpu_start_evt.elapsed_time(gpu_end_evt))
            except Exception:
                gpu_ms = -1.0

        dets: List[Detection] = []
        if r.boxes is None or len(r.boxes) == 0:
            predict_ms = (t1 - t0) * 1000
            if profile:
                try:
                    fh, fw = frame.shape[:2]
                    self._profile_log_fn(
                        f"[YOLO-PROF] frame={fw}x{fh} imgsz={self.imgsz} "
                        f"predict={int(predict_ms)}ms gpu={gpu_ms:.1f}ms "
                        f"post=0ms n_boxes=0 util={_gpu_util_pct(self.device)}%"
                    )
                except Exception:
                    pass
            self._maybe_log_spike(predict_ms, 0.0, gpu_ms, 0, frame)
            return dets
        xyxy = r.boxes.xyxy.cpu().numpy()
        confs = r.boxes.conf.cpu().numpy()
        clses = r.boxes.cls.cpu().numpy().astype(int)
        for (x1, y1, x2, y2), c, k in zip(xyxy, confs, clses):
            ix1, iy1, ix2, iy2 = int(x1), int(y1), int(x2), int(y2)
            kk = int(k)
            tab_color = "WHITE" if kk == CLS_WHITE else "RED"
            dets.append(Detection(ix1, iy1, ix2, iy2, float(c), kk,
                                  tab_color=tab_color))
        t2 = time.perf_counter()
        predict_ms = (t1 - t0) * 1000
        post_ms = (t2 - t1) * 1000
        if profile:
            try:
                fh, fw = frame.shape[:2]
                self._profile_log_fn(
                    f"[YOLO-PROF] frame={fw}x{fh} imgsz={self.imgsz} "
                    f"predict={int(predict_ms)}ms gpu={gpu_ms:.1f}ms "
                    f"post={int(post_ms)}ms "
                    f"n_boxes={len(dets)} util={_gpu_util_pct(self.device)}%"
                )
            except Exception:
                pass
        self._maybe_log_spike(predict_ms, post_ms, gpu_ms, len(dets), frame)
        return dets

    def _maybe_log_spike(self, predict_ms: float, post_ms: float,
                         gpu_ms: float, n_boxes: int,
                         frame: np.ndarray) -> None:
        """predict+post 가 임계 초과한 프레임만 로그 (프레임마다 호출).

        gpu_ms = torch.cuda.Event 로 잰 실제 GPU 시간.
        predict(wall) ≫ gpu_ms 면 Python/스레드 스케줄링 지연.
        predict(wall) ≈ gpu_ms 면 GPU 자체 연산이 느림.
        """
        total_ms = predict_ms + post_ms
        if total_ms <= self._spike_threshold_ms:
            return
        if self._profile_log_fn is None:
            return
        now = time.perf_counter()
        if now - self._last_spike_log_t < 0.1:
            return
        self._last_spike_log_t = now
        try:
            fh, fw = frame.shape[:2]
            self._profile_log_fn(
                f"[YOLO-SPIKE] frame={fw}x{fh} imgsz={self.imgsz} "
                f"predict={predict_ms:.1f}ms gpu={gpu_ms:.1f}ms "
                f"post={post_ms:.1f}ms total={total_ms:.1f}ms "
                f"n_boxes={n_boxes} util={_gpu_util_pct(self.device)}%"
            )
        except Exception:
            pass

    def detect_primary(self, frame: np.ndarray,
                       min_w: int = 25, min_h: int = 40) -> Optional[Detection]:
        """최소 크기 이상 중 conf 가장 높은 탭 1개 (RED/WHITE 무관).

        색상 구분은 호출자에서 `d.tab_color`로 분기.
        """
        best = None
        for d in self.detect(frame):
            if d.w < min_w or d.h < min_h:
                continue
            if best is None or d.conf > best.conf:
                best = d
        return best

    def detect_red(self, frame: np.ndarray,
                   min_w: int = 25, min_h: int = 40) -> Optional[Detection]:
        """RED 탭만 중 conf 최고 1개."""
        best = None
        for d in self.detect(frame):
            if d.w < min_w or d.h < min_h:
                continue
            if d.tab_color != "RED":
                continue
            if best is None or d.conf > best.conf:
                best = d
        return best


class AsyncYolo:
    """YoloRunner 를 백그라운드 스레드로 감쌈.

    2026-04-21: Windows WDDM 에서 게임 GPU queue 우선순위 때문에 YOLO predict
    가 5ms → 981ms 로 튀어 메인 루프 FPS 가 2까지 떨어짐. AsyncYolo 는
    메인 루프를 차단하지 않고 백그라운드에서 YOLO 를 돌려 FPS 를 고정한다.
    - submit(frame, offset): 최신 frame 덮어쓰기 (큐 쌓지 않음).
    - latest(): (detections, age_ms) 반환. 아직 1회도 못 돌렸으면 ([], -1).
    - detection offset (gx_off, gy_off) 는 submit 시점 값으로 기록되어
      latest() 함께 반환 → 좌표 보정은 호출자에서.
    """

    def __init__(self, runner: "YoloRunner"):
        self._runner = runner
        self._pending_frame: Optional[np.ndarray] = None
        self._pending_offset: Tuple[int, int] = (0, 0)
        self._pending_lock = threading.Lock()
        self._latest_dets: List[Detection] = []
        self._latest_offset: Tuple[int, int] = (0, 0)
        self._latest_ts: float = -1.0
        self._latest_predict_ms: float = 0.0
        self._latest_lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name="async-yolo", daemon=True
        )
        self._thread.start()

    # 호환 편의. 런타임 imgsz 조정 시 그대로 runner 로 위임.
    @property
    def imgsz(self) -> int:
        return int(getattr(self._runner, "imgsz", 0))

    @imgsz.setter
    def imgsz(self, v: int):
        try:
            self._runner.imgsz = int(v)
        except Exception:
            pass

    def submit(self, frame: np.ndarray, offset: Tuple[int, int] = (0, 0)):
        """최신 frame 을 백그라운드 큐에 전달 (덮어쓰기).

        큐가 쌓이지 않도록 항상 최신 1개만 유지 — 이전 pending 은 버린다.
        """
        if frame is None:
            return
        # view 저장 시 race 위험 — 호출자가 frame 을 덮어쓸 수 있으므로 shallow ref
        # 대신 bytes buffer 는 같이 써도 YOLO 내부에서 copy 하므로 OK.
        with self._pending_lock:
            self._pending_frame = frame
            self._pending_offset = (int(offset[0]), int(offset[1]))
        self._wake.set()

    def latest(self):
        """마지막 detection 결과 반환: (dets, offset, age_ms).

        age_ms = -1 → 아직 한 번도 detection 완료 못 함.
        """
        with self._latest_lock:
            dets = list(self._latest_dets)
            off = self._latest_offset
            ts = self._latest_ts
            pm = self._latest_predict_ms
        if ts < 0:
            return dets, off, -1.0, pm
        age_ms = (time.time() - ts) * 1000.0
        return dets, off, age_ms, pm

    def stop(self):
        self._stop.set()
        self._wake.set()
        try:
            self._thread.join(timeout=1.0)
        except Exception:
            pass

    def _loop(self):
        while not self._stop.is_set():
            self._wake.wait(timeout=0.2)
            if self._stop.is_set():
                break
            self._wake.clear()
            # 최신 frame 한 개만 처리 (큐 비움).
            with self._pending_lock:
                frame = self._pending_frame
                offset = self._pending_offset
                self._pending_frame = None
            if frame is None:
                continue
            t0 = time.perf_counter()
            try:
                dets = self._runner.detect(frame)
            except Exception:
                dets = []
            # offset 보정.
            gx, gy = offset
            if gx or gy:
                for d in dets:
                    d.x1 += gx
                    d.y1 += gy
                    d.x2 += gx
                    d.y2 += gy
            dt_ms = (time.perf_counter() - t0) * 1000.0
            with self._latest_lock:
                self._latest_dets = dets
                self._latest_offset = (gx, gy)
                self._latest_ts = time.time()
                self._latest_predict_ms = dt_ms
