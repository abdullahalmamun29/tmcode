from pathlib import Path

from termcode.editing import (
    comment_tokens,
    indent_unit,
    move_lines,
    outdent_lines,
    smart_home_column,
    toggle_comment,
    word_at,
)


def test_comment_tokens_per_language() -> None:
    assert comment_tokens(Path("a.py")) == ("#", "")
    assert comment_tokens(Path("a.c++")) == ("//", "")
    assert comment_tokens(Path("a.sql")) == ("--", "")
    assert comment_tokens(Path("a.tex")) == ("%", "")
    assert comment_tokens(Path("a.html")) == ("<!--", "-->")
    assert comment_tokens(Path("a.css")) == ("/*", "*/")
    assert comment_tokens(Path("Makefile")) == ("#", "")
    assert comment_tokens(None) == ("#", "")


def test_toggle_comment_aligns_and_round_trips() -> None:
    src = ["def f():", "    x = 1", "", "    return x"]
    commented = toggle_comment(src[1:], ("#", ""))
    assert commented == ["    # x = 1", "", "    # return x"]
    assert toggle_comment(commented, ("#", "")) == src[1:]
    # Mixed: some commented, some not -> comment all.
    assert toggle_comment(["// a", "b"], ("//", "")) == ["// // a", "// b"]
    assert toggle_comment(["  <p>hi</p>"], ("<!--", "-->")) == ["  <!-- <p>hi</p> -->"]
    assert toggle_comment(["  <!-- <p>hi</p> -->"], ("<!--", "-->")) == ["  <p>hi</p>"]
    assert toggle_comment(["", "  "], ("#", "")) == ["", "  "]


def test_outdent_and_indent_unit() -> None:
    assert outdent_lines(["        a", "  b", "c", "\tq"], "    ") == (["    a", "b", "c", "q"], [4, 2, 0, 1])
    assert indent_unit("x\n\ty") == "\t"
    assert indent_unit("x\n    y") == "    "
    assert indent_unit("x") == "    "


def test_move_lines() -> None:
    lines = ["a", "b", "c", "d"]
    assert move_lines(lines, 1, 2, -1) == (["b", "c", "a", "d"], 0)
    assert move_lines(lines, 1, 2, 1) == (["a", "d", "b", "c"], 2)
    assert move_lines(lines, 0, 0, -1) is None
    assert move_lines(lines, 3, 3, 1) is None


def test_smart_home_and_word_at() -> None:
    assert smart_home_column("    x = 1", 7) == 4
    assert smart_home_column("    x = 1", 4) == 0
    assert smart_home_column("    x = 1", 0) == 4
    assert word_at("foo bar_2 ", 5) == (4, 9)
    assert word_at("foo bar_2 ", 9) == (4, 9)
    assert word_at("a  b", 2) is None
