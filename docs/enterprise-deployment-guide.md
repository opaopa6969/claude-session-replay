[日本語版](ja/enterprise-deployment-guide.md)

# Enterprise Deployment Guide

This guide covers deploying session-shipper in an enterprise environment with OpenSearch, from initial setup to production operation.

## 1. Architecture overview

```text
Developer Workstation (Windows/WSL2, Linux, macOS)
  ├─ Claude Code / Codex CLI / Gemini CLI
  │     └─ writes session logs (~/.claude/projects/*/*.jsonl)
  │
  └─ session-shipper.py watch (daemon)
        ├─ monitors session log directories
        ├─ parses new messages in real-time
        ├─ applies: redaction → scope filter → security analysis
        └─ ships to ──────────────────────────────────────────┐
                                                               │
                                                               ▼
                                               ┌──────────────────────┐
                                               │  OpenSearch Cluster   │
                                               │  (corporate / SaaS)  │
                                               │                      │
                                               │  ├─ agent-sessions-* │
                                               │  ├─ Security Plugin  │
                                               │  │  (SAML/OIDC/DLS) │
                                               │  └─ ISM Policies     │
                                               └──────────┬───────────┘
                                                           │
                                          ┌────────────────┼────────────────┐
                                          ▼                ▼                ▼
                                   OpenSearch         Alerting         Compliance
                                   Dashboards         Plugin           Reports
                                   (Kibana-like)      (Slack/email)
                                          │
                                          ▼
                                   session-shipper.py lookup
                                   (OpenSearch result → local Player)
```

## 2. Prerequisites

### OpenSearch cluster

| Component | Version | Notes |
|-----------|---------|-------|
| OpenSearch | 2.x+ | Cluster or single-node |
| Security Plugin | included | For SAML/OIDC, DLS, RBAC |
| Alerting Plugin | included | For automated security alerts |
| ISM Plugin | included | For index lifecycle management |

### Developer workstation

| Requirement | Notes |
|-------------|-------|
| Python 3.6+ | Standard library only for core features |
| `cryptography` (optional) | Only if `encryption_at_rest` is enabled |
| Network access | To OpenSearch endpoint (HTTPS) |

## 3. OpenSearch cluster setup

### 3.1 Create index template

Apply this template before shipping any data. It defines field types for optimal search and aggregation.

```bash
curl -X PUT "https://opensearch.corp.example.com:9200/_index_template/agent-sessions" \
  -H 'Content-Type: application/json' \
  -u admin:admin \
  -d '{
  "index_patterns": ["agent-sessions-*"],
  "priority": 100,
  "template": {
    "settings": {
      "number_of_shards": 2,
      "number_of_replicas": 1,
      "index.mapping.total_fields.limit": 2000
    },
    "mappings": {
      "properties": {
        "user_id":             { "type": "keyword" },
        "hostname":            { "type": "keyword" },
        "os_user":             { "type": "keyword" },
        "os_platform":         { "type": "keyword" },
        "organization":        { "type": "keyword" },
        "agent":               { "type": "keyword" },
        "session_id":          { "type": "keyword" },
        "session_file":        { "type": "keyword" },
        "project":             { "type": "keyword" },
        "role":                { "type": "keyword" },
        "message_index":       { "type": "integer" },
        "timestamp_original":  { "type": "date" },
        "timestamp_shipped":   { "type": "date" },
        "text":                { "type": "text", "analyzer": "standard",
                                 "fields": { "keyword": { "type": "keyword", "ignore_above": 256 } } },
        "thinking":            { "type": "text" },
        "tool_uses": {
          "type": "nested",
          "properties": {
            "name":  { "type": "keyword" },
            "id":    { "type": "keyword" },
            "input": { "type": "object", "enabled": false }
          }
        },
        "tool_results": {
          "type": "nested",
          "properties": {
            "content": { "type": "text" }
          }
        },
        "security_flags": {
          "type": "nested",
          "properties": {
            "severity": { "type": "keyword" },
            "category": { "type": "keyword" },
            "detail":   { "type": "text" }
          }
        },
        "banned_word_hits": {
          "type": "nested",
          "properties": {
            "word":  { "type": "keyword" },
            "field": { "type": "keyword" },
            "count": { "type": "integer" }
          }
        },
        "_dls_user":  { "type": "keyword" },
        "_dls_roles": { "type": "keyword" },
        "_dls_org":   { "type": "keyword" },
        "text_length":       { "type": "integer" },
        "thinking_count":    { "type": "integer" },
        "tool_use_names":    { "type": "keyword" },
        "tool_result_count": { "type": "integer" }
      }
    }
  }
}'
```

