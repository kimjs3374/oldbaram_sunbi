"""로거 공용 셋업."""
from __future__ import annotations
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"

# 프로젝트 버전. 로그 헤더에 표기해 이전 로그와 구분.
# 파일 수정 시 업데이트.
BUILD_VERSION = "2026-04-21 v6-edge"


def _setup_logger(role: str = "healer") -> tuple[logging.Logger, Path]:
    """role: 'healer' | 'attacker'. 파일명 prefix 구분용."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    path = LOG_DIR / f"{role}_{ts}.log"
    lg = logging.getLogger(role)
    lg.setLevel(logging.DEBUG)
    for h in list(lg.handlers):
        lg.removeHandler(h)
    fh = RotatingFileHandler(path, maxBytes=20 * 1024 * 1024,
                             backupCount=3, encoding="utf-8")
    # 포매터: 날짜도 포함 (YYYY-MM-DD HH:MM:SS).
    fh.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    lg.addHandler(fh)
    # 이전 세션 로그와 구분용 헤더 — 파일 상단에 한 번 찍어둠.
    header = (
        "\n"
        + "=" * 78 + "\n"
        + f"  oldbaram {role}\n"
        + f"  BUILD         : {BUILD_VERSION}\n"
        + f"  SESSION START : {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
        + f"  LOG FILE      : {path}\n"
        + "=" * 78
    )
    lg.info(header)
    return lg, path
