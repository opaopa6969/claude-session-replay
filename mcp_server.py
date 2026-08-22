#!/usr/bin/env python3
"""claude-session-replay MCP server (Streamable HTTP).

Exposes session-log conversion, search, stats, and rendering as MCP tools.
Independent process from the Flask web_ui.py (port 5000).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

MCP_VERSION = "0.1.0"
MCP_HOST = "0.0.0.0"
MCP_PORT = int(os.environ.get("PORT", "9241"))
MCP_PATH = "/mcp"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ALLOW = ("127.0.0.1", "192.168.1.50", "192.168.1.8")

AGENTS = ("claude", "codex", "gemini", "aider", "cursor")
RENDER_FORMATS = ("md", "html", "player", "terminal")
MEDIA_TYPES = ("mp4", "pdf", "gif")

_jobs: dict[str, dict[str, Any]] = {}
_job_lock = threading.Lock()


def _import_module(name: str, filepath: str):
    spec = importlib.util.spec_from_file_location(name, filepath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_adapters: dict[str, Any] = {}


def _get_adapter(agent: str):
    if agent not in _adapters:
        mapping = {
            "claude": "claude-log2model.py",
            "codex": "codex-log2model.py",
            "gemini": "gemini-log2model.py",
            "aider": "aider-log2model.py",
            "cursor": "cursor-log2model.py",
        }
        if agent not in mapping:
            raise ValueError(f"Unknown agent: {agent}")
        _adapters[agent] = _import_module(
            f"{agent}_log2model", str(SCRIPT_DIR / mapping[agent])
        )
    return _adapters[agent]


_search_mod = None


def _get_search_mod():
    global _search_mod
    if _search_mod is None:
        _search_mod = _import_module("search_utils", str(SCRIPT_DIR / "search_utils.py"))
    return _search_mod


_stats_mod = None


def _get_stats_mod():
    global _stats_mod
    if _stats_mod is None:
        _stats_mod = _import_module("session_stats", str(SCRIPT_DIR / "session-stats.py"))
    return _stats_mod


mcp = MCPServer(
    name="session-replay",
    version=MCP_VERSION,
    description=(
        "AI コーディングエージェント（Claude Code / Codex CLI / Gemini CLI / Aider / Cursor）"
        "のセッションログを共通モデルに正規化し、Markdown / HTML / Player / Terminal / "
        "MP4 / PDF / GIF に変換・検索・統計するツールチェーン。"
    ),
)


def _allowed_ips() -> set[str]:
    raw = os.environ.get("SESSION_REPLAY_MCP_ALLOW")
    if raw:
        return {item.strip() for item in raw.split(",") if item.strip()}
    return set(DEFAULT_ALLOW)


class IPAllowlistMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/healthz" and request.method == "GET":
            return JSONResponse(
                {"ok": True, "name": "session-replay", "version": MCP_VERSION},
                headers={"content-encoding": "identity"},
            )
        peer = request.client.host if request.client else ""
        if peer not in _allowed_ips():
            return PlainTextResponse("forbidden", status_code=403)
        response = await call_next(request)
        response.headers["content-encoding"] = "identity"
        return response


def create_app():
    app = mcp.streamable_http_app(
        streamable_http_path=MCP_PATH, host=MCP_HOST, stateless_http=False
    )
    app.add_middleware(IPAllowlistMiddleware)
    return app


def _validate_agent(agent: str) -> str:
    if agent not in AGENTS:
        raise ValueError(f"agent must be one of {AGENTS}, got: {agent}")
    return agent


def _validate_session_path(session_path: str) -> str:
    if not session_path or not os.path.isfile(session_path):
        raise ValueError(f"session_path does not exist: {session_path}")
    return session_path


def _build_model(agent: str, session_path: str) -> dict:
    adapter = _get_adapter(agent)
    if agent == "claude":
        messages = adapter.parse_messages(session_path)
        return adapter.build_model(messages, session_path)
    elif agent == "gemini":
        with open(session_path, "r", encoding="utf-8") as f:
            session_data = json.load(f)
        return adapter.build_model(session_data, session_path)
    else:
        return adapter.build_model(session_path)


# ---------------------------------------------------------------------------
# Tools — synchronous (read-only, <30s)
# ---------------------------------------------------------------------------


@mcp.tool(
    description="指定 agent のセッションログ一覧を取得する（read）。"
    "agent: claude|codex|gemini|aider|cursor。"
    "戻り値: {sessions: [{path, project, size, mtime, preview}]}",
    annotations=ToolAnnotations(read_only_hint=True),
)
def list_sessions(agent: str) -> dict[str, Any]:
    _validate_agent(agent)
    adapter = _get_adapter(agent)
    sessions = adapter.discover_sessions()
    result = []
    for s in sessions:
        path = s.get("path", "")
        preview = {}
        try:
            if agent == "claude":
                preview = adapter._extract_preview(path)
            elif agent == "codex":
                use_event = adapter._codex_has_event_messages(path)
                preview = adapter._extract_preview(path, use_event)
            else:
                preview = adapter._extract_preview(path)
        except Exception:
            pass
        result.append({
            "path": path,
            "project": s.get("project", ""),
            "size": s.get("size", 0),
            "mtime": s.get("mtime", 0),
            "preview": preview,
        })
    return {"sessions": result, "count": len(result)}


@mcp.tool(
    description="セッションログを共通モデル JSON に変換する（read）。"
    "agent + session_path → {source, agent, messages[]}。"
    "5 つのアダプター（claude/codex/gemini/aider/cursor）に対応。",
    annotations=ToolAnnotations(read_only_hint=True),
)
def to_model(agent: str, session_path: str) -> dict[str, Any]:
    _validate_agent(agent)
    _validate_session_path(session_path)
    return _build_model(agent, session_path)


@mcp.tool(
    description="セッションを指定フォーマットにレンダリングする（read）。"
    "format: md|html|player|terminal。theme: light|dark（省略可）。"
    "range: メッセージ範囲指定（省略可）。戻り値: {format, content}",
    annotations=ToolAnnotations(read_only_hint=True),
)
def render(
    agent: str,
    session_path: str,
    format: str,
    theme: str | None = None,
    range: str | None = None,
) -> dict[str, Any]:
    _validate_agent(agent)
    _validate_session_path(session_path)
    if format not in RENDER_FORMATS:
        raise ValueError(f"format must be one of {RENDER_FORMATS}, got: {format}")

    fd, model_path = tempfile.mkstemp(prefix="log-model-", suffix=".json")
    os.close(fd)
    try:
        model = _build_model(agent, session_path)
        with open(model_path, "w", encoding="utf-8") as f:
            json.dump(model, f, ensure_ascii=False)

        fd2, out_path = tempfile.mkstemp(suffix=f".{format}")
        os.close(fd2)
        try:
            cmd = [
                sys.executable, str(SCRIPT_DIR / "log-model-renderer.py"),
                model_path, "-f", format, "-t", theme or "light", "-o", out_path,
            ]
            if range:
                cmd.extend(["-r", range])
            cmd.extend(["--truncate", "0"])
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                raise RuntimeError(result.stderr or "rendering failed")
            with open(out_path, "r", encoding="utf-8") as f:
                content = f.read()
        finally:
            try:
                os.remove(out_path)
            except OSError:
                pass
    finally:
        try:
            os.remove(model_path)
        except OSError:
            pass

    return {"format": format, "content": content}


@mcp.tool(
    description="1セッション内のメッセージを検索する（read）。"
    "query で検索。scope: text|thinking|tool_use|tool_result（省略時すべて）。"
    "case_sensitive, regex で検索モード指定可。戻り値: {matches[], total_matches}",
    annotations=ToolAnnotations(read_only_hint=True),
)
def search_session(
    agent: str,
    session_path: str,
    query: str,
    scope: list[str] | None = None,
    case_sensitive: bool = False,
    regex: bool = False,
    max_matches: int = 200,
) -> dict[str, Any]:
    _validate_agent(agent)
    _validate_session_path(session_path)
    if not query:
        return {"matches": [], "total_matches": 0}
    mod = _get_search_mod()
    options = {
        "case_sensitive": case_sensitive,
        "regex": regex,
        "max_matches": max_matches,
    }
    if scope:
        options["scope"] = scope
    matches = mod.search_session_messages(session_path, agent, query, options)
    return {"matches": matches, "total_matches": len(matches)}


@mcp.tool(
    description="1セッションの統計情報を取得する（read）。"
    "メッセージ数・ツール使用回数・所要時間等。戻り値: stats JSON",
    annotations=ToolAnnotations(read_only_hint=True),
)
def stats_session(agent: str, session_path: str) -> dict[str, Any]:
    _validate_agent(agent)
    _validate_session_path(session_path)
    mod = _get_stats_mod()
    model = mod._build_common_model(session_path, agent)
    return mod.compute_session_stats(model)


@mcp.tool(
    description="2つのセッションを比較する（read）。"
    "agent + session_a + session_b → {message_diff, tool_diff, ...}",
    annotations=ToolAnnotations(read_only_hint=True),
)
def diff_sessions(agent: str, session_a: str, session_b: str) -> dict[str, Any]:
    _validate_agent(agent)
    _validate_session_path(session_a)
    _validate_session_path(session_b)
    mod = _get_stats_mod()
    model_a = mod._build_common_model(session_a, agent)
    model_b = mod._build_common_model(session_b, agent)
    diff = mod.compute_diff(model_a, model_b)
    diff["messages_a"] = [
        {"role": m.get("role"), "text": m.get("text", "")[:100],
         "tools": len(m.get("tool_uses", []))}
        for m in diff.get("messages_a", [])[:200]
    ]
    diff["messages_b"] = [
        {"role": m.get("role"), "text": m.get("text", "")[:100],
         "tools": len(m.get("tool_uses", []))}
        for m in diff.get("messages_b", [])[:200]
    ]
    return diff


# ---------------------------------------------------------------------------
# Tools — job type (30s+, _start → _status → _result)
# ---------------------------------------------------------------------------


def _new_job(kind: str, params: dict) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _job_lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "kind": kind,
            "state": "pending",
            "params": params,
            "progress": {},
            "result": None,
            "error": None,
            "created_at": time.time(),
        }
    return job_id


def _run_media_job(job_id: str, agent: str, session_path: str,
                   media_type: str, width: int, height: int,
                   fps: int, speed: float):
    try:
        with _job_lock:
            if job_id in _jobs:
                _jobs[job_id]["state"] = "running"

        script_map = {
            "mp4": "log-replay-mp4.py",
            "pdf": "log-replay-pdf.py",
            "gif": "log-replay-gif.py",
        }
        script = SCRIPT_DIR / script_map[media_type]
        out_dir = Path(tempfile.mkdtemp(prefix="session-replay-job-"))
        out_file = out_dir / f"output.{media_type}"

        cmd = [sys.executable, str(script), "--agent", agent, session_path,
               "-o", str(out_file)]
        if media_type == "mp4":
            cmd += ["--width", str(width), "--height", str(height),
                    "--fps", str(fps), "--speed", str(speed)]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            with _job_lock:
                if job_id in _jobs:
                    _jobs[job_id]["state"] = "error"
                    _jobs[job_id]["error"] = result.stderr or "rendering failed"
            return

        with _job_lock:
            if job_id in _jobs:
                _jobs[job_id]["state"] = "done"
                _jobs[job_id]["result"] = {"output_path": str(out_file)}
    except Exception as e:
        with _job_lock:
            if job_id in _jobs:
                _jobs[job_id]["state"] = "error"
                _jobs[job_id]["error"] = str(e)


@mcp.tool(
    description="MP4/PDF/GIF レンダリングジョブを開始する（job 型・30秒超）。"
    "media_type: mp4|pdf|gif。Playwright 必要。"
    "戻り値: {job_id, state, hint}。render_media_status で進捗確認。",
    annotations=ToolAnnotations(read_only_hint=True),
)
def render_media_start(
    agent: str,
    session_path: str,
    media_type: str,
    width: int = 1280,
    height: int = 720,
    fps: int = 10,
    speed: float = 1.0,
) -> dict[str, Any]:
    _validate_agent(agent)
    _validate_session_path(session_path)
    if media_type not in MEDIA_TYPES:
        raise ValueError(f"media_type must be one of {MEDIA_TYPES}, got: {media_type}")

    job_id = _new_job("render_media", {
        "agent": agent, "session_path": session_path,
        "media_type": media_type,
    })
    t = threading.Thread(
        target=_run_media_job,
        args=(job_id, agent, session_path, media_type, width, height, fps, speed),
        daemon=True,
    )
    t.start()
    return {"job_id": job_id, "state": "queued",
            "hint": "render_media_status で進捗、render_media_result で成果物"}


@mcp.tool(
    description="メディアレンダリングジョブの状態・進捗を取得する（read）。",
    annotations=ToolAnnotations(read_only_hint=True),
)
def render_media_status(job_id: str) -> dict[str, Any]:
    with _job_lock:
        job = _jobs.get(job_id)
    if not job:
        return {"error": "job not found", "job_id": job_id}
    return {"job_id": job_id, "state": job["state"], "progress": job.get("progress", {}),
            "error": job.get("error")}


@mcp.tool(
    description="メディアレンダリングジョブの成果物パスを取得する（read）。"
    "state=done で output_path が返る。",
    annotations=ToolAnnotations(read_only_hint=True),
)
def render_media_result(job_id: str) -> dict[str, Any]:
    with _job_lock:
        job = _jobs.get(job_id)
    if not job:
        return {"error": "job not found", "job_id": job_id}
    if job["state"] != "done":
        return {"job_id": job_id, "state": job["state"], "error": job.get("error")}
    return {"job_id": job_id, "state": "done", **job.get("result", {})}


def _run_search_cross_job(job_id: str, agents: list[str], query: str, options: dict):
    try:
        with _job_lock:
            if job_id in _jobs:
                _jobs[job_id]["state"] = "running"

        mod = _get_search_mod()
        results, stats = mod.search_across_sessions(agents, query, options)
        with _job_lock:
            if job_id in _jobs:
                _jobs[job_id]["state"] = "done"
                _jobs[job_id]["result"] = {"results": results, "stats": stats}
    except Exception as e:
        with _job_lock:
            if job_id in _jobs:
                _jobs[job_id]["state"] = "error"
                _jobs[job_id]["error"] = str(e)


@mcp.tool(
    description="全セッション横断検索ジョブを開始する（job 型・30秒超）。"
    "agents: 検索対象 agent リスト。query: 検索文字列。"
    "戻り値: {job_id, state, hint}。search_cross_status で進捗確認。",
    annotations=ToolAnnotations(read_only_hint=True),
)
def search_cross_start(
    agents: list[str],
    query: str,
    scope: list[str] | None = None,
    case_sensitive: bool = False,
    regex: bool = False,
    max_sessions: int = 100,
    max_matches_per_session: int = 5,
) -> dict[str, Any]:
    for a in agents:
        _validate_agent(a)
    if not query:
        raise ValueError("query is required")
    options = {
        "case_sensitive": case_sensitive,
        "regex": regex,
        "max_sessions": max_sessions,
        "max_matches_per_session": max_matches_per_session,
    }
    if scope:
        options["scope"] = scope
    job_id = _new_job("search_cross", {"agents": agents, "query": query})
    t = threading.Thread(
        target=_run_search_cross_job,
        args=(job_id, agents, query, options),
        daemon=True,
    )
    t.start()
    return {"job_id": job_id, "state": "queued",
            "hint": "search_cross_status で進捗、search_cross_result で結果"}


@mcp.tool(
    description="横断検索ジョブの状態を取得する（read）。",
    annotations=ToolAnnotations(read_only_hint=True),
)
def search_cross_status(job_id: str) -> dict[str, Any]:
    with _job_lock:
        job = _jobs.get(job_id)
    if not job:
        return {"error": "job not found", "job_id": job_id}
    return {"job_id": job_id, "state": job["state"], "error": job.get("error")}


@mcp.tool(
    description="横断検索ジョブの結果を取得する（read）。"
    "state=done で results と stats が返る。",
    annotations=ToolAnnotations(read_only_hint=True),
)
def search_cross_result(job_id: str) -> dict[str, Any]:
    with _job_lock:
        job = _jobs.get(job_id)
    if not job:
        return {"error": "job not found", "job_id": job_id}
    if job["state"] != "done":
        return {"job_id": job_id, "state": job["state"], "error": job.get("error")}
    return {"job_id": job_id, "state": "done", **job.get("result", {})}


def _run_stats_overview_job(job_id: str, agents: list[str]):
    try:
        with _job_lock:
            if job_id in _jobs:
                _jobs[job_id]["state"] = "running"

        mod = _get_stats_mod()
        overview = mod.compute_overview_stats(agents)
        with _job_lock:
            if job_id in _jobs:
                _jobs[job_id]["state"] = "done"
                _jobs[job_id]["result"] = {"overview": overview}
    except Exception as e:
        with _job_lock:
            if job_id in _jobs:
                _jobs[job_id]["state"] = "error"
                _jobs[job_id]["error"] = str(e)


@mcp.tool(
    description="全セッションの統計概要ジョブを開始する（job 型・30秒超）。"
    "agents: 対象 agent リスト。戻り値: {job_id, state, hint}。",
    annotations=ToolAnnotations(read_only_hint=True),
)
def stats_overview_start(agents: list[str]) -> dict[str, Any]:
    for a in agents:
        _validate_agent(a)
    job_id = _new_job("stats_overview", {"agents": agents})
    t = threading.Thread(
        target=_run_stats_overview_job, args=(job_id, agents), daemon=True
    )
    t.start()
    return {"job_id": job_id, "state": "queued",
            "hint": "stats_overview_result で結果"}


@mcp.tool(
    description="全セッション統計概要ジョブの状態を取得する（read）。",
    annotations=ToolAnnotations(read_only_hint=True),
)
def stats_overview_status(job_id: str) -> dict[str, Any]:
    with _job_lock:
        job = _jobs.get(job_id)
    if not job:
        return {"error": "job not found", "job_id": job_id}
    return {"job_id": job_id, "state": job["state"], "error": job.get("error")}


@mcp.tool(
    description="全セッション統計概要ジョブの結果を取得する（read）。"
    "state=done で overview が返る。",
    annotations=ToolAnnotations(read_only_hint=True),
)
def stats_overview_result(job_id: str) -> dict[str, Any]:
    with _job_lock:
        job = _jobs.get(job_id)
    if not job:
        return {"error": "job not found", "job_id": job_id}
    if job["state"] != "done":
        return {"job_id": job_id, "state": job["state"], "error": job.get("error")}
    return {"job_id": job_id, "state": "done", **job.get("result", {})}


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

_SPEC_CAPABILITIES = [
    {"kind": "tool", "name": "list_sessions",
     "summary": "指定 agent のセッション一覧を取得する",
     "input": "agent: claude|codex|gemini|aider|cursor",
     "output": "{sessions: [{path, project, size, mtime, preview}], count}",
     "side_effect": "read", "long_running": False, "dry_run": False, "min_role": "MEMBER"},
    {"kind": "tool", "name": "to_model",
     "summary": "セッションログを共通モデル JSON に変換する",
     "input": "agent, session_path",
     "output": "{source, agent, messages[]}",
     "side_effect": "read", "long_running": False, "dry_run": False, "min_role": "MEMBER"},
    {"kind": "tool", "name": "render",
     "summary": "セッションを md/html/player/terminal にレンダリングする",
     "input": "agent, session_path, format, theme?, range?",
     "output": "{format, content}",
     "side_effect": "read", "long_running": False, "dry_run": False, "min_role": "MEMBER"},
    {"kind": "tool", "name": "render_media_start",
     "summary": "MP4/PDF/GIF レンダリングジョブを開始する（job 型）",
     "input": "agent, session_path, media_type, width?, height?, fps?, speed?",
     "output": "{job_id, state, hint}",
     "side_effect": "read", "long_running": True, "dry_run": False, "min_role": "MEMBER"},
    {"kind": "tool", "name": "render_media_status",
     "summary": "メディアレンダリングジョブの状態を取得する",
     "input": "job_id", "output": "{job_id, state, progress, error?}",
     "side_effect": "read", "long_running": False, "dry_run": False, "min_role": "MEMBER"},
    {"kind": "tool", "name": "render_media_result",
     "summary": "メディアレンダリングジョブの成果物パスを取得する",
     "input": "job_id", "output": "{job_id, state, output_path, error?}",
     "side_effect": "read", "long_running": False, "dry_run": False, "min_role": "MEMBER"},
    {"kind": "tool", "name": "search_session",
     "summary": "1セッション内のメッセージを検索する",
     "input": "agent, session_path, query, scope?, case_sensitive?, regex?, max_matches?",
     "output": "{matches[], total_matches}",
     "side_effect": "read", "long_running": False, "dry_run": False, "min_role": "MEMBER"},
    {"kind": "tool", "name": "search_cross_start",
     "summary": "全セッション横断検索ジョブを開始する（job 型）",
     "input": "agents[], query, scope?, case_sensitive?, regex?, max_sessions?, max_matches_per_session?",
     "output": "{job_id, state, hint}",
     "side_effect": "read", "long_running": True, "dry_run": False, "min_role": "MEMBER"},
    {"kind": "tool", "name": "search_cross_status",
     "summary": "横断検索ジョブの状態を取得する",
     "input": "job_id", "output": "{job_id, state, error?}",
     "side_effect": "read", "long_running": False, "dry_run": False, "min_role": "MEMBER"},
    {"kind": "tool", "name": "search_cross_result",
     "summary": "横断検索ジョブの結果を取得する",
     "input": "job_id", "output": "{job_id, state, results[], stats}",
     "side_effect": "read", "long_running": False, "dry_run": False, "min_role": "MEMBER"},
    {"kind": "tool", "name": "stats_session",
     "summary": "1セッションの統計情報を取得する",
     "input": "agent, session_path", "output": "stats JSON",
     "side_effect": "read", "long_running": False, "dry_run": False, "min_role": "MEMBER"},
    {"kind": "tool", "name": "stats_overview_start",
     "summary": "全セッション統計概要ジョブを開始する（job 型）",
     "input": "agents[]", "output": "{job_id, state, hint}",
     "side_effect": "read", "long_running": True, "dry_run": False, "min_role": "MEMBER"},
    {"kind": "tool", "name": "stats_overview_status",
     "summary": "全セッション統計概要ジョブの状態を取得する",
     "input": "job_id", "output": "{job_id, state, error?}",
     "side_effect": "read", "long_running": False, "dry_run": False, "min_role": "MEMBER"},
    {"kind": "tool", "name": "stats_overview_result",
     "summary": "全セッション統計概要ジョブの結果を取得する",
     "input": "job_id", "output": "{job_id, state, overview}",
     "side_effect": "read", "long_running": False, "dry_run": False, "min_role": "MEMBER"},
    {"kind": "tool", "name": "diff_sessions",
     "summary": "2つのセッションを比較する",
     "input": "agent, session_a, session_b",
     "output": "{message_diff, tool_diff, messages_a[], messages_b[]}",
     "side_effect": "read", "long_running": False, "dry_run": False, "min_role": "MEMBER"},
    {"kind": "resource", "name": "spec", "summary": "能力の機械可読仕様"},
    {"kind": "resource", "name": "guide", "summary": "使い方ガイド"},
    {"kind": "resource", "name": "schema", "summary": "共通モデル JSON スキーマ"},
    {"kind": "skill", "name": "ship-sessions",
     "summary": "セッションログのリダクション付き出荷手順"},
    {"kind": "skill", "name": "add-agent-adapter",
     "summary": "新しいエージェントアダプター追加手順"},
]

_SPEC_COMPOSITIONS = [
    {"title": "セッション録画を紙芝居の素材にする",
     "flow": ["index__agent_list", "session-replay__list_sessions",
              "session-replay__render", "kamishibai__render_start"],
     "note": "エージェントのセッションを player HTML にレンダリングし、kamishibai の動画素材にする"},
    {"title": "横断検索で該当セッションを特定し共有用 Markdown を生成",
     "flow": ["session-replay__search_cross_start", "session-replay__search_cross_result",
              "session-replay__stats_session", "session-replay__render"],
     "note": "検索→統計→Markdown 出力のワークフロー"},
    {"title": "共通モデル JSON を他サービスの入力にする",
     "flow": ["session-replay__to_model"],
     "note": "正規化された共通モデル JSON を他サービスの入力にする。スキーマは session-replay://schema"},
]

_SPEC_DEPENDS_ON = [
    {"namespace": "volta", "capability": "ファサード(session-replay__* の公開)"},
    {"namespace": "index", "capability": "index__agent_list / index__agent_status"},
    {"namespace": "kamishibai", "capability": "kamishibai__render_start（動画化）"},
]


def _spec() -> dict[str, Any]:
    return {
        "namespace": "session-replay",
        "name": "claude-session-replay",
        "version": MCP_VERSION,
        "summary": (
            "AI コーディングエージェントのセッションログを共通モデルに正規化し、"
            "Markdown / HTML / Player / Terminal / MP4 / PDF / GIF に変換・検索・統計するツールチェーン。"
        ),
        "capabilities": _SPEC_CAPABILITIES,
        "compositions": _SPEC_COMPOSITIONS,
        "depends_on": _SPEC_DEPENDS_ON,
        "health": "/healthz",
        "docs": ["session-replay://guide", "docs/mcp/DESIGN.md"],
    }


_COMMON_MODEL_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Session Common Model",
    "description": "AI coding agent session log → common model (agent-agnostic)",
    "type": "object",
    "required": ["source", "agent", "messages"],
    "properties": {
        "source": {"type": "string", "description": "Original log file path"},
        "agent": {"type": "string",
                  "enum": ["claude", "codex", "gemini", "aider", "cursor"]},
        "messages": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["role", "text"],
                "properties": {
                    "role": {"type": "string", "enum": ["user", "assistant"]},
                    "text": {"type": "string"},
                    "tool_uses": {"type": "array", "items": {"type": "object"}},
                    "tool_results": {"type": "array", "items": {"type": "object"}},
                    "thinking": {"type": "array", "items": {"type": "string"}},
                    "timestamp": {"type": "string",
                                  "description": "ISO-8601 or empty"},
                },
            },
        },
    },
}


@mcp.resource("session-replay://spec", mime_type="application/json")
def spec_resource() -> str:
    return json.dumps(_spec(), ensure_ascii=False, indent=2)


@mcp.resource("session-replay://guide", mime_type="text/markdown")
def guide_resource() -> str:
    return """# session-replay MCP — 使い方ガイド

