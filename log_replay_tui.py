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
from textual.widgets import Static, Button, ListItem, ListView, Input, Label
from textual.binding import Binding
from textual.message import Message
from textual.screen import Screen
from rich.text import Text

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


class ClickableButton(Static):
    """Clickable button displaying [ text ] style."""

    DEFAULT_CSS = """
    ClickableButton {
        width: auto;
        height: 1;
        padding: 0 1;
        border: solid $accent;
        content-align: center middle;
    }

    ClickableButton.active {
        background: $primary;
        border: solid $secondary;
        color: $text;
    }
    """

    def __init__(self, text: str, button_id: str = "", is_active: bool = False, **kwargs):
        self._button_text = f"[ {text} ]"
        self._button_id = button_id
        self._is_active = is_active
        super().__init__(self._button_text, **kwargs)
        self.button_id = button_id
        self.update_active_state(is_active)

    def render(self) -> Text:
        """Render the button text."""
        style = "bold white" if self._is_active else "white"
        return Text(self._button_text, style=style)

    def update_active_state(self, is_active: bool):
        """Update button active state."""
        self._is_active = is_active
        if is_active:
            self.add_class("active")
        else:
            self.remove_class("active")
        self.refresh()

    class ClickableButtonPressed(Message):
        """Posted when a ClickableButton is clicked."""
        def __init__(self, button: "ClickableButton") -> None:
            self.button = button
            super().__init__()

    def on_click(self) -> None:
        """Handle click events."""
        self.post_message(self.ClickableButtonPressed(self))


