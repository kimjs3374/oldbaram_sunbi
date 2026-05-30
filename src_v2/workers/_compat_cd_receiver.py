"""격수측 CooldownReport 수신 adapter. v1 attacker_worker.py:264-373 1:1.

v1_compat 의 attacker `_build_adapters` 에서 nested class 였던 것을 분리.
audit 8.1 3단계 분할.
"""
from __future__ import annotations

import socket
from typing import Any, Callable, List, Tuple


class UdpCdReceiver:
    """힐러 1Hz CooldownReport 수신용. attacker_recv_port (default 45455) bind."""

    def __init__(self, port: int, log_cb: Callable[[str], None]) -> None:
        from src.net.protocol import CooldownReport  # type: ignore  # lazy
        self._CooldownReport = CooldownReport
        self.port = int(port)
        self.log_cb = log_cb
        self.sock = None
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

    def poll(self) -> List[Tuple[Any, Any]]:
        if self.sock is None:
            return []
        out_lst = []
        for _ in range(64):
            try:
                data, addr = self.sock.recvfrom(2048)
            except BlockingIOError:
                break
            except Exception:
                break
            try:
                rep = self._CooldownReport.from_bytes(data)
                if rep is not None:
                    out_lst.append((rep, addr))
            except Exception:
                continue
        return out_lst

    def close(self) -> None:
        try:
            if self.sock is not None:
                self.sock.close()
        except Exception:
            pass
