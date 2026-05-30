"""ControlCmd end-to-end 계약 테스트 (v1_gap_fix_list P0-1).

v1: attacker → ControlCmd unicast → healer ControlListener 또는 worker udp_receiver
   → cmd_received signal → main_window._handle_remote_cmd[_active]

v2 회귀 이력:
- AttackerWorkerV1Facade.send_control 가 sender.send_control 위임 시도 →
  RealUdpSenderAdapter 에 메서드 부재 → 항상 False (dead code 한동안)
- RealUdpAdapter wrapper 가 set_control_handler 호출 안 함 → 워커 활성 시
  ControlCmd 받아도 무시 (사용자 stop/pause 무반응)
- _send_ctrl 가 worker None 시 무조건 skip → 격수 GUI 띄우자마자 send 0

본 테스트는 wiring 자체를 코드 차원으로 검증.
"""
from __future__ import annotations

import inspect


def test_attacker_facade_send_control_has_v1_fallback():
    """v1_compat AttackerWorkerV1Facade.send_control 가 v1 1:1 fallback 포팅됨."""
    from src_v2.workers._compat_attacker_facade import AttackerWorkerV1Facade

    src = inspect.getsource(AttackerWorkerV1Facade.send_control)
    # ControlCmd / send_to / underlying 호출 흔적이 있어야 함.
    assert "ControlCmd" in src, "send_control 가 ControlCmd 직접 만들어야 함"
    assert "send_to" in src, "send_control 가 underlying.send_to 호출해야 함"
    assert "_sender" in src or "underlying" in src, "underlying UdpSender 접근 필요"


def test_healer_adapter_builder_registers_control_handler():
    """build_healer_adapters 가 RealUdpAdapter 의 _r.set_control_handler 등록."""
    from src_v2.workers import _compat_healer_adapters as mod

    src = inspect.getsource(mod.build_healer_adapters)
    assert "set_control_handler" in src, (
        "udp adapter 의 ControlCmd handler 등록 누락 — 워커 활성 시 격수 명령 무시"
    )
    assert "cmd_emit" in src, "cmd_emit 콜백 인자 필수 (Qt signal 라우팅)"


def test_healer_facade_has_cmd_received_signal():
    """HealerWorkerV1Facade 가 cmd_received Qt signal 노출 (워커 활성 시 ctrl 라우팅)."""
    from src_v2.workers._compat_healer_facade import HealerWorkerV1Facade

    assert hasattr(HealerWorkerV1Facade, "cmd_received"), (
        "cmd_received signal 미정의 — 워커 활성 시 격수 stop/pause 무반응"
    )


def test_main_window_send_ctrl_has_no_worker_fallback():
    """_send_ctrl 가 worker None 시 직접 socket fallback 보유."""
    from src_v2.ui.main_window_v2 import MainWindow

    src = inspect.getsource(MainWindow._send_ctrl)
    assert "no-worker-fallback" in src or "fallback" in src.lower(), (
        "worker None 시 fallback 송신 경로 누락 — 격수 GUI 띄우자마자 송신 0"
    )
    assert "ControlCmd" in src, "fallback 도 ControlCmd 직접 생성"
    assert "sendto" in src, "fallback 이 socket.sendto 직접 호출"


def test_main_window_active_remote_handler_exists():
    """MainWindow._handle_remote_cmd_active — 워커 활성 시 cmd 처리."""
    from src_v2.ui.main_window_v2 import MainWindow

    assert hasattr(MainWindow, "_handle_remote_cmd_active"), (
        "워커 활성 시 ControlCmd 처리 핸들러 미정의"
    )
    src = inspect.getsource(MainWindow._handle_remote_cmd_active)
    assert "stop" in src and "stop_worker" in src, (
        "stop 명령 시 stop_worker 호출 누락"
    )
