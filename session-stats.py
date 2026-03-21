#!/usr/bin/env python3
"""Session statistics and diff engine for claude-session-replay.

Provides per-session stats, cross-session overview, and session diff/comparison.
"""

import argparse
import html as html_mod
import importlib.util
import json
import os
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Adapter loading (same pattern as search_utils.py)
# ---------------------------------------------------------------------------

def _import_module(name, filepath):
    spec = importlib.util.spec_from_file_location(name, filepath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_script_dir = Path(__file__).parent
_adapters = {}
_ADAPTER_FILES = {
    "claude": "claude-log2model.py",
    "codex": "codex-log2model.py",
    "gemini": "gemini-log2model.py",
}


def _get_adapter(agent):
    if agent not in _adapters:
        if agent not in _ADAPTER_FILES:
            raise ValueError(f"Unknown agent: {agent}")
        _adapters[agent] = _import_module(
            f"{agent}_log2model", str(_script_dir / _ADAPTER_FILES[agent])
        )
    return _adapters[agent]


def _build_common_model(session_path, agent):
    adapter = _get_adapter(agent)
    if agent == "gemini":
        with open(session_path, "r", encoding="utf-8") as f:
            session_data = json.load(f)
        return adapter.build_model(session_data, session_path)
    else:
        messages = adapter.parse_messages(session_path)
        return adapter.build_model(messages, session_path)


# ---------------------------------------------------------------------------
# Time utilities
# ---------------------------------------------------------------------------

def _parse_ts(ts_str):
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _format_duration(seconds):
    if seconds is None or seconds < 0:
        return "N/A"
    h = int(seconds) // 3600
    m = (int(seconds) % 3600) // 60
    s = int(seconds) % 60
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"


# ---------------------------------------------------------------------------
# Per-session statistics
# ---------------------------------------------------------------------------

def compute_session_stats(model):
    """Compute detailed statistics from a common model dict."""
    messages = model.get("messages", [])
    source = model.get("source", "")
    agent = model.get("agent", "")

    # Message counts
    user_msgs = [m for m in messages if m.get("role") == "user"]
    assistant_msgs = [m for m in messages if m.get("role") == "assistant"]

    # Timestamps
    timestamps = [_parse_ts(m.get("timestamp", "")) for m in messages]
    timestamps = [t for t in timestamps if t is not None]
    timestamps.sort()

    start_ts = timestamps[0] if timestamps else None
    end_ts = timestamps[-1] if timestamps else None
    total_seconds = (end_ts - start_ts).total_seconds() if start_ts and end_ts else None

    # Response times (time between user message and next assistant message)
    response_times = []
    for i, msg in enumerate(messages):
        if msg.get("role") == "user" and i + 1 < len(messages):
            next_msg = messages[i + 1]
            if next_msg.get("role") == "assistant":
                t1 = _parse_ts(msg.get("timestamp", ""))
                t2 = _parse_ts(next_msg.get("timestamp", ""))
                if t1 and t2:
                    diff = (t2 - t1).total_seconds()
                    if 0 < diff < 3600:  # sanity: under 1h
                        response_times.append(diff)

    # Tool stats
    tool_counter = Counter()
    for msg in messages:
        for tu in msg.get("tool_uses", []):
            tool_counter[tu.get("name", "Unknown")] += 1
    total_tool_uses = sum(tool_counter.values())

    # Text stats
    user_chars = sum(len(m.get("text", "")) for m in user_msgs)
    assistant_chars = sum(len(m.get("text", "")) for m in assistant_msgs)
    total_chars = user_chars + assistant_chars

    # Thinking stats
    thinking_blocks = 0
    thinking_chars = 0
    msgs_with_thinking = 0
    for msg in messages:
        blocks = msg.get("thinking", [])
        if blocks:
            msgs_with_thinking += 1
            thinking_blocks += len(blocks)
            thinking_chars += sum(len(b) for b in blocks)

    # Tool result stats
    tool_result_count = sum(len(m.get("tool_results", [])) for m in messages)
    tool_result_chars = sum(
        len(tr.get("content", ""))
        for m in messages
        for tr in m.get("tool_results", [])
    )

    return {
        "source": source,
        "agent": agent,
        "message_count": {
            "total": len(messages),
            "user": len(user_msgs),
            "assistant": len(assistant_msgs),
        },
        "duration": {
            "start": start_ts.isoformat() if start_ts else "",
            "end": end_ts.isoformat() if end_ts else "",
            "total_seconds": total_seconds,
            "formatted": _format_duration(total_seconds),
        },
        "tool_stats": {
            "total_uses": total_tool_uses,
            "by_name": dict(tool_counter.most_common()),
            "total_results": tool_result_count,
            "result_chars": tool_result_chars,
        },
        "text_stats": {
            "total_chars": total_chars,
            "avg_chars_per_message": total_chars // max(len(messages), 1),
            "user_chars": user_chars,
            "assistant_chars": assistant_chars,
            "ratio": round(assistant_chars / max(user_chars, 1), 1),
        },
        "thinking_stats": {
            "total_blocks": thinking_blocks,
            "messages_with_thinking": msgs_with_thinking,
            "total_chars": thinking_chars,
        },
        "timing": {
            "avg_response_time_seconds": round(sum(response_times) / max(len(response_times), 1), 1) if response_times else None,
            "max_response_time_seconds": round(max(response_times), 1) if response_times else None,
            "min_response_time_seconds": round(min(response_times), 1) if response_times else None,
            "messages_per_minute": round(len(messages) / max(total_seconds / 60, 1), 2) if total_seconds and total_seconds > 0 else None,
        },
    }


# ---------------------------------------------------------------------------
# Cross-session overview (parallel)
# ---------------------------------------------------------------------------

def _compute_one_session_stats(session_path, agent):
    """Worker for ProcessPoolExecutor."""
    try:
        model = _build_common_model(session_path, agent)
        stats = compute_session_stats(model)
        return stats
    except Exception:
        return None


def compute_overview_stats(agents=None):
    """Compute cross-session overview statistics using parallel processing."""
    if agents is None:
        agents = ["claude", "codex", "gemini"]

    sessions = []
    for agent in agents:
        adapter = _get_adapter(agent)
        for s in adapter.discover_sessions():
            s["agent"] = agent
            sessions.append(s)

    t0 = time.monotonic()
    all_stats = []
    max_workers = min(os.cpu_count() or 4, max(len(sessions), 1), 8)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_compute_one_session_stats, s["path"], s["agent"]): s
            for s in sessions
        }
        for future in as_completed(futures):
            result = future.result()
            if result:
                session = futures[future]
                result["_project"] = session.get("project", "")
                result["_path"] = session.get("path", "")
                result["_mtime"] = session.get("mtime", 0)
                all_stats.append(result)

    elapsed_ms = int((time.monotonic() - t0) * 1000)

    # Aggregate
    by_agent = defaultdict(lambda: {"sessions": 0, "messages": 0, "tool_uses": 0})
    by_project = defaultdict(lambda: {"sessions": 0, "messages": 0})
    tool_distribution = Counter()
    timeline = defaultdict(lambda: {"sessions": 0, "messages": 0})
    total_messages = 0
    durations = []

    for s in all_stats:
        agent = s.get("agent", "?")
        mc = s["message_count"]["total"]
        total_messages += mc
        by_agent[agent]["sessions"] += 1
        by_agent[agent]["messages"] += mc
        by_agent[agent]["tool_uses"] += s["tool_stats"]["total_uses"]

        proj = s.get("_project", "unknown")
        by_project[proj]["sessions"] += 1
        by_project[proj]["messages"] += mc

        for tool_name, count in s["tool_stats"]["by_name"].items():
            tool_distribution[tool_name] += count

        dur = s["duration"].get("total_seconds")
        if dur and dur > 0:
            durations.append(dur)

        # Timeline by date
        start = s["duration"].get("start", "")
        if start:
            date_key = start[:10]
            timeline[date_key]["sessions"] += 1
            timeline[date_key]["messages"] += mc

    return {
        "total_sessions": len(all_stats),
        "total_messages": total_messages,
        "by_agent": dict(by_agent),
        "by_project": dict(sorted(by_project.items(), key=lambda x: x[1]["messages"], reverse=True)),
        "tool_distribution": dict(tool_distribution.most_common()),
        "timeline": [{"date": k, **v} for k, v in sorted(timeline.items())],
        "avg_session_duration_seconds": round(sum(durations) / max(len(durations), 1)),
        "avg_session_duration_formatted": _format_duration(sum(durations) / max(len(durations), 1)),
        "avg_messages_per_session": round(total_messages / max(len(all_stats), 1)),
        "elapsed_ms": elapsed_ms,
    }


