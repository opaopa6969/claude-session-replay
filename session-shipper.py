#!/usr/bin/env python3
"""Session shipper: ship AI coding agent session logs to OpenSearch or file export.

Supports batch mode (ship completed sessions) and watch mode (real-time daemon).
All enterprise features are gated by config flags — disabled features have zero cost.
"""

import argparse
import copy
import getpass
import gzip
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
    "features": {
        "shipping_enabled": True,
        "security_analysis": True,
        "banned_word_detection": True,
        "redaction": True,
        "oidc_auth": False,
        "dls": False,
        "multi_tenant": False,
        "webhooks": False,
        "metadata_only": False,
        "auto_policy_sync": False,
        "compression": False,
        "encryption_at_rest": False,
        "dead_letter_queue": True,
    },
    "endpoint": {
        "type": "file",
        "url": "",
        "index": "agent-sessions",
        "auth": {
            "type": "none",
            "api_key": "",
            "username": "",
            "password": "",
            "issuer_url": "",
            "client_id": "",
            "client_secret": "",
            "scopes": ["openid"],
            "token_cache_path": "~/.claude-replay/oidc-token.json",
        },
        "timeout_seconds": 30,
        "verify_ssl": True,
    },
    "file_export": {
        "directory": "./shipped-sessions/",
        "format": "ndjson",
    },
    "identity": {"user_id": "", "hostname": "", "organization": "", "roles": []},
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
    "webhooks": {
        "url": "",
        "events": ["security_high", "banned_word"],
        "format": "generic",
        "headers": {},
        "timeout_seconds": 10,
        "max_retries": 2,
    },
    "policy_sync": {
        "url": "",
        "interval_seconds": 300,
        "auth_same_as_endpoint": True,
    },
    "encryption": {
        "key_file": "~/.claude-replay/encryption.key",
    },
    "multi_tenant": {
        "strategy": "index_per_tenant",
        "index_prefix": "agent-sessions",
    },
    "watch": {"agents": ["claude", "codex", "gemini"], "polling_interval_seconds": 2},
    "shipping": {
        "batch_size": 50,
        "flush_interval_seconds": 5,
        "max_retries": 3,
        "retry_backoff_seconds": 2,
        "max_field_size": 10240,
    },
    "dlq": {
        "directory": "~/.claude-replay/dlq/",
    },
    "state_file": "~/.claude-replay/shipper-state.json",
}


def _feat(config, flag):
    """Check if a feature flag is enabled."""
    return config.get("features", {}).get(flag, False)


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
    return copy.deepcopy(DEFAULT_CONFIG)


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
# OIDC authentication (feature flag: oidc_auth)
# ---------------------------------------------------------------------------

