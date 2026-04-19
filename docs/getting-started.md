[日本語版](getting-started-ja.md)

# Getting Started

This guide walks through installation and your first session replay for each supported agent.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Capturing a Session](#capturing-a-session)
  - [Claude Code](#claude-code)
  - [OpenAI Codex CLI](#openai-codex-cli)
  - [Gemini CLI](#gemini-cli)
  - [Aider](#aider)
  - [Cursor](#cursor)
- [Rendering](#rendering)
- [Next Steps](#next-steps)

---

## Prerequisites

- Python 3.6 or later
- At least one supported AI coding agent installed and used at least once (to have log files)

No package manager (`pyproject.toml` does not exist — this project is not published to PyPI).

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/opaopa6969/claude-session-replay.git
cd claude-session-replay

# 2. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. (Optional) Install extras for Web UI, MP4, GIF, PDF
python3 -m pip install flask playwright pillow

# FFmpeg for MP4 / GIF output
# Ubuntu/Debian: sudo apt-get install ffmpeg
# macOS:         brew install ffmpeg
# Windows:       choco install ffmpeg

python3 -m playwright install
```

The core CLI features (Markdown, HTML, Player, Terminal output) work with zero additional packages.

---

## Capturing a Session

### Claude Code

Claude Code automatically writes session logs to:

```
~/.claude/projects/<project-path>/*.jsonl
```

To convert a Claude session:

```bash
# Auto-discover and pick from list
python3 log-replay.py --agent claude -f player

# Specify a file explicitly
python3 claude-log2model.py ~/.claude/projects/my-project/session.jsonl \
    -o session.model.json
```

Filter by project name:

```bash
python3 log-replay.py --agent claude --project my-project -f player
```

### OpenAI Codex CLI

Codex CLI writes logs to:

```
~/.codex/sessions/<nested-path>/*.jsonl
```

```bash
# Auto-discover
python3 log-replay.py --agent codex -f player

# Explicit file
python3 codex-log2model.py ~/.codex/sessions/my-dir/session.jsonl \
    -o session.model.json

# Filter by path substring
python3 log-replay.py --agent codex --filter my-project -f html
```

### Gemini CLI

Gemini CLI writes logs to:

```
~/.gemini/tmp/<project-dir>/chats/session-*.json
```

```bash
# Auto-discover
python3 log-replay.py --agent gemini -f player

# Explicit file
python3 gemini-log2model.py ~/.gemini/tmp/my-project/chats/session-001.json \
    -o session.model.json
```

### Aider

Aider writes conversation history to `.aider.chat.history.md` in the working directory.

```bash
# Explicit file (Aider does not have a central log directory)
python3 aider-log2model.py /path/to/project/.aider.chat.history.md \
    -o session.model.json

# Then render
python3 log-model-renderer.py session.model.json -f player -o out.html
```

### Cursor

Cursor stores conversation data in SQLite databases under:

```
~/.cursor/                           (Linux)
~/.config/Cursor/                    (Linux alt)
~/Library/Application Support/Cursor/ (macOS)
%APPDATA%/Cursor/                    (Windows)
```

```bash
# Auto-discover (searches known paths)
python3 log-replay.py --agent cursor -f player

# Explicit run (adapter scans automatically)
python3 cursor-log2model.py -o session.model.json
```

---

## Rendering

Once you have a `session.model.json` (or use the `log-replay.py` wrapper), render to any format:

```bash
# Markdown — plain text
python3 log-model-renderer.py session.model.json -f md -o session.md

# HTML — static chat UI (light or dark theme)
python3 log-model-renderer.py session.model.json -f html -t light -o session.html
python3 log-model-renderer.py session.model.json -f html -t console -o session-dark.html

# Player — interactive replay with Alibai Mode
python3 log-model-renderer.py session.model.json -f player -o session.player.html

# Terminal — Claude Code UI replica
python3 log-model-renderer.py session.model.json -f terminal -o session.terminal.html

# MP4 — requires playwright + ffmpeg
python3 log-replay-mp4.py --agent claude session.jsonl -o out.mp4 \
    --width 1280 --height 720 --fps 30 --speed 2.0

# PDF — requires playwright
python3 log-replay-pdf.py --agent claude session.jsonl -o out.pdf

# GIF — requires playwright + pillow (or ffmpeg)
python3 log-replay-gif.py --agent claude session.jsonl -o out.gif
```

### Web UI

```bash
source .venv/bin/activate
python3 web_ui.py
# Open http://localhost:5000 in your browser
```

The Web UI auto-discovers sessions from all supported agents and provides a graphical interface for format selection, theme, range, and Alibai Mode.

---

## Next Steps

- [Agents](agents.md) — log formats, adapter details, known limitations per agent
- [Renderers](renderers.md) — full option reference for each output format
- [Architecture](architecture.md) — how the three-stage pipeline works
- [Data Model](data-model.md) — common model JSON schema
