[English version](architecture.md)

# アーキテクチャ

claude-session-replay は **3段パイプライン（capture → normalize → render）** でエージェント固有のログ解析と出力レンダリングを分離する。

---

## 目次

- [概要](#概要)
- [Stage 1 — Capture（エージェントアダプター）](#stage-1--captureエージェントアダプター)
- [Stage 2 — Normalize（共通モデル）](#stage-2--normalize共通モデル)
- [Stage 3 — Render](#stage-3--render)
- [エントリーポイント](#エントリーポイント)
- [レンダラーツリー](#レンダラーツリー)
- [依存関係モデル](#依存関係モデル)
- [ファイルレイアウト](#ファイルレイアウト)

---

## 概要

```
ベンダーログ（エージェント固有形式）
  ├─ Claude Code   ~/.claude/projects/*/*.jsonl
  ├─ Codex CLI     ~/.codex/sessions/**/*.jsonl
  ├─ Gemini CLI    ~/.gemini/tmp/*/chats/session-*.json
  ├─ Aider         .aider.chat.history.md
  └─ Cursor        ~/.cursor/ (SQLite)
         │
         ▼  Stage 1: Capture（エージェントアダプター）
  *-log2model.py スクリプト群
         │
         ▼  Stage 2: Normalize
  共通モデル JSON
  {source, agent, messages[{role, text, tool_uses, tool_results, thinking, timestamp}]}
         │
         ▼  Stage 3: Render
  出力
  ├─ Markdown     (.md,   静的)
  ├─ HTML         (.html, 静的)
  ├─ Player       (.html, インタラクティブ + Alibai Mode)
  ├─ Terminal     (.html, インタラクティブ、Claude Code UI 再現)
  ├─ MP4          (Playwright + FFmpeg)
  ├─ PDF          (Playwright)
  └─ GIF          (Playwright + Pillow または FFmpeg)
```

---

## Stage 1 — Capture（エージェントアダプター）

各アダプターは独立した Python スクリプトで、同じ論理インターフェースに従う。

```python
def parse_messages(input_path: str) -> list[dict]
    """ログファイルを読み取り、生のメッセージレコードを返す。"""

def build_model(messages: list[dict], input_path: str) -> dict
    """生のメッセージを共通モデル dict に変換する。"""

def discover_sessions(filter: str = None) -> list[dict]
    """既知のファイルシステムパスをスキャンし、セッションメタデータのリストを返す。"""

def select_session(sessions: list[dict]) -> str
    """対話的セッション選択。選択されたファイルパスを返す。"""
```

このコントラクトは抽象基底クラスではなく、慣習として各アダプターが実装する。

### アダプタースクリプト

| スクリプト | エージェント | 入力形式 |
|-----------|-------------|---------|
| `claude-log2model.py` | Claude Code | JSONL（1行1レコード） |
| `codex-log2model.py` | OpenAI Codex CLI | JSONL |
| `gemini-log2model.py` | Gemini CLI | JSON 配列 |
| `aider-log2model.py` | Aider | Markdown（`.aider.chat.history.md`） |
| `cursor-log2model.py` | Cursor | SQLite データベース |

ログ形式の詳細は [agents-ja.md](agents-ja.md) を参照。

### 新しいエージェントの追加

1. `<agent>-log2model.py` を作成し、4関数コントラクトを実装。
2. `log-replay.py` に `--agent <name>` を登録。
3. `web_ui.py` に import とセッション検出を登録。
4. レンダラーへの変更は不要。

---

## Stage 2 — Normalize（共通モデル）

すべてのアダプターが同じ JSON 構造を出力する。完全なスキーマは [data-model.md](data-model.md) を参照。

**不変条件**:
- `role` はソースエージェントの用語に関わらず常に `"user"` または `"assistant"`。
- `timestamp` は ISO 8601 文字列、利用不可の場合は空文字列。
- `source` はベースネームのみ — 絶対パスは含まない。
- メッセージは元のログの時系列順を保持。
- `text`、`tool_uses`、`tool_results`、`thinking` のいずれかが空でない場合のみメッセージを含む。

共通モデルは**エージェント非依存**。あらゆるレンダラーがあらゆるモデルを消費できる。

---

## Stage 3 — Render

`log-model-renderer.py` が共通モデルを読み取り出力を生成する。形式は `-f` で選択。

| 形式 | 出力タイプ | 依存関係 |
|-----|---------|---------|
| `md` | プレーンテキスト | なし |
| `html` | 静的 HTML | なし |
| `player` | 自己完結型 HTML + JS | ブラウザ |
| `terminal` | 自己完結型 HTML + JS | ブラウザ |

動画・画像レンダラーは、HTML をレンダリングしてからヘッドレスブラウザを操作する別スクリプト:

| スクリプト | 使用レンダラー | 出力 |
|-----------|-------------|-----|
| `log-replay-mp4.py` | `player` または `terminal` | MP4 |
| `log-replay-pdf.py` | `html` または `player` | PDF |
| `log-replay-gif.py` | `player` または `terminal` | GIF |

---

## エントリーポイント

| スクリプト | 役割 |
|-----------|-----|
| `log-replay.py` | CLI ラッパー — アダプターを選択し、レンダラーへパイプ |
| `web_ui.py` | Flask ブラウザ UI — セッション管理 + ライブ変換 |
| `log-model-renderer.py` | 直接レンダラー — 共通モデルを読んで出力 |
| `session-shipper.py` | エンタープライズ — OpenSearch へのセッション送信（バッチ/ウォッチ） |
| `session-stats.py` | 統計レポーター |

---

## レンダラーツリー

```
log-model-renderer.py
├── render_markdown(model)
│   └── メッセージごと: 見出し + テキスト + tool_uses + tool_results
├── render_html(model, theme)
│   └── インライン CSS チャット吹き出し; JS なし
├── render_player(model, theme)
│   ├── メッセージステッパー（Space / ← / →）
│   ├── 速度スライダー（0.25x〜16x）
│   ├── プログレスバー（クリックでシーク）
│   ├── 範囲フィルター（--range）
│   └── Alibai Mode
│       ├── サイド時計（メッセージごとに 44×44 px）
│       ├── 固定時計（100×100 px、右下固定）
│       └── 再生モード: Uniform / Real-time / Compressed
└── render_terminal(model)
    ├── Claude Code UI 再現
    ├── ユーザープロンプト（> 青背景）
    ├── アシスタントバー（オレンジの左ボーダー）
    ├── ツールブロック（Read/Write/Edit/Bash/Grep/Glob/Task）
    └── スピナーアニメーション（● → ✓）
```

---

## 依存関係モデル

```
コア（外部依存なし — Python 3.6+ 標準ライブラリのみ）
  claude-log2model.py
  codex-log2model.py
  gemini-log2model.py
  aider-log2model.py
  cursor-log2model.py
  log-model-renderer.py
  log-replay.py

オプション — Web UI
  web_ui.py           → flask

オプション — ヘッドレス録画
  log-replay-mp4.py   → playwright、ffmpeg（システムバイナリ）
  log-replay-pdf.py   → playwright
  log-replay-gif.py   → playwright、pillow（または ffmpeg）
```

> **注意**: `pyproject.toml` は存在しない。オプション依存は venv 内に手動でインストールする必要がある。

遅延インポートにより、オプションパッケージが不足していてもスタートアップではなく機能境界でのみエラーが発生する。

---

## ファイルレイアウト

```
claude-session-replay/
├── log-replay.py              # CLI ラッパー
├── claude-log2model.py        # Capture: Claude Code
├── codex-log2model.py         # Capture: Codex CLI
├── gemini-log2model.py        # Capture: Gemini CLI
├── aider-log2model.py         # Capture: Aider
├── cursor-log2model.py        # Capture: Cursor
├── log-model-renderer.py      # Render: md/html/player/terminal
├── log-replay-mp4.py          # Render: MP4
├── log-replay-pdf.py          # Render: PDF
├── log-replay-gif.py          # Render: GIF
├── web_ui.py                  # Flask Web UI
├── session-shipper.py         # エンタープライズシッパー
├── session-stats.py           # 統計
├── search_utils.py            # セッション検出共通ヘルパー
├── templates/index.html       # Web UI テンプレート
├── docs/
│   ├── architecture.md        # English version
│   ├── architecture-ja.md     # このドキュメント
│   ├── getting-started.md
│   ├── getting-started-ja.md
│   ├── agents.md
│   ├── agents-ja.md
│   ├── renderers.md
│   ├── renderers-ja.md
│   └── data-model.md
├── README.md                  # 日本語 README
├── README-en.md               # English README
└── CHANGELOG.md
```