### 3.2 Configure SAML/OIDC authentication

In your OpenSearch `config.yml` (Security Plugin):

```yaml
config:
  dynamic:
    authc:
      oidc_auth:
        http_enabled: true
        transport_enabled: false
        order: 0
        http_authenticator:
          type: openid
          challenge: false
          config:
            openid_connect_url: "https://idp.corp.example.com/realms/engineering/.well-known/openid-configuration"
            subject_key: preferred_username
            roles_key: groups
        authentication_backend:
          type: noop
```

### 3.3 Configure Document Level Security

Create a role that restricts users to their own documents:

```bash
curl -X PUT "https://opensearch.corp.example.com:9200/_plugins/_security/api/roles/session_viewer" \
  -H 'Content-Type: application/json' \
  -u admin:admin \
  -d '{
  "cluster_permissions": [],
  "index_permissions": [{
    "index_patterns": ["agent-sessions-*"],
    "dls": "{\"bool\":{\"should\":[{\"term\":{\"_dls_user\":\"${user.name}\"}},{\"terms\":{\"_dls_roles\":${user.roles}}}]}}",
    "allowed_actions": ["read"]
  }]
}'
```

Create a shipper role with write access:

```bash
curl -X PUT "https://opensearch.corp.example.com:9200/_plugins/_security/api/roles/session_shipper" \
  -H 'Content-Type: application/json' \
  -u admin:admin \
  -d '{
  "cluster_permissions": ["cluster_monitor"],
  "index_permissions": [{
    "index_patterns": ["agent-sessions-*"],
    "allowed_actions": ["crud", "create_index"]
  }]
}'
```

Create an admin role (sees all data):

```bash
curl -X PUT "https://opensearch.corp.example.com:9200/_plugins/_security/api/roles/session_admin" \
  -H 'Content-Type: application/json' \
  -u admin:admin \
  -d '{
  "cluster_permissions": ["cluster_monitor"],
  "index_permissions": [{
    "index_patterns": ["agent-sessions-*"],
    "allowed_actions": ["read"]
  }]
}'
```

### 3.4 Configure Index State Management (ISM)

Automate index lifecycle (roll over, warm tier, delete):

```bash
curl -X PUT "https://opensearch.corp.example.com:9200/_plugins/_ism/policies/session-retention" \
  -H 'Content-Type: application/json' \
  -u admin:admin \
  -d '{
  "policy": {
    "description": "Session log retention policy",
    "default_state": "hot",
    "states": [
      {
        "name": "hot",
        "actions": [],
        "transitions": [{ "state_name": "warm", "conditions": { "min_index_age": "30d" } }]
      },
      {
        "name": "warm",
        "actions": [{ "read_only": {} }],
        "transitions": [{ "state_name": "delete", "conditions": { "min_index_age": "365d" } }]
      },
      {
        "name": "delete",
        "actions": [{ "delete": {} }]
      }
    ],
    "ism_template": {
      "index_patterns": ["agent-sessions-*"],
      "priority": 100
    }
  }
}'
```

### 3.5 Configure Alerting

Create a monitor for high-severity security events:

