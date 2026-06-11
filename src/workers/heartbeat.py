from __future__ import annotations
import ctypes
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets

from ..utils.logger_setup import _setup_logger
from ..utils.win_helpers import _user32, _is_fg_hwnd, frame_to_qpix


class AttackerHeartbeat(QtCore.QThread):
    """격수 GUI 상시 가동 heartbeat.

    GUI 기동 즉시 가동(워커/시작버튼 무관):
      1. peers 전원에게 1초마다 ControlCmd(cmd="ping", target=-1) 송신
         → 힐러의 ControlListener가 패킷 src_addr로 격수 IP 자동 획득.
      2. attacker_recv_port에 CooldownReceiver bind → 힐러 heartbeat 수신
         → 격수 UI의 힐러 행이 즉시 연결됨 상태로 갱신.

    AttackerWorker가 기동되면 이 클래스를 stop()으로 해제하고 워커가
    port/CD 점유를 이어받음. 워커 종료 후 다시 기동.
    """
    cooldown_update = QtCore.pyqtSignal(dict)

    def __init__(self, cfg):
        super().__init__()
        self._cfg = cfg
        self._running = True
        self._sock = None
        self._cd_recv = None

    def stop(self):
        self._running = False
        try:
            if self._cd_recv is not None:
                self._cd_recv.stop()
        except Exception:
            pass
        try:
            if self._sock is not None:
                self._sock.close()
        except Exception:
            pass

    def run(self):
        import socket as _socket
        try:
            from ..net.protocol import ControlCmd, now_ms
            from ..net.udp_receiver import CooldownReceiver
        except Exception:
            return
        # 수신기: 힐러 heartbeat의 CooldownReport 수신.
        bind_host = getattr(self._cfg.net, "bind_host", "0.0.0.0")
        recv_port = int(getattr(
            self._cfg.net, "attacker_recv_port", 45455
        ))
        try:
            self._cd_recv = CooldownReceiver(bind_host, recv_port)
            cfg = self._cfg

            def _on_rep(rep, src_addr=None):
                # src_addr[0]으로 peers 매칭 → 힐러 PC별 고유 행 인덱스 결정.
                # 힐러 PC의 cfg.net.healer_idx 설정 오류(두 PC 모두 0)로
                # 같은 행에 덮어쓰는 문제 방지. 일치 실패 시 rep.src_idx로 fallback.
                row_idx = int(getattr(rep, "src_idx", 0))
                src_ip = ""
                if src_addr and isinstance(src_addr, tuple) and src_addr[0]:
                    src_ip = str(src_addr[0])
                    peers = list(getattr(cfg.net, "peers", []))
                    for i, p in enumerate(peers):
                        if str(p).strip() == src_ip:
                            row_idx = i
                            break
                try:
                    self.cooldown_update.emit({
                        "src_idx": row_idx,
                        "reported_idx": int(getattr(rep, "src_idx", 0)),
                        "src_ip": src_ip,
                        "cd_parlyuk": rep.cd_parlyuk,
                        "cd_baekho": rep.cd_baekho,
                        # 2026-06-12: 쩔캐(현인) 지폭지술 쿨.
                        "cd_jipok": int(getattr(rep, "cd_jipok", -1)),
                        "ts_ms": rep.ts_ms,
                        "armed": getattr(rep, "armed", False),
                        "nickname": getattr(rep, "nickname", ""),
                        "buff_parlyuk_sec": int(getattr(
                            rep, "buff_parlyuk_sec", -1)),
                        "xp_per_hour": int(getattr(rep, "xp_per_hour", 0)),
                        "event_text": str(getattr(rep, "event_text", "") or ""),
                        "event_seq": int(getattr(rep, "event_seq", 0)),
                        # 힐러 HP/MP (격수 HP/MP 오버레이용).
                        "hp_pct": int(getattr(rep, "hp_pct", -1)),
                        "mp_pct": int(getattr(rep, "mp_pct", -1)),
                        "hp_cur": int(getattr(rep, "hp_cur", -1)),
                        "mp_cur": int(getattr(rep, "mp_cur", -1)),
                        "hp_max": int(getattr(rep, "hp_max", 0)),
                        "mp_max": int(getattr(rep, "mp_max", 0)),
                        "self_heal_hp_thr": int(getattr(
                            rep, "self_heal_hp_thr", -1)),
                        "gyoungryeok_mp_thr": int(getattr(
                            rep, "gyoungryeok_mp_thr", -1)),
                        # 힐러 맵/좌표/상태 (격수 UI 힐러 행 세로 블록용).
                        "healer_map": str(getattr(rep, "healer_map", "") or ""),
                        "healer_x": int(getattr(rep, "healer_x", 0)),
                        "healer_y": int(getattr(rep, "healer_y", 0)),
                        "coord_valid": bool(getattr(rep, "coord_valid", False)),
                        "state_text": str(getattr(rep, "state_text", "") or ""),
                    })
                except Exception:
                    pass

            self._cd_recv.set_report_handler(_on_rep)
            self._cd_recv.start()
        except OSError:
            self._cd_recv = None
        # 송신 소켓.
        try:
            s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
            s.settimeout(0.5)
        except Exception:
            return
        self._sock = s
        port = int(getattr(self._cfg.net, "port", 54545))
        announced = False
        while self._running:
            peers = list(getattr(self._cfg.net, "peers", []))
            if peers:
                if not announced:
                    announced = True
                    try:
                        import sys as _sys
                        print(
                            f"[ATK-HB] pinging {peers}:{port}",
                            file=_sys.stderr, flush=True,
                        )
                    except Exception:
                        pass
                try:
                    c = ControlCmd(
                        target_idx=-1, cmd="ping", ts_ms=now_ms()
                    )
                    data = c.to_bytes()
                    for p in peers:
                        if not p:
                            continue
                        try:
                            s.sendto(data, (p, port))
                        except Exception:
                            pass
                except Exception:
                    pass
            for _ in range(10):
                if not self._running:
                    break
                time.sleep(0.1)
        try:
            s.close()
        except Exception:
            pass




