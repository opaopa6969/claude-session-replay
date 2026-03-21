[日本語版](ja/architecture.md)

# Architecture

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
