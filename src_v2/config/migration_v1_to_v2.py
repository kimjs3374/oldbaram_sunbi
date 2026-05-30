"""Migrate v1 cfg dict (existing src/config.py format) to v2 nested format.

Lossless: every v1 key is either mapped or preserved under `extras`.

Design ref: §5.1
"""
from __future__ import annotations
from typing import Any, Dict


def migrate_v1_to_v2(v1: Dict[str, Any]) -> Dict[str, Any]:
    """Return v2 cfg dict. Unknown v1 keys preserved under 'legacy_extras'."""
    v1 = dict(v1 or {})
    used = set()

    def take(key, default=None):
        used.add(key)
        return v1.get(key, default)

    v2: Dict[str, Any] = {
        "muscle": {
            "main_loop_hz_cap": take("main_hz_cap", 200),
            "combat_band": take("combat_band", 2),
        },
        "eyes": {
            "capture_poll_sec": take("capture_poll_sec", 0.02),
            "yolo": {
                "poll_sec": take("yolo_poll_sec", 0.05),
                "every_n": take("yolo_every_n", 4),
                "conf_threshold": take("yolo_conf_threshold", 0.45),
            },
            "ocr": {
                "poll_sec": take("ocr_poll_sec", 0.05),
                "every_n": take("ocr_every_n", 1),
            },
            "cooldown": {
                "poll_sec": take("cooldown_poll_sec", 1.0),
            },
            "buff": {
                "poll_sec": take("buff_poll_sec", 1.0),
            },
            "hpmp": {
                "poll_sec": take("hpmp_poll_sec", 0.5),
            },
            "udp": {
                "poll_sec": take("udp_poll_sec", 0.02),
                "port": take("udp_port", 51900),
            },
        },
        "rules": {
            "self_heal": {
                "enabled": True,
                "thr_hp": take("self_heal_hp_thr", 50),
                "burst_count": take("self_heal_burst_count", 3),
                "burst_gap_ms": take("self_heal_burst_gap_ms", 80),
                "enable_block_b": take("self_heal_enable_block_b", True),
            },
            "self_revive": {"enabled": True},
            "attacker_revive": {"enabled": True},
            "gyoungryeok": {
                "enabled": take("gyoungryeok_enabled", True),
                "mp_thr": take("gyoungryeok_mp_thr", 30),
            },
            "baekho": {"enabled": take("baekho_enabled", True)},
            "parlyuk": {"enabled": take("parlyuk_enabled", True)},
            "parhon": {
                "enabled": take("parhon_enabled", True),
                "edge_sec": take("parhon_edge_sec", 3),
            },
            "seq_rclick": {
                "enabled": take("seq_rclick_enabled", True),
                "duration_ms": take("seq_rclick_duration_ms", 1500),
                "interval_ms": take("seq_rclick_interval_ms", 500),
            },
            "tab_lock": {"enabled": take("tab_lock_enabled", True)},
        },
        "hands": {
            "numlock": {
                "enabled": take("numlock_enabled", False),
                "interval_sec": take("numlock_interval_sec", 30.0),
            },
        },
        "ui": {
            "publish_hz": take("ui_publish_hz", 15),
        },
        "memory": {
            "action_log_capacity": take("action_log_capacity", 4096),
            "action_log_file": take("action_log_file", None),
        },
        "regions": {
            "game": take("game_region"),
            "cooldown": take("cooldown_region"),
            "buff": take("buff_region"),
            "chat": take("chat_region"),
            "hp": take("hp_region"),
            "mp": take("mp_region"),
            "coord": take("coord_region"),
            "map": take("map_region"),
            "xp": take("xp_region"),
        },
        "version": 2,
    }

    # preserve unknown keys for safety (lossless)
    extras = {k: v for k, v in v1.items() if k not in used}
    if extras:
        v2["legacy_extras"] = extras

    return v2
