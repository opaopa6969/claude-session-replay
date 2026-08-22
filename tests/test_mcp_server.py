"""End-to-end tests for the session-replay MCP server.

Requires the server running on PORT (default 9241).
Run: python3 mcp_server.py & pytest tests/test_mcp_server.py
"""
import json
import os
import asyncio

import pytest
import urllib.request

PORT = int(os.environ.get("MCP_TEST_PORT", "9241"))
BASE = f"http://127.0.0.1:{PORT}"


def _run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if asyncio.get_event_loop().is_running() else asyncio.run(coro)


def _get_client():
    from mcp import Client
    return Client(f"{BASE}/mcp")


def _call_tool(tool_name, args):
    async def _do():
        async with _get_client() as client:
            result = await client.call_tool(tool_name, args)
            if result.is_error:
                return result
            return json.loads(result.content[0].text)
    return asyncio.run(_do())


def _list_tools():
    async def _do():
        async with _get_client() as client:
            tools = await client.list_tools()
            return tools.tools
    return asyncio.run(_do())


def _read_resource(uri):
    async def _do():
        async with _get_client() as client:
            result = await client.read_resource(uri)
            return result.contents
    return asyncio.run(_do())


def _list_resources():
    async def _do():
        async with _get_client() as client:
            result = await client.list_resources()
            return result.resources
    return asyncio.run(_do())


class TestHealthz:
    def test_healthz(self):
        resp = urllib.request.urlopen(f"{BASE}/healthz")
        assert resp.status == 200
        body = json.loads(resp.read())
        assert body["ok"] is True
        assert body["name"] == "session-replay"


class TestToolsList:
    def test_tool_count(self):
        tools = _list_tools()
        names = [t.name for t in tools]
        expected = [
            "list_sessions", "to_model", "render",
            "search_session", "stats_session", "diff_sessions",
            "render_media_start", "render_media_status", "render_media_result",
            "search_cross_start", "search_cross_status", "search_cross_result",
            "stats_overview_start", "stats_overview_result",
        ]
        for name in expected:
            assert name in names, f"Missing tool: {name}"

    def test_tool_annotations(self):
        tools = _list_tools()
        for t in tools:
            assert t.annotations is not None, f"{t.name} missing annotations"


class TestResources:
    def test_spec(self):
        contents = _read_resource("session-replay://spec")
        assert len(contents) >= 1
        spec = json.loads(contents[0].text)
        assert spec["namespace"] == "session-replay"
        assert len(spec["capabilities"]) >= 14

    def test_guide(self):
        contents = _read_resource("session-replay://guide")
        assert len(contents) >= 1
        text = contents[0].text
        assert "session-replay" in text
        assert len(text) > 100

    def test_schema(self):
        contents = _read_resource("session-replay://schema")
        assert len(contents) >= 1
        schema = json.loads(contents[0].text)
        assert schema["title"] == "Session Common Model"
        assert "messages" in schema["properties"]


class TestTools:
    def test_list_sessions(self):
        data = _call_tool("list_sessions", {"agent": "claude"})
        assert "sessions" in data
        assert "count" in data
        assert isinstance(data["sessions"], list)

    def test_list_sessions_invalid_agent(self):
        from mcp import Client
        async def _do():
            async with Client(f"{BASE}/mcp") as client:
                return await client.call_tool("list_sessions", {"agent": "invalid"})
        result = asyncio.run(_do())
        assert result.is_error

    @pytest.fixture
    def first_session(self):
        data = _call_tool("list_sessions", {"agent": "claude"})
        if data["count"] == 0:
            pytest.skip("No claude sessions")
        return data["sessions"][0]["path"]

    def test_to_model(self, first_session):
        data = _call_tool("to_model", {
            "agent": "claude",
            "session_path": first_session,
        })
        assert data["agent"] == "claude"
        assert "messages" in data
        assert isinstance(data["messages"], list)

    def test_render_md(self, first_session):
        data = _call_tool("render", {
            "agent": "claude",
            "session_path": first_session,
            "format": "md",
        })
        assert data["format"] == "md"
        assert len(data["content"]) > 0

    def test_search_session(self, first_session):
        data = _call_tool("search_session", {
            "agent": "claude",
            "session_path": first_session,
            "query": "a",
        })
        assert "matches" in data
        assert "total_matches" in data

    def test_stats_session(self, first_session):
        data = _call_tool("stats_session", {
            "agent": "claude",
            "session_path": first_session,
        })
        assert "message_count" in data or "agent" in data

    def test_search_cross_job(self):
        data = _call_tool("search_cross_start", {
            "agents": ["claude"],
            "query": "test",
            "max_sessions": 3,
        })
        assert "job_id" in data

        import time; time.sleep(3)

        status = _call_tool("search_cross_status", {"job_id": data["job_id"]})
        assert status["state"] in ("running", "done", "error")

        if status["state"] == "done":
            final = _call_tool("search_cross_result", {"job_id": data["job_id"]})
            assert "results" in final