```bash
curl -X POST "https://opensearch.corp.example.com:9200/_plugins/_alerting/monitors" \
  -H 'Content-Type: application/json' \
  -u admin:admin \
  -d '{
  "type": "monitor",
  "name": "AI Session Security Alerts",
  "enabled": true,
  "schedule": { "period": { "interval": 1, "unit": "MINUTES" } },
  "inputs": [{
    "search": {
      "indices": ["agent-sessions-*"],
      "query": {
        "size": 0,
        "query": {
          "bool": {
            "must": [
              { "range": { "timestamp_shipped": { "gte": "now-5m" } } },
              { "nested": {
                "path": "security_flags",
                "query": { "term": { "security_flags.severity": "high" } }
              }}
            ]
          }
        },
        "aggs": {
          "by_user": { "terms": { "field": "user_id", "size": 10 } }
        }
      }
    }
  }],
  "triggers": [{
    "name": "High severity detected",
    "severity": "1",
    "condition": { "script": { "source": "ctx.results[0].hits.total.value > 0" } },
    "actions": [{
      "name": "Notify Slack",
      "destination_id": "<your-slack-destination-id>",
      "message_template": {
        "source": "High severity AI security event detected. {{ctx.results[0].hits.total.value}} events in last 5 minutes."
      }
    }]
  }]
}'
```

## 4. Shipper configuration per deployment scenario

### 4.1 Scenario B: Small team

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
    "dead_letter_queue": true
  },
  "endpoint": {
    "type": "opensearch",
    "url": "https://opensearch.team.example.com:9200",
    "index": "agent-sessions",
    "auth": {
      "type": "basic",
      "username": "shipper-user",
      "password": "changeme"
    },
    "verify_ssl": true
  },
  "identity": {
    "user_id": "",
    "organization": "team-alpha"
  }
}
```

### 4.2 Scenario C: Enterprise with SSO

```json
{
  "features": {
    "shipping_enabled": true,
    "security_analysis": true,
    "banned_word_detection": true,
    "redaction": true,
    "oidc_auth": true,
    "dls": true,
    "multi_tenant": false,
    "webhooks": true,
    "auto_policy_sync": true,
    "compression": true,
    "dead_letter_queue": true
  },
  "endpoint": {
    "type": "opensearch",
    "url": "https://opensearch.corp.example.com:9200",
    "index": "agent-sessions",
    "auth": {
      "type": "oidc",
      "issuer_url": "https://idp.corp.example.com/realms/engineering",
      "client_id": "session-shipper",
      "client_secret": "your-client-secret-here",
      "scopes": ["openid", "profile", "groups"],
      "token_cache_path": "~/.claude-replay/oidc-token.json"
    },
    "verify_ssl": true
  },
  "identity": {
    "organization": "engineering"
  },
  "webhooks": {
    "url": "https://hooks.slack.com/services/T.../B.../xxx",
    "events": ["security_high", "banned_word"],
    "format": "slack"
  },
  "policy_sync": {
    "url": "https://opensearch.corp.example.com:9200/agent-policy/_doc/current",
    "interval_seconds": 300,
    "auth_same_as_endpoint": true
  },
  "security": {
    "sensitive_paths": ["/etc/shadow", "/etc/passwd", ".env", "credentials", "id_rsa", ".ssh/", ".aws/credentials"],
    "suspicious_commands": ["curl ", "wget ", "nc ", "ncat ", "base64 ", "eval ", "ssh "],
    "banned_words": ["confidential", "internal_only", "do_not_share"]
  }
}
```

### 4.3 Scenario D: Multi-tenant SaaS

```json
{
  "features": {
    "shipping_enabled": true,
    "security_analysis": true,
    "banned_word_detection": true,
    "redaction": true,
    "oidc_auth": true,
    "dls": true,
    "multi_tenant": true,
    "webhooks": true,
    "auto_policy_sync": true,
    "compression": true,
    "metadata_only": false,
    "dead_letter_queue": true
  },
  "endpoint": {
    "type": "opensearch",
    "url": "https://opensearch.saas.example.com:9200",
    "auth": {
      "type": "oidc",
      "issuer_url": "https://auth.saas.example.com/realms/main",
      "client_id": "session-shipper",
      "client_secret": "...",
      "scopes": ["openid", "profile", "groups", "org"]
    }
  },
  "identity": {
    "organization": "acme-corp"
  },
  "multi_tenant": {
    "strategy": "index_per_tenant",
    "index_prefix": "agent-sessions"
  }
}
```

### 4.4 Scenario E: Compliance-only (metadata mode)

```json
{
  "features": {
    "shipping_enabled": true,
    "security_analysis": true,
    "banned_word_detection": true,
    "redaction": true,
    "metadata_only": true,
    "dead_letter_queue": true
  },
  "endpoint": {
    "type": "opensearch",
    "url": "https://opensearch.corp.example.com:9200",
    "index": "agent-sessions-metadata",
    "auth": {
      "type": "api_key",
      "api_key": "your-api-key"
    }
  },
  "scope": {
    "include_text": false,
    "include_thinking": false,
    "include_tool_use": true,
    "include_tool_result": false
  }
}
```

Metadata-only mode ships:
- Who did what, when (user_id, timestamp, role)
- Which tools were used (tool names only, no content)
- Security flags and banned word hits (categories, not content)
- Message lengths and counts (for activity metrics)

## 5. Workstation deployment

### 5.1 Initial setup

```bash
# Clone the tool
git clone https://github.com/opaopa6969/claude-session-replay.git
cd claude-session-replay

