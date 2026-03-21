[日本語版](ja/spec-enterprise-shipping.md)

# Enterprise Session Shipping Specification

## 1. Purpose

This specification defines the enterprise-grade session shipping capabilities of `session-shipper.py`. It covers all scenarios from single-developer local use to multi-tenant SaaS deployments, controlled entirely by configuration flags.

All features described here are **implemented but gated by config flags** — disabled features have zero runtime cost (no imports, no network calls, no processing).

## 2. Deployment scenarios

### Scenario A: Solo developer (local audit)
- Ship to local NDJSON files for personal record-keeping
- No authentication, no network
- Config: `endpoint.type = "file"`

### Scenario B: Small team (shared OpenSearch)
- Ship to a team OpenSearch instance
- Basic auth or API key
- All team members write to the same index
- Config: `endpoint.type = "opensearch"`, `auth.type = "basic"` or `"api_key"`

### Scenario C: Enterprise (managed OpenSearch with SSO)
- Ship to corporate OpenSearch with SAML/OIDC authentication
- Document Level Security: users see only their own data, admins see all
- Per-team or per-org index separation
- Config: `endpoint.type = "opensearch"`, `auth.type = "oidc"`, `features.dls = true`

### Scenario D: SaaS / Multi-tenant
- Central OpenSearch cluster serving multiple organizations
- Tenant isolation via `organization` field + index-per-tenant or DLS
- Webhook notifications for security alerts
- Config: `features.multi_tenant = true`, `features.webhooks = true`

### Scenario E: Compliance-only (no data, metadata only)
- Ship only metadata (user_id, timestamps, tool names, security_flags) without message content
- For environments where session content must not leave the machine
- Config: `scope.include_text = false`, `scope.include_tool_result = false`, `scope.metadata_only = true`

## 3. Feature flags

All enterprise features are controlled via the `features` section of `shipper-config.json`:

```json
{
  "features": {
    "shipping_enabled": true,
    "security_analysis": true,
    "banned_word_detection": true,
    "redaction": true,
    "oidc_auth": false,
    "dls": false,
    "multi_tenant": false,
    "webhooks": false,
    "metadata_only": false,
    "auto_policy_sync": false,
    "compression": false,
    "encryption_at_rest": false,
    "dead_letter_queue": true
  }
}
```

| Flag | Default | Description |
|------|---------|-------------|
| `shipping_enabled` | true | Master kill switch. false = nothing ships |
| `security_analysis` | true | Analyze tool_uses for security flags |
| `banned_word_detection` | true | Scan messages for banned words |
| `redaction` | true | Apply regex redaction patterns before shipping |
| `oidc_auth` | false | Enable OIDC/SAML token-based authentication |
| `dls` | false | Tag documents for Document Level Security |
| `multi_tenant` | false | Enable organization-based tenant isolation |
| `webhooks` | false | Send webhook on security alerts / banned words |
| `metadata_only` | false | Ship only metadata, strip all content |
| `auto_policy_sync` | false | Periodically fetch policy (banned words, etc.) from server |
| `compression` | false | gzip compress payloads before shipping |
| `encryption_at_rest` | false | Encrypt exported NDJSON files at rest |
| `dead_letter_queue` | true | Write failed shipments to DLQ for retry |

## 4. Authentication

### 4.1 None (default)
No authentication headers. Suitable for local file export or unauthenticated OpenSearch.

### 4.2 API Key
```json
{ "auth": { "type": "api_key", "api_key": "VnVhQ2calTI0...base64..." } }
```
Sent as `Authorization: ApiKey <value>`.

### 4.3 Basic Auth
```json
{ "auth": { "type": "basic", "username": "shipper", "password": "..." } }
```
Sent as `Authorization: Basic <base64(user:pass)>`.

### 4.4 OIDC / SAML (feature flag: `oidc_auth`)
```json
{
  "auth": {
    "type": "oidc",
    "issuer_url": "https://idp.corp.example.com/realms/engineering",
    "client_id": "session-shipper",
    "client_secret": "...",
    "scopes": ["openid", "profile"],
    "token_cache_path": "~/.claude-replay/oidc-token.json"
  }
}
```

Flow:
1. On first ship, perform OIDC Client Credentials flow (or Device Code flow for interactive)
2. Cache access token + refresh token to `token_cache_path`
3. On subsequent ships, use cached token; refresh if expired
4. Send as `Authorization: Bearer <access_token>`
5. **user_id is extracted from the OIDC token claims** (sub or preferred_username) — overrides config `identity.user_id`

This ensures user_id authenticity is guaranteed by the IdP, not self-reported.

## 5. Document Level Security (feature flag: `dls`)

When enabled, each shipped document gets additional DLS fields:

```json
{
  "_dls_user": "tanaka.taro",
  "_dls_roles": ["engineering", "team-payment"],
  "_dls_org": "acme-corp"
}
```

OpenSearch index must have a DLS policy like:
```json
{
  "bool": {
    "should": [
      { "term": { "_dls_user": "${user.name}" } },
      { "term": { "_dls_roles": "${user.roles}" } }
    ]
  }
}
```

