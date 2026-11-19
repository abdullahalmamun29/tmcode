"""Unit tests for find/replace and go-to-line helpers."""

import pytest

from termcode.search import (
    SearchError,
    find_matches,
    next_match_index,
    parse_goto,
    replace_all,
    replace_one,
)


def test_plain_find_is_case_insensitive_by_default() -> None:
    text = "Total = total + TOTAL"
    assert find_matches(text, "total") == [(0, 5), (8, 13), (16, 21)]
    assert find_matches(text, "total", case_sensitive=True) == [(8, 13)]


def test_plain_find_treats_regex_chars_literally() -> None:
    assert find_matches("a.b axb", "a.b") == [(0, 3)]


def test_empty_query_finds_nothing() -> None:
    assert find_matches("abc", "") == []


def test_regex_find_and_empty_matches_are_skipped() -> None:
    assert find_matches("w1 w2 b", r"w\d", regex=True) == [(0, 2), (3, 5)]
    assert find_matches("abc", r"x*", regex=True) == []


def test_bad_regex_raises_search_error() -> None:
    with pytest.raises(SearchError):
        find_matches("abc", "(", regex=True)


def test_next_match_index_wraps_both_ways() -> None:
    matches = [(0, 2), (5, 7), (10, 12)]
    assert next_match_index(matches, cursor=3) == 1
    assert next_match_index(matches, cursor=5) == 1
    assert next_match_index(matches, cursor=11) == 0
    assert next_match_index(matches, cursor=10, backwards=True) == 1  # from selection start
    assert next_match_index(matches, cursor=11, backwards=True) == 2
    assert next_match_index(matches, cursor=0, backwards=True) == 2
    assert next_match_index([], cursor=0) is None


def test_replace_all_plain_and_regex_groups() -> None:
    assert replace_all("a.b a.b", "a.b", "X") == ("X X", 2)
    assert replace_all("Ab ab", "ab", "z", case_sensitive=True) == ("Ab z", 1)
    assert replace_all("w1 w2", r"w(\d)", r"v\1", regex=True) == ("v1 v2", 2)
    # Backslashes in plain replacement text stay literal.
    assert replace_all("a", "a", r"\n") == (r"\n", 1)


def test_replace_one_expands_regex_groups() -> None:
    assert replace_one("w12", r"w(\d+)", r"<\1>", regex=True) == "<12>"
    assert replace_one("anything", "x", "lit\\1") == "lit\\1"


def test_parse_goto() -> None:
    assert parse_goto("12") == (11, 0)
    assert parse_goto(" 3:7 ") == (2, 6)
    assert parse_goto("0") == (0, 0)
    assert parse_goto("abc") is None
    assert parse_goto("") is None
