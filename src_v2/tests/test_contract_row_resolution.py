"""row/IP resolved matching 계약 테스트 (v1_gap_fix_list P1-1).

attacker_worker_v2 가 publish 하는 payload 가 다음 셋을 명시 보유:
- resolved_row_idx: peers 매칭 결과 (없으면 -1)
- reported_idx: 송신자 자체 src_idx
- src_ip: src_addr 의 IP

UI 는 resolved >= 0 우선. mismatch 시 1회 경고.
"""
from __future__ import annotations
import os, sys, inspect

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


def test_payload_carries_resolved_reported_src_ip():
    """publish payload 에 3개 필드 다 있어야 함."""
    from src_v2.workers import attacker_worker_v2
    src = inspect.getsource(attacker_worker_v2.AttackerWorkerV2._handle_cd_report)
    assert '"resolved_row_idx"' in src, "resolved_row_idx 키 누락"
    assert '"reported_idx"' in src, "reported_idx 키 누락"
    assert '"src_ip"' in src, "src_ip 키 누락"


def test_resolved_minus1_on_peer_miss():
    """peers 에 IP 없으면 resolved_row_idx=-1 (reported fallback)."""
    from src_v2.workers import attacker_worker_v2
    src = inspect.getsource(attacker_worker_v2.AttackerWorkerV2._handle_cd_report)
    # -1 init + peers 매칭 시 갱신 패턴 검증.
    assert "resolved_row_idx = -1" in src, "init -1 패턴 누락"
    assert "resolved_row_idx = _i" in src, "peers 매칭 시 idx 할당 누락"


def test_mismatch_warning_present():
    """resolved != reported 시 1회 경고 로그."""
    from src_v2.workers import attacker_worker_v2
    src = inspect.getsource(attacker_worker_v2.AttackerWorkerV2._handle_cd_report)
    assert "CD-RECV-MISMATCH" in src, "mismatch 경고 prefix 누락"


def test_ui_prefers_resolved():
    """UI _on_attacker_cooldown 가 resolved_row_idx 우선 사용."""
    from src_v2.ui.main_window_v2 import MainWindow
    src = inspect.getsource(MainWindow._on_attacker_cooldown)
    assert "resolved_row_idx" in src, "UI 가 resolved 우선 사용 안 함"
    assert "reported_idx" in src, "UI fallback 으로 reported_idx 사용 안 함"


if __name__ == "__main__":
    test_payload_carries_resolved_reported_src_ip()
    test_resolved_minus1_on_peer_miss()
    test_mismatch_warning_present()
    test_ui_prefers_resolved()
    print("ALL PASS")
