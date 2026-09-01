"""Security regression tests.

Ensures dangerous patterns (e.g. Flask debug=True enabling the Werkzeug
debugger console — an unauthenticated RCE vector) do not reappear in
web_ui.py.

These tests read the source file as text so they run without Flask
installed (CI installs only the ``dev`` extra, not ``web``).
"""
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_web_ui_does_not_enable_flask_debug():
    """web_ui.py must not start Flask with debug=True.

    debug=True enables the Werkzeug interactive debugger console, which
    allows unauthenticated arbitrary Python code execution. Even on
    localhost this is a risk (CSRF, local process access), and with
    HOST=0.0.0.0 it becomes remote RCE.
    """
    src = (ROOT / "web_ui.py").read_text(encoding="utf-8")
    # The app.run(...) call must not pass debug=True.
    assert "debug=True" not in src, (
        "web_ui.py must not use debug=True — the Werkzeug debugger "
        "console allows unauthenticated RCE"
    )


def test_web_ui_app_run_uses_debug_false():
    """The app.run() call in web_ui.py should explicitly set debug=False."""
    src = (ROOT / "web_ui.py").read_text(encoding="utf-8")
    assert "debug=False" in src, (
        "web_ui.py app.run() should explicitly set debug=False"
    )
