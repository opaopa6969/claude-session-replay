#!/bin/bash
# Run Web UI for Claude Session Replay
# Automatically creates venv and installs dependencies

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python3"

# Create virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

# Activate venv and install/upgrade dependencies
echo "📥 Installing dependencies..."
"$VENV_PYTHON" -m pip install --upgrade pip flask playwright > /dev/null 2>&1

if [ $? -ne 0 ]; then
    echo "❌ Failed to install dependencies"
    exit 1
fi

echo ""
echo "✅ Web UI is starting at http://localhost:5000"
echo "   Press Ctrl+C to stop"
echo ""

# Start the Web UI
"$VENV_PYTHON" "$SCRIPT_DIR/web_ui.py"
