#!/usr/bin/env python3
"""Convert Cursor AI session logs into a common log model (JSON).

Cursor stores conversation data in several locations:
- ~/.cursor/  or ~/.config/Cursor/ (Linux)
- ~/Library/Application Support/Cursor/ (macOS)
- %APPDATA%/Cursor/ (Windows)

The storage format is VSCode-based (SQLite databases and JSON).
This adapter searches for conversation data in known locations.
"""

import argparse
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path


def _get_cursor_data_dirs():
    """Return list of potential Cursor data directories."""
    home = Path.home()
    dirs = []

    # Linux paths
    dirs.append(home / ".cursor")
    dirs.append(home / ".config" / "Cursor")
    dirs.append(home / ".config" / "Cursor" / "User")

    # macOS paths
    dirs.append(home / "Library" / "Application Support" / "Cursor")
    dirs.append(home / "Library" / "Application Support" / "Cursor" / "User")

    # Windows-like paths (WSL)
    dirs.append(home / "AppData" / "Roaming" / "Cursor")
    dirs.append(home / "AppData" / "Roaming" / "Cursor" / "User")

    # Workspace-level .cursor directories
    dirs.append(home / ".cursor-tutor")

    return dirs


def _try_parse_sqlite_state_db(db_path):
    """Try to extract conversation data from Cursor's state.vscdb SQLite database.

    Cursor uses VSCode's state storage (state.vscdb) which contains
    key-value pairs. Conversation data may be stored under keys like
    'workbench.panel.chat' or similar.
    """
    conversations = []
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Look for tables that might contain chat data
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]

        # VSCode state.vscdb uses ItemTable
        if "ItemTable" in tables:
            cursor.execute("SELECT key, value FROM ItemTable")
            for key, value in cursor.fetchall():
                if not isinstance(value, str):
                    continue
                # Look for chat/conversation related keys
                if any(kw in key.lower() for kw in ("chat", "composer", "conversation", "aichat")):
                    try:
                        data = json.loads(value)
                        convos = _extract_conversations_from_json(data, key)
                        conversations.extend(convos)
                    except (json.JSONDecodeError, TypeError):
                        pass

        conn.close()
    except (sqlite3.Error, OSError):
        pass

    return conversations


def _extract_conversations_from_json(data, source_key=""):
    """Extract conversation messages from a JSON structure.

    Cursor stores conversations in various JSON formats. This function
    handles the common patterns.
    """
    conversations = []

    if isinstance(data, dict):
        # Check if this is a conversation object with messages
        if "messages" in data and isinstance(data["messages"], list):
            messages = []
            for msg in data["messages"]:
                if isinstance(msg, dict):
                    role = msg.get("role", msg.get("type", ""))
                    content = msg.get("content", msg.get("text", msg.get("message", "")))
                    if isinstance(content, list):
                        # Content blocks format
                        text_parts = []
                        for block in content:
                            if isinstance(block, dict) and "text" in block:
                                text_parts.append(block["text"])
                            elif isinstance(block, str):
                                text_parts.append(block)
                        content = "\n".join(text_parts)
                    if role and content:
                        normalized_role = "user" if role in ("user", "human") else "assistant"
                        messages.append({
                            "role": normalized_role,
                            "text": str(content).strip(),
                            "timestamp": msg.get("timestamp", msg.get("createdAt", "")),
                        })
            if messages:
                conversations.append({
                    "source_key": source_key,
                    "title": data.get("title", data.get("name", "")),
                    "timestamp": data.get("timestamp", data.get("createdAt", "")),
                    "messages": messages,
                })

        # Check for tabs/conversations array
        for array_key in ("tabs", "conversations", "history", "chats"):
            if array_key in data and isinstance(data[array_key], list):
                for item in data[array_key]:
                    if isinstance(item, dict):
                        sub = _extract_conversations_from_json(item, source_key)
                        conversations.extend(sub)

    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                sub = _extract_conversations_from_json(item, source_key)
                conversations.extend(sub)

    return conversations


def _try_parse_json_file(json_path):
    """Try to parse a JSON file for conversation data."""
    conversations = []
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        conversations = _extract_conversations_from_json(data, str(json_path))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        pass
    return conversations


def _scan_cursor_dir(cursor_dir):
    """Scan a Cursor data directory for session files.

    Returns list of session dicts with path, type, size, mtime.
    """
    sessions = []
    if not cursor_dir.is_dir():
        return sessions

    # 1. Look for state.vscdb (SQLite)
    for db_file in cursor_dir.rglob("state.vscdb"):
        if db_file.is_file():
            file_stat = db_file.stat()
            sessions.append({
                "path": str(db_file),
                "type": "sqlite",
                "project": cursor_dir.name,
                "size": file_stat.st_size,
                "mtime": file_stat.st_mtime,
            })

    # 2. Look for JSON conversation files
    for pattern in ["**/chat*.json", "**/conversation*.json", "**/composer*.json",
                     "**/ai*.json", "**/history*.json"]:
        try:
            for json_file in cursor_dir.glob(pattern):
                if json_file.is_file() and json_file.stat().st_size > 50:
                    file_stat = json_file.stat()
                    sessions.append({
                        "path": str(json_file),
                        "type": "json",
                        "project": cursor_dir.name,
                        "size": file_stat.st_size,
                        "mtime": file_stat.st_mtime,
                    })
        except (OSError, PermissionError):
            continue

    # 3. Look for workspace storage with chat data
    ws_storage = cursor_dir / "workspaceStorage"
    if ws_storage.is_dir():
        try:
            for ws_dir in ws_storage.iterdir():
                if not ws_dir.is_dir():
                    continue
                for db_file in ws_dir.glob("state.vscdb"):
                    if db_file.is_file():
                        file_stat = db_file.stat()
                        sessions.append({
                            "path": str(db_file),
                            "type": "sqlite",
                            "project": ws_dir.name[:12],
                            "size": file_stat.st_size,
                            "mtime": file_stat.st_mtime,
                        })
        except (OSError, PermissionError):
            pass

    return sessions


