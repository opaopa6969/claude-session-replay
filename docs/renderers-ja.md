[English version](renderers.md)

# レンダラー

claude-session-replay の各出力形式の詳細オプションリファレンス。

---

## 目次

- [概要](#概要)
- [Markdown](#markdown-md)
- [HTML](#html-html)
- [Player](#player-player)
  - [Alibai Mode](#alibai-mode)
- [Terminal](#terminal-terminal)
- [MP4](#mp4)
- [PDF](#pdf)
- [GIF](#gif)
- [共通オプション](#共通オプション)

---

## 概要

テキスト・HTML 形式はすべて `log-model-renderer.py` が生成する。動画・PDF・GIF レンダラーはヘッドレスブラウザを使用する別スクリプト。

| 形式 | フラグ | スクリプト | 依存関係 | インタラクティブ |
|-----|------|---------|---------|--------------|
| Markdown | `md` | `log-model-renderer.py` | なし | No |
| HTML | `html` | `log-model-renderer.py` | なし | No |
| Player | `player` | `log-model-renderer.py` | ブラウザ | Yes |
| Terminal | `terminal` | `log-model-renderer.py` | ブラウザ | Yes |
| MP4 | — | `log-replay-mp4.py` | playwright, ffmpeg | No |
| PDF | — | `log-replay-pdf.py` | playwright | No |
| GIF | — | `log-replay-gif.py` | playwright, pillow/ffmpeg | No |

HTML 出力はすべて**自己完結型** — CSS と JS をインラインに埋め込む。外部リソースなし、CDN なし、オフラインで動作。

---

## Markdown (`md`)

プレーンテキスト。任意のテキストエディターでの閲覧や他のツールへのパイプに使用する。

```bash
python3 log-model-renderer.py session.model.json -f md
python3 log-model-renderer.py session.model.json -f md -o session.md
```

### 構造

```markdown
## User

<メッセージテキスト>

## Assistant

<メッセージテキスト>

**Read**: `path/to/file`

> (ツール結果の内容)
```

### コンテンツマッピング

| モデルフィールド | レンダリング |
|---------------|-----------|
| `role: "user"` | `## User` 見出し |
| `role: "assistant"` | `## Assistant` 見出し |
| `text` | プレーンパラグラフ |
| `tool_uses` | 太字ツール名 + フォーマット済みパラメーター |
| `tool_results` | 引用ブロックで内容を表示 |
| `thinking` | 非表示 |
| `timestamp` | 非表示 |

---

## HTML (`html`)

静的チャット UI。JavaScript なし — HTML をサポートする任意のブラウザやビューワーで動作。

```bash
python3 log-model-renderer.py session.model.json -f html              # ライトテーマ（デフォルト）
python3 log-model-renderer.py session.model.json -f html -t console   # ダークテーマ
```

### オプション

| オプション | 値 | 説明 |
|-----------|---|-----|
| `-t` / `--theme` | `light`（デフォルト）、`console` | カラーテーマ |

### 外観

- ユーザーメッセージ: 緑の吹き出し、右寄せ
- アシスタントメッセージ: 青の吹き出し、左寄せ
- ツールブロック: コンパクトなフォーマット済みブロック
- 再生コントロールなし

---

## Player (`player`)

インタラクティブ HTML プレイヤー。メッセージを 1 件ずつ再生。**Alibai Mode**（タイムスタンプ可視化）を含む。

```bash
python3 log-model-renderer.py session.model.json -f player              # ダークテーマ（デフォルト）
python3 log-model-renderer.py session.model.json -f player -t light     # ライトテーマ
python3 log-model-renderer.py session.model.json -f player --range "1-50"
```

### オプション

| オプション | 値 | 説明 |
|-----------|---|-----|
| `-t` / `--theme` | `light`、`console`（デフォルト） | カラーテーマ |
| `--range` | 例: `1-50,53-` | メッセージ範囲フィルター |
| `--ansi-mode` | `strip`（デフォルト）、`color` | ANSI エスケープ処理 |

### 再生コントロール

| キー | 機能 |
|-----|-----|
| `Space` | 再生 / 一時停止 |
| `→` | 次のメッセージ |
| `←` | 前のメッセージ |
| `Home` | 先頭へ |
| `End` | 末尾へ |
| `g` | 指定時刻へジャンプ |
| `j` / `k` | メッセージ内スクロール |
| `T` | 再生中のツールメッセージをスキップ |
| `E` | 空ツールの表示切替 |
| `D` | ツール詳細の表示切替 |

速度スライダー: 0.25x〜16x。プログレスバーはクリックでシーク可能。

### Alibai Mode

Alibai Mode はアナログ時計と代替再生タイミングで実際のタイムスタンプを可視化する。

**時計表示**（チェックボックス）:
- **サイド時計** — メッセージごとに 44×44 px のアナログ時計
- **固定時計** — 100×100 px のアナログ時計を右下に固定

**再生モード**（ラジオボタン）:
- **Uniform**（デフォルト）— 均一間隔（800 ms ÷ 速度）
- **Real-time** — メッセージ間の実際の時間差を尊重
- **Compressed** — セッション全体を 60 秒に圧縮して相対比率で再生

---

## Terminal (`terminal`)

Claude Code のターミナル UI の再現。セッションをアニメーションターミナルとしてレンダリング — スクリーンキャスト用途に最適。

```bash
python3 log-model-renderer.py session.model.json -f terminal
python3 log-model-renderer.py session.model.json -f terminal --range "5-20"
```

### オプション

| オプション | 値 | 説明 |
|-----------|---|-----|
| `--range` | 例: `1-50` | メッセージ範囲フィルター |
| `--ansi-mode` | `strip`（デフォルト）、`color` | ANSI エスケープ処理 |

### 外観

- ユーザー入力: `>` プロンプト付き青背景
- アシスタントレスポンス: オレンジの左ボーダー
- ツールブロック: Read/Write/Edit/Bash/Grep/Glob/Task をリアルに再現
- スピナーアニメーション: オレンジ `●` → 緑 `✓`
- テーブルレンダリング対応

再生コントロールは Player と同一。

---

## MP4

ヘッドレスブラウザで Player または Terminal HTML を再生しながら録画し、FFmpeg で MP4 にエンコードする。

**依存関係**: `playwright`、`ffmpeg`（システムバイナリ）

```bash
python3 log-replay-mp4.py --agent claude session.jsonl \
    -f player -o out.mp4 \
    --width 1280 --height 720 --fps 30 --speed 2.0
```

### オプション

| オプション | デフォルト | 説明 |
|-----------|---------|-----|
| `--width` | 1280 | 動画幅（px） |
| `--height` | 720 | 動画高さ（px） |
| `--fps` | 30 | フレームレート |
| `--speed` | 2.0 | 再生速度倍率 |
| `-f` / `--format` | `player` | `player` または `terminal` |
| `-t` / `--theme` | `console` | カラーテーマ |
| `--range` | — | メッセージ範囲 |

### セットアップ

```bash
python3 -m pip install playwright
python3 -m playwright install

# Ubuntu/Debian
sudo apt-get install ffmpeg

# macOS
brew install ffmpeg
```

---

## PDF

ヘッドレスブラウザで HTML プレイヤーをレンダリングし、Playwright の印刷機能で PDF に出力する。

**依存関係**: `playwright`

```bash
python3 log-replay-pdf.py --agent claude session.jsonl -o out.pdf
```

### オプション

| オプション | デフォルト | 説明 |
|-----------|---------|-----|
| `-f` / `--format` | `html` | `html` または `player` |
| `-t` / `--theme` | `light` | カラーテーマ |
| `--range` | — | メッセージ範囲 |

### セットアップ

```bash
python3 -m pip install playwright
python3 -m playwright install
```

---

## GIF

ヘッドレスブラウザで再生中にスクリーンショットを撮影し、Pillow でアニメーション GIF に合成する。Pillow が利用不可の場合は FFmpeg にフォールバック。

**依存関係**: `playwright`、`pillow`（または `ffmpeg`）

```bash
python3 log-replay-gif.py --agent claude session.jsonl -o out.gif
```

### オプション

| オプション | デフォルト | 説明 |
|-----------|---------|-----|
| `-f` / `--format` | `player` | `player` または `terminal` |
| `-t` / `--theme` | `console` | カラーテーマ |
| `--speed` | 2.0 | 再生速度 |
| `--range` | — | メッセージ範囲 |

### セットアップ

```bash
python3 -m pip install playwright pillow
python3 -m playwright install
# または: フォールバックとして ffmpeg をインストール
```

---

## 共通オプション

`log-model-renderer.py` を使ったすべてのレンダラー呼び出しで使用可能:

| オプション | 説明 |
|-----------|-----|
| `-f` / `--format` | 出力形式: `md`, `html`, `player`, `terminal` |
| `-t` / `--theme` | テーマ: `light`, `console` |
| `-o` / `--output` | 出力ファイルパス（デフォルト: 入力ファイル名から派生） |
| `--range` | メッセージ範囲、例: `1-50,53-` |
| `--ansi-mode` | ANSI 処理: `strip`（デフォルト）または `color` |

### 範囲指定の構文

| 構文 | 意味 |
|-----|-----|
| `1-50` | 1〜50番目 |
| `53-` | 53番目〜最後 |
| `-10` | 1〜10番目 |
| `7` | 7番目のみ |

複数指定: `1-10,20-30,50-`
