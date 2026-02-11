#!/usr/bin/env python3
"""Convert Claude Code JSONL session transcript into a common log model (JSON)."""

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
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        return "\n".join(texts)
    return ""


def _extract_tool_uses(content):
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict) and block.get("type") == "tool_use"]


def _extract_tool_results(content):
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict) and block.get("type") == "tool_result"]


def _format_tool_result_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        return "\n".join(texts)
    return str(content)


def parse_messages(input_path):
    messages = []
    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            data = json.loads(line)
            message_type = data.get("type", "")
            if message_type in ("user", "assistant"):
                messages.append(data)
    return messages


def build_model(messages, input_path):
    model = {
        "source": os.path.basename(input_path),
        "agent": "claude",
        "messages": [],
    }

    for data in messages:
        message = data.get("message", {})
        role = message.get("role", "")
        content = message.get("content", "")
        if role not in ("user", "assistant"):
            continue

        text = _extract_text_from_content(content)
        tool_uses = _extract_tool_uses(content)
        tool_results = _extract_tool_results(content)

        entry = {
            "role": role,
            "text": text.strip(),
            "tool_uses": tool_uses,
            "tool_results": [],
        }

        for result in tool_results:
            result_content = result.get("content", "")
            result_text = _format_tool_result_content(result_content)
            if result_text.strip():
                entry["tool_results"].append({"content": result_text})

        if entry["text"] or entry["tool_uses"] or entry["tool_results"]:
            model["messages"].append(entry)

    return model


def _format_size(size_bytes):
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f}M"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.0f}K"
    return f"{size_bytes}B"


def _extract_preview(jsonl_path):
    timestamp = None
    git_branch = ""
    first_message = ""
    user_count = 0
    assistant_count = 0

    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                record_type = data.get("type", "")

                if record_type == "user":
                    user_count += 1
                    if timestamp is None:
                        timestamp = data.get("timestamp", "")
                        git_branch = data.get("gitBranch", "")
                    if not first_message:
                        message = data.get("message", {})
                        content = message.get("content", "")
                        if isinstance(content, str):
                            first_message = content.strip()
                        elif isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    first_message = block.get("text", "").strip()
                                    break
                elif record_type == "assistant":
                    assistant_count += 1
    except (OSError, UnicodeDecodeError):
        pass

    return {
        "timestamp": timestamp or "",
        "git_branch": git_branch,
        "first_message": first_message,
        "user_count": user_count,
        "assistant_count": assistant_count,
    }


def _project_name_from_dir(dir_name):
    parts = dir_name.lstrip("-").split("-")
    if parts:
        return parts[-1]
    return dir_name


def discover_sessions(project_filter=None):
    claude_projects_dir = Path.home() / ".claude" / "projects"
    if not claude_projects_dir.is_dir():
        return []

    sessions = []
    for project_dir in sorted(claude_projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue

        project_name = _project_name_from_dir(project_dir.name)
        if project_filter and project_filter.lower() not in project_name.lower():
            continue

        for jsonl_file in project_dir.glob("*.jsonl"):
            if "/subagents/" in str(jsonl_file):
                continue
            file_stat = jsonl_file.stat()
            sessions.append({
                "path": str(jsonl_file),
                "project": project_name,
                "project_dir": project_dir.name,
                "size": file_stat.st_size,
                "mtime": file_stat.st_mtime,
            })

    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    sessions = [s for s in sessions if s["size"] > 1024]
    return sessions


def select_session(sessions):
    if not sessions:
        print("No sessions found in ~/.claude/projects/")
        raise SystemExit(1)

    filtered_sessions = []
    previews = []
    for session in sessions:
        preview = _extract_preview(session["path"])
        total = preview["user_count"] + preview["assistant_count"]
        if total == 0:
            continue
        filtered_sessions.append(session)
        previews.append(preview)
    sessions = filtered_sessions

    if not sessions:
        print("No sessions with messages found in ~/.claude/projects/")
        raise SystemExit(1)

    print()
    print(f"  {'#':>3}  {'Date':16}  {'Branch':28}  {'Project':14}  {'Size':>6}  {'Msgs':>5}  First message")
    print(f"  {'─' * 3}  {'─' * 16}  {'─' * 28}  {'─' * 14}  {'─' * 6}  {'─' * 5}  {'─' * 40}")

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

        branch = preview["git_branch"]
        if len(branch) > 28:
            branch = branch[:26] + ".."

        project = session["project"]
        if len(project) > 14:
            project = project[:12] + ".."

        size_display = _format_size(session["size"])
        total_msgs = preview["user_count"] + preview["assistant_count"]

        first_msg = preview["first_message"].replace("\n", " ")
        if len(first_msg) > 60:
            first_msg = first_msg[:58] + ".."

        print(f"  {idx:>3}  {date_display:16}  {branch:28}  {project:14}  {size_display:>6}  {total_msgs:>5}  {first_msg}")

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
    parser = argparse.ArgumentParser(description="Convert Claude Code JSONL to common log model")
    parser.add_argument("input", nargs="?", default=None, help="input JSONL file path (omit to select)")
    parser.add_argument("-o", "--output", help="output JSON file path")
    parser.add_argument("--project", default=None, help="filter sessions by project name (substring match)")
    args = parser.parse_args()

    input_path = args.input
    if input_path is None:
        sessions = discover_sessions(project_filter=args.project)
        input_path = select_session(sessions)

    messages = parse_messages(input_path)
    model = build_model(messages, input_path)

    if args.output:
        output_path = args.output
    else:
        output_path = os.path.splitext(input_path)[0] + ".model.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, indent=2)

    print(f"Converted {len(model['messages'])} messages -> {output_path}")


if __name__ == "__main__":
    main()
