[日本語版](architecture-ja.md)

# Architecture

claude-session-replay uses a **three-stage pipeline** to decouple agent-specific log parsing from output rendering.

---

## Table of Contents

- [Overview](#overview)
- [Stage 1 — Capture (Agent Adapters)](#stage-1--capture-agent-adapters)
- [Stage 2 — Normalize (Common Model)](#stage-2--normalize-common-model)
- [Stage 3 — Render](#stage-3--render)
- [Entry Points](#entry-points)
- [Renderer Tree](#renderer-tree)
- [Dependency Model](#dependency-model)
- [File Layout](#file-layout)

---

## Overview

```
Vendor logs (agent-specific format)
  ├─ Claude Code   ~/.claude/projects/*/*.jsonl
  ├─ Codex CLI     ~/.codex/sessions/**/*.jsonl
  ├─ Gemini CLI    ~/.gemini/tmp/*/chats/session-*.json
  ├─ Aider         .aider.chat.history.md
  └─ Cursor        ~/.cursor/ (SQLite)
         │
         ▼  Stage 1: Capture (Agent Adapters)
  *-log2model.py scripts
         │
         ▼  Stage 2: Normalize
  Common Model JSON
  {source, agent, messages[{role, text, tool_uses, tool_results, thinking, timestamp}]}
         │
         ▼  Stage 3: Render
  Output
  ├─ Markdown     (.md,   static)
  ├─ HTML         (.html, static)
  ├─ Player       (.html, interactive + Alibai Mode)
  ├─ Terminal     (.html, interactive, Claude Code UI replica)
  ├─ MP4          (Playwright + FFmpeg)
  ├─ PDF          (Playwright)
  └─ GIF          (Playwright + Pillow or FFmpeg)
```

---

## Stage 1 — Capture (Agent Adapters)

Each adapter is an independent Python script that follows the same logical interface:

```python
def parse_messages(input_path: str) -> list[dict]
    """Read the log file; return raw message records."""

def build_model(messages: list[dict], input_path: str) -> dict
    """Transform raw messages into the common model dict."""

def discover_sessions(filter: str = None) -> list[dict]
    """Scan known filesystem locations; return session metadata list."""

def select_session(sessions: list[dict]) -> str
    """Interactive picker; return chosen file path."""
```

The contract is a convention, not an abstract base class.

### Adapter scripts

| Script | Agent | Input format |
|--------|-------|-------------|
| `claude-log2model.py` | Claude Code | JSONL, one record per line |
| `codex-log2model.py` | OpenAI Codex CLI | JSONL |
| `gemini-log2model.py` | Gemini CLI | JSON array |
| `aider-log2model.py` | Aider | Markdown (`.aider.chat.history.md`) |
| `cursor-log2model.py` | Cursor | SQLite databases |

See [agents.md](agents.md) for per-adapter log format details.

### Adding a new agent

1. Create `<agent>-log2model.py` implementing the four-function contract.
2. Register `--agent <name>` in `log-replay.py`.
3. Register in `web_ui.py` (import + session discovery).
4. No changes needed in the renderer.

---

## Stage 2 — Normalize (Common Model)

All adapters output the same JSON structure. Full schema in [data-model.md](data-model.md).

**Invariants**:
- `role` is always `"user"` or `"assistant"` regardless of source agent terminology.
- `timestamp` is ISO 8601, or empty string when unavailable.
- `source` is a basename — never an absolute path.
- Messages are in original chronological order.
- A message is included only when at least one of `text`, `tool_uses`, `tool_results`, or `thinking` is non-empty.

The common model is **agent-agnostic**. Any renderer consumes any model.

---

## Stage 3 — Render

`log-model-renderer.py` reads the common model and produces output. Format selected via `-f`.

| Format | Output type | Dependencies |
|--------|------------|-------------|
| `md` | Plain text | None |
| `html` | Static HTML | None |
| `player` | Self-contained HTML + JS | Browser |
| `terminal` | Self-contained HTML + JS | Browser |

Video/image renderers are separate scripts that render to HTML then drive a headless browser:

| Script | Renderer used | Output |
|--------|--------------|--------|
| `log-replay-mp4.py` | `player` or `terminal` | MP4 |
| `log-replay-pdf.py` | `html` or `player` | PDF |
| `log-replay-gif.py` | `player` or `terminal` | GIF |

---

## Entry Points

| Script | Role |
|--------|------|
| `log-replay.py` | CLI wrapper — selects adapter, pipes to renderer |
| `web_ui.py` | Flask browser UI — session management + live conversion |
| `log-model-renderer.py` | Direct renderer — reads common model, writes output |
| `session-shipper.py` | Enterprise — ships sessions to OpenSearch (batch/watch) |
| `session-stats.py` | Statistics reporter |

---

## Renderer Tree

```
log-model-renderer.py
├── render_markdown(model)
│   └── per message: heading + text + tool_uses + tool_results
├── render_html(model, theme)
│   └── inline CSS chat bubbles; no JS
├── render_player(model, theme)
│   ├── message stepper (Space / ← / →)
│   ├── speed slider (0.25x–16x)
│   ├── progress bar (click-to-seek)
│   ├── range filter (--range)
│   └── Alibai Mode
│       ├── side clocks   (44×44 px per message)
│       ├── fixed clock   (100×100 px, bottom-right)
│       └── playback modes: Uniform / Real-time / Compressed
└── render_terminal(model)
    ├── Claude Code UI replica
    ├── user prompt (> blue background)
    ├── assistant bar (orange left border)
    ├── tool blocks (Read/Write/Edit/Bash/Grep/Glob/Task)
    └── spinner animation (● → ✓)
```

---

## Dependency Model

```
Core (no external dependencies — Python 3.6+ stdlib only)
  claude-log2model.py
  codex-log2model.py
  gemini-log2model.py
  aider-log2model.py
  cursor-log2model.py
  log-model-renderer.py
  log-replay.py

Optional — Web UI
  web_ui.py           → flask

Optional — Headless recording
  log-replay-mp4.py   → playwright, ffmpeg (system binary)
  log-replay-pdf.py   → playwright
  log-replay-gif.py   → playwright, pillow (or ffmpeg)
```

> **Note**: There is no `pyproject.toml`. Optional dependencies must be installed manually into a venv.

Lazy imports ensure missing optional packages only cause errors at the feature boundary, not at startup.

---

## File Layout

```
claude-session-replay/
├── log-replay.py              # CLI wrapper
├── claude-log2model.py        # Capture: Claude Code
├── codex-log2model.py         # Capture: Codex CLI
├── gemini-log2model.py        # Capture: Gemini CLI
├── aider-log2model.py         # Capture: Aider
├── cursor-log2model.py        # Capture: Cursor
├── log-model-renderer.py      # Render: md/html/player/terminal
├── log-replay-mp4.py          # Render: MP4
├── log-replay-pdf.py          # Render: PDF
├── log-replay-gif.py          # Render: GIF
├── web_ui.py                  # Flask Web UI
├── session-shipper.py         # Enterprise shipper
├── session-stats.py           # Statistics
├── search_utils.py            # Shared session discovery helpers
├── templates/index.html       # Web UI template
├── docs/
│   ├── architecture.md        # This document
│   ├── architecture-ja.md     # 日本語版
│   ├── getting-started.md
│   ├── getting-started-ja.md
│   ├── agents.md
│   ├── agents-ja.md
│   ├── renderers.md
│   ├── renderers-ja.md
│   └── data-model.md
├── README.md                  # 日本語 README
├── README-en.md               # English README
└── CHANGELOG.md
```

---

*Previous content below this line is retained for reference.*

---

## 1. Top-level view

```text
Session Logs (vendor-specific)
  ├─ Claude Code  (~/.claude/projects/*/*.jsonl)
  ├─ Codex CLI    (~/.codex/sessions/**/*.jsonl)
  └─ Gemini CLI   (~/.gemini/tmp/*/chats/session-*.json)
          │
          v
Agent Adapters (log2model)
  ├─ claude-log2model.py
  ├─ codex-log2model.py
  └─ gemini-log2model.py
          │
          v
Common Model (JSON)
  {source, agent, messages[{role, text, tool_uses, tool_results, thinking, timestamp}]}
          │
          v
Renderer (log-model-renderer.py)
  ├─ Markdown     (static text)
  ├─ HTML         (static chat UI)
  ├─ Player       (interactive replay + Alibai Mode)
  └─ Terminal     (Claude Code UI replica)
          │
          v (optional)
Video Recorder (log-replay-mp4.py)
  └─ Playwright + FFmpeg → MP4

Entry Points
  ├─ log-replay.py     (CLI wrapper, pipes adapter → renderer)
  └─ web_ui.py         (Flask, browser-based session management)
```

## 2. Pipeline model

This tool uses a **three-stage pipeline** architecture.

### 2.1 Stage 1 — Extraction (Agent Adapters)

Responsibility:
- parse vendor-specific log format (JSONL or JSON)
- extract messages, tool uses, tool results, thinking blocks, timestamps
- normalize into the common model schema
- discover and list available sessions from known filesystem locations

Each adapter is a standalone script that can be used independently.

### 2.2 Stage 2 — Rendering

Responsibility:
- read the common model JSON
- produce output in the requested format
- apply theme, range filters, ANSI mode, and other options
- embed all assets (CSS, JS) into a single self-contained HTML file

The renderer never knows which agent produced the data — it only reads the common model.

### 2.3 Stage 3 — Recording (optional)

Responsibility:
- open the rendered HTML in a headless browser (Playwright)
- automate playback (set speed, click play, wait for completion)
- capture video frames and encode to MP4 (FFmpeg)

This stage is optional and requires additional dependencies.

## 3. Subsystems

### 3.1 Agent Adapters

```text
Agent Adapter
  ├─ parse_messages(input_path) → raw messages
  ├─ build_model(messages, input_path) → common model dict
  ├─ discover_sessions(filter) → session list
  └─ select_session(sessions) → chosen path (interactive)
```

Each adapter implements the same four-function contract:
- **parse/build**: read the log file and produce a common model
- **discover**: scan known filesystem locations for available sessions
- **select**: present an interactive list and let the user choose

Adapters are isolated from each other. Adding a new agent means creating a new `<agent>-log2model.py` file and registering it in `log-replay.py` and `web_ui.py`.

### 3.2 Common Model

The common model is a JSON document with a flat message array. See [data-model.md](data-model.md) for the full schema.

Key property: the common model is **agent-agnostic**. Any renderer can consume any model regardless of which adapter produced it.

### 3.3 Renderer

```text
Renderer (log-model-renderer.py)
  ├─ Markdown renderer   — plain text, no dependencies
  ├─ HTML renderer       — static chat UI (light/console themes)
  ├─ Player renderer     — interactive HTML with JS controls
  │   └─ Alibai Mode     — timestamp visualization subsystem
  └─ Terminal renderer   — Claude Code terminal UI replica with JS
```

The renderer is the largest component (~2,580 lines) because it contains all output format logic and embedded CSS/JS for interactive formats.

### 3.4 CLI Wrapper (log-replay.py)

Orchestrates the pipeline:
1. Run the appropriate adapter to produce a temporary common model file
2. Run the renderer on that model with the requested format/options
3. Clean up the temporary file

Also supports TUI mode (interactive terminal UI) when invoked without arguments.

### 3.5 Web UI (web_ui.py)

A Flask application providing:
- session discovery across all supported agents
- session preview (first messages, metadata)
- format/theme/range selection
- in-browser rendering or file download
- Alibai Mode time adjustment

The Web UI imports adapter modules directly (not via subprocess) for session discovery and preview, but invokes the renderer via subprocess for output generation.

### 3.6 Video Recorder (log-replay-mp4.py)

Orchestrates a two-step process:
1. Generate HTML via the CLI wrapper pipeline
2. Record the HTML playback using Playwright and encode with FFmpeg

## 4. Core architectural rules

1. Renderers never read raw agent logs — only the common model.
2. Adapters never produce output — only the common model.
3. The common model schema is the contract between adapters and renderers.
4. Each output format is self-contained — HTML files embed all CSS and JS.
5. Interactive features (player, terminal) work offline from a single HTML file.
6. Optional dependencies (Flask, Playwright, FFmpeg) are imported lazily, never at module level.
7. The pipeline is composable — each stage can be invoked independently via CLI.

## 5. Architecture diagrams

### 5.1 Data flow

```text
+-------------------+     +-------------------+     +-------------------+
| Agent Logs        | --> | Agent Adapter     | --> | Common Model JSON |
| (JSONL/JSON)      |     | (claude/codex/    |     | (agent-agnostic)  |
|                   |     |  gemini-log2model)|     |                   |
+-------------------+     +-------------------+     +-------------------+
                                                            |
                                                            v
                                                    +-------------------+
                                                    | Renderer          |
                                                    | (log-model-       |
                                                    |  renderer.py)     |
                                                    +-------------------+
                                                            |
                                    ┌───────────┬───────────┼───────────┐
                                    v           v           v           v
                              Markdown       HTML       Player     Terminal
                              (.md)       (.html)     (.html+JS)  (.html+JS)
                                                            |
                                                            v (optional)
                                                    +-------------------+
                                                    | Video Recorder    |
                                                    | (Playwright +     |
                                                    |  FFmpeg → MP4)    |
                                                    +-------------------+
```

### 5.2 Entry point routing

```text
User
  ├─ CLI: log-replay.py --agent claude -f player
  │   └─ subprocess: claude-log2model.py → log-model-renderer.py
  │
  ├─ Web: web_ui.py (http://localhost:5000)
  │   └─ import: claude_log2model (discover/preview)
  │   └─ subprocess: log-model-renderer.py (render)
  │
  ├─ Direct: claude-log2model.py + log-model-renderer.py (manual pipeline)
  │
  └─ Video: log-replay-mp4.py --agent claude -f player
      └─ subprocess: log-replay.py → Playwright → FFmpeg
```

### 5.3 Session discovery paths

```text
Claude Code
  ~/.claude/projects/<project-dir>/*.jsonl
  ├─ Discovered by: claude-log2model.discover_sessions()
  ├─ Filtered by: --project (project name substring)
  └─ Excludes: subagents/ subdirectory

Codex CLI
  ~/.codex/sessions/<nested-path>/*.jsonl
  ├─ Discovered by: codex-log2model.discover_sessions()
  └─ Filtered by: --filter (path substring)

Gemini CLI
  ~/.gemini/tmp/<project-dir>/chats/session-*.json
  ├─ Discovered by: gemini-log2model.discover_sessions()
  └─ Filtered by: --project (project name substring)
```

## 6. Dependency model

```text
Core (no external dependencies):
  claude-log2model.py    — Python 3.6+ standard library
  codex-log2model.py     — Python 3.6+ standard library
  gemini-log2model.py    — Python 3.6+ standard library
  log-model-renderer.py  — Python 3.6+ standard library
  log-replay.py          — Python 3.6+ standard library

Web UI (optional):
  web_ui.py              — flask

Video Recording (optional):
  log-replay-mp4.py      — playwright, ffmpeg (system)
```

Lazy imports ensure that missing optional dependencies only cause errors when the specific feature is used, not at startup.

## 7. File layout

```
claude-session-replay/
├── log-replay.py              # CLI wrapper (pipeline orchestrator)
├── claude-log2model.py        # Claude Code adapter
├── codex-log2model.py         # Codex CLI adapter
├── gemini-log2model.py        # Gemini CLI adapter
├── log-model-renderer.py      # Multi-format renderer
├── log-replay-mp4.py          # Video recorder
├── web_ui.py                  # Flask Web UI
├── run-web.sh                 # Web UI startup script
├── claude-session-replay.py   # Legacy single-file script (retained)
├── templates/
│   └── index.html             # Web UI template
├── docs/
│   ├── vision.md              # Project vision and motivation
│   ├── architecture.md        # This document
│   ├── data-model.md          # Common model schema
│   ├── output-formats.md      # Output format specifications
│   ├── agent-adapters.md      # Agent adapter specifications
│   └── media/                 # Demo videos and screenshots
├── README.md                  # Japanese README
├── README.en.md               # English README
└── CLAUDE.md                  # AI development guide
```