class OIDCTokenManager:
    """Manage OIDC tokens via Client Credentials flow. Caches to disk."""

    def __init__(self, auth_config):
        self.issuer_url = auth_config.get("issuer_url", "").rstrip("/")
        self.client_id = auth_config.get("client_id", "")
        self.client_secret = auth_config.get("client_secret", "")
        self.scopes = auth_config.get("scopes", ["openid"])
        self.cache_path = os.path.expanduser(auth_config.get("token_cache_path", ""))
        self._token_data = None
        self._load_cache()

    def _load_cache(self):
        if self.cache_path and os.path.isfile(self.cache_path):
            try:
                with open(self.cache_path, "r") as f:
                    self._token_data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._token_data = None

    def _save_cache(self):
        if self.cache_path and self._token_data:
            os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
            with open(self.cache_path, "w") as f:
                json.dump(self._token_data, f)

    def _is_expired(self):
        if not self._token_data:
            return True
        expires_at = self._token_data.get("expires_at", 0)
        return time.time() >= expires_at - 30  # 30s buffer

    def _discover_token_endpoint(self):
        url = f"{self.issuer_url}/.well-known/openid-configuration"
        try:
            req = urllib.request.Request(url)
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read().decode())
            return data.get("token_endpoint", "")
        except Exception as e:
            log.warning("OIDC discovery failed: %s", e)
            return f"{self.issuer_url}/protocol/openid-connect/token"

    def _fetch_token(self):
        token_endpoint = self._discover_token_endpoint()
        params = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": " ".join(self.scopes),
        }).encode()
        try:
            import urllib.parse
            req = urllib.request.Request(token_endpoint, data=params, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read().decode())
            data["expires_at"] = time.time() + data.get("expires_in", 300)
            self._token_data = data
            self._save_cache()
            log.info("OIDC token acquired (expires in %ds)", data.get("expires_in", 0))
            return data
        except Exception as e:
            log.error("OIDC token fetch failed: %s", e)
            return None

    def get_access_token(self):
        if self._is_expired():
            self._fetch_token()
        return self._token_data.get("access_token", "") if self._token_data else ""

    def get_user_id(self):
        """Extract user_id from token claims (JWT)."""
        token = self.get_access_token()
        if not token:
            return ""
        try:
            import base64
            # Decode JWT payload (2nd segment) without verification
            parts = token.split(".")
            if len(parts) < 2:
                return ""
            payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
            return claims.get("preferred_username") or claims.get("sub", "")
        except Exception:
            return ""

    def get_roles(self):
        """Extract roles from token claims."""
        token = self.get_access_token()
        if not token:
            return []
        try:
            import base64
            parts = token.split(".")
            if len(parts) < 2:
                return []
            payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
            return claims.get("groups", claims.get("roles", []))
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Redaction engine
# ---------------------------------------------------------------------------

def build_redactor(config):
    if not _feat(config, "redaction"):
        return []
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

def filter_scope(message, config):
    scope_config = config.get("scope", {})

    # Metadata-only mode
    if _feat(config, "metadata_only"):
        return {
            "role": message.get("role", ""),
            "timestamp": message.get("timestamp", ""),
            "message_index": message.get("message_index", 0),
            "text_length": len(message.get("text", "")),
            "thinking_count": len(message.get("thinking", [])),
            "tool_use_names": [tu.get("name", "") for tu in message.get("tool_uses", [])],
            "tool_result_count": len(message.get("tool_results", [])),
        }

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
# Security analysis (feature flag: security_analysis)
# ---------------------------------------------------------------------------

def analyze_security(message, config):
    if not _feat(config, "security_analysis"):
        return []
    security_config = config.get("security", {})
    flags = []
    sensitive_paths = security_config.get("sensitive_paths", [])
    suspicious_cmds = security_config.get("suspicious_commands", [])

    for tu in message.get("tool_uses", []):
        name = tu.get("name", "")
        inp = tu.get("input", {}) or {}

        if name in ("Read", "file_read"):
            fpath = inp.get("file_path", "") or inp.get("path", "")
            for sp in sensitive_paths:
                if sp in fpath:
                    flags.append({"severity": "high", "category": "sensitive_file_read",
                                  "detail": f"{name} {fpath}"})
                    break

        if name in ("Write", "file_write"):
            fpath = inp.get("file_path", "") or inp.get("path", "")
            for sp in sensitive_paths:
                if sp in fpath:
                    flags.append({"severity": "high", "category": "sensitive_file_write",
                                  "detail": f"{name} {fpath}"})
                    break

        if name in ("Bash", "shell_command"):
            cmd = inp.get("command", "")
            for sc in suspicious_cmds:
                if sc in cmd:
                    flags.append({"severity": "medium", "category": "suspicious_command",
                                  "detail": f"{sc.strip()} in: {cmd[:200]}"})
                    break
            if re.search(r'https?://', cmd):
                urls = re.findall(r'https?://[^\s"\']+', cmd)
                for url in urls[:3]:
                    flags.append({"severity": "medium", "category": "external_access",
                                  "detail": f"URL access: {url[:200]}"})

        if name in ("Grep",):
            pattern = inp.get("pattern", "")
            for sp in sensitive_paths:
                if sp in pattern:
                    flags.append({"severity": "low", "category": "sensitive_search",
                                  "detail": f"Grep for: {pattern[:200]}"})
                    break

    return flags


