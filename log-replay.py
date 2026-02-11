#!/usr/bin/env python3
"""Wrapper: agent log -> common model -> renderer."""

import argparse
import os
import subprocess
import sys
import tempfile


def _run(cmd):
    return subprocess.run(cmd, check=False)


def main():
    parser = argparse.ArgumentParser(description="Replay Claude/Codex logs via common model")
    parser.add_argument("--agent", choices=["claude", "codex"], required=True, help="log agent type")
    parser.add_argument("input", nargs="?", default=None, help="input JSONL file path (omit to select)")
    parser.add_argument("-o", "--output", help="output file path")
    parser.add_argument("-f", "--format", choices=["md", "html", "player", "terminal"], default="md",
                        help="output format: md, html, player, or terminal")
    parser.add_argument("-t", "--theme", choices=["light", "console"], default="light",
                        help="HTML theme: light (default) or console (dark)")
    parser.add_argument("--model", help="write model JSON to this path")
    parser.add_argument("--project", help="(claude) filter sessions by project name")
    parser.add_argument("--filter", help="(codex) filter sessions by path substring")
    parser.add_argument("--log-arg", action="append", default=[], help="extra args for log2model (repeatable)")
    parser.add_argument("--render-arg", action="append", default=[], help="extra args for renderer (repeatable)")
    args = parser.parse_args()

    if args.agent == "claude":
        log2model = "claude-log2model.py"
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


if __name__ == "__main__":
    main()
