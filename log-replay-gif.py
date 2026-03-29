#!/usr/bin/env python3
"""Render Claude/Codex/Gemini logs to animated GIF by capturing screenshots during playback.

Requires:
  - playwright (python)
  - browsers installed via `python -m playwright install`
  - Pillow (PIL) for GIF assembly, OR ffmpeg in PATH as fallback
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(cmd, check=True):
    return subprocess.run(cmd, check=check)


def _has_pillow():
    try:
        from PIL import Image  # noqa: F401
        return True
    except Exception:
        return False


def _has_ffmpeg():
    try:
        return subprocess.run(["ffmpeg", "-version"], check=False,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except Exception:
        return False


def _ensure_deps():
    try:
        import playwright  # noqa: F401
    except Exception:
        print("playwright is not installed. Install with: pip install playwright", file=sys.stderr)
        raise SystemExit(1)

    if not _has_pillow() and not _has_ffmpeg():
        print("Neither Pillow nor ffmpeg found. Install one of:", file=sys.stderr)
        print("  pip install Pillow", file=sys.stderr)
        print("  OR install ffmpeg in PATH", file=sys.stderr)
        raise SystemExit(1)


def _capture_frames(html_path, frame_dir, width, height, fps, speed, fmt, timeout_s):
    """Capture screenshots during playback and return list of frame paths."""
    from playwright.sync_api import sync_playwright

    frame_interval = 1.0 / fps
    frames = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            chromium_sandbox=False,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto("file://{0}".format(html_path))

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
        page.eval_on_selector(
            speed_id,
            "(el, v) => { el.value = v; el.dispatchEvent(new Event('input', {bubbles:true})); }",
            str(speed),
        )

        # Start playback
        page.click(play_btn)

        def is_done():
            btn_text = page.eval_on_selector(play_btn, "el => el.textContent")
            last = page.eval_on_selector(last_sel, "el => getComputedStyle(el).display")
            return (btn_text or "").strip() == "play" and last != "none"

        # Capture frames
        elapsed = 0.0
        frame_num = 0
        while elapsed < timeout_s:
            frame_path = os.path.join(frame_dir, "frame_{0:06d}.png".format(frame_num))
            page.screenshot(path=frame_path)
            frames.append(frame_path)
            frame_num += 1

            if is_done():
                # Capture a few extra frames at the end so the final state is visible
                for _ in range(max(1, fps)):
                    frame_path = os.path.join(frame_dir, "frame_{0:06d}.png".format(frame_num))
                    page.screenshot(path=frame_path)
                    frames.append(frame_path)
                    frame_num += 1
                break

            page.wait_for_timeout(int(frame_interval * 1000))
            elapsed += frame_interval

        browser.close()

    return frames


def _assemble_gif_pillow(frames, out_gif, fps):
    """Assemble frames into GIF using Pillow."""
    from PIL import Image

    if not frames:
        print("No frames captured.", file=sys.stderr)
        raise SystemExit(1)

    images = []
    for f in frames:
        img = Image.open(f)
        # Convert to palette mode for smaller GIF
        img = img.convert("RGBA").convert("P", palette=Image.ADAPTIVE, colors=256)
        images.append(img)

    duration_ms = int(1000.0 / fps)
    out_gif.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(
        str(out_gif),
        save_all=True,
        append_images=images[1:],
        duration=duration_ms,
        loop=0,
        optimize=True,
    )


def _assemble_gif_ffmpeg(frames, out_gif, fps, width):
    """Assemble frames into GIF using ffmpeg with palette generation."""
    if not frames:
        print("No frames captured.", file=sys.stderr)
        raise SystemExit(1)

    frame_dir = os.path.dirname(frames[0])
    pattern = os.path.join(frame_dir, "frame_%06d.png")
    palette_path = os.path.join(frame_dir, "palette.png")

    out_gif.parent.mkdir(parents=True, exist_ok=True)

    # Generate palette for better quality
    _run([
        "ffmpeg", "-y", "-framerate", str(fps),
        "-i", pattern,
        "-vf", "fps={0},scale={1}:-1:flags=lanczos,palettegen".format(fps, width),
        palette_path,
    ])

    # Generate GIF using palette
    _run([
        "ffmpeg", "-y", "-framerate", str(fps),
        "-i", pattern,
        "-i", palette_path,
        "-lavfi", "fps={0},scale={1}:-1:flags=lanczos[x];[x][1:v]paletteuse".format(fps, width),
        str(out_gif),
    ])


def main():
    parser = argparse.ArgumentParser(description="Record log replay to animated GIF")
    parser.add_argument("--agent", choices=["claude", "codex", "gemini", "aider", "cursor"],
                        required=True, help="log agent type")
    parser.add_argument("input", nargs="?", default=None, help="input JSONL file path (omit to select)")
    parser.add_argument("-o", "--output", help="output GIF path")
    parser.add_argument("-f", "--format", choices=["player", "terminal"], default="player",
                        help="render format (default: player)")
    parser.add_argument("-t", "--theme", choices=["light", "console"], default="console", help="theme")
    parser.add_argument("--width", type=int, default=800, help="viewport width (default: 800)")
    parser.add_argument("--height", type=int, default=600, help="viewport height (default: 600)")
    parser.add_argument("--fps", type=int, default=4, help="frames per second (default: 4)")
    parser.add_argument("--speed", type=float, default=2.0, help="playback speed (default: 2.0)")
    parser.add_argument("--timeout", type=float, default=300, help="max seconds to wait (default: 300)")
    parser.add_argument("--project", help="(claude/gemini) filter sessions by project name")
    parser.add_argument("--filter", help="(codex) filter sessions by path substring")
    parser.add_argument("-r", "--range", dest="range_spec",
                        help="message range like '1-50,53-' (1-based, comma-separated)")
    parser.add_argument("--log-arg", action="append", default=[], help="extra args for log2model (repeatable)")
    parser.add_argument("--render-arg", action="append", default=[], help="extra args for renderer (repeatable)")
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
        if args.range_spec:
            cmd += ["--render-arg=--range", "--render-arg", args.range_spec]
        for extra in args.log_arg:
            if extra.startswith("-"):
                cmd.append("--log-arg={0}".format(extra))
            else:
                cmd += ["--log-arg", extra]
        for extra in args.render_arg:
            if extra.startswith("-"):
                cmd.append("--render-arg={0}".format(extra))
            else:
                cmd += ["--render-arg", extra]
        _run(cmd)

        # Capture frames
        with tempfile.TemporaryDirectory(prefix="log-replay-frames-") as frame_dir:
            frames = _capture_frames(
                str(html_path), frame_dir,
                args.width, args.height, args.fps, args.speed, args.format, args.timeout,
            )

            out_gif = Path(args.output) if args.output else Path(
                os.path.splitext(args.input or "session")[0] + ".gif"
            )

            if _has_pillow():
                _assemble_gif_pillow(frames, out_gif, args.fps)
            else:
                _assemble_gif_ffmpeg(frames, out_gif, args.fps, args.width)

            print("Wrote {0}".format(out_gif))


if __name__ == "__main__":
    main()
