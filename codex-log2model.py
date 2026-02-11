#!/usr/bin/env python3
"""Convert Codex JSONL session transcript into a common log model (JSON)."""

import argparse
import json
import os
from datetime import datetime
from pathlib import Path


def _extract_text_from_codex_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type")
                if block_type in ("input_text", "output_text", "text"):
                    texts.append(block.get("text", ""))
        return "\n".join(texts)
    return ""


def _safe_json_loads(value):
    if not isinstance(value, str):
        return value if isinstance(value, dict) else {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _codex_tool_use_from_function_call(payload):
    name = payload.get("name", "Unknown")
    if payload.get("type") == "function_call":
        args = _safe_json_loads(payload.get("arguments", ""))
        if name == "shell_command":
            command = args.get("command", "")
            workdir = args.get("workdir")
            if workdir:
                command = f"cd {workdir}\n{command}"
            return {"name": "Bash", "input": {"command": command}}
        if name == "update_plan":
            description = args.get("explanation", "update_plan")
            return {"name": "Task", "input": {"description": description}}
        return {"name": name, "input": args}

    tool_input = payload.get("input", "")
    return {"name": name, "input": {"input": tool_input}}


def _codex_has_event_messages(input_path):
    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if data.get("type") != "event_msg":
                continue
            payload = data.get("payload", {})
            if payload.get("type") in ("user_message", "agent_message"):
                return True
    return False


def _format_size(size_bytes):
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f}M"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.0f}K"
    return f"{size_bytes}B"


def _extract_preview(jsonl_path, use_event_msgs):
    timestamp = None
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
                payload = data.get("payload", {})
                payload_type = payload.get("type")

                if record_type == "event_msg" and payload_type in ("user_message", "agent_message"):
                    if payload_type == "user_message":
                        user_count += 1
                    else:
                        assistant_count += 1
                    if timestamp is None:
                        timestamp = data.get("timestamp", "")
                    if not first_message:
                        first_message = payload.get("message", "").strip()
                    continue

                if record_type == "response_item" and payload_type == "message" and not use_event_msgs:
                    role = payload.get("role", "")
                    if role == "user":
                        user_count += 1
                    elif role == "assistant":
                        assistant_count += 1
                    if timestamp is None:
                        timestamp = data.get("timestamp", "")
                    if not first_message:
                        first_message = _extract_text_from_codex_content(payload.get("content", [])).strip()
    except (OSError, UnicodeDecodeError):
        pass

    return {
        "timestamp": timestamp or "",
        "first_message": first_message,
        "user_count": user_count,
        "assistant_count": assistant_count,
    }


def discover_sessions(path_filter=None):
    codex_dir = Path.home() / ".codex" / "sessions"
    if not codex_dir.is_dir():
        return []

    sessions = []
    for jsonl_file in codex_dir.rglob("rollout-*.jsonl"):
        if not jsonl_file.is_file():
            continue
        path_str = str(jsonl_file)
        if path_filter and path_filter.lower() not in path_str.lower():
            continue
        file_stat = jsonl_file.stat()
        rel = ""
        try:
            rel_path = jsonl_file.relative_to(codex_dir)
            parts = rel_path.parts
            if len(parts) >= 3:
                rel = "/".join(parts[:3])
        except ValueError:
            rel = ""
        sessions.append({
            "path": path_str,
            "folder": rel,
            "size": file_stat.st_size,
            "mtime": file_stat.st_mtime,
        })

    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    sessions = [s for s in sessions if s["size"] > 1024]
    return sessions


def select_session(sessions):
    if not sessions:
        print("No sessions found in ~/.codex/sessions/")
        raise SystemExit(1)

    filtered_sessions = []
    previews = []
    for session in sessions:
        use_event_msgs = _codex_has_event_messages(session["path"])
        preview = _extract_preview(session["path"], use_event_msgs)
        total = preview["user_count"] + preview["assistant_count"]
        if total == 0:
            continue
        filtered_sessions.append(session)
        previews.append(preview)
    sessions = filtered_sessions

    if not sessions:
        print("No sessions with messages found in ~/.codex/sessions/")
        raise SystemExit(1)

    print()
    print(f"  {'#':>3}  {'Date':16}  {'Folder':10}  {'Size':>6}  {'Msgs':>5}  First message")
    print(f"  {'─' * 3}  {'─' * 16}  {'─' * 10}  {'─' * 6}  {'─' * 5}  {'─' * 55}")

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

        size_display = _format_size(session["size"])
        total_msgs = preview["user_count"] + preview["assistant_count"]
        folder = session.get("folder") or "-"
        if len(folder) > 10:
            folder = folder[:8] + ".."

        first_msg = preview["first_message"].replace("\n", " ")
        if len(first_msg) > 80:
            first_msg = first_msg[:78] + ".."

        print(f"  {idx:>3}  {date_display:16}  {folder:10}  {size_display:>6}  {total_msgs:>5}  {first_msg}")

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


def build_model(input_path):
    model = {
        "source": os.path.basename(input_path),
        "agent": "codex",
        "messages": [],
    }

    use_event_msgs = _codex_has_event_messages(input_path)

    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            record_type = data.get("type", "")
            payload = data.get("payload", {})
            payload_type = payload.get("type")

            if record_type == "event_msg":
                if payload_type == "user_message":
                    text = payload.get("message", "").strip()
                    if text:
                        model["messages"].append({
                            "role": "user",
                            "text": text,
                            "tool_uses": [],
                            "tool_results": [],
                        })
                elif payload_type == "agent_message":
                    text = payload.get("message", "").strip()
                    if text:
                        model["messages"].append({
                            "role": "assistant",
                            "text": text,
                            "tool_uses": [],
                            "tool_results": [],
                        })
                continue

            if record_type != "response_item":
                continue

            if payload_type == "message":
                if use_event_msgs:
                    continue
                role = payload.get("role", "")
                if role not in ("user", "assistant"):
                    continue
                text = _extract_text_from_codex_content(payload.get("content", [])).strip()
                if text:
                    model["messages"].append({
                        "role": role,
                        "text": text,
                        "tool_uses": [],
                        "tool_results": [],
                    })
                continue

            if payload_type in ("function_call", "custom_tool_call"):
                tool_use = _codex_tool_use_from_function_call(payload)
                if tool_use:
                    model["messages"].append({
                        "role": "assistant",
                        "text": "",
                        "tool_uses": [tool_use],
                        "tool_results": [],
                    })
                continue

            if payload_type in ("function_call_output", "custom_tool_call_output"):
                output = payload.get("output", "")
                if output:
                    model["messages"].append({
                        "role": "assistant",
                        "text": "",
                        "tool_uses": [],
                        "tool_results": [{"content": output}],
                    })
                continue

    return model


def main():
    parser = argparse.ArgumentParser(description="Convert Codex JSONL to common log model")
    parser.add_argument("input", nargs="?", default=None, help="input JSONL file path (omit to select)")
    parser.add_argument("-o", "--output", help="output JSON file path")
    parser.add_argument("--filter", default=None, help="filter sessions by path substring")
    args = parser.parse_args()

    input_path = args.input
    if input_path is None:
        sessions = discover_sessions(path_filter=args.filter)
        input_path = select_session(sessions)

    model = build_model(input_path)

    if args.output:
        output_path = args.output
    else:
        output_path = os.path.splitext(input_path)[0] + ".model.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, indent=2)

    print(f"Converted {len(model['messages'])} messages -> {output_path}")


if __name__ == "__main__":
    main()
