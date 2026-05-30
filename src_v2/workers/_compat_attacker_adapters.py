"""attacker facade adapter builder. v1_compat 분할 (audit 8.1 3단계).

build_attacker_adapters(cfg, log_cb, yolo_imgsz, yolo_conf) → ad dict.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional


def build_attacker_adapters(
    cfg: Any,
    log_cb: Callable[[str], None],
    yolo_imgsz: Optional[int] = None,
    yolo_conf: Optional[float] = None,
) -> Dict[str, Any]:
    """attacker adapter 들 일괄 생성. import 실패 시 None graceful."""
    from src.input.keys import find_windows_by_process
    from src_v2.adapters import (
        RealGrabberAdapter, RealYoloAdapter, RealOcrAdapter,
        RealHpMpAdapter, RealUdpSenderAdapter, RealXpAdapter,
        RealCooldownAdapter,
    )
    from ._compat_cd_receiver import UdpCdReceiver
    from ._compat_f1_key import Win32F1Key

    ad: Dict[str, Any] = {}

    hwnd = None
    try:
        target = cfg.input.target_window
        if isinstance(target, str) and target.lower().endswith(".exe"):
            wins = find_windows_by_process(target)
            if wins:
                hwnd = wins[0]
    except Exception:
        pass

    try:
        ad["grabber"] = RealGrabberAdapter(
            monitor_index=cfg.capture.monitor_index, hwnd=hwnd,
            target_interval_s=0.02,
        )
    except Exception as e:  # noqa: BLE001
        log_cb(f"[atk-v2][!] grabber: {e}"); ad["grabber"] = None
    try:
        ad["yolo"] = RealYoloAdapter(
            weights=cfg.vision.weights,
            imgsz=int(yolo_imgsz or cfg.vision.imgsz),
            conf=float(yolo_conf if yolo_conf is not None else cfg.vision.conf),
            iou=cfg.vision.iou, half=cfg.vision.half,
            device=(f"cuda:{cfg.vision.device}"
                    if isinstance(cfg.vision.device, int) else cfg.vision.device),
            log_fn=log_cb,
        )
    except Exception as e:  # noqa: BLE001
        log_cb(f"[atk-v2][!] yolo: {e}"); ad["yolo"] = None
    try:
        ad["ocr"] = RealOcrAdapter(cfg.ocr, gpu=True)
    except Exception as e:  # noqa: BLE001
        log_cb(f"[atk-v2][!] ocr: {e}"); ad["ocr"] = None
    try:
        ad["hpmp"] = RealHpMpAdapter(log_cb=log_cb)
    except Exception as e:  # noqa: BLE001
        log_cb(f"[atk-v2][!] hpmp: {e}"); ad["hpmp"] = None
    try:
        ad["xp"] = RealXpAdapter(log_cb=log_cb)
    except Exception as e:  # noqa: BLE001
        log_cb(f"[atk-v2][!] xp: {e}"); ad["xp"] = None
    try:
        ad["cooldown"] = RealCooldownAdapter(name="cd", poll_sec=1.0)
        ad["buff"] = RealCooldownAdapter(name="buff", poll_sec=1.0, own_rec=True)
    except Exception as e:  # noqa: BLE001
        log_cb(f"[atk-v2][!] cd/buff: {e}")
        ad["cooldown"] = ad["buff"] = None
    try:
        ad["udp_sender"] = RealUdpSenderAdapter(cfg.net.peers, port=cfg.net.port)
    except Exception as e:  # noqa: BLE001
        log_cb(f"[atk-v2][!] udp send: {e}"); ad["udp_sender"] = None
    try:
        recv_port = int(getattr(cfg.net, "attacker_recv_port", 45455) or 45455)
        ad["cd_receiver"] = UdpCdReceiver(recv_port, log_cb)
    except Exception as e:  # noqa: BLE001
        log_cb(f"[atk-v2][!] cd_receiver: {e}"); ad["cd_receiver"] = None
    try:
        ad["f1_key"] = Win32F1Key()
    except Exception as e:  # noqa: BLE001
        log_cb(f"[atk-v2][!] f1_key: {e}"); ad["f1_key"] = None
    return ad
