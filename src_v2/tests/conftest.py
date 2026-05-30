"""pytest config — make src_v2 importable from D:\\oldbaram root."""
import sys
import os
import pytest

# Ensure project root in sys.path so `import src_v2` works
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(autouse=True)
def _reset_registry():
    """Each test gets a fresh PluginRegistry."""
    from src_v2.core.plugin_registry import PluginRegistry
    PluginRegistry.reset()
    yield
    PluginRegistry.reset()
