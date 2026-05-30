"""UDP adapter — wraps src/net/udp_receiver.py + protocol.py."""
from __future__ import annotations
import logging
from typing import Any, Optional

from ..core.types import AttackerState

log = logging.getLogger("src_v2.adapters.udp")


class SrcUdpAdapter:
    """Wraps an existing UdpReceiver. recv() returns AttackerState or None."""

    def __init__(self, src_recv: Any) -> None:
        self._r = src_recv

    def recv(self) -> Optional[AttackerState]:
        if self._r is None:
            return None
        try:
            # try common method names
            for m in ("recv_nowait", "recv", "get", "poll"):
                fn = getattr(self._r, m, None)
                if callable(fn):
                    r = fn()
                    return self._normalize(r)
            return None
        except Exception:  # noqa: BLE001
            log.exception("udp recv fail")
            return None

    def _normalize(self, r: Any) -> Optional[AttackerState]:
        if r is None:
            return None
        if isinstance(r, AttackerState):
            return r
        if isinstance(r, dict):
            return AttackerState(
                coord=tuple(r.get("coord")) if r.get("coord") else None,
                coord_valid=bool(r.get("coord_valid", r.get("coord") is not None)),
                map_name=str(r.get("map") or r.get("map_name") or ""),
                map_seq=int(r.get("map_seq", 0)),
                hp=int(r.get("hp", -1)),
                last_dir=str(r.get("last_dir", "-")),
                honma_sec=int(r.get("honma_sec", -1)),
                mujang_sec=int(r.get("mujang_sec", -1)),
                boho_sec=int(r.get("boho_sec", -1)),
            )
        return None

    def is_available(self) -> bool:
        return self._r is not None


class RealUdpAdapter(SrcUdpAdapter):
    """Production receiver adapter — wraps src.net.udp_receiver.UdpReceiver.

    Parses raw payload via src.net.protocol.State.from_bytes and converts
    to v2 AttackerState.
    """

    def __init__(self, port: int = 51900, bind_host: str = "0.0.0.0") -> None:
        from src.net.udp_receiver import UdpReceiver  # lazy
        # 2026-04-25 v1 UdpReceiver(bind_host, port) 시그니처 — bind_host 필수.
        r = UdpReceiver(bind_host=bind_host, port=port)
        try:
            r.start()
        except Exception:  # noqa: BLE001
            pass
        super().__init__(r)

    def recv(self):
        if self._r is None:
            return None
        try:
            # UdpReceiver typically exposes .latest() returning State
            for m in ("latest", "recv_nowait", "recv", "get"):
                fn = getattr(self._r, m, None)
                if callable(fn):
                    s = fn()
                    if s is None:
                        return None
                    # 2026-04-25 모든 v1 net.protocol.State 필드 1:1 보존.
                    # v1 ↔ v2 alias: hp_pct→hp, mp_pct→mp, debuff_honmasul_sec→honma_sec.
                    return AttackerState(
                        coord=(int(getattr(s, "x", 0)), int(getattr(s, "y", 0)))
                              if getattr(s, "coord_valid", False) else None,
                        coord_valid=bool(getattr(s, "coord_valid", False)),
                        map_name=str(getattr(s, "map_name", "") or ""),
                        map_seq=int(getattr(s, "map_seq", 0) or 0),
                        hp=int(getattr(s, "hp_pct", -1)),
                        mp=int(getattr(s, "mp_pct", -1)),
                        seq=int(getattr(s, "seq", 0) or 0),
                        last_dir=str(getattr(s, "last_dir", "-")),
                        honma_sec=int(getattr(s, "debuff_honmasul_sec", -1)),
                        mujang_sec=int(getattr(s, "buff_mujang_sec", -1)),
                        boho_sec=int(getattr(s, "buff_boho_sec", -1)),
                        map_change_pending=bool(
                            getattr(s, "map_change_pending", False)
                        ),
                        red_tab=bool(getattr(s, "red_tab", False)),
                    )
            return None
        except Exception:  # noqa: BLE001
            log.exception("real udp recv fail")
            return None

    def stop(self) -> None:
        try:
            fn = getattr(self._r, "stop", None) or getattr(self._r, "close", None)
            if callable(fn):
                fn()
        except Exception:  # noqa: BLE001
            pass


class RealUdpSenderAdapter:
    """Production sender adapter — wraps src.net.udp_sender.UdpSender.

    Used by AttackerWorkerV2: implements UdpSenderAdapter Protocol.
    """

    def __init__(self, peers, port: int = 51900) -> None:
        from src.net.udp_sender import UdpSender  # lazy
        from src.net.protocol import State, now_ms  # noqa: F401
        self._sender = UdpSender(peers, port)
        self._State = State
        self._now_ms = now_ms
        self._seq = 0

    def send(self, state) -> None:
        try:
            # 2026-04-26 호출자가 bytes 또는 AttackerState 둘 다 보낼 수 있음.
            # bytes → 그대로 송신 (CooldownReport.to_bytes() 등 이미 직렬화됨).
            # 객체 → coord/map/hp 등 추출 후 State.to_bytes().
            if isinstance(state, (bytes, bytearray, memoryview)):
                self._sender.send(bytes(state))
                return
            x, y = (state.coord if state.coord else (0, 0))
            st = self._State(
                seq=self._seq,
                ts_ms=self._now_ms(),
                map_name=state.map_name or "",
                coord_valid=bool(state.coord_valid),
                x=int(x), y=int(y),
                last_dir=state.last_dir or "-",
                map_seq=int(state.map_seq or 0),
                hp_pct=int(state.hp),
            )
            pkt = st.to_bytes()
            self._sender.send(pkt)
            self._seq += 1
        except Exception:  # noqa: BLE001
            log.exception("real udp send fail")

    def is_available(self) -> bool:
        return self._sender is not None

    def close(self) -> None:
        try:
            self._sender.close()
        except Exception:  # noqa: BLE001
            pass
