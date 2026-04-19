[English version](README.en.md)

# claude-session-replay

AI コーディングエージェントのセッションログを **3段パイプライン（capture → normalize → render）** で録画・変換・再生するツール。

> 5つのエージェント、7つのレンダラー、1つの共通モデル。

---

## 目次

- [なぜ必要か](#なぜ必要か)
- [3段パイプライン](#3段パイプライン)
- [対応エージェント](#対応エージェント)
- [対応レンダラー](#対応レンダラー)
- [クイックスタート](#クイックスタート)
- [インストール](#インストール)
- [使い方](#使い方)
- [Web UI](#web-ui)
- [キーボードショートカット](#キーボードショートカット)
- [動作環境](#動作環境)
- [注意事項](#注意事項)
- [ドキュメント](#ドキュメント)

---

## なぜ必要か

Claude Code / Codex / Gemini CLI などのエージェントはそれぞれ独自形式でセッションログを保存する。このツールはそれらを**共通モデル**に正規化し、あらゆる出力形式に変換する。

- セッションをチームと共有したい → HTML / Markdown
- 作業の流れを動画にしたい → MP4 / GIF
- タイムスタンプ付きで証跡を残したい → Alibai Mode（アナログ時計付きプレイヤー）
- ブラウザで手軽に閲覧したい → Web UI

---

## 3段パイプライン

```
┌─────────────────────────────────────────────────────────┐
│  Stage 1: Capture (Agent Adapters)                      │
│  各エージェントのログを読み取り、共通モデルに変換         │
│  claude-log2model.py / codex-log2model.py / ...         │
└────────────────────┬────────────────────────────────────┘
                     │ common model JSON
┌────────────────────▼────────────────────────────────────┐
│  Stage 2: Normalize (Common Model)                      │
│  {source, agent, messages[{role, text, tool_uses,       │
│   tool_results, thinking, timestamp}]}                  │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────┐
│  Stage 3: Render (log-model-renderer.py)                │
│  md / html / player / terminal / MP4 / PDF / GIF        │
└─────────────────────────────────────────────────────────┘
```

各ステージは独立して実行できる。`log-replay.py` はパイプライン全体を一括実行するラッパー。

---

## 対応エージェント

| エージェント | アダプター | ログ場所 |
|-------------|-----------|---------|
| **Claude Code** | `claude-log2model.py` | `~/.claude/projects/*/*.jsonl` |
| **OpenAI Codex CLI** | `codex-log2model.py` | `~/.codex/sessions/**/*.jsonl` |
| **Gemini CLI** | `gemini-log2model.py` | `~/.gemini/tmp/*/chats/session-*.json` |
| **Aider** | `aider-log2model.py` | `.aider.chat.history.md` |
| **Cursor** | `cursor-log2model.py` | `~/.cursor/` (SQLite) |

詳細は [docs/agents.md](docs/agents.md) | [日本語](docs/agents-ja.md) を参照。

---

## 対応レンダラー

| フォーマット | フラグ | 説明 |
|------------|-------|-----|
| **Markdown** | `md` | プレーンテキスト、外部依存なし |
| **HTML** | `html` | 静的チャット UI、外部依存なし |
| **Player** | `player` | Alibai Mode 付きインタラクティブプレイヤー |
| **Terminal** | `terminal` | Claude Code ターミナル UI 再現プレイヤー |
| **MP4** | *(log-replay-mp4.py)* | Playwright + FFmpeg で動画録画 |
| **PDF** | *(log-replay-pdf.py)* | Playwright でPDF出力 |
| **GIF** | *(log-replay-gif.py)* | Playwright + Pillow でアニメーション GIF |

詳細は [docs/renderers.md](docs/renderers.md) | [日本語](docs/renderers-ja.md) を参照。

---

## クイックスタート

```bash
# 1. セットアップ
python3 -m venv .venv && source .venv/bin/activate

# 2. Claude セッションをプレイヤーとして開く（ファイル省略で一覧表示）
python3 log-replay.py --agent claude -f player

# 3. Codex セッションを HTML に変換
python3 log-replay.py --agent codex -f html -t light

# 4. 手動パイプライン
python3 claude-log2model.py session.jsonl -o session.model.json
python3 log-model-renderer.py session.model.json -f player -o out.html
```

---

## インストール

### 基本（CLI のみ、外部依存なし）

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
# 追加インストール不要 — 標準ライブラリのみで動作
```

> **Note**: `pyproject.toml` は存在しない。パッケージとして公開されておらず、venv + requirements なし の直接実行が前提。

### Web UI + MP4 / GIF / PDF 対応

```bash
source .venv/bin/activate
python3 -m pip install flask playwright pillow

# FFmpeg（MP4 / GIF に必要）
# Ubuntu/Debian: sudo apt-get install ffmpeg
# macOS:         brew install ffmpeg
# Windows:       choco install ffmpeg

python3 -m playwright install
```

---

## 使い方

### CLI ラッパー（推奨）

```bash
python3 log-replay.py --agent claude -f player          # Claude → Player
python3 log-replay.py --agent codex  -f terminal        # Codex  → Terminal
python3 log-replay.py --agent gemini -f player          # Gemini → Player
python3 log-replay.py --agent aider  -f html -t light   # Aider  → HTML Light
python3 log-replay.py --agent cursor -f md              # Cursor → Markdown
```

入力ファイルを省略すると既知ディレクトリからセッションを自動検出して一覧表示する。

**主要オプション**:

| オプション | 説明 |
|-----------|-----|
| `-f` / `--format` | `md` / `html` / `player` / `terminal` |
| `-t` / `--theme` | `light` / `console` |
| `-o` / `--output` | 出力ファイルパス |
| `--project` | Claude プロジェクト名でフィルター |
| `--filter` | Codex パスでフィルター |
| `--range` | メッセージ範囲（例: `1-50,53-`） |

### 手動パイプライン

```bash
# Step 1: エージェントログ → 共通モデル
python3 claude-log2model.py  session.jsonl    -o session.model.json
python3 codex-log2model.py   session.jsonl    -o session.model.json
python3 gemini-log2model.py  session.json     -o session.model.json
python3 aider-log2model.py   .aider.chat.history.md -o session.model.json
python3 cursor-log2model.py                   -o session.model.json

# Step 2: 共通モデル → 出力
python3 log-model-renderer.py session.model.json -f player
python3 log-model-renderer.py session.model.json -f html   -t console
python3 log-model-renderer.py session.model.json -f terminal

# MP4 / GIF / PDF
python3 log-replay-mp4.py --agent claude session.jsonl -o out.mp4 --width 1280 --height 720 --fps 30 --speed 2.0
python3 log-replay-gif.py --agent claude session.jsonl -o out.gif
python3 log-replay-pdf.py --agent claude session.jsonl -o out.pdf
```

### メッセージ範囲指定

```bash
python3 log-model-renderer.py session.model.json -f player --range "1-50,53-"
```

| 形式 | 意味 |
|-----|-----|
| `1-50` | 1〜50番目 |
| `53-` | 53番目〜最後 |
| `-10` | 1〜10番目 |
| `7` | 7番目のみ |

### ANSI モード

```bash
python3 log-model-renderer.py session.model.json -f terminal --ansi-mode strip   # 削除（デフォルト）
python3 log-model-renderer.py session.model.json -f terminal --ansi-mode color   # HTML カラーに変換
```

---

## Web UI

```bash
source .venv/bin/activate
python3 web_ui.py
# → http://localhost:5000
```

ブラウザからセッション選択・変換・再生が可能。Alibai Mode（タイムスタンプ可視化）対応。

---

## キーボードショートカット（player / terminal）

| キー | 機能 |
|-----|-----|
| `Space` | 再生 / 一時停止 |
| `→` | 次のメッセージ |
| `←` | 前のメッセージ |
| `Home` | 先頭へ |
| `End` | 末尾へ |
| `g` | 指定時刻へジャンプ |
| `j` / `k` | スクロール |
| `T` | ツールメッセージをスキップ |
| `E` | 空ツールの表示切替 |
| `D` | ツール詳細の表示切替 |

速度スライダー: 0.25x〜16x。

---

## 動作環境

| 機能 | 必須 |
|-----|-----|
| 基本 CLI | Python 3.6+、外部依存なし |
| Web UI | `flask`, `playwright` |
| MP4 出力 | `playwright`, `ffmpeg` |
| GIF 出力 | `playwright`, `pillow`（または `ffmpeg`） |
| PDF 出力 | `playwright` |

---

## 注意事項

- **`pyproject.toml` なし**: このプロジェクトは PyPI に公開されていない。venv を使った直接実行が前提。
- **`session-shipper.py` のリダクション**: `session-shipper.py` の個人情報リダクション機能（`redact_pii` フラグ）は現時点で十分にテストされていない。本番環境での使用前に動作確認を推奨する。

---

## ドキュメント

- [Architecture](docs/architecture.md) | [日本語](docs/architecture-ja.md) — 3段パイプライン詳細
- [Getting Started](docs/getting-started.md) | [日本語](docs/getting-started-ja.md) — インストールと最初の実行
- [Agents](docs/agents.md) | [日本語](docs/agents-ja.md) — 各エージェントの対応状況
- [Renderers](docs/renderers.md) | [日本語](docs/renderers-ja.md) — 各レンダラーの詳細
- [Data Model](docs/data-model.md) — 共通モデル JSON スキーマ
- [Changelog](CHANGELOG.md) — 変更履歴
