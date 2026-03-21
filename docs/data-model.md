[日本語版](ja/data-model.md)

# Data Model — Common Log Model

This document defines the common model JSON schema that serves as the contract between agent adapters and renderers.

## 1. Overview

The common model is a JSON document that represents a single session transcript in an agent-agnostic format. Every adapter produces this format; every renderer consumes it.

```json
{
  "source": "session-abc123.jsonl",
  "agent": "claude",
  "messages": [
    {
      "role": "user",
      "text": "Fix the login bug",
      "tool_uses": [],
      "tool_results": [],
      "thinking": [],
      "timestamp": "2026-03-21T10:30:00Z"
    }
  ]
}
```

## 2. Root object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source` | string | yes | Original filename (basename only, no path) |
| `agent` | string | yes | Agent identifier: `"claude"`, `"codex"`, or `"gemini"` |
| `messages` | array | yes | Ordered array of message objects |

### Invariants

- `source` is always a filename, never an absolute path.
- `agent` is one of the known agent identifiers. This field allows renderers to apply agent-specific display logic if needed.
- `messages` preserves the original chronological order from the session log.

## 3. Message object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `role` | string | yes | `"user"` or `"assistant"` |
| `text` | string | yes | Main text content of the message (may be empty string) |
| `tool_uses` | array | yes | Tool invocation blocks (see below) |
| `tool_results` | array | yes | Tool result blocks (see below) |
| `thinking` | array | yes | Thinking/reasoning blocks (see below) |
| `timestamp` | string | yes | ISO 8601 timestamp (may be empty string if unavailable) |

### Invariants

- A message is only included if at least one of `text`, `tool_uses`, `tool_results`, or `thinking` is non-empty.
- `role` is always exactly `"user"` or `"assistant"`, regardless of the source agent's terminology (e.g., Gemini uses `"gemini"` internally, but the adapter normalizes to `"assistant"`).
- `timestamp` is an ISO 8601 string when available, empty string when the source log does not provide timing data.

## 4. Tool use object

Represents a single tool invocation by the assistant.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | yes | Always `"tool_use"` |
| `id` | string | yes | Unique tool invocation ID |
| `name` | string | yes | Tool name (see known tools below) |
| `input` | object | yes | Tool-specific input parameters |

### Known tool names

Tools from Claude Code:

| Tool Name | Key Input Fields | Description |
|-----------|-----------------|-------------|
| `Read` | `file_path` | Read a file |
| `Write` | `file_path`, `content` | Write a file |
| `Edit` | `file_path`, `old_string`, `new_string` | Edit a file |
| `Bash` | `command` | Execute a shell command |
| `Grep` | `pattern`, `path` | Search file contents |
| `Glob` | `pattern` | Search file names |
| `Task` | `description` | Create/manage tasks |
| `Agent` | `prompt` | Launch a sub-agent |

Tools from Codex CLI:

| Tool Name | Key Input Fields | Description |
|-----------|-----------------|-------------|
| `shell_command` | `command`, `workdir` | Execute a shell command |
| `file_read` | `path` | Read a file |
| `file_write` | `path`, `content` | Write a file |
| `file_edit` | `path`, `old_string`, `new_string` | Edit a file |

Gemini CLI sessions currently do not include tool invocation data in their log format.

### Invariants

- Tool use objects are preserved as-is from the source agent's format.
- The renderer handles unknown tool names gracefully by displaying the name and raw input.
- Tool use `id` values are unique within a session and used to correlate with tool results.

## 5. Tool result object

Represents the output returned by a tool invocation.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `content` | string | yes | Text content of the tool result |

### Invariants

- Tool results with empty or whitespace-only content are excluded during model building.
- Tool results are stored in the same message entry as the corresponding tool use when possible, or in a subsequent user message (depending on the agent's message structure).

## 6. Thinking block

Represents an internal reasoning step by the assistant.

Stored as an array of strings, where each string is one thinking block's text content.

```json
{
  "thinking": [
    "Let me analyze the error message...",
    "The issue is in the authentication middleware."
  ]
}
```

### Source mapping

| Agent | Source field | Notes |
|-------|------------|-------|
| Claude Code | `content[].type == "thinking"` → `content[].thinking` | Extended thinking blocks |
| Codex CLI | `content[].type == "thinking"` → `content[].thinking` | Reasoning blocks |
| Gemini CLI | `thoughts[].description` | Thought descriptions |

## 7. Timestamp semantics

Timestamps record when each message was sent or received during the original session.

| Agent | Source field | Format |
|-------|------------|--------|
| Claude Code | `data.timestamp` (top-level JSONL record) | ISO 8601 with timezone |
| Codex CLI | `data.timestamp` (top-level JSONL record) | ISO 8601 with timezone |
| Gemini CLI | `message.timestamp` | ISO 8601 |

### Usage by renderers

- **Markdown / HTML**: timestamps displayed as text labels
- **Player / Terminal**: timestamps power Alibai Mode features:
  - Side clocks: analog clock (44x44px) next to each message
  - Fixed clock: large analog clock (100x100px) at bottom-right
  - Uniform mode: ignore timestamps, equal intervals
  - Real-time mode: respect actual time gaps between messages
  - Compressed mode: compress entire session to 60 seconds proportionally

## 8. Message numbering

Messages are numbered sequentially starting from 1 in the order they appear in the `messages` array. This numbering is used by:

- `--range` option for filtering (e.g., `"1-50,53-"`)
- Player/terminal UI progress display
- Time labels in Alibai Mode

## 9. Schema evolution

The common model schema is intentionally minimal. When adding support for new agents or new data types:

1. New fields should be added as optional with sensible defaults (empty string or empty array).
2. Existing fields must not change their type or semantics.
3. Renderers must tolerate missing optional fields gracefully.
4. The `agent` field allows renderers to apply agent-specific logic when needed, but this should be minimized.
