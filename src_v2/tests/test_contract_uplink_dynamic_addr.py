"""Uplink dynamic addr contract 테스트 (v1_gap_fix_list P0-5).

시나리오:
- bootstrap (peers=[], dynamic=None) → send() 무동작 + 경고 1회
- LEARN-IP 후 send() → dynamic 으로만 송신
- 같은 IP 재학습 → 로그 noop
- 새 IP 학습 → LEARN-IP 카운트 증가
- dynamic send 실패 시 static peers fallback + 경고 1회
"""
from __future__ import annotations
import sys
import types


class _FakeSender:
    def __init__(self, peers, port):
        self.peers = list(peers)
        self.port = port
        self.send_to_calls = []
        self.send_calls = []
        self.fail_send_to = False

    def send_to(self, ip, port, data):
        if self.fail_send_to:
            raise OSError("network unreachable")
        self.send_to_calls.append((ip, port, data))

    def send(self, data):
        self.send_calls.append(data)

    def close(self):
        pass


def _ensure_fake_src():
    """src.net.udp_sender 부재 환경(테스트 단독)에서 _FakeSender 주입."""
    if "src.net.udp_sender" in sys.modules:
        return
    pkg_src = types.ModuleType("src")
    pkg_net = types.ModuleType("src.net")
    mod = types.ModuleType("src.net.udp_sender")
    mod.UdpSender = _FakeSender
    sys.modules["src"] = pkg_src
    sys.modules["src.net"] = pkg_net
    sys.modules["src.net.udp_sender"] = mod


def _make(peers, log):
    _ensure_fake_src()
    # 정식 운영 모듈이 있으면 _FakeSender 로 한시 교체.
    import sys as _sys
    real = _sys.modules.get("src.net.udp_sender")
    real_cls = getattr(real, "UdpSender", None)
    real.UdpSender = _FakeSender
    try:
        from src_v2.workers._compat_uplink import UplinkSenderShim
        sh = UplinkSenderShim(peers, 6001, log_emit=log)
    finally:
        if real_cls is not None:
            real.UdpSender = real_cls
    return sh


def test_bootstrap_empty_peers_warns_once():
    logs = []
    sh = _make([], logs.append)
    assert any("bootstrap peers=[]" in s for s in logs)
    sh.send(b"x")
    # dynamic 없고 static 도 없음 → 송신 0
    assert sh._dynamic_send_count == 0
    assert sh._static_send_count == 0


def test_learn_ip_then_send_uses_dynamic_only():
    logs = []
    sh = _make(["1.2.3.4"], logs.append)
    sh.set_attacker_addr("100.64.0.5")
    sh.send(b"hello")
    assert sh._dynamic_send_count == 1
    assert sh._static_send_count == 0
    assert any("LEARN-IP" in s and "100.64.0.5" in s for s in logs)


def test_same_ip_relearn_no_duplicate_log():
    logs = []
    sh = _make([], logs.append)
    sh.set_attacker_addr("100.64.0.5")
    sh.set_attacker_addr("100.64.0.5")
    sh.set_attacker_addr("100.64.0.5")
    learn_logs = [s for s in logs if "LEARN-IP" in s]
    assert len(learn_logs) == 1, f"중복 학습 로그: {learn_logs}"


def test_new_ip_increments_learn_count():
    logs = []
    sh = _make([], logs.append)
    sh.set_attacker_addr("100.64.0.5")
    sh.set_attacker_addr("100.64.0.6")
    assert sh._learn_count == 2


def test_dynamic_fail_falls_back_to_static_with_warning():
    logs = []
    sh = _make(["1.2.3.4"], logs.append)
    sh.set_attacker_addr("100.64.0.5")
    sh._snd.fail_send_to = True
    sh.send(b"x")
    sh.send(b"y")
    # 경고 1회만
    fb_logs = [s for s in logs if "fallback" in s]
    assert len(fb_logs) == 1
    # static 으로 둘 다 fallback
    assert sh._static_send_count == 2


if __name__ == "__main__":
    test_bootstrap_empty_peers_warns_once()
    test_learn_ip_then_send_uses_dynamic_only()
    test_same_ip_relearn_no_duplicate_log()
    test_new_ip_increments_learn_count()
    test_dynamic_fail_falls_back_to_static_with_warning()
    print("ALL PASS")
