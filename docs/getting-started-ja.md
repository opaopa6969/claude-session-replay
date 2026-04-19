[English version](getting-started.md)

# はじめに

このガイドでは、インストールから各エージェントでの最初のセッション再生までを説明する。

---

## 目次

- [前提条件](#前提条件)
- [インストール](#インストール)
- [セッションのキャプチャ](#セッションのキャプチャ)
  - [Claude Code](#claude-code)
  - [OpenAI Codex CLI](#openai-codex-cli)
  - [Gemini CLI](#gemini-cli)
  - [Aider](#aider)
  - [Cursor](#cursor)
- [レンダリング](#レンダリング)
- [次のステップ](#次のステップ)

---

## 前提条件

- Python 3.6 以上
- サポートされている AI コーディングエージェントのうち少なくとも 1 つがインストール済みで、ログファイルが存在すること

`pyproject.toml` は存在しない。このプロジェクトは PyPI に公開されておらず、venv 内での直接実行が前提。

---

## インストール

```bash
# 1. リポジトリをクローン
git clone https://github.com/opaopa6969/claude-session-replay.git
cd claude-session-replay

# 2. 仮想環境を作成
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. （オプション）Web UI、MP4、GIF、PDF 用の追加パッケージ
python3 -m pip install flask playwright pillow

# MP4 / GIF 用 FFmpeg
# Ubuntu/Debian: sudo apt-get install ffmpeg
# macOS:         brew install ffmpeg
# Windows:       choco install ffmpeg

python3 -m playwright install
```

コア CLI 機能（Markdown、HTML、Player、Terminal 出力）は追加パッケージなしで動作する。

---

## セッションのキャプチャ

### Claude Code

Claude Code はセッションログを自動的に以下に書き込む:

```
~/.claude/projects/<プロジェクトパス>/*.jsonl
```

Claude セッションの変換:

```bash
# 自動検出して一覧から選択
python3 log-replay.py --agent claude -f player

# ファイルを直接指定
python3 claude-log2model.py ~/.claude/projects/my-project/session.jsonl \
    -o session.model.json
```

プロジェクト名でフィルター:

```bash
python3 log-replay.py --agent claude --project my-project -f player
```

### OpenAI Codex CLI

Codex CLI はログを以下に書き込む:

```
~/.codex/sessions/<ネストパス>/*.jsonl
```

```bash
# 自動検出
python3 log-replay.py --agent codex -f player

# ファイルを直接指定
python3 codex-log2model.py ~/.codex/sessions/my-dir/session.jsonl \
    -o session.model.json

# パスの部分文字列でフィルター
python3 log-replay.py --agent codex --filter my-project -f html
```

### Gemini CLI

Gemini CLI はログを以下に書き込む:

```
~/.gemini/tmp/<プロジェクトディレクトリ>/chats/session-*.json
```

```bash
# 自動検出
python3 log-replay.py --agent gemini -f player

# ファイルを直接指定
python3 gemini-log2model.py ~/.gemini/tmp/my-project/chats/session-001.json \
    -o session.model.json
```

### Aider

Aider は作業ディレクトリの `.aider.chat.history.md` に会話履歴を書き込む。

```bash
# ファイルを直接指定（Aider には中央ログディレクトリがない）
python3 aider-log2model.py /path/to/project/.aider.chat.history.md \
    -o session.model.json

# レンダリング
python3 log-model-renderer.py session.model.json -f player -o out.html
```

### Cursor

Cursor は以下の SQLite データベースに会話データを保存する:

```
~/.cursor/                           （Linux）
~/.config/Cursor/                    （Linux 別パス）
~/Library/Application Support/Cursor/ （macOS）
%APPDATA%/Cursor/                    （Windows）
```

```bash
# 自動検出（既知のパスを検索）
python3 log-replay.py --agent cursor -f player

# アダプターが自動スキャン
python3 cursor-log2model.py -o session.model.json
```

---

## レンダリング

`session.model.json` を生成したら（または `log-replay.py` ラッパーを使って）、任意の形式に変換する:

```bash
# Markdown — プレーンテキスト
python3 log-model-renderer.py session.model.json -f md -o session.md

# HTML — 静的チャット UI（ライトまたはダークテーマ）
python3 log-model-renderer.py session.model.json -f html -t light   -o session.html
python3 log-model-renderer.py session.model.json -f html -t console -o session-dark.html

# Player — Alibai Mode 付きインタラクティブ再生
python3 log-model-renderer.py session.model.json -f player -o session.player.html

# Terminal — Claude Code UI 再現
python3 log-model-renderer.py session.model.json -f terminal -o session.terminal.html

# MP4 — playwright + ffmpeg が必要
python3 log-replay-mp4.py --agent claude session.jsonl -o out.mp4 \
    --width 1280 --height 720 --fps 30 --speed 2.0

# PDF — playwright が必要
python3 log-replay-pdf.py --agent claude session.jsonl -o out.pdf

# GIF — playwright + pillow（または ffmpeg）が必要
python3 log-replay-gif.py --agent claude session.jsonl -o out.gif
```

### Web UI

```bash
source .venv/bin/activate
python3 web_ui.py
# ブラウザで http://localhost:5000 を開く
```

Web UI はすべての対応エージェントのセッションを自動検出し、形式・テーマ・範囲・Alibai Mode の設定をグラフィカルに提供する。

---

## 次のステップ

- [エージェント](agents-ja.md) — ログ形式、アダプターの詳細、エージェントごとの既知の制限
- [レンダラー](renderers-ja.md) — 各出力形式のオプションリファレンス
- [アーキテクチャ](architecture-ja.md) — 3段パイプラインの仕組み
- [データモデル](data-model.md) — 共通モデル JSON スキーマ
