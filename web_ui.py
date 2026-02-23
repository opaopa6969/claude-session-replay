#!/usr/bin/env python3
"""Web UI for Claude Session Replay."""

import os
import sys
import json
import subprocess
import tempfile
import importlib.util
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template, jsonify, request, send_file, send_from_directory

# Import log2model modules
def _import_module(name, filepath):
    spec = importlib.util.spec_from_file_location(name, filepath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

script_dir = Path(__file__).parent
claude_log2model = _import_module("claude_log2model", str(script_dir / "claude-log2model.py"))
codex_log2model = _import_module("codex_log2model", str(script_dir / "codex-log2model.py"))

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max


def _format_size(size_bytes):
    """Format bytes to human-readable size."""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}".replace(".0", "")
        size_bytes /= 1024
    return f"{size_bytes:.1f}TB".replace(".0", "")


def _has_tool_blocks(content):
    """Check if content has tool_use or tool_result blocks."""
    if isinstance(content, str):
        return False
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") in ("tool_use", "tool_result"):
                return True
    return False


def _extract_thinking_from_content(content):
    """Extract thinking blocks from content list."""
    if not isinstance(content, list):
        return []
    return [block.get("thinking", "") for block in content
            if isinstance(block, dict) and block.get("type") == "thinking"]


def _extract_tool_uses_from_content(content):
    """Extract tool_use blocks from content list."""
    if not isinstance(content, list):
        return []
    return [block for block in content
            if isinstance(block, dict) and block.get("type") == "tool_use"]


def _extract_tool_results_from_content(content):
    """Extract tool_result blocks from content list."""
    if not isinstance(content, list):
        return []
    return [block for block in content
            if isinstance(block, dict) and block.get("type") == "tool_result"]


def _extract_all_messages_for_editor(jsonl_path, agent):
    """Extract all messages with line indices for editor."""
    messages = []
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line_idx, line in enumerate(f):
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if agent == "claude":
                    record_type = data.get("type", "")

                    if record_type == "user":
                        message = data.get("message", {})
                        role = message.get("role", "")
                        content = message.get("content", "")
                        text = claude_log2model._extract_text_from_content(content)
                        tool_results = _extract_tool_results_from_content(content)
                        has_tools = _has_tool_blocks(content)

                        messages.append({
                            "idx": len(messages) + 1,
                            "lineIdx": line_idx,
                            "blockType": "user",
                            "role": role,
                            "text": text or "",
                            "thinking": [],
                            "tool_results": tool_results,
                            "hasTools": has_tools,
                            "isReadOnly": False
                        })

                    elif record_type == "assistant":
                        message = data.get("message", {})
                        role = message.get("role", "")
                        content = message.get("content", "")
                        text = claude_log2model._extract_text_from_content(content)
                        thinking = _extract_thinking_from_content(content)
                        tool_uses = _extract_tool_uses_from_content(content)
                        tool_results = _extract_tool_results_from_content(content)
                        has_tools = _has_tool_blocks(content)

                        messages.append({
                            "idx": len(messages) + 1,
                            "lineIdx": line_idx,
                            "blockType": "assistant",
                            "role": role,
                            "text": text or "",
                            "thinking": thinking,
                            "tool_uses": tool_uses,
                            "tool_results": tool_results,
                            "hasTools": has_tools,
                            "isReadOnly": False
                        })

                    elif record_type == "progress":
                        messages.append({
                            "idx": len(messages) + 1,
                            "lineIdx": line_idx,
                            "blockType": "progress",
                            "role": "progress",
                            "text": data.get("message", ""),
                            "thinking": [],
                            "hasTools": False,
                            "isReadOnly": True
                        })

                    elif record_type == "file-history-snapshot":
                        messages.append({
                            "idx": len(messages) + 1,
                            "lineIdx": line_idx,
                            "blockType": "file-history",
                            "role": "file-history",
                            "text": f"File snapshot: {len(data.get('paths', []))} files",
                            "thinking": [],
                            "hasTools": False,
                            "isReadOnly": True
                        })

                elif agent == "codex":
                    record_type = data.get("type", "")
                    payload = data.get("payload", {})
                    payload_type = payload.get("type", "")

                    # Check if codex has event_messages mode
                    use_event_msgs = codex_log2model._codex_has_event_messages(jsonl_path)

                    if use_event_msgs and record_type == "event_msg":
                        if payload_type == "user_message":
                            text = payload.get("message", "")
                            messages.append({
                                "idx": len(messages) + 1,
                                "lineIdx": line_idx,
                                "blockType": "user",
                                "role": "user",
                                "text": text or "",
                                "thinking": [],
                                "tool_results": [],
                                "hasTools": False,
                                "isReadOnly": False
                            })
                        elif payload_type == "agent_message":
                            text = payload.get("message", "")
                            messages.append({
                                "idx": len(messages) + 1,
                                "lineIdx": line_idx,
                                "blockType": "assistant",
                                "role": "assistant",
                                "text": text or "",
                                "thinking": [],
                                "hasTools": False,
                                "isReadOnly": False
                            })
                    elif not use_event_msgs and record_type == "response_item":
                        if payload_type == "message":
                            role = payload.get("role", "")
                            if role not in ("user", "assistant"):
                                continue
                            content = payload.get("content", [])
                            text = codex_log2model._extract_text_from_codex_content(content)
                            thinking = codex_log2model._extract_thinking_from_codex_content(content)
                            tool_uses = _extract_tool_uses_from_content(content)
                            tool_results = _extract_tool_results_from_content(content)
                            has_tools = _has_tool_blocks(content)

                            messages.append({
                                "idx": len(messages) + 1,
                                "lineIdx": line_idx,
                                "blockType": role,
                                "role": role,
                                "text": text or "",
                                "thinking": thinking,
                                "tool_uses": tool_uses,
                                "tool_results": tool_results,
                                "hasTools": has_tools,
                                "isReadOnly": False
                            })
    except (OSError, UnicodeDecodeError):
        pass

    return messages


