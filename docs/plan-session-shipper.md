# Session Shipper 設計

## Context

企業での証跡管理ニーズ: AIコーディングエージェントのセッションログを中央のOpenSearchに送信し、禁止ワード検知・セキュリティ監視・監査証跡として活用する。JSONLはリアルタイム追記されるため、ストリーミング送信も可能。

**追加要件:**
- OpenSearchの認証(SAML/OIDC)でuser_id正当性を担保
- SaaS化を見据えたマルチテナント設計
- OpenSearch検索結果 → ローカルセッション紐付け → Player再生の導線

## アーキテクチャ

```
Agent Log (.jsonl)
  ├→ Adapter → Common Model → Renderer  (既存)
  └→ Session Shipper                      (新規)
       ├─ Envelope (identity + session_id)
       ├─ Scope filter
       ├─ Redaction engine
       ├─ Security analysis
       └─ Transport → OpenSearch / File export
                          │
                          ↓
                    OpenSearch (central)
                          │
                          ↓
                    Search UI / API
                          │
                          ↓ (session_id + message_index)
                    Local session lookup → Player再生
```

## ファイル構成

| ファイル | 種別 | 内容 |
|---------|------|------|
| `session-shipper.py` | 新規 | Shipper本体 (CLI + importable module) |
| `shipper-config.json` | 新規 | デフォルト設定テンプレート |
| `log-replay.py` | 変更 | `--ship` フラグ追加 |
| 各アダプター | 変更不要 | 既存の内部関数を直接利用 |

## 送信ドキュメント構造 (per message)

```json
{
  "user_id": "tanaka.taro",
  "hostname": "CORP-PC-1234",
  "os_user": "tanaka",
  "os_platform": "linux",

  "agent": "claude",
  "session_id": "a1b2c3d4e5f6",
  "session_file": "/home/tanaka/.claude/projects/.../session.jsonl",
  "project": "payment-service",

  "message_index": 3,
  "role": "assistant",
  "timestamp_original": "2026-03-21T10:30:00Z",
  "timestamp_shipped": "2026-03-21T10:31:05Z",

  "text": "Let me fix that bug...",
  "thinking": [],
  "tool_uses": [{"name": "Read", "input": {"file_path": "/etc/shadow"}}],
  "tool_results": [],

  "security_flags": [
    {"severity": "high", "category": "sensitive_file_read",
     "detail": "Read /etc/shadow"}
  ],
  "banned_word_hits": [
    {"word": "password", "field": "text", "count": 1}
  ]
}
```

- `session_id`: ファイルパスのSHA-256 (16 hex chars)。OpenSearch検索結果からローカルセッションへの逆引きキー
- `_id` (OpenSearch doc ID): `sha256(session_id + message_index)` → 冪等性保証 (再送時にupsert)

## 設定ファイル (`shipper-config.json`)

```json
{
  "endpoint": {
    "type": "opensearch",
    "url": "https://opensearch.corp:9200/sessions/_bulk",
    "index": "agent-sessions",
    "auth": {
      "type": "api_key",
      "api_key": ""
    },
    "timeout_seconds": 30,
    "verify_ssl": true
  },
  "file_export": {
    "directory": "/var/log/agent-sessions/",
    "format": "ndjson"
  },
  "identity": {
    "user_id": "",
    "hostname": "",
    "organization": ""
  },
  "scope": {
    "include_text": true,
    "include_thinking": false,
    "include_tool_use": true,
    "include_tool_result": false
  },
  "redaction": {
    "patterns": [
      {"name": "api_key", "regex": "(?i)(api[_-]?key|token|secret)\\s*[:=]\\s*['\"]?([A-Za-z0-9_\\-]{20,})", "replacement": "$1=***REDACTED***"},
      {"name": "email", "regex": "[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}", "replacement": "***EMAIL***"}
    ]
  },
  "security": {
    "sensitive_paths": ["/etc/shadow", "/etc/passwd", ".env", "credentials", "id_rsa", ".ssh/"],
    "suspicious_commands": ["curl", "wget", "nc ", "ncat", "base64", "eval"],
    "banned_words": []
  },
  "watch": {
    "agents": ["claude", "codex", "gemini"],
    "polling_interval_seconds": 2
  },
  "shipping": {
    "batch_size": 50,
    "flush_interval_seconds": 5,
    "max_retries": 3
  },
  "state_file": "~/.claude-replay/shipper-state.json"
}
```

## 2つの動作モード

### Batch モード
```bash
python3 session-shipper.py batch --agent claude
python3 session-shipper.py batch --input session.jsonl --agent claude
python3 session-shipper.py batch --agent claude --dry-run
```
- セッション全体をparse → 共通モデル変換 → メッセージ単位でship
- OffsetTrackerで既送信分をスキップ

