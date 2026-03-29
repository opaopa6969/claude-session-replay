#!/usr/bin/env python3
"""TUI application for Claude session replay."""

import importlib.util
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    OptionList,
    Static,
)
from textual.widgets.option_list import Option

# ---------------------------------------------------------------------------
# Module import helpers
# ---------------------------------------------------------------------------

script_dir = Path(__file__).parent


def _import_module(name, filepath):
    spec = importlib.util.spec_from_file_location(name, str(filepath))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


claude_log2model = _import_module("claude_log2model", script_dir / "claude-log2model.py")
codex_log2model = _import_module("codex_log2model", script_dir / "codex-log2model.py")
gemini_log2model = _import_module("gemini_log2model", script_dir / "gemini-log2model.py")
aider_log2model = _import_module("aider_log2model", script_dir / "aider-log2model.py")
cursor_log2model = _import_module("cursor_log2model", script_dir / "cursor-log2model.py")

ADAPTERS = {
    "claude": claude_log2model,
    "codex": codex_log2model,
    "gemini": gemini_log2model,
    "aider": aider_log2model,
    "cursor": cursor_log2model,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_size(size_bytes):
    """Format bytes to human-readable size."""
    if size_bytes >= 1024 * 1024:
        return "{:.1f}M".format(size_bytes / (1024 * 1024))
    if size_bytes >= 1024:
        return "{:.0f}K".format(size_bytes / 1024)
    return "{}B".format(size_bytes)


def _format_date(mtime):
    try:
        return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError):
        return ""


def _get_preview_messages(session, agent):
    """Get preview messages using adapter's _extract_preview_messages if available."""
    path = session["path"]
    adapter = ADAPTERS.get(agent)
    if adapter and hasattr(adapter, "_extract_preview_messages"):
        if agent == "codex":
            return adapter._extract_preview_messages(path, count=5)
        return adapter._extract_preview_messages(path, count=5)
    # Fallback for gemini: parse first few messages from JSON
    if agent == "gemini":
        import json
        messages = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for msg in data.get("messages", []):
                    m_type = msg.get("type", "")
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        text = " ".join(
                            b.get("text", "") for b in content
                            if isinstance(b, dict) and b.get("type") == "text"
                        )
                    elif isinstance(content, str):
                        text = content
                    else:
                        text = str(content)
                    if text.strip():
                        role = "user" if m_type == "user" else "assistant"
                        messages.append({"role": role, "text": text.strip()})
                    if len(messages) >= 5:
                        break
        except Exception:
            pass
        return messages
    return []


def _get_session_preview(session, agent):
    """Get preview metadata for a session."""
    adapter = ADAPTERS.get(agent)
    if not adapter:
        return {}
    path = session["path"]
    if agent == "codex":
        use_event = adapter._codex_has_event_messages(path)
        return adapter._extract_preview(path, use_event)
    return adapter._extract_preview(path)


# ---------------------------------------------------------------------------
# Session data structure for display
# ---------------------------------------------------------------------------

class SessionInfo:
    """Holds session data for display in the list."""

    def __init__(self, session_dict, agent, preview):
        self.session = session_dict
        self.agent = agent
        self.path = session_dict["path"]
        self.project = session_dict.get("project", session_dict.get("folder", ""))
        self.size = session_dict.get("size", 0)
        self.mtime = session_dict.get("mtime", 0)
        self.date = _format_date(self.mtime)
        self.preview = preview
        self.user_count = preview.get("user_count", 0)
        self.assistant_count = preview.get("assistant_count", 0)
        self.total_msgs = self.user_count + self.assistant_count
        self.first_message = preview.get("first_message", "")

    @property
    def display_line(self):
        project = self.project
        if len(project) > 16:
            project = project[:14] + ".."
        first = self.first_message.replace("\n", " ")
        if len(first) > 50:
            first = first[:48] + ".."
        return "{agent:7s}  {date:16s}  {proj:16s}  {size:>6s}  {msgs:>4d}  {first}".format(
            agent=self.agent,
            date=self.date,
            proj=project,
            size=_format_size(self.size),
            msgs=self.total_msgs,
            first=first,
        )


