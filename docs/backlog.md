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

## Phase 7 — Full-text search ✓
1. ~~search_utils.py — Core search with ProcessPoolExecutor parallel scanning~~
2. ~~Web UI cross-session search — Search bar above session list~~
3. ~~Web UI within-session search — Search bar in preview/editor with highlight~~
4. ~~Player/Terminal HTML embedded search — `/` key, Enter/Shift+Enter navigation~~
5. ~~CLI search — `--search` flag with `--regex`, `--case-sensitive`, `--search-scope`~~

## Phase 8 — Session Shipper ✓
1. ~~session-shipper.py — Batch + Watch (daemon) mode~~
2. ~~Identity envelope (user_id, hostname, organization)~~
3. ~~Redaction engine (regex patterns for API keys, emails, etc.)~~
4. ~~Scope filtering (include/exclude text, thinking, tool_use, tool_result)~~
5. ~~Security analysis (sensitive file read/write, suspicious commands, external URL access)~~
6. ~~Banned word detection~~
7. ~~Transport layer (OpenSearch HTTP Bulk API + File export for Filebeat)~~
8. ~~Offset tracking with crash recovery~~
9. ~~Lookup command (session_id → local session → Player)~~

## Phase 9 — Statistics & Diff View ✓
1. ~~session-stats.py — Statistics engine + Diff logic~~
2. ~~Per-session stats: messages, duration, tools, text, thinking, timing~~
3. ~~Cross-session overview: by agent, by project, tool distribution, timeline~~
4. ~~Diff view: side-by-side comparison with tool breakdown~~
5. ~~CLI: --stats, --overview, --diff flags in log-replay.py~~
6. ~~HTML output: self-contained stats dashboard and diff view~~
7. ~~Web UI: Stats button, Diff button with session picker~~

## Phase 10 — New capabilities (planned)
1. TUI mode (Textual-based terminal UI for session selection)
2. Export to other formats (PDF, animated GIF)
3. Session merging — combine multiple sessions into one timeline
4. Streaming mode — live-follow an active session

## Phase 10 — Search scalability (planned)
1. Search index backend — Whoosh / SQLite FTS5 for persistent full-text index
2. Lucene / OpenSearch integration — for large-scale deployments (hundreds~thousands of sessions)
3. Incremental indexing — index new sessions on discovery, skip already-indexed
4. Search result ranking — relevance scoring beyond simple match order

## Phase 11 — Enterprise OpenSearch integration (planned, pending internal review)
1. OpenSearch Security Plugin 連携 — SAML/OIDC 認証でuser_id正当性担保
2. Document Level Security — ユーザーごとのアクセス制御
3. SaaS化対応 — organization フィールドによるマルチテナント
4. OpenSearch Dashboards 連携 — セキュリティアラート・禁止ワードダッシュボード
5. Web UI → OpenSearch → ローカルセッション逆引き連携


## Implementation progress

| Component | Status | Description |
|-----------|--------|-------------|
| claude-log2model.py | ✓ Complete | Claude Code JSONL → common model |
| codex-log2model.py | ✓ Complete | Codex CLI JSONL → common model |
| gemini-log2model.py | ✓ Complete | Gemini CLI JSON → common model |
| log-model-renderer.py | ✓ Complete | Common model → md/html/player/terminal |
| log-replay.py | ✓ Complete | CLI wrapper + search + shipper integration |
| log-replay-mp4.py | ✓ Complete | Video export (Playwright + FFmpeg) |
| web_ui.py | ✓ Complete | Flask Web UI + search APIs |
| Alibai Mode | ✓ Complete | Timestamp visualization + playback modes |
| search_utils.py | ✓ Complete | Full-text search with ProcessPoolExecutor |
| session-shipper.py | ✓ Complete | Batch/watch shipping + security analysis |
| session-stats.py | ✓ Complete | Statistics engine + Diff view |

### Remaining work (priority order)
1. **TUI mode** — Textual-based interactive terminal UI (started, `log_replay_tui.py`)
2. **Additional agent adapters** — Aider, Cursor, other agents
3. **Enterprise OpenSearch** — Authentication, DLS, multi-tenant (pending internal review)
4. **Export formats** — PDF, animated GIF
5. **Streaming mode** — Live-follow active sessions
