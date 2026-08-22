# MCP 化設計 — claude-session-replay

## 1. namespace と種別

- **namespace**: `session-replay`
- **種別**: `wrap`（既存 Python モジュールを MCP tool として公開）
- **port**: 9241（割当表 MCPIFY-phase2-plan.md #44 の指定。既存サービス web_ui.py は port 5000 で別プロセス稼働中。MCP サーバは独立プロセス）
- **host**: 192.168.1.50（prod）
- **runtime**: systemd
- **min_role**: MEMBER

既存の volta カタログ（id=claude-session-replay, port=5000, Flask Web UI）とは独立した MCP サーバプロセスを立てる。同じ `volta.service.json` に `mcp` 項を追加し、ファサード経由で `session-replay__*` tools を公開する。

## 2. tools 表

| name | 目的 | 入力 schema（要点） | 出力の形 | 副作用 | dry-run | job 型 | 所要時間 | min_role |
|------|------|---------------------|----------|--------|---------|--------|----------|----------|
| `list_sessions` | 指定 agent のセッション一覧を取得 | `agent: claude\|codex\|gemini\|aider\|cursor` | `[{path, project, size, mtime, preview}]` | read | — | no | <1s | MEMBER |
| `to_model` | セッションログを共通モデル JSON に変換 | `agent, session_path` | `{source, agent, messages[]}` | read | — | no | <5s | MEMBER |
| `render` | セッションを md/html/player/terminal にレンダリング | `agent, session_path, format: md\|html\|player\|terminal, theme?, range?` | `{format, content}` | read | — | no | <10s | MEMBER |
| `render_media_start` | MP4/PDF/GIF レンダリングジョブを開始 | `agent, session_path, media_type: mp4\|pdf\|gif, width?, height?, fps?, speed?` | `{job_id, state, hint}` | read | no | **yes** | 30s+ | MEMBER |
| `render_media_status` | メディアレンダリングジョブの進捗 | `job_id` | `{job_id, state, progress, log_tail}` | read | — | — | <1s | MEMBER |
| `render_media_result` | メディアレンダリングジョブの成果物パス | `job_id` | `{job_id, state, output_path, error?}` | read | — | — | <1s | MEMBER |
| `search_session` | 1セッション内を検索 | `agent, session_path, query, scope?, case_sensitive?, regex?, max_matches?` | `{matches[], total_matches}` | read | — | no | <5s | MEMBER |
| `search_cross_start` | 全セッション横断検索ジョブを開始 | `agents[], query, scope?, case_sensitive?, regex?, max_sessions?, max_matches_per_session?` | `{job_id, state, hint}` | read | no | **yes** | 30s+ | MEMBER |
| `search_cross_status` | 横断検索ジョブの進捗 | `job_id` | `{job_id, state, progress}` | read | — | — | <1s | MEMBER |
| `search_cross_result` | 横断検索ジョブの結果 | `job_id` | `{job_id, state, results[], stats}` | read | — | — | <1s | MEMBER |
| `stats_session` | 1セッションの統計 | `agent, session_path` | `{message_count, tool_usage, duration, ...}` | read | — | no | <5s | MEMBER |
| `stats_overview_start` | 全セッション統計概要ジョブを開始 | `agents[]` | `{job_id, state, hint}` | read | no | **yes** | 30s+ | MEMBER |
| `stats_overview_result` | 全セッション統計概要の結果 | `job_id` | `{job_id, state, overview}` | read | — | — | <1s | MEMBER |
| `diff_sessions` | 2セッションを比較 | `agent, session_a, session_b` | `{message_diff, tool_diff, ...}` | read | — | no | <5s | MEMBER |

### job 型の設計

MP4/PDF/GIF レンダリング（Playwright 必須）、cross-session search（全セッションスキャン）、stats overview（全セッションスキャン）は 30 秒を超える可能性が高いため job 型とする。ジョブ状態はメモリ内 dict で管理（プロセス再起動で消える。永続化は不要＝read-only ツールなので再実行で復旧）。バックグラウンドスレッドで実行。

## 3. resources 表

| uri | 内容 | mime |
|-----|------|------|
| `session-replay://spec` | 能力の機械可読仕様（capabilities, compositions, depends_on, health, docs） | application/json |
| `session-replay://guide` | 使い方ガイド（接続方法、ワークフロー、注意事項） | text/markdown |
| `session-replay://schema` | 共通モデル JSON スキーマ | application/json |

## 4. prompts / skills

### prompts
なし（固定手順は guide resource と skill で配る）。

### skills

| name | 用途 | locality | applies_when | requires | min_role |
|------|------|----------|---------------|----------|----------|
| `ship-sessions` | セッションログから個人情報をリダクションして出荷する手順 | repo | セッションログを外部に共有・出荷するとき | session-shipper.py | MEMBER |
| `add-agent-adapter` | 新しいエージェントアダプターを追加する手順 | repo | 新しい agent のログ形式をサポートするとき | log2model module | MEMBER |

skill ファイルは `docs/skills/<name>/SKILL.md` に配置し、resource `skill://<name>` でも返す。

## 5. 組み合わせ例

1. **セッション録画を紙芝居の素材にする**
   - `index__agent_list` → `session-replay__list_sessions(agent=claude)` → `session-replay__render(agent=claude, format=player)` → `kamishibai__render_start`
   - データ: agent セッションパス → player HTML → kamishibai シナリオ素材