def build_model(input_path):
    """Parse a Cursor session file and return common model dict."""
    model = {
        "source": os.path.basename(input_path),
        "agent": "cursor",
        "messages": [],
    }

    conversations = []

    if input_path.endswith(".vscdb"):
        conversations = _try_parse_sqlite_state_db(input_path)
    elif input_path.endswith(".json"):
        conversations = _try_parse_json_file(input_path)

    # Merge all conversations into flat message list
    for convo in conversations:
        for msg in convo.get("messages", []):
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
    """Extract preview metadata from a Cursor session file."""
    timestamp = ""
    first_message = ""
    user_count = 0
    assistant_count = 0

    conversations = []
    if file_path.endswith(".vscdb"):
        conversations = _try_parse_sqlite_state_db(file_path)
    elif file_path.endswith(".json"):
        conversations = _try_parse_json_file(file_path)

    for convo in conversations:
        if not timestamp and convo.get("timestamp"):
            timestamp = convo["timestamp"]
        for msg in convo.get("messages", []):
            if msg["role"] == "user":
                user_count += 1
                if not first_message:
                    first_message = msg["text"].split("\n")[0]
                if not timestamp and msg.get("timestamp"):
                    timestamp = msg["timestamp"]
            elif msg["role"] == "assistant":
                assistant_count += 1

    return {
        "timestamp": timestamp,
        "first_message": first_message,
        "user_count": user_count,
        "assistant_count": assistant_count,
    }


def _extract_preview_messages(file_path, count=3):
    """Extract first N user/assistant messages for preview display."""
    messages = []

    conversations = []
    if file_path.endswith(".vscdb"):
        conversations = _try_parse_sqlite_state_db(file_path)
    elif file_path.endswith(".json"):
        conversations = _try_parse_json_file(file_path)

    for convo in conversations:
        for msg in convo.get("messages", []):
            if msg.get("text"):
                messages.append({"role": msg["role"], "text": msg["text"]})
            if len(messages) >= count:
                return messages

    return messages


def discover_sessions(project_filter=None):
    """Find Cursor session files across known locations."""
    sessions = []
    seen_paths = set()

    for cursor_dir in _get_cursor_data_dirs():
        if not cursor_dir.is_dir():
            continue
        found = _scan_cursor_dir(cursor_dir)
        for session in found:
            path_str = session["path"]
            if path_str in seen_paths:
                continue
            if project_filter and project_filter.lower() not in path_str.lower():
                continue
            seen_paths.add(path_str)
            sessions.append(session)

    # Also check for workspace-level .cursor/ directories in common dev paths
    home = Path.home()
    for dev_dir_name in ("work", "projects", "src", "dev", "code", "repos"):
        dev_dir = home / dev_dir_name
        if not dev_dir.is_dir():
            continue
        try:
            for cursor_ws in dev_dir.glob("*/.cursor"):
                if cursor_ws.is_dir():
                    found = _scan_cursor_dir(cursor_ws)
                    for session in found:
                        path_str = session["path"]
                        if path_str in seen_paths:
                            continue
                        if project_filter and project_filter.lower() not in path_str.lower():
                            continue
                        seen_paths.add(path_str)
                        session["project"] = cursor_ws.parent.name
                        sessions.append(session)
        except (OSError, PermissionError):
            continue

    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions


def select_session(sessions):
    if not sessions:
        print("No Cursor sessions found.")
        raise SystemExit(1)

    print()
    print("  {:>3}  {:16}  {:20}  {:>6}  {:>5}  First message".format(
        "#", "Date", "Project", "Size", "Msgs"))
    print("  {}  {}  {}  {}  {}  {}".format(
        "\u2500" * 3, "\u2500" * 16, "\u2500" * 20, "\u2500" * 6, "\u2500" * 5, "\u2500" * 40))

    previews = []
    filtered_sessions = []
    for session in sessions:
        preview = _extract_preview(session["path"])
        total = preview["user_count"] + preview["assistant_count"]
        # Include sqlite files even with 0 messages (might have data in other keys)
        if total == 0 and session.get("type") != "sqlite":
            continue
        filtered_sessions.append(session)
        previews.append(preview)

    sessions = filtered_sessions
    if not sessions:
        print("No Cursor sessions with messages found.")
        raise SystemExit(1)

    for i, (session, preview) in enumerate(zip(sessions, previews)):
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

        project = session.get("project", "cursor")
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
    parser = argparse.ArgumentParser(description="Convert Cursor session data to common log model")
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
