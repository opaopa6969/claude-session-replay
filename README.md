# claude-session-replay

Claude Code のセッションログ (JSONL) を Markdown / HTML / インタラクティブプレイヤーに変換するツール。

## セッションログの場所

```
~/.claude/projects/<プロジェクトパス>/*.jsonl
```

## 使い方

```bash
python3 claude-session-replay.py <input.jsonl> [options]
```

## 出力フォーマット

### Markdown (デフォルト)

```bash
python3 claude-session-replay.py session.jsonl
python3 claude-session-replay.py session.jsonl -o output.md
```

プレーンなMarkdownテキスト。User/Assistant の会話とツール使用をテキストで記録。

### HTML (静的)

```bash
python3 claude-session-replay.py session.jsonl -f html              # light テーマ
python3 claude-session-replay.py session.jsonl -f html -t console   # dark テーマ
```

チャットUI風の静的HTML。User は緑、Assistant は青の吹き出し表示。

### Player (再生プレイヤー)

```bash
python3 claude-session-replay.py session.jsonl -f player              # dark テーマ
python3 claude-session-replay.py session.jsonl -f player -t light     # light テーマ
```

メッセージを順番に再生できるインタラクティブHTMLプレイヤー。

### Terminal (Claude Code 風)

```bash
python3 claude-session-replay.py session.jsonl -f terminal
```

Claude Code のターミナルUIを忠実に再現したプレイヤー。

- `>` プロンプト付きのユーザー入力 (青背景)
- オレンジの左バー付き Assistant レスポンス
- ツールブロック: Read/Write/Edit/Bash/Grep/Glob/Task をリアルに表示
- スピナーアニメーション (orange `●` → green `✓`)
- テーブルのレンダリング対応

## オプション

| オプション | 説明 |
|---|---|
| `-f`, `--format` | 出力形式: `md`, `html`, `player`, `terminal` |
| `-t`, `--theme` | HTMLテーマ: `light` (デフォルト), `console` (dark) |
| `-o`, `--output` | 出力ファイルパス (省略時は入力ファイルの拡張子を変更) |

## キーボードショートカット (player / terminal)

| キー | 機能 |
|---|---|
| `Space` | 再生 / 一時停止 |
| `→` | 次のメッセージ |
| `←` | 前のメッセージ |
| `Home` | 最初に戻る |
| `End` | 最後まで表示 |
| `T` | ツールメッセージをスキップ (再生時) |
| `E` | 空ツールの表示/非表示 (TaskCreate等) |
| `D` | ツール詳細の表示/非表示 |

速度スライダーで 0.25x ~ 16x の再生速度に対応。
プログレスバーのクリックで任意の位置にジャンプ可能。

## 動作環境

- Python 3.6+
- 外部ライブラリ不要 (標準ライブラリのみ)
