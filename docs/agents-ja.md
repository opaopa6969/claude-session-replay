[English version](agents.md)

# エージェント

各 AI コーディングエージェントの対応状況、ログ形式、アダプターの詳細。

---

## 目次

- [対応状況マトリクス](#対応状況マトリクス)
- [Claude Code](#claude-code)
- [OpenAI Codex CLI](#openai-codex-cli)
- [Gemini CLI](#gemini-cli)
- [Aider](#aider)
- [Cursor](#cursor)
- [新しいエージェントの追加](#新しいエージェントの追加)

---

## 対応状況マトリクス

| エージェント | アダプター | テキスト | ツール呼び出し | 思考ブロック | タイムスタンプ | 自動検出 |
|------------|-----------|--------|------------|-----------|------------|--------|
| **Claude Code** | `claude-log2model.py` | Yes | Yes | Yes | Yes | Yes |
| **Codex CLI** | `codex-log2model.py` | Yes | Yes（正規化） | Yes | Yes | Yes |
| **Gemini CLI** | `gemini-log2model.py` | Yes | No | Yes | Yes | Yes |
| **Aider** | `aider-log2model.py` | Yes | No | No | 一部 | No |
| **Cursor** | `cursor-log2model.py` | Yes | No | No | 一部 | Yes |

---

## Claude Code

**スクリプト**: `claude-log2model.py`

### ログの場所

```
~/.claude/projects/<プロジェクトディレクトリ>/*.jsonl
```

### 入力形式

JSONL（1行1 JSON オブジェクト）:

```json
{
  "type": "user" | "assistant" | "summary",
  "timestamp": "2026-03-21T10:30:00.000Z",
  "gitBranch": "feature/login-fix",
  "message": {
    "role": "user" | "assistant",
    "content": "string" | [content_block, ...]
  }
}
```

### コンテンツブロック型

| ブロック型 | フィールド | マッピング先 |
|-----------|---------|-----------|
| `text` | `text` | `message.text` |
| `tool_use` | `id`, `name`, `input` | `message.tool_uses[]` |
| `tool_result` | `tool_use_id`, `content` | `message.tool_results[]` |
| `thinking` | `thinking` | `message.thinking[]` |

### 既知のツール

`Read`, `Write`, `Edit`, `Bash`, `Grep`, `Glob`, `Task`, `WebFetch`, `WebSearch`

### セッション検出

- `~/.claude/projects/` を再帰的にスキャンして `.jsonl` ファイルを検出
- `subagents/` サブディレクトリのファイルを除外
- 1 KB 未満のファイルを除外
- 更新時刻の降順でソート

### フィルターオプション

| オプション | 説明 |
|-----------|-----|
| `--project` | プロジェクトディレクトリ名の部分文字列マッチ |

---

## OpenAI Codex CLI

**スクリプト**: `codex-log2model.py`

### ログの場所

```
~/.codex/sessions/<ネストパス>/*.jsonl
```

### 入力形式

JSONL（1行1 JSON オブジェクト）:

```json
{
  "type": "message",
  "timestamp": "2026-03-21T10:30:00Z",
  "message": {
    "role": "user" | "assistant",
    "content": "string" | [content_block, ...]
  }
}
```

### コンテンツブロック型

| ブロック型 | フィールド | マッピング先 |
|-----------|---------|-----------|
| `input_text` / `output_text` / `text` | `text` | `message.text` |
| `thinking` | `thinking` | `message.thinking[]` |
| `function_call` | `name`, `arguments` | `message.tool_uses[]`（正規化） |
| `function_call_output` | `output` | `message.tool_results[]` |

### ツール名の正規化

Codex の関数名は一貫したレンダリングのために Claude のツール名に正規化される:

| Codex 関数名 | 正規化後の名前 |
|------------|-------------|
| `shell_command` | `Bash` |
| `file_read` | `Read` |
| `file_write` | `Write` |
| `file_edit` | `Edit` |

### セッション検出

- `~/.codex/sessions/` を再帰的にスキャンして `.jsonl` ファイルを検出
- 更新時刻の降順でソート

### フィルターオプション

| オプション | 説明 |
|-----------|-----|
| `--filter` | ファイルパスの部分文字列マッチ |

---

## Gemini CLI

**スクリプト**: `gemini-log2model.py`

### ログの場所

```
~/.gemini/tmp/<プロジェクトディレクトリ>/chats/session-*.json
```

### 入力形式

セッションごとの単一 JSON ファイル（JSONL ではない）:

```json
{
  "startTime": "2026-03-21T10:30:00Z",
  "messages": [
    {
      "type": "user" | "gemini",
      "content": "string" | [{"text": "..."}],
      "thoughts": [{"description": "..."}],
      "timestamp": "2026-03-21T10:30:00Z"
    }
  ]
}
```

### ロールマッピング

| Gemini `type` | 共通モデル `role` |
|--------------|----------------|
| `"user"` | `"user"` |
| `"gemini"` | `"assistant"` |

### 制限事項

- Gemini CLI のセッションログにツール呼び出しデータは**含まれない**。`tool_uses` と `tool_results` は常に空配列になる。

### セッション検出

- `~/.gemini/tmp/` をスキャンしてプロジェクトディレクトリを検出
- 各プロジェクト内の `chats/` から `session-*.json` ファイルを検出
- 更新時刻の降順でソート

### フィルターオプション

| オプション | 説明 |
|-----------|-----|
| `--project` | プロジェクトディレクトリ名の部分文字列マッチ |

---

## Aider

**スクリプト**: `aider-log2model.py`

### ログの場所

Aider は各プロジェクトの作業ディレクトリに `.aider.chat.history.md` を書き込む。中央ログディレクトリは存在しないため、ファイルパスを明示的に指定する必要がある。

### 入力形式

マーカー付き Markdown ファイル:

```markdown
# aider chat started at 2026-03-21 10:30:00

#### /user
ログインのバグを直して。

#### /assistant
まずログインハンドラーを確認します。
```

Aider のバージョンによっては以下の形式も使用される:

```markdown
#### /user timestamp
<メッセージ>

#### /assistant timestamp
<メッセージ>
```

### 制限事項

- ツール呼び出しデータなし（この形式では構造化ツール呼び出しを保存しない）。
- 思考ブロックなし。
- タイムスタンプの有無は Aider のバージョンに依存。

### セッション検出

Aider には中央セッションディレクトリがないため、自動検出は**非対応**。ファイルパスを明示的に指定する:

```bash
python3 aider-log2model.py /path/to/project/.aider.chat.history.md -o out.json
```

---

## Cursor

**スクリプト**: `cursor-log2model.py`

### ログの場所

Cursor は SQLite データベースに会話データを保存する:

```
~/.cursor/                              （Linux）
~/.config/Cursor/                       （Linux 別パス）
~/Library/Application Support/Cursor/   （macOS）
%APPDATA%/Cursor/                       （Windows）
~/.cursor-tutor/                        （ワークスペースレベル）
```

### 入力形式

会話レコードを含む SQLite データベース。正確なスキーマは Cursor のバージョンによって異なる。アダプターは既知のテーブル・カラムパターンを検索する。

### 制限事項

- SQLite スキーマは公式に文書化されておらず、Cursor のバージョン間で変更される可能性がある。
- ツール呼び出しデータと思考ブロックは利用不可。
- タイムスタンプの有無はバージョンに依存。

### セッション検出

- すべての既知の Cursor データディレクトリを検索
- SQLite データベースを読み取り、会話レコードを抽出

---

## 新しいエージェントの追加

1. `<agent>-log2model.py` を作成し、4関数コントラクトを実装:
   - `parse_messages(input_path) -> list[dict]`
   - `build_model(messages, input_path) -> dict`
   - `discover_sessions(filter=None) -> list[dict]`
   - `select_session(sessions) -> str`
2. `log-replay.py` に `--agent <name>` を登録。
3. `web_ui.py` に import とセッション検出エンドポイントを登録。
4. レンダラーへの変更は**不要** — 共通モデルのみを読み取るため。

チェックリスト:
- [ ] ロール正規化 → `"user"` / `"assistant"`
- [ ] タイムスタンプを ISO 8601 で抽出
- [ ] ツール名の正規化（該当する場合）
- [ ] 思考ブロックの抽出（利用可能な場合）
- [ ] セッション一覧表示用プレビュー情報の抽出