# ---------------------------------------------------------------------------
# File Picker Screen
# ---------------------------------------------------------------------------

class FilePickerScreen(ModalScreen):
    """Simple file picker for output file selection."""

    CSS = """
    FilePickerScreen {
        align: center middle;
    }
    #fp-dialog {
        width: 72;
        height: 22;
        border: solid $primary;
        background: $boost;
        padding: 1;
    }
    #fp-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    #fp-path-input {
        margin-bottom: 1;
    }
    #fp-buttons {
        height: 3;
        align: center middle;
    }
    #fp-buttons Button {
        margin: 0 2;
    }
    """

    class FileSelected(Message):
        def __init__(self, path):
            super().__init__()
            self.path = path

    def __init__(self, initial_path=""):
        super().__init__()
        self.initial_path = initial_path

    def compose(self):
        with Vertical(id="fp-dialog"):
            yield Label("Select Output File", id="fp-title")
            yield Input(value=self.initial_path, placeholder="/path/to/output.html", id="fp-path-input")
            with Horizontal(id="fp-buttons"):
                yield Button("OK", variant="primary", id="fp-ok")
                yield Button("Cancel", id="fp-cancel")

    @on(Button.Pressed, "#fp-ok")
    def on_ok(self):
        inp = self.query_one("#fp-path-input", Input)
        self.dismiss(inp.value)

    @on(Button.Pressed, "#fp-cancel")
    def on_cancel(self):
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Main Application
# ---------------------------------------------------------------------------

