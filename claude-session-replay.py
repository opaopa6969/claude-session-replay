#!/usr/bin/env python3
"""Claude Code JSONL session transcript to Markdown/HTML converter."""

import argparse
import html
import json
import os
import re
import sys


def extract_text_from_content(content):
    """メッセージのcontentからテキスト部分を抽出する。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    texts.append(block.get("text", ""))
        return "\n".join(texts)
    return ""


def extract_tool_uses(content):
    """メッセージのcontentからツール使用情報を抽出する。"""
    if not isinstance(content, list):
        return []
    tool_uses = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            tool_uses.append(block)
    return tool_uses


def extract_tool_results(content):
    """メッセージのcontentからツール実行結果を抽出する。"""
    if not isinstance(content, list):
        return []
    results = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            results.append(block)
    return results


def format_tool_use(tool_use):
    """ツール使用をMarkdown形式でフォーマットする。"""
    name = tool_use.get("name", "Unknown")
    tool_input = tool_use.get("input", {})

    if name == "Read":
        file_path = tool_input.get("file_path", "")
        return f"**Read**: `{file_path}`"
    elif name == "Write":
        file_path = tool_input.get("file_path", "")
        content = tool_input.get("content", "")
        line_count = content.count("\n") + 1 if content else 0
        return f"**Write**: `{file_path}` ({line_count} lines)"
    elif name == "Edit":
        file_path = tool_input.get("file_path", "")
        old_string = tool_input.get("old_string", "")
        new_string = tool_input.get("new_string", "")
        return f"**Edit**: `{file_path}`\n```diff\n- {old_string[:200]}\n+ {new_string[:200]}\n```"
    elif name == "Bash":
        command = tool_input.get("command", "")
        return f"**Bash**:\n```bash\n{command}\n```"
    elif name == "Grep":
        pattern = tool_input.get("pattern", "")
        path = tool_input.get("path", "")
        return f"**Grep**: `{pattern}` in `{path}`"
    elif name == "Glob":
        pattern = tool_input.get("pattern", "")
        return f"**Glob**: `{pattern}`"
    elif name == "Task":
        description = tool_input.get("description", "")
        return f"**Task**: {description}"
    else:
        return f"**{name}**"


def format_tool_result_content(content):
    """ツール結果のcontentをテキストに変換する。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        return "\n".join(texts)
    return str(content)


def parse_messages(input_path):
    """JSONLファイルからuser/assistantメッセージを読み込む。"""
    log_format = detect_log_format(input_path)
    if log_format == "codex":
        return parse_codex_messages(input_path)

    messages = []
    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            data = json.loads(line)
            message_type = data.get("type", "")
            if message_type in ("user", "assistant"):
                messages.append(data)
    return messages


def detect_log_format(input_path):
    """ログ形式を推定する。"""
    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            record_type = data.get("type", "")
            if record_type in ("session_meta", "event_msg", "response_item", "turn_context"):
                return "codex"
            if record_type in ("user", "assistant"):
                return "claude"
    return "claude"


def _codex_has_event_messages(input_path):
    """Codexログにevent_msg由来のメッセージがあるか確認する。"""
    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if data.get("type") != "event_msg":
                continue
            payload = data.get("payload", {})
            if payload.get("type") in ("user_message", "agent_message"):
                return True
    return False


def _extract_text_from_codex_content(content):
    """Codexのmessage.contentからテキストを抽出する。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type")
                if block_type in ("input_text", "output_text", "text"):
                    texts.append(block.get("text", ""))
        return "\n".join(texts)
    return ""


def _safe_json_loads(value):
    """JSON文字列を安全に解析する。"""
    if not isinstance(value, str):
        return value if isinstance(value, dict) else {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _codex_tool_use_from_function_call(payload):
    """Codexのfunction_call/custom_tool_callをClaude互換のtool_useに変換する。"""
    name = payload.get("name", "Unknown")
    if payload.get("type") == "function_call":
        args = _safe_json_loads(payload.get("arguments", ""))
        if name == "shell_command":
            command = args.get("command", "")
            workdir = args.get("workdir")
            if workdir:
                command = f"cd {workdir}\n{command}"
            return {"type": "tool_use", "name": "Bash", "input": {"command": command}}
        if name == "update_plan":
            description = args.get("explanation", "update_plan")
            return {"type": "tool_use", "name": "Task", "input": {"description": description}}
        return {"type": "tool_use", "name": name, "input": args}

    # custom_tool_call
    tool_input = payload.get("input", "")
    return {"type": "tool_use", "name": name, "input": {"input": tool_input}}


def parse_codex_messages(input_path):
    """Codex JSONLからuser/assistantメッセージを抽出する。"""
    messages = []
    use_event_msgs = _codex_has_event_messages(input_path)

    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            record_type = data.get("type", "")
            payload = data.get("payload", {})
            payload_type = payload.get("type")

            if record_type == "event_msg":
                if payload_type == "user_message":
                    text = payload.get("message", "")
                    if text.strip():
                        messages.append({
                            "type": "user",
                            "message": {"role": "user", "content": [{"type": "text", "text": text}]}
                        })
                elif payload_type == "agent_message":
                    text = payload.get("message", "")
                    if text.strip():
                        messages.append({
                            "type": "assistant",
                            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}
                        })
                continue

            if record_type != "response_item":
                continue

            if payload_type == "message":
                if use_event_msgs:
                    continue
                role = payload.get("role", "")
                if role not in ("user", "assistant"):
                    continue
                text = _extract_text_from_codex_content(payload.get("content", []))
                if text.strip():
                    messages.append({
                        "type": role,
                        "message": {"role": role, "content": [{"type": "text", "text": text}]}
                    })
                continue

            if payload_type in ("function_call", "custom_tool_call"):
                tool_use = _codex_tool_use_from_function_call(payload)
                if tool_use:
                    messages.append({
                        "type": "assistant",
                        "message": {"role": "assistant", "content": [tool_use]}
                    })
                continue

            if payload_type in ("function_call_output", "custom_tool_call_output"):
                output = payload.get("output", "")
                if output:
                    messages.append({
                        "type": "assistant",
                        "message": {"role": "assistant", "content": [{"type": "tool_result", "content": output}]}
                    })
                continue

    return messages


# ---------------------------------------------------------------------------
# Markdown output
# ---------------------------------------------------------------------------

def convert_to_markdown(messages, input_path):
    """メッセージリストをMarkdown文字列に変換する。"""
    lines = []
    lines.append("# Claude Code Session Transcript\n")
    lines.append(f"Source: `{os.path.basename(input_path)}`\n")
    lines.append("---\n")

    message_number = 0

    for data in messages:
        message = data.get("message", {})
        role = message.get("role", "")
        content = message.get("content", "")

        if role == "user":
            message_number += 1

        text = extract_text_from_content(content)
        tool_uses = extract_tool_uses(content)
        tool_results = extract_tool_results(content)

        if not text.strip() and not tool_uses and not tool_results:
            continue

        if role == "user":
            lines.append(f"## User ({message_number})\n")
        elif role == "assistant":
            lines.append("## Assistant\n")
        else:
            continue

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
                if result_text.strip():
                    truncated = result_text[:500]
                    if len(result_text) > 500:
                        truncated += "\n... (truncated)"
                    lines.append(f"\n<details><summary>Tool Result</summary>\n")
                    lines.append(f"```\n{truncated}\n```\n")
                    lines.append(f"</details>\n")

        lines.append("")

    lines.append("\n---\n*Converted from Claude Code JSONL session transcript.*\n")
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
<title>Claude Code Session</title>
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
</style>
</head>
<body>
<h1>Claude Code Session</h1>
<div class="chat-container">
{{MESSAGES}}
</div>
<div class="footer">Converted from Claude Code JSONL session transcript.</div>
</body>
</html>
"""


