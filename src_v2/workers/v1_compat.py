"""V1-compatible facade — re-export wrapper (audit 8.1 3단계 분할 완료).

기존 1561줄 단일 파일 → sibling 모듈 8개로 분할.
외부 import path (`from src_v2.workers.v1_compat import ...`) 호환 유지.

분할 구조:
    _compat_logger.py            — setup_compat_logger (싱글톤 파일 로거)
    _compat_helpers.py           — cfg_to_flat_dict
    _compat_uplink.py            — UplinkSenderShim (격수 IP 동적 학습)
    _compat_cd_receiver.py       — UdpCdReceiver (힐러 CooldownReport 수신)
    _compat_f1_key.py            — Win32F1Key (F1 down 감지)
    _compat_healer_adapters.py   — build_healer_adapters
    _compat_attacker_adapters.py — build_attacker_adapters
    _compat_healer_facade.py     — HealerWorkerV1Facade
    _compat_attacker_facade.py   — AttackerWorkerV1Facade

이 파일 (v1_compat.py) 은 위 모듈들을 합쳐 v1 인터페이스를 단일 namespace 로
재노출. 신규 코드는 가능하면 sibling 모듈을 직접 import 권장.
"""
from __future__ import annotations

# Public re-export — 기존 import path 호환.
from ._compat_healer_facade import HealerWorkerV1Facade  # noqa: F401
from ._compat_attacker_facade import AttackerWorkerV1Facade  # noqa: F401

# Helper re-export (test / 외부 코드 의존성).
from ._compat_logger import setup_compat_logger as _setup_compat_logger  # noqa: F401
from ._compat_helpers import cfg_to_flat_dict as _cfg_to_flat_dict  # noqa: F401

__all__ = ["HealerWorkerV1Facade", "AttackerWorkerV1Facade"]
