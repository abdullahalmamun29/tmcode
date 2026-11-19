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
