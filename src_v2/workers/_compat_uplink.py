"""v1_compat 의 _UplinkSenderShim 분리. audit 8.1 3단계.

격수 IP 동적 학습 — v1 healer_worker.py:2367 동치.
ControlListener 가 학습한 src_addr 을 set_attacker_addr 로 주입 가능.
이후 send 가 그 IP 우선 (cfg.peers 보조 — 둘 다 송신 가능).
"""
from __future__ import annotations

from typing import List, Optional


class UplinkSenderShim:
    """CooldownUplink 가 send(bytes) 만 호출 — UdpSender wrap.

    동적 격수 IP 학습 — v1 healer_worker.py:2367 동치.
    set_attacker_addr(ip) 로 ControlListener 가 학습한 src_addr 주입 가능.
    이후 send 가 그 IP 우선 (cfg.peers 보조 — 둘 다 송신).
    """

    def __init__(self, peers: List[str], port: int, log_emit=None) -> None:
        import logging
        from src.net.udp_sender import UdpSender  # type: ignore  # lazy
        # send_to 단일 송신 가능하게 빈 peers 로도 동작.
        self._snd = UdpSender(list(peers or []), port)
        self._port = int(port)
        self._dynamic_addr: Optional[str] = None
        # P0-5 contract 로그 — 학습 사건 / fallback 사건 명시.
        self._log = logging.getLogger("src_v2.workers.uplink_shim")
        self._log_emit = log_emit if callable(log_emit) else None
        self._learn_count = 0
        self._fallback_warned = False
        self._send_count = 0
        self._dynamic_send_count = 0
        self._static_send_count = 0
        # bootstrap 진단 — peers 비어있으면 1회 경고.
        if not peers:
            self._emit("[UPLINK-CONTRACT] bootstrap peers=[] — dynamic 학습 전엔 송신 불가")

    def _emit(self, s: str) -> None:
        try:
            self._log.info(s)
            if self._log_emit:
                self._log_emit(s)
        except Exception:
            pass

    def set_attacker_addr(self, ip: str, port: Optional[int] = None) -> None:
        """격수 IP 학습 시점 호출 — 다음 send 부터 이 IP 우선.

        v1_gap_fix_list P0-5: 학습 사건은 contract 로그로 명시.
        같은 IP 가 다시 학습되면 noop, 새 IP 면 [LEARN-IP] 로그.
        """
        try:
            new_ip = str(ip).strip() or None
            if not new_ip:
                return
            old = self._dynamic_addr
            if new_ip != old:
                self._learn_count += 1
                self._emit(
                    f"[UPLINK-CONTRACT] LEARN-IP src={new_ip} prev={old} count={self._learn_count}"
                )
            self._dynamic_addr = new_ip
            if port is not None:
                try:
                    self._port = int(port)
                except Exception:
                    pass
        except Exception:
            pass

    def send(self, payload: bytes) -> None:
        try:
            data = bytes(payload)
            self._send_count += 1
            # P0-5: dynamic 학습된 경우 dynamic 우선 강제 (static fallback 만 폴리시).
            if self._dynamic_addr:
                try:
                    self._snd.send_to(self._dynamic_addr, self._port, data)
                    self._dynamic_send_count += 1
                    return
                except Exception as e:
                    if not self._fallback_warned:
                        self._fallback_warned = True
                        self._emit(
                            f"[UPLINK-CONTRACT] dynamic send fail addr={self._dynamic_addr} "
                            f"err={e!r} → static peers fallback"
                        )
            # static peers fallback (bootstrap 또는 dynamic 실패).
            if self._snd.peers:
                self._snd.send(data)
                self._static_send_count += 1
        except Exception:
            pass

    def is_available(self) -> bool:
        return bool(self._dynamic_addr) or bool(self._snd.peers)

    def close(self) -> None:
        try:
            self._snd.close()
        except Exception:
            pass
