#!/usr/bin/env python3
"""Wrapper: agent log -> common model -> renderer.  Also provides --search for cross-session search."""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(cmd):
    return subprocess.run(cmd, check=False)


def _cli_main(args):
    """CLI mode (with command-line arguments)."""
    # Delegate pdf/gif to their standalone export scripts
    if args.format in ("pdf", "gif"):
        script = "log-replay-pdf.py" if args.format == "pdf" else "log-replay-gif.py"
        cmd = [sys.executable, script, "--agent", args.agent]
        if args.input:
            cmd.append(args.input)
        if args.output:
            cmd += ["-o", args.output]
        if args.project:
            cmd += ["--project", args.project]
        if args.filter:
            cmd += ["--filter", args.filter]
        if args.theme:
            cmd += ["-t", args.theme]
        if args.log_arg:
            for extra in args.log_arg:
                if extra.startswith("-"):
                    cmd.append("--log-arg={0}".format(extra))
                else:
                    cmd += ["--log-arg", extra]
        if args.render_arg:
            for extra in args.render_arg:
                if extra.startswith("-"):
                    cmd.append("--render-arg={0}".format(extra))
                else:
                    cmd += ["--render-arg", extra]
        res = _run(cmd)
        raise SystemExit(res.returncode)

    if args.agent == "claude":
        log2model = "claude-log2model.py"
        agent_args = []
        if args.project:
            agent_args += ["--project", args.project]
    elif args.agent == "gemini":
        log2model = "gemini-log2model.py"
        agent_args = []
        if args.project:
            agent_args += ["--project", args.project]
    elif args.agent == "aider":
        log2model = "aider-log2model.py"
        agent_args = []
        if args.project:
            agent_args += ["--project", args.project]
    elif args.agent == "cursor":
        log2model = "cursor-log2model.py"
        agent_args = []
        if args.project:
            agent_args += ["--project", args.project]
    else:
        log2model = "codex-log2model.py"
        agent_args = []
        if args.filter:
            agent_args += ["--filter", args.filter]

    if args.model:
        model_path = args.model
    else:
        fd, model_path = tempfile.mkstemp(prefix="log-model-", suffix=".json")
        os.close(fd)

    log_cmd = [sys.executable, log2model]
    if args.input:
        log_cmd.append(args.input)
    log_cmd += ["-o", model_path]
    log_cmd += agent_args
    if args.log_arg:
        log_cmd += args.log_arg

    res = _run(log_cmd)
    if res.returncode != 0:
        raise SystemExit(res.returncode)

    render_cmd = [sys.executable, "log-model-renderer.py", model_path, "-f", args.format, "-t", args.theme]
    if args.output:
        render_cmd += ["-o", args.output]
    if args.render_arg:
        render_cmd += args.render_arg

    res = _run(render_cmd)
    if res.returncode != 0:
        raise SystemExit(res.returncode)

    if not args.model:
        try:
            os.remove(model_path)
        except OSError:
            pass


def _search_main(args):
    """Search mode: search across sessions and display results."""
    from datetime import datetime
    import search_utils

    query = args.search
    agents = [args.agent] if args.agent else ["claude", "codex", "gemini", "aider", "cursor"]

    options = {
        "case_sensitive": args.case_sensitive,
        "regex": args.regex,
        "max_sessions": 100,
        "max_matches_per_session": 5,
    }
    if args.search_scope:
        options["scope"] = args.search_scope.split(",")

    print(f"\n  Searching for: \"{query}\"  (agents: {', '.join(agents)})\n")

    results, stats = search_utils.search_across_sessions(agents, query, options)

    if not results:
        print("  No matches found.")
        print(f"  ({stats.get('sessions_scanned', 0)} sessions scanned in {stats.get('elapsed_ms', 0)}ms)")
        print()
        return

    # Print results table
    print(f"  {'#':>3}  {'Date':16}  {'Agent':8}  {'Project':16}  {'Matches':>7}  First match")
    print(f"  {'─' * 3}  {'─' * 16}  {'─' * 8}  {'─' * 16}  {'─' * 7}  {'─' * 50}")

    for i, result in enumerate(results):
        idx = i + 1
        session = result["session"]
        agent = result["agent"]
        match_count = result["match_count"]

        # Format date
        mtime = session.get("mtime", 0)
        if mtime:
            date_display = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        else:
            date_display = ""

        project = session.get("project", "")
        if len(project) > 16:
            project = project[:14] + ".."

        # First match excerpt with highlighted query
        first_match = result["matches"][0] if result["matches"] else None
        if first_match:
            excerpt = first_match["excerpt"].replace("\n", " ")
            if len(excerpt) > 50:
                excerpt = excerpt[:48] + ".."
        else:
            excerpt = ""

        print(f"  {idx:>3}  {date_display:16}  {agent:8}  {project:16}  {match_count:>7}  {excerpt}")

    print()
    print(f"  {stats['sessions_matched']} sessions matched / "
          f"{stats['sessions_scanned']} scanned in {stats['elapsed_ms']}ms")
    print()


