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
    return render_template('index.html')


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

        if agent == "claude":
            messages = claude_log2model._extract_preview_messages(session_path, count=50)
        elif agent == "codex":
            messages = codex_log2model._extract_preview_messages(session_path, count=50)
        else:
            return jsonify({"error": "Invalid agent"}), 400

        preview_text = ""
        for msg in messages:
            role = msg["role"].upper()
            text = msg["text"][:200].replace("\n", " ")
            if len(msg["text"]) > 200:
                text += "..."
            preview_text += f"[{role}] {text}\n\n"

        return jsonify({"preview": preview_text})
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
