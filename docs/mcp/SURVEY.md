# MCP 化調査 — claude-session-replay

## 概要

**claude-session-replay** は、AI コーディングエージェント（Claude Code / Codex CLI / Gemini CLI / Aider / Cursor）のセッションログを共通モデルに正規化し、Markdown / HTML / Player / Terminal / MP4 / PDF / GIF に変換・再生するツールチェーン。

3 段パイプライン（Capture → Normalize → Render）で構成され、5 つのエージェントアダプター、7 つのレンダラー、Flask Web UI、TUI、セッションシッパー（OpenSearch 連携）、検索エンジン、統計エンジンを持つ。

**種別**: `cli`（CLI 中心だが Flask Web UI + REST API も持つ）

**既存の稼働状況**: volta カタログに `claude-session-replay` として登録済み。URL `https://replay-hvu.unlaxer.org`、`/healthz` 200、docker / systemd / source で複数環境稼働中。ただし MCP 項は未設定（`backend: null`）。

## 判定と理由

**判定: `wrap`** — 既存の REST API を薄く包む

既に volta カタログに登録済みで、Flask Web UI が 18 の REST API エンドポイントを提供している。MCP 化は `web_ui.py` の横に `/mcp` エンドポイント（Streamable HTTP）を追加し、既存のエンドポイントを MCP tool として公開すれば足りる。新規サーバ実装は不要。

エージェントが「セッションログを発見・変換・検索・分析・レンダリング」する能力は、volta 内の他サービス（`index__agent_*` のオーケストレーション、`kamishibai` の動画化）と組み合わせる明確な価値がある。

## 公開候補

| kind | name | io | 副作用 | 長時間 | 対応する既存実装 |
|------|------|----|--------|--------|-----------------|
| tool | `list_sessions` | agent → [{path, project, size, mtime, preview}] | read | no | `/api/sessions/<agent>` |
| tool | `to_model` | agent + session_path → common model JSON | read | no | `*_log2model.build_model()` |
| tool | `render` | agent + session_path + format + theme + range → HTML/MD | read | no | `/api/convert` |
| tool | `render_mp4` | agent + session_path + (width,height,fps,speed) → MP4 | read | **yes** | `log-replay-mp4.py` |
| tool | `render_pdf` | agent + session_path → PDF | read | **yes** | `log-replay-pdf.py` |
| tool | `render_gif` | agent + session_path → GIF | read | **yes** | `log-replay-gif.py` |
| tool | `search_session` | agent + session_path + query + options → matches | read | no | `/api/search/within-session` |
| tool | `search_cross` | [agent] + query + options → results + stats | read | **yes** | `/api/search/cross-session` |
| tool | `stats_session` | agent + session_path → stats | read | no | `/api/stats/session` |
| tool | `stats_overview` | [agent] → overview | read | **yes** | `/api/stats/overview` |
| tool | `diff_sessions` | agent + session_a + session_b → diff | read | no | `/api/diff` |
| tool | `stream_session` | agent + session_path + poll → SSE stream | read | **yes** | `/api/stream/<agent>` |
| resource | `spec` | `replay://spec` | — | — | 能力の機械可読仕様 |
| resource | `guide` | `replay://guide` | — | — | 使い方 |
| resource | `common_model_schema` | `replay://schema` | — | — | 共通モデル JSON スキーマ |
| skill | `ship-sessions` | — | — | — | セッションログのリダクション付き出荷手順 (locality: repo) |
| skill | `add-agent-adapter` | — | — | — | 新しいエージェントアダプター追加手順 (locality: repo) |

**長時間処理（job 型）**: `render_mp4` / `render_pdf` / `render_gif`（Playwright 必須、30 秒超の可能性）、`search_cross` / `stats_overview`（全セッションスキャン）、`stream_session`（ポーリング）は job 型（`xxx_start` → `xxx_status` → `xxx_result`）で実装する。

## 組み合わせ例

1. `index__agent_list` → `replay__list_sessions(agent=claude)` → `replay__render(agent=claude, format=player)` → `kamishibai__render_start`（セッション録画を紙芝居の素材にする）
2. `replay__search_cross(query='refactor')` → `replay__stats_session` で該当セッションの規模を把握 → `replay__render(format=md)` で共有用 Markdown を生成
3. `replay__to_model` → 共通モデル JSON を他サービス（独自分析ツール等）の入力にする

## 依存と協調

| 相手 repo | 方向 | 能力 | exists_now | 備考 |
|-----------|------|------|-----------|------|
| `agent-log-replayer` | depends_on | ブラウザベースのリアルタイムリプレイ（xterm.js + WebSocket）。Node.js 製で WebSocket リアルタイム表示に特化。機能重複あり | yes | 独立。本リポジトリは Python 製で変換パイプライン・検索・統計・多フォーマット出力が主。統合の可能性は Phase 2 で検討 |
| `agent-log-broker` | provides_to | セッションログの共通モデル JSON をブローカー経由で他コンシューマーに配信可能。`session-shipper.py` が OpenSearch/file エクスポートを持つが、broker 連携は未実装 | yes | トランスポート層が OpenSearch/File/DryRun のみ |
| `volta-index` | provides_to | `index__agent_list` / `index__agent_status` が稼働中エージェントのセッションを管理。`replay__list_sessions` と組み合わせて「どのエージェントが何をしたか」を再生できる | yes | 既に volta カタログに登録済み |
| `kamishibai` | provides_to | セッション録画（player HTML / MP4 / GIF）を台本素材として活用可能 | yes | `replay__render_mp4` の出力を `kamishibai__render_start` の入力にする絵が描ける |

## ライブラリのサーバ化

該当しない。既にサーバ（Flask Web UI）が稼働中。必要な作業は `/mcp` エンドポイントの追加と `volta.service.json` のみ。

## リスク

1. **秘密情報漏洩**: セッションログに API キー・トークン・ファイル内容が含まれる。MCP 経由で外部に公開する場合、redaction（`session-shipper.py` の機能）を必ず通す。現状 `redact_pii` フラグは十分テストされていない（README に警告あり）。
2. **長時間レンダリング**: MP4 / GIF / PDF は Playwright（ヘッドレスブラウザ）を要し、30 秒超の可能性が高い。job 型で実装する。
3. **全セッションスキャンの負荷**: `search_cross` / `stats_overview` は全セッションをスキャンする。`max_sessions` 制限とタイムアウトを設定する。
4. **Flask 開発サーバー**: 本番向きでない（SPEC.md も Gunicorn 推奨）。MCP エンドポイント追加時も同様の制約。
5. **機能重複**: `agent-log-replayer`（Node.js 製）と機能が重複。どちらを主とするか整理が必要。

## 持ち主への質問

1. `agent-log-replayer`（Node.js 製、WebSocket リアルタイム）と `claude-session-replay`（Python 製、変換パイプライン）は統合すべきか、役割分担するか？
2. MP4/GIF/PDF の長時間レンダリングを job 型にする場合、状態ファイル（job ID → 成果物）をどこに置くか（`~/.claude-replay/` 配下か、一時ディレクトリか）？
3. MCP エンドポイントは既存の `web_ui.py` Flask アプリに `/mcp` を追加する形がよいか、独立した Python MCP サーバを立てる形がよいか？
4. `volta.service.json` をリポジトリ root に置くタイミング（Phase 2 実装時）
5. セッションログのアクセス権限: MCP 経由でセッション内容を公開する場合、どの role まで許可するか
