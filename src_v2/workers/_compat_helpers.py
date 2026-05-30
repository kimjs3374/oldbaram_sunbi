"""v1_compat.py 에서 분리한 일반 helper.

audit 8.1 3단계 분할 진행 1차.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Dict


def cfg_to_flat_dict(cfg: Any) -> Dict[str, Any]:
    """nested cfg dataclass → flat dict. v1 → v2 migration helper.

    nested dict 한 단계만 flat 화 (sub_dict 의 키를 top-level 로 promote).
    """
    out: Dict[str, Any] = {}
    if not is_dataclass(cfg):
        return out
    for k, v in asdict(cfg).items():
        if isinstance(v, dict):
            for sk, sv in v.items():
                out[sk] = sv
        else:
            out[k] = v
    return out
