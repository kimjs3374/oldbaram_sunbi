"""mss 기반 화면 캡처. DPI aware + monitor/HWND 선택.

2026-04-21: 비동기 캡처 스레드 추가 (AsyncGrabber).
키 입력 폭주 구간에서 GDI BitBlt 이 30ms→100ms 로 튀어 메인 루프 FPS 가
2~3 까지 내려가는 현상 완화. 캡처 스레드가 백그라운드에서 최신 프레임을
상주 갱신하고 메인 루프는 copy 만 한다.
"""
import ctypes
import threading
import time
from ctypes import wintypes
from typing import Optional

import numpy as np
import mss

ctypes.windll.user32.SetProcessDPIAware()
user32 = ctypes.WinDLL("user32", use_last_error=True)


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


def get_window_rect(hwnd: int) -> Optional[dict]:
    """HWND의 클라이언트 영역 → mss monitor dict."""
    rect = RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return None
    return {"left": pt.x, "top": pt.y, "width": w, "height": h}


class Grabber:
    """동기 캡처 (기존 동작 유지)."""
    def __init__(self, monitor_index: int = 1, hwnd: Optional[int] = None):
        self._sct = mss.mss()
        self._hwnd = hwnd
        if hwnd:
            r = get_window_rect(hwnd)
            if r is None:
                raise RuntimeError(f"hwnd {hwnd} rect 얻기 실패")
            self.mon = r
        else:
            mons = self._sct.monitors
            if monitor_index >= len(mons):
                monitor_index = 1
            self.mon = mons[monitor_index]

    def refresh_rect(self):
        """창이 이동/리사이즈 되었을 때 호출."""
        if self._hwnd:
            r = get_window_rect(self._hwnd)
            if r:
                self.mon = r

    def grab(self) -> np.ndarray:
        raw = self._sct.grab(self.mon)
        return np.array(raw)[..., :3]  # BGRA → BGR


class AsyncGrabber:
    """백그라운드 스레드에서 mss 호출. 메인 루프는 copy 만.

    - grab() 은 블로킹 없이 최신 프레임 반환 (최악의 경우 1~2프레임 지연).
    - BitBlt 지연(100ms 등) 이 발생해도 메인 루프 FPS 는 영향 없음.
    - Grabber 와 API 호환 (grab / refresh_rect / mon).
    """

    def __init__(self, monitor_index: int = 1, hwnd: Optional[int] = None,
                 target_interval_s: float = 0.02):
        # mss 객체는 스레드별 TLS 권장 — 캡처 스레드 내부에서 생성.
        self._hwnd = hwnd
        self._monitor_index = monitor_index
        self._target_interval_s = float(target_interval_s)
        # 초기 rect 결정.
        tmp = mss.mss()
        try:
            if hwnd:
                r = get_window_rect(hwnd)
                if r is None:
                    raise RuntimeError(f"hwnd {hwnd} rect 얻기 실패")
                self.mon = r
            else:
                mons = tmp.monitors
                if monitor_index >= len(mons):
                    monitor_index = 1
                self.mon = mons[monitor_index]
        finally:
            try:
                tmp.close()
            except Exception:
                pass
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._frame_ts: float = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name="async-grabber", daemon=True
        )
        self._thread.start()

    def refresh_rect(self):
        if self._hwnd:
            r = get_window_rect(self._hwnd)
            if r:
                with self._lock:
                    self.mon = r

    def stop(self):
        self._stop.set()
        try:
            self._thread.join(timeout=1.0)
        except Exception:
            pass

    def _loop(self):
        # mss 는 thread-local. 캡처 스레드 내부에서만 사용.
        sct = mss.mss()
        try:
            while not self._stop.is_set():
                t0 = time.perf_counter()
                with self._lock:
                    mon = dict(self.mon)
                try:
                    raw = sct.grab(mon)
                    arr = np.array(raw)[..., :3]
                except Exception:
                    arr = None
                if arr is not None:
                    with self._lock:
                        self._frame = arr
                        self._frame_ts = time.time()
                elapsed = time.perf_counter() - t0
                remain = self._target_interval_s - elapsed
                if remain > 0:
                    # 캡처가 느리면 sleep 없이 다시 호출 (BitBlt 중단 없음).
                    time.sleep(remain)
        finally:
            try:
                sct.close()
            except Exception:
                pass

    def grab(self) -> np.ndarray:
        """가장 최신 프레임 반환. 스레드가 아직 한 번도 캡처 못 했으면 대기.

        2026-04-22: copy() 제거. 캡처 스레드가 매 iter **새 ndarray 할당** 하고
        _frame 에 교체만 (기존 참조는 main 이 잡고 있어도 별개 객체). race 없음.
        main loop 는 reference 만 받음 → ms 단위 copy 삭제로 순수 병렬.
        """
        t_deadline = time.time() + 1.0
        while True:
            with self._lock:
                if self._frame is not None:
                    return self._frame
            if time.time() >= t_deadline:
                # fallback: 동기 grab 1회. (AsyncGrabber 가 사용 불가 상태면)
                sct = mss.mss()
                try:
                    with self._lock:
                        mon = dict(self.mon)
                    raw = sct.grab(mon)
                    return np.array(raw)[..., :3]
                finally:
                    try:
                        sct.close()
                    except Exception:
                        pass
            time.sleep(0.005)
