[日本語版](ja/backlog.md)

# Prioritized Backlog

## Phase 0 — Core pipeline ✓
1. ~~Claude Code log → common model adapter~~
2. ~~Common model → Markdown renderer~~
3. ~~Common model → HTML renderer (light/console themes)~~
4. ~~Common model → interactive Player renderer~~
5. ~~Common model → Terminal (Claude Code UI replica) renderer~~
6. ~~CLI wrapper (log-replay.py) orchestrating adapter → renderer~~

## Phase 1 — Multi-agent support (partial)
1. ~~Codex CLI adapter (codex-log2model.py)~~
2. ~~Gemini CLI adapter (gemini-log2model.py)~~
3. Aider session log adapter
4. Cursor session log adapter

## Phase 2 — Interactive features ✓
1. ~~Playback controls (play/pause, step, seek, speed)~~
2. ~~Keyboard shortcuts~~
3. ~~Progress bar with click-to-seek~~
4. ~~Tool block rendering (Read/Write/Edit/Bash/Grep/Glob/Task)~~
5. ~~Spinner animation (processing → complete)~~
6. ~~Message range filtering (--range)~~

## Phase 3 — Alibai Mode ✓
1. ~~Timestamp extraction and preservation in common model~~
2. ~~Side clocks (per-message analog clock)~~
3. ~~Fixed clock (bottom-right large clock)~~
4. ~~Uniform playback mode~~
5. ~~Real-time playback mode~~
6. ~~Compressed playback mode (60s)~~
7. ~~Message numbering in time labels~~

## Phase 4 — Web UI ✓
1. ~~Flask Web UI (web_ui.py)~~
2. ~~Session auto-discovery (Claude/Codex/Gemini)~~
3. ~~Session preview (first messages, metadata)~~
4. ~~Format/theme/range selection~~
5. ~~Alibai time adjustment~~
6. ~~Session statistics panel~~

## Phase 5 — Video export ✓
1. ~~Playwright-based HTML recording~~
2. ~~FFmpeg encoding to MP4~~
3. ~~Width/height/fps/speed options~~

## Phase 6 — Quality & polish
1. ANSI color rendering mode (`--ansi-mode color`)
2. ~~Thinking block display (collapsible)~~
3. Content truncation (`--truncate-length`)
4. Table rendering in terminal format
5. Sub-agent message handling

## Phase 7 — New capabilities (planned)
1. TUI mode (Textual-based terminal UI for session selection)
2. Diff view — side-by-side comparison of two sessions
3. Search — full-text search within a session replay
4. Export to other formats (PDF, animated GIF)
5. Session merging — combine multiple sessions into one timeline
6. Statistics dashboard — token usage, tool frequency, session duration analytics
7. Streaming mode — live-follow an active session

## Phase 8 — Search scalability (planned)
1. Search index backend — Whoosh / SQLite FTS5 for persistent full-text index
2. Lucene / OpenSearch integration — for large-scale deployments (hundreds~thousands of sessions)
3. Incremental indexing — index new sessions on discovery, skip already-indexed
4. Search result ranking — relevance scoring beyond simple match order


## Implementation progress

| Component | Status | Description |
|-----------|--------|-------------|
| claude-log2model.py | ✓ Complete | Claude Code JSONL → common model |
| codex-log2model.py | ✓ Complete | Codex CLI JSONL → common model |
| gemini-log2model.py | ✓ Complete | Gemini CLI JSON → common model |
| log-model-renderer.py | ✓ Complete | Common model → md/html/player/terminal |
| log-replay.py | ✓ Complete | CLI wrapper (pipeline orchestrator) |
| log-replay-mp4.py | ✓ Complete | Video export (Playwright + FFmpeg) |
| web_ui.py | ✓ Complete | Flask Web UI |
| Alibai Mode | ✓ Complete | Timestamp visualization + playback modes |

### Remaining work (priority order)
1. **TUI mode** — Textual-based interactive terminal UI (started, `log_replay_tui.py`)
2. **Additional agent adapters** — Aider, Cursor, other agents
3. **Session search** — Full-text search across sessions
4. **Diff view** — Compare two sessions side-by-side
5. **Statistics** — Token usage, tool frequency, duration analytics
6. **Export formats** — PDF, animated GIF
7. **Streaming mode** — Live-follow active sessions
