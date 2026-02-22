#!/bin/bash
# Run Web UI for Claude Session Replay

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python3"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "Error: Virtual environment not found."
    echo "Please run: python3 -m venv .venv && source .venv/bin/activate && pip install flask"
    exit 1
fi

echo "Starting Web UI at http://localhost:5000"
"$VENV_PYTHON" "$SCRIPT_DIR/web_ui.py"
