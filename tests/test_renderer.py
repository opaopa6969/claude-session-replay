"""Tests for log-model-renderer.py pure utility functions."""
import sys
from pathlib import Path

import pytest

# Load the module using conftest helper
sys.path.insert(0, str(Path(__file__).parent))
from conftest import load_module

renderer = load_module("log_model_renderer", "log-model-renderer.py")

parse_range_spec = renderer.parse_range_spec
strip_ansi = renderer.strip_ansi
format_tool_use = renderer.format_tool_use


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