class LogReplayApp(App):
    """TUI application for Claude Session Replay."""

    TITLE = "Claude Session Replay"

    CSS = """
    Screen {
        layout: vertical;
    }

    #main-content {
        height: 1fr;
        layout: horizontal;
    }

    /* --- Left panel: sessions --- */
    #left-panel {
        width: 1fr;
        min-width: 40;
        border-right: solid $accent;
    }

    #agent-bar {
        height: 3;
        layout: horizontal;
        align: center middle;
        background: $boost;
        border-bottom: solid $accent;
    }
    #agent-bar Button {
        margin: 0 1;
    }

    #search-bar {
        height: 3;
        border-bottom: solid $accent;
    }

    #sessions-list {
        height: 1fr;
    }

    #session-count {
        height: 1;
        background: $boost;
        padding: 0 1;
    }

    /* --- Right panel: preview + options --- */
    #right-panel {
        width: 1fr;
        min-width: 40;
    }

    #preview-panel {
        height: 1fr;
        border-bottom: solid $accent;
        padding: 1;
    }

    #options-panel {
        height: auto;
        max-height: 12;
        padding: 1;
    }

    #format-bar {
        height: 3;
        layout: horizontal;
        align: left middle;
    }
    #format-bar Button {
        margin: 0 1;
    }

    #theme-bar {
        height: 3;
        layout: horizontal;
        align: left middle;
    }
    #theme-bar Button {
        margin: 0 1;
    }

    #output-bar {
        height: 3;
        layout: horizontal;
        align: left middle;
    }
    #output-bar Button {
        margin: 0 1;
    }

    #action-bar {
        height: 3;
        layout: horizontal;
        align: center middle;
        background: $boost;
        border-top: solid $accent;
        dock: bottom;
    }
    #action-bar Button {
        margin: 0 2;
    }

    .panel-title {
        text-style: bold;
        margin-bottom: 1;
    }

    .active-btn {
        background: $primary;
        color: $text;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("1", "agent('claude')", "Claude", show=False),
        Binding("2", "agent('codex')", "Codex", show=False),
        Binding("3", "agent('gemini')", "Gemini", show=False),
        Binding("r", "run_replay", "Replay"),
        Binding("slash", "focus_search", "Search", show=False),
        Binding("f1", "set_format('md')", "MD", show=False),
        Binding("f2", "set_format('html')", "HTML", show=False),
        Binding("f3", "set_format('player')", "Player", show=False),
        Binding("f4", "set_format('terminal')", "Terminal", show=False),
    ]

    def __init__(self):
        super().__init__()
        self.current_agent = "claude"
        self.current_format = "player"
        self.selected_theme = "light"
        self.output_path = ""
        self.all_sessions = []  # type: list[SessionInfo]
        self.filtered_sessions = []  # type: list[SessionInfo]
        self.selected_index = -1

    # ---- Compose ----

    def compose(self):
        yield Header()
        with Horizontal(id="main-content"):
            with Vertical(id="left-panel"):
                with Horizontal(id="agent-bar"):
                    yield Button("Claude [1]", id="btn-claude", variant="primary")
                    yield Button("Codex [2]", id="btn-codex")
                    yield Button("Gemini [3]", id="btn-gemini")
                yield Input(placeholder="Search sessions... (/)", id="search-input")
                yield OptionList(id="sessions-list")
                yield Label("0 sessions", id="session-count")
            with Vertical(id="right-panel"):
                yield VerticalScroll(
                    Static("Select a session to preview", id="preview-text"),
                    id="preview-panel",
                )
                with Vertical(id="options-panel"):
                    with Horizontal(id="format-bar"):
                        yield Label("Format: ", classes="panel-title")
                        yield Button("MD [F1]", id="fmt-md")
                        yield Button("HTML [F2]", id="fmt-html")
                        yield Button("Player [F3]", id="fmt-player", variant="primary")
                        yield Button("Terminal [F4]", id="fmt-terminal")
                    with Horizontal(id="theme-bar"):
                        yield Label("Theme:  ", classes="panel-title")
                        yield Button("Light", id="theme-light", variant="primary")
                        yield Button("Console", id="theme-console")
                    with Horizontal(id="output-bar"):
                        yield Label("Output: ", classes="panel-title")
                        yield Label("(stdout)", id="output-display")
                        yield Button("Browse", id="browse-btn")
        with Horizontal(id="action-bar"):
            yield Button("Replay [r]", variant="primary", id="run-btn")
            yield Button("Quit [q]", id="quit-btn")
        yield Footer()

    # ---- Lifecycle ----

    def on_mount(self):
        self.load_sessions()

    # ---- Session loading ----

    @work(thread=True)
    def load_sessions(self):
        """Load sessions for the current agent in a worker thread."""
        agent = self.current_agent
        adapter = ADAPTERS.get(agent)
        if not adapter:
            return

        raw_sessions = adapter.discover_sessions()
        sessions = []
        for s in raw_sessions:
            try:
                preview = _get_session_preview(s, agent)
                total = preview.get("user_count", 0) + preview.get("assistant_count", 0)
                if total == 0:
                    continue
                sessions.append(SessionInfo(s, agent, preview))
            except Exception:
                continue

        self.all_sessions = sessions
        self.call_from_thread(self._apply_filter)

    def _apply_filter(self):
        """Filter sessions by search query and update the list."""
        search_input = self.query_one("#search-input", Input)
        query = search_input.value.strip().lower()

        if query:
            self.filtered_sessions = [
                s for s in self.all_sessions
                if query in s.first_message.lower()
                or query in s.project.lower()
                or query in s.date.lower()
                or query in s.path.lower()
            ]
        else:
            self.filtered_sessions = list(self.all_sessions)

        option_list = self.query_one("#sessions-list", OptionList)
        option_list.clear_options()
        for s in self.filtered_sessions:
            option_list.add_option(Option(s.display_line))

        count_label = self.query_one("#session-count", Label)
        total = len(self.all_sessions)
        shown = len(self.filtered_sessions)
        if shown == total:
            count_label.update("{} sessions".format(total))
        else:
            count_label.update("{} / {} sessions".format(shown, total))

        self.selected_index = -1
        self._update_preview_text("Select a session to preview")

    # ---- Event handlers ----

    @on(OptionList.OptionHighlighted, "#sessions-list")
    def on_session_highlighted(self, event):
        idx = event.option_index
        if 0 <= idx < len(self.filtered_sessions):
            self.selected_index = idx
            self._refresh_preview()

    @on(Input.Changed, "#search-input")
    def on_search_changed(self, event):
        self._apply_filter()

    @on(Button.Pressed, "#btn-claude")
    def on_claude(self):
        self.action_agent("claude")

    @on(Button.Pressed, "#btn-codex")
    def on_codex(self):
        self.action_agent("codex")

    @on(Button.Pressed, "#btn-gemini")
    def on_gemini(self):
        self.action_agent("gemini")

    @on(Button.Pressed, "#fmt-md")
    def on_fmt_md(self):
        self.action_set_format("md")

    @on(Button.Pressed, "#fmt-html")
    def on_fmt_html(self):
        self.action_set_format("html")

    @on(Button.Pressed, "#fmt-player")
    def on_fmt_player(self):
        self.action_set_format("player")

    @on(Button.Pressed, "#fmt-terminal")
    def on_fmt_terminal(self):
        self.action_set_format("terminal")

    @on(Button.Pressed, "#theme-light")
    def on_theme_light(self):
        self._set_theme("light")

    @on(Button.Pressed, "#theme-console")
    def on_theme_console(self):
        self._set_theme("console")

    @on(Button.Pressed, "#browse-btn")
    def on_browse(self):
        self.push_screen(FilePickerScreen(self.output_path), self._on_file_selected)

    @on(Button.Pressed, "#run-btn")
    def on_run(self):
        self.action_run_replay()

    @on(Button.Pressed, "#quit-btn")
    def on_quit_btn(self):
        self.exit()

    def _on_file_selected(self, path):
        if path:
            self.output_path = path
            self.query_one("#output-display", Label).update(path)

    # ---- Actions ----

    def action_agent(self, agent):
        self.current_agent = agent
        self._update_agent_buttons()
        self.load_sessions()

    def action_set_format(self, fmt):
        self.current_format = fmt
        self._update_format_buttons()

    def _set_theme(self, theme):
        self.selected_theme = theme
        self._update_theme_buttons()

    def action_focus_search(self):
        self.query_one("#search-input", Input).focus()

    def action_run_replay(self):
        if self.selected_index < 0 or self.selected_index >= len(self.filtered_sessions):
            self.notify("Please select a session first", severity="warning")
            return
        self.run_replay()

    # ---- Replay execution ----

    @work(thread=True)
    def run_replay(self):
        session_info = self.filtered_sessions[self.selected_index]
        agent = session_info.agent
        input_path = session_info.path
        fmt = self.current_format
        theme = self.selected_theme
        output = self.output_path

        # Build pipeline commands
        if agent == "claude":
            log2model = "claude-log2model.py"
        elif agent == "codex":
            log2model = "codex-log2model.py"
        elif agent == "aider":
            log2model = "aider-log2model.py"
        elif agent == "cursor":
            log2model = "cursor-log2model.py"
        else:
            log2model = "gemini-log2model.py"

        fd, model_path = tempfile.mkstemp(prefix="log-model-", suffix=".json")
        os.close(fd)

        log2model_path = str(script_dir / log2model)
        renderer_path = str(script_dir / "log-model-renderer.py")

        # Step 1: adapter
        log_cmd = [sys.executable, log2model_path, input_path, "-o", model_path]
        try:
            res = subprocess.run(log_cmd, capture_output=True, text=True, check=False)
            if res.returncode != 0:
                err = res.stderr.strip() or "adapter failed"
                self.call_from_thread(
                    self.notify, "Error in log2model: " + err, severity="error"
                )
                return
        except Exception as e:
            self.call_from_thread(
                self.notify, "Error: " + str(e), severity="error"
            )
            return

        # Step 2: renderer
        render_cmd = [sys.executable, renderer_path, model_path, "-f", fmt, "-t", theme]
        if output:
            render_cmd += ["-o", output]
        else:
            # Auto-generate output path for browser-viewable formats
            if fmt in ("player", "terminal", "html"):
                fd2, auto_path = tempfile.mkstemp(prefix="replay-", suffix=".html")
                os.close(fd2)
                render_cmd += ["-o", auto_path]
                output = auto_path

        try:
            res = subprocess.run(render_cmd, capture_output=True, text=True, check=False)
            if res.returncode != 0:
                err = res.stderr.strip() or "renderer failed"
                self.call_from_thread(
                    self.notify, "Error in renderer: " + err, severity="error"
                )
                return
        except Exception as e:
            self.call_from_thread(
                self.notify, "Error: " + str(e), severity="error"
            )
            return

        # Cleanup model
        try:
            os.remove(model_path)
        except OSError:
            pass

        # Open in browser for HTML formats
        if output and fmt in ("player", "terminal", "html"):
            import webbrowser
            webbrowser.open("file://" + os.path.abspath(output))
            self.call_from_thread(
                self.notify, "Opened in browser: " + output, severity="information"
            )
        elif output:
            self.call_from_thread(
                self.notify, "Output saved to " + output, severity="information"
            )
        else:
            self.call_from_thread(
                self.notify, "Conversion complete (output to stdout)", severity="information"
            )

    # ---- Preview ----

    def _refresh_preview(self):
        if self.selected_index < 0 or self.selected_index >= len(self.filtered_sessions):
            self._update_preview_text("Select a session to preview")
            return

        session_info = self.filtered_sessions[self.selected_index]
        lines = []
        lines.append("[bold]Session Preview[/bold]")
        lines.append("")
        lines.append("[dim]Agent:[/dim]   {}".format(session_info.agent))
        lines.append("[dim]Date:[/dim]    {}".format(session_info.date))
        lines.append("[dim]Project:[/dim] {}".format(session_info.project or "(none)"))
        lines.append("[dim]Size:[/dim]    {}".format(_format_size(session_info.size)))
        lines.append("[dim]Messages:[/dim] {} total ({} user, {} assistant)".format(
            session_info.total_msgs, session_info.user_count, session_info.assistant_count
        ))
        lines.append("[dim]Path:[/dim]    {}".format(session_info.path))
        lines.append("")
        lines.append("[bold]First messages:[/bold]")
        lines.append("")

        preview_msgs = _get_preview_messages(session_info.session, session_info.agent)
        if preview_msgs:
            for msg in preview_msgs:
                role = msg["role"]
                text = msg["text"].replace("\n", " ")
                if len(text) > 200:
                    text = text[:198] + ".."
                if role == "user":
                    lines.append("[bold cyan]> User:[/bold cyan] {}".format(text))
                else:
                    lines.append("[bold green]> Assistant:[/bold green] {}".format(text))
                lines.append("")
        else:
            lines.append("[dim]No preview available[/dim]")

        self._update_preview_text("\n".join(lines))

    def _update_preview_text(self, text):
        preview = self.query_one("#preview-text", Static)
        preview.update(text)

    # ---- Button state management ----

    def _update_agent_buttons(self):
        for btn_id, agent in [("#btn-claude", "claude"), ("#btn-codex", "codex"), ("#btn-gemini", "gemini")]:
            btn = self.query_one(btn_id, Button)
            btn.variant = "primary" if agent == self.current_agent else "default"

    def _update_format_buttons(self):
        fmt_map = {"md": "#fmt-md", "html": "#fmt-html", "player": "#fmt-player", "terminal": "#fmt-terminal"}
        for fmt, btn_id in fmt_map.items():
            btn = self.query_one(btn_id, Button)
            btn.variant = "primary" if fmt == self.current_format else "default"

    def _update_theme_buttons(self):
        for btn_id, theme in [("#theme-light", "light"), ("#theme-console", "console")]:
            btn = self.query_one(btn_id, Button)
            btn.variant = "primary" if theme == self.selected_theme else "default"


def main():
    """Entry point for TUI."""
    app = LogReplayApp()
    app.run()


if __name__ == "__main__":
    main()
