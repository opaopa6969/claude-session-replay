# MCP 化ステータス — claude-session-replay

## 進捗

| 項目 | 状態 | 備考 |
|------|------|------|
| Phase 1 調査 (survey.json) | 完了 | decision=wrap |
| DESIGN.md | 完了 | docs/mcp/DESIGN.md |
| MCP サーバ実装 (mcp_server.py) | 完了 | 15 tools, 3 resources |
| e2e テスト | 完了 | 13 tests all pass |
| volta.service.json | 完了 | namespace=session-replay, port=9241 |
| deploy/run_mcp.sh | 完了 | |
| deploy/claude-session-replay-mcp.service | 完了 | systemd user unit |
| skills (SKILL.md) | 完了 | ship-sessions, add-agent-adapter |
| README MCP 節 | 完了 | |
| 既存テスト互換性 | 完了 | 80 tests all pass |
| issue-hub 協調 | 完了 | #295 #296 #297（返答待たず暫定仕様で進行） |
| commit & push | 完了 | |
| volta svc_add (dry-run) | 完了 | |
| volta svc_add (confirm) | 完了 | mcp 項追加（namespace=session-replay, port=9241） |
| gateway routes diff | 完了 | 自分の1件のみ変更（backend URL + min_role） |
| gateway routes apply | 完了 | job done, SIGHUP 済み |
| https://hostname/healthz 200 | 完了 | http://192.168.1.50:9241/healthz 200 |
| catalog__backend_status ready | 完了 | namespace=session-replay, status=ready, tools=15 |
| catalog__audit_backend | 完了 | 8 ok / 0 ng / 3 skip / 1 unknown |

## namespace / port

- **namespace**: `session-replay`（割当表 #44 の指定通り）
- **port**: 9241（割当表指定、machine_ports で空き確認済み）
- **host**: 192.168.1.50（prod）
- 既存の Flask Web UI (port 5000) とは独立したプロセス

## tools 一覧

| tool | 種別 | 備考 |
|------|------|------|
| list_sessions | 同期 | 5 agent 対応 |
| to_model | 同期 | 共通モデル JSON 変換 |
| render | 同期 | md/html/player/terminal |
| render_media_start | job 型 | MP4/PDF/GIF (Playwright) |
| render_media_status | job 型 | |
| render_media_result | job 型 | |
| search_session | 同期 | 1セッション内検索 |
| search_cross_start | job 型 | 全セッション横断検索 |
| search_cross_status | job 型 | |
| search_cross_result | job 型 | |
| stats_session | 同期 | 1セッション統計 |
| stats_overview_start | job 型 | |
| stats_overview_status | job 型 | |
| stats_overview_result | job 型 | |
| diff_sessions | 同期 | 2セッション比較 |

## resources

- `session-replay://spec` (application/json)
- `session-replay://guide` (text/markdown)
- `session-replay://schema` (application/json)

## skills

- `ship-sessions` (locality: repo)
- `add-agent-adapter` (locality: repo)

## issue-hub

| issue | 相手 | 内容 |
|-------|------|------|
| #295 | agent-log-replayer | 機能重複の役割分担確認 |
| #296 | volta-index | セッションパスの相互参照形式 |
| #297 | kamishibai | セッション録画を台本素材として活用 |

## 既知の制限

1. **codex/aider/cursor の search_session が機能しない**: search_utils._build_common_model が parse_messages を呼ぶが、codex/aider/cursor には parse_messages がない。claude/gemini は正常動作。to_model, stats_session, diff_sessions は独自の _build_model を使うので全 agent で動作する。
2. **MP4/PDF/GIF レンダリングは Playwright 依存**: 環境に未インストールの場合はエラーになる。
3. **ジョブ状態はメモリ内**: プロセス再起動で消える（read-only ツールなので再実行で復旧）。
4. **redact_pii は十分テストされていない**: 外部共有時は手動確認を推奨。

## 持ち主への質問（解決済み）

Phase 1 で未決だった項目:
1. agent-log-replayer との統合 → 暫定: 独立並存（issue #295 で協調）
2. ジョブ状態ファイルの配置 → メモリ内（プロセス再起動で消える）
3. MCP エンドポイントの構成 → 独立した Python MCP サーバ（mcp_server.py）
4. volta.service.json の配置 → Phase 2 で実装（完了）
5. アクセス権限 → minRole:MEMBER