The shipper populates `_dls_roles` from:
1. OIDC token claims (`groups` or `roles` claim) if `oidc_auth` is enabled
2. Config `identity.roles` as fallback
3. Empty array if neither

## 6. Multi-tenant isolation (feature flag: `multi_tenant`)

When enabled:
- `organization` field is **required** (error if empty)
- Index name includes org: `{index_prefix}-{organization}` (e.g., `agent-sessions-acme`)
- Each tenant gets its own OpenSearch index for complete data isolation
- Alternative: single index with DLS org-based filtering (config `multi_tenant_strategy: "dls"` vs `"index_per_tenant"`)

```json
{
  "multi_tenant": {
    "strategy": "index_per_tenant",
    "index_prefix": "agent-sessions"
  }
}
```

## 7. Webhooks (feature flag: `webhooks`)

Send HTTP POST notifications when security events or banned word hits are detected.

```json
{
  "webhooks": {
    "url": "https://hooks.slack.example.com/services/T.../B.../xxx",
    "events": ["security_high", "security_medium", "banned_word"],
    "format": "slack",
    "headers": { "X-Custom-Header": "value" },
    "timeout_seconds": 10,
    "max_retries": 2
  }
}
```

### Webhook formats

**`slack`** format:
```json
{
  "text": ":warning: Security alert",
  "blocks": [
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": "*Security Alert*\n*User:* tanaka.taro\n*Category:* sensitive_file_read\n*Detail:* Read /etc/shadow\n*Session:* f91386e7\n*Message:* #42"
      }
    }
  ]
}
```

**`generic`** format:
```json
{
  "event_type": "security_alert",
  "timestamp": "2026-03-22T10:30:00Z",
  "user_id": "tanaka.taro",
  "organization": "acme-corp",
  "session_id": "f91386e7",
  "message_index": 42,
  "alerts": [
    { "severity": "high", "category": "sensitive_file_read", "detail": "Read /etc/shadow" }
  ]
}
```

## 8. Policy sync (feature flag: `auto_policy_sync`)

Periodically fetch updated policies (banned words, sensitive paths, redaction patterns) from a central endpoint.

```json
{
  "policy_sync": {
    "url": "https://opensearch.corp.example.com/agent-policy/_doc/current",
    "interval_seconds": 300,
    "auth_same_as_endpoint": true
  }
}
```

The policy document structure:
```json
{
  "banned_words": ["password", "secret_key", "internal_only"],
  "sensitive_paths": ["/etc/shadow", ".env", "credentials.json"],
  "suspicious_commands": ["curl ", "wget ", "nc "],
  "redaction_patterns": [
    { "name": "api_key", "regex": "...", "replacement": "***REDACTED***" }
  ],
  "updated_at": "2026-03-22T00:00:00Z"
}
```

This allows security teams to update policies centrally without redeploying shipper configs.

## 9. Dead letter queue (feature flag: `dead_letter_queue`)

Failed shipments are written to a local DLQ file for manual or automatic retry.

- Location: `~/.claude-replay/dlq/` directory
- Format: `dlq-{timestamp}.ndjson`
- Each DLQ entry includes the original document + error reason
- CLI command: `session-shipper.py retry-dlq` to re-ship failed documents

```json
{
  "_dlq_error": "HTTP 503 Service Unavailable",
  "_dlq_timestamp": "2026-03-22T10:30:00Z",
  "_dlq_attempts": 3,
  "user_id": "tanaka.taro",
  "...": "original document fields"
}
```

## 10. Compression (feature flag: `compression`)

When enabled, gzip-compress the HTTP request body before shipping.
- Adds `Content-Encoding: gzip` header
- Reduces bandwidth for large batches (tool_result content can be huge)
- File export also writes `.ndjson.gz` instead of `.ndjson`

## 11. Encryption at rest (feature flag: `encryption_at_rest`)

When enabled with file export, encrypt NDJSON files using Fernet symmetric encryption (from `cryptography` library, optional dep).

```json
{
  "encryption": {
    "key_file": "~/.claude-replay/encryption.key"
  }
}
```

- `init-config --generate-key` creates a new Fernet key
- Encrypted files use `.ndjson.enc` extension
- `session-shipper.py decrypt --input file.ndjson.enc --output file.ndjson` for recovery

## 12. Metadata-only mode (feature flag: `metadata_only`)

For environments where session **content** must not leave the machine, but **activity metadata** must be audited.

Shipped document in metadata-only mode:
```json
{
  "user_id": "tanaka.taro",
  "hostname": "CORP-PC-1234",
  "agent": "claude",
  "session_id": "f91386e7",
  "project": "payment-service",
  "message_index": 42,
  "role": "assistant",
  "timestamp_original": "2026-03-22T10:30:00Z",
  "timestamp_shipped": "2026-03-22T10:31:00Z",
  "text_length": 1523,
  "thinking_count": 2,
  "tool_use_names": ["Read", "Bash", "Write"],
  "tool_result_count": 3,
  "security_flags": [{"severity": "high", "category": "sensitive_file_read", "detail": "Read /etc/shadow"}],
  "banned_word_hits": [{"word": "password", "field": "text", "count": 1}]
}
```

