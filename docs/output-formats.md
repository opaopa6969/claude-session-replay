[日本語版](ja/output-formats.md)

# Output Formats

This document specifies each output format produced by the renderer (`log-model-renderer.py`).

## 1. Overview

All formats are produced by the same renderer from the same common model. The format is selected via the `-f` / `--format` flag.

| Format | Flag | File Extension | Interactive | Dependencies |
|--------|------|---------------|-------------|-------------|
| Markdown | `md` | `.md` | No | None |
| HTML | `html` | `.html` | No | None |
| Player | `player` | `.html` | Yes | Browser |
| Terminal | `terminal` | `.html` | Yes | Browser |

## 2. Markdown (`md`)

The simplest output format. Plain Markdown text suitable for reading in any text editor or Markdown viewer.

### Structure

```markdown
## User

<message text>

## Assistant

<message text>

**Read**: `path/to/file`

> (tool result content)
```

### Content mapping

| Model field | Markdown rendering |
|-------------|-------------------|
| `role: "user"` | `## User` heading |
| `role: "assistant"` | `## Assistant` heading |
| `text` | Plain text paragraph |
| `tool_uses` | Bold tool name + formatted parameters |
| `tool_results` | Blockquote with result content |
| `thinking` | Not rendered (omitted) |
| `timestamp` | Not rendered (omitted) |

### Tool formatting

Each tool type has specialized formatting:

| Tool | Format |
|------|--------|
| Read | `**Read**: \`file_path\`` |
| Write | `**Write**: \`file_path\` (N lines)` |
| Edit | `**Edit**: \`file_path\`` + diff code block |
| Bash | `**Bash**:` + bash code block |
| Grep | `**Grep**: \`pattern\` in \`path\`` |
| Glob | `**Glob**: \`pattern\`` |
| Task | `**Task**: description` |
| Other | `**ToolName**` |

## 3. HTML (`html`)

Static chat UI with styled message bubbles. No JavaScript required.

### Themes

| Theme | Flag | Background | User bubble | Assistant bubble |
|-------|------|-----------|-------------|-----------------|
| Light | `light` | White | Green | Blue |
| Console | `console` | Dark | Green (dark) | Blue (dark) |

### Structure

Self-contained HTML file with embedded CSS. No external resources.

```html
<!DOCTYPE html>
<html>
<head><style>/* all CSS embedded */</style></head>
<body>
  <div class="chat-container">
    <div class="message user">...</div>
    <div class="message assistant">...</div>
  </div>
</body>
</html>
```

### Content mapping

| Model field | HTML rendering |
|-------------|---------------|
| `role: "user"` | Green bubble (`.message.user`) |
| `role: "assistant"` | Blue bubble (`.message.assistant`) |
| `text` | Rendered with Markdown-to-HTML (code blocks, links, etc.) |
| `tool_uses` | Styled tool blocks within assistant bubble |
| `tool_results` | Collapsible result sections |
| `thinking` | Collapsible thinking sections (if present) |
| `timestamp` | Displayed as time label |

## 4. Player (`player`)

Interactive replay player with JavaScript-driven playback controls.

### Features

- **Playback**: Play/pause, step forward/backward, seek via progress bar
- **Speed control**: 0.25x to 16x via slider
- **Message navigation**: keyboard shortcuts for fast navigation
- **Alibai Mode**: timestamp-based time visualization (see below)
- **Theme**: light and dark (console) themes
- **Self-contained**: single HTML file, works offline

### Keyboard shortcuts

| Key | Action |
|-----|--------|
| `Space` | Play / Pause |
| `→` | Next message |
| `←` | Previous message |
| `Home` | Jump to first message |
| `End` | Show all messages |
| `T` | Skip tool messages during playback |
| `E` | Toggle empty tool visibility |
| `D` | Toggle tool detail visibility |
| `g` | Jump to specific time |
| `j` / `k` | Scroll messages |

### Alibai Mode

