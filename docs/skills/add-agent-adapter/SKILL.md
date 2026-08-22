---
name: add-agent-adapter
description: 新しいエージェントアダプターを追加する手順
volta:
  version: 1
  namespace: session-replay
  locality: repo
  applies_when: 新しい agent のログ形式をサポートするとき
  requires:
    - log2model module
  min_role: MEMBER
  export: true
---

# add-agent-adapter: 新しいエージェントアダプターの追加

## 目的

新しい AI コーディングエージェントのセッションログ形式をサポートするためのアダプターを追加する。

## 手順

1. **`<agent>-log2model.py` の作成**
   - 既存アダプター（例: `claude-log2model.py`）を参考に、`build_model()`、`discover_sessions()`、`select_session()` を実装
   - 共通モデルスキーマ（`session-replay://schema`）に従う: `{source, agent, messages[]}`
   - 各 message: `{role: "user"|"assistant", text, tool_uses[], tool_results[], thinking[], timestamp}`

2. **`log-replay.py` への登録**
   - `--agent` choices に追加
   - log2model script mapping を追加

3. **`web_ui.py` への登録**
   - import と session discovery を追加
   - `/api/sessions/<agent>` ルートの分岐を追加

4. **`mcp_server.py` への登録**
   - `AGENTS` タプルに追加
   - `_get_adapter` の mapping に追加
   - `_build_model` に必要に応じて分岐を追加（`parse_messages` が必要な場合等）

5. **ドキュメント更新**
   - `docs/agent-adapters.md` を更新
   - `session-replay://guide` resource の対応 agent リストを更新

6. **テスト**
   - テストフィクスチャ（`tests/fixtures/`）にログサンプルを追加
   - アダプターのユニットテストを作成
