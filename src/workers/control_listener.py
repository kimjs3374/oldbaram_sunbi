from __future__ import annotations
import ctypes
import logging
import os
import socket
import time
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets

from ..utils.logger_setup import _setup_logger
from ..utils.win_helpers import _user32, _is_fg_hwnd, frame_to_qpix



class ControlListener(QtCore.QThread):
    """힐러 UI 상시 ControlCmd 수신기.

    워커(HealerWorker)가 돌지 않을 때에도 cfg.net.port에 UDP bind하여
    격수의 start 명령을 받으면 MainWindow가 워커를 자동 기동시킬 수 있게 함.

    포트 경합: HealerWorker.run() 내부의 UdpReceiver도 동일 포트에 bind →
    두 리스너가 동시에 bind 불가. 해결:
      - start_worker() 전에 stop() 호출 → socket close.
      - _on_stopped() 시 다시 ControlListener.start() 호출.

    워커 실행 중에는 HealerWorker의 UdpReceiver가 ControlCmd를 처리하므로
    이 리스너는 중복 수신하지 않음 (닫혀 있음).
    """
    cmd_received = QtCore.pyqtSignal(str, int)  # (cmd, target_idx)
    # 첫 패킷(State/ctrl 무관) 도착 시 격수 IP 공지 → Heartbeat가 45455로 송신 시작.
    attacker_seen = QtCore.pyqtSignal(str, int)  # (ip, port)

    def __init__(self, bind_host: str, port: int, my_idx: int):
        super().__init__()
        self._bind_host = bind_host
        self._port = int(port)
        self._my_idx = int(my_idx)
        # init에서 True. stop()이 run() bind 전에 호출되어도 안전하게 종료.
        self._running = True
        self._sock = None
        self._attacker_announced = False

    def set_my_idx(self, idx: int) -> None:
        self._my_idx = int(idx)

    def stop(self):
        self._running = False
        try:
            if self._sock is not None:
                self._sock.close()
        except Exception:
            pass

    def run(self):
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # SO_REUSEADDR 제거: Windows UDP는 TIME_WAIT 없어 불필요하고,
            # 켜두면 워커의 UdpReceiver와 동시 bind되어 패킷 라우팅이 엉킴.
            s.bind((self._bind_host, self._port))
            s.settimeout(0.5)
        except OSError:
            # bind 실패: 워커가 이미 해당 포트를 잡고 있음 → 조용히 종료.
            self._running = False
            return
        self._sock = s
        # bind 후에 stop이 먼저 호출된 상태면 즉시 소켓 정리하고 탈출.
        if not self._running:
            try:
                s.close()
            except Exception:
                pass
            return
        try:
            from ..net.protocol import parse_packet, ControlCmd, State
        except Exception:
            return
        while self._running:
            try:
                data, addr = s.recvfrom(4096)
            except Exception:
                if not self._running:
                    break
                continue
            # 패킷 타입 관계없이 src_addr로 격수 IP 즉시 공지 (1회).
            if not self._attacker_announced and addr and addr[0]:
                self._attacker_announced = True
                try:
                    self.attacker_seen.emit(str(addr[0]), int(addr[1]))
                except Exception:
                    pass
            try:
                msg = parse_packet(data)
            except Exception:
                continue
            if not isinstance(msg, ControlCmd):
                continue
            if msg.target_idx not in (-1, self._my_idx):
                continue
            try:
                self.cmd_received.emit(str(msg.cmd), int(msg.target_idx))
            except Exception:
                pass
        try:
            s.close()
        except Exception:
            pass