def escape(text):
    """HTMLエスケープする。"""
    return html.escape(text)


def _inline_format(escaped_line):
    """インライン書式（コード、太字）を変換する。"""
    escaped_line = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped_line)
    escaped_line = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped_line)
    return escaped_line


def _is_table_row(line):
    """テーブル行かどうかを判定する。"""
    return line.strip().startswith("|") and line.strip().endswith("|")


def _is_separator_row(line):
    """テーブルのセパレータ行 (|---|---| 形式) かどうかを判定する。"""
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")):
        return False
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    return all(re.match(r"^:?-{1,}:?$", cell) for cell in cells)


def _parse_table_cells(line):
    """テーブル行からセルを抽出する。"""
    stripped = line.strip().strip("|")
    return [cell.strip() for cell in stripped.split("|")]


def _render_table(table_lines):
    """テーブル行のリストをHTML tableに変換する。"""
    if len(table_lines) < 2:
        return ""

    header_line = table_lines[0]
    header_cells = _parse_table_cells(header_line)

    # セパレータ行を探す
    separator_index = -1
    for i in range(1, min(3, len(table_lines))):
        if _is_separator_row(table_lines[i]):
            separator_index = i
            break

    rows_html = []
    # ヘッダー
    header_html = "".join(
        f"<th>{_inline_format(escape(cell))}</th>" for cell in header_cells
    )
    rows_html.append(f"<thead><tr>{header_html}</tr></thead>")

    # ボディ
    body_start = separator_index + 1 if separator_index >= 0 else 1
    body_rows = []
    for row_line in table_lines[body_start:]:
        if _is_separator_row(row_line):
            continue
        cells = _parse_table_cells(row_line)
        cells_html = "".join(
            f"<td>{_inline_format(escape(cell))}</td>" for cell in cells
        )
        body_rows.append(f"<tr>{cells_html}</tr>")

    if body_rows:
        rows_html.append("<tbody>" + "".join(body_rows) + "</tbody>")

    return "<table>" + "".join(rows_html) + "</table>"


def markdown_to_html_simple(text):
    """簡易Markdown→HTML変換（コードブロック、テーブル、インラインコード、太字、見出し）。"""
    result_parts = []
    in_code_block = False
    code_block_lines = []
    code_lang = ""
    table_lines = []

    def flush_table():
        nonlocal table_lines
        if table_lines:
            result_parts.append(_render_table(table_lines))
            table_lines = []

    for line in text.split("\n"):
        # コードブロック開始
        if not in_code_block and re.match(r"^```(\w*)$", line):
            flush_table()
            in_code_block = True
            code_lang = re.match(r"^```(\w*)$", line).group(1)
            code_block_lines = []
            continue
        # コードブロック終了
        if in_code_block and line.strip() == "```":
            in_code_block = False
            code_content = escape("\n".join(code_block_lines))
            result_parts.append(f"<pre><code>{code_content}</code></pre>")
            continue
        if in_code_block:
            code_block_lines.append(line)
            continue

        # テーブル行の蓄積
        if _is_table_row(line):
            table_lines.append(line)
            continue
        else:
            flush_table()

        escaped_line = escape(line)

        # 見出し
        heading_match = re.match(r"^(#{1,4})\s+(.+)$", escaped_line)
        if heading_match:
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2)
            result_parts.append(f"<h{level + 1}>{heading_text}</h{level + 1}>")
            continue

        escaped_line = _inline_format(escaped_line)

        if escaped_line.strip() == "":
            result_parts.append("<br>")
        else:
            result_parts.append(f"<p>{escaped_line}</p>")

    flush_table()

    if in_code_block:
        code_content = escape("\n".join(code_block_lines))
        result_parts.append(f"<pre><code>{code_content}</code></pre>")

    return "\n".join(result_parts)


