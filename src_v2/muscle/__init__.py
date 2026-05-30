"""src_v2.muscle — main loop (1-2ms target).

Design ref: §2.7
"""
from .main_loop import MainLoop, decide_direction

__all__ = ["MainLoop", "decide_direction"]
