"""Auto-closing brackets and quotes, VS Code style (pure helpers, no UI).

- Typing ( [ { " ' ` inserts the closing partner and puts the cursor between.
- Typing a closer when the same closer is next just steps over it.
- Backspace inside an empty pair deletes both halves.
- Enter between { } (or ( ) / [ ]) opens an indented block.
"""

from __future__ import annotations

from dataclasses import dataclass

PAIRS = {"(": ")", "[": "]", "{": "}", '"': '"', "'": "'", "`": "`"}
CLOSERS = {")", "]", "}"}
QUOTES = {'"', "'", "`"}

# Auto-close only when the next character is one of these (or end of line),
# so `(|value` doesn't turn into `()value`.
_OK_BEFORE = set(")]};,:.") | {" ", "\t", ""}

_PY_STRING_PREFIXES = {"f", "r", "b", "u", "rb", "br", "fr", "rf"}


@dataclass(frozen=True)
class LineEdit:
    """Replace line[start:end] with text, then put the cursor at column `cursor`."""

    start: int
    end: int
    text: str
    cursor: int


def _word_before(line: str, col: int) -> str:
    i = col
    while i > 0 and (line[i - 1].isalnum() or line[i - 1] == "_"):
        i -= 1
    return line[i:col]


def _inside_string(line_before: str, quote: str) -> bool:
    """True if line_before has an unclosed `quote` string (odd count of unescaped quotes)."""
    count = 0
    escaped = False
    for ch in line_before:
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == quote:
            count += 1
    return count % 2 == 1


def plan_typed(char: str, line: str, col: int, language: str | None) -> LineEdit | None:
    """What typing `char` at `col` should do, or None for normal typing."""
    nxt = line[col : col + 1]

    # Step over a closer that is already there.
    if (char in CLOSERS or char in QUOTES) and nxt == char:
        if char in CLOSERS or _inside_string(line[:col], char):
            return LineEdit(col, col, "", col + 1)

    if char not in PAIRS or nxt not in _OK_BEFORE:
        return None

    if char in QUOTES:
        if char == "'" and language == "rust":
            return None  # lifetimes: &'a, <'a>
        if char == "`" and language not in (None, "javascript", "typescript", "markdown", "bash", "go", "sql"):
            return None
        word = _word_before(line, col)
        if word and not (language == "python" and word.lower() in _PY_STRING_PREFIXES):
            return None  # don't, it's, abc" ...
        if _inside_string(line[:col], char):
            return None  # this quote closes a string being typed

    return LineEdit(col, col, char + PAIRS[char], col + 1)


def plan_backspace(line: str, col: int) -> tuple[int, int] | None:
    """Columns (start, end) to delete when Backspace is pressed inside an empty pair."""
    if col <= 0 or col >= len(line):
        return None
    before, after = line[col - 1], line[col]
    if PAIRS.get(before) == after:
        return col - 1, col + 1
    return None


def plan_enter(line: str, col: int, indent_unit: str = "    ") -> tuple[str, int] | None:
    """Text to insert for Enter between an opener and its closer, and the cursor offset in it."""
    if col <= 0 or col >= len(line):
        return None
    before, after = line[col - 1], line[col]
    if before not in "([{" or PAIRS[before] != after:
        return None
    base = line[: len(line) - len(line.lstrip(" \t"))]
    inner = base + indent_unit
    return "\n" + inner + "\n" + base, 1 + len(inner)
