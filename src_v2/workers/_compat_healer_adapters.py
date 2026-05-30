"""healer facade 의 adapter builder. v1_compat 분할 (audit 8.1 3단계).

build_healer_adapters(cfg, log_cb, yolo_overrides, cmd_emit) → ad dict.
v1_compat.py 의 _build_adapters 130줄을 외부 함수로 추출.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional


def build_healer_adapters(
    cfg: Any,
    log_cb: Callable[[str], None],
    yolo_imgsz: Optional[int] = None,
    yolo_conf: Optional[float] = None,
    cmd_emit: Optional[Callable[[str, int], None]] = None,
) -> Dict[str, Any]:
    """healer adapter 들 일괄 생성. import 실패 시 None graceful degrade.

    Args:
      cfg: project config (dataclass).
      log_cb: 진단 로그 emit 콜백.
      yolo_imgsz: facade 가 보유한 imgsz override (None 이면 cfg.vision.imgsz).
      yolo_conf: facade 가 보유한 conf override.
      cmd_emit: udp ControlCmd 수신 시 (cmd, target_idx) emit 콜백.
    """
    from src.input.keys import find_windows_by_process
    from src_v2.adapters import (
        RealGrabberAdapter, RealYoloAdapter, RealOcrAdapter,
        RealCooldownAdapter, RealHpMpAdapter, RealUdpAdapter,
        RealKeysAdapter, RealXpAdapter,
    )
    from ._compat_uplink import UplinkSenderShim

    out: Dict[str, Any] = {}

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
        out["grabber"] = RealGrabberAdapter(
            monitor_index=cfg.capture.monitor_index, hwnd=hwnd,
            target_interval_s=0.02,
        )
    except Exception as e:  # noqa: BLE001
        log_cb(f"[v2][!] grabber: {e}")
        out["grabber"] = None
    try:
        out["yolo"] = RealYoloAdapter(
            weights=cfg.vision.weights,
            imgsz=int(yolo_imgsz or cfg.vision.imgsz),
            conf=float(yolo_conf if yolo_conf is not None else cfg.vision.conf),
            iou=cfg.vision.iou, half=cfg.vision.half,
            device=(f"cuda:{cfg.vision.device}"
                    if isinstance(cfg.vision.device, int) else cfg.vision.device),
            log_fn=log_cb,
        )
    except Exception as e:  # noqa: BLE001
        log_cb(f"[v2][!] yolo: {e}")
        out["yolo"] = None
    try:
        out["ocr"] = RealOcrAdapter(cfg.ocr, gpu=True)
    except Exception as e:  # noqa: BLE001
        log_cb(f"[v2][!] ocr: {e}")
        out["ocr"] = None
    try:
        out["cooldown"] = RealCooldownAdapter(name="cd", poll_sec=1.0)
        out["buff"] = RealCooldownAdapter(name="buff", poll_sec=1.0, own_rec=True)
        out["chat"] = RealCooldownAdapter(name="chat", poll_sec=0.5)
    except Exception as e:  # noqa: BLE001
        log_cb(f"[v2][!] cd/buff: {e}")
        out["cooldown"] = out["buff"] = out["chat"] = None
    try:
        out["hpmp"] = RealHpMpAdapter(log_cb=log_cb)
    except Exception as e:  # noqa: BLE001
        log_cb(f"[v2][!] hpmp: {e}")
        out["hpmp"] = None
    try:
        out["xp"] = RealXpAdapter(log_cb=log_cb)
    except Exception as e:  # noqa: BLE001
        log_cb(f"[v2][!] xp: {e}")
        out["xp"] = None

    # udp bind 30회 × 200ms = 6초 max (Win port grace).
    _bh = getattr(cfg.net, "bind_host", "0.0.0.0") or "0.0.0.0"
    _udp_ad = None
    _last_err = None
    for _try in range(1, 31):
        try:
            _udp_ad = RealUdpAdapter(port=cfg.net.port, bind_host=_bh)
            if _try > 1:
                log_cb(f"[v2] udp bind 성공 ({_try}회 시도)")
            break
        except Exception as e:  # noqa: BLE001
            _last_err = e
            time.sleep(0.2)
    if _udp_ad is None:
        log_cb(f"[v2][!] udp bind 30회 실패 — 격수 좌표 수신 불가: {_last_err}")
        out["udp"] = None
    else:
        out["udp"] = _udp_ad
        # ControlCmd handler 등록 — 워커 활성 시 격수 명령 처리.
        try:
            _r = getattr(_udp_ad, "_r", None)
            if _r is not None and hasattr(_r, "set_control_handler") and cmd_emit is not None:
                def _on_remote_cmd(cmd_obj):
                    try:
                        cmd_emit(
                            str(getattr(cmd_obj, "cmd", "")),
                            int(getattr(cmd_obj, "target_idx", -1)),
                        )
                    except Exception:
                        pass
                _r.set_control_handler(_on_remote_cmd)
                log_cb("[v2] udp ControlCmd handler 등록")
        except Exception as e:  # noqa: BLE001
            log_cb(f"[v2][!] udp ctrl_handler 등록 실패: {e}")

    try:
        out["keys"] = RealKeysAdapter()
        try:
            if hwnd and hasattr(out["keys"], "set_hwnd"):
                out["keys"].set_hwnd(int(hwnd))
        except Exception:
            pass
    except Exception as e:  # noqa: BLE001
        log_cb(f"[v2][!] keys: {e}")
        out["keys"] = None

    # uplink sender — 격수 PC 로 1Hz CooldownReport 송신.
    try:
        peers = list(getattr(cfg.net, "peers", []) or [])
        recv_port = int(getattr(cfg.net, "attacker_recv_port", 45455) or 45455)
        out["uplink_sender"] = UplinkSenderShim(peers, recv_port)
        if peers:
            log_cb(f"[v2] uplink_sender 준비 peers={peers} port={recv_port}")
        else:
            log_cb(
                f"[v2] uplink_sender 준비 (peers 비어있음 — 격수 ping 학습 대기) "
                f"port={recv_port}"
            )
    except Exception as e:  # noqa: BLE001
        log_cb(f"[v2][!] uplink_sender: {e}")
        out["uplink_sender"] = None
    return out
