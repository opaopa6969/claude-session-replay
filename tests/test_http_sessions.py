"""実際の ASGI アプリで200回再接続し、セッション辞書の残留を検出する。"""

import importlib.util
import json
from pathlib import Path

import pytest

# CLI 単体のテスト環境（Python 3.9 を含む）は MCP を導入しない。
# SDK を導入した環境では実 HTTP アプリの検証を必ず実行する。
pytest.importorskip("mcp.server.mcpserver", reason="HTTP 回帰テストには MCP SDK 2.x が必要")

import anyio
import httpx2 as httpx


# ファイルからロードし、SDK の mcp や他のテストモジュールと名前が衝突しないようにする。
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("session_regression_server", ROOT / "mcp_server.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

HEADERS = {
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-03-26",  # セッションを使う旧プロトコルでも検証する。
}


def _result(response):
    assert response.status_code == 200, response.text
    if response.headers["content-type"].startswith("text/event-stream"):
        message = next(line[5:].strip() for line in response.text.splitlines() if line.startswith("data:"))
        payload = json.loads(message)
    else:
        payload = response.json()
    assert "error" not in payload, payload
    return payload["result"]


def test_200_reconnects_do_not_retain_sessions():
    async def exercise():
        app = module.create_app()
        manager = module.mcp.session_manager
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
        with anyio.fail_after(60):
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1", headers=HEADERS) as client:
                    for cycle in range(200):
                        response = await client.post("/mcp", json={
                            "jsonrpc": "2.0", "id": cycle, "method": "initialize",
                            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                       "clientInfo": {"name": "session-regression", "version": "1"}},
                        })
                        assert _result(response)["protocolVersion"] == "2025-03-26"
                        assert "mcp-session-id" not in response.headers
                        initialized = await client.post("/mcp", json={
                            "jsonrpc": "2.0", "method": "notifications/initialized",
                        })
                        assert initialized.status_code == 202
                        listing = await client.post("/mcp", json={
                            "jsonrpc": "2.0", "id": 1000 + cycle, "method": "tools/list",
                        })
                        assert _result(listing)["tools"]
                        # stateless はセッションを持たないので DELETE は405。辞書も増えない。
                        deleted = await client.delete("/mcp")
                        assert deleted.status_code == 405
                        assert manager._server_instances == {}, cycle
                        assert manager._session_owners == {}, cycle
                    resources = await client.post("/mcp", json={
                        "jsonrpc": "2.0", "id": 2000, "method": "resources/list",
                    })
                    assert _result(resources)["resources"]
                    # shutdown 時の clear() による偽陽性を避け、lifespan の内側で確認する。
                    assert manager._server_instances == {}
    anyio.run(exercise)