def detect_banned_words(message, config):
    if not _feat(config, "banned_word_detection"):
        return []
    banned_words = config.get("security", {}).get("banned_words", [])
    if not banned_words:
        return []
    hits = []
    fields_to_check = [("text", message.get("text", ""))]
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
# Webhooks (feature flag: webhooks)
# ---------------------------------------------------------------------------

def send_webhook(config, alerts, doc):
    """Send webhook notification for security alerts or banned word hits."""
    if not _feat(config, "webhooks"):
        return
    wh_config = config.get("webhooks", {})
    url = wh_config.get("url", "")
    if not url:
        return

    events = wh_config.get("events", [])
    should_fire = False
    for alert in alerts:
        sev = alert.get("severity", "")
        cat = alert.get("category", "")
        if f"security_{sev}" in events or cat in events:
            should_fire = True
            break
    if not should_fire and "banned_word" in events and doc.get("banned_word_hits"):
        should_fire = True
    if not should_fire:
        return

    fmt = wh_config.get("format", "generic")
    if fmt == "slack":
        lines = [f"*User:* {doc.get('user_id', '?')}",
                 f"*Session:* {doc.get('session_id', '?')} #{doc.get('message_index', '?')}"]
        for a in alerts:
            lines.append(f"*[{a['severity'].upper()}]* {a['category']}: {a.get('detail', '')[:100]}")
        for bw in doc.get("banned_word_hits", []):
            lines.append(f"*Banned word:* `{bw['word']}` in {bw['field']} (x{bw['count']})")
        payload = json.dumps({
            "text": ":warning: Session Shipper Alert",
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}}],
        })
    else:
        payload = json.dumps({
            "event_type": "session_shipper_alert",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_id": doc.get("user_id", ""),
            "organization": doc.get("organization", ""),
            "session_id": doc.get("session_id", ""),
            "message_index": doc.get("message_index", 0),
            "alerts": alerts,
            "banned_word_hits": doc.get("banned_word_hits", []),
        }, ensure_ascii=False)

    headers = {"Content-Type": "application/json"}
    headers.update(wh_config.get("headers", {}))
    retries = wh_config.get("max_retries", 2)
    timeout = wh_config.get("timeout_seconds", 10)

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=payload.encode(), headers=headers, method="POST")
            urllib.request.urlopen(req, timeout=timeout)
            log.info("Webhook sent to %s", url)
            return
        except Exception as e:
            log.warning("Webhook attempt %d/%d failed: %s", attempt + 1, retries, e)


# ---------------------------------------------------------------------------
# Policy sync (feature flag: auto_policy_sync)
# ---------------------------------------------------------------------------

