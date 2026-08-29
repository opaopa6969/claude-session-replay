"""Tests for log-model-renderer.py pure utility functions."""
import sys
from pathlib import Path

import pytest

# Load the module using conftest helper
sys.path.insert(0, str(Path(__file__).parent))
from conftest import load_module

renderer = load_module("log_model_renderer", "log-model-renderer.py")

parse_range_spec = renderer.parse_range_spec
filter_messages_by_range = renderer.filter_messages_by_range
strip_ansi = renderer.strip_ansi
format_tool_use = renderer.format_tool_use
format_tool_result_content = renderer.format_tool_result_content
truncate_text = renderer._truncate_text


# ---------------------------------------------------------------------------
# parse_range_spec
# ---------------------------------------------------------------------------

class TestParseRangeSpec:
    def test_empty_spec_returns_all_indices(self):
        result = parse_range_spec("", 10)
        assert result == list(range(10))

    def test_empty_spec_zero_total(self):
        result = parse_range_spec("", 0)
        assert result == []

    def test_single_range(self):
        result = parse_range_spec("1-3", 10)
        assert result == [0, 1, 2]

    def test_single_index(self):
        result = parse_range_spec("5", 10)
        assert result == [4]

    def test_open_end_range(self):
        # "3-" means from index 3 (1-based) to end
        result = parse_range_spec("3-", 5)
        assert result == [2, 3, 4]

    def test_open_start_range(self):
        # "-3" means first 3 (1-based indices 1, 2, 3)
        result = parse_range_spec("-3", 10)
        assert result == [0, 1, 2]

    def test_full_range(self):
        result = parse_range_spec("1-5", 5)
        assert result == [0, 1, 2, 3, 4]

    def test_single_index_first(self):
        result = parse_range_spec("1", 10)
        assert result == [0]

    def test_single_index_last(self):
        result = parse_range_spec("10", 10)
        assert result == [9]

    def test_out_of_bounds_index_ignored(self):
        result = parse_range_spec("20", 10)
        assert result == []

    def test_comma_separated_indices(self):
        result = parse_range_spec("1,3,5", 10)
        assert result == [0, 2, 4]

    def test_comma_separated_ranges(self):
        result = parse_range_spec("1-2,4-5", 10)
        assert result == [0, 1, 3, 4]

    def test_result_is_sorted(self):
        result = parse_range_spec("5,1,3", 10)
        assert result == sorted(result)

    def test_range_clamped_to_total(self):
        result = parse_range_spec("1-100", 5)
        assert result == [0, 1, 2, 3, 4]


# ---------------------------------------------------------------------------
# strip_ansi
# ---------------------------------------------------------------------------

class TestStripAnsi:
    def test_strips_red_color_code(self):
        text = "\x1b[31mHello\x1b[0m"
        assert strip_ansi(text) == "Hello"

    def test_strips_reset_code(self):
        text = "plain\x1b[0mtext"
        result = strip_ansi(text)
        assert "\x1b" not in result

    def test_plain_text_unchanged(self):
        text = "Hello, world!"
        assert strip_ansi(text) == text

    def test_empty_string(self):
        assert strip_ansi("") == ""

    def test_multiple_codes(self):
        text = "\x1b[1m\x1b[32mBold Green\x1b[0m"
        assert strip_ansi(text) == "Bold Green"

    def test_only_ansi_codes(self):
        text = "\x1b[31m\x1b[0m"
        assert strip_ansi(text) == ""

    def test_preserves_non_ansi_content(self):
        text = "\x1b[34mblue\x1b[0m and plain"
        result = strip_ansi(text)
        assert "blue" in result
        assert "plain" in result
        assert "\x1b" not in result


# ---------------------------------------------------------------------------
# format_tool_use
# ---------------------------------------------------------------------------

