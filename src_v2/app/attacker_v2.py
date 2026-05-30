"""Attacker v2 entry — v2 자체 GUI + AttackerWorkerV2.

facade 폐기. v1 main_window 절대 import 안 함.

실행:
    py -m src_v2.app.attacker_v2                # GUI 모드
    py -m src_v2.app.attacker_v2 --headless     # GUI 없이 worker 만 기동
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from typing import Any, Dict, Optional

# torch / Qt 사전 로드.
try:
    import torch  # noqa: F401
except Exception:  # noqa: BLE001
    pass
try:
    import PyQt5 as _pyqt5
    _qt_root = os.path.dirname(_pyqt5.__file__)
    for _sub in ("Qt5", "Qt"):
        _plugs = os.path.join(_qt_root, _sub, "plugins")
        if os.path.isdir(_plugs):
            os.environ["QT_PLUGIN_PATH"] = _plugs
            os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(_plugs, "platforms")
            break
except Exception:  # noqa: BLE001
    pass

log = logging.getLogger("src_v2.app.attacker_v2")


def _build_attacker_adapters(cfg, log_cb) -> Dict[str, Any]:
    # 2026-05-05 Cycle 5-2 — src_v2.utils.win_helpers 우선 + src.* fallback.
    try:
        from src_v2.utils.win_helpers import find_windows_by_process
    except Exception:  # noqa: BLE001
        from src.input.keys import find_windows_by_process  # type: ignore
    from src_v2.adapters import (
        RealGrabberAdapter, RealYoloAdapter, RealOcrAdapter,
        RealHpMpAdapter, RealUdpSenderAdapter,
    )
    out: Dict[str, Any] = {}
    hwnd = None
    try:
        target = cfg.input.target_window
        if isinstance(target, str) and target.lower().endswith(".exe"):
            wins = find_windows_by_process(target)
            if wins:
                hwnd = wins[0]
    except Exception:  # noqa: BLE001
        pass

    try:
        out["grabber"] = RealGrabberAdapter(
            monitor_index=cfg.capture.monitor_index, hwnd=hwnd,
            target_interval_s=0.02,
        )
    except Exception as e:  # noqa: BLE001
        log_cb(f"[atk-v2][!] grabber: {e}"); out["grabber"] = None
    try:
        out["yolo"] = RealYoloAdapter(
            weights=cfg.vision.weights, imgsz=int(cfg.vision.imgsz),
            conf=float(cfg.vision.conf), iou=cfg.vision.iou, half=cfg.vision.half,
            device=(f"cuda:{cfg.vision.device}"
                    if isinstance(cfg.vision.device, int) else cfg.vision.device),
            log_fn=log_cb,
        )
    except Exception as e:  # noqa: BLE001
        log_cb(f"[atk-v2][!] yolo: {e}"); out["yolo"] = None
    try:
        out["ocr"] = RealOcrAdapter(cfg.ocr, gpu=True)
    except Exception as e:  # noqa: BLE001
        log_cb(f"[atk-v2][!] ocr: {e}"); out["ocr"] = None
    try:
        out["hpmp"] = RealHpMpAdapter(log_cb=log_cb)
    except Exception as e:  # noqa: BLE001
        log_cb(f"[atk-v2][!] hpmp: {e}"); out["hpmp"] = None
    # 2026-05-05 — udp_sender 진단 로그: peers / port 명시.
    _peers = list(getattr(cfg.net, "peers", []) or [])
    _send_port = int(getattr(cfg.net, "port", 54545))
    log_cb(f"[atk-v2] udp_sender 준비 peers={_peers} port={_send_port}")
    if not _peers:
        log_cb("[atk-v2][!] cfg.net.peers 비어있음 — 송신 대상 없음 (healer 가 격수 좌표 못 받음)")
    try:
        out["udp_sender"] = RealUdpSenderAdapter(_peers, port=_send_port)
        log_cb(f"[atk-v2] udp_sender init OK peers={_peers} port={_send_port}")
    except Exception as e:  # noqa: BLE001
        log_cb(f"[atk-v2][!] udp_sender init 실패: {e}")
        out["udp_sender"] = None

    # F1 key adapter — win32api 기반 (없으면 _NullF1 폴백 처리, worker 측에서)
    try:
        import ctypes
        _user32 = ctypes.WinDLL("user32", use_last_error=True)

        class _Win32F1Key:
            VK_F1 = 0x70

            def is_down(self) -> bool:
                try:
                    s = _user32.GetAsyncKeyState(self.VK_F1)
                    return bool(s & 0x8000)
                except Exception:
                    return False

        out["f1_key"] = _Win32F1Key()
    except Exception as e:  # noqa: BLE001
        log_cb(f"[atk-v2][!] f1_key: {e}"); out["f1_key"] = None

    # CooldownReceiver — 힐러들이 보낸 CooldownReport 역수신 (UDP recv).
    # v1 attacker_worker.py:268+ — bind on attacker_recv_port (default 45455).
    try:
        import socket
        from src.net.protocol import CooldownReport  # type: ignore

        class _UdpCdReceiver:
            def __init__(self, port: int, log_cb):
                self.port = int(port)
                self.log_cb = log_cb
                self.sock: Optional[Any] = None
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind(("0.0.0.0", self.port))
                    s.setblocking(False)
                    self.sock = s
                    log_cb(f"[atk-v2] cd_recv bind 0.0.0.0:{self.port}")
                except Exception as ee:  # noqa: BLE001
                    log_cb(f"[atk-v2][!] cd_recv bind {self.port}: {ee}")
                    self.sock = None

            def is_available(self) -> bool:
                return self.sock is not None

            def poll(self):
                if self.sock is None:
                    return []
                out = []
                for _ in range(64):
                    try:
                        data, addr = self.sock.recvfrom(2048)
                    except BlockingIOError:
                        break
                    except Exception:
                        break
                    try:
                        rep = CooldownReport.from_bytes(data)
                        out.append((rep, addr))
                    except Exception:
                        continue
                return out

        port = int(getattr(cfg.net, "attacker_recv_port", 45455) or 45455)
        out["cd_receiver"] = _UdpCdReceiver(port, log_cb)
    except Exception as e:  # noqa: BLE001
        log_cb(f"[atk-v2][!] cd_receiver: {e}"); out["cd_receiver"] = None
    return out


def _build_attacker_worker(cfg, adapters):
    from src_v2.workers.attacker_worker_v2 import AttackerWorkerV2, AttackerConfig
    acfg = AttackerConfig(
        udp_send_hz=int(getattr(cfg.net, "send_rate_hz", 30) or 30),
    )
    return AttackerWorkerV2(
        cfg=acfg,
        grabber=adapters.get("grabber"), yolo=adapters.get("yolo"),
        ocr=adapters.get("ocr"), hpmp=adapters.get("hpmp"),
        udp_sender=adapters.get("udp_sender"),
        cd_receiver=adapters.get("cd_receiver"),
        f1_key=adapters.get("f1_key"),
    )


def _run_gui(ui_mode: str = "v2") -> int:
    """2026-05-05 Cycle 4-7 — V2MainWindow native (default) 또는 legacy 안전망.
    role="attacker" 강제 (격수 PC 가 healer 워커 자동 가동 차단).
    """
    from PyQt5 import QtWidgets
    from src.config import load as load_cfg
    cfg = load_cfg()

    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")

    if ui_mode == "legacy":
        # 안전망 — v1 main_window 통째 복사본 (Cycle 7 까지 보존).
        from src_v2.ui.main_window_v2 import MainWindow
        win = MainWindow(cfg, initial_role="attacker")
        log.info("[atk-v2] legacy compat UI 시작")
    else:
        # v2 native — _build_attacker_adapters + AttackerWorkerV2 + V2MainWindow.
        # 2026-05-05 — worker_factory 람다 (재시작 RuntimeError 방지).
        log_cb = lambda m: log.info("%s", m)

        def _build_worker():
            adapters = _build_attacker_adapters(cfg, log_cb)
            return _build_attacker_worker(cfg, adapters)

        worker = _build_worker()
        from src_v2.ui.v2_main_window import V2MainWindow
        win = V2MainWindow(
            role="attacker", cfg=cfg, worker=worker, worker_factory=_build_worker,
        )
        log.info("[atk-v2] V2MainWindow native UI 시작")

    win.show()
    return app.exec_()


def _run_headless() -> int:
    from src.config import load as load_cfg
    cfg = load_cfg()
    log_cb = lambda m: log.info("%s", m)
    adapters = _build_attacker_adapters(cfg, log_cb)
    worker = _build_attacker_worker(cfg, adapters)

    stopped = {"v": False}

    def _stop(*_a):
        if stopped["v"]:
            return
        stopped["v"] = True
        try: worker.stop(timeout=3.0)
        except Exception: log.exception("worker stop fail")
        try:
            sender = adapters.get("udp_sender")
            if sender is not None and hasattr(sender, "close"):
                sender.close()
        except Exception: pass

    signal.signal(signal.SIGINT, _stop)
    try: signal.signal(signal.SIGTERM, _stop)
    except Exception: pass

    log.info("[atk-v2] start (headless) — Ctrl+C 로 종료")
    worker.start()
    try:
        while not stopped["v"]:
            time.sleep(0.5)
    except KeyboardInterrupt:
        _stop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true",
                        help="GUI 없이 v2 worker 만 기동")
    parser.add_argument(
        "--ui", choices=("v2", "legacy"), default="v2",
        help="v2(default)=V2MainWindow native, legacy=main_window_v2 compat",
    )
    args = parser.parse_args()

    # 2026-05-05 — basicConfig 먼저 (root level INFO 보장) + file handler 추가.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    logging.getLogger().setLevel(logging.INFO)

    import time as _time
    _ts = _time.strftime("%Y%m%d_%H%M%S")
    _log_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "logs",
    )
    try:
        os.makedirs(_log_dir, exist_ok=True)
        _log_file = os.path.join(_log_dir, f"attacker_v2_native_{_ts}.log")
        _file_h = logging.FileHandler(_log_file, encoding="utf-8")
        _file_h.setFormatter(logging.Formatter(
            "%(asctime)s %(name)s %(levelname)s %(message)s"
        ))
        _file_h.setLevel(logging.INFO)
        logging.getLogger().addHandler(_file_h)
        logging.getLogger().info("[atk-v2] file log 시작: %s", _log_file)
    except Exception as _e:  # noqa: BLE001
        logging.getLogger().warning("[atk-v2] file log 시작 실패: %s", _e)

    if args.headless:
        return _run_headless()
    return _run_gui(args.ui)


if __name__ == "__main__":
    sys.exit(main())
