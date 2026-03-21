#!/usr/bin/env python3
"""Session shipper: ship AI coding agent session logs to OpenSearch or file export.

Supports batch mode (ship completed sessions) and watch mode (real-time daemon).
"""

import argparse
import copy
import getpass
import hashlib
import importlib.util
import json
import logging
import os
import platform
import re
import signal
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("shipper")


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


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "endpoint": {
        "type": "file",
        "url": "",
        "index": "agent-sessions",
        "auth": {"type": "none", "api_key": "", "username": "", "password": ""},
        "timeout_seconds": 30,
        "verify_ssl": True,
    },
    "file_export": {
        "directory": "./shipped-sessions/",
        "format": "ndjson",
    },
    "identity": {"user_id": "", "hostname": "", "organization": ""},
    "scope": {
        "include_text": True,
        "include_thinking": False,
        "include_tool_use": True,
        "include_tool_result": False,
    },
    "redaction": {"patterns": []},
    "security": {
        "sensitive_paths": [],
        "suspicious_commands": [],
        "banned_words": [],
    },
    "watch": {"agents": ["claude", "codex", "gemini"], "polling_interval_seconds": 2},
    "shipping": {
        "batch_size": 50,
        "flush_interval_seconds": 5,
        "max_retries": 3,
        "retry_backoff_seconds": 2,
        "max_field_size": 10240,
    },
    "state_file": "~/.claude-replay/shipper-state.json",
}


def _deep_merge(base, override):
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_config(config_path=None):
    search = [
        config_path,
        "./shipper-config.json",
        str(Path.home() / ".claude-replay" / "shipper.json"),
    ]
    for p in search:
        if p and os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                user_config = json.load(f)
            log.info("Loaded config from %s", p)
            return _deep_merge(DEFAULT_CONFIG, user_config)
    log.info("No config file found, using defaults")
    return DEFAULT_CONFIG.copy()


# ---------------------------------------------------------------------------
# Identity envelope
# ---------------------------------------------------------------------------

def _session_id_from_path(path):
    return hashlib.sha256(os.path.abspath(path).encode()).hexdigest()[:16]


def _resolve_identity(config):
    ident = config.get("identity", {})
    return {
        "user_id": ident.get("user_id") or os.getenv("USER") or os.getenv("USERNAME") or getpass.getuser(),
        "hostname": ident.get("hostname") or socket.gethostname(),
        "os_user": os.getenv("USER") or os.getenv("USERNAME") or getpass.getuser(),
        "os_platform": platform.system().lower(),
        "organization": ident.get("organization", ""),
    }


def build_envelope(config, session_path, agent, project=""):
    identity = _resolve_identity(config)
    identity.update({
        "agent": agent,
        "session_id": _session_id_from_path(session_path),
        "session_file": os.path.abspath(session_path),
        "project": project,
    })
    return identity


# ---------------------------------------------------------------------------
# Redaction engine
# ---------------------------------------------------------------------------

def build_redactor(config):
    patterns = []
    for p in config.get("redaction", {}).get("patterns", []):
        try:
            compiled = re.compile(p["regex"])
            patterns.append((compiled, p.get("replacement", "***REDACTED***")))
        except re.error as e:
            log.warning("Bad redaction pattern '%s': %s", p.get("name", "?"), e)
    return patterns


def apply_redaction(text, redactor):
    if not text or not redactor:
        return text
    for pattern, replacement in redactor:
        text = pattern.sub(replacement, text)
    return text


def redact_message(message, redactor):
    if not redactor:
        return message
    msg = copy.deepcopy(message)
    if msg.get("text"):
        msg["text"] = apply_redaction(msg["text"], redactor)
    if msg.get("thinking"):
        msg["thinking"] = [apply_redaction(t, redactor) for t in msg["thinking"]]
    if msg.get("tool_uses"):
        for tu in msg["tool_uses"]:
            inp = tu.get("input", {})
            if isinstance(inp, dict):
                for k, v in inp.items():
                    if isinstance(v, str):
                        inp[k] = apply_redaction(v, redactor)
    if msg.get("tool_results"):
        for tr in msg["tool_results"]:
            if tr.get("content"):
                tr["content"] = apply_redaction(tr["content"], redactor)
    return msg