class TestFormatToolUse:
    def test_bash_tool_includes_name(self):
        tool_use = {"name": "Bash", "input": {"command": "echo hello"}}
        result = format_tool_use(tool_use)
        assert "Bash" in result

    def test_bash_tool_includes_command(self):
        tool_use = {"name": "Bash", "input": {"command": "echo hello"}}
        result = format_tool_use(tool_use)
        assert "echo hello" in result

    def test_read_tool_includes_file_path(self):
        tool_use = {"name": "Read", "input": {"file_path": "/tmp/foo.txt"}}
        result = format_tool_use(tool_use)
        assert "/tmp/foo.txt" in result

    def test_read_tool_includes_name(self):
        tool_use = {"name": "Read", "input": {"file_path": "/tmp/foo.txt"}}
        result = format_tool_use(tool_use)
        assert "Read" in result

    def test_write_tool_includes_file_path(self):
        tool_use = {"name": "Write", "input": {"file_path": "/tmp/out.txt", "content": "line1\nline2"}}
        result = format_tool_use(tool_use)
        assert "/tmp/out.txt" in result
        assert "Write" in result

    def test_write_tool_includes_line_count(self):
        tool_use = {"name": "Write", "input": {"file_path": "/tmp/out.txt", "content": "line1\nline2\nline3"}}
        result = format_tool_use(tool_use)
        assert "3" in result

    def test_grep_tool_includes_pattern_and_path(self):
        tool_use = {"name": "Grep", "input": {"pattern": "TODO", "path": "/src"}}
        result = format_tool_use(tool_use)
        assert "Grep" in result
        assert "TODO" in result
        assert "/src" in result

    def test_glob_tool(self):
        tool_use = {"name": "Glob", "input": {"pattern": "*.py"}}
        result = format_tool_use(tool_use)
        assert "Glob" in result
        assert "*.py" in result

    def test_task_tool(self):
        tool_use = {"name": "Task", "input": {"description": "Do something"}}
        result = format_tool_use(tool_use)
        assert "Task" in result
        assert "Do something" in result

    def test_unknown_tool_uses_name(self):
        tool_use = {"name": "MyCustomTool", "input": {}}
        result = format_tool_use(tool_use)
        assert "MyCustomTool" in result

    def test_missing_input_field(self):
        # Should not raise an error
        tool_use = {"name": "Bash"}
        result = format_tool_use(tool_use)
        assert "Bash" in result

    def test_missing_name_field(self):
        # Should not raise an error, uses "Unknown"
        tool_use = {"input": {"command": "ls"}}
        result = format_tool_use(tool_use)
        assert result is not None

    def test_bash_empty_command(self):
        tool_use = {"name": "Bash", "input": {"command": ""}}
        result = format_tool_use(tool_use)
        assert "Bash" in result

    def test_edit_tool_includes_file_path(self):
        tool_use = {"name": "Edit", "input": {"file_path": "/tmp/foo.py", "old_string": "a", "new_string": "b"}}
        result = format_tool_use(tool_use)
        assert "Edit" in result
        assert "/tmp/foo.py" in result

    def test_edit_tool_includes_diff_block(self):
        tool_use = {"name": "Edit", "input": {"file_path": "/tmp/foo.py", "old_string": "old line", "new_string": "new line"}}
        result = format_tool_use(tool_use)
        assert "```diff" in result
        assert "- old line" in result
        assert "+ new line" in result

    def test_edit_tool_truncates_long_strings(self):
        long_old = "x" * 300
        long_new = "y" * 300
        tool_use = {"name": "Edit", "input": {"file_path": "/tmp/foo.py", "old_string": long_old, "new_string": long_new}}
        result = format_tool_use(tool_use)
        # Old/new strings are truncated to 200 chars in the output
        assert "x" * 300 not in result
        assert "y" * 300 not in result
        assert "x" * 200 in result
        assert "y" * 200 in result


# ---------------------------------------------------------------------------
# format_tool_result_content
# ---------------------------------------------------------------------------

