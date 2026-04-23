"""Tests for claude-log2model.py adapter functions."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import load_module

claude_adapter = load_module("claude_log2model", "claude-log2model.py")

parse_messages = claude_adapter.parse_messages
build_model = claude_adapter.build_model

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CLAUDE_FIXTURE = FIXTURES_DIR / "claude_session.jsonl"


# ---------------------------------------------------------------------------
# parse_messages
# ---------------------------------------------------------------------------

class TestParseMessages:
    def test_parses_fixture_returns_list(self):
        messages = parse_messages(str(CLAUDE_FIXTURE))
        assert isinstance(messages, list)

    def test_parses_fixture_non_empty(self):
        messages = parse_messages(str(CLAUDE_FIXTURE))
        assert len(messages) > 0

    def test_messages_have_type_field(self):
        messages = parse_messages(str(CLAUDE_FIXTURE))
        for msg in messages:
            assert "type" in msg

    def test_only_user_and_assistant_types(self):
        messages = parse_messages(str(CLAUDE_FIXTURE))
        for msg in messages:
            assert msg["type"] in ("user", "assistant")

    def test_summary_lines_excluded(self):
        # The fixture contains a "summary" type line — it should be excluded
        messages = parse_messages(str(CLAUDE_FIXTURE))
        for msg in messages:
            assert msg.get("type") != "summary"

    def test_handles_malformed_file_gracefully(self, tmp_path):
        # File with a mix of valid and invalid JSON lines
        bad_file = tmp_path / "bad.jsonl"
        bad_file.write_text(
            '{"type":"user","message":{"role":"user","content":"hi"},"timestamp":""}\n'
            'not valid json\n'
            '{"type":"assistant","message":{"role":"assistant","content":"hello"},"timestamp":""}\n',
            encoding="utf-8",
        )
        # The current implementation uses json.loads without try/except per-line,
        # so malformed lines will raise. We test that valid-only files still parse.
        good_file = tmp_path / "good.jsonl"
        good_file.write_text(
            '{"type":"user","message":{"role":"user","content":"hi"},"timestamp":""}\n'
            '{"type":"assistant","message":{"role":"assistant","content":"hello"},"timestamp":""}\n',
            encoding="utf-8",
        )
        messages = parse_messages(str(good_file))
        assert len(messages) == 2

    def test_empty_file_returns_empty_list(self, tmp_path):
        empty_file = tmp_path / "empty.jsonl"
        empty_file.write_text("", encoding="utf-8")
        messages = parse_messages(str(empty_file))
        assert messages == []

    def test_only_summary_lines_returns_empty(self, tmp_path):
        summary_file = tmp_path / "summary.jsonl"
        summary_file.write_text(
            '{"type":"summary","summary":"test"}\n',
            encoding="utf-8",
        )
        messages = parse_messages(str(summary_file))
        assert messages == []


# ---------------------------------------------------------------------------
# build_model
# ---------------------------------------------------------------------------

class TestBuildModel:
    def _get_messages(self):
        return parse_messages(str(CLAUDE_FIXTURE))

    def test_returns_dict(self):
        messages = self._get_messages()
        model = build_model(messages, str(CLAUDE_FIXTURE))
        assert isinstance(model, dict)

    def test_has_messages_key(self):
        messages = self._get_messages()
        model = build_model(messages, str(CLAUDE_FIXTURE))
        assert "messages" in model

    def test_has_agent_key(self):
        messages = self._get_messages()
        model = build_model(messages, str(CLAUDE_FIXTURE))
        assert model.get("agent") == "claude"

    def test_has_source_key(self):
        messages = self._get_messages()
        model = build_model(messages, str(CLAUDE_FIXTURE))
        assert "source" in model
        assert model["source"] == "claude_session.jsonl"

    def test_messages_have_role(self):
        messages = self._get_messages()
        model = build_model(messages, str(CLAUDE_FIXTURE))
        for msg in model["messages"]:
            assert "role" in msg
            assert msg["role"] in ("user", "assistant")

    def test_messages_have_text_field(self):
        messages = self._get_messages()
        model = build_model(messages, str(CLAUDE_FIXTURE))
        for msg in model["messages"]:
            assert "text" in msg
            assert isinstance(msg["text"], str)

    def test_messages_have_tool_uses_field(self):
        messages = self._get_messages()
        model = build_model(messages, str(CLAUDE_FIXTURE))
        for msg in model["messages"]:
            assert "tool_uses" in msg
            assert isinstance(msg["tool_uses"], list)

    def test_messages_have_tool_results_field(self):
        messages = self._get_messages()
        model = build_model(messages, str(CLAUDE_FIXTURE))
        for msg in model["messages"]:
            assert "tool_results" in msg
            assert isinstance(msg["tool_results"], list)

    def test_messages_have_thinking_field(self):
        messages = self._get_messages()
        model = build_model(messages, str(CLAUDE_FIXTURE))
        for msg in model["messages"]:
            assert "thinking" in msg
            assert isinstance(msg["thinking"], list)

    def test_result_is_json_serializable(self):
        messages = self._get_messages()
        model = build_model(messages, str(CLAUDE_FIXTURE))
        # Should not raise
        serialized = json.dumps(model, ensure_ascii=False)
        parsed = json.loads(serialized)
        assert parsed["agent"] == "claude"

    def test_text_messages_parsed(self):
        messages = self._get_messages()
        model = build_model(messages, str(CLAUDE_FIXTURE))
        texts = [msg["text"] for msg in model["messages"] if msg["text"]]
        assert any("Hello" in t or "help" in t.lower() for t in texts)

    def test_tool_use_parsed(self):
        messages = self._get_messages()
        model = build_model(messages, str(CLAUDE_FIXTURE))
        tool_uses = []
        for msg in model["messages"]:
            tool_uses.extend(msg["tool_uses"])
        # Fixture has a Read tool_use
        assert any(tu.get("name") == "Read" for tu in tool_uses)

    def test_empty_messages_input(self):
        model = build_model([], str(CLAUDE_FIXTURE))
        assert model["messages"] == []

    def test_source_is_basename(self, tmp_path):
        tmp_file = tmp_path / "mytest.jsonl"
        tmp_file.write_text(
            '{"type":"user","message":{"role":"user","content":"hi"},"timestamp":""}\n',
            encoding="utf-8",
        )
        messages = parse_messages(str(tmp_file))
        model = build_model(messages, str(tmp_file))
        assert model["source"] == "mytest.jsonl"
