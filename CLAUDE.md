# CLAUDE.md — Development Guide

## Project overview

claude-session-replay converts AI coding agent session logs (Claude Code, Codex CLI, Gemini CLI) into a common JSON model and renders them as Markdown, HTML, interactive player, or terminal-style player. Optionally exports to MP4 video.

## Architecture

Three-stage pipeline: **Agent Adapter → Common Model → Renderer**

See [docs/architecture.md](docs/architecture.md) for full details.

## Quick reference

### Run

```bash
# CLI (recommended)
python3 log-replay.py --agent claude -f player
python3 log-replay.py --agent codex -f terminal
python3 log-replay.py --agent gemini -f player

# Web UI
python3 web_ui.py  # http://localhost:5000

# Direct pipeline
python3 claude-log2model.py session.jsonl -o session.model.json
python3 log-model-renderer.py session.model.json -f player -o out.html

# Video
python3 log-replay-mp4.py --agent claude session.jsonl -f player -o out.mp4
```

### Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
# Basic CLI: no pip install needed
# Web UI: pip install flask playwright && python3 -m playwright install
# Video: pip install playwright && python3 -m playwright install  (+ ffmpeg in PATH)
```

### Test

No formal test suite. Verify manually:

```bash
# Smoke test: convert and render a Claude session
python3 log-replay.py --agent claude -f player -o /tmp/test.html
# Open /tmp/test.html in browser and verify playback
```

## File map

| File | Purpose | Lines |
|------|---------|-------|
| `log-replay.py` | CLI wrapper (pipeline orchestrator) | ~96 |
| `claude-log2model.py` | Claude Code log → common model | ~332 |
| `codex-log2model.py` | Codex CLI log → common model | ~397 |
| `gemini-log2model.py` | Gemini CLI log → common model | ~223 |
| `log-model-renderer.py` | Common model → md/html/player/terminal | ~2580 |
| `log-replay-mp4.py` | HTML → MP4 via Playwright + FFmpeg | ~160 |
| `web_ui.py` | Flask Web UI | ~934 |
| `templates/index.html` | Web UI template | ~large |
| `claude-session-replay.py` | Legacy single-file script (retained) | ~2162 |

## Key conventions

- **Python 3.6+** — no type hints beyond what 3.6 supports
- **Standard library only** for core functionality — Flask/Playwright are optional
- **No package manager** (no pyproject.toml, no requirements.txt) — deps are few and documented in README
- **Self-contained HTML output** — all CSS/JS embedded, no external resources
- **Filenames use hyphens** (`claude-log2model.py`), not underscores

## Common model schema

```json
{
  "source": "filename.jsonl",
  "agent": "claude" | "codex" | "gemini",
  "messages": [{
    "role": "user" | "assistant",
    "text": "",
    "tool_uses": [],
    "tool_results": [],
    "thinking": [],
    "timestamp": "ISO-8601 or empty"
  }]
}
```

See [docs/data-model.md](docs/data-model.md) for full schema.

## Session log locations

```
~/.claude/projects/<project-dir>/*.jsonl        # Claude Code
~/.codex/sessions/<nested-path>/*.jsonl         # Codex CLI
~/.gemini/tmp/<project-dir>/chats/session-*.json # Gemini CLI
```

## Documentation

- [docs/vision.md](docs/vision.md) — Project vision and motivation
- [docs/architecture.md](docs/architecture.md) — System design and pipeline model
- [docs/data-model.md](docs/data-model.md) — Common model JSON schema
- [docs/output-formats.md](docs/output-formats.md) — Output format specifications
- [docs/agent-adapters.md](docs/agent-adapters.md) — Agent adapter specifications

## Adding a new agent

1. Create `<agent>-log2model.py` with `build_model()`, `discover_sessions()`, `select_session()`
2. Register in `log-replay.py` (`--agent` choices + script mapping)
3. Register in `web_ui.py` (import + session discovery)
4. Update `docs/agent-adapters.md`
5. The renderer requires no changes
