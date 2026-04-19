[日本語版](agents-ja.md)

# Agents

Support status, log formats, and adapter details for each AI coding agent.

---

## Table of Contents

- [Support Matrix](#support-matrix)
- [Claude Code](#claude-code)
- [OpenAI Codex CLI](#openai-codex-cli)
- [Gemini CLI](#gemini-cli)
- [Aider](#aider)
- [Cursor](#cursor)
- [Adding a New Agent](#adding-a-new-agent)

---

## Support Matrix

| Agent | Adapter | Text | Tool calls | Thinking | Timestamps | Autodiscover |
|-------|---------|------|-----------|---------|-----------|-------------|
| **Claude Code** | `claude-log2model.py` | Yes | Yes | Yes | Yes | Yes |
| **Codex CLI** | `codex-log2model.py` | Yes | Yes (normalized) | Yes | Yes | Yes |
| **Gemini CLI** | `gemini-log2model.py` | Yes | No | Yes | Yes | Yes |
| **Aider** | `aider-log2model.py` | Yes | No | No | Partial | No |
| **Cursor** | `cursor-log2model.py` | Yes | No | No | Partial | Yes |

---

## Claude Code

**Script**: `claude-log2model.py`

### Log location

```
~/.claude/projects/<project-dir>/*.jsonl
```

### Input format

JSONL — one JSON object per line:

```json
{
  "type": "user" | "assistant" | "summary",
  "timestamp": "2026-03-21T10:30:00.000Z",
  "gitBranch": "feature/login-fix",
  "message": {
    "role": "user" | "assistant",
    "content": "string" | [content_block, ...]
  }
}
```

### Content block types

| Block type | Fields | Maps to |
|-----------|--------|---------|
| `text` | `text` | `message.text` |
| `tool_use` | `id`, `name`, `input` | `message.tool_uses[]` |
| `tool_result` | `tool_use_id`, `content` | `message.tool_results[]` |
| `thinking` | `thinking` | `message.thinking[]` |

### Known tools

`Read`, `Write`, `Edit`, `Bash`, `Grep`, `Glob`, `Task`, `WebFetch`, `WebSearch`

### Session discovery

- Scans `~/.claude/projects/` recursively for `.jsonl` files
- Excludes files in `subagents/` subdirectories
- Excludes files smaller than 1 KB
- Sorts by modification time (newest first)

### Filter options

| Option | Description |
|--------|-------------|
| `--project` | Substring match on project directory name |

---

## OpenAI Codex CLI

**Script**: `codex-log2model.py`

### Log location

```
~/.codex/sessions/<nested-path>/*.jsonl
```

### Input format

JSONL — one JSON object per line:

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

| Block type | Fields | Maps to |
|-----------|--------|---------|
| `input_text` / `output_text` / `text` | `text` | `message.text` |
| `thinking` | `thinking` | `message.thinking[]` |
| `function_call` | `name`, `arguments` | `message.tool_uses[]` (normalized) |
| `function_call_output` | `output` | `message.tool_results[]` |

### Tool name normalization

Codex function names are normalized to match Claude's tool names for consistent rendering:

| Codex function | Normalized name |
|---------------|----------------|
| `shell_command` | `Bash` |
| `file_read` | `Read` |
| `file_write` | `Write` |
| `file_edit` | `Edit` |

### Session discovery

- Scans `~/.codex/sessions/` recursively for `.jsonl` files
- Sorts by modification time (newest first)

### Filter options

| Option | Description |
|--------|-------------|
| `--filter` | Substring match on file path |

---

## Gemini CLI

**Script**: `gemini-log2model.py`

### Log location

```
~/.gemini/tmp/<project-dir>/chats/session-*.json
```

### Input format

Single JSON file (not JSONL) per session:

```json
{
  "startTime": "2026-03-21T10:30:00Z",
  "messages": [
    {
      "type": "user" | "gemini",
      "content": "string" | [{"text": "..."}],
      "thoughts": [{"description": "..."}],
      "timestamp": "2026-03-21T10:30:00Z"
    }
  ]
}
```

### Role mapping

| Gemini `type` | Common model `role` |
|--------------|-------------------|
| `"user"` | `"user"` |
| `"gemini"` | `"assistant"` |

### Limitations

- Tool invocation data is **not** stored in Gemini CLI session logs. `tool_uses` and `tool_results` are always empty arrays.

### Session discovery

- Scans `~/.gemini/tmp/` for project directories
- Within each project, scans `chats/` for `session-*.json` files
- Sorts by modification time (newest first)

### Filter options

| Option | Description |
|--------|-------------|
| `--project` | Substring match on project directory name |

---

## Aider

**Script**: `aider-log2model.py`

### Log location

Aider writes `.aider.chat.history.md` in the working directory of each project. There is no central log directory — you must specify the file path explicitly.

### Input format

Markdown file with markers:

```markdown
# aider chat started at 2026-03-21 10:30:00

#### /user
Fix the login bug.

#### /assistant
I'll look at the login handler first.
```

Some Aider versions use:

```markdown
#### /user timestamp
<message>

#### /assistant timestamp
<message>
```

### Limitations

- No tool call data (Aider does not store structured tool invocations in this format).
- No thinking blocks.
- Timestamp availability depends on Aider version — some versions omit them.

### Session discovery

Aider does not have a central session directory. Auto-discovery is **not supported**. Provide the file path explicitly:

```bash
python3 aider-log2model.py /path/to/project/.aider.chat.history.md -o out.json
```

---

## Cursor

**Script**: `cursor-log2model.py`

### Log location

Cursor stores conversation data in SQLite databases:

```
~/.cursor/                              (Linux)
~/.config/Cursor/                       (Linux alt)
~/Library/Application Support/Cursor/   (macOS)
%APPDATA%/Cursor/                       (Windows)
~/.cursor-tutor/                        (workspace-level)
```

### Input format

SQLite databases containing conversation records. The exact schema varies by Cursor version. The adapter searches for known table/column patterns.

### Limitations

- The SQLite schema is not publicly documented and may change between Cursor versions.
- Tool call data and thinking blocks are not available.
- Timestamp availability varies.

### Session discovery

- Searches all known Cursor data directories
- Reads SQLite databases and extracts conversation records

---

## Adding a New Agent

1. Create `<agent>-log2model.py` with the four-function contract:
   - `parse_messages(input_path) -> list[dict]`
   - `build_model(messages, input_path) -> dict`
   - `discover_sessions(filter=None) -> list[dict]`
   - `select_session(sessions) -> str`
2. Register `--agent <name>` in `log-replay.py`.
3. Register in `web_ui.py` (import + session discovery endpoint).
4. The renderer requires **no changes** — it reads the common model only.

Checklist:
- [ ] Role normalization → `"user"` / `"assistant"`
- [ ] Timestamp extraction in ISO 8601
- [ ] Tool name normalization (if applicable)
- [ ] Thinking block extraction (if available)
- [ ] Preview extraction for session list display
