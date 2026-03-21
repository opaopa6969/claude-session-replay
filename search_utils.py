#!/usr/bin/env python3
"""Search utilities for claude-session-replay.

Provides full-text search across session logs (cross-session and within-session).
Uses ProcessPoolExecutor for parallel cross-session search.
"""

import importlib.util
import json
import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path


# ---------------------------------------------------------------------------
# Adapter module loading
# ---------------------------------------------------------------------------

def _import_module(name, filepath):
    spec = importlib.util.spec_from_file_location(name, filepath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_script_dir = Path(__file__).parent
_adapters = {}


def _get_adapter(agent):
    """Lazily load and cache adapter modules."""
    if agent not in _adapters:
        filemap = {
            "claude": "claude-log2model.py",
            "codex": "codex-log2model.py",
            "gemini": "gemini-log2model.py",
        }
        if agent not in filemap:
            raise ValueError(f"Unknown agent: {agent}")
        _adapters[agent] = _import_module(
            f"{agent}_log2model", str(_script_dir / filemap[agent])
        )
    return _adapters[agent]


# ---------------------------------------------------------------------------
# Low-level text search
# ---------------------------------------------------------------------------

def search_in_text(text, query, case_sensitive=False, is_regex=False):
    """Find all match positions in *text*.

    Returns list of (start, length) tuples.
    """
    if not text or not query:
        return []

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        if is_regex:
            pattern = re.compile(query, flags)
        else:
            pattern = re.compile(re.escape(query), flags)
    except re.error:
        return []

    return [(m.start(), m.end() - m.start()) for m in pattern.finditer(text)]


def extract_excerpt(text, match_start, match_length, context=100):
    """Return a substring of *text* centred on the match with surrounding context.

    Returns (excerpt, offset_in_excerpt) so callers know where the match sits.
    """
    start = max(0, match_start - context)
    end = min(len(text), match_start + match_length + context)
    excerpt = text[start:end]
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    offset = match_start - start + len(prefix)
    return prefix + excerpt + suffix, offset


# ---------------------------------------------------------------------------
# Session-level search
# ---------------------------------------------------------------------------

_DEFAULT_SCOPE = ("text", "thinking", "tool_use", "tool_result")


def _build_common_model(session_path, agent):
    """Parse a session file into the common model dict."""
    adapter = _get_adapter(agent)
    if agent == "gemini":
        with open(session_path, "r", encoding="utf-8") as f:
            session_data = json.load(f)
        return adapter.build_model(session_data, session_path)
    else:
        messages = adapter.parse_messages(session_path)
        return adapter.build_model(messages, session_path)


def _tool_use_text(tool_use):
    """Flatten a tool_use block into searchable text."""
    name = tool_use.get("name", "")
    inp = tool_use.get("input", {})
    parts = [name]
    if isinstance(inp, dict):
        for v in inp.values():
            if isinstance(v, str):
                parts.append(v)
    return " ".join(parts)


def search_session_messages(session_path, agent, query, options=None):
    """Search all messages in one session.

    *options* dict keys:
        case_sensitive (bool, default False)
        regex (bool, default False)
        scope (list[str], default all four fields)
        max_matches (int, default 200)

    Returns list of match dicts:
        {idx, role, field, excerpt, match_offset, match_length}
    """
    if options is None:
        options = {}

    case_sensitive = options.get("case_sensitive", False)
    is_regex = options.get("regex", False)
    scope = options.get("scope", _DEFAULT_SCOPE)
    max_matches = options.get("max_matches", 200)

    try:
        model = _build_common_model(session_path, agent)
    except Exception:
        return []

    results = []
    for idx, msg in enumerate(model.get("messages", []), start=1):
        if len(results) >= max_matches:
            break

        role = msg.get("role", "")

        # text
        if "text" in scope:
            text = msg.get("text", "")
            for start, length in search_in_text(text, query, case_sensitive, is_regex):
                excerpt, offset = extract_excerpt(text, start, length)
                results.append({
                    "idx": idx,
                    "role": role,
                    "field": "text",
                    "excerpt": excerpt,
                    "match_offset": offset,
                    "match_length": length,
                })
                if len(results) >= max_matches:
                    break

        # thinking
        if "thinking" in scope:
            for block in msg.get("thinking", []):
                if len(results) >= max_matches:
                    break
                for start, length in search_in_text(block, query, case_sensitive, is_regex):
                    excerpt, offset = extract_excerpt(block, start, length)
                    results.append({
                        "idx": idx,
                        "role": role,
                        "field": "thinking",
                        "excerpt": excerpt,
                        "match_offset": offset,
                        "match_length": length,
                    })
                    if len(results) >= max_matches:
                        break

        # tool_use
        if "tool_use" in scope:
            for tu in msg.get("tool_uses", []):
                if len(results) >= max_matches:
                    break
                tu_text = _tool_use_text(tu)
                for start, length in search_in_text(tu_text, query, case_sensitive, is_regex):
                    excerpt, offset = extract_excerpt(tu_text, start, length)
                    results.append({
                        "idx": idx,
                        "role": role,
                        "field": "tool_use",
                        "excerpt": excerpt,
                        "match_offset": offset,
                        "match_length": length,
                    })
                    if len(results) >= max_matches:
                        break

        # tool_result
        if "tool_result" in scope:
            for tr in msg.get("tool_results", []):
                if len(results) >= max_matches:
                    break
                content = tr.get("content", "")
                for start, length in search_in_text(content, query, case_sensitive, is_regex):
                    excerpt, offset = extract_excerpt(content, start, length)
                    results.append({
                        "idx": idx,
                        "role": role,
                        "field": "tool_result",
                        "excerpt": excerpt,
                        "match_offset": offset,
                        "match_length": length,
                    })
                    if len(results) >= max_matches:
                        break

    return results


# ---------------------------------------------------------------------------
# Cross-session parallel search
# ---------------------------------------------------------------------------

def _search_one_session(session_path, agent, query, options):
    """Worker function for ProcessPoolExecutor (must be top-level for pickling)."""
    return search_session_messages(session_path, agent, query, options)


def search_across_sessions(agents, query, options=None):
    """Search across all sessions of the given agents in parallel.

    *options* dict keys (in addition to search_session_messages options):
        max_sessions (int, default 100)
        max_matches_per_session (int, default 5)

    Returns:
        results: list of {agent, session, match_count, matches}
        stats: {sessions_scanned, sessions_matched, total_matches, elapsed_ms}
    """
    import time

    if options is None:
        options = {}

    max_sessions = options.get("max_sessions", 100)
    max_per_session = options.get("max_matches_per_session", 5)

    # Per-session options
    session_opts = {
        "case_sensitive": options.get("case_sensitive", False),
        "regex": options.get("regex", False),
        "scope": options.get("scope", list(_DEFAULT_SCOPE)),
        "max_matches": max_per_session,
    }

    # Discover sessions
    sessions = []
    for agent in agents:
        adapter = _get_adapter(agent)
        for s in adapter.discover_sessions():
            s["agent"] = agent
            sessions.append(s)
            if len(sessions) >= max_sessions:
                break
        if len(sessions) >= max_sessions:
            break

    # Sort by mtime descending (newest first)
    sessions.sort(key=lambda s: s.get("mtime", 0), reverse=True)
    sessions = sessions[:max_sessions]

    t0 = time.monotonic()
    results = []
    total_matches = 0
    max_workers = min(os.cpu_count() or 4, max(len(sessions), 1), 8)

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        future_to_session = {
            executor.submit(
                _search_one_session, s["path"], s["agent"], query, session_opts
            ): s
            for s in sessions
        }

        for future in as_completed(future_to_session):
            session = future_to_session[future]
            try:
                matches = future.result()
            except Exception:
                matches = []

            if matches:
                results.append({
                    "agent": session["agent"],
                    "session": {
                        "path": session["path"],
                        "project": session.get("project", ""),
                        "size": session.get("size", 0),
                        "mtime": session.get("mtime", 0),
                    },
                    "match_count": len(matches),
                    "matches": matches,
                })
                total_matches += len(matches)

    elapsed_ms = int((time.monotonic() - t0) * 1000)

    # Sort results by newest session first
    results.sort(key=lambda r: r["session"].get("mtime", 0), reverse=True)

    stats = {
        "sessions_scanned": len(sessions),
        "sessions_matched": len(results),
        "total_matches": total_matches,
        "elapsed_ms": elapsed_ms,
    }
    return results, stats
