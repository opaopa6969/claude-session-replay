# Changelog

All notable changes to claude-session-replay are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Added
- `aider-log2model.py` — Aider chat history markdown adapter
- `cursor-log2model.py` — Cursor AI session adapter (SQLite-based)
- `log-replay-gif.py` — animated GIF output via Playwright + Pillow/FFmpeg
- `log-replay-pdf.py` — PDF output via Playwright
- Full documentation suite: architecture, getting-started, agents, renderers (en + ja)
- `CHANGELOG.md`

### Notes
- `pyproject.toml` intentionally absent — project is not published to PyPI
- `session-shipper.py` `redact_pii` flag is not yet thoroughly tested

---

## [0.4.0] - 2026-03-01

### Added
- `session-shipper.py` — ship sessions to OpenSearch or file export (batch + watch mode)
- Enterprise docs: `docs/spec-enterprise-shipping.md`, `docs/enterprise-deployment-guide.md`
- `shipper-config.json` example configuration

### Changed
- `session-stats.py` — session statistics reporter

---

## [0.3.0] - 2026-02-20

### Added
- `gemini-log2model.py` — Gemini CLI adapter (`session-*.json`)
- `log-replay-mp4.py` — MP4 video output via Playwright + FFmpeg
- `web_ui.py` — Flask-based browser UI with session management
- `run-web.sh` — convenience launcher for web UI
- Alibai Mode in player renderer (analog clock, real-time / compressed playback)
- `--ansi-mode strip|color` option in renderer
- `--range` message range filter

### Changed
- `log-model-renderer.py` — added Terminal format, GIF/PDF stubs, range filter

---

## [0.2.0] - 2026-02-10

### Added
- `codex-log2model.py` — OpenAI Codex CLI adapter
- `log-replay.py` — unified CLI wrapper (agent → renderer pipeline)
- `log-model-renderer.py` — common model renderer (md / html / player)
- `search_utils.py` — shared session discovery utilities
- `templates/` — Jinja2 HTML templates for web UI

### Changed
- Architecture split into three stages (capture / normalize / render)
- `claude-log2model.py` rewritten to follow adapter contract

---

## [0.1.0] - 2026-01-25

### Added
- `claude-session-replay.py` — original single-file Claude Code session replayer
- `claude-log2model.py` — initial Claude log parser
- `log_replay_tui.py` — terminal TUI using `tui/` module
- Basic Markdown and HTML output