# Generate config
python3 session-shipper.py init-config -o shipper-config.json

# Edit shipper-config.json with your corporate settings
# (see Scenario B/C/D above)

# Validate configuration and test connectivity
python3 session-shipper.py validate-config
```

### 5.2 Batch ship existing sessions

```bash
# Dry run first (see what would be shipped)
python3 session-shipper.py batch --agent claude --dry-run 2>&1 | head -50

# Ship all existing sessions
python3 session-shipper.py batch --agent claude
python3 session-shipper.py batch --agent codex
python3 session-shipper.py batch --agent gemini

# Check status
python3 session-shipper.py status
```

### 5.3 Start watch daemon

```bash
# Foreground (for testing)
python3 session-shipper.py watch --agent claude codex gemini

# Background (production)
nohup python3 session-shipper.py watch > /var/log/session-shipper.log 2>&1 &

# Or as a systemd service (Linux)
# See Section 5.4
```

### 5.4 Systemd service (Linux/WSL2)

Create `/etc/systemd/user/session-shipper.service`:

```ini
[Unit]
Description=AI Session Log Shipper
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/claude-session-replay
ExecStart=/usr/bin/python3 session-shipper.py watch
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable session-shipper
systemctl --user start session-shipper
systemctl --user status session-shipper

# View logs
journalctl --user -u session-shipper -f
```

### 5.5 Windows Task Scheduler

For native Windows deployments (not WSL2):

```powershell
# Create a scheduled task that runs at login
$action = New-ScheduledTaskAction `
  -Execute "python3" `
  -Argument "session-shipper.py watch" `
  -WorkingDirectory "C:\path\to\claude-session-replay"

$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName "SessionShipper" `
  -Action $action -Trigger $trigger -Settings $settings
