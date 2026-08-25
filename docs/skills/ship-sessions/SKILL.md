---
name: ship-sessions
description: セッションログから個人情報をリダクションして出荷する手順
volta:
  version: 1
  namespace: session-replay
  locality: repo
  applies_when: セッションログを外部に共有・出荷するとき
  requires:
    - session-shipper.py
  min_role: MEMBER
  export: true
---

# ship-sessions: セッションログのリダクション付き出荷

## 目的

AI コーディングエージェントのセッションログには、API キー、トークン、ファイル内容、個人情報（PII）が含まれる。外部共有時は必ず redaction を通す。

## 手順

1. **対象セッションの特定**
   - `session-replay__list_sessions(agent=claude)` でセッション一覧を取得
   - 出荷対象の `path` を控える

2. **リダクション設定**
   - `session-shipper.py` の `--redact-pii` フラグを有効化
   - redaction パターン: API キー、トークン、ファイルパス、メールアドレス等
   - **注意**: `redact_pii` は十分テストされていない（README に警告あり）。手動確認を推奨

3. **出荷形式の選択**
   - JSON（共通モデル）: `session-replay__to_model` → JSON
   - Markdown: `session-replay__render(format=md)`
   - HTML Player: `session-replay__render(format=player)`
   - ZIP: `session-shipper.py` のファイルエクスポート

4. **OpenSearch 連携（オプション）**
   - `session-shipper.py` が OpenSearch エクスポートをサポート
   - トランスポート層: OpenSearch / File / DryRun

## 注意事項

- redaction が不十分な場合、秘密情報が漏洩するリスクがある
- 本番ログは `minRole:MEMBER` で保護されている
- MCP 経由でセッション内容を公開する場合、アクセス権限を確認すること
