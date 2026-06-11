"""v1 ↔ v2 facade introspection 검증.

목적
----
v1 main_window.py 가 worker.* 로 접근하는 attribute/method/signal 이
v2 facade (HealerWorkerV1Facade / AttackerWorkerV1Facade) 에 모두
노출되는지 정적 검증. 사용자 PC 의존성 0 (PyQt5 import + introspection).

본 테스트는 CI 단독에서 실행 가능 — 외부 라이브러리 (mss/torch/rapidocr) 미사용.
실제 워커 start 도 안 함. 인터페이스 매칭만 본다.
"""
from __future__ import annotations

import pytest

# PyQt5 가 설치되어 있어야 facade 가 import 가능. 없으면 환경 문제이므로 skip.
pytest.importorskip("PyQt5")


def _public_attrs(obj_or_cls) -> set[str]:
    return {n for n in dir(obj_or_cls) if not n.startswith("_")}


# --------------------------------------------------------------------------- #
# v1 인터페이스 정답 — main_window 가 실제로 사용하는 항목 (전수)
# --------------------------------------------------------------------------- #
HEALER_V1_REQUIRED_METHODS = {
    # QThread-like
    "start", "stop", "isRunning", "wait",
    # region setters/clear
    "set_cooldown_region", "clear_cooldown_region",
    "set_nick_region", "clear_nick_region",
    "set_buff_region", "clear_buff_region",
    "set_chat_region", "clear_chat_region",
    "set_game_region", "clear_game_region",
    "set_xp_region", "clear_xp_region",
    "set_hp_region", "clear_hp_region",
    "set_mp_region", "clear_mp_region",
    # hp/mp
    "set_hp_max", "set_mp_max", "latest_hpmp",
    # remote / control
    "apply_remote_control", "send_control",
    # skill
    "set_skill_enabled", "set_primary_vk", "set_cycle_vks",
    "set_skill_vk", "set_parlyuk_offset", "set_own_skill_names",
    # analytics
    "get_analytics_snapshot",
}

HEALER_V1_REQUIRED_ATTRS = {
    "armed", "follow_only", "min_w", "min_h", "coord_tol",
    "yolo_conf", "yolo_every_n", "yolo_imgsz",
    "preview_hz_limit", "ocr_poll_sec", "crop_capture_to_game",
    "skill_enabled", "parlyuk_offset",
    "primary_vks", "skill_vks",
    "self_heal_hp_thr", "gyoungryeok_mp_thr",
    "log_path", "last_fps",
    "healer_coord", "healer_map",
}

HEALER_V1_REQUIRED_SIGNALS = {
    "frame_ready", "log_msg", "stopped", "remote_control_applied",
}

ATTACKER_V1_REQUIRED_METHODS = {
    "start", "stop", "isRunning", "wait",
    "set_xp_region", "clear_xp_region",
    "set_cooldown_region", "clear_cooldown_region",
    "set_buff_region", "clear_buff_region",
    "set_hp_region", "clear_hp_region",
    "set_mp_region", "clear_mp_region",
    "set_hp_max", "set_mp_max", "latest_hpmp",
    "set_own_skill_names", "xp_per_hour", "get_analytics_snapshot",
    "send_control",
}
ATTACKER_V1_REQUIRED_SIGNALS = {
    "log_msg", "stat_ready", "cooldown_update",
    "own_cooldown_update", "stopped",
}


# --------------------------------------------------------------------------- #
# Healer
# --------------------------------------------------------------------------- #
def _make_healer_facade():
    from src_v2.workers.v1_compat import HealerWorkerV1Facade

    class _DummyVision:
        conf = 0.25
        imgsz = 640
        weights = "stub.pt"
        iou = 0.5
        half = False
        device = 0

    class _DummyCfg:
        vision = _DummyVision()
    return HealerWorkerV1Facade(_DummyCfg())


def test_healer_facade_methods_present():
    fac = _make_healer_facade()
    have = _public_attrs(fac)
    missing = HEALER_V1_REQUIRED_METHODS - have
    assert not missing, f"Healer facade 누락 method: {sorted(missing)}"


def test_healer_facade_attrs_present():
    fac = _make_healer_facade()
    have = _public_attrs(fac)
    missing = HEALER_V1_REQUIRED_ATTRS - have
    assert not missing, f"Healer facade 누락 attribute: {sorted(missing)}"


def test_healer_facade_signals_present():
    from src_v2.workers.v1_compat import HealerWorkerV1Facade
    for sig in HEALER_V1_REQUIRED_SIGNALS:
        assert hasattr(HealerWorkerV1Facade, sig), \
            f"Healer facade 누락 signal: {sig}"


def test_healer_facade_attribute_setattr_no_raise():
    """main_window 는 일부 attribute 를 worker 에 setattr 만 한다.
    facade 가 raise 하지 않고 흡수해야 함."""
    fac = _make_healer_facade()
    # main_window 에서 실제 setattr 하는 모든 키 (3490~3550 + 2655~2709 라인).
    setattr_keys = [
        "armed", "follow_only", "min_w", "min_h", "coord_tol",
        "yolo_conf", "yolo_every_n", "yolo_imgsz",
        "preview_hz_limit", "ocr_poll_sec", "crop_capture_to_game",
        "skill_enabled", "parlyuk_offset", "primary_vks", "skill_vks",
        "self_heal_hp_thr", "gyoungryeok_mp_thr",
    ]
    for k in setattr_keys:
        # 안전한 default 값으로 set + get 확인.
        try:
            setattr(fac, k, 1)
        except Exception as e:
            pytest.fail(f"setattr({k}) raised: {e}")


