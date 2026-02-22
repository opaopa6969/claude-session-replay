#!/usr/bin/env python3
"""TUI application for Claude Session Replay."""

import os
import sys
import tempfile
import json
import subprocess
import importlib.util
from pathlib import Path
from datetime import datetime

from textual.app import ComposeResult, App
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.widgets import Static, Button, RadioSet, RadioButton, ListItem, ListView, Input, Label, ProgressBar
from textual.binding import Binding
from textual.message import Message
from rich.text import Text
from rich.console import Console

# Import from claude-log2model and codex-log2model (with hyphens)
def _import_module(name, filepath):
    spec = importlib.util.spec_from_file_location(name, filepath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

script_dir = Path(__file__).parent
claude_log2model = _import_module("claude_log2model", str(script_dir / "claude-log2model.py"))
codex_log2model = _import_module("codex_log2model", str(script_dir / "codex-log2model.py"))


def _format_size(size_bytes):
    """Format bytes to human-readable size."""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}".replace(".0", "")
        size_bytes /= 1024
    return f"{size_bytes:.1f}TB".replace(".0", "")


class SessionListItem(ListItem):
    """Custom list item for sessions."""

    def __init__(self, session_data, preview_data):
        self.session_data = session_data
        self.preview_data = preview_data

        mtime = session_data.get("mtime", 0)
        dt = datetime.fromtimestamp(mtime)
        date_str = dt.strftime("%Y-%m-%d %H:%M")

        project = session_data.get("project", "")[:14]
        size_str = _format_size(session_data.get("size", 0))

        first_msg = preview_data.get("first_message", "")[:40].replace("\n", " ")

        label = f"{date_str}  {project:14}  {size_str:>6}  {first_msg}"
        super().__init__(Label(label))


class PreviewPanel(Static):
    """Right panel showing message preview."""

    DEFAULT_CSS = """
    PreviewPanel {
        border: solid $primary;
        height: 100%;
        overflow-y: auto;
    }
    """

    def update_preview(self, messages):
        """Update preview display with messages."""
        content = ""
        for msg in messages:
            role = msg["role"].upper()
            text = msg["text"][:100].replace("\n", " ")
            if len(msg["text"]) > 100:
                text += "..."
            content += f"[{role}] {text}\n\n"

        self.update(content or "No preview available")