### Watch モード (daemon)
```bash
python3 session-shipper.py watch --agent claude codex
```
- ファイルシステムをポーリング (os.stat mtime比較、2秒間隔)
- 新規行をtail → per-line parse → ship
- SIGINT/SIGTERMでgraceful shutdown (flush + state save)
- **Gemini**: JSONファイルのため、mtime変更検知 → 全体リパース → 差分送信

### プラットフォーム対応
| Platform | ファイル監視 | 備考 |
|----------|------------|------|
| Linux | os.stat polling (+ 任意で watchdog/inotify) | |
| macOS | os.stat polling (+ 任意で watchdog/kqueue) | |
| WSL2 (Linux側パス) | os.stat polling ✓ / inotify ✓ | `~/.claude/` 等 |
| WSL2 (/mnt/c/) | os.stat polling ✓ / inotify ✗ | ポーリングのみ |
| Windows native | os.stat polling ✓ | |

## セキュリティ分析 (自動付与)

tool_useを自動解析して `security_flags` を付与:

| カテゴリ | トリガー | 重要度 |
|---------|---------|--------|
| `sensitive_file_read` | Read で .env, /etc/shadow 等 | high |
| `sensitive_file_write` | Write で /etc/, ~/.ssh/ 等 | high |
| `suspicious_command` | Bash で curl, wget, eval 等 | medium |
| `external_access` | Bash でURL含むコマンド | medium |
| `sensitive_search` | Grep でpassword, secret等 | low |

## OpenSearch → ローカル紐付け

OpenSearchで検索した結果をローカルセッションに戻す導線:

1. OpenSearch検索結果に `session_id` + `session_file` + `message_index` が含まれる
2. ローカルの `shipper-state.json` に `session_id → session_file` のマッピングがある
3. **lookup サブコマンド**:
```bash
# session_id からローカルセッションをPlayerで開く
python3 session-shipper.py lookup --session-id a1b2c3d4 --open-player
# → log-replay.py を呼んで該当セッションをPlayer出力 + --range でメッセージにジャンプ

# OpenSearchクエリ結果からまとめて開く
python3 session-shipper.py lookup --query "banned_word_hits.word:password" --opensearch-url https://...
```
4. **Web UIからの連携** (将来): OpenSearch検索UIの結果リンクが `http://localhost:5000/open?session_id=xxx&msg=5` のようなURLを持ち、Web UIがローカルセッションを開く

## 認証とSaaS化の考慮

- OpenSearchのSecurity Pluginで **Document Level Security** を使えば、`user_id` フィールドでアクセス制御可能
- SAML/OIDC連携でSSO。shipperがトークンを取得してBulk APIに送信
- SaaS化する場合は `organization` フィールドをテナント識別子にして、OpenSearchのテナント機能 or インデックス分離でマルチテナント
- shipperのconfig `auth.type` に `"oidc"` を追加すれば対応可能

## 実装フェーズ

### Phase 1: Core + Batch
- 設定ロード、Identity envelope、Redaction、Scope filter
- Security analysis、Banned word detection
- Transport (OpenSearch HTTP + File export)
- Offset tracking
- CLI batch サブコマンド
- `shipper-config.json` テンプレート

### Phase 2: Watch (daemon)
- FileWatcher (os.stat polling)
- Per-line parsing (アダプター内部関数を直接利用)
- Stream daemon loop + graceful shutdown
- Gemini差分検知

### Phase 3: Lookup + 連携
- lookup サブコマンド (session_id → ローカルPlayer)
- `log-replay.py` への `--ship` フラグ追加
- init-config, status サブコマンド

### Phase 4: 認証強化
- OIDC/SAML トークン取得
- organization フィールド対応

## 検証方法

### Phase 1
```bash
# 設定生成
python3 session-shipper.py init-config -o shipper-config.json

# dry-run で送信内容確認
python3 session-shipper.py batch --agent claude --dry-run

# ファイルエクスポート (Filebeatピックアップ用)
# shipper-config.json の endpoint.type を "file" に変更
python3 session-shipper.py batch --agent claude
ls /var/log/agent-sessions/

# OpenSearch直接送信 (テスト用ローカルOpenSearch)
docker run -p 9200:9200 opensearchproject/opensearch:latest
python3 session-shipper.py batch --agent claude
curl http://localhost:9200/agent-sessions/_search?q=role:assistant
```

### Phase 2
```bash
# daemon起動してセッション操作
python3 session-shipper.py watch --agent claude &
# 別ターミナルでClaude Code操作 → リアルタイムでOpenSearchに到達
```

### Phase 3
```bash
# OpenSearch検索結果からローカルPlayerを開く
python3 session-shipper.py lookup --session-id a1b2c3d4 --open-player
# → ブラウザにPlayerが開く
```
