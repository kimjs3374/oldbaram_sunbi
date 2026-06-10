"""로거 공용 셋업.

- 세션 1파일: 프로그램 켜고 끌 때까지 같은 role 은 한 파일 (분할 없음).
- 파일명: {닉네임}_{역할}_{날짜_시간}.log  (닉은 시작 시 set_session 으로 주입).
- 워커는 기존대로 _setup_logger(role) 만 호출 — 닉은 모듈 전역에서 가져옴.
"""
from __future__ import annotations

import logging
import re
import socket
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"


def local_ip_suffix(default: str = "0") -> str:
    """PC 구분용 IP 끝자리. Tailscale(100.x) 우선, 없으면 주 IP 끝자리."""
    try:
        cands = []
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET):
            cands.append(info[4][0])
        for ip in cands:
            if ip.startswith("100."):
                return ip.split(".")[-1]
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0].split(".")[-1]
        finally:
            s.close()
    except Exception:
        return default

# 프로젝트 버전. 로그 헤더에 표기해 이전 로그와 구분.
BUILD_VERSION = "2026-06-08 cloud"

_SESSION_NICK = ""          # 시작 다이얼로그에서 set_session 으로 주입
_CACHE: dict = {}           # role -> (logger, path). 프로세스 내 1파일 보장


def set_session(nick: str = "", role: str = "") -> None:
    """GUI 시작 시 1회 호출. 닉네임을 로그 파일명에 반영."""
    global _SESSION_NICK
    _SESSION_NICK = (nick or "").strip()


def _sanitize(s: str) -> str:
    """파일명 금지문자 제거 (한글은 유지)."""
    out = re.sub(r'[\\/:*?"<>|\s]+', "", s or "")
    return out or "nonick"


def _setup_logger(role: str = "healer") -> "tuple[logging.Logger, Path]":
    """role: 'healer' | 'attacker'. 같은 role 재호출 시 동일 파일 재사용."""
    if role in _CACHE:
        return _CACHE[role]
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    nick = _sanitize(_SESSION_NICK)
    ipsuf = local_ip_suffix()  # PC 구분(IP 끝자리). storage 업로드에도 동일 활용.
    path = LOG_DIR / f"{nick}_{role}_{ipsuf}_{ts}.log"
    lg = logging.getLogger(role)
    lg.setLevel(logging.DEBUG)
    for h in list(lg.handlers):
        lg.removeHandler(h)
    # 분할 없는 단일 FileHandler → 끄기 전까지 한 파일.
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    lg.addHandler(fh)
    header = (
        "\n"
        + "=" * 78 + "\n"
        + f"  oldbaram {role}  (nick={_SESSION_NICK or '-'})\n"
        + f"  BUILD         : {BUILD_VERSION}\n"
        + f"  SESSION START : {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
        + f"  LOG FILE      : {path}\n"
        + "=" * 78
    )
    lg.info(header)
    _CACHE[role] = (lg, path)
    return _CACHE[role]
