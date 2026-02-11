#!/usr/bin/env python3
"""Render common log model (JSON) to Markdown / HTML / player / terminal."""

import argparse
import html
import json
import os
import re


def _extract_text_from_model(entry):
    return entry.get("text", "") or ""


def _extract_tool_uses_from_model(entry):
    return entry.get("tool_uses", []) or []


def _extract_tool_results_from_model(entry):
    return entry.get("tool_results", []) or []


def parse_range_spec(spec, total):
    """Parse range spec like '1-50,53-' into zero-based indices."""
    if not spec:
        return list(range(total))
    indices = set()
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    for part in parts:
        if "-" in part:
            start_str, end_str = part.split("-", 1)
            start = int(start_str) if start_str.strip() else 1
            end = int(end_str) if end_str.strip() else total
            if start < 1:
                start = 1
            if end > total:
                end = total
            if start <= end:
                for i in range(start, end + 1):
                    indices.add(i - 1)
        else:
            try:
                idx = int(part)
            except ValueError:
                continue
            if 1 <= idx <= total:
                indices.add(idx - 1)
    return sorted(indices)


def filter_messages_by_range(messages, spec):
    if not spec:
        return messages
    total = len(messages)
    indices = parse_range_spec(spec, total)
    return [messages[i] for i in indices]


def format_tool_use(tool_use):
    name = tool_use.get("name", "Unknown")
    tool_input = tool_use.get("input", {})

    if name == "Read":
        file_path = tool_input.get("file_path", "")
        return f"**Read**: `{file_path}`"
    if name == "Write":
        file_path = tool_input.get("file_path", "")
        content = tool_input.get("content", "")
        line_count = content.count("\n") + 1 if content else 0
        return f"**Write**: `{file_path}` ({line_count} lines)"
    if name == "Edit":
        file_path = tool_input.get("file_path", "")
        old_string = tool_input.get("old_string", "")
        new_string = tool_input.get("new_string", "")
        return f"**Edit**: `{file_path}`\n```diff\n- {old_string[:200]}\n+ {new_string[:200]}\n```"
    if name == "Bash":
        command = tool_input.get("command", "")
        return f"**Bash**:\n```bash\n{command}\n```"
    if name == "Grep":
        pattern = tool_input.get("pattern", "")
        path = tool_input.get("path", "")
        return f"**Grep**: `{pattern}` in `{path}`"
    if name == "Glob":
        pattern = tool_input.get("pattern", "")
        return f"**Glob**: `{pattern}`"
    if name == "Task":
        description = tool_input.get("description", "")
        return f"**Task**: {description}"
    return f"**{name}**"


def format_tool_result_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        return "\n".join(texts)
    return str(content)


def escape(text):
    return html.escape(text)


def _inline_format(escaped_line):
    escaped_line = escaped_line.replace("**", "\x00")
    parts = escaped_line.split("\x00")
    if len(parts) >= 3:
        rebuilt = []
        for i, part in enumerate(parts):
            if i % 2 == 1:
                rebuilt.append(f"<strong>{part}</strong>")
            else:
                rebuilt.append(part)
        escaped_line = "".join(rebuilt)

    escaped_line = escaped_line.replace("`", "\x01")
    parts = escaped_line.split("\x01")
    if len(parts) >= 3:
        rebuilt = []
        for i, part in enumerate(parts):
            if i % 2 == 1:
                rebuilt.append(f"<code>{part}</code>")
            else:
                rebuilt.append(part)
        escaped_line = "".join(rebuilt)
    return escaped_line


def _split_table_line(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_table_separator(line):
    stripped = line.strip()
    if "|" not in stripped:
        return False
    allowed = set("|:- ")
    return all(ch in allowed for ch in stripped)


# SGR color sequences only
ANSI_SGR_RE = re.compile(r"\x1b\[[0-9;]*m")
# Any CSI sequence (cursor movement, erase, etc.)
ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# OSC sequences (e.g., hyperlinks, titles)
ANSI_OSC_RE = re.compile(r"\x1b\].*?\x1b\\|\x1b\].*?\x07")
# Single ESC sequences
ANSI_ESC_RE = re.compile(r"\x1b[@-Z\\-_]")


def strip_ansi(text):
    text = ANSI_OSC_RE.sub("", text)
    text = ANSI_CSI_RE.sub("", text)
    text = ANSI_ESC_RE.sub("", text)
    text = ANSI_SGR_RE.sub("", text)
    return text


def ansi_to_html(text):
    # Basic SGR color support (30-37,90-97,40-47,100-107) + reset/bold
    text = ANSI_OSC_RE.sub("", text)
    text = ANSI_CSI_RE.sub("", text)
    text = ANSI_ESC_RE.sub("", text)
    fg = None
    bg = None
    bold = False
    spans = []

    def open_span():
        styles = []
        if fg:
            styles.append(f"color:{fg}")
        if bg:
            styles.append(f"background:{bg}")
        if bold:
            styles.append("font-weight:bold")
        if styles:
            return f'<span style="{";".join(styles)}">'
        return ""

    def close_span():
        return "</span>" if fg or bg or bold else ""

    # color maps
    fg_map = {
        30: "#000000", 31: "#e06c75", 32: "#98c379", 33: "#e5c07b",
        34: "#61afef", 35: "#c678dd", 36: "#56b6c2", 37: "#dcdfe4",
        90: "#5c6370", 91: "#e06c75", 92: "#98c379", 93: "#e5c07b",
        94: "#61afef", 95: "#c678dd", 96: "#56b6c2", 97: "#ffffff",
    }
    bg_map = {
        40: "#000000", 41: "#e06c75", 42: "#98c379", 43: "#e5c07b",
        44: "#61afef", 45: "#c678dd", 46: "#56b6c2", 47: "#dcdfe4",
        100: "#5c6370", 101: "#e06c75", 102: "#98c379", 103: "#e5c07b",
        104: "#61afef", 105: "#c678dd", 106: "#56b6c2", 107: "#ffffff",
    }

    parts = ANSI_SGR_RE.split(text)
    codes = ANSI_SGR_RE.findall(text)
    for idx, part in enumerate(parts):
        if part:
            spans.append(open_span() + escape(part) + close_span())
        if idx < len(codes):
            seq = codes[idx][2:-1]
            if not seq:
                continue
            for code_str in seq.split(";"):
                if not code_str:
                    continue
                try:
                    code = int(code_str)
                except ValueError:
                    continue
                if code == 0:
                    fg = None
                    bg = None
                    bold = False
                elif code == 1:
                    bold = True
                elif code in fg_map:
                    fg = fg_map[code]
                elif code in bg_map:
                    bg = bg_map[code]
                elif code == 22:
                    bold = False
    return "".join(spans)


def markdown_to_html_simple(text, ansi_mode="strip"):
    lines = text.splitlines()
    html_lines = []
    in_code = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            if not in_code:
                in_code = True
                html_lines.append("<pre><code>")
            else:
                in_code = False
                html_lines.append("</code></pre>")
            i += 1
            continue
        if in_code:
            if ansi_mode == "color":
                html_lines.append(ansi_to_html(line))
            else:
                html_lines.append(escape(strip_ansi(line)))
            i += 1
            continue

        # Table detection
        if i + 1 < len(lines) and "|" in line and _is_table_separator(lines[i + 1]):
            headers = _split_table_line(line)
            i += 2
            rows = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_split_table_line(lines[i]))
                i += 1

            html_lines.append("<table>")
            html_lines.append("<thead><tr>")
            for h in headers:
                cell = ansi_to_html(h) if ansi_mode == "color" else escape(strip_ansi(h))
                html_lines.append(f"<th>{_inline_format(cell)}</th>")
            html_lines.append("</tr></thead>")
            html_lines.append("<tbody>")
            for row in rows:
                html_lines.append("<tr>")
                for cell in row:
                    cell_html = ansi_to_html(cell) if ansi_mode == "color" else escape(strip_ansi(cell))
                    html_lines.append(f"<td>{_inline_format(cell_html)}</td>")
                html_lines.append("</tr>")
            html_lines.append("</tbody></table>")
            continue

        if ansi_mode == "color":
            line = ansi_to_html(line)
        else:
            line = escape(strip_ansi(line))
        line = _inline_format(line)
        if line.strip().startswith("# "):
            html_lines.append(f"<h2>{line[2:].strip()}</h2>")
        elif line.strip().startswith("## "):
            html_lines.append(f"<h3>{line[3:].strip()}</h3>")
        elif line.strip().startswith("### "):
            html_lines.append(f"<h4>{line[4:].strip()}</h4>")
        else:
            html_lines.append(f"<p>{line}</p>")
        i += 1

    if in_code:
        html_lines.append("</code></pre>")
    return "\n".join(html_lines)


