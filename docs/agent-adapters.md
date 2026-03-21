[日本語版](ja/agent-adapters.md)

# Agent Adapters

This document specifies the input log format and adapter behavior for each supported agent.

## 1. Adapter contract

Every agent adapter implements the same logical interface:

```python
def parse_messages(input_path: str) -> list[dict]
    """Read the log file and return raw message records."""

def build_model(messages: list[dict], input_path: str) -> dict
    """Transform raw messages into a common model dict."""

def discover_sessions(filter: str = None) -> list[dict]
    """Scan filesystem for available sessions. Returns list of session metadata."""

def select_session(sessions: list[dict]) -> str
    """Interactive session picker. Returns chosen file path."""
```

The adapter contract is not enforced by an abstract base class — it is a convention followed by each adapter script.

## 2. Claude Code Adapter

**Script**: `claude-log2model.py`

### Input format

- **Location**: `~/.claude/projects/<project-dir>/*.jsonl`
- **Format**: JSONL (one JSON object per line)
- **Encoding**: UTF-8

Each JSONL line is a record with top-level fields:

```json
{
  "type": "user" | "assistant" | "summary" | ...,
  "timestamp": "2026-03-21T10:30:00.000Z",
  "gitBranch": "feature/login-fix",
  "message": {
    "role": "user" | "assistant",
    "content": "string" | [content_block, ...]
  }
}
```

### Content block types

| Block type | Fields | Mapping |
|-----------|--------|---------|
| `text` | `text` | → `message.text` |
| `tool_use` | `id`, `name`, `input` | → `message.tool_uses[]` |
| `tool_result` | `tool_use_id`, `content` | → `message.tool_results[]` |
| `thinking` | `thinking` | → `message.thinking[]` |

### Session discovery

- Scans `~/.claude/projects/` recursively for `.jsonl` files
- Excludes files in `subagents/` subdirectories
- Excludes files smaller than 1KB (likely empty or abandoned sessions)
- Sorts by modification time (newest first)
- Extracts preview data: timestamp, git branch, first message, message counts

### Filter options

| Option | Description |
|--------|-------------|
| `--project` | Filter by project name (substring match, case-insensitive) |

### Session list display

```
  #  Date              Branch                        Project         Size   Msgs  First message
  ─  ────────────────  ────────────────────────────  ──────────────  ─────  ─────  ────────────
  1  2026-03-21 10:30  feature/login-fix             myproject        45K     28  Fix the login bug
  2  2026-03-20 14:15  main                          webapp           120K    64  Add user dashboard
```

## 3. Codex CLI Adapter

**Script**: `codex-log2model.py`

### Input format

- **Location**: `~/.codex/sessions/<nested-path>/*.jsonl`
- **Format**: JSONL (one JSON object per line)
- **Encoding**: UTF-8

Codex uses a different message structure from Claude:

```json
{
  "type": "message",
  "timestamp": "2026-03-21T10:30:00Z",
  "message": {
    "role": "user" | "assistant",
    "content": "string" | [content_block, ...]
  }
}
```

### Content block types

| Block type | Fields | Mapping |
|-----------|--------|---------|
| `input_text` | `text` | → `message.text` (user) |
| `output_text` | `text` | → `message.text` (assistant) |
| `text` | `text` | → `message.text` |
| `thinking` | `thinking` | → `message.thinking[]` |
| `function_call` | `name`, `arguments` | → `message.tool_uses[]` (normalized) |
| `function_call_output` | `output` | → `message.tool_results[]` |

### Tool name normalization

Codex uses different tool names from Claude. The adapter normalizes them for consistent display:

| Codex function | Normalized tool_use format |
|---------------|---------------------------|
| `shell_command` | `name: "Bash"`, `input: {command, workdir}` |
| `file_read` | `name: "Read"`, `input: {file_path}` |
| `file_write` | `name: "Write"`, `input: {file_path, content}` |
| `file_edit` | `name: "Edit"`, `input: {file_path, old_string, new_string}` |

### Session discovery

- Scans `~/.codex/sessions/` recursively for `.jsonl` files
- Sorts by modification time (newest first)

### Filter options

| Option | Description |
|--------|-------------|
| `--filter` | Filter by path substring |

## 4. Gemini CLI Adapter

**Script**: `gemini-log2model.py`

### Input format

- **Location**: `~/.gemini/tmp/<project-dir>/chats/session-*.json`
- **Format**: JSON (single JSON object, not JSONL)
- **Encoding**: UTF-8

Gemini uses a single JSON file per session:

```json
{
  "startTime": "2026-03-21T10:30:00Z",
  "messages": [
    {
      "type": "user" | "gemini",
      "content": "string" | [content_block, ...],
      "thoughts": [{"description": "..."}],
      "timestamp": "2026-03-21T10:30:00Z"
    }
  ]
}
```

### Role mapping

| Gemini type | Common model role |
|-------------|------------------|
| `"user"` | `"user"` |
| `"gemini"` | `"assistant"` |

### Content mapping

| Field | Mapping |
|-------|---------|
| `content` (string) | → `message.text` |
| `content` (array with `text` blocks) | → `message.text` (joined) |
| `thoughts[].description` | → `message.thinking[]` |

### Limitations

- Gemini CLI session logs do not include tool invocation data — `tool_uses` and `tool_results` are always empty arrays in the common model.

### Session discovery

- Scans `~/.gemini/tmp/` for project directories
- Within each project, scans `chats/` for `session-*.json` files
- Sorts by modification time (newest first)

### Filter options

| Option | Description |
|--------|-------------|
| `--project` | Filter by project name (substring match, case-insensitive) |

## 5. Adding a new agent adapter

To add support for a new agent:

1. **Create** `<agent>-log2model.py` implementing the four-function contract
2. **Register** in `log-replay.py`:
   - Add the agent name to the `--agent` choices
   - Map it to the new adapter script
3. **Register** in `web_ui.py`:
   - Import the new module
   - Add session discovery to the session list endpoint
4. **Document** the new agent in this file

The renderer requires no changes — it reads the common model, not raw logs.

### Adapter checklist

- [ ] `parse_messages()` / input parsing
- [ ] `build_model()` → common model with all 6 message fields
- [ ] `discover_sessions()` with filesystem scanning
- [ ] `select_session()` with interactive picker
- [ ] Role normalization to `"user"` / `"assistant"`
- [ ] Timestamp extraction in ISO 8601
- [ ] Tool name normalization (if applicable)
- [ ] Thinking block extraction (if available)
- [ ] Preview extraction for session list display