def format_tool_use_html(tool_use):
    """ツール使用をHTML形式でフォーマットする。"""
    name = tool_use.get("name", "Unknown")
    tool_input = tool_use.get("input", {})

    if name == "Read":
        file_path = escape(tool_input.get("file_path", ""))
        return f'<span class="tool-name">Read</span>: <code>{file_path}</code>'
    elif name == "Write":
        file_path = escape(tool_input.get("file_path", ""))
        content = tool_input.get("content", "")
        line_count = content.count("\n") + 1 if content else 0
        return f'<span class="tool-name">Write</span>: <code>{file_path}</code> ({line_count} lines)'
    elif name == "Edit":
        file_path = escape(tool_input.get("file_path", ""))
        old_str = escape(tool_input.get("old_string", "")[:200])
        new_str = escape(tool_input.get("new_string", "")[:200])
        return (f'<span class="tool-name">Edit</span>: <code>{file_path}</code>'
                f"<pre>- {old_str}\n+ {new_str}</pre>")
    elif name == "Bash":
        command = escape(tool_input.get("command", ""))
        return f'<span class="tool-name">Bash</span>:<pre>{command}</pre>'
    elif name == "Grep":
        pattern = escape(tool_input.get("pattern", ""))
        path = escape(tool_input.get("path", ""))
        return f'<span class="tool-name">Grep</span>: <code>{pattern}</code> in <code>{path}</code>'
    elif name == "Glob":
        pattern = escape(tool_input.get("pattern", ""))
        return f'<span class="tool-name">Glob</span>: <code>{pattern}</code>'
    elif name == "Task":
        description = escape(tool_input.get("description", ""))
        return f'<span class="tool-name">Task</span>: {description}'
    else:
        return f'<span class="tool-name">{escape(name)}</span>'


