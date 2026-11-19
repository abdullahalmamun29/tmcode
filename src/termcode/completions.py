"""Lightweight offline code completion and auto-indentation helpers."""

from __future__ import annotations

import re

_PREFIX_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_:\.\-]*$")
_WORD_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{1,}\b")

LANGUAGE_COMPLETIONS: dict[str, tuple[str, ...]] = {
    "python": (
        "print", "input", "range", "len", "enumerate", "isinstance", "type",
        "def", "class", "return", "import", "from", "as", "if", "elif", "else",
        "for", "while", "break", "continue", "pass", "try", "except", "finally",
        "raise", "with", "yield", "lambda", "async", "await", "True", "False",
        "None", "and", "or", "not", "in", "is", "list", "dict", "set", "tuple",
        "str", "int", "float", "bool", "open", "sorted", "reversed", "zip",
        "map", "filter", "sum", "min", "max", "abs", "any", "all", "super",
        "__init__", "__name__", "__main__", "self", "dataclass", "Path",
    ),
    "c": (
        "printf", "scanf", "fprintf", "fscanf", "sprintf", "snprintf", "puts",
        "fgets", "getchar", "putchar", "include", "define", "ifdef", "ifndef",
        "endif", "int", "long", "short", "char", "float", "double", "void",
        "unsigned", "signed", "const", "static", "extern", "struct", "union",
        "enum", "typedef", "sizeof", "return", "if", "else", "for", "while",
        "do", "switch", "case", "default", "break", "continue", "NULL",
        "malloc", "calloc", "realloc", "free", "memcpy", "memset", "strlen",
        "strcpy", "strcmp", "strcat", "stdio.h", "stdlib.h", "string.h",
        "math.h", "stdbool.h", "stdint.h", "main", "size_t", "FILE",
    ),
    "cpp": (
        "printf", "scanf", "std::cout", "std::cin", "std::cerr", "std::endl",
        "std::string", "std::vector", "std::map", "std::unordered_map",
        "std::set", "std::unordered_set", "std::pair", "std::sort",
        "std::max", "std::min", "std::getline", "std::unique_ptr",
        "std::shared_ptr", "std::make_unique", "std::make_shared",
        "include", "iostream", "vector", "string", "algorithm", "cmath",
        "namespace", "using", "class", "struct", "public", "private",
        "protected", "virtual", "override", "template", "typename", "auto",
        "constexpr", "const", "static", "nullptr", "true", "false", "new",
        "delete", "return", "if", "else", "for", "while", "switch", "case",
        "break", "continue", "try", "catch", "throw", "int", "long", "double",
        "float", "char", "bool", "void", "size_t", "main", "push_back",
        "emplace_back", "begin", "end", "size", "empty",
    ),
    "javascript": (
        "console.log", "console.error", "console.warn", "const", "let", "var",
        "function", "return", "async", "await", "import", "export", "default",
        "from", "class", "extends", "constructor", "this", "super", "new",
        "if", "else", "for", "while", "switch", "case", "break", "continue",
        "try", "catch", "finally", "throw", "true", "false", "null",
        "undefined", "typeof", "instanceof", "Promise", "Array", "Object",
        "JSON.stringify", "JSON.parse", "fetch", "setTimeout", "setInterval",
        "document.querySelector", "addEventListener", "forEach", "map",
        "filter", "reduce", "length", "push", "includes",
    ),
    "html": (
        "<!DOCTYPE html>", "<html>", "<head>", "<body>", "<div>", "<span>",
        "<section>", "<main>", "<header>", "<footer>", "<nav>", "<h1>", "<h2>",
        "<p>", "<a href=\"\">", "<img src=\"\" alt=\"\">", "<ul>", "<ol>",
        "<li>", "<button>", "<input>", "<form>", "<label>", "<script>",
        "<style>", "<link>", "<meta>", "<table>", "<thead>", "<tbody>",
        "<tr>", "<td>", "<th>", "class=", "id=", "href=", "src=",
    ),
    "css": (
        "display", "flex", "grid", "position", "relative", "absolute", "fixed",
        "margin", "padding", "width", "height", "max-width", "min-height",
        "background", "background-color", "color", "font-size", "font-weight",
        "font-family", "border", "border-radius", "box-shadow", "gap",
        "align-items", "justify-content", "flex-direction", "transition",
        "transform", "opacity", "cursor", "pointer", "overflow", "z-index",
    ),
    "java": (
        "System.out.println", "System.out.print", "public", "private",
        "protected", "class", "interface", "extends", "implements", "static",
        "final", "void", "int", "long", "double", "boolean", "String",
        "return", "new", "this", "super", "import", "package", "if", "else",
        "for", "while", "switch", "case", "try", "catch", "finally", "throw",
        "throws", "ArrayList", "HashMap", "List", "Map", "Scanner", "main",
    ),
    "go": (
        "fmt.Println", "fmt.Printf", "fmt.Sprintf", "package", "import",
        "func", "return", "var", "const", "type", "struct", "interface",
        "map", "chan", "go", "defer", "select", "if", "else", "for", "range",
        "switch", "case", "default", "break", "continue", "make", "len",
        "cap", "append", "copy", "delete", "error", "nil", "true", "false",
        "string", "int", "int64", "float64", "bool", "byte", "main",
    ),
    "rust": (
        "println!", "print!", "eprintln!", "format!", "vec!", "fn", "let",
        "mut", "const", "pub", "struct", "enum", "impl", "trait", "for",
        "while", "loop", "if", "else", "match", "return", "use", "mod",
        "crate", "self", "Self", "Some", "None", "Ok", "Err", "Option",
        "Result", "String", "Vec", "HashMap", "usize", "i32", "i64", "f64",
        "bool", "str", "async", "await", "unwrap", "expect", "clone", "main",
    ),
    "bash": (
        "echo", "printf", "read", "export", "local", "function", "if", "then",
        "elif", "else", "fi", "for", "while", "do", "done", "case", "esac",
        "in", "return", "exit", "set -euo pipefail", "mkdir", "grep", "awk",
        "sed", "find", "chmod", "source",
    ),
    "sql": (
        "SELECT", "FROM", "WHERE", "INSERT INTO", "VALUES", "UPDATE", "SET",
        "DELETE FROM", "CREATE TABLE", "ALTER TABLE", "DROP TABLE", "JOIN",
        "LEFT JOIN", "INNER JOIN", "ON", "GROUP BY", "ORDER BY", "HAVING",
        "LIMIT", "OFFSET", "DISTINCT", "COUNT", "SUM", "AVG", "MIN", "MAX",
        "AS", "AND", "OR", "NOT", "NULL", "PRIMARY KEY", "FOREIGN KEY",
    ),
}