2. **横断検索で該当セッションを特定し共有用 Markdown を生成**
   - `session-replay__search_cross_start(query='refactor')` → `search_cross_result` → `session-replay__stats_session` → `session-replay__render(format=md)`
   - データ: 検索クエリ → マッチしたセッションパス → 統計 → Markdown 出力

3. **共通モデル JSON を他サービスの入力にする**
   - `session-replay__to_model` → 共通モデル JSON → 独自分析ツール / `session-replay://schema` でスキーマ確認
   - データ: セッションログ → 正規化された {source, agent, messages[]}

## 6. 依存と協調

| 相手 repo | 方向 | 能力 | 合意したいこと | issue-hub |
|-----------|------|------|----------------|-----------|
| `AskOS-workspace/agent-log-replayer` | depends_on | ブラウザベースのリアルタイムリプレイ（Node.js, WebSocket）。機能重複あり | 役割分担の境界（claude-session-replay=変換・検索・統計・多フォーマット出力、agent-log-replayer=リアルタイム表示）。暫定: 独立並存 | 要 |
| `tools-workspace/issue-broker` | provides_to | セッションログの共通モデル JSON をブローカー経由で配信可能 | 共通モデル JSON の入出力形式の合意。暫定: `session-replay://schema` に従う | 要 |
| `volta-index` | provides_to | `index__agent_list` / `index__agent_status` と組み合わせて「どのエージェントが何をしたか」を再生 | セッションパスの相互参照形式。暫定: ファイルパス文字列 | 要 |
| `kamishibai` | provides_to | セッション録画（player HTML / MP4 / GIF）を台本素材として活用 | `render_media_result` の成果物パスを kamishibai が参照可能な形式に。暫定: ローカルファイルパス | 要 |

### 暫定仕様（返答を待たず進める）

- **共通モデル JSON 形式**: `session-replay://schema` resource で自己記述。他サービスはこのスキーマに従う。
- **セッションパス参照**: 絶対ファイルパス文字列。`list_sessions` が返す `path` フィールドをそのまま使う。
- **メディア成果物**: ローカルファイルパス（`/tmp/session-replay-jobs/<job_id>/output.<ext>`）。URL 配信は将来課題。

## 7. 非対応にした候補と理由

| 候補 | 理由 |
|------|------|
| `stream_session`（SSE ポーリング） | MCP の tool call モデルに合わない（SSE ストリームは MCP tool の戻り値として表現しにくい）。将来は MCP の streaming response で対応可能。 |
| `preview` / `editor-content` / `apply-to-output` / `apply-to-session-log` | Web UI 専用機能（ブラウザインタラクション）。MCP tool としての価値が低い。 |
| `export-pdf` / `export-jsonl-zip` / `export-all-zip` | `render_media_start`（PDF）と `to_model`（JSON）で代替可能。ZIP 圧縮は tool ではなくファイルシステム操作。 |

## 8. 参加方法

### volta.service.json（root）

```json
{
  "id": "claude-session-replay",
  "name": "Claude Session Replay",
  "description": "AI coding agent session logs → common model → Markdown/HTML/Player/Terminal/MP4/PDF/GIF",
  "type": "python",
  "hostname": "replay-hvu.unlaxer.org",
  "port": 5000,
  "host": "192.168.1.50",
  "runtime": "systemd",
  "exec_start": "/home/opa/claude-session-replay/run.sh",
  "user": "opa",
  "auth": "minRole:MEMBER",
  "health_check": "/healthz",
  "tags": ["llm", "recording", "analysis", "mcp"],
  "repo_url": "https://github.com/opaopa6969/claude-session-replay",
  "mcp": {
    "enabled": true,
    "port": 9241,
    "path": "/mcp",
    "namespace": "session-replay",
    "min_role": "MEMBER",
    "timeoutMs": 110000,
    "description": "AI エージェントセッションログの変換・検索・統計・レンダリング"
  }
}
```

- **port**: 9241（割当表指定、machine_ports で空き確認済み）
- **host**: 192.168.1.50（prod）
- **runtime**: systemd
- **auth**: minRole:MEMBER（セッションログに個人情報が含まれる可能性があるため VIEWER は不可）
- **health_check**: /healthz

### deploy 構成

- `mcp_server.py`（root）: MCP サーバ本体（FastMCP, Streamable HTTP, /mcp, /healthz）
- `deploy/claude-session-replay-mcp.service`: systemd user unit
- `run_mcp.sh`: MCP サーバ起動スクリプト
- `deploy/claude-session-replay.service`: 既存 Web UI 用 systemd unit（新規追加、既存の docker compose がある環境では不要）

## 9. テスト方針

### e2e テスト（tests/test_mcp_server.py）

1. サーバ起動 → `/healthz` が 200
2. MCP クライアントで `tools/list` → 14 tools が返る
3. `list_sessions(agent=claude)` → セッション一覧が返る
4. `to_model` → 共通モデル JSON が返る
5. `render(format=md)` → Markdown 文字列が返る
6. `search_session` → マッチ結果が返る
7. `stats_session` → 統計が返る
8. `diff_sessions` → 差分が返る
9. `render_media_start` → job_id が返る（dry-run 相当: 実際のレンダリングは Playwright 依存なのでスキップ or モック）
10. `session-replay://spec` resource → JSON が返る
11. `session-replay://guide` resource → Markdown が返る
12. `session-replay://schema` resource → JSON が返る

### 手動 smoke test

```bash
python3 mcp_server.py &
curl http://127.0.0.1:9241/healthz
# MCP クライアントで tools/list, list_sessions を叩く
```
