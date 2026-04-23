import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def load_module(name: str, filename: str):
    """Load a module from a hyphenated filename."""
    path = ROOT / filename
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