# ---------------------------------------------------------------------------
# Scope filtering
# ---------------------------------------------------------------------------

def filter_scope(message, scope_config):
    result = {
        "role": message.get("role", ""),
        "timestamp": message.get("timestamp", ""),
        "message_index": message.get("message_index", 0),
    }
    if scope_config.get("include_text", True):
        result["text"] = message.get("text", "")
    if scope_config.get("include_thinking", False):
        result["thinking"] = message.get("thinking", [])
    if scope_config.get("include_tool_use", True):
        result["tool_uses"] = message.get("tool_uses", [])
    if scope_config.get("include_tool_result", False):
        result["tool_results"] = message.get("tool_results", [])
    return result


# ---------------------------------------------------------------------------
# Field size truncation
# ---------------------------------------------------------------------------

def truncate_fields(message, max_size):
    if not max_size or max_size <= 0:
        return message
    msg = copy.deepcopy(message)
    if isinstance(msg.get("text"), str) and len(msg["text"]) > max_size:
        msg["text"] = msg["text"][:max_size] + "...[truncated]"
    if msg.get("tool_results"):
        for tr in msg["tool_results"]:
            if isinstance(tr.get("content"), str) and len(tr["content"]) > max_size:
                tr["content"] = tr["content"][:max_size] + "...[truncated]"
    return msg


# ---------------------------------------------------------------------------
# Security analysis
# ---------------------------------------------------------------------------

def analyze_security(message, security_config):
    flags = []
    sensitive_paths = security_config.get("sensitive_paths", [])
    suspicious_cmds = security_config.get("suspicious_commands", [])

    for tu in message.get("tool_uses", []):
        name = tu.get("name", "")
        inp = tu.get("input", {}) or {}

        # Sensitive file read
        if name in ("Read", "file_read"):
            fpath = inp.get("file_path", "") or inp.get("path", "")
            for sp in sensitive_paths:
                if sp in fpath:
                    flags.append({
                        "severity": "high",
                        "category": "sensitive_file_read",
                        "detail": f"{name} {fpath}",
                    })
                    break

        # Sensitive file write
        if name in ("Write", "file_write"):
            fpath = inp.get("file_path", "") or inp.get("path", "")
            for sp in sensitive_paths:
                if sp in fpath:
                    flags.append({
                        "severity": "high",
                        "category": "sensitive_file_write",
                        "detail": f"{name} {fpath}",
                    })
                    break

        # Suspicious commands
        if name in ("Bash", "shell_command"):
            cmd = inp.get("command", "")
            for sc in suspicious_cmds:
                if sc in cmd:
                    flags.append({
                        "severity": "medium",
                        "category": "suspicious_command",
                        "detail": f"{sc.strip()} in: {cmd[:200]}",
                    })
                    break
            # External URL access
            if re.search(r'https?://', cmd):
                urls = re.findall(r'https?://[^\s"\']+', cmd)
                for url in urls[:3]:
                    flags.append({
                        "severity": "medium",
                        "category": "external_access",
                        "detail": f"URL access: {url[:200]}",
                    })

        # Sensitive search
        if name in ("Grep",):
            pattern = inp.get("pattern", "")
            for sp in sensitive_paths:
                if sp in pattern:
                    flags.append({
                        "severity": "low",
                        "category": "sensitive_search",
                        "detail": f"Grep for: {pattern[:200]}",
                    })
                    break

    return flags


def detect_banned_words(message, banned_words):
    if not banned_words:
        return []
    hits = []
    fields_to_check = [
        ("text", message.get("text", "")),
    ]
    for t in message.get("thinking", []):
        fields_to_check.append(("thinking", t))
    for tu in message.get("tool_uses", []):
        inp = tu.get("input", {})
        if isinstance(inp, dict):
            for v in inp.values():
                if isinstance(v, str):
                    fields_to_check.append(("tool_use", v))
    for tr in message.get("tool_results", []):
        fields_to_check.append(("tool_result", tr.get("content", "")))

    for field_name, text in fields_to_check:
        if not text:
            continue
        text_lower = text.lower()
        for word in banned_words:
            count = text_lower.count(word.lower())
            if count > 0:
                hits.append({"word": word, "field": field_name, "count": count})
    return hits


# ---------------------------------------------------------------------------
# Transport layer
# ---------------------------------------------------------------------------