# ---------------------------------------------------------------------------
# Diff / comparison
# ---------------------------------------------------------------------------

def compute_diff(model_a, model_b):
    """Compare two session models."""
    stats_a = compute_session_stats(model_a)
    stats_b = compute_session_stats(model_b)

    tools_a = set(stats_a["tool_stats"]["by_name"].keys())
    tools_b = set(stats_b["tool_stats"]["by_name"].keys())

    dur_a = stats_a["duration"]["total_seconds"] or 0
    dur_b = stats_b["duration"]["total_seconds"] or 0

    return {
        "session_a": stats_a,
        "session_b": stats_b,
        "comparison": {
            "message_count_diff": stats_b["message_count"]["total"] - stats_a["message_count"]["total"],
            "duration_diff_seconds": dur_b - dur_a,
            "duration_diff_formatted": _format_duration(abs(dur_b - dur_a)),
            "tool_count_diff": stats_b["tool_stats"]["total_uses"] - stats_a["tool_stats"]["total_uses"],
            "text_chars_diff": stats_b["text_stats"]["total_chars"] - stats_a["text_stats"]["total_chars"],
            "common_tools": sorted(tools_a & tools_b),
            "only_a_tools": sorted(tools_a - tools_b),
            "only_b_tools": sorted(tools_b - tools_a),
        },
        "messages_a": model_a.get("messages", []),
        "messages_b": model_b.get("messages", []),
    }


