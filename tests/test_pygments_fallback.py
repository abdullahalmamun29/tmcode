"""Pygments fallback highlighting for languages without a tree-sitter grammar."""

from termcode.highlighting import pygments_highlights, pygments_lexer_for


def test_lexers_found_for_many_languages() -> None:
    for name in ["a.kt", "a.swift", "a.hs", "a.ex", "a.zig", "a.dart", "a.cs", "a.r",
                 "a.jl", "a.scala", "a.rb", "a.php", "a.lua", "Dockerfile", "a.ps1", "a.nim"]:
        assert pygments_lexer_for(name) is not None, name
    assert pygments_lexer_for("notes.txt") is None


def test_shebang_without_extension() -> None:
    lexer = pygments_lexer_for("myscript", "#!/usr/bin/env ruby\nputs 1\n")
    assert lexer is not None and "Ruby" in lexer.name


def test_highlights_use_byte_columns_and_rows() -> None:
    lexer = pygments_lexer_for("a.kt")
    text = 'fun main() {\n    val s = "héllo" // hi\n}\n'
    hl = pygments_highlights(text, lexer)
    names_row0 = {name for _s, _e, name in hl[0]}
    assert "keyword" in names_row0
    row1 = hl[1]
    line1 = text.split("\n")[1].encode("utf-8")
    strings = [line1[s:e].decode() for s, e, n in row1 if n == "string"]
    comments = [line1[s:e].decode() for s, e, n in row1 if n == "comment"]
    assert "".join(strings) == '"héllo"'
    assert comments == ["// hi"]