# ---------------------------------------------------------------------------
# Markdown output
# ---------------------------------------------------------------------------

def convert_to_markdown(model, input_path, ansi_mode="strip", range_spec=None):
    lines = []
    lines.append("# Session Transcript\n")
    lines.append(f"Source: `{os.path.basename(input_path)}`\n")
    lines.append("---\n")

    message_number = 0

    messages = filter_messages_by_range(model.get("messages", []), range_spec)
    for entry in messages:
        role = entry.get("role", "")
        if role == "user":
            message_number += 1

        text = _extract_text_from_model(entry)
        tool_uses = _extract_tool_uses_from_model(entry)
        tool_results = _extract_tool_results_from_model(entry)

        if not text.strip() and not tool_uses and not tool_results:
            continue

        if role == "user":
            lines.append(f"## User ({message_number})\n")
        elif role == "assistant":
            lines.append("## Assistant\n")
        else:
            continue

        text = strip_ansi(text)
        if text.strip():
            lines.append(f"{text.strip()}\n")

        if tool_uses:
            for tool_use in tool_uses:
                formatted = format_tool_use(tool_use)
                lines.append(f"\n{formatted}\n")

        if tool_results:
            for result in tool_results:
                result_content = result.get("content", "")
                result_text = format_tool_result_content(result_content)
                result_text = strip_ansi(result_text)
                if result_text.strip():
                    truncated = result_text[:500]
                    if len(result_text) > 500:
                        truncated += "\n... (truncated)"
                    lines.append("\n<details><summary>Tool Result</summary>\n")
                    lines.append(f"```\n{truncated}\n```\n")
                    lines.append("</details>\n")

        lines.append("")

    lines.append("\n---\n*Converted from session transcript.*\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML output
# ---------------------------------------------------------------------------

THEME_LIGHT = """\
  :root {
    --body-bg: #f0f0f0;
    --body-color: #333;
    --user-bg: #dcf8c6;
    --user-border: #a5d6a7;
    --user-label: #2e7d32;
    --assistant-bg: #e3f2fd;
    --assistant-border: #90caf9;
    --assistant-label: #1565c0;
    --tool-bg: #fff3e0;
    --tool-border: #ffcc80;
    --tool-name-color: #e65100;
    --result-bg: #f5f5f5;
    --result-color: #333;
    --code-bg: #263238;
    --code-color: #eeffff;
    --inline-code-bg: rgba(0,0,0,0.06);
    --details-summary: #666;
    --footer-color: #999;
    --footer-border: #ddd;
    --h1-color: #333;
  }
"""

THEME_CONSOLE = """\
  :root {
    --body-bg: #1a1b26;
    --body-color: #c0caf5;
    --user-bg: #1e2030;
    --user-border: #9ece6a;
    --user-label: #9ece6a;
    --assistant-bg: #16161e;
    --assistant-border: #7aa2f7;
    --assistant-label: #7aa2f7;
    --tool-bg: #1a1e2e;
    --tool-border: #e0af68;
    --tool-name-color: #ff9e64;
    --result-bg: #1a1b26;
    --result-color: #a9b1d6;
    --code-bg: #0d0e17;
    --code-color: #a9b1d6;
    --inline-code-bg: rgba(255,255,255,0.08);
    --details-summary: #565f89;
    --footer-color: #565f89;
    --footer-border: #292e42;
    --h1-color: #c0caf5;
  }
"""

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Session Transcript</title>
<style>
  {{THEME}}
  --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Hiragino Sans", "Noto Sans CJK JP", sans-serif;
  --mono: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: var(--font);
    background: var(--body-bg);
    color: var(--body-color);
    padding: 20px;
    line-height: 1.6;
  }
  h1 {
    text-align: center;
    margin-bottom: 24px;
    color: var(--h1-color);
  }
  .chat-container {
    max-width: 900px;
    margin: 0 auto;
  }
  .message {
    margin: 12px 0;
    padding: 14px 18px;
    border-radius: 12px;
    border-left: 4px solid;
    word-wrap: break-word;
    overflow-wrap: break-word;
  }
  .message.user {
    background: var(--user-bg);
    border-left-color: var(--user-border);
    margin-right: 60px;
  }
  .message.assistant {
    background: var(--assistant-bg);
    border-left-color: var(--assistant-border);
    margin-left: 60px;
  }
  .role-label {
    font-weight: bold;
    font-size: 0.8em;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 6px;
  }
  .message.user .role-label { color: var(--user-label); }
  .message.assistant .role-label { color: var(--assistant-label); }
  .message-body { white-space: pre-wrap; }
  .message-body p { margin: 0.4em 0; }
  .tool-section {
    margin-top: 10px;
    padding: 8px 12px;
    background: var(--tool-bg);
    border: 1px solid var(--tool-border);
    border-radius: 6px;
    font-size: 0.9em;
  }
  .tool-section .tool-name {
    font-weight: bold;
    color: var(--tool-name-color);
  }
  pre {
    background: var(--code-bg);
    color: var(--code-color);
    padding: 10px 14px;
    border-radius: 6px;
    overflow-x: auto;
    font-family: var(--mono);
    font-size: 0.85em;
    margin: 6px 0;
    white-space: pre-wrap;
    word-wrap: break-word;
  }
  code {
    font-family: var(--mono);
    background: var(--inline-code-bg);
    padding: 1px 4px;
    border-radius: 3px;
    font-size: 0.9em;
  }
  pre code { background: none; padding: 0; }
  table {
    border-collapse: collapse;
    margin: 8px 0;
    width: 100%;
    font-size: 0.9em;
  }
  th, td {
    border: 1px solid var(--tool-border, #ddd);
    padding: 6px 12px;
    text-align: left;
  }
  th { background: var(--tool-bg, #f5f5f5); font-weight: bold; }
  details {
    margin: 6px 0;
    font-size: 0.85em;
  }
  details summary {
    cursor: pointer;
    color: var(--details-summary);
    font-style: italic;
  }
  details pre {
    background: var(--result-bg);
    color: var(--result-color);
    max-height: 300px;
    overflow-y: auto;
  }
  .controls {
    position: sticky;
    top: 0;
    background: var(--body-bg);
    padding: 8px 0 10px;
    margin-bottom: 6px;
    border-bottom: 1px solid var(--footer-border);
    z-index: 100;
  }
  .progress {
    width: 100%;
    height: 6px;
    background: var(--tool-border);
    border-radius: 4px;
    cursor: pointer;
    position: relative;
  }
  .progress span {
    display: block;
    height: 100%;
    width: 0;
    background: var(--tool-name-color);
    border-radius: 4px;
  }
  .progress-info {
    margin-top: 6px;
    text-align: center;
    font-size: 12px;
    color: var(--details-summary);
  }
  .footer {
    text-align: center;
    color: var(--footer-color);
    font-size: 0.8em;
    margin-top: 30px;
    padding-top: 16px;
    border-top: 1px solid var(--footer-border);
  }
</style>
</head>
<body class="trim-empty">
<h1>Session Transcript</h1>
<div class="controls">
  <div class="progress" id="progress"><span id="progressBar"></span></div>
  <div class="progress-info" id="progressInfo">0 / 0</div>
</div>
<div class="chat-container">
{{MESSAGES}}
</div>
<div class="footer">Converted from session transcript.</div>
<script>
(() => {
  const messages = Array.from(document.querySelectorAll('.message'));
  const progress = document.getElementById('progress');
  const progressBar = document.getElementById('progressBar');
  const progressInfo = document.getElementById('progressInfo');

  function updateInfo(idx) {
    const total = messages.length;
    progressInfo.textContent = `${idx + 1} / ${total}`;
    const val = total ? ((idx + 1) / total) * 100 : 0;
    progressBar.style.width = `${val}%`;
  }

  progress.addEventListener('click', (e) => {
    const rect = progress.getBoundingClientRect();
    const ratio = (e.clientX - rect.left) / rect.width;
    const idx = Math.max(0, Math.min(messages.length - 1, Math.floor(ratio * (messages.length - 1))));
    const target = messages[idx];
    if (target) {
      target.scrollIntoView({ behavior: 'auto', block: 'nearest' });
      updateInfo(idx);
    }
  });

  updateInfo(0);
})();
</script>
</body>
</html>
"""


def format_tool_use_html(tool_use):
    name = tool_use.get("name", "Unknown")
    tool_input = tool_use.get("input", {})

    if name == "Read":
        file_path = escape(tool_input.get("file_path", ""))
        return f'<span class="tool-name">Read</span>: <code>{file_path}</code>'
    if name == "Write":
        file_path = escape(tool_input.get("file_path", ""))
        content = tool_input.get("content", "")
        line_count = content.count("\n") + 1 if content else 0
        return f'<span class="tool-name">Write</span>: <code>{file_path}</code> ({line_count} lines)'
    if name == "Edit":
        file_path = escape(tool_input.get("file_path", ""))
        old_string = escape(tool_input.get("old_string", ""))
        new_string = escape(tool_input.get("new_string", ""))
        return (f'<span class="tool-name">Edit</span>: <code>{file_path}</code>'
                f'<pre>- {old_string[:200]}\n+ {new_string[:200]}</pre>')
    if name == "Bash":
        command = escape(tool_input.get("command", ""))
        return f'<span class="tool-name">Bash</span>:<pre>{command}</pre>'
    if name == "Grep":
        pattern = escape(tool_input.get("pattern", ""))
        path = escape(tool_input.get("path", ""))
        return f'<span class="tool-name">Grep</span>: <code>{pattern}</code> in <code>{path}</code>'
    if name == "Glob":
        pattern = escape(tool_input.get("pattern", ""))
        return f'<span class="tool-name">Glob</span>: <code>{pattern}</code>'
    if name == "Task":
        description = escape(tool_input.get("description", ""))
        return f'<span class="tool-name">Task</span>: {description}'
    return f'<span class="tool-name">{escape(name)}</span>'


def convert_to_html(model, input_path, theme="light", ansi_mode="strip", range_spec=None):
    message_blocks = []
    message_number = 0

    messages = filter_messages_by_range(model.get("messages", []), range_spec)
    for entry in messages:
        role = entry.get("role", "")
        if role == "user":
            message_number += 1

        text = _extract_text_from_model(entry)
        tool_uses = _extract_tool_uses_from_model(entry)
        tool_results = _extract_tool_results_from_model(entry)

        if not text.strip() and not tool_uses and not tool_results:
            continue

        if role == "user":
            parts = [f'<div class="role-label">User ({message_number})</div>']
            wrapper_class = "user"
        elif role == "assistant":
            parts = ['<div class="role-label">Assistant</div>']
            wrapper_class = "assistant"
        else:
            continue

        if text.strip():
            parts.append(f'<div class="message-body">{markdown_to_html_simple(text.strip(), ansi_mode=ansi_mode)}</div>')

        if tool_uses:
            for tool_use in tool_uses:
                formatted = format_tool_use_html(tool_use)
                parts.append(f'<div class="tool-section">{formatted}</div>')

        if tool_results:
            for result in tool_results:
                result_content = result.get("content", "")
                result_text = format_tool_result_content(result_content)
                if result_text.strip():
                    truncated = escape(result_text[:500])
                    if len(result_text) > 500:
                        truncated += "\n... (truncated)"
                    parts.append(f"<details><summary>Tool Result</summary><pre>{truncated}</pre></details>")

        message_blocks.append(f'<div class="message {wrapper_class}">\n' + "\n".join(parts) + "\n</div>")

    theme_css = THEME_CONSOLE if theme == "console" else THEME_LIGHT
    all_messages = "\n".join(message_blocks)
    return HTML_TEMPLATE.replace("{{THEME}}", theme_css).replace("{{MESSAGES}}", all_messages)


# ---------------------------------------------------------------------------
# Player output
# ---------------------------------------------------------------------------

PLAYER_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Session Player</title>
<style>
  {{THEME}}
  --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Hiragino Sans", "Noto Sans CJK JP", sans-serif;
  --mono: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: var(--font);
    background: var(--body-bg);
    color: var(--body-color);
    padding: 20px;
    line-height: 1.6;
  }
  h1 {
    text-align: center;
    margin-bottom: 24px;
    color: var(--h1-color);
  }
  .chat-container {
    max-width: 900px;
    margin: 0 auto;
  }
  .message {
    margin: 12px 0;
    padding: 14px 18px;
    border-radius: 12px;
    border-left: 4px solid;
    word-wrap: break-word;
    overflow-wrap: break-word;
  }
  .message.user {
    background: var(--user-bg);
    border-left-color: var(--user-border);
    margin-right: 60px;
  }
  .message.assistant {
    background: var(--assistant-bg);
    border-left-color: var(--assistant-border);
    margin-left: 60px;
  }
  .role-label {
    font-weight: bold;
    font-size: 0.8em;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 6px;
  }
  .message.user .role-label { color: var(--user-label); }
  .message.assistant .role-label { color: var(--assistant-label); }
  .message-body { white-space: pre-wrap; }
  .message-body p { margin: 0.4em 0; }
  .tool-section {
    margin-top: 10px;
    padding: 8px 12px;
    background: var(--tool-bg);
    border: 1px solid var(--tool-border);
    border-radius: 6px;
    font-size: 0.9em;
  }
  .tool-section .tool-name {
    font-weight: bold;
    color: var(--tool-name-color);
  }
  pre {
    background: var(--code-bg);
    color: var(--code-color);
    padding: 10px 14px;
    border-radius: 6px;
    overflow-x: auto;
    font-family: var(--mono);
    font-size: 0.85em;
    margin: 6px 0;
    white-space: pre-wrap;
    word-wrap: break-word;
  }
  code {
    font-family: var(--mono);
    background: var(--inline-code-bg);
    padding: 1px 4px;
    border-radius: 3px;
    font-size: 0.9em;
  }
  pre code { background: none; padding: 0; }
  table {
    border-collapse: collapse;
    margin: 8px 0;
    width: 100%;
    font-size: 0.9em;
  }
  th, td {
    border: 1px solid var(--tool-border, #ddd);
    padding: 6px 12px;
    text-align: left;
  }
  th { background: var(--tool-bg, #f5f5f5); font-weight: bold; }
  details {
    margin: 6px 0;
    font-size: 0.85em;
  }
  details summary {
    cursor: pointer;
    color: var(--details-summary);
    font-style: italic;
  }
  details pre {
    background: var(--result-bg);
    color: var(--result-color);
    max-height: 300px;
    overflow-y: auto;
  }
  .footer {
    text-align: center;
    color: var(--footer-color);
    font-size: 0.8em;
    margin-top: 30px;
    padding-top: 16px;
    border-top: 1px solid var(--footer-border);
  }

  .controls {
    position: sticky;
    top: 0;
    background: var(--body-bg);
    padding: 12px 0 10px;
    margin-bottom: 6px;
    border-bottom: 1px solid var(--footer-border);
    z-index: 100;
    display: flex;
    gap: 10px;
    align-items: center;
    justify-content: center;
    flex-wrap: wrap;
  }
  .controls button {
    background: var(--tool-bg);
    border: 1px solid var(--tool-border);
    color: var(--body-color);
    padding: 4px 12px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 13px;
    font-family: var(--mono);
  }
  .controls button.active {
    background: var(--tool-name-color);
    color: #fff;
    border-color: var(--tool-name-color);
  }
  .progress {
    width: 100%;
    height: 6px;
    background: var(--tool-border);
    border-radius: 4px;
    cursor: pointer;
    position: relative;
  }
  .progress span {
    display: block;
    height: 100%;
    width: 0;
    background: var(--tool-name-color);
    border-radius: 4px;
  }
  .speed {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
  }
</style>
</head>
<body>
<h1>Session Player</h1>
<div class="controls">
  <button id="btnPlay">play</button>
  <button id="btnPrev">prev</button>
  <button id="btnNext">next</button>
  <button id="btnSkipTool" class="">skip tools</button>
  <button id="btnFollow" class="active">follow</button>
  <div class="speed">
    <span>speed</span>
    <input id="speed" type="range" min="0.25" max="16" step="0.25" value="1" />
    <span id="speedVal">1.0x</span>
  </div>
</div>
<div class="progress" id="progress"><span id="progressBar"></span></div>
<div class="chat-container" id="chat">
{{MESSAGES}}
</div>
<div class="footer">Converted from session transcript.</div>
<script>
(() => {
  const messages = Array.from(document.querySelectorAll('.message'));
  let idx = -1;
  let playing = false;
  let skipTools = false;
  let follow = true;
  let timer = null;

  const btnPlay = document.getElementById('btnPlay');
  const btnPrev = document.getElementById('btnPrev');
  const btnNext = document.getElementById('btnNext');
  const btnSkipTool = document.getElementById('btnSkipTool');
  const btnFollow = document.getElementById('btnFollow');
  const speed = document.getElementById('speed');
  const speedVal = document.getElementById('speedVal');
  const progress = document.getElementById('progress');
  const progressBar = document.getElementById('progressBar');

  function isTool(msg) {
    return msg.querySelector('.tool-section') !== null || msg.querySelector('details') !== null;
  }

  function scrollToCurrent(instant = false) {
    const target = messages[idx];
    if (!target) return;
    const behavior = instant ? 'auto' : 'smooth';
    target.scrollIntoView({ behavior, block: 'nearest' });
  }

  function updateVisibility() {
    messages.forEach((m, i) => {
      if (i <= idx) {
        m.style.display = '';
      } else {
        m.style.display = 'none';
      }
    });
  }

  function updateProgress() {
    const total = messages.length;
    const val = total ? ((idx + 1) / total) * 100 : 0;
    progressBar.style.width = `${val}%`;
  }

  function step(dir) {
    let next = idx + dir;
    while (next >= 0 && next < messages.length) {
      if (skipTools && isTool(messages[next])) {
        next += dir;
        continue;
      }
      idx = next;
      updateVisibility();
      updateProgress();
      if (!playing || follow) scrollToCurrent(!playing);
      return;
    }
    idx = Math.max(-1, Math.min(messages.length - 1, next));
    updateVisibility();
    updateProgress();
    if (!playing || follow) scrollToCurrent(!playing);
  }

  function play() {
    if (playing) return;
    playing = true;
    btnPlay.textContent = 'pause';
    const interval = () => Math.max(50, 800 / parseFloat(speed.value));
    const tick = () => {
      if (!playing) return;
      if (idx >= messages.length - 1) {
        stop();
        return;
      }
      step(1);
      timer = setTimeout(tick, interval());
    };
    tick();
  }

  function stop() {
    playing = false;
    btnPlay.textContent = 'play';
    if (timer) clearTimeout(timer);
  }

  btnPlay.addEventListener('click', () => (playing ? stop() : play()));
  btnPrev.addEventListener('click', () => { stop(); step(-1); });
  btnNext.addEventListener('click', () => { stop(); step(1); });
  btnSkipTool.addEventListener('click', () => {
    skipTools = !skipTools;
    btnSkipTool.classList.toggle('active', skipTools);
  });
  btnFollow.addEventListener('click', () => {
    follow = !follow;
    btnFollow.classList.toggle('active', follow);
  });
  speed.addEventListener('input', () => { speedVal.textContent = `${parseFloat(speed.value).toFixed(2)}x`; });
  progress.addEventListener('click', (e) => {
    const rect = progress.getBoundingClientRect();
    const ratio = (e.clientX - rect.left) / rect.width;
    idx = Math.floor(ratio * (messages.length - 1));
    updateVisibility();
    updateProgress();
    scrollToCurrent(true);
  });

  updateVisibility();
  updateProgress();
})();
</script>
</body>
</html>
"""


def convert_to_player(model, input_path, theme="console", ansi_mode="strip", range_spec=None):
    message_blocks = []
    message_number = 0

    messages = filter_messages_by_range(model.get("messages", []), range_spec)
    for entry in messages:
        role = entry.get("role", "")
        if role == "user":
            message_number += 1

        text = _extract_text_from_model(entry)
        tool_uses = _extract_tool_uses_from_model(entry)
        tool_results = _extract_tool_results_from_model(entry)

        if not text.strip() and not tool_uses and not tool_results:
            continue

        if role == "user":
            parts = [f'<div class="role-label">User ({message_number})</div>']
            if text.strip():
                parts.append(f'<div class="message-body">{markdown_to_html_simple(text.strip(), ansi_mode=ansi_mode)}</div>')
            if tool_uses:
                for tool_use in tool_uses:
                    formatted = format_tool_use_html(tool_use)
                    parts.append(f'<div class="tool-section">{formatted}</div>')
            if tool_results:
                for result in tool_results:
                    result_content = result.get("content", "")
                    result_text = format_tool_result_content(result_content)
                    if ansi_mode == "strip":
                        result_text = strip_ansi(result_text)
                    elif ansi_mode == "color":
                        result_text = ansi_to_html(result_text)
                    if result_text.strip():
                        truncated = result_text[:500]
                        if ansi_mode == "strip":
                            truncated = escape(truncated)
                        elif ansi_mode == "color":
                            truncated = truncated
                        if len(result_text) > 500:
                            truncated += "\n... (truncated)"
                        parts.append(f"<details><summary>Tool Result</summary><pre>{truncated}</pre></details>")
            message_blocks.append(f'<div class="message user">\n' + "\n".join(parts) + "\n</div>")

        elif role == "assistant":
            if text.strip():
                text_parts = []
                text_parts.append('<div class="role-label">Assistant</div>')
                text_parts.append(f'<div class="message-body">{markdown_to_html_simple(text.strip(), ansi_mode=ansi_mode)}</div>')
                message_blocks.append(f'<div class="message assistant">\n' + "\n".join(text_parts) + "\n</div>")

            if tool_uses:
                for tool_use in tool_uses:
                    formatted = format_tool_use_html(tool_use)
                    tool_parts = []
                    tool_parts.append(f'<div class="tool-section">{formatted}</div>')
                    message_blocks.append(f'<div class="message assistant">\n' + "\n".join(tool_parts) + "\n</div>")

            if tool_results:
                for result in tool_results:
                    result_content = result.get("content", "")
                    result_text = format_tool_result_content(result_content)
                    if ansi_mode == "strip":
                        result_text = strip_ansi(result_text)
                    elif ansi_mode == "color":
                        result_text = ansi_to_html(result_text)
                    if result_text.strip():
                        truncated = result_text[:500]
                        if ansi_mode == "strip":
                            truncated = escape(truncated)
                        elif ansi_mode == "color":
                            truncated = truncated
                        if len(result_text) > 500:
                            truncated += "\n... (truncated)"
                        tool_parts = []
                        tool_parts.append(f"<details><summary>Tool Result</summary><pre>{truncated}</pre></details>")
                        message_blocks.append(f'<div class="message assistant">\n' + "\n".join(tool_parts) + "\n</div>")

    theme_css = THEME_LIGHT if theme == "light" else THEME_CONSOLE
    all_messages = "\n".join(message_blocks)
    return PLAYER_TEMPLATE.replace("{{THEME}}", theme_css).replace("{{MESSAGES}}", all_messages)


# ---------------------------------------------------------------------------
# Terminal output
# ---------------------------------------------------------------------------

TOOL_ICONS = {
    "Read": "\U0001F4C4",
    "Write": "\u270F\uFE0F",
    "Edit": "\U0001F527",
    "Bash": "$",
    "Grep": "\U0001F50D",
    "Glob": "\U0001F50D",
    "Task": "\U0001F916",
    "WebFetch": "\U0001F310",
}

TRIMMABLE_TOOLS = {"TaskCreate"}


def format_tool_use_terminal(tool_use):
    name = tool_use.get("name", "Unknown")
    tool_input = tool_use.get("input", {})
    icon = TOOL_ICONS.get(name, "\u2022")
    header = ""
    body = ""

    if name == "Read":
        file_path = escape(tool_input.get("file_path", ""))
        header = f'{icon} Read <span class="t-path">{file_path}</span>'
    elif name == "Write":
        file_path = escape(tool_input.get("file_path", ""))
        content = tool_input.get("content", "")
        line_count = content.count("\n") + 1 if content else 0
        header = f'{icon} Write <span class="t-path">{file_path}</span>'
        body = f'<div class="t-dim">{line_count} lines</div>'
    elif name == "Edit":
        file_path = escape(tool_input.get("file_path", ""))
        old_string = escape(tool_input.get("old_string", ""))
        new_string = escape(tool_input.get("new_string", ""))
        header = f'{icon} Edit <span class="t-path">{file_path}</span>'
        body = ("<div class=\"t-diff\">"
                f"<span class=\"t-diff-del\">- {old_string[:200]}</span>"
                f"<span class=\"t-diff-add\">+ {new_string[:200]}</span>"
                "</div>")
    elif name == "Bash":
        command = escape(tool_input.get("command", ""))
        header = f'{icon} Bash'
        body = f'<pre class="t-cmd">{command}</pre>'
    elif name == "Grep":
        pattern = escape(tool_input.get("pattern", ""))
        path = escape(tool_input.get("path", "."))
        header = f'{icon} Grep <span class="t-str">"{pattern}"</span> <span class="t-dim">in {path}</span>'
    elif name == "Glob":
        pattern = escape(tool_input.get("pattern", ""))
        header = f'{icon} Glob <span class="t-str">{pattern}</span>'
    elif name == "Task":
        description = escape(tool_input.get("description", ""))
        agent = escape(tool_input.get("subagent_type", ""))
        header = f'{icon} Task <span class="t-str">{description}</span>'
        if agent:
            header += f' <span class="t-dim">({agent})</span>'
    else:
        header = f'{icon} {escape(name)}'

    return header, body


TERMINAL_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Session Terminal</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #0d1117;
    --fg: #e6edf3;
    --dim: #6e7681;
    --border: #21262d;
    --prompt: #79c0ff;
    --user-text: #f0f6fc;
    --assistant-bar: #da7756;
    --tool-bg: #161b22;
    --tool-header-bg: #1c2129;
    --tool-border: #30363d;
    --diff-add-fg: #7ee787;
    --diff-add-bg: rgba(46,160,67,0.12);
    --diff-del-fg: #ffa198;
    --diff-del-bg: rgba(248,81,73,0.12);
    --cmd-bg: #0d1117;
    --path-color: #79c0ff;
    --str-color: #a5d6ff;
    --code-bg: #161b22;
    --code-fg: #e6edf3;
    --spinner-color: #da7756;
    --check-color: #3fb950;
    --ctrl-bg: #010409;
    --ctrl-border: #30363d;
    --progress-bg: #21262d;
    --progress-fg: #da7756;
    --mono: "SFMono-Regular", Consolas, "Liberation Mono", "Courier New", monospace;
  }
  body {
    font-family: var(--mono);
    font-size: 14px;
    background: var(--bg);
    color: var(--fg);
    line-height: 1.5;
    padding: 0;
    padding-bottom: 100px;
  }

  .t-topbar {
    background: var(--ctrl-bg);
    border-bottom: 1px solid var(--ctrl-border);
    padding: 8px 16px;
    display: flex;
    align-items: center;
    gap: 12px;
    position: sticky;
    top: 0;
    z-index: 100;
  }
  .t-topbar-title {
    color: var(--assistant-bar);
    font-weight: bold;
    font-size: 13px;
  }
  .t-topbar-model {
    color: var(--dim);
    font-size: 12px;
  }
  .t-topbar-cwd {
    color: var(--dim);
    font-size: 12px;
    margin-left: auto;
  }

  .t-container { max-width: 960px; margin: 0 auto; padding: 8px 16px; }

  .t-msg {
    padding: 6px 0;
    opacity: 0;
    transform: translateY(8px);
    transition: opacity 0.3s ease, transform 0.3s ease;
  }
  .t-msg.visible {
    opacity: 1;
    transform: translateY(0);
  }

  .t-user {
    display: flex;
    gap: 10px;
    padding: 12px 16px;
    background: linear-gradient(135deg, rgba(56,139,253,0.08), rgba(56,139,253,0.03));
    border: 1px solid rgba(56,139,253,0.2);
    border-radius: 8px;
    margin: 12px 0 6px;
  }
  .t-prompt {
    color: var(--prompt);
    font-weight: bold;
    font-size: 18px;
    line-height: 1.3;
    flex-shrink: 0;
    user-select: none;
    text-shadow: 0 0 8px rgba(121,192,255,0.4);
  }
  .t-user-text {
    color: var(--user-text);
    font-size: 15px;
    white-space: pre-wrap;
    word-wrap: break-word;
  }

  .t-assistant {
    border-left: 3px solid var(--assistant-bar);
    padding-left: 14px;
    margin: 6px 0;
  }
  .t-response { white-space: pre-wrap; word-wrap: break-word; }
  .t-response p { margin: 0.3em 0; }
  .t-response h2, .t-response h3, .t-response h4 {
    color: var(--user-text);
    margin: 0.6em 0 0.2em;
  }
  .t-response strong { color: var(--user-text); }

  .t-tool {
    background: var(--tool-bg);
    border: 1px solid var(--tool-border);
    border-radius: 6px;
    margin: 6px 0;
    overflow: hidden;
  }
  .t-tool-header {
    background: var(--tool-header-bg);
    padding: 6px 12px;
    font-size: 13px;
    border-bottom: 1px solid var(--tool-border);
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .t-tool-body { padding: 8px 12px; font-size: 13px; overflow: hidden; }

  body.trim-empty .t-tool-empty { display: none !important; }
  body.hide-details .t-tool-body { display: none !important; }
  body.hide-details .t-tool .t-tool-header { border-bottom: none; }
  .t-path { color: var(--path-color); }
  .t-str { color: var(--str-color); }
  .t-dim { color: var(--dim); }
  .t-bash-dollar { color: var(--check-color); font-weight: bold; }

  table {
    border-collapse: collapse;
    margin: 8px 0;
    width: 100%;
    font-size: 13px;
  }
  th, td {
    border: 1px solid var(--tool-border);
    padding: 6px 12px;
    text-align: left;
  }
  th {
    background: var(--tool-header-bg);
    color: var(--user-text);
    font-weight: bold;
  }
  td { background: var(--tool-bg); }
  tr:hover td { background: var(--tool-header-bg); }

  .t-spinner {
    display: inline-block;
    width: 14px;
    height: 14px;
    flex-shrink: 0;
  }
  .t-spinner::after {
    content: "\\25CF";
    color: var(--spinner-color);
    animation: t-spin-pulse 1.2s ease-in-out infinite;
  }
  .t-msg.done .t-spinner::after {
    content: "\\2713";
    color: var(--check-color);
    animation: none;
  }
  @keyframes t-spin-pulse {
    0%, 100% { opacity: 0.3; }
    50% { opacity: 1; }
  }

  .t-diff {
    background: var(--cmd-bg);
    padding: 8px 12px;
    margin: 4px 0;
    border-radius: 4px;
    font-size: 12px;
    overflow-x: auto;
  }
  .t-diff-add {
    color: var(--diff-add-fg);
    background: var(--diff-add-bg);
    display: block;
  }
  .t-diff-del {
    color: var(--diff-del-fg);
    background: var(--diff-del-bg);
    display: block;
  }

  .t-cmd {
    background: var(--cmd-bg);
    padding: 8px 12px;
    margin: 4px 0;
    border-radius: 4px;
    font-size: 12px;
    overflow-x: auto;
    color: var(--fg);
  }

  pre {
    background: var(--code-bg);
    color: var(--code-fg);
    padding: 10px 14px;
    border-radius: 6px;
    border: 1px solid var(--tool-border);
    overflow-x: auto;
    font-family: var(--mono);
    font-size: 13px;
    margin: 6px 0;
    white-space: pre-wrap;
    word-wrap: break-word;
  }
  code {
    font-family: var(--mono);
    background: rgba(110,118,129,0.15);
    padding: 1px 5px;
    border-radius: 3px;
    font-size: 0.92em;
  }
  pre code { background: none; padding: 0; }

  .controls {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    background: var(--ctrl-bg);
    border-top: 1px solid var(--ctrl-border);
    padding: 10px 16px;
    z-index: 1000;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .progress-bar-container {
    width: 100%;
    height: 4px;
    background: var(--progress-bg);
    border-radius: 2px;
    cursor: pointer;
  }
  .progress-bar {
    height: 100%;
    background: var(--progress-fg);
    border-radius: 2px;
    transition: width 0.15s linear;
  }
  .controls-row {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
  }
  .controls button {
    background: var(--tool-bg);
    border: 1px solid var(--tool-border);
    color: var(--fg);
    padding: 4px 12px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 13px;
    font-family: var(--mono);
    transition: background 0.15s;
  }
  .controls button:hover { background: var(--tool-header-bg); }
  .controls button.active { background: var(--assistant-bar); color: #fff; border-color: var(--assistant-bar); }
  .controls .info {
    color: var(--dim);
    font-size: 12px;
  }
</style>
</head>
<body>
<div class="t-topbar">
  <div class="t-topbar-title">Session</div>
  <div class="t-topbar-model">log-model-renderer</div>
  <div class="t-topbar-cwd">{cwd}</div>
</div>
<div class="t-container">
{{MESSAGES}}
</div>
<div class="controls">
  <div class="progress-bar-container" id="t-progress-container"><div class="progress-bar" id="t-progress"></div></div>
  <div class="controls-row">
    <button id="t-play">play</button>
    <button id="t-prev">prev</button>
    <button id="t-next">next</button>
    <button id="t-first">first</button>
    <button id="t-last">last</button>
    <button id="t-skip">skip tools</button>
    <button id="t-trim">trim empty</button>
    <button id="t-details">details</button>
    <button id="t-follow" class="active">follow</button>
  </div>
  <div class="controls-row">
    <span class="info">speed</span>
    <input id="t-speed" type="range" min="0.25" max="16" step="0.25" value="1" />
    <span class="info" id="t-speed-val">1.0x</span>
    <span class="info" id="t-count"></span>
  </div>
</div>
<script>
(() => {
  const msgs = Array.from(document.querySelectorAll('.t-msg'));
  let idx = -1;
  let playing = false;
  let skipTools = false;
  let trimEmpty = true;
  let hideDetails = false;
  let follow = true;
  let timer = null;

  const btnPlay = document.getElementById('t-play');
  const btnPrev = document.getElementById('t-prev');
  const btnNext = document.getElementById('t-next');
  const btnFirst = document.getElementById('t-first');
  const btnLast = document.getElementById('t-last');
  const btnSkip = document.getElementById('t-skip');
  const btnTrim = document.getElementById('t-trim');
  const btnDetails = document.getElementById('t-details');
  const btnFollow = document.getElementById('t-follow');
  const speed = document.getElementById('t-speed');
  const speedVal = document.getElementById('t-speed-val');
  const count = document.getElementById('t-count');
  const progress = document.getElementById('t-progress');
  const progressContainer = document.getElementById('t-progress-container');

  function isTool(msg) {
    return msg.classList.contains('t-tool');
  }

  function scrollToCurrent(instant = false) {
    const target = msgs[idx];
    if (!target) return;
    const behavior = instant ? 'auto' : 'smooth';
    target.scrollIntoView({ behavior, block: 'nearest' });
  }

  function scheduleDone(msg) {
    if (!msg || msg.dataset.doneScheduled === '1' || msg.classList.contains('done')) return;
    const tool = (msg.dataset.tool || '').toLowerCase();
    const delays = {
      bash: 1200,
      read: 500,
      write: 700,
      edit: 900,
      grep: 700,
      glob: 500,
      task: 800,
      result: 600,
      default: 600
    };
    const delay = delays[tool] ?? delays.default;
    msg.dataset.doneScheduled = '1';
    setTimeout(() => { msg.classList.add('done'); }, delay);
  }

  function update() {
    msgs.forEach((m, i) => {
      if (i <= idx) {
        m.classList.add('visible');
      } else {
        m.classList.remove('visible');
      }
      if (skipTools && isTool(m)) {
        m.style.display = 'none';
      } else {
        m.style.display = '';
      }
      if (m.classList.contains('visible') && isTool(m)) {
        scheduleDone(m);
      }
    });
    const total = msgs.length;
    const val = total ? ((idx + 1) / total) * 100 : 0;
    progress.style.width = `${val}%`;
    count.textContent = `${Math.max(0, idx + 1)} / ${total}`;
    if (!playing || follow) scrollToCurrent(!playing);
  }

  function step(dir) {
    let next = idx + dir;
    while (next >= 0 && next < msgs.length) {
      if (skipTools && isTool(msgs[next])) { next += dir; continue; }
      idx = next;
      update();
      return;
    }
    idx = Math.max(-1, Math.min(msgs.length - 1, next));
    update();
  }

  function play() {
    if (playing) return;
    playing = true;
    btnPlay.textContent = 'pause';
    const interval = () => Math.max(50, 800 / parseFloat(speed.value));
    const tick = () => {
      if (!playing) return;
      if (idx >= msgs.length - 1) { stop(); return; }
      step(1);
      timer = setTimeout(tick, interval());
    };
    tick();
  }

  function stop() {
    playing = false;
    btnPlay.textContent = 'play';
    if (timer) clearTimeout(timer);
  }

  btnPlay.addEventListener('click', () => (playing ? stop() : play()));
  btnPrev.addEventListener('click', () => { stop(); step(-1); });
  btnNext.addEventListener('click', () => { stop(); step(1); });
  btnFirst.addEventListener('click', () => { stop(); idx = -1; update(); });
  btnLast.addEventListener('click', () => { stop(); idx = msgs.length - 1; update(); });
  btnSkip.addEventListener('click', () => { skipTools = !skipTools; btnSkip.classList.toggle('active', skipTools); update(); });
  btnTrim.addEventListener('click', () => { trimEmpty = !trimEmpty; btnTrim.classList.toggle('active', trimEmpty); document.body.classList.toggle('trim-empty', trimEmpty); });
  btnDetails.addEventListener('click', () => { hideDetails = !hideDetails; btnDetails.classList.toggle('active', hideDetails); btnDetails.textContent = hideDetails ? 'details off' : 'details'; document.body.classList.toggle('hide-details', hideDetails); });
  btnFollow.addEventListener('click', () => { follow = !follow; btnFollow.classList.toggle('active', follow); });
  speed.addEventListener('input', () => { speedVal.textContent = `${parseFloat(speed.value).toFixed(2)}x`; });
  progressContainer.addEventListener('click', (e) => {
    const rect = progressContainer.getBoundingClientRect();
    const ratio = (e.clientX - rect.left) / rect.width;
    idx = Math.floor(ratio * (msgs.length - 1));
    update();
  });

  btnTrim.classList.toggle('active', trimEmpty);
  document.body.classList.toggle('trim-empty', trimEmpty);
  update();
})();
</script>
</body>
</html>
"""


def convert_to_terminal(model, input_path, ansi_mode="strip", range_spec=None):
    message_blocks = []
    message_number = 0

    messages = filter_messages_by_range(model.get("messages", []), range_spec)
    for entry in messages:
        role = entry.get("role", "")
        if role == "user":
            message_number += 1

        text = _extract_text_from_model(entry)
        tool_uses = _extract_tool_uses_from_model(entry)
        tool_results = _extract_tool_results_from_model(entry)

        if not text.strip() and not tool_uses and not tool_results:
            continue

        if role == "user":
            if text.strip():
                user_html = f'<div class="t-prompt">\u276F</div>'
                if ansi_mode == "strip":
                    safe_text = escape(strip_ansi(text.strip()))
                elif ansi_mode == "color":
                    safe_text = ansi_to_html(text.strip())
                else:
                    safe_text = escape(text.strip())
                user_html += f'<div class="t-user-text">{safe_text}</div>'
                message_blocks.append(f'<div class="t-msg t-user">{user_html}</div>')
        elif role == "assistant":
            if text.strip():
                body_html = markdown_to_html_simple(text.strip(), ansi_mode=ansi_mode)
                message_blocks.append(
                    f'<div class="t-msg t-assistant">'
                    f'<div class="t-response">{body_html}</div>'
                    f'</div>')
        else:
            continue

        for tool_use in tool_uses:
            header, body = format_tool_use_terminal(tool_use)
            tool_name = tool_use.get("name", "")
            extra_class = " t-tool-empty" if tool_name in TRIMMABLE_TOOLS else ""
            tool_key = tool_name.lower()
            tool_html = (
                f'<div class="t-msg t-tool{extra_class}" data-tool="{escape(tool_key)}">'
                f'<div class="t-tool-header">'
                f'<span class="t-spinner"></span>{header}'
                f'</div>')
            if body:
                tool_html += f'<div class="t-tool-body">{body}</div>'
            tool_html += '</div>'
            message_blocks.append(tool_html)

        for result in tool_results:
            result_content = result.get("content", "")
            result_text = format_tool_result_content(result_content)
            if ansi_mode == "strip":
                result_text = strip_ansi(result_text)
            elif ansi_mode == "color":
                result_text = ansi_to_html(result_text)
            if not str(result_text).strip():
                continue
            header = '\U0001F4DD Result'
            if ansi_mode == "color":
                body = f'<pre class="t-cmd">{result_text}</pre>'
            else:
                body = f'<pre class="t-cmd">{escape(str(result_text))}</pre>'
            tool_html = (
                f'<div class="t-msg t-tool" data-tool="result">'
                f'<div class="t-tool-header">'
                f'<span class="t-spinner"></span>{header}'
                f'</div>'
                f'<div class="t-tool-body">{body}</div>'
                f'</div>')
            message_blocks.append(tool_html)

    all_messages = "\n".join(message_blocks)
    cwd = os.getcwd()
    return TERMINAL_TEMPLATE.replace("{{MESSAGES}}", all_messages).replace("{cwd}", escape(cwd))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Render common log model")
    parser.add_argument("input", help="input model JSON file")
    parser.add_argument("-o", "--output", help="output file path")
    parser.add_argument("-f", "--format", choices=["md", "html", "player", "terminal"], default="md",
                        help="output format: md, html, player, or terminal")
    parser.add_argument("-t", "--theme", choices=["light", "console"], default="light",
                        help="HTML theme: light (default) or console (dark)")
    parser.add_argument("--ansi-mode", choices=["strip", "color"], default="strip",
                        help="ANSI handling: strip or color (HTML)")
    parser.add_argument("-r", "--range", dest="range_spec",
                        help="message range like '1-50,53-' (1-based, comma-separated)")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        model = json.load(f)

    if args.output:
        output_path = args.output
    else:
        extension = ".html" if args.format in ("html", "player", "terminal") else ".md"
        output_path = os.path.splitext(args.input)[0] + extension

    if args.format == "terminal":
        result = convert_to_terminal(model, args.input, ansi_mode=args.ansi_mode, range_spec=args.range_spec)
    elif args.format == "player":
        result = convert_to_player(model, args.input, theme=args.theme, ansi_mode=args.ansi_mode, range_spec=args.range_spec)
    elif args.format == "html":
        result = convert_to_html(model, args.input, theme=args.theme, ansi_mode=args.ansi_mode, range_spec=args.range_spec)
    else:
        result = convert_to_markdown(model, args.input, ansi_mode=args.ansi_mode, range_spec=args.range_spec)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(result)

    print(f"Rendered {len(model.get('messages', []))} messages ({args.format}) -> {output_path}")


if __name__ == "__main__":
    main()
