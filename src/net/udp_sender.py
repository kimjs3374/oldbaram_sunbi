"""UDP unicast sender. 여러 peer에 동일 패킷 전송.

2026-04-22: 전부 병렬 처리 원칙. 이전 queue + 전용 tx 스레드 구조 제거.
non-blocking socket 이라 sendto() 가 즉시 리턴 — 스레드 경유할 이유 없음.
실패 시 drop (UDP 본래 성질). State 는 매 프레임 재송신되므로 손실 허용.
"""
import socket
from typing import List


class UdpSender:
    def __init__(self, peers: List[str], port: int):
        self.peers = peers
        self.port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 256 * 1024)
        # non-blocking: sendto 가 즉시 반환. 버퍼 가득/peer 다운 시 drop.
        self._sock.setblocking(False)

    def send(self, data: bytes):
        """peers 전체에 port로 송신 (비블로킹)."""
        for peer in self.peers:
            try:
                self._sock.sendto(data, (peer, self.port))
            except Exception:
                pass

    def send_to(self, peer: str, port: int, data: bytes) -> bool:
        """단일 (peer, port)에 송신 (비블로킹). 성공 여부 반환."""
        try:
            self._sock.sendto(data, (peer, int(port)))
            return True
        except Exception:
            return False

    def close(self):
        try:
            self._sock.close()
        except Exception:
            pass
