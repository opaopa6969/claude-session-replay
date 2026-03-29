#!/usr/bin/env python3
"""Convert Aider chat history markdown into a common log model (JSON)."""

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path


def _parse_chat_history(input_path):
    """Parse Aider .aider.chat.history.md file into message records.

    Aider chat history uses markdown with markers like:
        #### /user timestamp
        <user message text>

        #### /assistant timestamp
        <assistant message text>

    Some formats use:
        # aider chat started at <timestamp>
        #### <user message>
        > <assistant response>
    """
    messages = []
    current_role = None
    current_text_lines = []
    current_timestamp = ""

    def _flush():
        if current_role and current_text_lines:
            text = "\n".join(current_text_lines).strip()
            if text:
                messages.append({
                    "role": current_role,
                    "text": text,
                    "timestamp": current_timestamp,
                })

    try:
        with open(input_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except (OSError, UnicodeDecodeError):
        return messages

    # Detect format: look for #### /user or #### /assistant markers
    has_role_markers = False
    for line in lines:
        if re.match(r'^####\s+/(user|assistant)', line):
            has_role_markers = True
            break

    if has_role_markers:
        # Format with explicit role markers
        for line in lines:
            stripped = line.rstrip("\n")

            # Match #### /user or #### /assistant with optional timestamp
            role_match = re.match(r'^####\s+/(user|assistant)\s*(.*)', stripped)
            if role_match:
                _flush()
                current_role = role_match.group(1)
                if current_role not in ("user", "assistant"):
                    current_role = None
                    current_text_lines = []
                    current_timestamp = ""
                    continue
                ts_part = role_match.group(2).strip()
                current_timestamp = ts_part if ts_part else ""
                current_text_lines = []
                continue

            if current_role is not None:
                current_text_lines.append(stripped)
    else:
        # Aider standard format:
        # "# aider chat started at <timestamp>"
        # "#### <user prompt>"
        # "> assistant response lines"
        chat_started_ts = ""
        for line in lines:
            stripped = line.rstrip("\n")

            # Chat session start marker
            start_match = re.match(r'^#\s+aider chat started at\s+(.*)', stripped)
            if start_match:
                _flush()
                chat_started_ts = start_match.group(1).strip()
                current_role = None
                current_text_lines = []
                current_timestamp = ""
                continue

            # User message: #### <text>
            user_match = re.match(r'^####\s+(.*)', stripped)
            if user_match:
                _flush()
                current_role = "user"
                current_timestamp = chat_started_ts
                current_text_lines = [user_match.group(1)]
                continue

            # Assistant response: lines starting with >
            if stripped.startswith("> ") or stripped == ">":
                if current_role != "assistant":
                    _flush()
                    current_role = "assistant"
                    current_timestamp = chat_started_ts
                    current_text_lines = []
                resp_text = stripped[2:] if stripped.startswith("> ") else ""
                current_text_lines.append(resp_text)
                continue

            # Continuation of current block
            if current_role is not None:
                # Blank lines between blocks end the current block if assistant
                if not stripped and current_role == "assistant":
                    _flush()
                    current_role = None
                    current_text_lines = []
                    current_timestamp = ""
                else:
                    current_text_lines.append(stripped)

    _flush()
    return messages


def build_model(input_path):
    """Parse Aider chat history and return common model dict."""
    raw_messages = _parse_chat_history(input_path)

    model = {
        "source": os.path.basename(input_path),
        "agent": "aider",
        "messages": [],
    }

    for msg in raw_messages:
        entry = {
            "role": msg["role"],
            "text": msg["text"],
            "tool_uses": [],
            "tool_results": [],
            "thinking": [],
            "timestamp": msg.get("timestamp", ""),
        }
        model["messages"].append(entry)

    return model


def _format_size(size_bytes):
    if size_bytes >= 1024 * 1024:
        return "{:.1f}M".format(size_bytes / (1024 * 1024))
    if size_bytes >= 1024:
        return "{:.0f}K".format(size_bytes / 1024)
    return "{}B".format(size_bytes)


def _extract_preview(file_path):
    """Extract preview metadata from an Aider chat history file."""
    timestamp = ""
    first_message = ""
    user_count = 0
    assistant_count = 0

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()

                # Try to get timestamp from chat start marker
                start_match = re.match(r'^#\s+aider chat started at\s+(.*)', stripped)
                if start_match and not timestamp:
                    timestamp = start_match.group(1).strip()

                # Role markers format
                role_match = re.match(r'^####\s+/(user|assistant)\s*(.*)', stripped)
                if role_match:
                    role = role_match.group(1)
                    if role == "user":
                        user_count += 1
                        if not timestamp:
                            ts_part = role_match.group(2).strip()
                            if ts_part:
                                timestamp = ts_part
                    elif role == "assistant":
                        assistant_count += 1
                    continue

                # Standard format: #### = user prompt
                user_match = re.match(r'^####\s+(.*)', stripped)
                if user_match and not role_match:
                    user_count += 1
                    if not first_message:
                        first_message = user_match.group(1).strip()
                    continue

                # Count assistant response lines (rough)
                if stripped.startswith("> "):
                    # Only count once per block - use simple heuristic
                    pass

    except (OSError, UnicodeDecodeError):
        pass

    # If no first message found from #### markers, try to grab from parsed messages
    if not first_message:
        try:
            msgs = _parse_chat_history(file_path)
            for m in msgs:
                if m["role"] == "user":
                    first_message = m["text"].split("\n")[0]
                    break
                user_count = sum(1 for m in msgs if m["role"] == "user")
                assistant_count = sum(1 for m in msgs if m["role"] == "assistant")
        except Exception:
            pass

    return {
        "timestamp": timestamp,
        "first_message": first_message,
        "user_count": user_count,
        "assistant_count": assistant_count,
    }


def _extract_preview_messages(file_path, count=3):
    """Extract first N user/assistant messages for preview display."""
    messages = []
    try:
        raw = _parse_chat_history(file_path)
        for msg in raw:
            if msg["text"]:
                messages.append({"role": msg["role"], "text": msg["text"]})
            if len(messages) >= count:
                break
    except Exception:
        pass
    return messages


def discover_sessions(project_filter=None):
    """Find Aider chat history files.

    Aider stores chat history in:
    - .aider.chat.history.md in project directories
    - ~/.aider/chat-history/ for centralized history
    - .aider.chat.history.md can also appear in subdirectories
    """
    sessions = []
    seen_paths = set()

    # 1. Search home directory for .aider chat histories
    aider_home = Path.home() / ".aider"
    if aider_home.is_dir():
        for md_file in aider_home.rglob("*.md"):
            if not md_file.is_file():
                continue
            path_str = str(md_file)
            if path_str in seen_paths:
                continue
            if project_filter and project_filter.lower() not in path_str.lower():
                continue
            seen_paths.add(path_str)
            file_stat = md_file.stat()
            sessions.append({
                "path": path_str,
                "project": md_file.parent.name or "aider",
                "size": file_stat.st_size,
                "mtime": file_stat.st_mtime,
            })

    # 2. Search common project directories for .aider.chat.history.md
    search_roots = []

    # Home directory itself
    home = Path.home()
    search_roots.append(home)

    # Common development directories
    for dev_dir_name in ("work", "projects", "src", "dev", "code", "repos"):
        dev_dir = home / dev_dir_name
        if dev_dir.is_dir():
            search_roots.append(dev_dir)

    for root in search_roots:
        try:
            # Search up to 3 levels deep for .aider.chat.history.md
            for pattern in [
                ".aider.chat.history.md",
                "*/.aider.chat.history.md",
                "*/*/.aider.chat.history.md",
            ]:
                for md_file in root.glob(pattern):
                    if not md_file.is_file():
                        continue
                    path_str = str(md_file)
                    if path_str in seen_paths:
                        continue
                    if project_filter and project_filter.lower() not in path_str.lower():
                        continue
                    seen_paths.add(path_str)
                    file_stat = md_file.stat()
                    # Project name = parent directory name
                    project_name = md_file.parent.name
                    if project_name == home.name:
                        project_name = "home"
                    sessions.append({
                        "path": path_str,
                        "project": project_name,
                        "size": file_stat.st_size,
                        "mtime": file_stat.st_mtime,
                    })
        except (OSError, PermissionError):
            continue

    # 3. Also look for .aider.input.history and .aider.output.history
    for hist_name in (".aider.input.history", ".aider.output.history"):
        for root in search_roots:
            try:
                for pattern in [
                    hist_name,
                    "*/" + hist_name,
                ]:
                    for hist_file in root.glob(pattern):
                        if not hist_file.is_file():
                            continue
                        path_str = str(hist_file)
                        if path_str in seen_paths:
                            continue
                        if project_filter and project_filter.lower() not in path_str.lower():
                            continue
                        seen_paths.add(path_str)
                        file_stat = hist_file.stat()
                        project_name = hist_file.parent.name
                        if project_name == home.name:
                            project_name = "home"
                        sessions.append({
                            "path": path_str,
                            "project": project_name,
                            "size": file_stat.st_size,
                            "mtime": file_stat.st_mtime,
                        })
            except (OSError, PermissionError):
                continue

    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    # Filter out very small files
    sessions = [s for s in sessions if s["size"] > 100]
    return sessions


def select_session(sessions):
    if not sessions:
        print("No Aider sessions found.")
        raise SystemExit(1)

    print()
    print("  {:>3}  {:16}  {:20}  {:>6}  {:>5}  First message".format(
        "#", "Date", "Project", "Size", "Msgs"))
    print("  {}  {}  {}  {}  {}  {}".format(
        "\u2500" * 3, "\u2500" * 16, "\u2500" * 20, "\u2500" * 6, "\u2500" * 5, "\u2500" * 40))

    previews = []
    for i, session in enumerate(sessions):
        preview = _extract_preview(session["path"])
        previews.append(preview)
        idx = i + 1

        timestamp_str = preview["timestamp"]
        if timestamp_str:
            try:
                dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                dt_local = dt.astimezone()
                date_display = dt_local.strftime("%Y-%m-%d %H:%M")
            except (ValueError, OSError):
                date_display = timestamp_str[:16]
        else:
            date_display = datetime.fromtimestamp(session["mtime"]).strftime("%Y-%m-%d %H:%M")

        project = session["project"]
        if len(project) > 20:
            project = project[:18] + ".."

        size_display = _format_size(session["size"])
        total_msgs = preview["user_count"] + preview["assistant_count"]

        first_msg = preview["first_message"].replace("\n", " ")
        if len(first_msg) > 60:
            first_msg = first_msg[:58] + ".."

        print("  {:>3}  {:16}  {:20}  {:>6}  {:>5}  {}".format(
            idx, date_display, project, size_display, total_msgs, first_msg))

    print()

    while True:
        try:
            choice = input("Select session [1]: ").strip()
            if not choice:
                choice = "1"
            num = int(choice)
            if 1 <= num <= len(sessions):
                selected = sessions[num - 1]
                print("  -> {}".format(selected["path"]))
                print()
                return selected["path"]
            print("  1-{} の数字を入力してください".format(len(sessions)))
        except ValueError:
            print("  数字を入力してください")
        except (KeyboardInterrupt, EOFError):
            print()
            raise SystemExit(0)


def main():
    parser = argparse.ArgumentParser(description="Convert Aider chat history to common log model")
    parser.add_argument("input", nargs="?", default=None, help="input file path (omit to select)")
    parser.add_argument("-o", "--output", help="output JSON file path")
    parser.add_argument("--project", default=None, help="filter sessions by project name (substring match)")
    args = parser.parse_args()

    input_path = args.input
    if input_path is None:
        sessions = discover_sessions(project_filter=args.project)
        input_path = select_session(sessions)

    model = build_model(input_path)

    if args.output:
        output_path = args.output
    else:
        output_path = os.path.splitext(input_path)[0] + ".model.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, indent=2)

    print("Converted {} messages -> {}".format(len(model["messages"]), output_path))


if __name__ == "__main__":
    main()