```

## 6. OpenSearch Dashboards

### 6.1 Useful queries

**Find all security alerts in the last 24 hours:**
```json
{
  "query": {
    "bool": {
      "must": [
        { "range": { "timestamp_shipped": { "gte": "now-24h" } } },
        { "nested": {
          "path": "security_flags",
          "query": { "exists": { "field": "security_flags.severity" } }
        }}
      ]
    }
  },
  "sort": [{ "timestamp_shipped": "desc" }]
}
```

**Find banned word hits by user:**
```json
{
  "size": 0,
  "query": {
    "nested": {
      "path": "banned_word_hits",
      "query": { "exists": { "field": "banned_word_hits.word" } }
    }
  },
  "aggs": {
    "by_user": {
      "terms": { "field": "user_id", "size": 50 },
      "aggs": {
        "words": {
          "nested": { "path": "banned_word_hits" },
          "aggs": {
            "word_breakdown": {
              "terms": { "field": "banned_word_hits.word", "size": 20 }
            }
          }
        }
      }
    }
  }
}
```

**Find sessions where sensitive files were accessed:**
```json
{
  "query": {
    "nested": {
      "path": "security_flags",
      "query": {
        "term": { "security_flags.category": "sensitive_file_read" }
      }
    }
  },
  "aggs": {
    "by_session": {
      "terms": { "field": "session_id", "size": 20 },
      "aggs": {
        "user": { "terms": { "field": "user_id" } },
        "flags": {
          "nested": { "path": "security_flags" },
          "aggs": {
            "details": { "terms": { "field": "security_flags.detail.keyword", "size": 10 } }
          }
        }
      }
    }
  }
}
```

**Activity timeline — messages per hour by user:**
```json
{
  "size": 0,
  "aggs": {
    "timeline": {
      "date_histogram": {
        "field": "timestamp_original",
        "fixed_interval": "1h"
      },
      "aggs": {
        "by_user": {
          "terms": { "field": "user_id", "size": 20 }
        }
      }
    }
  }
}
```

**Most used tools across all sessions:**
```json
{
  "size": 0,
  "aggs": {
    "tools": {
      "nested": { "path": "tool_uses" },
      "aggs": {
        "tool_names": {
          "terms": { "field": "tool_uses.name", "size": 20 }
        }
      }
    }
  }
}
```

**Find external URL access (suspicious commands):**
```json
{
  "query": {
    "nested": {
      "path": "security_flags",
      "query": {
        "term": { "security_flags.category": "external_access" }
      }
    }
  },
  "sort": [{ "timestamp_shipped": "desc" }],
  "_source": ["user_id", "session_id", "message_index", "security_flags", "timestamp_original"]
}
```

### 6.2 Recommended dashboard panels

| Panel | Visualization | Data |
|-------|--------------|------|
| Security Alert Feed | Data table | Last 50 security_flags, sorted by timestamp |
| Alert Severity Breakdown | Pie chart | security_flags.severity counts |
| Banned Word Heatmap | Heatmap | banned_word_hits.word × user_id |
| User Activity Timeline | Line chart | Messages per hour per user_id |
| Tool Usage Distribution | Bar chart | tool_uses.name counts |
| Session Duration | Histogram | max(timestamp_original) - min(timestamp_original) per session_id |
| Top Active Users | Metric / Table | Message count by user_id |
| Sensitive File Access Log | Data table | security_flags where category=sensitive_file_read |

### 6.3 Sample dashboard JSON

Import this via OpenSearch Dashboards > Management > Saved Objects > Import:

```json
{
  "title": "AI Session Security Dashboard",
  "panels": [
    {
      "type": "search",
      "title": "Recent Security Alerts",
      "columns": ["timestamp_shipped", "user_id", "session_id", "security_flags.severity", "security_flags.category", "security_flags.detail"]
    }
  ]
}
```

For a full dashboard export, see the `opensearch-dashboards/` directory (if provided by your admin).

## 7. OpenSearch → Local session linking

The shipper stores `session_id` (SHA-256 of file path) in every document. This enables tracing from OpenSearch back to the local session.

### 7.1 From OpenSearch Dashboards

1. Find the document of interest in OpenSearch Dashboards
2. Note the `session_id` and `message_index` fields
3. On the developer's workstation:

```bash
# Look up the session
python3 session-shipper.py lookup --session-id f91386e7

# Open directly in Player, jumping to the matching message
python3 session-shipper.py lookup --session-id f91386e7 -m 42 --open-player
```

### 7.2 Programmatic lookup

```python
import session_shipper

config = session_shipper.load_config()
tracker = session_shipper.OffsetTracker(config["state_file"])
session_map = tracker.get_session_map()

# session_id → local file path
file_path = session_map.get("f91386e7aa6ee989")
print(file_path)
# /home/user/.claude/projects/.../91f6fe51-....jsonl
```

## 8. Central policy management

### 8.1 Policy document in OpenSearch

Store a policy document that the shipper periodically fetches:

```bash
curl -X PUT "https://opensearch.corp.example.com:9200/agent-policy/_doc/current" \
  -H 'Content-Type: application/json' \
  -u admin:admin \
  -d '{
  "banned_words": ["confidential", "internal_only", "proprietary", "do_not_share"],
  "sensitive_paths": ["/etc/shadow", "/etc/passwd", ".env", "credentials", "id_rsa", ".ssh/", ".aws/"],
  "suspicious_commands": ["curl ", "wget ", "nc ", "ncat ", "base64 ", "eval ", "ssh ", "scp "],
  "redaction_patterns": [
    { "name": "api_key", "regex": "(?i)(api[_-]?key|token|secret)\\s*[:=]\\s*[^\\s]{20,}", "replacement": "***REDACTED***" },
    { "name": "jwt", "regex": "eyJ[A-Za-z0-9_-]{10,}\\.eyJ[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]+", "replacement": "***JWT_REDACTED***" }
  ],
  "updated_at": "2026-03-22T00:00:00Z"
}'
```

### 8.2 Shipper auto-sync

With `auto_policy_sync` enabled, the shipper fetches this document every `interval_seconds` (default: 5 minutes) and updates its in-memory config. No shipper restart required.

```json
{
  "features": { "auto_policy_sync": true },
  "policy_sync": {
    "url": "https://opensearch.corp.example.com:9200/agent-policy/_doc/current",
    "interval_seconds": 300,
    "auth_same_as_endpoint": true
  }
}
```

### 8.3 Manual policy sync

```bash
python3 session-shipper.py policy-sync
# Policy sync complete.
#   Banned words: 4
#   Sensitive paths: 7
```

## 9. Operational procedures

### 9.1 Monitoring the shipper

```bash
# Check shipping state
python3 session-shipper.py status