def _update_jsonl_text(line_data, agent, new_text):
    """Update text in a jsonl line record. Returns modified record."""
    data = dict(line_data)  # shallow copy

    if agent == "claude":
        message = data.get("message", {})
        content = message.get("content", "")

        if isinstance(content, str):
            # Simple string content - replace entirely
            message["content"] = new_text
        elif isinstance(content, list):
            # List of content blocks - update text blocks, preserve tool blocks
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    block["text"] = new_text
                    break  # Only update first text block

        data["message"] = message

    elif agent == "codex":
        payload = data.get("payload", {})
        payload_type = payload.get("type", "")

        if payload_type == "user_message" or payload_type == "agent_message":
            # event_msg mode
            payload["message"] = new_text
        elif payload_type == "message":
            # response_item mode - update text blocks in content
            content = payload.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        block["text"] = new_text
                        break

        data["payload"] = payload

    return data


def _apply_alibai_offset(model_path, alibai_time):
    """Apply Alibai time offset to model timestamps (UTC-based)."""
    from datetime import datetime, timedelta

    # Parse alibai_time (HH:MM format)
    try:
        h, m = map(int, alibai_time.split(':'))
    except:
        raise ValueError("Invalid time format. Use HH:MM (e.g., 21:33)")

    # Load model
    with open(model_path, 'r', encoding='utf-8') as f:
        model = json.load(f)

    # Find first timestamp
    first_timestamp = None
    for msg in model.get("messages", []):
        if msg.get("timestamp"):
            first_timestamp = msg["timestamp"]
            break

    if not first_timestamp:
        # No timestamps in model, nothing to do
        return

    # Parse first timestamp (already in UTC)
    try:
        first_dt = datetime.fromisoformat(first_timestamp.replace('Z', '+00:00'))
    except:
        return

    # Create target datetime: same date as first message, but with specified time (in UTC)
    alibai_dt = first_dt.replace(hour=h, minute=m, second=0, microsecond=0)

    # Calculate offset (in UTC)
    offset = alibai_dt - first_dt

    # Apply offset to all timestamps
    for msg in model.get("messages", []):
        if msg.get("timestamp"):
            try:
                ts_dt = datetime.fromisoformat(msg["timestamp"].replace('Z', '+00:00'))
                new_ts = (ts_dt + offset).isoformat().replace('+00:00', '') + 'Z'
                msg["timestamp"] = new_ts
            except:
                pass

    # Save modified model
    with open(model_path, 'w', encoding='utf-8') as f:
        json.dump(model, f)


