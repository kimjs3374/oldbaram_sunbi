"""Rule plugins — registered via @rule decorator on import.

Auto-registers (priority order):
  1. self_revive   — HP=0 cross-down edge
  2. attacker_revive — atk_hp=0 + self_hp>0 edge
  3. self_heal     — HP < thr cross-down (EDGE-DEFER)
  20. gyoungryeok  — MP < thr cross-down + allow_hp_drop_for(5s)
  30. baekho       — cd_baekho==0 ready edge
  30. parlyuk      — cd_parlyuk<=offset ready edge + buff coord_tol=1
  40. parhon       — atk debuff_honmasul edge
  50. mujang       — atk buff_mujang_sec=0 edge
  51. boho         — atk buff_boho_sec=0 edge
  + seq_rclick (sub-loop hook), tab_lock (TAB-CONFIRM Route A).
"""
from . import self_heal  # noqa: F401
from . import self_revive  # noqa: F401
from . import attacker_revive  # noqa: F401
from . import parhon  # noqa: F401
from . import baekho  # noqa: F401
from . import parlyuk  # noqa: F401
from . import gyoungryeok  # noqa: F401
from . import mujang  # noqa: F401
from . import boho  # noqa: F401
from . import geumgang  # noqa: F401
from . import seq_rclick  # noqa: F401
from . import tab_lock  # noqa: F401
