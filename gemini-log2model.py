#!/usr/bin/env python3
"""Convert Gemini CLI session JSON into a common log model (JSON)."""

import argparse
import json
import os
from datetime import datetime
from pathlib import Path


def _extract_text_from_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and "text" in block:
                texts.append(block.get("text", ""))
        return "
".join(texts)
    return ""


def build_model(session_data, input_path):
    model = {
        "source": os.path.basename(input_path),
        "agent": "gemini",
        "messages": [],
    }

    for data in session_data.get("messages", []):
        role_type = data.get("type", "")
        if role_type == "user":
            role = "user"
        elif role_type == "gemini":
            role = "assistant"
        else:
            continue

        content = data.get("content", "")
        text = _extract_text_from_content(content)
        
        # thoughts を thinking ブロックとして抽出
        thinking = []
        for thought in data.get("thoughts", []):
            desc = thought.get("description", "")
            if desc:
                thinking.append(desc)

        entry = {
            "role": role,
            "text": text.strip(),
            "tool_uses": [],  # Gemini CLI はセッション JSON にツール使用を含まない
            "tool_results": [],
            "thinking": thinking,
            "timestamp": data.get("timestamp", ""),
        }

        if entry["text"] or entry["thinking"]:
            model["messages"].append(entry)

    return model


def _format_size(size_bytes):
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f}M"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.0f}K"
    return f"{size_bytes}B"


def _extract_preview(session_path):
    timestamp = None
    first_message = ""
    user_count = 0
    assistant_count = 0

    try:
        with open(session_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            timestamp = data.get("startTime", "")
            for msg in data.get("messages", []):
                m_type = msg.get("type", "")
                if m_type == "user":
                    user_count += 1
                    if not first_message:
                        content = msg.get("content", "")
                        first_message = _extract_text_from_content(content)
                elif m_type == "gemini":
                    assistant_count += 1
    except (OSError, json.JSONDecodeError):
        pass

    return {
        "timestamp": timestamp or "",
        "first_message": first_message,
        "user_count": user_count,
        "assistant_count": assistant_count,
    }


def discover_sessions(project_filter=None):
    gemini_tmp_dir = Path.home() / ".gemini" / "tmp"
    if not gemini_tmp_dir.is_dir():
        return []

    sessions = []
    # Project directories in ~/.gemini/tmp/
    for project_dir in sorted(gemini_tmp_dir.iterdir()):
        if not project_dir.is_dir():
            continue

        project_name = project_dir.name
        if project_filter and project_filter.lower() not in project_name.lower():
            continue

        # Search in chats/
        chats_dir = project_dir / "chats"
        if chats_dir.is_dir():
            for json_file in chats_dir.glob("session-*.json"):
                file_stat = json_file.stat()
                sessions.append({
                    "path": str(json_file),
                    "project": project_name,
                    "project_dir": project_dir.name,
                    "size": file_stat.st_size,
                    "mtime": file_stat.st_mtime,
                })

    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions


def select_session(sessions):
    if not sessions:
        print("No sessions found in ~/.gemini/tmp/")
        raise SystemExit(1)

    print()
    print(f"  {'#':>3}  {'Date':16}  {'Project':20}  {'Size':>6}  {'Msgs':>5}  First message")
    print(f"  {'─' * 3}  {'─' * 16}  {'─' * 20}  {'─' * 6}  {'─' * 5}  {'─' * 40}")

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

        first_msg = preview["first_message"].replace("
", " ")
        if len(first_msg) > 60:
            first_msg = first_msg[:58] + ".."

        print(f"  {idx:>3}  {date_display:16}  {project:20}  {size_display:>6}  {total_msgs:>5}  {first_msg}")

    print()

    while True:
        try:
            choice = input("Select session [1]: ").strip()
            if not choice:
                choice = "1"
            num = int(choice)
            if 1 <= num <= len(sessions):
                selected = sessions[num - 1]
                print(f"  -> {selected['path']}")
                print()
                return selected["path"]
            print(f"  1-{len(sessions)} の数字を入力してください")
        except ValueError:
            print("  数字を入力してください")
        except (KeyboardInterrupt, EOFError):
            print()
            raise SystemExit(0)


def main():
    parser = argparse.ArgumentParser(description="Convert Gemini CLI session JSON to common log model")
    parser.add_argument("input", nargs="?", default=None, help="input session JSON path (omit to select)")
    parser.add_argument("-o", "--output", help="output JSON file path")
    parser.add_argument("--project", default=None, help="filter sessions by project name (substring match)")
    args = parser.parse_args()

    input_path = args.input
    if input_path is None:
        sessions = discover_sessions(project_filter=args.project)
        input_path = select_session(sessions)

    with open(input_path, "r", encoding="utf-8") as f:
        session_data = json.load(f)
    
    model = build_model(session_data, input_path)

    if args.output:
        output_path = args.output
    else:
        output_path = os.path.splitext(input_path)[0] + ".model.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, indent=2)

    print(f"Converted {len(model['messages'])} messages -> {output_path}")


if __name__ == "__main__":
    main()