class TestFormatToolResultContent:
    def test_string_content_returned_as_is(self):
        assert format_tool_result_content("hello") == "hello"

    def test_empty_string_returned(self):
        assert format_tool_result_content("") == ""

    def test_list_of_text_blocks_joined(self):
        content = [
            {"type": "text", "text": "line1"},
            {"type": "text", "text": "line2"},
        ]
        assert format_tool_result_content(content) == "line1\nline2"

    def test_list_with_non_text_blocks_ignored(self):
        content = [
            {"type": "image", "text": "ignored"},
            {"type": "text", "text": "kept"},
        ]
        assert format_tool_result_content(content) == "kept"

    def test_empty_list_returns_empty_string(self):
        assert format_tool_result_content([]) == ""

    def test_list_blocks_missing_text_key_default_empty(self):
        content = [{"type": "text"}]
        assert format_tool_result_content(content) == ""

    def test_non_str_non_list_falls_back_to_str(self):
        assert format_tool_result_content(42) == "42"
        assert format_tool_result_content(None) == "None"

    def test_list_with_non_dict_entries_does_not_raise(self):
        # Non-dict entries are simply skipped (no .get on them)
        content = ["raw", {"type": "text", "text": "ok"}]
        assert format_tool_result_content(content) == "ok"


# ---------------------------------------------------------------------------
# _truncate_text
# ---------------------------------------------------------------------------

class TestTruncateText:
    def test_none_length_returns_text_untruncated(self):
        text, was_truncated = truncate_text("hello", None)
        assert text == "hello"
        assert was_truncated is False

    def test_zero_length_returns_text_untruncated(self):
        text, was_truncated = truncate_text("hello", 0)
        assert text == "hello"
        assert was_truncated is False

    def test_text_shorter_than_length_not_truncated(self):
        text, was_truncated = truncate_text("hi", 10)
        assert text == "hi"
        assert was_truncated is False

    def test_text_exactly_at_length_not_truncated(self):
        text, was_truncated = truncate_text("hello", 5)
        assert text == "hello"
        assert was_truncated is False

    def test_text_longer_than_length_truncated(self):
        text, was_truncated = truncate_text("hello world", 5)
        assert text == "hello"
        assert was_truncated is True

    def test_empty_text_not_truncated(self):
        text, was_truncated = truncate_text("", 5)
        assert text == ""
        assert was_truncated is False


# ---------------------------------------------------------------------------
# filter_messages_by_range
# ---------------------------------------------------------------------------

class TestFilterMessagesByRange:
    def test_empty_spec_returns_all_messages(self):
        msgs = ["a", "b", "c"]
        assert filter_messages_by_range(msgs, "") == ["a", "b", "c"]

    def test_empty_spec_on_empty_list_returns_empty(self):
        assert filter_messages_by_range([], "") == []

    def test_single_index(self):
        msgs = ["a", "b", "c"]
        assert filter_messages_by_range(msgs, "2") == ["b"]

    def test_range_subset(self):
        msgs = ["a", "b", "c", "d", "e"]
        assert filter_messages_by_range(msgs, "2-4") == ["b", "c", "d"]

    def test_range_beyond_total_clamped(self):
        msgs = ["a", "b"]
        # Spec asks for indices up to 10 but only 2 exist
        assert filter_messages_by_range(msgs, "1-10") == ["a", "b"]

    def test_open_end_range(self):
        msgs = ["a", "b", "c"]
        assert filter_messages_by_range(msgs, "2-") == ["b", "c"]

    def test_open_start_range(self):
        msgs = ["a", "b", "c"]
        assert filter_messages_by_range(msgs, "-2") == ["a", "b"]

    def test_comma_separated(self):
        msgs = ["a", "b", "c", "d", "e"]
        assert filter_messages_by_range(msgs, "1,3,5") == ["a", "c", "e"]

    def test_out_of_bounds_index_yields_empty(self):
        msgs = ["a", "b"]
        assert filter_messages_by_range(msgs, "99") == []

    def test_invalid_token_ignored(self):
        msgs = ["a", "b", "c"]
        # "abc" is not a valid integer; parse_range_spec skips it
        result = filter_messages_by_range(msgs, "abc")
        assert result == []