## 接続

- MCP URL: `http://192.168.1.50:9241/mcp`
- volta ファサード経由: `session-replay__*` tools
- healthz: `/healthz`

## 対応 agent

claude, codex, gemini, aider, cursor

## ワークフロー

### セッションの確認と変換

1. `list_sessions(agent=claude)` でセッション一覧を取得
2. `to_model(agent=claude, session_path=<path>)` で共通モデル JSON に変換
3. `render(agent=claude, session_path=<path>, format=md)` で Markdown にレンダリング

### 検索

1. 1セッション: `search_session(agent, session_path, query)`
2. 全セッション横断: `search_cross_start` → `search_cross_status` → `search_cross_result`（job 型）

### 統計・比較

1. `stats_session(agent, session_path)` で1セッションの統計
2. `stats_overview_start` → `stats_overview_result` で全体統計（job 型）
3. `diff_sessions(agent, session_a, session_b)` で2セッション比較

### メディア出力（job 型）

1. `render_media_start(agent, session_path, media_type=mp4)` でジョブ開始
2. `render_media_status(job_id)` で進捗確認（30〜60秒間隔）
3. `render_media_result(job_id)` で成果物パス取得

## 注意

- セッションログに個人情報・秘密情報が含まれる可能性がある。外部共有時は redaction を通すこと。
- MP4/PDF/GIF レンダリングは Playwright + FFmpeg が必要。環境に未インストールの場合はエラーになる。
- cross-session search / stats overview は全セッションをスキャンするため job 型（30秒超の可能性）。
- 詳細は `session-replay://spec` と `docs/mcp/DESIGN.md` を参照。
"""


@mcp.resource("session-replay://schema", mime_type="application/json")
def schema_resource() -> str:
    return json.dumps(_COMMON_MODEL_SCHEMA, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    import uvicorn
    uvicorn.run(create_app(), host=MCP_HOST, port=MCP_PORT, log_level="info")


if __name__ == "__main__":
    main()
