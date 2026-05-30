"""UDP receiver.

v5 확장:
- 기존 State 수신 (힐러측): `latest()` 유지 (호환).
- ControlCmd 콜백 (힐러측): `set_control_handler(fn)`.
- 송신자 IP 자동 획득 (힐러측): `last_src_addr()` — 격수 IP+port.
- CooldownReport 콜백 (격수측): 격수는 별도 수신 포트(ATTACKER_RECV_PORT)에
  `CooldownReceiver`를 bind해 사용.
"""
import socket
import threading
from typing import Optional, Callable, Tuple

from .protocol import State, ControlCmd, CooldownReport, parse_packet


class UdpReceiver:
    """힐러측 수신기. State latest + ControlCmd 콜백 + 격수 IP 자동 파악."""

    def __init__(self, bind_host: str, port: int):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # SO_REUSEADDR 금지: Windows UDP는 TIME_WAIT 없어 불필요하고,
        # 켜두면 ControlListener가 같은 포트에 공존 bind된 경우 패킷
        # 라우팅이 비결정적이 되어 State가 엉뚱한 소켓으로 감. 여기서
        # bind가 실패하면 listener가 아직 살아있다는 명확한 신호.
        try:
            self._sock.bind((bind_host, port))
        except OSError as e:
            import sys as _sys
            print(
                f"[UDP-BIND] FAIL bind_host={bind_host} port={port} err={e}",
                file=_sys.stderr, flush=True,
            )
            raise
        import sys as _sys
        print(
            f"[UDP-BIND] ok bind_host={bind_host} port={port}",
            file=_sys.stderr, flush=True,
        )
        self._sock.settimeout(0.5)
        self._latest: Optional[State] = None
        self._last_seq = -1
        self._last_src: Optional[Tuple[str, int]] = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._ctrl_handler: Optional[Callable[[ControlCmd], None]] = None
        self._state_first_logged = False

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def set_control_handler(self, fn: Callable[[ControlCmd], None]) -> None:
        """ControlCmd 수신 시 호출될 콜백. 수신 스레드에서 호출됨."""
        self._ctrl_handler = fn

    def last_src_addr(self) -> Optional[Tuple[str, int]]:
        """마지막으로 State를 받은 (src_ip, src_port). 격수 IP 자동 획득용."""
        with self._lock:
            return self._last_src

    def _loop(self):
        # 2026-04-22: 원래 구조로 원복. 이전 drain 패턴이 State 중간 drop 유발
        # 가능성. 단순 한 패킷씩 처리 → 매 State 즉시 latest 갱신.
        while self._running:
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            msg = parse_packet(data)
            if msg is None:
                continue
            if isinstance(msg, State):
                s = msg
                with self._lock:
                    if s.seq < self._last_seq and (self._last_seq - s.seq) < 1000:
                        continue
                    self._latest = s
                    self._last_seq = s.seq
                    self._last_src = addr
                if not self._state_first_logged:
                    self._state_first_logged = True
                    try:
                        import sys as _sys
                        print(
                            f"[UDP-RECV] first State from={addr} "
                            f"seq={s.seq} map='{s.map_name}'",
                            file=_sys.stderr, flush=True,
                        )
                    except Exception:
                        pass
            elif isinstance(msg, ControlCmd):
                if self._ctrl_handler is not None:
                    try:
                        self._ctrl_handler(msg)
                    except Exception:
                        pass

    def latest(self) -> Optional[State]:
        with self._lock:
            return self._latest

    def stop(self):
        self._running = False
        try:
            self._sock.close()
        except OSError:
            pass


class CooldownReceiver:
    """격수측 수신기. 힐러들이 보낸 CooldownReport를 인덱스별로 보관."""

    def __init__(self, bind_host: str, port: int):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((bind_host, port))
        self._sock.settimeout(0.5)
        self._reports: dict[int, CooldownReport] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        # 핸들러 시그니처: fn(report) 또는 fn(report, src_addr). 인자 수 자동 감지.
        self._on_report: Optional[Callable] = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def set_report_handler(self, fn: Callable[[CooldownReport], None]) -> None:
        self._on_report = fn

    def latest_for(self, src_idx: int) -> Optional[CooldownReport]:
        with self._lock:
            return self._reports.get(src_idx)

    def all_reports(self) -> dict:
        with self._lock:
            return dict(self._reports)

    def _loop(self):
        while self._running:
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            msg = parse_packet(data)
            if not isinstance(msg, CooldownReport):
                continue
            with self._lock:
                self._reports[msg.src_idx] = msg
            if self._on_report is not None:
                try:
                    # 먼저 (report, src_addr) 시도 → TypeError면 (report) fallback.
                    try:
                        self._on_report(msg, addr)
                    except TypeError:
                        self._on_report(msg)
                except Exception:
                    pass

    def stop(self):
        self._running = False
        try:
            self._sock.close()
        except OSError:
            pass
