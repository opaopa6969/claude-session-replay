[日本語版](README.md)

# claude-session-replay

Record, convert, and replay AI coding agent sessions through a **three-stage pipeline: capture → normalize → render**.

> 5 agents, 7 renderers, 1 common model.

---

## Table of Contents

- [Why it exists](#why-it-exists)
- [Three-Stage Pipeline](#three-stage-pipeline)
- [Supported Agents](#supported-agents)
- [Supported Renderers](#supported-renderers)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Usage](#usage)
- [Web UI](#web-ui)
- [Keyboard Shortcuts](#keyboard-shortcuts)
- [Requirements](#requirements)
- [Notes](#notes)
- [Documentation](#documentation)

---

## Why it exists

Claude Code, Codex, Gemini CLI, Aider, and Cursor each store session logs in incompatible formats. This tool normalizes them into a **common model** and renders to any output format:

- Share a session with your team → HTML or Markdown
- Create a screencast → MP4 or GIF
- Keep a timestamped audit trail → Alibai Mode (player with analog clock)
- Browse sessions in a browser → Web UI

---

## Three-Stage Pipeline

```
┌─────────────────────────────────────────────────────────┐
│  Stage 1: Capture (Agent Adapters)                      │
│  Read each agent's log; produce the common model        │
│  claude-log2model.py / codex-log2model.py / ...         │
└────────────────────┬────────────────────────────────────┘
                     │ common model JSON
┌────────────────────▼────────────────────────────────────┐
│  Stage 2: Normalize (Common Model)                      │
│  {source, agent, messages[{role, text, tool_uses,       │
│   tool_results, thinking, timestamp}]}                  │
└────────────────────▼────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────┐
│  Stage 3: Render (log-model-renderer.py)                │
│  md / html / player / terminal / MP4 / PDF / GIF        │
└─────────────────────────────────────────────────────────┘
```

Each stage can be run independently. `log-replay.py` is a convenience wrapper that runs the full pipeline.

---

## Supported Agents

| Agent | Adapter | Log location |
|-------|---------|-------------|
| **Claude Code** | `claude-log2model.py` | `~/.claude/projects/*/*.jsonl` |
| **OpenAI Codex CLI** | `codex-log2model.py` | `~/.codex/sessions/**/*.jsonl` |
| **Gemini CLI** | `gemini-log2model.py` | `~/.gemini/tmp/*/chats/session-*.json` |
| **Aider** | `aider-log2model.py` | `.aider.chat.history.md` |
| **Cursor** | `cursor-log2model.py` | `~/.cursor/` (SQLite) |

See [docs/agents.md](docs/agents.md) for log format details and adapter behavior.

---

## Supported Renderers

| Format | Flag | Description |
|--------|------|-------------|
| **Markdown** | `md` | Plain text, no dependencies |
| **HTML** | `html` | Static chat UI, no dependencies |
| **Player** | `player` | Interactive player with Alibai Mode |
| **Terminal** | `terminal` | Replica of Claude Code's terminal UI |
| **MP4** | *(log-replay-mp4.py)* | Video via Playwright + FFmpeg |
| **PDF** | *(log-replay-pdf.py)* | PDF via Playwright |
| **GIF** | *(log-replay-gif.py)* | Animated GIF via Playwright + Pillow |

See [docs/renderers.md](docs/renderers.md) for full details.

---

## Quick Start

```bash
# 1. Setup
python3 -m venv .venv && source .venv/bin/activate

# 2. Open a Claude session as a player (omit file to pick from list)
python3 log-replay.py --agent claude -f player

# 3. Convert a Codex session to HTML
python3 log-replay.py --agent codex -f html -t light

# 4. Manual pipeline
python3 claude-log2model.py session.jsonl -o session.model.json
python3 log-model-renderer.py session.model.json -f player -o out.html
```

---

## Installation

### Basic (CLI only, no external dependencies)

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
# No additional packages needed — standard library only
```

> **Note**: There is no `pyproject.toml`. This project is not published to PyPI. Run scripts directly inside a venv.

### Web UI + MP4 / GIF / PDF support

```bash
source .venv/bin/activate
python3 -m pip install flask playwright pillow

# FFmpeg (required for MP4 / GIF)
# Ubuntu/Debian: sudo apt-get install ffmpeg
# macOS:         brew install ffmpeg
# Windows:       choco install ffmpeg

python3 -m playwright install
```

---

## Usage

### CLI wrapper (recommended)

```bash
python3 log-replay.py --agent claude -f player          # Claude → Player
python3 log-replay.py --agent codex  -f terminal        # Codex  → Terminal
python3 log-replay.py --agent gemini -f player          # Gemini → Player
python3 log-replay.py --agent aider  -f html -t light   # Aider  → HTML Light
python3 log-replay.py --agent cursor -f md              # Cursor → Markdown
```

Omit the input file to auto-discover sessions and pick from an interactive list.

**Key options**:

| Option | Description |
|--------|-------------|
| `-f` / `--format` | `md` / `html` / `player` / `terminal` |
| `-t` / `--theme` | `light` / `console` |
| `-o` / `--output` | Output file path |
| `--project` | Filter by Claude project name |
| `--filter` | Filter by Codex path |
| `--range` | Message range (e.g., `1-50,53-`) |

### Manual pipeline

```bash
# Step 1: agent log → common model
python3 claude-log2model.py  session.jsonl          -o session.model.json
python3 codex-log2model.py   session.jsonl          -o session.model.json
python3 gemini-log2model.py  session.json           -o session.model.json
python3 aider-log2model.py   .aider.chat.history.md -o session.model.json
python3 cursor-log2model.py                         -o session.model.json

# Step 2: common model → output
python3 log-model-renderer.py session.model.json -f player
python3 log-model-renderer.py session.model.json -f html   -t console
python3 log-model-renderer.py session.model.json -f terminal

# MP4 / GIF / PDF
python3 log-replay-mp4.py --agent claude session.jsonl -o out.mp4 --width 1280 --height 720 --fps 30 --speed 2.0
python3 log-replay-gif.py --agent claude session.jsonl -o out.gif
python3 log-replay-pdf.py --agent claude session.jsonl -o out.pdf
```

### Message range

```bash
python3 log-model-renderer.py session.model.json -f player --range "1-50,53-"
```

| Syntax | Meaning |
|--------|---------|
| `1-50` | Messages 1–50 |
| `53-` | Message 53 to end |
| `-10` | Messages 1–10 |
| `7` | Message 7 only |

### ANSI mode

```bash
python3 log-model-renderer.py session.model.json -f terminal --ansi-mode strip  # remove (default)
python3 log-model-renderer.py session.model.json -f terminal --ansi-mode color  # convert to HTML color
```

---

## Web UI

```bash
source .venv/bin/activate
python3 web_ui.py
# → http://localhost:5000
```

Browse, convert, and replay sessions in a browser. Includes Alibai Mode (timestamp visualization with analog clock).

---

## Keyboard Shortcuts (player / terminal)

| Key | Action |
|-----|--------|
| `Space` | Play / Pause |
| `→` | Next message |
| `←` | Previous message |
| `Home` | Jump to start |
| `End` | Jump to end |
| `g` | Jump to timestamp |
| `j` / `k` | Scroll |
| `T` | Skip tool messages during playback |
| `E` | Toggle empty tool visibility |
| `D` | Toggle tool details |

Speed slider: 0.25x–16x.

---

## Requirements

| Feature | Requires |
|---------|---------|
| Basic CLI | Python 3.6+, no external packages |
| Web UI | `flask`, `playwright` |
| MP4 output | `playwright`, `ffmpeg` |
| GIF output | `playwright`, `pillow` (or `ffmpeg`) |
| PDF output | `playwright` |

---

## Notes

- **No `pyproject.toml`**: This project is not published to PyPI. Run scripts directly inside a venv — no `pip install claude-session-replay`.
- **`session-shipper.py` redaction**: The `redact_pii` flag in `session-shipper.py` has not been thoroughly tested. Verify behavior before using in production environments.

---

## Documentation

- [Architecture](docs/architecture.md) | [日本語](docs/architecture-ja.md) — Pipeline internals
- [Getting Started](docs/getting-started.md) | [日本語](docs/getting-started-ja.md) — Install and first run
- [Agents](docs/agents.md) | [日本語](docs/agents-ja.md) — Per-agent log formats
- [Renderers](docs/renderers.md) | [日本語](docs/renderers-ja.md) — Output format details
- [Data Model](docs/data-model.md) — Common model JSON schema
- [Changelog](CHANGELOG.md) — Release history
