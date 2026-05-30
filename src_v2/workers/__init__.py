"""src_v2.workers — process entry points.

healer_worker_v2: replaces src/workers/healer_worker.py
attacker_worker_v2: replaces src/workers/attacker_worker.py
"""
from .healer_worker_v2 import HealerWorkerV2, HealerConfig
from .attacker_worker_v2 import AttackerWorkerV2
from .v1_compat import HealerWorkerV1Facade, AttackerWorkerV1Facade

__all__ = [
    "HealerWorkerV2", "HealerConfig", "AttackerWorkerV2",
    "HealerWorkerV1Facade", "AttackerWorkerV1Facade",
]
