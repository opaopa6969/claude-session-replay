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


def test_convert_does_not_write_request_selected_output_path():
    """The web conversion endpoint must not expose server-side file writes."""
    src = (ROOT / "web_ui.py").read_text(encoding="utf-8")
    convert_src = src.split("@app.route('/api/convert'", 1)[1].split(
        "LOG2MODEL_SCRIPTS", 1
    )[0]
    assert "write_text(output" not in convert_src
    assert "output_file.parent.mkdir" not in convert_src
    assert "The output parameter is not supported" in convert_src


def test_web_ui_has_no_server_output_path_control():
    """The UI must use its browser-side download controls."""
    template = (ROOT / "templates/index.html").read_text(encoding="utf-8")
    assert "id=\"outputInput\"" not in template
    assert "id=\"browseBtn\"" not in template