@app.route('/')
def index():
    """Render main page."""
    accept_lang = request.headers.get('Accept-Language', '')
    lang = 'ja' if 'ja' in accept_lang else 'en'
    return render_template('index.html', lang=lang)


@app.route('/api/sessions/<agent>')
def get_sessions(agent):
    """Get list of sessions for agent."""
    try:
        if agent == "claude":
            sessions = claude_log2model.discover_sessions()
        elif agent == "codex":
            sessions = codex_log2model.discover_sessions()
        else:
            return jsonify({"error": "Invalid agent"}), 400

        session_list = []
        for session in sessions:
            if agent == "claude":
                preview = claude_log2model._extract_preview(session["path"])
            else:
                use_event_msgs = codex_log2model._codex_has_event_messages(session["path"])
                preview = codex_log2model._extract_preview(session["path"], use_event_msgs)

            total = preview.get("user_count", 0) + preview.get("assistant_count", 0)
            if total > 0:
                mtime = session.get("mtime", 0)
                dt = datetime.fromtimestamp(mtime)
                date_str = dt.strftime("%Y-%m-%d %H:%M")

                first_msg = preview.get("first_message", "")[:80]
                project = session.get("project", first_msg or "Unknown")

                session_list.append({
                    "path": session["path"],
                    "project": project,
                    "size": session.get("size", 0),
                    "mtime": mtime,
                    "date_str": date_str,
                    "size_str": _format_size(session.get("size", 0)),
                    "first_message": first_msg,
                })

        return jsonify({"sessions": session_list})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/preview/<agent>', methods=['POST'])
