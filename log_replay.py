"""Importable entry point for the hyphenated ``log-replay.py`` script."""

import importlib.util
from pathlib import Path


_SCRIPT = Path(__file__).with_name("log-replay.py")
_SPEC = importlib.util.spec_from_file_location("log_replay_cli", _SCRIPT)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"Unable to load {_SCRIPT}")
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
main = _MODULE.main


if __name__ == "__main__":
    main()