# ---------------------------------------------------------------------------
# CLI display helpers
# ---------------------------------------------------------------------------

def _print_session_stats(stats):
    """Print session stats in a formatted table."""
    print(f"\n  📊 Session Statistics: {stats['source']}")
    print(f"  {'─' * 60}")

    mc = stats["message_count"]
    print(f"  Messages:      {mc['total']} total ({mc['user']} user, {mc['assistant']} assistant)")

    d = stats["duration"]
    print(f"  Duration:      {d['formatted']}")
    if d["start"]:
        print(f"  Time range:    {d['start'][:19]} → {d['end'][:19]}")

    ts = stats["tool_stats"]
    print(f"  Tool uses:     {ts['total_uses']} total, {ts['total_results']} results")
    if ts["by_name"]:
        tool_str = ", ".join(f"{n}: {c}" for n, c in list(ts["by_name"].items())[:8])
        print(f"  Tool breakdown: {tool_str}")

    tx = stats["text_stats"]
    print(f"  Text:          {tx['total_chars']:,} chars (user: {tx['user_chars']:,}, assistant: {tx['assistant_chars']:,})")
    print(f"  Avg msg size:  {tx['avg_chars_per_message']:,} chars")
    print(f"  Assistant/User ratio: {tx['ratio']}x")

    th = stats["thinking_stats"]
    if th["total_blocks"] > 0:
        print(f"  Thinking:      {th['total_blocks']} blocks in {th['messages_with_thinking']} messages ({th['total_chars']:,} chars)")

    tm = stats["timing"]
    if tm["avg_response_time_seconds"]:
        print(f"  Response time: avg {tm['avg_response_time_seconds']}s, max {tm['max_response_time_seconds']}s, min {tm['min_response_time_seconds']}s")
    if tm["messages_per_minute"]:
        print(f"  Pace:          {tm['messages_per_minute']} messages/min")

    print()