class Transport:
    def ship(self, documents):
        raise NotImplementedError


class OpenSearchTransport(Transport):
    def __init__(self, config):
        self.url = config["endpoint"]["url"].rstrip("/")
        self.index = config["endpoint"].get("index", "agent-sessions")
        self.auth = config["endpoint"].get("auth", {})
        self.timeout = config["endpoint"].get("timeout_seconds", 30)
        self.verify_ssl = config["endpoint"].get("verify_ssl", True)
        self.max_retries = config.get("shipping", {}).get("max_retries", 3)
        self.backoff = config.get("shipping", {}).get("retry_backoff_seconds", 2)

    def _build_headers(self):
        headers = {"Content-Type": "application/x-ndjson"}
        auth_type = self.auth.get("type", "none")
        if auth_type == "api_key":
            headers["Authorization"] = f"ApiKey {self.auth['api_key']}"
        elif auth_type == "basic":
            import base64
            creds = base64.b64encode(
                f"{self.auth['username']}:{self.auth['password']}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {creds}"
        return headers

    def ship(self, documents):
        if not documents:
            return 0, 0
        # Build NDJSON bulk body
        lines = []
        for doc in documents:
            doc_id = doc.get("_doc_id", "")
            action = json.dumps({"index": {"_index": self.index, "_id": doc_id}})
            body = json.dumps({k: v for k, v in doc.items() if k != "_doc_id"}, ensure_ascii=False)
            lines.append(action)
            lines.append(body)
        payload = "\n".join(lines) + "\n"

        bulk_url = f"{self.url}/_bulk"
        headers = self._build_headers()

        for attempt in range(self.max_retries):
            try:
                req = urllib.request.Request(
                    bulk_url,
                    data=payload.encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                if not self.verify_ssl:
                    import ssl
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    resp = urllib.request.urlopen(req, timeout=self.timeout, context=ctx)
                else:
                    resp = urllib.request.urlopen(req, timeout=self.timeout)
                result = json.loads(resp.read().decode())
                errors = result.get("errors", False)
                if errors:
                    error_count = sum(1 for item in result.get("items", [])
                                      if "error" in item.get("index", {}))
                    return len(documents) - error_count, error_count
                return len(documents), 0
            except (urllib.error.URLError, OSError) as e:
                log.warning("Ship attempt %d/%d failed: %s", attempt + 1, self.max_retries, e)
                if attempt < self.max_retries - 1:
                    time.sleep(self.backoff * (attempt + 1))
        return 0, len(documents)


class FileExportTransport(Transport):
    def __init__(self, config):
        self.directory = config.get("file_export", {}).get("directory", "./shipped-sessions/")
        self.fmt = config.get("file_export", {}).get("format", "ndjson")
        os.makedirs(self.directory, exist_ok=True)

    def ship(self, documents):
        if not documents:
            return 0, 0
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"ship-{ts}-{os.getpid()}.ndjson"
        filepath = os.path.join(self.directory, filename)
        try:
            with open(filepath, "a", encoding="utf-8") as f:
                for doc in documents:
                    clean = {k: v for k, v in doc.items() if k != "_doc_id"}
                    f.write(json.dumps(clean, ensure_ascii=False) + "\n")
            log.info("Exported %d documents to %s", len(documents), filepath)
            return len(documents), 0
        except OSError as e:
            log.error("File export failed: %s", e)
            return 0, len(documents)


class DryRunTransport(Transport):
    def ship(self, documents):
        for doc in documents:
            clean = {k: v for k, v in doc.items() if k != "_doc_id"}
            print(json.dumps(clean, ensure_ascii=False, indent=2))
        return len(documents), 0


def create_transport(config, dry_run=False):
    if dry_run:
        return DryRunTransport()
    ep_type = config.get("endpoint", {}).get("type", "file")
    if ep_type == "opensearch" or ep_type == "rest":
        return OpenSearchTransport(config)
    return FileExportTransport(config)


# ---------------------------------------------------------------------------
# Offset tracking
# ---------------------------------------------------------------------------

class OffsetTracker:
    def __init__(self, state_file_path):
        self.path = os.path.expanduser(state_file_path)
        self.state = {"files": {}}
        self.load()

    def load(self):
        if os.path.isfile(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.state = json.load(f)
            except (json.JSONDecodeError, OSError):
                self.state = {"files": {}}

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)

    def get_offset(self, file_path):
        key = os.path.abspath(file_path)
        info = self.state.get("files", {}).get(key, {})
        return info.get("byte_offset", 0), info.get("line_count", 0)

    def update_offset(self, file_path, byte_offset, line_count):
        key = os.path.abspath(file_path)
        if "files" not in self.state:
            self.state["files"] = {}
        self.state["files"][key] = {
            "byte_offset": byte_offset,
            "line_count": line_count,
            "last_mtime": os.path.getmtime(file_path) if os.path.exists(file_path) else 0,
            "last_shipped": datetime.now(timezone.utc).isoformat(),
            "session_id": _session_id_from_path(file_path),
        }

    def get_session_map(self):
        """Return session_id → file_path mapping."""
        mapping = {}
        for fpath, info in self.state.get("files", {}).items():
            sid = info.get("session_id", "")
            if sid:
                mapping[sid] = fpath
        return mapping


# ---------------------------------------------------------------------------
# Message processing pipeline
# ---------------------------------------------------------------------------

def _doc_id(session_id, message_index):
    raw = f"{session_id}:{message_index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def process_message(message, message_index, envelope, config, redactor):
    """Process a single common-model message into a shippable document."""
    msg = dict(message)
    msg["message_index"] = message_index

    # Truncate large fields
    max_size = config.get("shipping", {}).get("max_field_size", 10240)
    msg = truncate_fields(msg, max_size)

    # Scope filter
    msg = filter_scope(msg, config.get("scope", {}))

    # Redaction
    msg = redact_message(msg, redactor)

    # Security analysis (on original tool_uses before scope filter removed them)
    security_flags = analyze_security(message, config.get("security", {}))

    # Banned word detection
    banned_hits = detect_banned_words(message, config.get("security", {}).get("banned_words", []))

    # Build final document
    doc = {}
    doc.update(envelope)
    doc.update(msg)
    doc["timestamp_original"] = message.get("timestamp", "")
    doc["timestamp_shipped"] = datetime.now(timezone.utc).isoformat()
    doc["security_flags"] = security_flags
    doc["banned_word_hits"] = banned_hits
    doc["_doc_id"] = _doc_id(envelope["session_id"], message_index)

    return doc


# ---------------------------------------------------------------------------
# Batch shipper
# ---------------------------------------------------------------------------

def ship_batch_file(session_path, agent, config, transport, offset_tracker, redactor):
    """Ship one session file in batch mode."""
    adapter = _get_adapter(agent)

    # Build common model
    if agent == "gemini":
        with open(session_path, "r", encoding="utf-8") as f:
            session_data = json.load(f)
        model = adapter.build_model(session_data, session_path)
    else:
        messages = adapter.parse_messages(session_path)
        model = adapter.build_model(messages, session_path)

    project = ""
    if hasattr(adapter, "_project_name_from_dir"):
        project_dir = Path(session_path).parent.name
        project = adapter._project_name_from_dir(project_dir)
    elif agent == "gemini":
        project = Path(session_path).parent.parent.name

    envelope = build_envelope(config, session_path, agent, project)

    # Check offset (skip already shipped messages)
    _, shipped_count = offset_tracker.get_offset(session_path)

    all_messages = model.get("messages", [])
    new_messages = all_messages[shipped_count:]

    if not new_messages:
        log.info("  %s: no new messages (already shipped %d)", Path(session_path).name, shipped_count)
        return 0

    # Process messages into documents
    batch = []
    batch_size = config.get("shipping", {}).get("batch_size", 50)
    total_shipped = 0

    for i, msg in enumerate(new_messages):
        msg_idx = shipped_count + i + 1
        doc = process_message(msg, msg_idx, envelope, config, redactor)
        batch.append(doc)

        if len(batch) >= batch_size:
            ok, err = transport.ship(batch)
            total_shipped += ok
            batch = []

    if batch:
        ok, err = transport.ship(batch)
        total_shipped += ok

    # Update offset
    new_total = shipped_count + len(new_messages)
    offset_tracker.update_offset(session_path, 0, new_total)
    offset_tracker.save()

    log.info("  %s: shipped %d new messages (total: %d)", Path(session_path).name, total_shipped, new_total)
    return total_shipped


def cmd_batch(args):
    """Handle 'batch' subcommand."""
    config = load_config(args.config)
    transport = create_transport(config, dry_run=args.dry_run)
    offset_tracker = OffsetTracker(config["state_file"])
    redactor = build_redactor(config)

    agents = [args.agent] if args.agent else config.get("watch", {}).get("agents", ["claude", "codex", "gemini"])

    if args.input:
        # Ship specific files
        agent = args.agent or "claude"
        for path in args.input:
            if os.path.isfile(path):
                ship_batch_file(path, agent, config, transport, offset_tracker, redactor)
            else:
                log.error("File not found: %s", path)
    else:
        # Discover and ship all sessions
        total = 0
        for agent in agents:
            adapter = _get_adapter(agent)
            sessions = adapter.discover_sessions()
            log.info("Found %d %s sessions", len(sessions), agent)
            for session in sessions:
                shipped = ship_batch_file(
                    session["path"], agent, config, transport, offset_tracker, redactor
                )
                total += shipped
        log.info("Total: %d messages shipped", total)


# ---------------------------------------------------------------------------
# Watch daemon (streaming)
# ---------------------------------------------------------------------------

class FileWatcher:
    def __init__(self, agents, config):
        self.agents = agents
        self.polling_interval = config.get("watch", {}).get("polling_interval_seconds", 2)
        self._known_files = {}  # path -> mtime

    def poll(self):
        """Poll for new/modified session files. Returns [(path, agent, is_new)]."""
        changed = []
        for agent in self.agents:
            adapter = _get_adapter(agent)
            try:
                sessions = adapter.discover_sessions()
            except Exception as e:
                log.warning("discover_sessions(%s) failed: %s", agent, e)
                continue
            for s in sessions:
                path = s["path"]
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                prev_mtime = self._known_files.get(path)
                if prev_mtime is None:
                    self._known_files[path] = mtime
                    changed.append((path, agent, True))
                elif mtime > prev_mtime:
                    self._known_files[path] = mtime
                    changed.append((path, agent, False))
        return changed


def _parse_line_claude(line_text, adapter):
    """Parse one JSONL line from Claude into a common-model message or None."""
    try:
        data = json.loads(line_text)
    except json.JSONDecodeError:
        return None
    if data.get("type") not in ("user", "assistant"):
        return None
    message = data.get("message", {})
    role = message.get("role", "")
    if role not in ("user", "assistant"):
        return None
    content = message.get("content", "")
    text = adapter._extract_text_from_content(content)
    tool_uses = adapter._extract_tool_uses(content)
    tool_results_raw = adapter._extract_tool_results(content)
    thinking = adapter._extract_thinking_from_content(content)

    tool_results = []
    for r in tool_results_raw:
        rc = r.get("content", "")
        rt = adapter._format_tool_result_content(rc)
        if rt.strip():
            tool_results.append({"content": rt})

    entry = {
        "role": role,
        "text": text.strip(),
        "tool_uses": tool_uses,
        "tool_results": tool_results,
        "thinking": thinking,
        "timestamp": data.get("timestamp", ""),
    }
    if entry["text"] or entry["tool_uses"] or entry["tool_results"] or entry["thinking"]:
        return entry
    return None


def _parse_line_codex(line_text, adapter):
    """Parse one JSONL line from Codex into a common-model message or None."""
    try:
        data = json.loads(line_text)
    except json.JSONDecodeError:
        return None
    msg_type = data.get("type", "")
    if msg_type not in ("message",):
        return None
    message = data.get("message", {})
    role = message.get("role", "")
    if role not in ("user", "assistant"):
        return None
    content = message.get("content", "")
    text = adapter._extract_text_from_codex_content(content)
    thinking = adapter._extract_thinking_from_codex_content(content)

    tool_uses = []
    tool_results = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "function_call":
                    tu = adapter._codex_tool_use_from_function_call(block)
                    if tu:
                        tool_uses.append(tu)
                elif block.get("type") == "function_call_output":
                    output = block.get("output", "")
                    if output:
                        tool_results.append({"content": str(output)})

    entry = {
        "role": role,
        "text": text.strip() if text else "",
        "tool_uses": tool_uses,
        "tool_results": tool_results,
        "thinking": thinking,
        "timestamp": data.get("timestamp", ""),
    }
    if entry["text"] or entry["tool_uses"] or entry["tool_results"] or entry["thinking"]:
        return entry
    return None


def ship_stream_file(file_path, agent, config, transport, offset_tracker, redactor):
    """Read new lines from a file and ship them."""
    byte_offset, line_count = offset_tracker.get_offset(file_path)

    # Detect truncation
    try:
        file_size = os.path.getsize(file_path)
    except OSError:
        return 0
    if file_size < byte_offset:
        log.info("File truncated, resetting offset: %s", file_path)
        byte_offset = 0
        line_count = 0

    adapter = _get_adapter(agent)
    project = ""
    if hasattr(adapter, "_project_name_from_dir"):
        project_dir = Path(file_path).parent.name
        project = adapter._project_name_from_dir(project_dir)
    elif agent == "gemini":
        project = Path(file_path).parent.parent.name

    envelope = build_envelope(config, file_path, agent, project)

    # Gemini: full JSON, not JSONL
    if agent == "gemini":
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                session_data = json.load(f)
            model = adapter.build_model(session_data, file_path)
            all_messages = model.get("messages", [])
            new_messages = all_messages[line_count:]
            if not new_messages:
                return 0
            batch = []
            for i, msg in enumerate(new_messages):
                msg_idx = line_count + i + 1
                doc = process_message(msg, msg_idx, envelope, config, redactor)
                batch.append(doc)
            ok, _ = transport.ship(batch)
            offset_tracker.update_offset(file_path, 0, len(all_messages))
            offset_tracker.save()
            return ok
        except Exception as e:
            log.warning("Gemini parse error: %s", e)
            return 0

    # JSONL agents: read from byte offset
    new_lines = 0
    batch = []
    batch_size = config.get("shipping", {}).get("batch_size", 50)
    total_shipped = 0

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            f.seek(byte_offset)
            for raw_line in f:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                new_lines += 1
                msg_idx = line_count + new_lines

                if agent == "claude":
                    msg = _parse_line_claude(raw_line, adapter)
                elif agent == "codex":
                    msg = _parse_line_codex(raw_line, adapter)
                else:
                    continue

                if msg is None:
                    continue

                doc = process_message(msg, msg_idx, envelope, config, redactor)
                batch.append(doc)

                if len(batch) >= batch_size:
                    ok, _ = transport.ship(batch)
                    total_shipped += ok
                    batch = []

            new_byte_offset = f.tell()
    except OSError as e:
        log.warning("Error reading %s: %s", file_path, e)
        return 0

    if batch:
        ok, _ = transport.ship(batch)
        total_shipped += ok

    if new_lines > 0:
        offset_tracker.update_offset(file_path, new_byte_offset, line_count + new_lines)
        offset_tracker.save()
        log.info("  %s: +%d lines, shipped %d", Path(file_path).name, new_lines, total_shipped)

    return total_shipped


_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    log.info("Received signal %d, shutting down...", signum)
    _shutdown = True


def cmd_watch(args):
    """Handle 'watch' subcommand."""
    global _shutdown
    config = load_config(args.config)
    transport = create_transport(config)
    offset_tracker = OffsetTracker(config["state_file"])
    redactor = build_redactor(config)

    agents = args.agent or config.get("watch", {}).get("agents", ["claude", "codex", "gemini"])
    watcher = FileWatcher(agents, config)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    log.info("Watch daemon started (agents: %s, polling: %ds)",
             ", ".join(agents), watcher.polling_interval)

    # Initial poll to populate known files
    watcher.poll()

    while not _shutdown:
        changed = watcher.poll()
        for path, agent, is_new in changed:
            try:
                ship_stream_file(path, agent, config, transport, offset_tracker, redactor)
            except Exception as e:
                log.error("Error shipping %s: %s", path, e)
        time.sleep(watcher.polling_interval)

    log.info("Watch daemon stopped")


# ---------------------------------------------------------------------------
# Lookup: OpenSearch → local session
# ---------------------------------------------------------------------------

def cmd_lookup(args):
    """Handle 'lookup' subcommand."""
    config = load_config(args.config)
    offset_tracker = OffsetTracker(config["state_file"])
    session_map = offset_tracker.get_session_map()

    if args.session_id:
        file_path = session_map.get(args.session_id)
        if not file_path:
            print(f"Session ID '{args.session_id}' not found in local state.")
            print("Known session IDs:")
            for sid, fpath in sorted(session_map.items()):
                print(f"  {sid}  {fpath}")
            return

        print(f"Session: {file_path}")
        if args.open_player:
            import subprocess
            # Determine agent from path
            agent = "claude"
            if "codex" in file_path.lower():
                agent = "codex"
            elif "gemini" in file_path.lower():
                agent = "gemini"
            cmd = [sys.executable, str(_script_dir / "log-replay.py"),
                   "--agent", agent, file_path, "-f", "player"]
            if args.message:
                cmd += ["--render-arg", f"--range", "--render-arg", str(args.message)]
            subprocess.run(cmd)
    else:
        # List all known sessions
        print(f"{'Session ID':18}  {'Agent':8}  Path")
        print(f"{'─' * 18}  {'─' * 8}  {'─' * 60}")
        for sid, fpath in sorted(session_map.items()):
            agent = "claude"
            if "codex" in fpath.lower():
                agent = "codex"
            elif "gemini" in fpath.lower():
                agent = "gemini"
            print(f"{sid:18}  {agent:8}  {fpath}")


# ---------------------------------------------------------------------------
# Init config & status
# ---------------------------------------------------------------------------

def cmd_init_config(args):
    """Generate default config file."""
    output = args.output or "shipper-config.json"
    if os.path.exists(output) and not args.force:
        print(f"{output} already exists. Use --force to overwrite.")
        return
    with open(output, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
    print(f"Config written to {output}")


def cmd_status(args):
    """Show shipping state and stats."""
    config = load_config(args.config)
    tracker = OffsetTracker(config["state_file"])

    files = tracker.state.get("files", {})
    if not files:
        print("No shipping state found.")
        return

    print(f"\n  {'Session ID':18}  {'Messages':>8}  {'Last Shipped':20}  Path")
    print(f"  {'─' * 18}  {'─' * 8}  {'─' * 20}  {'─' * 50}")
    total_msgs = 0
    for fpath, info in sorted(files.items()):
        sid = info.get("session_id", "?")
        count = info.get("line_count", 0)
        last = info.get("last_shipped", "?")[:19]
        total_msgs += count
        fname = Path(fpath).name if len(fpath) > 50 else fpath
        print(f"  {sid:18}  {count:>8}  {last:20}  {fname}")

    print(f"\n  Total: {len(files)} sessions, {total_msgs} messages shipped\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Ship AI coding agent session logs to OpenSearch or file export"
    )
    parser.add_argument("--config", "-c", help="config file path")

    sub = parser.add_subparsers(dest="command")

    # batch
    batch_p = sub.add_parser("batch", help="Ship completed sessions")
    batch_p.add_argument("--agent", choices=["claude", "codex", "gemini"])
    batch_p.add_argument("--input", nargs="*", help="specific session file(s)")
    batch_p.add_argument("--dry-run", action="store_true", help="parse and display, don't ship")

    # watch
    watch_p = sub.add_parser("watch", help="Tail session logs in real-time (daemon)")
    watch_p.add_argument("--agent", nargs="*", choices=["claude", "codex", "gemini"])

    # lookup
    lookup_p = sub.add_parser("lookup", help="Find local session from OpenSearch result")
    lookup_p.add_argument("--session-id", help="session ID to look up")
    lookup_p.add_argument("--message", "-m", type=int, help="message index to jump to")
    lookup_p.add_argument("--open-player", action="store_true", help="open session in Player")

    # init-config
    init_p = sub.add_parser("init-config", help="Generate default config file")
    init_p.add_argument("--output", "-o", default="shipper-config.json")
    init_p.add_argument("--force", action="store_true")

    # status
    sub.add_parser("status", help="Show shipping state and stats")

    args = parser.parse_args()

    if args.command == "batch":
        cmd_batch(args)
    elif args.command == "watch":
        cmd_watch(args)
    elif args.command == "lookup":
        cmd_lookup(args)
    elif args.command == "init-config":
        cmd_init_config(args)
    elif args.command == "status":
        cmd_status(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
