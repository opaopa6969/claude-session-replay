#!/usr/bin/env python3
"""Render Claude/Codex logs to MP4 by recording the HTML player in a headless browser.

Requires:
  - playwright (python)
  - browsers installed via `python -m playwright install`
  - ffmpeg in PATH
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(cmd, check=True):
    return subprocess.run(cmd, check=check)


def _ensure_deps():
    try:
        import playwright  # noqa: F401
    except Exception:
        print("playwright is not installed. Install with: pip install playwright", file=sys.stderr)
        raise SystemExit(1)

    if _run(["ffmpeg", "-version"], check=False).returncode != 0:
        print("ffmpeg not found in PATH. Install ffmpeg and retry.", file=sys.stderr)
        raise SystemExit(1)


def _record_with_playwright(html_path, out_mp4, width, height, fps, speed, fmt, theme, timeout_s):
    # Import locally to avoid hard dependency at module import time
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(prefix="log-replay-video-") as tmpdir:
        record_dir = Path(tmpdir) / "record"
        record_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"]
            )
            context = browser.new_context(
                viewport={"width": width, "height": height},
                record_video_dir=str(record_dir),
                record_video_size={"width": width, "height": height},
            )
            page = context.new_page()
            page.goto(f"file://{html_path}")

            if fmt == "terminal":
                play_btn = "#t-play"
                speed_id = "#t-speed"
                wait_sel = ".t-msg"
                last_sel = ".t-msg:last-of-type"
            else:
                play_btn = "#btnPlay"
                speed_id = "#speed"
                wait_sel = ".message"
                last_sel = ".message:last-of-type"

            page.wait_for_selector(wait_sel)

            # Set speed
            page.eval_on_selector(speed_id, "(el, v) => { el.value = v; el.dispatchEvent(new Event('input', {bubbles:true})); }", str(speed))

            # Start playback
            page.click(play_btn)

            def is_done():
                btn_text = page.eval_on_selector(play_btn, "el => el.textContent")
                last = page.eval_on_selector(last_sel, "el => getComputedStyle(el).display")
                return (btn_text or "").strip() == "play" and last != "none"

            # Wait until playback completes or timeout
            elapsed = 0.0
            step = 0.5
            while elapsed < timeout_s:
                if is_done():
                    break
                page.wait_for_timeout(int(step * 1000))
                elapsed += step

            # Close to flush video
            context.close()
            browser.close()

        # Find latest webm
        webms = sorted(record_dir.glob("*.webm"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not webms:
            print("No recorded video found.", file=sys.stderr)
            raise SystemExit(1)

        webm_path = webms[0]
        out_mp4.parent.mkdir(parents=True, exist_ok=True)
        _run([
            "ffmpeg", "-y", "-i", str(webm_path),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps), str(out_mp4)
        ])


def main():
    parser = argparse.ArgumentParser(description="Record log replay to MP4")
    parser.add_argument("--agent", choices=["claude", "codex"], required=True, help="log agent type")
    parser.add_argument("input", nargs="?", default=None, help="input JSONL file path (omit to select)")
    parser.add_argument("-o", "--output", help="output mp4 path")
    parser.add_argument("-f", "--format", choices=["player", "terminal"], default="player", help="render format")
    parser.add_argument("-t", "--theme", choices=["light", "console"], default="console", help="theme")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=600, help="max seconds to wait")
    parser.add_argument("--project", help="(claude) filter sessions by project name")
    parser.add_argument("--filter", help="(codex) filter sessions by path substring")
    args = parser.parse_args()

    _ensure_deps()

    # Create HTML via existing pipeline
    with tempfile.TemporaryDirectory(prefix="log-replay-html-") as tmpdir:
        html_path = Path(tmpdir) / "replay.html"
        cmd = [sys.executable, "log-replay.py", "--agent", args.agent]
        if args.input:
            cmd.append(args.input)
        if args.project:
            cmd += ["--log-arg", "--project", "--log-arg", args.project]
        if args.filter:
            cmd += ["--log-arg", "--filter", "--log-arg", args.filter]
        cmd += ["-f", args.format, "-t", args.theme, "-o", str(html_path)]
        _run(cmd)

        out_mp4 = Path(args.output) if args.output else Path(os.path.splitext(args.input or "session")[0] + ".mp4")
        _record_with_playwright(str(html_path), out_mp4, args.width, args.height, args.fps, args.speed, args.format, args.theme, args.timeout)
        print(f"Wrote {out_mp4}")


if __name__ == "__main__":
    main()
