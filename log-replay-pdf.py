#!/usr/bin/env python3
"""Render Claude/Codex/Gemini logs to PDF by rendering HTML in a headless browser.

Requires:
  - playwright (python)
  - browsers installed via `python -m playwright install`
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


def _render_pdf(html_path, out_pdf, page_size, margin, header, footer, landscape):
    """Render HTML file to PDF using Playwright."""
    from playwright.sync_api import sync_playwright

    margin_val = "{0}mm".format(margin)
    pdf_options = {
        "path": str(out_pdf),
        "format": page_size,
        "margin": {
            "top": margin_val,
            "right": margin_val,
            "bottom": margin_val,
            "left": margin_val,
        },
        "print_background": True,
        "landscape": landscape,
    }

    if header:
        pdf_options["display_header_footer"] = True
        pdf_options["header_template"] = (
            '<div style="font-size:9px;width:100%;text-align:center;">'
            '{0}</div>'.format(header)
        )
    if footer:
        pdf_options["display_header_footer"] = True
        pdf_options["footer_template"] = (
            '<div style="font-size:9px;width:100%;text-align:center;">'
            '{0}</div>'.format(footer)
        )

    # If header/footer enabled but one not set, provide empty template for the other
    if pdf_options.get("display_header_footer"):
        if "header_template" not in pdf_options:
            pdf_options["header_template"] = '<div></div>'
        if "footer_template" not in pdf_options:
            pdf_options["footer_template"] = '<div></div>'

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            chromium_sandbox=False,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        page = browser.new_page()
        page.goto("file://{0}".format(html_path))
        # Wait for content to load
        page.wait_for_load_state("networkidle")

        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        page.pdf(**pdf_options)

        browser.close()


def main():
    parser = argparse.ArgumentParser(description="Render log replay to PDF")
    parser.add_argument("--agent", choices=["claude", "codex", "gemini", "aider", "cursor"],
                        required=True, help="log agent type")
    parser.add_argument("input", nargs="?", default=None, help="input JSONL file path (omit to select)")
    parser.add_argument("-o", "--output", help="output PDF path")
    parser.add_argument("-f", "--format", choices=["html", "player", "terminal"], default="html",
                        help="render format for source HTML (default: html)")
    parser.add_argument("-t", "--theme", choices=["light", "console"], default="light", help="theme")
    parser.add_argument("--page-size", default="A4",
                        help="PDF page size: A4, Letter, A3, etc. (default: A4)")
    parser.add_argument("--margin", type=int, default=15,
                        help="page margin in mm (default: 15)")
    parser.add_argument("--header", default=None, help="header HTML text")
    parser.add_argument("--footer", default=None, help="footer HTML text")
    parser.add_argument("--landscape", action="store_true", help="landscape orientation")
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

        out_pdf = Path(args.output) if args.output else Path(os.path.splitext(args.input or "session")[0] + ".pdf")
        _render_pdf(str(html_path), out_pdf, args.page_size, args.margin,
                    args.header, args.footer, args.landscape)
        print("Wrote {0}".format(out_pdf))


if __name__ == "__main__":
    main()
