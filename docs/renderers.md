[日本語版](renderers-ja.md)

# Renderers

Full option reference for each output format produced by claude-session-replay.

---

## Table of Contents

- [Overview](#overview)
- [Markdown](#markdown-md)
- [HTML](#html-html)
- [Player](#player-player)
  - [Alibai Mode](#alibai-mode)
- [Terminal](#terminal-terminal)
- [MP4](#mp4)
- [PDF](#pdf)
- [GIF](#gif)
- [Common Options](#common-options)

---

## Overview

All text/HTML formats are produced by `log-model-renderer.py`. Video, PDF, and GIF renderers are separate scripts that use a headless browser.

| Format | Flag | Script | Dependencies | Interactive |
|--------|------|--------|-------------|-------------|
| Markdown | `md` | `log-model-renderer.py` | None | No |
| HTML | `html` | `log-model-renderer.py` | None | No |
| Player | `player` | `log-model-renderer.py` | Browser | Yes |
| Terminal | `terminal` | `log-model-renderer.py` | Browser | Yes |
| MP4 | — | `log-replay-mp4.py` | playwright, ffmpeg | No |
| PDF | — | `log-replay-pdf.py` | playwright | No |
| GIF | — | `log-replay-gif.py` | playwright, pillow/ffmpeg | No |

All HTML output is **self-contained** — CSS and JS are embedded inline. No external resources, no CDN, works offline.

---

## Markdown (`md`)

Plain Markdown text. Use for reading in any text editor or piping to other tools.

```bash
python3 log-model-renderer.py session.model.json -f md
python3 log-model-renderer.py session.model.json -f md -o session.md
```

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

| Model field | Rendering |
|-------------|-----------|
| `role: "user"` | `## User` heading |
| `role: "assistant"` | `## Assistant` heading |
| `text` | Plain paragraph |
| `tool_uses` | Bold tool name + formatted parameters |
| `tool_results` | Blockquote with result content |
| `thinking` | Not rendered |
| `timestamp` | Not rendered |

---

## HTML (`html`)

Static chat UI. No JavaScript — works in any browser or Markdown viewer that supports HTML.

```bash
python3 log-model-renderer.py session.model.json -f html              # light theme (default)
python3 log-model-renderer.py session.model.json -f html -t console   # dark theme
```

### Options

| Option | Values | Description |
|--------|--------|-------------|
| `-t` / `--theme` | `light` (default), `console` | Color theme |

### Appearance

- User messages: green speech bubbles, right-aligned
- Assistant messages: blue speech bubbles, left-aligned
- Tool blocks: compact formatted blocks
- No playback controls

---

## Player (`player`)

Interactive HTML player. Replay messages one by one with full playback controls. Includes **Alibai Mode** for timestamp visualization.

```bash
python3 log-model-renderer.py session.model.json -f player              # dark theme (default)
python3 log-model-renderer.py session.model.json -f player -t light     # light theme
python3 log-model-renderer.py session.model.json -f player --range "1-50"
```

### Options

| Option | Values | Description |
|--------|--------|-------------|
| `-t` / `--theme` | `light`, `console` (default) | Color theme |
| `--range` | e.g. `1-50,53-` | Message range filter |
| `--ansi-mode` | `strip` (default), `color` | ANSI escape handling |

### Playback controls

| Key | Action |
|-----|--------|
| `Space` | Play / Pause |
| `→` | Next message |
| `←` | Previous message |
| `Home` | Jump to start |
| `End` | Jump to end |
| `g` | Jump to timestamp |
| `j` / `k` | Scroll message content |
| `T` | Skip tool messages during playback |
| `E` | Toggle empty tool visibility |
| `D` | Toggle tool details |

Speed slider: 0.25x–16x. Progress bar: click to seek.

### Alibai Mode

Alibai Mode visualizes actual timestamps using analog clocks and alternative playback timings.

**Clock displays** (checkboxes):
- **Side clocks** — 44×44 px analog clock beside each message
- **Fixed clock** — 100×100 px analog clock fixed at bottom-right

**Playback modes** (radio buttons):
- **Uniform** (default) — equal interval (800 ms ÷ speed)
- **Real-time** — honor actual time gaps between messages
- **Compressed** — compress entire session to 60 seconds with relative proportions

Enable via the controls panel in the player, or set the start time via:

```bash
# Web UI: "Alibai Time" field (HH:MM format)
```

---

## Terminal (`terminal`)

Replica of Claude Code's terminal UI. Renders the session as an animated terminal — ideal for screencasts.

```bash
python3 log-model-renderer.py session.model.json -f terminal
python3 log-model-renderer.py session.model.json -f terminal --range "5-20"
```

### Options

| Option | Values | Description |
|--------|--------|-------------|
| `--range` | e.g. `1-50` | Message range filter |
| `--ansi-mode` | `strip` (default), `color` | ANSI escape handling |

### Appearance

- User input: `>` prompt with blue background
- Assistant response: orange left border
- Tool blocks: realistic Read/Write/Edit/Bash/Grep/Glob/Task rendering
- Spinner animation: orange `●` transitions to green `✓`
- Table rendering support

Playback controls identical to Player.

---

## MP4

Records the Player or Terminal HTML in a headless browser and encodes to MP4 using FFmpeg.

**Dependencies**: `playwright`, `ffmpeg` (system binary)

```bash
python3 log-replay-mp4.py --agent claude session.jsonl \
    -f player -o out.mp4 \
    --width 1280 --height 720 --fps 30 --speed 2.0
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--width` | 1280 | Video width (px) |
| `--height` | 720 | Video height (px) |
| `--fps` | 30 | Frame rate |
| `--speed` | 2.0 | Playback speed multiplier |
| `-f` / `--format` | `player` | `player` or `terminal` |
| `-t` / `--theme` | `console` | Color theme |
| `--range` | — | Message range |

### Setup

```bash
python3 -m pip install playwright
python3 -m playwright install

# Ubuntu/Debian
sudo apt-get install ffmpeg

# macOS
brew install ffmpeg
```

---

## PDF

Renders the HTML player in a headless browser and exports to PDF using Playwright's print-to-PDF.

**Dependencies**: `playwright`

```bash
python3 log-replay-pdf.py --agent claude session.jsonl -o out.pdf
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `-f` / `--format` | `html` | `html` or `player` |
| `-t` / `--theme` | `light` | Color theme |
| `--range` | — | Message range |

### Setup

```bash
python3 -m pip install playwright
python3 -m playwright install
```

---

## GIF

Captures screenshots during headless playback and assembles an animated GIF using Pillow. Falls back to FFmpeg if Pillow is unavailable.

**Dependencies**: `playwright`, `pillow` (or `ffmpeg`)

```bash
python3 log-replay-gif.py --agent claude session.jsonl -o out.gif
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `-f` / `--format` | `player` | `player` or `terminal` |
| `-t` / `--theme` | `console` | Color theme |
| `--speed` | 2.0 | Playback speed |
| `--range` | — | Message range |

### Setup

```bash
python3 -m pip install playwright pillow
python3 -m playwright install
# or: install ffmpeg as fallback
```

---

## Common Options

All renderer invocations via `log-model-renderer.py` support:

| Option | Description |
|--------|-------------|
| `-f` / `--format` | Output format: `md`, `html`, `player`, `terminal` |
| `-t` / `--theme` | Theme: `light`, `console` |
| `-o` / `--output` | Output file path (default: derived from input filename) |
| `--range` | Message range, e.g. `1-50,53-` |
| `--ansi-mode` | ANSI handling: `strip` (default) or `color` |

### Range syntax

| Syntax | Meaning |
|--------|---------|
| `1-50` | Messages 1–50 |
| `53-` | Message 53 to end |
| `-10` | Messages 1–10 |
| `7` | Message 7 only |

Multiple ranges: `1-10,20-30,50-`
