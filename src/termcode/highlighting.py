"""Optional tree-sitter syntax highlighting registration (C and C++)."""

from __future__ import annotations

from textual.widgets import TextArea

_C_LANG = None
_C_QUERY: str | None = None
_CPP_LANG = None
_CPP_QUERY: str | None = None
_LOADED = False


def _load_extra_grammars() -> None:
    global _C_LANG, _C_QUERY, _CPP_LANG, _CPP_QUERY, _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        from tree_sitter import Language
    except ImportError:
        return

    try:
        import tree_sitter_c

        _C_LANG = Language(tree_sitter_c.language())
        _C_QUERY = str(tree_sitter_c.HIGHLIGHTS_QUERY)
    except Exception:
        _C_LANG = None
        _C_QUERY = None

    try:
        import tree_sitter_cpp

        _CPP_LANG = Language(tree_sitter_cpp.language())
        cpp_q = str(tree_sitter_cpp.HIGHLIGHTS_QUERY)
        # tree-sitter-cpp builds on C's node types; combining C + C++ queries
        # ensures full coverage for strings, comments, #include, types, and functions.
        _CPP_QUERY = f"{_C_QUERY}\n{cpp_q}" if _C_QUERY else cpp_q
    except Exception:
        _CPP_LANG = None
        _CPP_QUERY = None


def register_extra_languages(text_area: TextArea) -> None:
    """Register C and C++ tree-sitter languages on a TextArea when installed."""
    _load_extra_grammars()
    available = text_area.available_languages

    if _C_LANG is not None and _C_QUERY and "c" not in available:
        try:
            text_area.register_language("c", _C_LANG, _C_QUERY)
        except Exception:
            pass

    if _CPP_LANG is not None and _CPP_QUERY and "cpp" not in available:
        try:
            text_area.register_language("cpp", _CPP_LANG, _CPP_QUERY)
        except Exception:
            pass


# ---------- Pygments fallback: colors for ~500 more languages ----------
#
# Tree-sitter grammars cover the common languages. For everything else
# (Kotlin, Swift, Haskell, Elixir, Zig, Dockerfile, ...) the editor asks
# Pygments, which ships with TmCode already, and maps its tokens to the
# same theme colors.

_MAX_FALLBACK_CHARS = 400_000  # very large files stay plain for speed


def pygments_lexer_for(name: str, text: str = ""):  # type: ignore[no-untyped-def]
    """A Pygments lexer for this filename (or a #! line), or None for plain text."""
    try:
        from pygments.lexers import get_lexer_for_filename, guess_lexer
        from pygments.util import ClassNotFound
    except ImportError:
        return None
    head = text[:2048]
    options = {"stripnl": False, "stripall": False, "ensurenl": False}
    lexer = None
    try:
        lexer = get_lexer_for_filename(name, head, **options)
    except ClassNotFound:
        if head.startswith("#!"):
            try:
                lexer = guess_lexer(head, **options)
            except ClassNotFound:
                lexer = None
    except Exception:
        lexer = None
    if lexer is None or lexer.name in ("Text only", "Text output"):
        return None
    return lexer


def _token_map():  # type: ignore[no-untyped-def]
    from pygments.token import (
        Comment, Generic, Keyword, Literal, Name, Number, Operator, String,
    )

    return [
        (Comment, "comment"),
        (String.Doc, "string.documentation"),
        (String, "string"),
        (Number, "number"),
        (Keyword.Constant, "constant.builtin"),
        (Keyword.Type, "type.builtin"),
        (Keyword, "keyword"),
        (Operator.Word, "keyword.operator"),
        (Operator, "operator"),
        (Name.Builtin.Pseudo, "constant.builtin"),
        (Name.Builtin, "function.call"),
        (Name.Function, "function"),
        (Name.Decorator, "function"),
        (Name.Class, "class"),
        (Name.Namespace, "type"),
        (Name.Exception, "type"),
        (Name.Constant, "constant.builtin"),
        (Name.Tag, "tag"),
        (Name.Attribute, "css.property"),
        (Name.Label, "json.label"),
        (Generic.Heading, "heading"),
        (Generic.Subheading, "heading"),
        (Generic.Strong, "bold"),
        (Generic.Emph, "italic"),
        (Generic.Inserted, "string"),
        (Generic.Deleted, "number"),
        (Literal, "string"),
    ]


_NAME_CACHE: dict[object, str | None] = {}


def _highlight_name(token_type) -> str | None:  # type: ignore[no-untyped-def]
    if token_type in _NAME_CACHE:
        return _NAME_CACHE[token_type]
    name = None
    for parent, highlight in _token_map():
        if token_type in parent:
            name = highlight
            break
    _NAME_CACHE[token_type] = name
    return name


def pygments_highlights(text: str, lexer) -> dict[int, list[tuple[int, int, str]]]:  # type: ignore[no-untyped-def]
    """Per-line (start_byte, end_byte, highlight_name) ranges, like TextArea's tree-sitter map."""
    result: dict[int, list[tuple[int, int, str]]] = {}
    if len(text) > _MAX_FALLBACK_CHARS:
        return result
    row = 0
    col = 0  # byte column within the current line
    for _index, token_type, value in lexer.get_tokens_unprocessed(text):
        name = _highlight_name(token_type)
        parts = value.split("\n")
        for i, part in enumerate(parts):
            if i:
                row += 1
                col = 0
            size = len(part.encode("utf-8"))
            if name and size:
                result.setdefault(row, []).append((col, col + size, name))
            col += size
    return result