Note: `security_flags` and `banned_word_hits` are **always included** even in metadata-only mode — they contain the flag/category but not the actual content.

## 13. Complete configuration schema

```json
{
  "features": {
    "shipping_enabled": true,
    "security_analysis": true,
    "banned_word_detection": true,
    "redaction": true,
    "oidc_auth": false,
    "dls": false,
    "multi_tenant": false,
    "webhooks": false,
    "metadata_only": false,
    "auto_policy_sync": false,
    "compression": false,
    "encryption_at_rest": false,
    "dead_letter_queue": true
  },
  "endpoint": {
    "type": "file",
    "url": "https://opensearch.example.com:9200",
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
      "token_cache_path": "~/.claude-replay/oidc-token.json"
    },
    "timeout_seconds": 30,
    "verify_ssl": true
  },
  "file_export": {
    "directory": "./shipped-sessions/",
    "format": "ndjson"
  },
  "identity": {
    "user_id": "",
    "hostname": "",
    "organization": "",
    "roles": []
  },
  "scope": {
    "include_text": true,
    "include_thinking": false,
    "include_tool_use": true,
    "include_tool_result": false
  },
  "redaction": {
    "patterns": [
      { "name": "api_key", "regex": "(?i)(api[_-]?key|token|secret)\\s*[:=]\\s*['\"]?([A-Za-z0-9_\\-]{20,})", "replacement": "$1=***REDACTED***" },
      { "name": "email", "regex": "[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}", "replacement": "***EMAIL***" }
    ]
  },
  "security": {
    "sensitive_paths": ["/etc/shadow", "/etc/passwd", ".env", "credentials", "id_rsa", ".ssh/"],
    "suspicious_commands": ["curl ", "wget ", "nc ", "ncat ", "base64 ", "eval "],
    "banned_words": []
  },
  "webhooks": {
    "url": "",
    "events": ["security_high", "banned_word"],
    "format": "generic",
    "headers": {},
    "timeout_seconds": 10,
    "max_retries": 2
  },
  "policy_sync": {
    "url": "",
    "interval_seconds": 300,
    "auth_same_as_endpoint": true
  },
  "encryption": {
    "key_file": "~/.claude-replay/encryption.key"
  },
  "multi_tenant": {
    "strategy": "index_per_tenant",
    "index_prefix": "agent-sessions"
  },
  "watch": {
    "agents": ["claude", "codex", "gemini"],
    "polling_interval_seconds": 2
  },
  "shipping": {
    "batch_size": 50,
    "flush_interval_seconds": 5,
    "max_retries": 3,
    "retry_backoff_seconds": 2,
    "max_field_size": 10240
  },
  "dlq": {
    "directory": "~/.claude-replay/dlq/"
  },
  "state_file": "~/.claude-replay/shipper-state.json"
}
```

## 14. CLI commands

```bash
# Core commands (existing)
session-shipper.py batch [--agent X] [--input FILE...] [--dry-run]
session-shipper.py watch [--agent X...]
session-shipper.py lookup [--session-id X] [--open-player] [-m N]
session-shipper.py status
session-shipper.py init-config [-o FILE] [--force] [--generate-key]

# Enterprise commands (new)
session-shipper.py retry-dlq                    # Re-ship dead letter queue
session-shipper.py policy-sync                  # One-shot policy fetch
session-shipper.py decrypt --input X --output Y # Decrypt encrypted export
session-shipper.py validate-config              # Validate config + connectivity test
```

## 15. OpenSearch index template (reference)

Recommended index template for `agent-sessions-*`:

```json
{
  "index_patterns": ["agent-sessions-*"],
  "template": {
    "settings": {
      "number_of_shards": 2,
      "number_of_replicas": 1
    },
    "mappings": {
      "properties": {
        "user_id": { "type": "keyword" },
        "hostname": { "type": "keyword" },
        "organization": { "type": "keyword" },
        "agent": { "type": "keyword" },
        "session_id": { "type": "keyword" },
        "project": { "type": "keyword" },
        "role": { "type": "keyword" },
        "message_index": { "type": "integer" },
        "timestamp_original": { "type": "date" },
        "timestamp_shipped": { "type": "date" },
        "text": { "type": "text", "analyzer": "standard" },
        "thinking": { "type": "text" },
        "tool_uses": { "type": "nested", "properties": {
          "name": { "type": "keyword" },
          "input": { "type": "object", "enabled": false }
        }},
        "security_flags": { "type": "nested", "properties": {
          "severity": { "type": "keyword" },
          "category": { "type": "keyword" },
          "detail": { "type": "text" }
        }},
        "banned_word_hits": { "type": "nested", "properties": {
          "word": { "type": "keyword" },
          "field": { "type": "keyword" },
          "count": { "type": "integer" }
        }},
        "_dls_user": { "type": "keyword" },
        "_dls_roles": { "type": "keyword" },
        "_dls_org": { "type": "keyword" }
      }
    }
  }
}
```
