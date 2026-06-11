"""src_v2.eyes — watchers (background observation).

Each watcher = its own thread, polls a source, updates SnapshotStore + EventBus.
External libs (RapidOCR/YOLO/mss/numpy) are accessed via injected adapters
so unit tests can run with mocks.

Design ref: §2.4
"""
from .base_watcher import BaseWatcher, WatcherAdapter
from .capture import CaptureWatcher
from .yolo_watcher import YoloWatcher
from .ocr_watcher import OcrWatcher
from .cooldown_watcher import CooldownWatcher
from .hpmp_watcher import HpMpWatcher
from .xp_watcher import XpWatcher
from .udp_watcher import UdpWatcher

__all__ = [
    "BaseWatcher", "WatcherAdapter",
    "CaptureWatcher", "YoloWatcher", "OcrWatcher",
    "CooldownWatcher", "HpMpWatcher", "XpWatcher", "UdpWatcher",
]