def get_preview(agent):
    """Get preview messages for a session."""
    try:
        data = request.json
        session_path = data.get("path")

        if not session_path:
            return jsonify({"error": "No path provided"}), 400

        if agent not in ("claude", "codex"):
            return jsonify({"error": "Invalid agent"}), 400

        # Use editor-content to get ALL messages for preview
        messages = _extract_all_messages_for_editor(session_path, agent)

        preview_text = ""
        for msg in messages:
            # Get block type for label
            block_type = msg.get("blockType", msg.get("role", "unknown")).upper()
            preview_text += f"#{msg['idx']} [{block_type}]"

            # Add hasTools badge if present
            if msg.get("hasTools"):
                preview_text += " 🔧"

            preview_text += "\n"

            # Show thinking blocks if present
            thinking = msg.get("thinking", [])
            if thinking:
                for i, thought in enumerate(thinking):
                    if thought.strip():
                        thought_preview = thought.strip()[:100].replace("\n", " ")
                        if len(thought.strip()) > 100:
                            thought_preview += "..."
                        preview_text += f"  💭 思考: {thought_preview}\n"

            # Show tool_uses if present
            tool_uses = msg.get("tool_uses", [])
            if tool_uses:
                for tool_use in tool_uses:
                    tool_name = tool_use.get("name", "Unknown")
                    preview_text += f"  🔧 {tool_name}\n"

            # Show tool_results if present
            tool_results = msg.get("tool_results", [])
            if tool_results:
                for result in tool_results:
                    result_content = result.get("content", "")
                    if isinstance(result_content, str):
                        preview_text += f"  📋 Result: {result_content[:100]}\n"

            # Show text or blockType-specific info
            if msg.get("blockType") == "progress":
                # Progress messages: show with icon
                if msg["text"]:
                    preview_text += f"  ⏳ {msg['text'][:150]}\n"
            elif msg.get("blockType") == "file-history":
                # File-history messages: show with icon
                if msg["text"]:
                    preview_text += f"  📁 {msg['text'][:150]}\n"
            else:
                # User/Assistant messages: show text
                if msg["text"]:
                    text = msg["text"][:200].replace("\n", " ")
                    if len(msg["text"]) > 200:
                        text += "..."
                    preview_text += f"  {text}\n"

            preview_text += "\n"

        return jsonify({"preview": preview_text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/editor-content/<agent>', methods=['POST'])
def get_editor_content(agent):
    """Get all messages with line indices for editor."""
    try:
        data = request.json
        session_path = data.get("path")

        if not session_path:
            return jsonify({"error": "No path provided"}), 400

        if agent not in ("claude", "codex"):
            return jsonify({"error": "Invalid agent"}), 400

        messages = _extract_all_messages_for_editor(session_path, agent)
        return jsonify({"messages": messages})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/apply-to-output', methods=['POST'])
def apply_to_output():
    """Apply edits to a temp jsonl and convert."""
    try:
        data = request.json
        agent = data.get("agent")
        session_path = data.get("session_path")
        format_type = data.get("format")
        theme = data.get("theme")
        range_filter = data.get("range")
        alibai_time = data.get("alibai_time")
        edits = data.get("edits", [])
        filters = data.get("filters", {})
        truncate_length = data.get("truncate_length")

        if not all([agent, session_path, format_type]):
            return jsonify({"error": "Missing required parameters"}), 400

        # Create a mapping of lineIdx -> edit
        edits_map = {e["lineIdx"]: e for e in edits}

        # Read original jsonl and apply edits
        edited_lines = []
        try:
            with open(session_path, "r", encoding="utf-8") as f:
                for line_idx, line in enumerate(f):
                    if line_idx in edits_map:
                        edit = edits_map[line_idx]
                        if edit.get("deleted"):
                            continue  # Skip deleted lines

                        # Parse and update text
                        try:
                            record = json.loads(line.strip())
                            record = _update_jsonl_text(record, agent, edit.get("text", ""))
                            edited_lines.append(json.dumps(record))
                        except json.JSONDecodeError:
                            edited_lines.append(line.strip())
                    else:
                        edited_lines.append(line.strip())
        except Exception as e:
            return jsonify({"error": f"Failed to read session: {str(e)}"}), 500

        # Write to temp jsonl
        fd, temp_jsonl = tempfile.mkstemp(prefix="edited-", suffix=".jsonl")
        try:
            os.write(fd, "\n".join(edited_lines).encode("utf-8"))
            os.close(fd)

            # Create temporary model file
            fd_model, model_path = tempfile.mkstemp(prefix="log-model-", suffix=".json")
            os.close(fd_model)

            try:
                # Convert edited jsonl to model
                if agent == "claude":
                    log2model = "claude-log2model.py"
                elif agent == "codex":
                    log2model = "codex-log2model.py"
                else:
                    return jsonify({"error": "Invalid agent"}), 400

                log_cmd = [sys.executable, str(script_dir / log2model), temp_jsonl, "-o", model_path]
                result = subprocess.run(log_cmd, capture_output=True, text=True, timeout=60)
                if result.returncode != 0:
                    return jsonify({"error": f"Log conversion failed: {result.stderr}"}), 500

                # Apply Alibai time offset if specified
                if alibai_time and format_type == "player":
                    try:
                        _apply_alibai_offset(model_path, alibai_time)
                    except Exception as e:
                        return jsonify({"error": f"Alibai time error: {str(e)}"}), 500

                # Render to temp file
                fd_render, render_output_path = tempfile.mkstemp(suffix=f".{format_type}")
                os.close(fd_render)

                try:
                    render_cmd = [
                        sys.executable, str(script_dir / "log-model-renderer.py"), model_path,
                        "-f", format_type,
                        "-t", theme or "light",
                        "-o", render_output_path
                    ]

                    if range_filter:
                        render_cmd.extend(["-r", range_filter])

                    # Add filters as JSON
                    if filters:
                        filters_json = json.dumps(filters)
                        render_cmd.extend(["--filters", filters_json])

                    # Add truncate length
                    if truncate_length is not None:
                        render_cmd.extend(["--truncate", str(truncate_length)])

                    result = subprocess.run(render_cmd, capture_output=True, text=True, timeout=60)
                    if result.returncode != 0:
                        return jsonify({"error": f"Rendering failed: {result.stderr}"}), 500

                    # Read output
                    with open(render_output_path, 'r', encoding='utf-8') as f:
                        output_content = f.read()

                    return jsonify({
                        "success": True,
                        "format": format_type,
                        "content": output_content
                    })

                finally:
                    try:
                        os.remove(render_output_path)
                    except:
                        pass

            finally:
                try:
                    os.remove(model_path)
                except:
                    pass

        finally:
            try:
                os.remove(temp_jsonl)
            except:
                pass

    except subprocess.TimeoutExpired:
        return jsonify({"error": "Conversion timeout"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/apply-to-session-log', methods=['POST'])
def _create_backup(file_path):
    """Create a backup of the file with numbered naming.

    If backup.jsonl exists, create backup.1.jsonl, backup.2.jsonl, etc.
    Returns the backup path.
    """
    try:
        path = Path(file_path)
        if not path.exists():
            return None

        # Determine backup name
        parent = path.parent
        stem = path.stem
        suffix = path.suffix

        # First backup is just "backup.jsonl"
        backup_path = parent / f"{stem}.backup{suffix}"
        if not backup_path.exists():
            # Copy file to backup location
            with open(path, 'rb') as src:
                with open(backup_path, 'wb') as dst:
                    dst.write(src.read())
            return str(backup_path)

        # If backup exists, find the next numbered backup
        counter = 1
        while True:
            numbered_backup = parent / f"{stem}.backup.{counter}{suffix}"
            if not numbered_backup.exists():
                with open(path, 'rb') as src:
                    with open(numbered_backup, 'wb') as dst:
                        dst.write(src.read())
                return str(numbered_backup)
            counter += 1
    except Exception as e:
        print(f"Warning: Failed to create backup: {e}", file=sys.stderr)
        return None


def apply_to_session_log():
    """Apply edits directly to the original jsonl file."""
    try:
        data = request.json
        agent = data.get("agent")
        session_path = data.get("session_path")
        alibai_time = data.get("alibai_time")
        edits = data.get("edits", [])

        if not all([agent, session_path]):
            return jsonify({"error": "Missing required parameters"}), 400

        # Create a mapping of lineIdx -> edit
        edits_map = {e["lineIdx"]: e for e in edits}

        # Read original jsonl, apply edits, and collect all records
        edited_records = []
        try:
            with open(session_path, "r", encoding="utf-8") as f:
                for line_idx, line in enumerate(f):
                    if line_idx in edits_map:
                        edit = edits_map[line_idx]
                        if edit.get("deleted"):
                            continue  # Skip deleted lines

                        try:
                            record = json.loads(line.strip())
                            record = _update_jsonl_text(record, agent, edit.get("text", ""))
                            edited_records.append(record)
                        except json.JSONDecodeError:
                            pass
                    else:
                        try:
                            record = json.loads(line.strip())
                            edited_records.append(record)
                        except json.JSONDecodeError:
                            pass
        except Exception as e:
            return jsonify({"error": f"Failed to read session: {str(e)}"}), 500

        # Apply Alibai time offset if specified
        if alibai_time:
            try:
                # Find first timestamp
                first_timestamp = None
                for record in edited_records:
                    ts = record.get("timestamp")
                    if ts:
                        first_timestamp = ts
                        break

                if first_timestamp:
                    # Parse alibai_time
                    try:
                        h, m = map(int, alibai_time.split(':'))
                    except:
                        return jsonify({"error": "Invalid time format. Use HH:MM"}), 400

                    # Parse first timestamp
                    try:
                        first_dt = datetime.fromisoformat(first_timestamp.replace('Z', '+00:00'))
                    except:
                        return jsonify({"error": "Cannot parse timestamps"}), 400

                    # Calculate offset
                    alibai_dt = first_dt.replace(hour=h, minute=m, second=0, microsecond=0)
                    offset = alibai_dt - first_dt

                    # Apply offset to all timestamps
                    for record in edited_records:
                        if record.get("timestamp"):
                            try:
                                ts_dt = datetime.fromisoformat(record["timestamp"].replace('Z', '+00:00'))
                                new_ts = (ts_dt + offset).isoformat().replace('+00:00', '') + 'Z'
                                record["timestamp"] = new_ts
                            except:
                                pass

            except Exception as e:
                return jsonify({"error": f"Alibai time error: {str(e)}"}), 500

        # Create backup before writing
        backup_path = _create_backup(session_path)

        # Write back to original file
        try:
            with open(session_path, "w", encoding="utf-8") as f:
                for record in edited_records:
                    f.write(json.dumps(record) + "\n")

            message = f"Session log updated: {session_path}"
            if backup_path:
                message += f"\nBackup created: {backup_path}"

            return jsonify({
                "success": True,
                "message": message
            })

        except Exception as e:
            return jsonify({"error": f"Failed to write session: {str(e)}"}), 500

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/convert', methods=['POST'])
def convert():
    """Convert and render session."""
    try:
        data = request.json
        agent = data.get("agent")
        session_path = data.get("session_path")
        format_type = data.get("format")
        theme = data.get("theme")
        range_filter = data.get("range")
        output_path = data.get("output")
        alibai_time = data.get("alibai_time")

        if not all([agent, session_path, format_type]):
            return jsonify({"error": "Missing required parameters"}), 400

        # Create temporary model file
        fd, model_path = tempfile.mkstemp(prefix="log-model-", suffix=".json")
        os.close(fd)

        try:
            # Step 1: Convert to model
            if agent == "claude":
                log2model = "claude-log2model.py"
            elif agent == "codex":
                log2model = "codex-log2model.py"
            else:
                return jsonify({"error": "Invalid agent"}), 400

            log_cmd = [sys.executable, str(script_dir / log2model), session_path, "-o", model_path]
            result = subprocess.run(log_cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                return jsonify({"error": f"Log conversion failed: {result.stderr}"}), 500

            # Apply Alibai time offset if specified
            if alibai_time and format_type == "player":
                try:
                    _apply_alibai_offset(model_path, alibai_time)
                except Exception as e:
                    return jsonify({"error": f"Alibai time error: {str(e)}"}), 500

            # Step 2: Render to temp file
            fd, render_output_path = tempfile.mkstemp(suffix=f".{format_type}")
            os.close(fd)

            try:
                render_cmd = [
                    sys.executable, str(script_dir / "log-model-renderer.py"), model_path,
                    "-f", format_type,
                    "-t", theme or "light",
                    "-o", render_output_path
                ]

                if range_filter:
                    render_cmd.extend(["-r", range_filter])

                # Run renderer
                result = subprocess.run(render_cmd, capture_output=True, text=True, timeout=60)
                if result.returncode != 0:
                    return jsonify({"error": f"Rendering failed: {result.stderr}"}), 500

                # Read generated output
                with open(render_output_path, 'r', encoding='utf-8') as f:
                    output_content = f.read()
            finally:
                # Cleanup render output temp file
                try:
                    os.remove(render_output_path)
                except:
                    pass

            # If output file is requested, write it
            if output_path:
                output_file = Path(output_path)
                output_file.parent.mkdir(parents=True, exist_ok=True)
                output_file.write_text(output_content)
                return jsonify({
                    "success": True,
                    "message": f"Output saved to {output_path}",
                    "download_url": f"/api/download/{output_file.name}"
                })

            # Otherwise, return content based on format
            if format_type in ["html", "player"]:
                return jsonify({
                    "success": True,
                    "format": format_type,
                    "content": output_content
                })
            else:
                # For md and terminal, return as text
                return jsonify({
                    "success": True,
                    "format": format_type,
                    "content": output_content
                })

        finally:
            # Cleanup temp model file
            try:
                os.remove(model_path)
            except:
                pass

    except subprocess.TimeoutExpired:
        return jsonify({"error": "Conversion timeout"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    print("Starting Web UI on http://localhost:5000")
    app.run(debug=True, host='localhost', port=5000)