def _print_overview_stats(overview):
    """Print cross-session overview."""
    print(f"\n  📈 Overview Statistics ({overview['total_sessions']} sessions, {overview['total_messages']:,} messages)")
    print(f"  Computed in {overview['elapsed_ms']}ms")
    print(f"  {'─' * 60}")

    print(f"\n  By Agent:")
    for agent, data in overview["by_agent"].items():
        print(f"    {agent:10}  {data['sessions']:>3} sessions  {data['messages']:>7,} msgs  {data['tool_uses']:>6,} tools")

    print(f"\n  By Project (top 10):")
    for proj, data in list(overview["by_project"].items())[:10]:
        print(f"    {proj:20}  {data['sessions']:>3} sessions  {data['messages']:>7,} msgs")

    print(f"\n  Tool Distribution (top 10):")
    for tool, count in list(overview["tool_distribution"].items())[:10]:
        bar = "█" * min(count // 100, 40)
        print(f"    {tool:15}  {count:>6,}  {bar}")

    print(f"\n  Averages:")
    print(f"    Session duration:  {overview['avg_session_duration_formatted']}")
    print(f"    Messages/session:  {overview['avg_messages_per_session']}")

    if overview["timeline"]:
        print(f"\n  Timeline (last 14 days):")
        for entry in overview["timeline"][-14:]:
            bar = "█" * min(entry["messages"] // 50, 40)
            print(f"    {entry['date']}  {entry['sessions']:>2} sessions  {entry['messages']:>5,} msgs  {bar}")

    print()


def _print_diff(diff):
    """Print session diff comparison."""
    sa = diff["session_a"]
    sb = diff["session_b"]
    comp = diff["comparison"]

    print(f"\n  🔀 Session Diff")
    print(f"  {'─' * 70}")

    def _sign(v):
        return f"+{v}" if v > 0 else str(v)

    print(f"\n  {'':30}  {'Session A':>15}  {'Session B':>15}  {'Diff':>10}")
    print(f"  {'─' * 30}  {'─' * 15}  {'─' * 15}  {'─' * 10}")
    print(f"  {'Source':30}  {sa['source'][:15]:>15}  {sb['source'][:15]:>15}")
    print(f"  {'Agent':30}  {sa['agent']:>15}  {sb['agent']:>15}")
    print(f"  {'Messages':30}  {sa['message_count']['total']:>15}  {sb['message_count']['total']:>15}  {_sign(comp['message_count_diff']):>10}")
    print(f"  {'Duration':30}  {sa['duration']['formatted']:>15}  {sb['duration']['formatted']:>15}  {comp['duration_diff_formatted']:>10}")
    print(f"  {'Tool uses':30}  {sa['tool_stats']['total_uses']:>15}  {sb['tool_stats']['total_uses']:>15}  {_sign(comp['tool_count_diff']):>10}")
    print(f"  {'Text chars':30}  {sa['text_stats']['total_chars']:>15,}  {sb['text_stats']['total_chars']:>15,}  {_sign(comp['text_chars_diff']):>10}")
    print(f"  {'Avg response time':30}  {str(sa['timing']['avg_response_time_seconds']) + 's':>15}  {str(sb['timing']['avg_response_time_seconds']) + 's':>15}")

    print(f"\n  Tools:")
    if comp["common_tools"]:
        print(f"    Common:   {', '.join(comp['common_tools'])}")
    if comp["only_a_tools"]:
        print(f"    Only A:   {', '.join(comp['only_a_tools'])}")
    if comp["only_b_tools"]:
        print(f"    Only B:   {', '.join(comp['only_b_tools'])}")

    # Tool comparison
    all_tools = sorted(set(list(sa["tool_stats"]["by_name"].keys()) + list(sb["tool_stats"]["by_name"].keys())))
    if all_tools:
        print(f"\n  {'Tool':20}  {'A':>8}  {'B':>8}  {'Diff':>8}")
        print(f"  {'─' * 20}  {'─' * 8}  {'─' * 8}  {'─' * 8}")
        for tool in all_tools:
            a_count = sa["tool_stats"]["by_name"].get(tool, 0)
            b_count = sb["tool_stats"]["by_name"].get(tool, 0)
            print(f"  {tool:20}  {a_count:>8}  {b_count:>8}  {_sign(b_count - a_count):>8}")

    print()


# ---------------------------------------------------------------------------
# HTML output
# ---------------------------------------------------------------------------

def _esc(text):
    return html_mod.escape(str(text))


def render_stats_html(stats):
    """Render session stats as a self-contained HTML dashboard."""
    tool_rows = ""
    for name, count in stats["tool_stats"]["by_name"].items():
        pct = count / max(stats["tool_stats"]["total_uses"], 1) * 100
        tool_rows += f'<tr><td>{_esc(name)}</td><td>{count}</td><td><div class="bar" style="width:{pct}%"></div></td></tr>\n'

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Session Stats: {_esc(stats['source'])}</title>
<style>
  :root {{ --bg: #1a1a2e; --card: #16213e; --text: #e0e0e0; --accent: #4a9eff; --green: #4caf50; --orange: #ff9800; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, sans-serif; background: var(--bg); color: var(--text); padding: 20px; }}
  h1 {{ text-align: center; color: var(--accent); margin-bottom: 24px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; max-width: 1000px; margin: 0 auto 24px; }}
  .card {{ background: var(--card); border-radius: 8px; padding: 16px; text-align: center; }}
  .card .value {{ font-size: 28px; font-weight: bold; color: var(--accent); }}
  .card .label {{ font-size: 12px; color: #888; margin-top: 4px; }}
  table {{ width: 100%; max-width: 800px; margin: 16px auto; border-collapse: collapse; }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #333; }}
  th {{ color: var(--accent); }}
  .bar {{ height: 14px; background: var(--accent); border-radius: 3px; min-width: 2px; }}
  .section {{ max-width: 800px; margin: 24px auto; }}
  .section h2 {{ color: var(--orange); margin-bottom: 12px; }}
</style></head><body>
<h1>📊 {_esc(stats['source'])}</h1>
<div class="grid">
  <div class="card"><div class="value">{stats['message_count']['total']}</div><div class="label">Messages</div></div>
  <div class="card"><div class="value">{stats['message_count']['user']}</div><div class="label">User</div></div>
  <div class="card"><div class="value">{stats['message_count']['assistant']}</div><div class="label">Assistant</div></div>
  <div class="card"><div class="value">{stats['duration']['formatted']}</div><div class="label">Duration</div></div>
  <div class="card"><div class="value">{stats['tool_stats']['total_uses']}</div><div class="label">Tool Uses</div></div>
  <div class="card"><div class="value">{stats['text_stats']['avg_chars_per_message']:,}</div><div class="label">Avg Chars/Msg</div></div>
  <div class="card"><div class="value">{stats['text_stats']['ratio']}x</div><div class="label">Assistant/User Ratio</div></div>
  <div class="card"><div class="value">{stats['thinking_stats']['total_blocks']}</div><div class="label">Thinking Blocks</div></div>
</div>
<div class="section"><h2>Tool Distribution</h2>
<table><tr><th>Tool</th><th>Count</th><th>Distribution</th></tr>
{tool_rows}</table></div>
<div class="section"><h2>Timing</h2>
<table>
<tr><td>Avg Response Time</td><td>{stats['timing']['avg_response_time_seconds'] or 'N/A'}s</td></tr>
<tr><td>Max Response Time</td><td>{stats['timing']['max_response_time_seconds'] or 'N/A'}s</td></tr>
<tr><td>Messages/Minute</td><td>{stats['timing']['messages_per_minute'] or 'N/A'}</td></tr>
</table></div>
<div class="section"><h2>Text Statistics</h2>
<table>
<tr><td>Total Characters</td><td>{stats['text_stats']['total_chars']:,}</td></tr>
<tr><td>User Characters</td><td>{stats['text_stats']['user_chars']:,}</td></tr>
<tr><td>Assistant Characters</td><td>{stats['text_stats']['assistant_chars']:,}</td></tr>
<tr><td>Thinking Characters</td><td>{stats['thinking_stats']['total_chars']:,}</td></tr>
<tr><td>Tool Result Characters</td><td>{stats['tool_stats']['result_chars']:,}</td></tr>
</table></div>
</body></html>"""


def render_diff_html(diff):
    """Render diff as side-by-side HTML."""
    sa = diff["session_a"]
    sb = diff["session_b"]
    comp = diff["comparison"]

    def _sign(v):
        if v > 0:
            return f'<span style="color:#4caf50">+{v}</span>'
        elif v < 0:
            return f'<span style="color:#f44336">{v}</span>'
        return "0"

    # Comparison table rows
    rows = ""
    metrics = [
        ("Messages", sa["message_count"]["total"], sb["message_count"]["total"], comp["message_count_diff"]),
        ("Duration", sa["duration"]["formatted"], sb["duration"]["formatted"], None),
        ("Tool Uses", sa["tool_stats"]["total_uses"], sb["tool_stats"]["total_uses"], comp["tool_count_diff"]),
        ("Text Chars", f"{sa['text_stats']['total_chars']:,}", f"{sb['text_stats']['total_chars']:,}", comp["text_chars_diff"]),
        ("Avg Response", f"{sa['timing']['avg_response_time_seconds'] or 'N/A'}s", f"{sb['timing']['avg_response_time_seconds'] or 'N/A'}s", None),
    ]
    for label, va, vb, d in metrics:
        diff_cell = _sign(d) if d is not None else ""
        rows += f"<tr><td>{label}</td><td>{va}</td><td>{vb}</td><td>{diff_cell}</td></tr>\n"

    # Tool comparison rows
    tool_rows = ""
    all_tools = sorted(set(list(sa["tool_stats"]["by_name"].keys()) + list(sb["tool_stats"]["by_name"].keys())))
    for tool in all_tools:
        a = sa["tool_stats"]["by_name"].get(tool, 0)
        b = sb["tool_stats"]["by_name"].get(tool, 0)
        tool_rows += f"<tr><td>{_esc(tool)}</td><td>{a}</td><td>{b}</td><td>{_sign(b - a)}</td></tr>\n"

    # Message side-by-side
    msgs_a = diff["messages_a"]
    msgs_b = diff["messages_b"]
    max_msgs = max(len(msgs_a), len(msgs_b))
    msg_rows = ""
    for i in range(min(max_msgs, 200)):  # cap at 200 for perf
        def _msg_cell(msgs, idx):
            if idx >= len(msgs):
                return '<td class="empty">—</td>'
            m = msgs[idx]
            role = m.get("role", "?")
            text = _esc(m.get("text", "")[:200])
            tools = len(m.get("tool_uses", []))
            badge = f'<span class="badge">{role}</span>'
            tool_badge = f' <span class="tool-badge">{tools} tools</span>' if tools else ""
            return f'<td class="msg-cell {role}">{badge}{tool_badge}<div class="msg-text">{text}</div></td>'

        msg_rows += f"<tr><td class='idx'>#{i+1}</td>{_msg_cell(msgs_a, i)}{_msg_cell(msgs_b, i)}</tr>\n"

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Session Diff</title>
<style>
  :root {{ --bg: #1a1a2e; --card: #16213e; --text: #e0e0e0; --accent: #4a9eff; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, sans-serif; background: var(--bg); color: var(--text); padding: 20px; }}
  h1 {{ text-align: center; color: var(--accent); margin-bottom: 8px; }}
  h2 {{ color: #ff9800; margin: 20px 0 12px; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }}
  th, td {{ padding: 8px; text-align: left; border-bottom: 1px solid #333; }}
  th {{ color: var(--accent); }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: bold; }}
  .badge {{ background: #333; }}
  td.user .badge {{ background: #1b5e20; }}
  td.assistant .badge {{ background: #0d47a1; }}
  .tool-badge {{ background: #e65100; padding: 2px 6px; border-radius: 8px; font-size: 10px; color: white; }}
  .msg-text {{ margin-top: 4px; font-size: 12px; color: #aaa; font-family: monospace; white-space: pre-wrap; max-height: 80px; overflow: hidden; }}
  .msg-cell {{ vertical-align: top; width: 45%; }}
  .idx {{ width: 5%; color: #555; text-align: center; }}
  .empty {{ color: #444; text-align: center; }}
  .subtitle {{ text-align: center; color: #888; margin-bottom: 20px; }}
</style></head><body>
<h1>🔀 Session Diff</h1>
<p class="subtitle">{_esc(sa['source'])} vs {_esc(sb['source'])}</p>

<h2>Comparison</h2>
<table><tr><th>Metric</th><th>Session A</th><th>Session B</th><th>Diff</th></tr>
{rows}</table>

<h2>Tool Comparison</h2>
<table><tr><th>Tool</th><th>A</th><th>B</th><th>Diff</th></tr>
{tool_rows}</table>

<h2>Messages (side-by-side)</h2>
<table><tr><th>#</th><th>Session A</th><th>Session B</th></tr>
{msg_rows}</table>
</body></html>"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Session statistics and diff")
    parser.add_argument("--agent", choices=["claude", "codex", "gemini"], default=None)

    sub = parser.add_subparsers(dest="command")

    # stats
    stats_p = sub.add_parser("stats", help="Show session statistics")
    stats_p.add_argument("session", nargs="?", help="session file path (omit for interactive selection)")
    stats_p.add_argument("--html", "-o", help="output as HTML to file")

    # overview
    sub.add_parser("overview", help="Cross-session overview statistics")

    # diff
    diff_p = sub.add_parser("diff", help="Compare two sessions")
    diff_p.add_argument("session_a", help="first session file")
    diff_p.add_argument("session_b", help="second session file")
    diff_p.add_argument("--html", "-o", help="output as HTML to file")

    args = parser.parse_args()

    if args.command == "stats":
        agent = args.agent or "claude"
        if args.session:
            path = args.session
        else:
            adapter = _get_adapter(agent)
            sessions = adapter.discover_sessions()
            path = adapter.select_session(sessions)

        model = _build_common_model(path, agent)
        stats = compute_session_stats(model)

        if args.html:
            html = render_stats_html(stats)
            with open(args.html, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"Stats HTML written to {args.html}")
        else:
            _print_session_stats(stats)

    elif args.command == "overview":
        agents = [args.agent] if args.agent else ["claude", "codex", "gemini"]
        overview = compute_overview_stats(agents)
        _print_overview_stats(overview)

    elif args.command == "diff":
        agent = args.agent or "claude"
        model_a = _build_common_model(args.session_a, agent)
        model_b = _build_common_model(args.session_b, agent)
        diff = compute_diff(model_a, model_b)

        if args.html:
            html = render_diff_html(diff)
            with open(args.html, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"Diff HTML written to {args.html}")
        else:
            _print_diff(diff)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
