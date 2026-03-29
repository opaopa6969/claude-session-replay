#!/usr/bin/env python3
"""Stream watcher: live-follow active AI coding sessions (tail -f for session logs).

Monitors a session log file (JSONL) for new lines, parses each new line
using the appropriate agent adapter, and renders incrementally to terminal.

Usage:
    python3 log-replay-stream.py --agent claude -f                # follow latest session
    python3 log-replay-stream.py --agent claude session.jsonl -f  # follow specific file
    python3 log-replay-stream.py --session                        # auto-detect latest active
    python3 log-replay-stream.py --agent codex --format markdown  # markdown output
"""

import argparse
import importlib.util
import json
import os
import signal
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Adapter loading
# ---------------------------------------------------------------------------

_script_dir = Path(__file__).parent
_adapters = {}

_ADAPTER_FILES = {
    "claude": "claude-log2model.py",
    "codex": "codex-log2model.py",
    "gemini": "gemini-log2model.py",
    "aider": "aider-log2model.py",
    "cursor": "cursor-log2model.py",
}


def _import_module(name, filepath):
    spec = importlib.util.spec_from_file_location(name, filepath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _get_adapter(agent):
    if agent not in _adapters:
        if agent not in _ADAPTER_FILES:
            raise ValueError("Unknown agent: {}".format(agent))
        _adapters[agent] = _import_module(
            "{}_log2model".format(agent),
            str(_script_dir / _ADAPTER_FILES[agent]),
        )
    return _adapters[agent]


# ---------------------------------------------------------------------------
# Line parsers (per agent) — produce common-model entries
# ---------------------------------------------------------------------------

def _parse_line_claude(line_text, adapter):
    try:
        data = json.loads(line_text)
    except json.JSONDecodeError:
        return None
    if data.get("type") not in ("user", "assistant"):
        return None
    message = data.get("message", {})
    role = message.get("role", "")
    if role not in ("user", "assistant"):
        return None
    content = message.get("content", "")
    text = adapter._extract_text_from_content(content)
    tool_uses = adapter._extract_tool_uses(content)
    tool_results_raw = adapter._extract_tool_results(content)
    thinking = adapter._extract_thinking_from_content(content)

    tool_results = []
    for r in tool_results_raw:
        rc = r.get("content", "")
        rt = adapter._format_tool_result_content(rc)
        if rt.strip():
            tool_results.append({"content": rt})

    entry = {
        "role": role, "text": text.strip(), "tool_uses": tool_uses,
        "tool_results": tool_results, "thinking": thinking,
        "timestamp": data.get("timestamp", ""),
    }
    if entry["text"] or entry["tool_uses"] or entry["tool_results"] or entry["thinking"]:
        return entry
    return None


def _parse_line_codex(line_text, adapter):
    try:
        data = json.loads(line_text)
    except json.JSONDecodeError:
        return None
    record_type = data.get("type", "")
    # Support both response_item and event_msg formats
    if record_type == "response_item":
        payload = data.get("payload", data)
        if payload.get("type") == "message":
            role = payload.get("role", "")
            if role not in ("user", "assistant"):
                return None
            content = payload.get("content", [])
            text = adapter._extract_text_from_codex_content(content) if hasattr(adapter, '_extract_text_from_codex_content') else ""
            thinking = adapter._extract_thinking_from_codex_content(content) if hasattr(adapter, '_extract_thinking_from_codex_content') else []
            entry = {
                "role": role, "text": (text or "").strip(),
                "tool_uses": [], "tool_results": [],
                "thinking": thinking or [], "timestamp": data.get("timestamp", ""),
            }
            if entry["text"] or entry["thinking"]:
                return entry
    elif record_type == "event_msg":
        payload = data.get("payload", {})
        payload_type = payload.get("type", "")
        if payload_type == "user_message":
            return {"role": "user", "text": payload.get("message", "").strip(),
                    "tool_uses": [], "tool_results": [], "thinking": [],
                    "timestamp": data.get("timestamp", "")}
        elif payload_type == "agent_message":
            return {"role": "assistant", "text": payload.get("message", "").strip(),
                    "tool_uses": [], "tool_results": [], "thinking": [],
                    "timestamp": data.get("timestamp", "")}
    return None


def _parse_line_generic(line_text, adapter):
    """Generic JSONL line parser for aider/cursor/gemini."""
    try:
        data = json.loads(line_text)
    except json.JSONDecodeError:
        return None
    record_type = data.get("type", "")
    if record_type not in ("user", "assistant"):
        return None
    message = data.get("message", {})
    role = message.get("role", record_type)
    if role not in ("user", "assistant"):
        return None
    content = message.get("content", "")
    if hasattr(adapter, '_extract_text_from_content'):
        text = adapter._extract_text_from_content(content)
    elif isinstance(content, str):
        text = content
    else:
        text = ""
    entry = {
        "role": role, "text": (text or "").strip(),
        "tool_uses": [], "tool_results": [], "thinking": [],
        "timestamp": data.get("timestamp", ""),
    }
    if entry["text"]:
        return entry
    return None


_LINE_PARSERS = {
    "claude": _parse_line_claude,
    "codex": _parse_line_codex,
    "gemini": _parse_line_generic,
    "aider": _parse_line_generic,
    "cursor": _parse_line_generic,
}


# ---------------------------------------------------------------------------
# Terminal renderer (ANSI colors, incremental)
# ---------------------------------------------------------------------------

# ANSI escape codes
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_BLUE = "\033[34m"
_CYAN = "\033[36m"
_YELLOW = "\033[33m"
_MAGENTA = "\033[35m"
_WHITE = "\033[37m"
_GRAY = "\033[90m"


def _render_entry_terminal(entry, msg_num):
    """Render a single common-model entry to terminal with ANSI colors."""
    lines = []
    role = entry.get("role", "")
    text = entry.get("text", "")
    tool_uses = entry.get("tool_uses", [])
    tool_results = entry.get("tool_results", [])
    thinking = entry.get("thinking", [])
    timestamp = entry.get("timestamp", "")

    ts_display = ""
    if timestamp:
        try:
            # Try ISO format
            ts_display = timestamp[:19].replace("T", " ")
        except Exception:
            pass

    if role == "user":
        header = "{bold}{green}> User [{num}]{reset}".format(
            bold=_BOLD, green=_GREEN, num=msg_num, reset=_RESET)
        if ts_display:
            header += "  {dim}{ts}{reset}".format(dim=_DIM, ts=ts_display, reset=_RESET)
        lines.append(header)
        if text:
            lines.append(text)
        lines.append("")

    elif role == "assistant":
        header = "{bold}{cyan}< Assistant{reset}".format(
            bold=_BOLD, cyan=_CYAN, reset=_RESET)
        if ts_display:
            header += "  {dim}{ts}{reset}".format(dim=_DIM, ts=ts_display, reset=_RESET)
        lines.append(header)

        # Thinking
        for thought in thinking:
            if thought.strip():
                lines.append("{dim}{magenta}  [thinking] {text}{reset}".format(
                    dim=_DIM, magenta=_MAGENTA, text=thought.strip()[:200], reset=_RESET))

        if text:
            lines.append(text)

        # Tool uses
        for tu in tool_uses:
            name = tu.get("name", "Unknown")
            tool_input = tu.get("input", {})
            if name == "Bash":
                cmd = tool_input.get("command", "")
                lines.append("{yellow}  [{name}]{reset} {cmd}".format(
                    yellow=_YELLOW, name=name, reset=_RESET, cmd=cmd[:120]))
            elif name in ("Read", "Write", "Glob"):
                fp = tool_input.get("file_path", tool_input.get("pattern", ""))
                lines.append("{yellow}  [{name}]{reset} {fp}".format(
                    yellow=_YELLOW, name=name, reset=_RESET, fp=fp))
            elif name == "Edit":
                fp = tool_input.get("file_path", "")
                lines.append("{yellow}  [{name}]{reset} {fp}".format(
                    yellow=_YELLOW, name=name, reset=_RESET, fp=fp))
            elif name == "Grep":
                pattern = tool_input.get("pattern", "")
                path = tool_input.get("path", ".")
                lines.append("{yellow}  [{name}]{reset} \"{pat}\" in {p}".format(
                    yellow=_YELLOW, name=name, reset=_RESET, pat=pattern, p=path))
            elif name == "Task":
                desc = tool_input.get("description", "")[:100]
                lines.append("{yellow}  [{name}]{reset} {desc}".format(
                    yellow=_YELLOW, name=name, reset=_RESET, desc=desc))
            else:
                lines.append("{yellow}  [{name}]{reset}".format(
                    yellow=_YELLOW, name=name, reset=_RESET))

        # Tool results (truncated)
        for tr in tool_results:
            content = tr.get("content", "")
            if content.strip():
                preview = content.strip()[:120].replace("\n", " ")
                lines.append("{gray}  [result] {text}{reset}".format(
                    gray=_GRAY, text=preview, reset=_RESET))

        lines.append("")

    return "\n".join(lines)


def _render_entry_markdown(entry, msg_num):
    """Render a single common-model entry as Markdown."""
    lines = []
    role = entry.get("role", "")
    text = entry.get("text", "")
    tool_uses = entry.get("tool_uses", [])
    tool_results = entry.get("tool_results", [])
    thinking = entry.get("thinking", [])

    if role == "user":
        lines.append("### User [{}]".format(msg_num))
        if text:
            lines.append(text)
        lines.append("")

    elif role == "assistant":
        lines.append("### Assistant")
        for thought in thinking:
            if thought.strip():
                lines.append("> *thinking:* {}".format(thought.strip()[:200]))
        if text:
            lines.append(text)
        for tu in tool_uses:
            name = tu.get("name", "Unknown")
            lines.append("- **{}**".format(name))
        for tr in tool_results:
            content = tr.get("content", "")
            if content.strip():
                lines.append("```\n{}\n```".format(content.strip()[:200]))
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Session auto-detection
# ---------------------------------------------------------------------------

def _find_latest_active_session(agent=None, max_age_seconds=60):
    """Find the most recently modified session file across agents.

    Returns (path, agent_name) or (None, None).
    """
    agents = [agent] if agent else list(_ADAPTER_FILES.keys())
    best = None
    best_mtime = 0
    best_agent = None

    for ag in agents:
        try:
            adapter = _get_adapter(ag)
            sessions = adapter.discover_sessions()
        except Exception:
            continue
        for s in sessions:
            path = s["path"]
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime > best_mtime:
                best_mtime = mtime
                best = path
                best_agent = ag

    if best is None:
        return None, None

    age = time.time() - best_mtime
    if max_age_seconds and age > max_age_seconds:
        # Still return it, but mark as inactive
        return best, best_agent

    return best, best_agent


def _is_session_active(path, threshold_seconds=60):
    """Check if a session file was modified within threshold."""
    try:
        mtime = os.path.getmtime(path)
        return (time.time() - mtime) < threshold_seconds
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Stream watcher core
# ---------------------------------------------------------------------------

class StreamWatcher(object):
    """Watches a file for new lines using os.stat() polling."""

    def __init__(self, file_path, agent, poll_interval_ms=500, output_format="terminal"):
        self.file_path = file_path
        self.agent = agent
        self.poll_interval = poll_interval_ms / 1000.0
        self.output_format = output_format
        self._adapter = _get_adapter(agent)
        self._parser = _LINE_PARSERS.get(agent, _parse_line_generic)
        self._byte_offset = 0
        self._msg_num = 0
        self._running = False

    def _read_new_lines(self):
        """Read new lines from file since last offset."""
        try:
            file_size = os.path.getsize(self.file_path)
        except OSError:
            return []

        if file_size < self._byte_offset:
            # File was truncated — reset
            self._byte_offset = 0
            self._msg_num = 0

        if file_size == self._byte_offset:
            return []

        new_lines = []
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                f.seek(self._byte_offset)
                for line in f:
                    line = line.rstrip("\n\r")
                    if line:
                        new_lines.append(line)
                self._byte_offset = f.tell()
        except (OSError, UnicodeDecodeError):
            pass

        return new_lines

    def _process_line(self, line_text):
        """Parse and render a single line."""
        entry = self._parser(line_text, self._adapter)
        if entry is None:
            return

        if entry.get("role") == "user":
            self._msg_num += 1

        if self.output_format == "markdown":
            output = _render_entry_markdown(entry, self._msg_num)
        else:
            output = _render_entry_terminal(entry, self._msg_num)

        if output.strip():
            sys.stdout.write(output + "\n")
            sys.stdout.flush()

    def replay_existing(self):
        """Replay all existing content in the file."""
        lines = self._read_new_lines()
        for line in lines:
            self._process_line(line)

    def follow(self):
        """Follow the file for new content (blocking loop)."""
        self._running = True
        while self._running:
            lines = self._read_new_lines()
            for line in lines:
                self._process_line(line)
            if self._running:
                time.sleep(self.poll_interval)

    def stop(self):
        """Signal the follow loop to stop."""
        self._running = False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Stream watcher: live-follow active AI coding sessions",
        epilog="Examples:\n"
               "  python3 log-replay-stream.py --agent claude -f\n"
               "  python3 log-replay-stream.py --session\n"
               "  python3 log-replay-stream.py --agent codex session.jsonl -f\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", nargs="?", default=None,
                        help="input session log file (omit to auto-select latest)")
    parser.add_argument("--agent", choices=list(_ADAPTER_FILES.keys()), default=None,
                        help="agent type (required unless --session)")
    parser.add_argument("-f", "--follow", action="store_true",
                        help="keep watching for new lines (like tail -f)")
    parser.add_argument("--format", choices=["terminal", "markdown"], default="terminal",
                        help="output format (default: terminal)")
    parser.add_argument("--session", action="store_true",
                        help="auto-detect latest active session across all agents")
    parser.add_argument("--poll-interval", type=int, default=500,
                        help="poll interval in milliseconds (default: 500)")

    args = parser.parse_args()

    # Resolve session file and agent
    file_path = args.input
    agent = args.agent

    if args.session or (file_path is None and agent is None):
        # Auto-detect
        file_path, agent = _find_latest_active_session(agent=agent)
        if file_path is None:
            print("No session files found.", file=sys.stderr)
            raise SystemExit(1)
        active = _is_session_active(file_path)
        status = "ACTIVE" if active else "inactive"
        print("{dim}Session: {path}  ({agent}, {status}){reset}".format(
            dim=_DIM, path=file_path, agent=agent, status=status, reset=_RESET),
            file=sys.stderr)

    elif file_path is None:
        # No file specified, select from agent's sessions
        adapter = _get_adapter(agent)
        sessions = adapter.discover_sessions()
        file_path = adapter.select_session(sessions)

    if agent is None:
        # Try to guess agent from file path
        path_lower = file_path.lower()
        if ".claude" in path_lower:
            agent = "claude"
        elif ".codex" in path_lower:
            agent = "codex"
        elif ".gemini" in path_lower:
            agent = "gemini"
        elif "aider" in path_lower:
            agent = "aider"
        elif "cursor" in path_lower:
            agent = "cursor"
        else:
            agent = "claude"  # default

    if not os.path.isfile(file_path):
        print("File not found: {}".format(file_path), file=sys.stderr)
        raise SystemExit(1)

    watcher = StreamWatcher(
        file_path=file_path,
        agent=agent,
        poll_interval_ms=args.poll_interval,
        output_format=args.format,
    )

    # Graceful Ctrl+C handling
    def _signal_handler(signum, frame):
        watcher.stop()
        print("\n{dim}Stream stopped.{reset}".format(dim=_DIM, reset=_RESET),
              file=sys.stderr)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # Replay existing content
    watcher.replay_existing()

    if args.follow:
        active = _is_session_active(file_path)
        if active:
            print("{dim}Following... (Ctrl+C to stop){reset}".format(
                dim=_DIM, reset=_RESET), file=sys.stderr)
        else:
            print("{dim}File not recently active, but following... (Ctrl+C to stop){reset}".format(
                dim=_DIM, reset=_RESET), file=sys.stderr)
        watcher.follow()


if __name__ == "__main__":
    main()
