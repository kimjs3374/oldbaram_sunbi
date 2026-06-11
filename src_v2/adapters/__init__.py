"""src_v2.adapters — production adapters that bridge src/* modules to v2 protocols.

These wrap the existing (frozen) src/ modules so v2 watchers/dispatchers can
use them without modifying src/.

Each adapter:
- Imports src/* modules lazily (inside __init__) to keep tests independent.
- Implements the matching v2 Protocol (GrabberAdapter, YoloAdapter, etc.).
- Translates return shapes to v2 expectations.

If src/* import fails (e.g. test env missing PyQt5/RapidOCR), is_available()
returns False and reads return safe defaults.

Two adapter flavors per module:
- Src*Adapter — wraps an already-constructed src/ object (mock-friendly)
- Real*Adapter — constructs the underlying src/ object internally
                 (production entry-point convenience)
"""

from .grabber_adapter import SrcGrabberAdapter, RealGrabberAdapter  # noqa: F401
from .yolo_adapter import SrcYoloAdapter, RealYoloAdapter  # noqa: F401
from .ocr_adapter import SrcOcrAdapter, RealOcrAdapter  # noqa: F401
from .cooldown_adapter import SrcCooldownAdapter, RealCooldownAdapter  # noqa: F401
from .hpmp_adapter import SrcHpMpAdapter, RealHpMpAdapter  # noqa: F401
from .udp_adapter import SrcUdpAdapter, RealUdpAdapter, RealUdpSenderAdapter  # noqa: F401
from .keys_adapter import SrcKeysAdapter, RealKeysAdapter  # noqa: F401
from .xp_adapter import SrcXpAdapter, RealXpAdapter  # noqa: F401