_BRACE_LANGUAGES = frozenset({"c", "cpp", "javascript", "java", "go", "rust", "css", "json"})
_TAG_OPEN_RE = re.compile(r"<([A-Za-z][A-Za-z0-9\-]*)(?:\s[^>]*)?>$")
_VOID_HTML_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
)


def extract_prefix(line: str, col: int) -> str:
    """Extract the word/symbol prefix immediately before cursor column col."""
    segment = line[: max(0, col)]
    match = _PREFIX_RE.search(segment)
    return match.group(0) if match else ""


def extract_buffer_words(buffer_text: str) -> list[str]:
    """Return unique identifier-like words from the current file buffer."""
    seen: set[str] = set()
    words: list[str] = []
    for match in _WORD_RE.finditer(buffer_text):
        word = match.group(0)
        if word not in seen:
            seen.add(word)
            words.append(word)
    return words


def get_completions(
    prefix: str,
    language: str | None,
    buffer_text: str,
    *,
    limit: int = 8,
) -> list[str]:
    """Return ranked completion candidates matching prefix for language + buffer words."""
    if len(prefix) < 2:
        return []

    needle = prefix.lower()
    lang_words = LANGUAGE_COMPLETIONS.get(language or "", ())
    buf_words = extract_buffer_words(buffer_text)

    seen: set[str] = set()
    prefix_hits: list[str] = []
    substring_hits: list[str] = []

    for word in (*lang_words, *buf_words):
        if word in seen or word == prefix:
            continue
        seen.add(word)
        low = word.lower()
        if low.startswith(needle):
            prefix_hits.append(word)
        elif needle in low:
            substring_hits.append(word)

    return (prefix_hits + substring_hits)[:limit]


def compute_enter_indent(line_before_cursor: str, language: str | None) -> str:
    """Compute indentation string for a newly inserted line after pressing Enter."""
    leading_len = len(line_before_cursor) - len(line_before_cursor.lstrip(" \t"))
    base_indent = line_before_cursor[:leading_len]
    stripped = line_before_cursor.rstrip()

    if language == "python":
        if stripped.endswith(":"):
            return base_indent + "    "
        return base_indent

    if language in _BRACE_LANGUAGES:
        if stripped.endswith("{") or stripped.endswith("[") or stripped.endswith("("):
            return base_indent + "    "
        return base_indent

    if language in ("html", "xml"):
        m = _TAG_OPEN_RE.search(stripped)
        if m and m.group(1).lower() not in _VOID_HTML_TAGS:
            return base_indent + "    "
        return base_indent

    return base_indent


def compute_closing_brace_dedent(line_before_cursor: str) -> int:
    """Return number of spaces to remove before inserting '}' on a whitespace-only line."""
    if not line_before_cursor or line_before_cursor.strip():
        return 0
    if line_before_cursor.endswith("    "):
        return 4
    if line_before_cursor.endswith("\t"):
        return 1
    spaces = len(line_before_cursor) - len(line_before_cursor.rstrip(" "))
    return min(spaces, 4)