def convert_to_html(messages, input_path, theme="light"):
    """メッセージリストをHTML文字列に変換する。"""
    message_blocks = []
    message_number = 0

    for data in messages:
        message = data.get("message", {})
        role = message.get("role", "")
        content = message.get("content", "")

        if role == "user":
            message_number += 1

        text = extract_text_from_content(content)
        tool_uses = extract_tool_uses(content)
        tool_results = extract_tool_results(content)

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
            parts.append(f'<div class="message-body">{markdown_to_html_simple(text.strip())}</div>')

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
<title>Claude Code Session Player</title>
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
    padding-bottom: 120px;
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
    opacity: 0;
    transform: translateY(20px);
    transition: opacity 0.4s ease, transform 0.4s ease;
  }
  .message.visible {
    opacity: 1;
    transform: translateY(0);
  }
  .message.typing .message-body {
    border-right: 2px solid var(--body-color);
    animation: blink-cursor 0.7s step-end infinite;
  }
  @keyframes blink-cursor {
    50% { border-right-color: transparent; }
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
  details { margin: 6px 0; font-size: 0.85em; }
  details summary { cursor: pointer; color: var(--details-summary); font-style: italic; }
  details pre { background: var(--result-bg); color: var(--result-color); max-height: 300px; overflow-y: auto; }

  /* Controls */
  .controls {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    background: var(--code-bg);
    border-top: 2px solid var(--assistant-border);
    padding: 12px 20px;
    z-index: 1000;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .progress-bar-container {
    width: 100%;
    height: 6px;
    background: rgba(255,255,255,0.1);
    border-radius: 3px;
    cursor: pointer;
    position: relative;
  }
  .progress-bar {
    height: 100%;
    background: var(--assistant-border);
    border-radius: 3px;
    transition: width 0.15s linear;
  }
  .controls-row {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 16px;
  }
  .controls button {
    background: none;
    border: 1px solid rgba(255,255,255,0.2);
    color: var(--code-color);
    padding: 6px 14px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 1em;
    font-family: var(--font);
    transition: background 0.15s;
  }
  .controls button:hover { background: rgba(255,255,255,0.1); }
  .controls button.active { background: var(--assistant-border); color: #fff; }
  .controls .info {
    color: var(--details-summary);
    font-size: 0.85em;
    min-width: 160px;
    text-align: center;
  }
  .speed-control {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--details-summary);
    font-size: 0.85em;
  }
  .speed-control input[type=range] {
    width: 80px;
    accent-color: var(--assistant-border);
  }
  .kbd {
    display: inline-block;
    background: rgba(255,255,255,0.08);
    border: 1px solid rgba(255,255,255,0.15);
    border-radius: 3px;
    padding: 0 5px;
    font-family: var(--mono);
    font-size: 0.75em;
    color: var(--details-summary);
    margin-left: 2px;
  }
</style>
</head>
<body>
<h1>Claude Code Session Player</h1>
<div class="chat-container" id="chat">
{{MESSAGES}}
</div>

<div class="controls">
  <div class="progress-bar-container" id="progressContainer">
    <div class="progress-bar" id="progressBar"></div>
  </div>
  <div class="controls-row">
    <button id="btnFirst" title="First (Home)">&#x23EE;</button>
    <button id="btnPrev" title="Previous (Left)">&#x23EA;</button>
    <button id="btnPlay" title="Play/Pause (Space)">&#x25B6;</button>
    <button id="btnNext" title="Next (Right)">&#x23E9;</button>
    <button id="btnLast" title="Last (End)">&#x23ED;</button>
    <button id="btnSkipTool" title="Skip tool messages (T)">Skip Tools</button>
    <div class="speed-control">
      <span>Speed</span>
      <input type="range" id="speedSlider" min="1" max="10" value="5">
      <span id="speedLabel">1.0x</span>
    </div>
    <div class="info" id="info">0 / 0</div>
    <div>
      <span class="kbd">Space</span>
      <span class="kbd">&larr;</span>
      <span class="kbd">&rarr;</span>
      <span class="kbd">Home</span>
      <span class="kbd">End</span>
      <span class="kbd">T</span>
    </div>
  </div>
</div>

<script>
(function() {
  const messages = document.querySelectorAll('.message');
  const total = messages.length;
  let current = -1;
  let playing = false;
  let timer = null;
  let skipTools = false;
  let speed = 1.0;

  const btnPlay = document.getElementById('btnPlay');
  const btnPrev = document.getElementById('btnPrev');
  const btnNext = document.getElementById('btnNext');
  const btnFirst = document.getElementById('btnFirst');
  const btnLast = document.getElementById('btnLast');
  const btnSkipTool = document.getElementById('btnSkipTool');
  const speedSlider = document.getElementById('speedSlider');
  const speedLabel = document.getElementById('speedLabel');
  const info = document.getElementById('info');
  const progressBar = document.getElementById('progressBar');
  const progressContainer = document.getElementById('progressContainer');

  function updateInfo() {
    info.textContent = (current + 1) + ' / ' + total;
    const pct = total > 0 ? ((current + 1) / total * 100) : 0;
    progressBar.style.width = pct + '%';
  }

  function showMessage(index) {
    if (index < 0 || index >= total) return;
    const msg = messages[index];
    msg.classList.add('visible');
    msg.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }

  function hideMessage(index) {
    if (index < 0 || index >= total) return;
    messages[index].classList.remove('visible');
    messages[index].classList.remove('typing');
  }

  function isToolMessage(index) {
    if (index < 0 || index >= total) return false;
    const msg = messages[index];
    return msg.querySelector('.tool-section') !== null
        && msg.querySelector('.message-body') === null;
  }

  function goTo(index) {
    if (index < -1) index = -1;
    if (index >= total) index = total - 1;

    if (index > current) {
      for (let i = current + 1; i <= index; i++) showMessage(i);
    } else if (index < current) {
      for (let i = current; i > index; i--) hideMessage(i);
    }
    current = index;
    updateInfo();
  }

  function stepForward() {
    let next = current + 1;
    if (skipTools) {
      while (next < total && isToolMessage(next)) next++;
    }
    if (next >= total) {
      stopPlaying();
      return;
    }
    goTo(next);
  }

  function stepBackward() {
    let prev = current - 1;
    if (skipTools) {
      while (prev >= 0 && isToolMessage(prev)) prev--;
    }
    if (prev < -1) prev = -1;
    goTo(prev);
  }

  function getInterval() {
    return Math.max(100, 1200 / speed);
  }

  function startPlaying() {
    if (current >= total - 1) goTo(-1);
    playing = true;
    btnPlay.textContent = '\\u23F8';
    btnPlay.classList.add('active');
    tick();
  }

  function stopPlaying() {
    playing = false;
    clearTimeout(timer);
    timer = null;
    btnPlay.textContent = '\\u25B6';
    btnPlay.classList.remove('active');
  }

  function tick() {
    if (!playing) return;
    stepForward();
    if (current >= total - 1) {
      stopPlaying();
      return;
    }
    timer = setTimeout(tick, getInterval());
  }

  function togglePlay() {
    if (playing) stopPlaying();
    else startPlaying();
  }

  // Button events
  btnPlay.addEventListener('click', togglePlay);
  btnNext.addEventListener('click', function() { stopPlaying(); stepForward(); });
  btnPrev.addEventListener('click', function() { stopPlaying(); stepBackward(); });
  btnFirst.addEventListener('click', function() { stopPlaying(); goTo(-1); });
  btnLast.addEventListener('click', function() { stopPlaying(); goTo(total - 1); });
  btnSkipTool.addEventListener('click', function() {
    skipTools = !skipTools;
    btnSkipTool.classList.toggle('active', skipTools);
  });

  speedSlider.addEventListener('input', function() {
    const val = parseInt(this.value);
    const speeds = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 16.0];
    speed = speeds[val - 1] || 1.0;
    speedLabel.textContent = speed + 'x';
  });

  // Progress bar click
  progressContainer.addEventListener('click', function(e) {
    stopPlaying();
    const rect = this.getBoundingClientRect();
    const pct = (e.clientX - rect.left) / rect.width;
    const target = Math.round(pct * total) - 1;
    goTo(target);
  });

  // Keyboard shortcuts
  document.addEventListener('keydown', function(e) {
    if (e.target.tagName === 'INPUT') return;
    switch(e.code) {
      case 'Space':
        e.preventDefault();
        togglePlay();
        break;
      case 'ArrowRight':
        e.preventDefault();
        stopPlaying();
        stepForward();
        break;
      case 'ArrowLeft':
        e.preventDefault();
        stopPlaying();
        stepBackward();
        break;
      case 'Home':
        e.preventDefault();
        stopPlaying();
        goTo(-1);
        break;
      case 'End':
        e.preventDefault();
        stopPlaying();
        goTo(total - 1);
        break;
      case 'KeyT':
        e.preventDefault();
        skipTools = !skipTools;
        btnSkipTool.classList.toggle('active', skipTools);
        break;
    }
  });

  updateInfo();
})();
</script>
</body>
</html>
"""


def convert_to_player(messages, input_path, theme="console"):
    """メッセージリストをプレイヤーHTML文字列に変換する。"""
    message_blocks = []
    message_number = 0

    for data in messages:
        message = data.get("message", {})
        role = message.get("role", "")
        content = message.get("content", "")

        if role == "user":
            message_number += 1

        text = extract_text_from_content(content)
        tool_uses = extract_tool_uses(content)
        tool_results = extract_tool_results(content)

        if not text.strip() and not tool_uses and not tool_results:
            continue

        if role == "user":
            parts = [f'<div class="role-label">User ({message_number})</div>']
            if text.strip():
                parts.append(f'<div class="message-body">{markdown_to_html_simple(text.strip())}</div>')
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
            message_blocks.append(f'<div class="message user">\n' + "\n".join(parts) + "\n</div>")

        elif role == "assistant":
            # テキストとツールを別メッセージに分割（ツールスキップ用）
            if text.strip():
                text_parts = []
                text_parts.append('<div class="role-label">Assistant</div>')
                text_parts.append(f'<div class="message-body">{markdown_to_html_simple(text.strip())}</div>')
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
                    if result_text.strip():
                        truncated = escape(result_text[:500])
                        if len(result_text) > 500:
                            truncated += "\n... (truncated)"
                        tool_parts = []
                        tool_parts.append(f"<details><summary>Tool Result</summary><pre>{truncated}</pre></details>")
                        message_blocks.append(f'<div class="message assistant">\n' + "\n".join(tool_parts) + "\n</div>")

    theme_css = THEME_LIGHT if theme == "light" else THEME_CONSOLE
    all_messages = "\n".join(message_blocks)
    return PLAYER_TEMPLATE.replace("{{THEME}}", theme_css).replace("{{MESSAGES}}", all_messages)


# ---------------------------------------------------------------------------
# Terminal (Claude Code replica) output
# ---------------------------------------------------------------------------

TOOL_ICONS = {
    "Read": "\U0001F4C4",     # file
    "Write": "\u270F\uFE0F",  # pencil
    "Edit": "\U0001F527",     # wrench
    "Bash": "$",
    "Grep": "\U0001F50D",     # magnifier
    "Glob": "\U0001F50D",
    "Task": "\U0001F916",     # robot
    "WebFetch": "\U0001F310", # globe
    "WebSearch": "\U0001F310",
}

# trim empty 対象: 情報量が少ないメタ操作系ツール
TRIMMABLE_TOOLS = {"TaskCreate", "TaskUpdate", "TaskList", "TaskGet"}


def format_tool_use_terminal(tool_use):
    """ツール使用をClaude Code風HTMLに変換する。"""
    name = tool_use.get("name", "Unknown")
    tool_input = tool_use.get("input", {})
    icon = TOOL_ICONS.get(name, "\u2699")

    header = ""
    body = ""

    if name == "Read":
        file_path = escape(tool_input.get("file_path", ""))
        header = f'{icon} Read <span class="t-path">{file_path}</span>'
    elif name == "Write":
        file_path = escape(tool_input.get("file_path", ""))
        content = tool_input.get("content", "")
        line_count = content.count("\n") + 1 if content else 0
        header = f'{icon} Write <span class="t-path">{file_path}</span> <span class="t-dim">({line_count} lines)</span>'
    elif name == "Edit":
        file_path = escape(tool_input.get("file_path", ""))
        old_str = escape(tool_input.get("old_string", "")[:300])
        new_str = escape(tool_input.get("new_string", "")[:300])
        header = f'{icon} Edit <span class="t-path">{file_path}</span>'
        diff_lines = []
        for line in old_str.split("\n"):
            diff_lines.append(f'<span class="t-diff-del">- {line}</span>')
        for line in new_str.split("\n"):
            diff_lines.append(f'<span class="t-diff-add">+ {line}</span>')
        body = '<pre class="t-diff">' + "\n".join(diff_lines) + '</pre>'
    elif name == "Bash":
        command = escape(tool_input.get("command", ""))
        description = escape(tool_input.get("description", ""))
        header = f'<span class="t-bash-dollar">$</span> Bash'
        if description:
            header += f' <span class="t-dim">{description}</span>'
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


def convert_to_terminal(messages, input_path):
    """メッセージリストをClaude Code風プレイヤーHTMLに変換する。"""
    message_blocks = []
    message_number = 0

    for data in messages:
        message = data.get("message", {})
        role = message.get("role", "")
        content = message.get("content", "")

        if role == "user":
            message_number += 1

        text = extract_text_from_content(content)
        tool_uses = extract_tool_uses(content)
        tool_results = extract_tool_results(content)

        if not text.strip() and not tool_uses and not tool_results:
            continue

        if role == "user":
            if text.strip():
                user_html = f'<div class="t-prompt">\u276F</div>'
                user_html += f'<div class="t-user-text">{escape(text.strip())}</div>'
                message_blocks.append(f'<div class="t-msg t-user">{user_html}</div>')
        elif role == "assistant":
            if text.strip():
                body_html = markdown_to_html_simple(text.strip())
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
            tool_html = (
                f'<div class="t-msg t-tool{extra_class}">'
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
            if not str(result_text).strip():
                continue
            header = '\U0001F4DD Result'
            body = f'<pre class="t-cmd">{escape(str(result_text))}</pre>'
            tool_html = (
                f'<div class="t-msg t-tool">'
                f'<div class="t-tool-header">'
                f'<span class="t-spinner"></span>{header}'
                f'</div>'
                f'<div class="t-tool-body">{body}</div>'
                f'</div>')
            message_blocks.append(tool_html)

    all_messages = "\n".join(message_blocks)
    return TERMINAL_TEMPLATE.replace("{{MESSAGES}}", all_messages)


TERMINAL_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude Code</title>
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

  /* Top bar */
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

  /* Messages */
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

  /* User */
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

  /* Assistant */
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

  /* Tool blocks */
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

  /* Trim empty: hide tool-empty when active */
  body.trim-empty .t-tool-empty { display: none !important; }

  /* Collapse tool body when hide-details active */
  body.hide-details .t-tool-body { display: none !important; }
  body.hide-details .t-tool .t-tool-header { border-bottom: none; }
  .t-path { color: var(--path-color); }
  .t-str { color: var(--str-color); }
  .t-dim { color: var(--dim); }
  .t-bash-dollar { color: var(--check-color); font-weight: bold; }

  /* Tables */
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
  td {
    background: var(--tool-bg);
  }
  tr:hover td {
    background: var(--tool-header-bg);
  }

  /* Spinner */
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

  /* Diff */
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

  /* Command */
  .t-cmd {
    background: var(--cmd-bg);
    padding: 8px 12px;
    margin: 4px 0;
    border-radius: 4px;
    font-size: 12px;
    overflow-x: auto;
    color: var(--fg);
  }

  /* Code blocks in assistant response */
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

  /* Controls */
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
    min-width: 100px;
    text-align: center;
  }
  .speed-control {
    display: flex;
    align-items: center;
    gap: 4px;
    color: var(--dim);
    font-size: 12px;
  }
  .speed-control input[type=range] {
    width: 70px;
    accent-color: var(--assistant-bar);
  }
  .kbd {
    display: inline-block;
    background: var(--tool-bg);
    border: 1px solid var(--tool-border);
    border-radius: 3px;
    padding: 0 4px;
    font-family: var(--mono);
    font-size: 11px;
    color: var(--dim);
    margin-left: 1px;
  }
</style>
</head>
<body>

<div class="t-topbar">
  <span class="t-topbar-title">\\u2728 Claude Code</span>
  <span class="t-topbar-model">claude-opus-4-6</span>
  <span class="t-topbar-cwd">Session Replay</span>
</div>

<div class="t-container" id="chat">
{{MESSAGES}}
</div>

<div class="controls">
  <div class="progress-bar-container" id="progressContainer">
    <div class="progress-bar" id="progressBar"></div>
  </div>
  <div class="controls-row">
    <button id="btnFirst" title="Home">&#x23EE;</button>
    <button id="btnPrev" title="Left">&#x23EA;</button>
    <button id="btnPlay" title="Space">&#x25B6;</button>
    <button id="btnNext" title="Right">&#x23E9;</button>
    <button id="btnLast" title="End">&#x23ED;</button>
    <button id="btnSkipTool" title="T: skip tools during playback">skip tools</button>
    <button id="btnTrimEmpty" title="E: hide empty tool blocks (TaskCreate etc.)">trim empty</button>
    <button id="btnDetails" title="D: toggle tool details">details</button>
    <div class="speed-control">
      <span>speed</span>
      <input type="range" id="speedSlider" min="1" max="10" value="5">
      <span id="speedLabel">1.0x</span>
    </div>
    <div class="info" id="info">0 / 0</div>
    <div>
      <span class="kbd">Space</span>
      <span class="kbd">&larr;&rarr;</span>
      <span class="kbd">Home</span>
      <span class="kbd">End</span>
      <span class="kbd">T</span>
      <span class="kbd">E</span>
      <span class="kbd">D</span>
    </div>
  </div>
</div>

<script>
(function() {
  const msgs = document.querySelectorAll('.t-msg');
  const total = msgs.length;
  let current = -1;
  let playing = false;
  let timer = null;
  let skipTools = false;
  let speed = 1.0;

  const btnPlay = document.getElementById('btnPlay');
  const info = document.getElementById('info');
  const progressBar = document.getElementById('progressBar');
  const progressContainer = document.getElementById('progressContainer');
  const btnSkipTool = document.getElementById('btnSkipTool');
  const speedSlider = document.getElementById('speedSlider');
  const speedLabel = document.getElementById('speedLabel');

  function update() {
    info.textContent = (current + 1) + ' / ' + total;
    progressBar.style.width = (total > 0 ? ((current + 1) / total * 100) : 0) + '%';
  }

  function show(i) {
    if (i < 0 || i >= total) return;
    const m = msgs[i];
    m.classList.add('visible');
    // ツールブロックは表示後に少し遅れて完了マーク
    if (m.classList.contains('t-tool')) {
      setTimeout(function() { m.classList.add('done'); }, 600 / speed);
    }
    m.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }

  function hide(i) {
    if (i < 0 || i >= total) return;
    msgs[i].classList.remove('visible');
    msgs[i].classList.remove('done');
  }

  function isTool(i) {
    return i >= 0 && i < total && msgs[i].classList.contains('t-tool');
  }

  function goTo(idx) {
    idx = Math.max(-1, Math.min(idx, total - 1));
    if (idx > current) { for (let i = current + 1; i <= idx; i++) show(i); }
    else if (idx < current) { for (let i = current; i > idx; i--) hide(i); }
    current = idx;
    update();
  }

  function stepFwd() {
    let n = current + 1;
    if (skipTools) while (n < total && isTool(n)) n++;
    if (n >= total) { stopPlay(); return; }
    goTo(n);
  }

  function stepBack() {
    let p = current - 1;
    if (skipTools) while (p >= 0 && isTool(p)) p--;
    goTo(Math.max(p, -1));
  }

  function getInterval() {
    return Math.max(80, 1200 / speed);
  }

  function startPlay() {
    if (current >= total - 1) goTo(-1);
    playing = true;
    btnPlay.textContent = '\\u23F8';
    btnPlay.classList.add('active');
    tick();
  }

  function stopPlay() {
    playing = false;
    clearTimeout(timer);
    timer = null;
    btnPlay.textContent = '\\u25B6';
    btnPlay.classList.remove('active');
  }

  function tick() {
    if (!playing) return;
    stepFwd();
    if (current >= total - 1) { stopPlay(); return; }
    // ユーザーメッセージの後は少し長めに待つ
    let interval = getInterval();
    if (current >= 0 && msgs[current].classList.contains('t-user')) interval *= 1.5;
    timer = setTimeout(tick, interval);
  }

  function toggle() { playing ? stopPlay() : startPlay(); }

  btnPlay.addEventListener('click', toggle);
  document.getElementById('btnNext').addEventListener('click', function() { stopPlay(); stepFwd(); });
  document.getElementById('btnPrev').addEventListener('click', function() { stopPlay(); stepBack(); });
  document.getElementById('btnFirst').addEventListener('click', function() { stopPlay(); goTo(-1); });
  document.getElementById('btnLast').addEventListener('click', function() { stopPlay(); goTo(total - 1); });
  btnSkipTool.addEventListener('click', function() {
    skipTools = !skipTools;
    this.classList.toggle('active', skipTools);
  });

  var btnTrimEmpty = document.getElementById('btnTrimEmpty');
  var btnDetails = document.getElementById('btnDetails');
  var trimEmpty = false;
  var hideDetails = false;

  btnTrimEmpty.addEventListener('click', function() {
    trimEmpty = !trimEmpty;
    this.classList.toggle('active', trimEmpty);
    document.body.classList.toggle('trim-empty', trimEmpty);
  });

  btnDetails.addEventListener('click', function() {
    hideDetails = !hideDetails;
    this.classList.toggle('active', hideDetails);
    this.textContent = hideDetails ? 'details off' : 'details';
    document.body.classList.toggle('hide-details', hideDetails);
  });

  speedSlider.addEventListener('input', function() {
    var speeds = [0.25,0.5,0.75,1.0,1.5,2.0,3.0,5.0,8.0,16.0];
    speed = speeds[parseInt(this.value) - 1] || 1.0;
    speedLabel.textContent = speed + 'x';
  });

  progressContainer.addEventListener('click', function(e) {
    stopPlay();
    var pct = (e.clientX - this.getBoundingClientRect().left) / this.offsetWidth;
    goTo(Math.round(pct * total) - 1);
  });

  document.addEventListener('keydown', function(e) {
    if (e.target.tagName === 'INPUT') return;
    switch(e.code) {
      case 'Space': e.preventDefault(); toggle(); break;
      case 'ArrowRight': e.preventDefault(); stopPlay(); stepFwd(); break;
      case 'ArrowLeft': e.preventDefault(); stopPlay(); stepBack(); break;
      case 'Home': e.preventDefault(); stopPlay(); goTo(-1); break;
      case 'End': e.preventDefault(); stopPlay(); goTo(total - 1); break;
      case 'KeyT': e.preventDefault(); skipTools = !skipTools; btnSkipTool.classList.toggle('active', skipTools); break;
      case 'KeyE': e.preventDefault(); trimEmpty = !trimEmpty; btnTrimEmpty.classList.toggle('active', trimEmpty); document.body.classList.toggle('trim-empty', trimEmpty); break;
      case 'KeyD': e.preventDefault(); hideDetails = !hideDetails; btnDetails.classList.toggle('active', hideDetails); btnDetails.textContent = hideDetails ? 'details off' : 'details'; document.body.classList.toggle('hide-details', hideDetails); break;
    }
  });

  update();
})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Session discovery & selection UI
# ---------------------------------------------------------------------------

import glob as glob_module
from datetime import datetime, timezone
from pathlib import Path


def _format_size(size_bytes):
    """ファイルサイズを人間が読みやすい形式に変換する。"""
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f}M"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.0f}K"
    return f"{size_bytes}B"


def _extract_preview(jsonl_path):
    """JSONLファイルの先頭から プレビュー情報を抽出する。"""
    timestamp = None
    git_branch = ""
    first_message = ""
    user_count = 0
    assistant_count = 0

    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                record_type = data.get("type", "")

                if record_type == "user":
                    user_count += 1
                    if timestamp is None:
                        timestamp = data.get("timestamp", "")
                        git_branch = data.get("gitBranch", "")
                    if not first_message:
                        message = data.get("message", {})
                        content = message.get("content", "")
                        if isinstance(content, str):
                            first_message = content.strip()
                        elif isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    first_message = block.get("text", "").strip()
                                    break
                elif record_type == "assistant":
                    assistant_count += 1
    except (OSError, UnicodeDecodeError):
        pass

    return {
        "timestamp": timestamp or "",
        "git_branch": git_branch,
        "first_message": first_message,
        "user_count": user_count,
        "assistant_count": assistant_count,
    }


def _project_name_from_dir(dir_name):
    """ディレクトリ名からプロジェクト名（最後のパス要素）を復元する。"""
    parts = dir_name.lstrip("-").split("-")
    if parts:
        return parts[-1]
    return dir_name


def discover_sessions(project_filter=None):
    """~/.claude/projects/ 配下のセッションJSONLを検出する。"""
    claude_projects_dir = Path.home() / ".claude" / "projects"
    if not claude_projects_dir.is_dir():
        return []

    sessions = []
    for project_dir in sorted(claude_projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue

        project_name = _project_name_from_dir(project_dir.name)
        if project_filter and project_filter.lower() not in project_name.lower():
            continue

        for jsonl_file in project_dir.glob("*.jsonl"):
            # サブエージェント用ディレクトリ内のファイルは除外
            if "/subagents/" in str(jsonl_file):
                continue

            file_stat = jsonl_file.stat()
            sessions.append({
                "path": str(jsonl_file),
                "project": project_name,
                "project_dir": project_dir.name,
                "size": file_stat.st_size,
                "mtime": file_stat.st_mtime,
            })

    sessions.sort(key=lambda s: s["mtime"], reverse=True)

    # 極端に小さいファイル（メタデータのみ）を除外
    sessions = [s for s in sessions if s["size"] > 1024]
    return sessions


def select_session(sessions):
    """セッション一覧を表示して選択させる。"""
    if not sessions:
        print("No sessions found in ~/.claude/projects/")
        sys.exit(1)

    # プレビュー情報を収集し、空セッションを除外
    filtered_sessions = []
    previews = []
    for session in sessions:
        preview = _extract_preview(session["path"])
        total = preview["user_count"] + preview["assistant_count"]
        if total == 0:
            continue
        filtered_sessions.append(session)
        previews.append(preview)
    sessions = filtered_sessions

    if not sessions:
        print("No sessions with messages found in ~/.claude/projects/")
        sys.exit(1)

    # ヘッダー
    print()
    print(f"  {'#':>3}  {'Date':16}  {'Branch':28}  {'Project':14}  {'Size':>6}  {'Msgs':>5}  First message")
    print(f"  {'─' * 3}  {'─' * 16}  {'─' * 28}  {'─' * 14}  {'─' * 6}  {'─' * 5}  {'─' * 40}")

    for i, (session, preview) in enumerate(zip(sessions, previews)):
        idx = i + 1

        # 日時
        timestamp_str = preview["timestamp"]
        if timestamp_str:
            try:
                dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                dt_local = dt.astimezone()
                date_display = dt_local.strftime("%Y-%m-%d %H:%M")
            except (ValueError, OSError):
                date_display = timestamp_str[:16]
        else:
            date_display = datetime.fromtimestamp(session["mtime"]).strftime("%Y-%m-%d %H:%M")

        # ブランチ（長い場合は切り詰め）
        branch = preview["git_branch"]
        if len(branch) > 28:
            branch = branch[:26] + ".."

        # プロジェクト名
        project = session["project"]
        if len(project) > 14:
            project = project[:12] + ".."

        # サイズ
        size_display = _format_size(session["size"])

        # メッセージ数
        total_msgs = preview["user_count"] + preview["assistant_count"]

        # 最初のメッセージ（切り詰め）
        first_msg = preview["first_message"].replace("\n", " ")
        if len(first_msg) > 60:
            first_msg = first_msg[:58] + ".."

        print(f"  {idx:>3}  {date_display:16}  {branch:28}  {project:14}  {size_display:>6}  {total_msgs:>5}  {first_msg}")

    print()

    # 選択
    while True:
        try:
            choice = input(f"Select session [1]: ").strip()
            if not choice:
                choice = "1"
            num = int(choice)
            if 1 <= num <= len(sessions):
                selected = sessions[num - 1]
                print(f"  -> {selected['path']}")
                print()
                return selected["path"]
            else:
                print(f"  1-{len(sessions)} の数字を入力してください")
        except ValueError:
            print(f"  数字を入力してください")
        except (KeyboardInterrupt, EOFError):
            print()
            sys.exit(0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Claude Code JSONL session transcript converter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  python3 claude-session-replay.py                                  # -> session selector
  python3 claude-session-replay.py -f terminal                      # -> session selector -> terminal
  python3 claude-session-replay.py session.jsonl                    # -> session.md
  python3 claude-session-replay.py session.jsonl -f terminal        # -> session.html (Claude Code replica)
  python3 claude-session-replay.py session.jsonl -f html -t console # -> session.html (dark)
  python3 claude-session-replay.py --project onigiri -f terminal    # -> selector filtered by project
""")
    parser.add_argument("input", nargs="?", default=None,
                        help="input JSONL file path (omit to select interactively)")
    parser.add_argument("-o", "--output", help="output file path (default: input with .md/.html extension)")
    parser.add_argument("-f", "--format", choices=["md", "html", "player", "terminal"], default="md",
                        help="output format: md, html, player, or terminal (Claude Code replica)")
    parser.add_argument("-t", "--theme", choices=["light", "console"], default="light",
                        help="HTML theme: light (default) or console (dark terminal style)")
    parser.add_argument("--project", default=None,
                        help="filter sessions by project name (substring match)")

    args = parser.parse_args()

    # 入力ファイルの決定
    input_path = args.input
    if input_path is None:
        sessions = discover_sessions(project_filter=args.project)
        input_path = select_session(sessions)

    messages = parse_messages(input_path)

    if args.output:
        output_path = args.output
    else:
        extension = ".html" if args.format in ("html", "player", "terminal") else ".md"
        output_path = os.path.splitext(input_path)[0] + extension

    if args.format == "terminal":
        result = convert_to_terminal(messages, input_path)
    elif args.format == "player":
        result = convert_to_player(messages, input_path, theme=args.theme)
    elif args.format == "html":
        result = convert_to_html(messages, input_path, theme=args.theme)
    else:
        result = convert_to_markdown(messages, input_path)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(result)

    print(f"Converted {len(messages)} messages ({args.format}) -> {output_path}")


if __name__ == "__main__":
    main()