# Check DLQ (dead letter queue)
python3 session-shipper.py status  # shows DLQ count at bottom

# Retry failed shipments
python3 session-shipper.py retry-dlq

# Validate config after changes
python3 session-shipper.py validate-config
```

### 9.2 Incident investigation workflow

When OpenSearch alerts fire:

1. **Identify**: Find the alert in OpenSearch Dashboards
2. **Locate**: Get `session_id` and `message_index` from the document
3. **Replay**: On the developer's machine:
   ```bash
   python3 session-shipper.py lookup --session-id <id> -m <index> --open-player
   ```
4. **Context**: The Player opens with the full session, scrolled to the relevant message
5. **Document**: Export the session as HTML for the incident report:
   ```bash
   python3 log-replay.py --agent claude <session.jsonl> -f html -o incident-report.html
   ```

### 9.3 Onboarding a new developer

1. Clone the tool on their workstation
2. Copy `shipper-config.json` from the team template
3. Run `python3 session-shipper.py validate-config`
4. Batch-ship existing sessions: `python3 session-shipper.py batch`
5. Start the watch daemon (systemd or Task Scheduler)
6. Verify data appears in OpenSearch Dashboards

### 9.4 Offboarding / rotating credentials

1. Revoke the OIDC client credentials for the user
2. Delete their token cache: `rm ~/.claude-replay/oidc-token.json`
3. Their existing data remains in OpenSearch (retention policy governs deletion)
4. DLS ensures the departed user's data is only visible to admins

## 10. Security considerations

| Concern | Mitigation |
|---------|-----------|
| Session content contains secrets | Redaction engine strips API keys, tokens, emails before shipping |
| User impersonation | OIDC auth extracts user_id from IdP-signed JWT tokens |
| Data at rest on workstation | `encryption_at_rest` encrypts exported NDJSON files |
| Data in transit | HTTPS (TLS) to OpenSearch; `verify_ssl: true` by default |
| Unauthorized access to other users' data | Document Level Security restricts queries by `_dls_user` |
| Shipper daemon compromise | Shipper has write-only access; read access requires separate role |
| Content sensitivity varies by team | `metadata_only` mode ships activity data without content |
| Policy drift | `auto_policy_sync` keeps all shippers aligned with central policy |
| Shipping failures | Dead letter queue preserves failed documents for retry |

## 11. Capacity planning

### Storage estimation

| Factor | Typical value |
|--------|--------------|
| Average message document size | 1-5 KB (without tool_result) |
| Average messages per session | 50-200 |
| Average sessions per developer per day | 2-5 |
| Developers | N |
| Daily ingest | N × 3.5 sessions × 125 msgs × 3 KB ≈ N × 1.3 MB/day |
| Monthly ingest | N × 40 MB/month |
| With tool_result included | Multiply by 5-10x |
| metadata_only mode | Divide by 5-10x |

**Example**: 50 developers, 30-day retention:
- Standard: 50 × 40 MB × 1 month = ~2 GB
- With tool_results: ~10-20 GB
- Metadata only: ~400 MB

### Index management

- Use ISM policy (Section 3.4) for automatic rollover and deletion
- Consider daily indices for large teams: `agent-sessions-2026.03.22`
- Use `compression: true` to reduce network and storage by ~60-70%