Alibai Mode enables timestamp-based visualization using data from the common model's `timestamp` fields.

**Clock displays** (independently togglable):
- **Side clocks**: small analog clock (44x44px) displayed to the left of each message, showing the timestamp of that message
- **Fixed clock**: large analog clock (100x100px) fixed at the bottom-right corner, showing the current playback time

**Playback modes** (mutually exclusive):
- **Uniform** (default): messages appear at equal intervals (800ms / speed). Timestamps are displayed but do not affect timing.
- **Real-time**: messages appear with delays proportional to the actual time gaps between them. Respects the original session pacing.
- **Compressed**: the entire session is compressed to 60 seconds. Time gaps are proportional to the original but scaled down.

### HTML structure

```html
<div id="controls">
  <button id="btnPlay">play</button>
  <input id="speed" type="range" ...>
  <div id="progress">...</div>
  <!-- Alibai Mode controls -->
  <input type="checkbox" id="chkSideClocks"> Side clocks
  <input type="checkbox" id="chkFixedClock"> Fixed clock
  <input type="radio" name="playMode" value="uniform"> Uniform
  <input type="radio" name="playMode" value="realtime"> Real-time
  <input type="radio" name="playMode" value="compressed"> Compressed
</div>
<div id="messages">
  <div class="message" data-ts="..." style="display:none">...</div>
</div>
```

## 5. Terminal (`terminal`)

Claude Code terminal UI replica with faithful visual reproduction of the terminal experience.

### Visual elements

| Element | Appearance |
|---------|-----------|
| User input | `>` prompt with blue background bar |
| Assistant text | Orange left border, dark background |
| Tool blocks | Indented with tool-specific icons |
| Spinner | Orange `●` during processing → green `✓` on completion |
| Tables | Rendered as HTML tables |

### Tool block rendering

Each tool type is rendered to match Claude Code's actual terminal output:

| Tool | Terminal rendering |
|------|-------------------|
| Read | `📄 Read file_path` |
| Write | `📝 Write file_path (N lines)` |
| Edit | `✏️ Edit file_path` with diff display |
| Bash | `$ command` with output block |
| Grep | `🔍 Grep pattern in path` |
| Glob | `📁 Glob pattern` |
| Task | `📋 Task description` |

### Keyboard shortcuts

Same as Player format (see section 4).

### HTML structure

```html
<div id="t-controls">
  <button id="t-play">play</button>
  <input id="t-speed" type="range" ...>
  <div id="t-progress">...</div>
</div>
<div id="t-messages">
  <div class="t-msg" style="display:none">...</div>
</div>
```

## 6. Common options

These options apply across all formats:

| Option | Flag | Values | Default | Description |
|--------|------|--------|---------|-------------|
| Format | `-f`, `--format` | md, html, player, terminal | md | Output format |
| Theme | `-t`, `--theme` | light, console | light | Color theme |
| Output | `-o`, `--output` | file path | auto-generated | Output file path |
| Range | `--range` | e.g., `1-50,53-` | all | Message range filter |
| ANSI mode | `--ansi-mode` | strip, color | strip | ANSI escape handling |
| Truncate | `--truncate-length` | integer | 0 (disabled) | Max content length per block |

### Range specification syntax

| Pattern | Meaning |
|---------|---------|
| `1-50` | Messages 1 through 50 |
| `53-` | Messages 53 through last |
| `-10` | Messages 1 through 10 |
| `7` | Message 7 only |
| `1-50,53-` | Messages 1-50 and 53-end |

Message numbers are 1-indexed and correspond to the position in the common model's `messages` array.

## 7. ANSI escape handling

Terminal tool output often contains ANSI escape sequences for colors and formatting. The `--ansi-mode` option controls how these are handled:

| Mode | Behavior |
|------|----------|
| `strip` (default) | All ANSI escape sequences are removed |
| `color` | Color sequences are converted to HTML `<span>` elements with appropriate CSS |