def _stats_main(args):
    """Stats mode."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("session_stats", str(Path(__file__).parent / "session-stats.py"))
    stats_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stats_mod)

    if args.overview:
        agents = [args.agent] if args.agent else ["claude", "codex", "gemini", "aider", "cursor"]
        overview = stats_mod.compute_overview_stats(agents)
        stats_mod._print_overview_stats(overview)
    else:
        agent = args.agent or "claude"
        if args.input:
            path = args.input
        else:
            adapter = stats_mod._get_adapter(agent)
            sessions = adapter.discover_sessions()
            path = adapter.select_session(sessions)

        model = stats_mod._build_common_model(path, agent)
        stats = stats_mod.compute_session_stats(model)

        if args.output and args.output.endswith(".html"):
            html = stats_mod.render_stats_html(stats)
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"Stats HTML written to {args.output}")
        else:
            stats_mod._print_session_stats(stats)


def _diff_main(args):
    """Diff mode."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("session_stats", str(Path(__file__).parent / "session-stats.py"))
    stats_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stats_mod)

    agent = args.agent or "claude"
    model_a = stats_mod._build_common_model(args.diff[0], agent)
    model_b = stats_mod._build_common_model(args.diff[1], agent)
    diff = stats_mod.compute_diff(model_a, model_b)

    if args.output and args.output.endswith(".html"):
        html = stats_mod.render_diff_html(diff)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"Diff HTML written to {args.output}")
    else:
        stats_mod._print_diff(diff)


def _stream_main(args):
    """Stream/follow mode: delegate to log-replay-stream.py."""
    cmd = [sys.executable, str(Path(__file__).parent / "log-replay-stream.py")]
    if args.agent:
        cmd += ["--agent", args.agent]
    if args.input:
        cmd.append(args.input)
    else:
        cmd.append("--session")
    cmd.append("-f")
    cmd += ["--poll-interval", str(args.poll_interval)]
    # For streaming, default to terminal output (not md which is the replay default)
    # Only use markdown if user explicitly requested it via --format
    fmt_is_explicit = any(a in sys.argv for a in ("-f", "--format"))
    # Check if -f was used as --follow (short flag collision); use --format presence
    if "--format" in sys.argv or (fmt_is_explicit and args.format == "terminal"):
        stream_fmt = "markdown" if args.format in ("md", "markdown") else "terminal"
    else:
        stream_fmt = "terminal"
    cmd += ["--format", stream_fmt]
    res = _run(cmd)
    raise SystemExit(res.returncode)


def _tui_main():
    """TUI mode (interactive)."""
    from log_replay_tui import LogReplayApp
    app = LogReplayApp()
    app.run()


def main():
    # If no arguments provided, launch TUI
    if len(sys.argv) == 1:
        _tui_main()
        return

    # Otherwise, use CLI mode
    parser = argparse.ArgumentParser(description="Replay Claude/Codex/Gemini logs via common model")
    parser.add_argument("--agent", choices=["claude", "codex", "gemini", "aider", "cursor"], default=None,
                        help="log agent type (required for replay, optional for search)")
    parser.add_argument("input", nargs="?", default=None, help="input JSONL file path (omit to select)")
    parser.add_argument("-o", "--output", help="output file path")
    parser.add_argument("-f", "--format", choices=["md", "html", "player", "terminal", "pdf", "gif"], default="md",
                        help="output format: md, html, player, terminal, pdf, or gif")
    parser.add_argument("-t", "--theme", choices=["light", "console"], default="light",
                        help="HTML theme: light (default) or console (dark)")
    parser.add_argument("--model", help="write model JSON to this path")
    parser.add_argument("--project", help="(claude/gemini) filter sessions by project name")
    parser.add_argument("--filter", help="(codex) filter sessions by path substring")
    parser.add_argument("--log-arg", action="append", default=[], help="extra args for log2model (repeatable)")
    parser.add_argument("--render-arg", action="append", default=[], help="extra args for renderer (repeatable)")

    # Search options
    parser.add_argument("--search", help="search query (enables search mode)")
    parser.add_argument("--search-scope", default=None,
                        help="comma-separated fields to search: text,thinking,tool_use,tool_result (default: all)")
    parser.add_argument("--case-sensitive", action="store_true", help="case-sensitive search")
    parser.add_argument("--regex", action="store_true", help="treat search query as regex")

    # Streaming options
    parser.add_argument("--follow", "-F", action="store_true",
                        help="stream/follow a session in real-time (like tail -f)")
    parser.add_argument("--stream", action="store_true",
                        help="alias for --follow")
    parser.add_argument("--poll-interval", type=int, default=500,
                        help="poll interval in ms for --follow mode (default: 500)")

    # Stats & Diff options
    parser.add_argument("--stats", action="store_true", help="show session statistics")
    parser.add_argument("--overview", action="store_true", help="cross-session overview (with --stats)")
    parser.add_argument("--diff", nargs=2, metavar=("SESSION_A", "SESSION_B"),
                        help="compare two sessions side-by-side")

    args = parser.parse_args()

    if args.search:
        _search_main(args)
    elif args.follow or args.stream:
        _stream_main(args)
    elif args.stats or args.overview:
        _stats_main(args)
    elif args.diff:
        _diff_main(args)
    else:
        if not args.agent:
            parser.error("--agent is required for replay mode (or use --search/--stats/--diff)")
        _cli_main(args)


if __name__ == "__main__":
    main()