class LogReplayApp(App):
    """TUI application for Claude session replay."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #title {
        dock: top;
        height: 1;
        background: $primary;
        color: $text;
        content-align: left middle;
    }

    #agent-section {
        height: 3;
        border: solid $accent;
        background: $boost;
    }

    #content {
        height: 1fr;
        layout: horizontal;
    }

    #sessions-panel {
        width: 40%;
        border: solid $primary;
    }

    #preview-panel {
        width: 1fr;
    }

    #options-section {
        height: auto;
        border: solid $accent;
        background: $boost;
        max-height: 6;
    }

    #buttons-section {
        height: 3;
        border: solid $accent;
        layout: horizontal;
        align: center middle;
    }

    Button {
        margin: 0 2;
    }

    RadioButton {
        margin: 0 1;
    }

    Input {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self.current_agent = "claude"
        self.sessions = []
        self.selected_session = None
        self.selected_format = "md"
        self.selected_theme = "light"
        self.range_filter = ""
        self.output_path = ""

    def compose(self) -> ComposeResult:
        """Compose the UI."""
        yield Label("Claude Session Replay", id="title")

        with Vertical(id="agent-section"):
            yield Label("Agent:")
            with Horizontal():
                yield RadioSet(
                    RadioButton("claude", value=True, id="agent-claude"),
                    RadioButton("codex", id="agent-codex"),
                    id="agent-radio"
                )

        with Horizontal(id="content"):
            with Vertical(id="sessions-panel"):
                yield Label("Sessions:")
                yield ListView(id="sessions-list")

            yield PreviewPanel(id="preview-panel")

        with Vertical(id="options-section"):
            yield Label("Options:")
            with Horizontal():
                yield Label("Format:")
                yield RadioSet(
                    RadioButton("md", value=True, id="fmt-md"),
                    RadioButton("html", id="fmt-html"),
                    RadioButton("player", id="fmt-player"),
                    RadioButton("terminal", id="fmt-terminal"),
                    id="format-radio"
                )
                yield Label("Theme:")
                yield RadioSet(
                    RadioButton("light", value=True, id="theme-light"),
                    RadioButton("console", id="theme-console"),
                    id="theme-radio"
                )
            with Horizontal():
                yield Label("Range:")
                yield Input(id="range-input", placeholder="e.g., 1-50,53-")
                yield Label("Output:")
                yield Input(id="output-input", placeholder="File path (empty = stdout)")

        with Horizontal(id="buttons-section"):
            yield Button("Run", id="run-btn", variant="primary")
            yield Button("Quit", id="quit-btn")

    def on_mount(self):
        """Initialize after UI is mounted."""
        self.load_sessions()
        self._refresh_preview()

    def load_sessions(self):
        """Load sessions for current agent."""
        list_widget = self.query_one("#sessions-list", ListView)
        list_widget.clear()

        if self.current_agent == "claude":
            self.sessions = claude_log2model.discover_sessions()
            for session in self.sessions:
                preview = claude_log2model._extract_preview(session["path"])
                total = preview.get("user_count", 0) + preview.get("assistant_count", 0)
                if total > 0:
                    item = SessionListItem(session, preview)
                    list_widget.append(item)
        else:
            self.sessions = codex_log2model.discover_sessions()
            for session in self.sessions:
                use_event_msgs = codex_log2model._codex_has_event_messages(session["path"])
                preview = codex_log2model._extract_preview(session["path"], use_event_msgs)
                total = preview.get("user_count", 0) + preview.get("assistant_count", 0)
                if total > 0:
                    item = SessionListItem(session, preview)
                    list_widget.append(item)

    def on_radio_set_changed(self, message):
        """Handle radio button changes."""
        radio_set = message.control
        selected_id = None
        for btn in radio_set.query("RadioButton"):
            if btn.value:
                selected_id = btn.id
                break

        if radio_set.id == "agent-radio":
            agent = "claude" if selected_id == "agent-claude" else "codex"
            if agent != self.current_agent:
                self.current_agent = agent
                self.load_sessions()
                self._refresh_preview()
        elif radio_set.id == "format-radio":
            format_map = {
                "fmt-md": "md",
                "fmt-html": "html",
                "fmt-player": "player",
                "fmt-terminal": "terminal"
            }
            self.selected_format = format_map.get(selected_id, "md")
        elif radio_set.id == "theme-radio":
            theme = "light" if selected_id == "theme-light" else "console"
            self.selected_theme = theme

    def on_list_view_selected(self, message):
        """Handle session selection."""
        list_widget = self.query_one("#sessions-list", ListView)
        if message.list_view == list_widget:
            if message.item is not None:
                # Find corresponding session
                index = list(list_widget.children).index(message.item)
                if 0 <= index < len(self.sessions):
                    self.selected_session = self.sessions[index]
                    self._refresh_preview()

    def _refresh_preview(self):
        """Update preview panel with current selection."""
        preview_panel = self.query_one("#preview-panel", PreviewPanel)

        if self.selected_session:
            if self.current_agent == "claude":
                messages = claude_log2model._extract_preview_messages(
                    self.selected_session["path"], count=3
                )
            else:
                messages = codex_log2model._extract_preview_messages(
                    self.selected_session["path"], count=3
                )
            preview_panel.update_preview(messages)
        else:
            preview_panel.update("Select a session to preview")

    def on_input_changed(self, message):
        """Handle input field changes."""
        if message.input.id == "range-input":
            self.range_filter = message.value
        elif message.input.id == "output-input":
            self.output_path = message.value

    def on_button_pressed(self, message):
        """Handle button presses."""
        if message.button.id == "run-btn":
            self.run_replay()
        elif message.button.id == "quit-btn":
            self.exit()

    def run_replay(self):
        """Execute the replay pipeline."""
        if not self.selected_session:
            self.notify("Please select a session first", timeout=3)
            return

        try:
            # Create temporary model file
            fd, model_path = tempfile.mkstemp(prefix="log-model-", suffix=".json")
            os.close(fd)

            input_path = self.selected_session["path"]

            # Determine log2model script
            if self.current_agent == "claude":
                log2model = "claude-log2model.py"
            else:
                log2model = "codex-log2model.py"

            # Step 1: Convert to model
            log_cmd = [sys.executable, log2model, input_path, "-o", model_path]
            result = subprocess.run(log_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                self.notify(f"Error in log2model: {result.stderr}", timeout=5)
                return

            # Step 2: Render
            render_cmd = [
                sys.executable, "log-model-renderer.py", model_path,
                "-f", self.selected_format,
                "-t", self.selected_theme
            ]

            if self.range_filter:
                render_cmd.extend(["-r", self.range_filter])

            if self.output_path:
                render_cmd.extend(["-o", self.output_path])

            result = subprocess.run(render_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                self.notify(f"Error in renderer: {result.stderr}", timeout=5)
                return

            # Success message
            if self.output_path:
                self.notify(f"✓ Output saved to {self.output_path}", timeout=3)
            else:
                self.notify("✓ Conversion complete (output to stdout)", timeout=3)

        except Exception as e:
            self.notify(f"Error: {str(e)}", timeout=5)
        finally:
            # Cleanup
            try:
                os.remove(model_path)
            except OSError:
                pass


def main():
    """Entry point for TUI."""
    app = LogReplayApp()
    app.run()


if __name__ == "__main__":
    main()