class FilePickerScreen(Screen):
    """Simple file picker screen."""

    CSS = """
    FilePickerScreen {
        align: center middle;
    }

    #file-picker-dialog {
        width: 70;
        height: 20;
        border: solid $primary;
        background: $boost;
    }

    #file-picker-title {
        dock: top;
        height: 1;
        content-align: center middle;
    }

    #file-picker-content {
        height: 1fr;
    }

    #file-picker-path {
        height: 1;
        border-bottom: solid $accent;
    }

    #file-picker-list {
        height: 1fr;
        border-bottom: solid $accent;
    }

    #file-picker-buttons {
        height: 3;
        layout: horizontal;
        align: center middle;
    }

    #file-picker-buttons ClickableButton {
        margin: 0 2;
    }
    """

    def __init__(self, initial_path: str = "."):
        super().__init__()
        self.current_path = Path(initial_path).expanduser().resolve()
        self.selected_file = None

    class FileSelected(Message):
        """Posted when a file is selected."""
        def __init__(self, path: str) -> None:
            self.path = path
            super().__init__()

    def compose(self) -> ComposeResult:
        """Compose the file picker."""
        with Vertical(id="file-picker-dialog"):
            yield Label("Select Output File", id="file-picker-title")
            with Vertical(id="file-picker-content"):
                yield Label(f"📁 {self.current_path}", id="file-picker-path")
                yield ListView(id="file-picker-list")
            with Horizontal(id="file-picker-buttons"):
                yield ClickableButton("OK", button_id="file-ok", is_active=True)
                yield ClickableButton("Cancel", button_id="file-cancel")

    def on_mount(self) -> None:
        """Load file list on mount."""
        self._refresh_file_list()

    def _refresh_file_list(self) -> None:
        """Refresh the file list view."""
        list_view = self.query_one("#file-picker-list", ListView)
        list_view.clear()

        try:
            items = sorted(self.current_path.iterdir())
            for item in items:
                name = item.name
                if item.is_dir():
                    name = f"📁 {name}/"
                else:
                    name = f"📄 {name}"
                list_item = ListItem(Label(name))
                list_item.metadata = {"path": item, "is_dir": item.is_dir()}
                list_view.append(list_item)
        except PermissionError:
            pass

    def on_list_view_selected(self, message: ListView.Selected) -> None:
        """Handle item selection."""
        if message.item and message.item.metadata:
            metadata = message.item.metadata
            path = metadata.get("path")
            is_dir = metadata.get("is_dir", False)

            if is_dir:
                self.current_path = path
                self.query_one("#file-picker-path", Label).update(f"📁 {self.current_path}")
                self._refresh_file_list()
            else:
                self.selected_file = str(path)

    def on_clickable_label_clickable_button_pressed(self, message: ClickableButton.ClickableButtonPressed) -> None:
        """Handle file picker buttons."""
        btn_id = message.button.button_id
        if btn_id == "file-ok":
            if self.selected_file:
                self.post_message(self.FileSelected(self.selected_file))
                self.app.pop_screen()
        elif btn_id == "file-cancel":
            self.app.pop_screen()


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

    #agent-buttons {
        height: 3;
        border: solid $accent;
        background: $boost;
        layout: horizontal;
        align: center middle;
    }

    #agent-buttons ClickableButton {
        margin: 0 1;
        width: 1fr;
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
        border: solid $primary;
        overflow-y: auto;
    }

    #options-section {
        height: auto;
        border: solid $accent;
        background: $boost;
        max-height: 8;
    }

    #buttons-section {
        height: 3;
        border: solid $accent;
        layout: horizontal;
        align: center middle;
    }

    #buttons-section ClickableButton {
        margin: 0 1;
    }

    Input {
        margin: 0 1;
        height: 1;
        width: 1fr;
    }

    Input > .input--cursor-line {
        color: $text;
        background: $surface;
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

        with Horizontal(id="agent-buttons"):
            yield ClickableButton("Claude", button_id="btn-claude", is_active=True, id="btn-claude")
            yield ClickableButton("Codex", button_id="btn-codex", is_active=False, id="btn-codex")

        with Horizontal(id="content"):
            with Vertical(id="sessions-panel"):
                yield Label("Sessions:")
                yield ListView(id="sessions-list")

            with Static(id="preview-panel"):
                pass

        with Vertical(id="options-section"):
            yield Label("Options:")
            with Horizontal():
                yield Label("Format:")
                with Horizontal():
                    yield ClickableButton("md", button_id="fmt-md", is_active=True, id="fmt-md")
                    yield ClickableButton("html", button_id="fmt-html", is_active=False, id="fmt-html")
                    yield ClickableButton("player", button_id="fmt-player", is_active=False, id="fmt-player")
                    yield ClickableButton("terminal", button_id="fmt-terminal", is_active=False, id="fmt-terminal")
            with Horizontal():
                yield Label("Theme:")
                with Horizontal():
                    yield ClickableButton("light", button_id="theme-light", is_active=True, id="theme-light")
                    yield ClickableButton("console", button_id="theme-console", is_active=False, id="theme-console")
            with Horizontal():
                yield Label("Range:")
                yield Static(id="range-display")
            with Horizontal():
                yield Label("Output:")
                yield Static(id="output-display")
                yield ClickableButton("Browse", button_id="browse-btn", is_active=False)

        with Horizontal(id="buttons-section"):
            yield ClickableButton("Run", button_id="run-btn", is_active=True, id="run-btn")
            yield ClickableButton("Quit", button_id="quit-btn", is_active=False, id="quit-btn")

    def on_mount(self):
        """Initialize after UI is mounted."""
        self.load_sessions()
        self._refresh_preview()
        self._update_range_display()
        self._update_output_display()

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

    def _refresh_preview(self):
        """Update preview panel with current selection."""
        preview_panel = self.query_one("#preview-panel", Static)

        if self.selected_session:
            if self.current_agent == "claude":
                messages = claude_log2model._extract_preview_messages(
                    self.selected_session["path"], count=10
                )
            else:
                messages = codex_log2model._extract_preview_messages(
                    self.selected_session["path"], count=10
                )
            content = ""
            for msg in messages:
                role = msg["role"].upper()
                text = msg["text"][:200].replace("\n", " ")
                if len(msg["text"]) > 200:
                    text += "..."
                content += f"[{role}] {text}\n\n"
            preview_panel.update(content or "No preview available")
        else:
            preview_panel.update("Select a session to preview")

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

    def _update_range_display(self):
        """Update range display."""
        display_text = f"[ {self.range_filter or '(none)'} ]"
        self.query_one("#range-display", Static).update(display_text)

    def _update_output_display(self):
        """Update output display."""
        display_text = f"[ {self.output_path or '(stdout)'} ]"
        self.query_one("#output-display", Static).update(display_text)

    def on_clickable_label_clickable_button_pressed(self, message: ClickableButton.ClickableButtonPressed) -> None:
        """Handle clickable label clicks."""
        btn_id = message.button.button_id

        # Agent selection
        if btn_id == "btn-claude":
            if self.current_agent != "claude":
                self.current_agent = "claude"
                self._update_agent_buttons()
                self.load_sessions()
                self._refresh_preview()
        elif btn_id == "btn-codex":
            if self.current_agent != "codex":
                self.current_agent = "codex"
                self._update_agent_buttons()
                self.load_sessions()
                self._refresh_preview()

        # Format selection
        elif btn_id == "fmt-md":
            self.selected_format = "md"
            self._update_format_buttons()
        elif btn_id == "fmt-html":
            self.selected_format = "html"
            self._update_format_buttons()
        elif btn_id == "fmt-player":
            self.selected_format = "player"
            self._update_format_buttons()
        elif btn_id == "fmt-terminal":
            self.selected_format = "terminal"
            self._update_format_buttons()

        # Theme selection
        elif btn_id == "theme-light":
            self.selected_theme = "light"
            self._update_theme_buttons()
        elif btn_id == "theme-console":
            self.selected_theme = "console"
            self._update_theme_buttons()

        # Actions
        elif btn_id == "browse-btn":
            self.open_file_picker()
        elif btn_id == "run-btn":
            self.run_replay()
        elif btn_id == "quit-btn":
            self.exit()

    def _update_agent_buttons(self):
        """Update agent button active state."""
        for btn_id, agent in [("btn-claude", "claude"), ("btn-codex", "codex")]:
            btn = self.query_one(f"#{btn_id}", ClickableButton)
            btn.update_active_state(self.current_agent == agent)

    def _update_format_buttons(self):
        """Update format button active state."""
        format_map = {
            "fmt-md": "md",
            "fmt-html": "html",
            "fmt-player": "player",
            "fmt-terminal": "terminal"
        }
        for btn_id, fmt in format_map.items():
            btn = self.query_one(f"#{btn_id}", ClickableButton)
            btn.update_active_state(self.selected_format == fmt)

    def _update_theme_buttons(self):
        """Update theme button active state."""
        for btn_id, theme in [("theme-light", "light"), ("theme-console", "console")]:
            btn = self.query_one(f"#{btn_id}", ClickableButton)
            btn.update_active_state(self.selected_theme == theme)

    def open_file_picker(self):
        """Open file picker screen."""
        def on_file_selected(message: FilePickerScreen.FileSelected) -> None:
            self.output_path = message.path
            self._update_output_display()

        picker = FilePickerScreen(initial_path=self.output_path or str(Path.home()))
        picker.on_message(FilePickerScreen.FileSelected, on_file_selected)
        self.push_screen(picker)

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
