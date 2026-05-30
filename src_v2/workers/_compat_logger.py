"""v1_compat.py 에서 분리한 logger setup.

audit 8.1 3단계 분할 진행 1차 — facade 클래스 거대화 차단을 위해
helper 부터 sibling 모듈로 분리.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

_compat_logger_singleton = None
_compat_logger_path = None
_compat_file_handler = None


def setup_compat_logger():
    """프로세스 단위 단일 로거. UI 뜬 직후 첫 호출 시 파일 생성, 이후 모든
    호출자 (HealerWorkerV1Facade / AttackerWorkerV1Facade / 워커 재시작) 가
    같은 파일 사용. 워커 stop/start 마다 새 파일 생성 안 함.

    sub-logger (cooldown_ocr, numlock_cycle 등) 의 INFO emit 도 동일 file
    handler 부착해 진단 로그 통합.
    """
    global _compat_logger_singleton, _compat_logger_path, _compat_file_handler
    if _compat_logger_singleton is not None:
        return _compat_logger_singleton, _compat_logger_path

    log_dir = Path("logs")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = log_dir / f"healer_v2_{ts}.log"
    lg = logging.getLogger("src_v2.runtime")
    lg.setLevel(logging.INFO)
    lg.propagate = False
    fh = None
    if not lg.handlers:
        try:
            fh = logging.FileHandler(str(path), encoding="utf-8")
            fh.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s %(message)s"))
            lg.addHandler(fh)
        except Exception:
            fh = None
    _compat_logger_singleton = lg
    _compat_logger_path = str(path)
    _compat_file_handler = fh
    if fh is not None:
        for name in (
            "attacker",
            "src_v2.workers.healer",
            "src_v2.eyes.cooldown",
            "src_v2.eyes.udp",
            "src_v2.eyes.ocr",
            "src_v2.eyes.yolo",
            "src_v2.eyes.hpmp",
            "src_v2.eyes.capture",
            "src_v2.adapters.cooldown",
            "src_v2.adapters.udp",
            "src_v2.adapters.ocr",
            "src_v2.adapters.yolo",
            "src_v2.adapters.hpmp",
            "src_v2.brain.engine",
            "src_v2.hands.numlock",
            "src_v2.hands.executor",
            "src_v2.workers.v1_compat",
        ):
            try:
                _ext = logging.getLogger(name)
                _ext.setLevel(logging.INFO)
                if not any(getattr(h, "baseFilename", None)
                           == getattr(fh, "baseFilename", None)
                           for h in _ext.handlers):
                    _ext.addHandler(fh)
            except Exception:
                pass
    return lg, str(path)
