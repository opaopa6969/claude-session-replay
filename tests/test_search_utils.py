"""Tests for search_utils.py pure utility functions."""
import sys
from pathlib import Path

import pytest

# search_utils.py uses underscores so it can be imported normally
sys.path.insert(0, str(Path(__file__).parent.parent))
import search_utils


search_in_text = search_utils.search_in_text
extract_excerpt = search_utils.extract_excerpt


# ---------------------------------------------------------------------------
# search_in_text
# ---------------------------------------------------------------------------

class TestSearchInText:
    def test_finds_basic_match(self):
        results = search_in_text("Hello World", "World")
        assert len(results) == 1
        start, length = results[0]
        assert start == 6
        assert length == 5

    def test_case_insensitive_by_default(self):
        results = search_in_text("Hello World", "world")
        assert len(results) == 1

    def test_case_sensitive_no_match(self):
        results = search_in_text("Hello World", "world", case_sensitive=True)
        assert results == []

    def test_case_sensitive_match(self):
        results = search_in_text("Hello World", "World", case_sensitive=True)
        assert len(results) == 1

    def test_multiple_matches(self):
        results = search_in_text("abcabc", "abc")
        assert len(results) == 2

    def test_no_match_returns_empty(self):
        results = search_in_text("Hello World", "xyz")
        assert results == []

    def test_empty_text_returns_empty(self):
        results = search_in_text("", "query")
        assert results == []

    def test_empty_query_returns_empty(self):
        results = search_in_text("Hello World", "")
        assert results == []

    def test_regex_mode(self):
        results = search_in_text("abc123def", r"\d+", is_regex=True)
        assert len(results) == 1
        start, length = results[0]
        assert start == 3
        assert length == 3

    def test_regex_case_insensitive(self):
        results = search_in_text("Hello WORLD", r"world", is_regex=True, case_sensitive=False)
        assert len(results) == 1

    def test_regex_case_sensitive_no_match(self):
        results = search_in_text("Hello WORLD", r"world", is_regex=True, case_sensitive=True)
        assert results == []

    def test_invalid_regex_returns_empty(self):
        results = search_in_text("Hello World", "[invalid", is_regex=True)
        assert results == []

    def test_returns_start_and_length_tuples(self):
        results = search_in_text("foobar", "foo")
        assert len(results) == 1
        start, length = results[0]
        assert isinstance(start, int)
        assert isinstance(length, int)
        assert start == 0
        assert length == 3

    def test_match_at_start(self):
        results = search_in_text("hello world", "hello")
        start, length = results[0]
        assert start == 0

    def test_match_at_end(self):
        results = search_in_text("hello world", "world")
        start, length = results[0]
        assert start == 6


# ---------------------------------------------------------------------------
# extract_excerpt
# ---------------------------------------------------------------------------

class TestExtractExcerpt:
    def test_basic_excerpt_contains_match(self):
        text = "Hello World"
        excerpt, offset = extract_excerpt(text, 6, 5, context=100)
        assert "World" in excerpt

    def test_match_near_start_no_leading_ellipsis(self):
        text = "Hello World"
        excerpt, offset = extract_excerpt(text, 0, 5, context=100)
        # Start is at beginning, no leading "..."
        assert not excerpt.startswith("...")

    def test_match_near_end_has_no_trailing_ellipsis_when_context_covers_end(self):
        text = "Hello World"
        excerpt, offset = extract_excerpt(text, 6, 5, context=100)
        assert not excerpt.endswith("...")

    def test_match_in_middle_of_long_text_has_ellipsis(self):
        text = "a" * 200 + "TARGET" + "b" * 200
        match_start = 200
        match_length = 6
        excerpt, offset = extract_excerpt(text, match_start, match_length, context=50)
        assert "TARGET" in excerpt
        assert excerpt.startswith("...")
        assert excerpt.endswith("...")

    def test_offset_points_to_match_in_excerpt(self):
        text = "Hello World"
        excerpt, offset = extract_excerpt(text, 6, 5, context=100)
        # The match "World" should be found at the given offset within excerpt
        assert excerpt[offset:offset + 5] == "World"

    def test_context_zero(self):
        text = "Hello World"
        excerpt, offset = extract_excerpt(text, 6, 5, context=0)
        assert "World" in excerpt

    def test_full_text_returned_when_context_large(self):
        text = "Hello World"
        excerpt, offset = extract_excerpt(text, 6, 5, context=1000)
        assert "Hello" in excerpt
        assert "World" in excerpt

    def test_match_at_start(self):
        text = "MATCH and more text here"
        excerpt, offset = extract_excerpt(text, 0, 5, context=5)
        assert "MATCH" in excerpt

    def test_match_at_end(self):
        text = "some text here MATCH"
        match_start = len(text) - 5
        excerpt, offset = extract_excerpt(text, match_start, 5, context=5)
        assert "MATCH" in excerpt