def sync_policy(config):
    """Fetch policy from central endpoint and merge into config."""
    if not _feat(config, "auto_policy_sync"):
        return config
    ps = config.get("policy_sync", {})
    url = ps.get("url", "")
    if not url:
        return config

    headers = {}
    if ps.get("auth_same_as_endpoint"):
        auth = config.get("endpoint", {}).get("auth", {})
        auth_type = auth.get("type", "none")
        if auth_type == "api_key":
            headers["Authorization"] = f"ApiKey {auth['api_key']}"
        elif auth_type == "basic":
            import base64
            creds = base64.b64encode(f"{auth['username']}:{auth['password']}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"

    try:
        req = urllib.request.Request(url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=15)
        policy = json.loads(resp.read().decode())
        # Merge policy fields into security config
        if "banned_words" in policy:
            config["security"]["banned_words"] = policy["banned_words"]
        if "sensitive_paths" in policy:
            config["security"]["sensitive_paths"] = policy["sensitive_paths"]
        if "suspicious_commands" in policy:
            config["security"]["suspicious_commands"] = policy["suspicious_commands"]
        if "redaction_patterns" in policy:
            config["redaction"]["patterns"] = policy["redaction_patterns"]
        log.info("Policy synced from %s (updated_at: %s)", url, policy.get("updated_at", "?"))
    except Exception as e:
        log.warning("Policy sync failed: %s", e)

    return config


# ---------------------------------------------------------------------------
# Dead letter queue (feature flag: dead_letter_queue)
# ---------------------------------------------------------------------------

class DeadLetterQueue:
    def __init__(self, config):
        self.enabled = _feat(config, "dead_letter_queue")
        self.directory = os.path.expanduser(config.get("dlq", {}).get("directory", "~/.claude-replay/dlq/"))
        if self.enabled:
            os.makedirs(self.directory, exist_ok=True)

    def write(self, documents, error_reason):
        if not self.enabled or not documents:
            return
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        filepath = os.path.join(self.directory, f"dlq-{ts}-{os.getpid()}.ndjson")
        try:
            with open(filepath, "a", encoding="utf-8") as f:
                for doc in documents:
                    entry = {k: v for k, v in doc.items() if k != "_doc_id"}
                    entry["_dlq_error"] = str(error_reason)
                    entry["_dlq_timestamp"] = datetime.now(timezone.utc).isoformat()
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            log.info("DLQ: wrote %d documents to %s", len(documents), filepath)
        except OSError as e:
            log.error("DLQ write failed: %s", e)

    def list_files(self):
        if not os.path.isdir(self.directory):
            return []
        return sorted(Path(self.directory).glob("dlq-*.ndjson"))

    def read_all(self):
        docs = []
        for f in self.list_files():
            with open(f, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            docs.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        return docs


# ---------------------------------------------------------------------------
# Transport layer
# ---------------------------------------------------------------------------

class Transport:
    def ship(self, documents):
        raise NotImplementedError


class OpenSearchTransport(Transport):
    def __init__(self, config):
        self.url = config["endpoint"]["url"].rstrip("/")
        self.base_index = config["endpoint"].get("index", "agent-sessions")
        self.auth = config["endpoint"].get("auth", {})
        self.timeout = config["endpoint"].get("timeout_seconds", 30)
        self.verify_ssl = config["endpoint"].get("verify_ssl", True)
        self.max_retries = config.get("shipping", {}).get("max_retries", 3)
        self.backoff = config.get("shipping", {}).get("retry_backoff_seconds", 2)
        self.compress = _feat(config, "compression")
        self.multi_tenant = _feat(config, "multi_tenant")
        self.mt_config = config.get("multi_tenant", {})
        self.dlq = DeadLetterQueue(config)
        self._oidc = None
        if _feat(config, "oidc_auth"):
            self._oidc = OIDCTokenManager(self.auth)

    def _resolve_index(self, doc):
        if self.multi_tenant and self.mt_config.get("strategy") == "index_per_tenant":
            org = doc.get("organization", "default")
            prefix = self.mt_config.get("index_prefix", "agent-sessions")
            return f"{prefix}-{org}" if org else self.base_index
        return self.base_index

    def _build_headers(self):
        headers = {"Content-Type": "application/x-ndjson"}
        if self._oidc:
            token = self._oidc.get_access_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
        else:
            auth_type = self.auth.get("type", "none")
            if auth_type == "api_key":
                headers["Authorization"] = f"ApiKey {self.auth['api_key']}"
            elif auth_type == "basic":
                import base64
                creds = base64.b64encode(
                    f"{self.auth['username']}:{self.auth['password']}".encode()
                ).decode()
                headers["Authorization"] = f"Basic {creds}"
        if self.compress:
            headers["Content-Encoding"] = "gzip"
        return headers

    def ship(self, documents):
        if not documents:
            return 0, 0
        lines = []
        for doc in documents:
            doc_id = doc.get("_doc_id", "")
            index = self._resolve_index(doc)
            action = json.dumps({"index": {"_index": index, "_id": doc_id}})
            body = json.dumps({k: v for k, v in doc.items() if k != "_doc_id"}, ensure_ascii=False)
            lines.append(action)
            lines.append(body)
        payload_bytes = ("\n".join(lines) + "\n").encode("utf-8")

        if self.compress:
            payload_bytes = gzip.compress(payload_bytes)

        bulk_url = f"{self.url}/_bulk"
        headers = self._build_headers()

        for attempt in range(self.max_retries):
            try:
                req = urllib.request.Request(bulk_url, data=payload_bytes, headers=headers, method="POST")
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

        # All retries failed → DLQ
        self.dlq.write(documents, "All retries exhausted")
        return 0, len(documents)


class FileExportTransport(Transport):
    def __init__(self, config):
        self.directory = config.get("file_export", {}).get("directory", "./shipped-sessions/")
        self.encrypt = _feat(config, "encryption_at_rest")
        self.key_file = os.path.expanduser(config.get("encryption", {}).get("key_file", ""))
        self.compress = _feat(config, "compression")
        self.dlq = DeadLetterQueue(config)
        os.makedirs(self.directory, exist_ok=True)

    def _get_encryption_key(self):
        if not self.encrypt or not self.key_file:
            return None
        try:
            with open(self.key_file, "rb") as f:
                return f.read().strip()
        except OSError:
            log.warning("Encryption key file not found: %s", self.key_file)
            return None

    def ship(self, documents):
        if not documents:
            return 0, 0
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        ext = ".ndjson"
        if self.compress:
            ext += ".gz"
        if self.encrypt:
            ext += ".enc"
        filename = f"ship-{ts}-{os.getpid()}{ext}"
        filepath = os.path.join(self.directory, filename)
        try:
            raw = ""
            for doc in documents:
                clean = {k: v for k, v in doc.items() if k != "_doc_id"}
                raw += json.dumps(clean, ensure_ascii=False) + "\n"
            data = raw.encode("utf-8")

            if self.compress:
                data = gzip.compress(data)

            if self.encrypt:
                key = self._get_encryption_key()
                if key:
                    try:
                        from cryptography.fernet import Fernet
                        f = Fernet(key)
                        data = f.encrypt(data)
                    except ImportError:
                        log.warning("cryptography library not installed, skipping encryption")
                    except Exception as e:
                        log.warning("Encryption failed: %s", e)

            with open(filepath, "wb") as f:
                f.write(data)
            log.info("Exported %d documents to %s", len(documents), filepath)
            return len(documents), 0
        except OSError as e:
            log.error("File export failed: %s", e)
            self.dlq.write(documents, str(e))
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
    if ep_type in ("opensearch", "rest"):
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


def process_message(message, message_index, envelope, config, redactor, oidc_mgr=None):
    """Process a single common-model message into a shippable document."""
    msg = dict(message)
    msg["message_index"] = message_index

    max_size = config.get("shipping", {}).get("max_field_size", 10240)
    msg = truncate_fields(msg, max_size)
    msg = filter_scope(msg, config)
    msg = redact_message(msg, redactor)

    security_flags = analyze_security(message, config)
    banned_hits = detect_banned_words(message, config)

    doc = {}
    doc.update(envelope)

    # Override user_id from OIDC token if available
    if oidc_mgr:
        oidc_user = oidc_mgr.get_user_id()
        if oidc_user:
            doc["user_id"] = oidc_user

    doc.update(msg)
    doc["timestamp_original"] = message.get("timestamp", "")
    doc["timestamp_shipped"] = datetime.now(timezone.utc).isoformat()
    doc["security_flags"] = security_flags
    doc["banned_word_hits"] = banned_hits
    doc["_doc_id"] = _doc_id(envelope["session_id"], message_index)

    # DLS fields
    if _feat(config, "dls"):
        doc["_dls_user"] = doc.get("user_id", "")
        doc["_dls_org"] = doc.get("organization", "")
        if oidc_mgr:
            doc["_dls_roles"] = oidc_mgr.get_roles()
        else:
            doc["_dls_roles"] = config.get("identity", {}).get("roles", [])

    return doc, security_flags, banned_hits


# ---------------------------------------------------------------------------
# Batch shipper
# ---------------------------------------------------------------------------

def ship_batch_file(session_path, agent, config, transport, offset_tracker, redactor, oidc_mgr=None):
    adapter = _get_adapter(agent)

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

    _, shipped_count = offset_tracker.get_offset(session_path)
    all_messages = model.get("messages", [])
    new_messages = all_messages[shipped_count:]

    if not new_messages:
        log.info("  %s: no new messages (already shipped %d)", Path(session_path).name, shipped_count)
        return 0

    batch = []
    batch_size = config.get("shipping", {}).get("batch_size", 50)
    total_shipped = 0

    for i, msg in enumerate(new_messages):
        msg_idx = shipped_count + i + 1
        doc, sec_flags, banned = process_message(msg, msg_idx, envelope, config, redactor, oidc_mgr)
        batch.append(doc)

        # Webhook on alerts
        if sec_flags or banned:
            send_webhook(config, sec_flags, doc)

        if len(batch) >= batch_size:
            ok, err = transport.ship(batch)
            total_shipped += ok
            batch = []

    if batch:
        ok, err = transport.ship(batch)
        total_shipped += ok

    new_total = shipped_count + len(new_messages)
    offset_tracker.update_offset(session_path, 0, new_total)
    offset_tracker.save()

    log.info("  %s: shipped %d new messages (total: %d)", Path(session_path).name, total_shipped, new_total)
    return total_shipped


def cmd_batch(args):
    config = load_config(args.config)

    if not _feat(config, "shipping_enabled"):
        log.info("Shipping is disabled (features.shipping_enabled = false)")
        return

    config = sync_policy(config)
    transport = create_transport(config, dry_run=args.dry_run)
    offset_tracker = OffsetTracker(config["state_file"])
    redactor = build_redactor(config)
    oidc_mgr = OIDCTokenManager(config["endpoint"]["auth"]) if _feat(config, "oidc_auth") else None

    if _feat(config, "multi_tenant") and not config.get("identity", {}).get("organization"):
        log.error("multi_tenant is enabled but identity.organization is empty")
        return

    agents = [args.agent] if args.agent else config.get("watch", {}).get("agents", ["claude", "codex", "gemini"])

    if args.input:
        agent = args.agent or "claude"
        for path in args.input:
            if os.path.isfile(path):
                ship_batch_file(path, agent, config, transport, offset_tracker, redactor, oidc_mgr)
            else:
                log.error("File not found: %s", path)
    else:
        total = 0
        for agent in agents:
            adapter = _get_adapter(agent)
            sessions = adapter.discover_sessions()
            log.info("Found %d %s sessions", len(sessions), agent)
            for session in sessions:
                shipped = ship_batch_file(
                    session["path"], agent, config, transport, offset_tracker, redactor, oidc_mgr
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
        self._known_files = {}

    def poll(self):
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
        "role": role, "text": text.strip(), "tool_uses": tool_uses,
        "tool_results": tool_results, "thinking": thinking,
        "timestamp": data.get("timestamp", ""),
    }
    if entry["text"] or entry["tool_uses"] or entry["tool_results"] or entry["thinking"]:
        return entry
    return None


def _parse_line_codex(line_text, adapter):
    try:
        data = json.loads(line_text)
    except json.JSONDecodeError:
        return None
    if data.get("type") not in ("message",):
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
        "role": role, "text": text.strip() if text else "",
        "tool_uses": tool_uses, "tool_results": tool_results,
        "thinking": thinking, "timestamp": data.get("timestamp", ""),
    }
    if entry["text"] or entry["tool_uses"] or entry["tool_results"] or entry["thinking"]:
        return entry
    return None


def ship_stream_file(file_path, agent, config, transport, offset_tracker, redactor, oidc_mgr=None):
    byte_offset, line_count = offset_tracker.get_offset(file_path)

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
                doc, sec_flags, banned = process_message(msg, msg_idx, envelope, config, redactor, oidc_mgr)
                batch.append(doc)
                if sec_flags or banned:
                    send_webhook(config, sec_flags, doc)
            ok, _ = transport.ship(batch)
            offset_tracker.update_offset(file_path, 0, len(all_messages))
            offset_tracker.save()
            return ok
        except Exception as e:
            log.warning("Gemini parse error: %s", e)
            return 0

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

                doc, sec_flags, banned = process_message(msg, msg_idx, envelope, config, redactor, oidc_mgr)
                batch.append(doc)

                if sec_flags or banned:
                    send_webhook(config, sec_flags, doc)

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
    global _shutdown
    config = load_config(args.config)

    if not _feat(config, "shipping_enabled"):
        log.info("Shipping is disabled (features.shipping_enabled = false)")
        return

    config = sync_policy(config)
    transport = create_transport(config)
    offset_tracker = OffsetTracker(config["state_file"])
    redactor = build_redactor(config)
    oidc_mgr = OIDCTokenManager(config["endpoint"]["auth"]) if _feat(config, "oidc_auth") else None

    agents = args.agent or config.get("watch", {}).get("agents", ["claude", "codex", "gemini"])
    watcher = FileWatcher(agents, config)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    log.info("Watch daemon started (agents: %s, polling: %ds)",
             ", ".join(agents), watcher.polling_interval)

    watcher.poll()
    policy_sync_at = time.monotonic()
    policy_interval = config.get("policy_sync", {}).get("interval_seconds", 300)

    while not _shutdown:
        # Periodic policy sync
        if _feat(config, "auto_policy_sync") and time.monotonic() - policy_sync_at > policy_interval:
            config = sync_policy(config)
            redactor = build_redactor(config)
            policy_sync_at = time.monotonic()

        changed = watcher.poll()
        for path, agent, is_new in changed:
            try:
                ship_stream_file(path, agent, config, transport, offset_tracker, redactor, oidc_mgr)
            except Exception as e:
                log.error("Error shipping %s: %s", path, e)
        time.sleep(watcher.polling_interval)

    log.info("Watch daemon stopped")


# ---------------------------------------------------------------------------
# Lookup: OpenSearch → local session
# ---------------------------------------------------------------------------

def cmd_lookup(args):
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
            agent = "claude"
            if "codex" in file_path.lower():
                agent = "codex"
            elif "gemini" in file_path.lower():
                agent = "gemini"
            cmd = [sys.executable, str(_script_dir / "log-replay.py"),
                   "--agent", agent, file_path, "-f", "player"]
            if args.message:
                cmd += ["--render-arg", "--range", "--render-arg", str(args.message)]
            subprocess.run(cmd)
    else:
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
# Enterprise commands
# ---------------------------------------------------------------------------

def cmd_retry_dlq(args):
    """Retry shipping dead letter queue entries."""
    config = load_config(args.config)
    dlq = DeadLetterQueue(config)
    docs = dlq.read_all()
    if not docs:
        print("DLQ is empty.")
        return
    print(f"Found {len(docs)} DLQ entries. Re-shipping...")

    transport = create_transport(config)
    # Strip DLQ metadata
    clean_docs = []
    for doc in docs:
        clean = {k: v for k, v in doc.items() if not k.startswith("_dlq_")}
        clean_docs.append(clean)

    ok, err = transport.ship(clean_docs)
    print(f"Shipped: {ok}, Failed: {err}")

    if ok > 0 and err == 0:
        # Clean up DLQ files
        for f in dlq.list_files():
            f.unlink()
        print("DLQ cleared.")


def cmd_policy_sync(args):
    """One-shot policy sync."""
    config = load_config(args.config)
    config = sync_policy(config)
    print("Policy sync complete.")
    bw = config.get("security", {}).get("banned_words", [])
    sp = config.get("security", {}).get("sensitive_paths", [])
    print(f"  Banned words: {len(bw)}")
    print(f"  Sensitive paths: {len(sp)}")


def cmd_decrypt(args):
    """Decrypt an encrypted export file."""
    key_file = os.path.expanduser(args.key or "~/.claude-replay/encryption.key")
    try:
        with open(key_file, "rb") as f:
            key = f.read().strip()
    except OSError:
        print(f"Key file not found: {key_file}")
        return

    try:
        from cryptography.fernet import Fernet
    except ImportError:
        print("cryptography library not installed. Install with: pip install cryptography")
        return

    try:
        with open(args.input, "rb") as f:
            encrypted = f.read()
        fernet = Fernet(key)
        decrypted = fernet.decrypt(encrypted)

        # Check if gzip compressed
        if decrypted[:2] == b'\x1f\x8b':
            decrypted = gzip.decompress(decrypted)

        with open(args.output, "wb") as f:
            f.write(decrypted)
        print(f"Decrypted: {args.input} -> {args.output}")
    except Exception as e:
        print(f"Decryption failed: {e}")


def cmd_validate_config(args):
    """Validate config and test connectivity."""
    config = load_config(args.config)
    features = config.get("features", {})

    print("Feature flags:")
    for k, v in sorted(features.items()):
        status = "ON" if v else "off"
        print(f"  {k:25} {status}")

    print(f"\nEndpoint: {config['endpoint']['type']}")
    print(f"  URL: {config['endpoint'].get('url', '(none)')}")
    print(f"  Auth: {config['endpoint']['auth']['type']}")
    print(f"  Index: {config['endpoint'].get('index', '?')}")

    if config["endpoint"]["type"] in ("opensearch", "rest") and config["endpoint"].get("url"):
        print("\nConnectivity test...")
        try:
            url = config["endpoint"]["url"].rstrip("/")
            req = urllib.request.Request(f"{url}/_cluster/health", method="GET")
            if not config["endpoint"].get("verify_ssl", True):
                import ssl
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                resp = urllib.request.urlopen(req, timeout=10, context=ctx)
            else:
                resp = urllib.request.urlopen(req, timeout=10)
            health = json.loads(resp.read().decode())
            print(f"  Cluster: {health.get('cluster_name', '?')} ({health.get('status', '?')})")
            print("  Connection: OK")
        except Exception as e:
            print(f"  Connection: FAILED ({e})")

    if features.get("multi_tenant"):
        org = config.get("identity", {}).get("organization", "")
        print(f"\nMulti-tenant: strategy={config['multi_tenant']['strategy']}, org={org or '(empty!)'}")
        if not org:
            print("  WARNING: organization is empty but multi_tenant is enabled")

    print("\nConfig validation complete.")


# ---------------------------------------------------------------------------
# Init config & status
# ---------------------------------------------------------------------------

def cmd_init_config(args):
    output = args.output or "shipper-config.json"
    if os.path.exists(output) and not args.force:
        print(f"{output} already exists. Use --force to overwrite.")
        return
    with open(output, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
    print(f"Config written to {output}")

    if args.generate_key:
        key_path = os.path.expanduser(DEFAULT_CONFIG["encryption"]["key_file"])
        os.makedirs(os.path.dirname(key_path) or ".", exist_ok=True)
        try:
            from cryptography.fernet import Fernet
            key = Fernet.generate_key()
            with open(key_path, "wb") as f:
                f.write(key)
            print(f"Encryption key written to {key_path}")
        except ImportError:
            print("cryptography library not installed. Skipping key generation.")


def cmd_status(args):
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

    print(f"\n  Total: {len(files)} sessions, {total_msgs} messages shipped")

    # DLQ status
    dlq = DeadLetterQueue(config)
    dlq_files = dlq.list_files()
    if dlq_files:
        dlq_docs = dlq.read_all()
        print(f"  DLQ: {len(dlq_files)} files, {len(dlq_docs)} entries pending retry")
    print()


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
    init_p.add_argument("--generate-key", action="store_true", help="generate encryption key")

    # status
    sub.add_parser("status", help="Show shipping state and stats")

    # Enterprise commands
    sub.add_parser("retry-dlq", help="Re-ship dead letter queue entries")
    sub.add_parser("policy-sync", help="One-shot policy sync from central endpoint")

    decrypt_p = sub.add_parser("decrypt", help="Decrypt an encrypted export file")
    decrypt_p.add_argument("--input", "-i", required=True, help="encrypted file")
    decrypt_p.add_argument("--output", "-o", required=True, help="output file")
    decrypt_p.add_argument("--key", help="encryption key file path")

    sub.add_parser("validate-config", help="Validate config and test connectivity")

    args = parser.parse_args()

    commands = {
        "batch": cmd_batch,
        "watch": cmd_watch,
        "lookup": cmd_lookup,
        "init-config": cmd_init_config,
        "status": cmd_status,
        "retry-dlq": cmd_retry_dlq,
        "policy-sync": cmd_policy_sync,
        "decrypt": cmd_decrypt,
        "validate-config": cmd_validate_config,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