class HealerHeartbeat(QtCore.QThread):
    """힐러 GUI 기동 즉시 가동되는 격수 연결 heartbeat.

    격수 IP가 확보되면 1초마다 빈 CooldownReport(armed=False)를 격수
    수신포트(attacker_recv_port)로 송신 → 격수 UI의 힐러 행이 연결됨
    상태(초록)로 전환됨. 워커 기동/정지와 무관하게 GUI 종료 전까지 돈다.

    격수 IP는 3가지 경로로 획득:
      1. ControlListener.attacker_seen (격수 브로드캐스트 첫 패킷).
      2. 사용자가 명시 설정 (set_attacker_addr).
      3. 워커의 UdpReceiver.last_src_addr (워커 돌 때).
    """

    def __init__(self, cfg):
        super().__init__()
        self._cfg = cfg
        self._addr: Optional[Tuple[str, int]] = None
        self._nickname: str = ""
        self._armed: bool = False
        self._cd_parlyuk: int = -1
        self._cd_baekho: int = -1
        self._lock = threading.Lock()
        self._running = True
        self._sock = None

    def set_attacker_addr(self, ip: str, port: int) -> None:
        with self._lock:
            self._addr = (str(ip), int(port))

    def attacker_addr(self) -> Optional[Tuple[str, int]]:
        with self._lock:
            return self._addr

    def update_state(self, armed: bool, nickname: str,
                     cd_parlyuk: int, cd_baekho: int) -> None:
        with self._lock:
            self._armed = bool(armed)
            self._nickname = str(nickname or "")
            self._cd_parlyuk = int(cd_parlyuk)
            self._cd_baekho = int(cd_baekho)

    def stop(self):
        self._running = False

    def run(self):
        # HealerHeartbeat 송신 완전 비활성화.
        # 과거 heartbeat는 update_state가 시작 시 1회만 호출돼 초기 고정값
        # (nickname="힐러N", armed=False, cdp=-1, cdb=-1)을 1Hz로 계속 송신.
        # cooldown_ocr 루프의 실제 CooldownReport와 번갈아 격수에 도착 →
        # 격수 오버레이/행에서 닉/쿨다운/뱃지(armed 빨강↔초록) 모두 깜빡임.
        # CooldownReport 루프가 이미 1Hz 송신하므로 heartbeat는 기능 중복.
        # 인스턴스/set_attacker_addr 경로는 유지(향후 재활성화 대비), 송신만 제거.
        return


