"""Capture watcher — frame grabber.

Wraps an injected GrabberAdapter (e.g. AsyncGrabber from src/capture/screen.py)
and stores the latest frame on Snapshot.last_frame.

Design ref: §2.4 + §11.2 (src/capture/screen.py wrap)
"""
from __future__ import annotations
import logging
import time
from typing import Any, Optional, Protocol

from .base_watcher import BaseWatcher
from ..core.event_bus import EventBus
from ..core.snapshot import SnapshotStore

log = logging.getLogger("src_v2.eyes.capture")


class GrabberAdapter(Protocol):
    """Frame grabber adapter — must return a frame (numpy ndarray) or None."""
    def grab(self) -> Any: ...
    def is_available(self) -> bool: ...


class _NullGrabber:
    """Default no-op grabber. Returns None — useful for tests w/o real capture."""
    def grab(self) -> Any:
        return None

    def is_available(self) -> bool:
        return False


class CaptureWatcher(BaseWatcher):
    """Polls grabber, writes frame to Snapshot.last_frame.

    Does NOT publish frame_ready event by default (UI publisher does that).
    """

    TOPIC_FRAME = "eye.frame"

    def __init__(self,
                 store: SnapshotStore,
                 bus: EventBus,
                 grabber: Optional[GrabberAdapter] = None,
                 poll_sec: float = 0.02,  # 50 Hz
                 publish_event: bool = False) -> None:
        super().__init__("capture", store, bus, poll_sec=poll_sec, adapter=grabber)
        self.grabber: GrabberAdapter = grabber or _NullGrabber()
        self.publish_event = publish_event

    def _tick(self) -> None:
        if not self.grabber.is_available():
            return
        frame = self.grabber.grab()
        if frame is None:
            return
        # frame size
        h, w = 0, 0
        try:
            shape = getattr(frame, "shape", None)
            if shape and len(shape) >= 2:
                h = int(shape[0]); w = int(shape[1])
        except Exception:
            pass
        # 2026-04-25 v1 healer_worker.py:1342~1366 crop 로직 1:1.
        # game_region_abs 가 SnapshotStore 에 있으면 frame 을 crop 해서 last_crop
        # 에 저장. yolo/ocr watcher 가 이 crop 을 입력으로 사용. preview 도 crop.
        # offset (rx, ry) 도 함께 저장 — yolo detection 결과 mss-relative 변환용.
        crop_frame = None
        crop_origin = (0, 0)
        try:
            gr = self.store.read_field("game_region_abs", None)
            if gr and len(gr) == 4:
                gx, gy, gw, gh = int(gr[0]), int(gr[1]), int(gr[2]), int(gr[3])
                # mss frame 의 monitor origin (left, top). adapter 에서 가져옴.
                ml, mt = 0, 0
                try:
                    mon = getattr(self.grabber, "mon", None)
                    if isinstance(mon, dict):
                        ml = int(mon.get("left", 0))
                        mt = int(mon.get("top", 0))
                except Exception:
                    pass
                rx = max(0, gx - ml)
                ry = max(0, gy - mt)
                rw2 = min(gw, w - rx)
                rh2 = min(gh, h - ry)
                if rw2 > 20 and rh2 > 20:
                    crop_frame = frame[ry:ry + rh2, rx:rx + rw2]
                    crop_origin = (rx, ry)
        except Exception:
            crop_frame = None
        # fps 추정
        now = time.monotonic()
        prev_ts = self.store.read_field("last_frame_ts", 0.0) or 0.0
        fps = 0.0
        if prev_ts and now > prev_ts:
            fps = 1.0 / (now - prev_ts)
        # 2026-04-25 monitor_origin 추가 — HpMp/Cooldown/Xp 등 OCR adapter 가
        # 화면 절대 좌표 region 을 frame 좌표로 변환할 때 사용. mss grabber 의
        # mon.left/top.
        ml, mt = 0, 0
        try:
            mon = getattr(self.grabber, "mon", None)
            if isinstance(mon, dict):
                ml = int(mon.get("left", 0))
                mt = int(mon.get("top", 0))
        except Exception:
            pass
        self.store.update(
            last_frame=frame,
            last_crop=crop_frame,
            last_crop_origin=crop_origin,
            last_frame_ts=now,
            last_frame_origin=crop_origin,  # publisher preview_offset 호환
            frame_w=w,
            frame_h=h,
            fps=fps,
            monitor_origin=(ml, mt),
        )
        if self.publish_event:
            self.bus.publish(self.TOPIC_FRAME, frame)