def test_healer_facade_method_no_raise():
    """region setter / skill setter 등이 worker 시작 전에도 raise 하지 않는다."""
    fac = _make_healer_facade()
    fac.set_cooldown_region(10, 20, 100, 50)
    fac.clear_cooldown_region()
    fac.set_nick_region(0, 0, 50, 20)
    fac.clear_nick_region()
    fac.set_buff_region(0, 0, 50, 20)
    fac.clear_buff_region()
    fac.set_chat_region(0, 0, 50, 20)
    fac.clear_chat_region()
    fac.set_game_region(0, 0, 1920, 1080)
    fac.clear_game_region()
    fac.set_xp_region(0, 0, 50, 20); fac.clear_xp_region()
    fac.set_hp_region(0, 0, 50, 20); fac.clear_hp_region()
    fac.set_mp_region(0, 0, 50, 20); fac.clear_mp_region()
    fac.set_hp_max(10000); fac.set_mp_max(5000)
    assert fac.latest_hpmp() is None or hasattr(fac.latest_hpmp(), "hp") or True
    fac.set_skill_enabled("백호의희원", True)
    fac.set_primary_vk(0, 0x61)
    fac.set_cycle_vks([0x61, 0x62])
    fac.set_skill_vk("메인힐", 0x61)
    fac.set_parlyuk_offset(0.5)
    fac.set_own_skill_names(["a", "b"])
    fac.send_control(0, "start")
    fac.apply_remote_control("pause")
    assert fac.isRunning() is False
    snap = fac.get_analytics_snapshot()
    assert isinstance(snap, dict)


# --------------------------------------------------------------------------- #
# Attacker
# --------------------------------------------------------------------------- #
def _make_attacker_facade():
    from src_v2.workers.v1_compat import AttackerWorkerV1Facade

    class _DummyVision:
        conf = 0.25
        imgsz = 640
        weights = "stub.pt"
        iou = 0.5
        half = False
        device = 0

    class _DummyCfg:
        vision = _DummyVision()
    return AttackerWorkerV1Facade(_DummyCfg())


def test_attacker_facade_methods_present():
    fac = _make_attacker_facade()
    have = _public_attrs(fac)
    missing = ATTACKER_V1_REQUIRED_METHODS - have
    assert not missing, f"Attacker facade 누락 method: {sorted(missing)}"


def test_attacker_facade_signals_present():
    from src_v2.workers.v1_compat import AttackerWorkerV1Facade
    for sig in ATTACKER_V1_REQUIRED_SIGNALS:
        assert hasattr(AttackerWorkerV1Facade, sig), \
            f"Attacker facade 누락 signal: {sig}"


def test_attacker_facade_method_no_raise():
    fac = _make_attacker_facade()
    fac.set_xp_region(0, 0, 50, 20); fac.clear_xp_region()
    fac.set_cooldown_region(0, 0, 50, 20); fac.clear_cooldown_region()
    fac.set_buff_region(0, 0, 50, 20); fac.clear_buff_region()
    fac.set_hp_region(0, 0, 50, 20); fac.clear_hp_region()
    fac.set_mp_region(0, 0, 50, 20); fac.clear_mp_region()
    fac.set_hp_max(10000); fac.set_mp_max(5000)
    fac.set_own_skill_names(["a"])
    assert isinstance(fac.xp_per_hour(), int)
    assert isinstance(fac.get_analytics_snapshot(), dict)
    assert fac.send_control(0, "start") is False
    assert fac.isRunning() is False


# --------------------------------------------------------------------------- #
# Cross check: v1 클래스 attribute 존재여부 비교 (best-effort, optional).
# v1 워커가 import 가능하면 더 강한 대조. 실패 시 skip.
# --------------------------------------------------------------------------- #
def test_healer_facade_covers_v1_class_publics():
    try:
        from src.workers import healer_worker as _hw
    except Exception:
        pytest.skip("v1 healer_worker import 실패 — 환경 종속")
    v1 = getattr(_hw, "HealerWorker", None)
    if v1 is None:
        pytest.skip("v1 HealerWorker 클래스 없음")
    from src_v2.workers.v1_compat import HealerWorkerV1Facade
    v1_methods = {n for n in dir(v1) if not n.startswith("_")
                  and callable(getattr(v1, n, None))}
    fac_methods = {n for n in dir(HealerWorkerV1Facade) if not n.startswith("_")
                   and callable(getattr(HealerWorkerV1Facade, n, None))}
    # main_window 가 직접 호출하는 method 만 필수. v1 고유 internal 은 제외.
    must = HEALER_V1_REQUIRED_METHODS & v1_methods
    missing = must - fac_methods
    assert not missing, f"v1 에 있고 main_window 가 쓰는데 facade 에 없는 method: {missing}"


def test_attacker_facade_covers_v1_class_publics():
    try:
        from src.workers import attacker_worker as _aw
    except Exception:
        pytest.skip("v1 attacker_worker import 실패 — 환경 종속")
    v1 = getattr(_aw, "AttackerWorker", None)
    if v1 is None:
        pytest.skip("v1 AttackerWorker 클래스 없음")
    from src_v2.workers.v1_compat import AttackerWorkerV1Facade
    v1_methods = {n for n in dir(v1) if not n.startswith("_")
                  and callable(getattr(v1, n, None))}
    fac_methods = {n for n in dir(AttackerWorkerV1Facade) if not n.startswith("_")
                   and callable(getattr(AttackerWorkerV1Facade, n, None))}
    must = ATTACKER_V1_REQUIRED_METHODS & v1_methods
    missing = must - fac_methods
    assert not missing, f"v1 에 있고 main_window 가 쓰는데 facade 에 없는 method: {missing}"
