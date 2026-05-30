"""Healer GUI v2 — v2 자체 GUI + HealerWorkerV2 직접 구동.

facade 폐기. v1 src.ui.main_window 절대 import 안 함.

실행:
    py -m src_v2.app.healer_gui_v2                 # v2 native UI (default)
    py -m src_v2.app.healer_gui_v2 --ui legacy     # v1 compat 안전망

흐름 (v2 default):
    1) cfg.yaml 로드 (src.config.load — v1 dataclass cfg)
    2) RealAdapter 들 인스턴스화 (grabber/yolo/ocr/cooldown/buff/hpmp/xp/udp/keys)
    3) HealerWorkerV2 build (start 는 GUI 의 ▶시작 버튼이 호출)
    4) V2MainWindow 띄우고 picker → worker.set_*_region 직접 주입
    5) 1Hz 타이머가 worker.store.read() 직접 read 해서 텔레메트리 갱신
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any, Dict

# torch 사전 로드 (Windows MSVC 충돌 회피).
try:
    import torch  # noqa: F401
except Exception:  # noqa: BLE001
    pass

# Qt plugin path (PyQt5 import 전).
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

from PyQt5 import QtWidgets  # noqa: E402

log = logging.getLogger("src_v2.app.healer_gui_v2")


def _build_healer_adapters(cfg, log_cb) -> Dict[str, Any]:
    # 2026-05-05 Cycle 5-2 — src_v2.utils.win_helpers 우선 + src.* fallback.
    try:
        from src_v2.utils.win_helpers import find_windows_by_process
    except Exception:  # noqa: BLE001
        from src.input.keys import find_windows_by_process  # type: ignore
    from src_v2.adapters import (
        RealGrabberAdapter, RealYoloAdapter, RealOcrAdapter,
        RealCooldownAdapter, RealHpMpAdapter, RealUdpAdapter,
        RealKeysAdapter, RealXpAdapter,
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
        log_cb(f"[v2][!] grabber: {e}"); out["grabber"] = None
    try:
        out["yolo"] = RealYoloAdapter(
            weights=cfg.vision.weights, imgsz=int(cfg.vision.imgsz),
            conf=float(cfg.vision.conf), iou=cfg.vision.iou, half=cfg.vision.half,
            device=(f"cuda:{cfg.vision.device}"
                    if isinstance(cfg.vision.device, int) else cfg.vision.device),
            log_fn=log_cb,
        )
    except Exception as e:  # noqa: BLE001
        log_cb(f"[v2][!] yolo: {e}"); out["yolo"] = None
    try:
        out["ocr"] = RealOcrAdapter(cfg.ocr, gpu=True)
    except Exception as e:  # noqa: BLE001
        log_cb(f"[v2][!] ocr: {e}"); out["ocr"] = None
    try:
        out["cooldown"] = RealCooldownAdapter(name="cd", poll_sec=1.0)
        out["buff"] = RealCooldownAdapter(name="buff", poll_sec=1.0, own_rec=True)
    except Exception as e:  # noqa: BLE001
        log_cb(f"[v2][!] cd/buff: {e}")
        out["cooldown"] = None; out["buff"] = None
    try:
        out["hpmp"] = RealHpMpAdapter(log_cb=log_cb)
    except Exception as e:  # noqa: BLE001
        log_cb(f"[v2][!] hpmp: {e}"); out["hpmp"] = None
    try:
        out["xp"] = RealXpAdapter(log_cb=log_cb)
    except Exception as e:  # noqa: BLE001
        log_cb(f"[v2][!] xp: {e}"); out["xp"] = None
    # 2026-05-05 — compat 패턴: bind_host 명시 + 30회 retry + 진단 로그.
    import time as _udp_time
    _bh = getattr(cfg.net, "bind_host", "0.0.0.0") or "0.0.0.0"
    _udp_port = int(getattr(cfg.net, "port", 54545))
    log_cb(f"[v2] udp_recv bind 시도 host={_bh} port={_udp_port}")
    out["udp"] = None
    _last_err = None
    for _try in range(1, 31):
        try:
            out["udp"] = RealUdpAdapter(port=_udp_port, bind_host=_bh)
            log_cb(
                f"[v2] udp_recv bind 성공 host={_bh} port={_udp_port} "
                f"(시도 {_try}회)"
            )
            break
        except Exception as e:  # noqa: BLE001
            _last_err = e
            _udp_time.sleep(0.2)
    if out["udp"] is None:
        log_cb(f"[v2][!] udp_recv bind 30회 실패 — 격수 좌표 수신 불가: {_last_err}")
    try:
        out["keys"] = RealKeysAdapter()
    except Exception as e:  # noqa: BLE001
        log_cb(f"[v2][!] keys: {e}"); out["keys"] = None
    return out


def _run_v2_native(cfg) -> int:
    """2026-05-05 Cycle 4-7 — V2MainWindow + HealerWorkerV2 직접 구동.

    v1 main_window 의존 끊기. _build_healer_adapters 활용해 어댑터 build,
    HealerWorkerV2 인스턴스 만들고 V2MainWindow 에 주입.

    2026-05-05 — worker_factory 람다 전달 (정지 후 재시작 RuntimeError 방지).
    BaseWatcher(threading.Thread) 가 stop 후 재시작 불가 → V2MainWindow 가
    정지 시 worker=None, 재시작 시 factory() 호출로 새 인스턴스 생성.
    factory 호출마다 adapter + worker 다시 build (heavy 약 5-10초).
    """
    log_cb: Any = lambda m: log.info("%s", m)

    def _build_worker():
        from src_v2.workers.healer_worker_v2 import HealerWorkerV2, HealerConfig
        adapters = _build_healer_adapters(cfg, log_cb)
        hcfg = HealerConfig()
        return HealerWorkerV2(
            cfg=hcfg,
            grabber=adapters.get("grabber"),
            yolo=adapters.get("yolo"),
            ocr=adapters.get("ocr"),
            cooldown=adapters.get("cooldown"),
            buff=adapters.get("buff"),
            hpmp=adapters.get("hpmp"),
            xp=adapters.get("xp"),
            udp=adapters.get("udp"),
            keys=adapters.get("keys"),
            log_callback=log_cb,
        )

    worker = _build_worker()

    from src_v2.ui.v2_main_window import V2MainWindow
    win = V2MainWindow(
        role="healer", cfg=cfg, worker=worker, worker_factory=_build_worker,
    )
    win.show()
    log.info("[v2] V2MainWindow native UI 시작")
    return None  # 호출자가 app.exec_() 처리


def _run_legacy_compat(cfg) -> None:
    """안전망 — v1 main_window 통째 복사본 (Cycle 7 까지 보존)."""
    from src_v2.ui.main_window_v2 import MainWindow
    win = MainWindow(cfg, initial_role="healer")
    win.show()
    log.info("[v2] legacy compat UI 시작")
    return win  # exec_ 는 호출자


def main() -> int:
    """v2 entry — default V2MainWindow native UI, --ui legacy 옵션으로 안전망.

    Cycle 4 운영 entry 재배선 (2026-05-05). V2MainWindow 가 부족한 기능
    발견되면 --ui legacy 로 즉시 fallback 가능.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ui", choices=("v2", "legacy"), default="v2",
        help="v2(default)=V2MainWindow native, legacy=main_window_v2 compat",
    )
    args = parser.parse_args()

    # 2026-05-05 — basicConfig 먼저 (root level INFO 보장) + file handler 추가.
    # 이전: file handler 먼저 → basicConfig 가 noop → root level WARNING →
    # INFO 로그가 파일에 안 떨어지는 버그. 사용자 신고로 수정.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    # root logger level 명시 (basicConfig 가 이미 호출됐다면 setLevel 만).
    logging.getLogger().setLevel(logging.INFO)

    import time as _time
    _ts = _time.strftime("%Y%m%d_%H%M%S")
    _log_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "logs",
    )
    try:
        os.makedirs(_log_dir, exist_ok=True)
        _log_file = os.path.join(_log_dir, f"healer_v2_native_{_ts}.log")
        _file_h = logging.FileHandler(_log_file, encoding="utf-8")
        _file_h.setFormatter(logging.Formatter(
            "%(asctime)s %(name)s %(levelname)s %(message)s"
        ))
        _file_h.setLevel(logging.INFO)
        logging.getLogger().addHandler(_file_h)
        logging.getLogger().info("[v2] file log 시작: %s", _log_file)
    except Exception as _e:  # noqa: BLE001
        logging.getLogger().warning("[v2] file log 시작 실패: %s", _e)

    from src.config import load as load_cfg
    cfg = load_cfg()

    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")

    if args.ui == "legacy":
        _win = _run_legacy_compat(cfg)
    else:
        _run_v2_native(cfg)

    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
