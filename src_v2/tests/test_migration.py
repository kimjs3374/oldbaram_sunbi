"""Phase 9 — Migration unit tests."""
import json
import pytest

from src_v2.config.migration_v1_to_v2 import migrate_v1_to_v2
from src_v2.config.loader import load_v2_config, save_v2_config


def test_migrate_basic_keys():
    v1 = {
        "self_heal_hp_thr": 40,
        "main_hz_cap": 250,
        "yolo_every_n": 5,
        "udp_port": 51901,
    }
    v2 = migrate_v1_to_v2(v1)
    assert v2["rules"]["self_heal"]["thr_hp"] == 40
    assert v2["muscle"]["main_loop_hz_cap"] == 250
    assert v2["eyes"]["yolo"]["every_n"] == 5
    assert v2["eyes"]["udp"]["port"] == 51901
    assert v2["version"] == 2


def test_migrate_defaults_for_missing():
    v2 = migrate_v1_to_v2({})
    assert v2["rules"]["self_heal"]["thr_hp"] == 50
    assert v2["muscle"]["main_loop_hz_cap"] == 200
    assert v2["eyes"]["yolo"]["conf_threshold"] == 0.45


def test_migrate_lossless_extras():
    v1 = {"unknown_legacy_key": 42, "another_one": "abc"}
    v2 = migrate_v1_to_v2(v1)
    assert v2["legacy_extras"]["unknown_legacy_key"] == 42
    assert v2["legacy_extras"]["another_one"] == "abc"


def test_migrate_regions_preserved():
    v1 = {"game_region": [10, 20, 800, 600], "hp_region": [0, 0, 100, 10]}
    v2 = migrate_v1_to_v2(v1)
    assert v2["regions"]["game"] == [10, 20, 800, 600]
    assert v2["regions"]["hp"] == [0, 0, 100, 10]


def test_save_load_roundtrip(tmp_path):
    fp = str(tmp_path / "cfg_v2.json")
    data = {"a": 1, "b": [1, 2, 3], "c": {"k": "v"}}
    save_v2_config(fp, data)
    loaded = load_v2_config(fp)
    assert loaded == data


def test_load_missing_returns_empty(tmp_path):
    fp = str(tmp_path / "nope.json")
    assert load_v2_config(fp) == {}


def test_migrate_full_real_like():
    """Simulate a realistic v1 cfg blob."""
    v1 = {
        "self_heal_hp_thr": 45,
        "self_heal_burst_count": 4,
        "gyoungryeok_mp_thr": 25,
        "parhon_edge_sec": 2,
        "yolo_every_n": 3,
        "udp_port": 51900,
        "game_region": [0, 0, 1280, 720],
        "numlock_enabled": True,
        "ui_publish_hz": 20,
        "extra_thing": True,
    }
    v2 = migrate_v1_to_v2(v1)
    assert v2["rules"]["self_heal"]["thr_hp"] == 45
    assert v2["rules"]["self_heal"]["burst_count"] == 4
    assert v2["rules"]["gyoungryeok"]["mp_thr"] == 25
    assert v2["rules"]["parhon"]["edge_sec"] == 2
    assert v2["hands"]["numlock"]["enabled"] is True
    assert v2["ui"]["publish_hz"] == 20
    assert v2["legacy_extras"]["extra_thing"] is True
