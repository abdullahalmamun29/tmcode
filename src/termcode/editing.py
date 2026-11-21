"""Line editing helpers behind the VS Code-style shortcuts.

Pure functions over lists of lines, so they are easy to test. Rows are
0-based and ranges are inclusive (first_row..last_row).
"""

from __future__ import annotations

from pathlib import Path

# ---------- comment tokens ----------

_HASH = {
    ".py", ".pyw", ".sh", ".bash", ".zsh", ".fish", ".rb", ".pl", ".pm", ".r", ".jl",
    ".yaml", ".yml", ".toml", ".ex", ".exs", ".nim", ".cr", ".tcl", ".raku", ".awk",
    ".ps1", ".coffee", ".mojo", ".cmake", ".conf", ".ini", ".cfg", ".mk", ".dockerfile",
    ".gitignore", ".env", ".properties",
}
_SLASHES = {
    ".c", ".h", ".cpp", ".cc", ".cxx", ".c++", ".cp", ".hpp", ".hh", ".hxx", ".h++",
    ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs", ".kt", ".kts",
    ".swift", ".dart", ".cs", ".fsx", ".fs", ".scala", ".sc", ".zig", ".d", ".groovy",
    ".php", ".odin", ".vala", ".scss", ".less", ".sass", ".json5", ".jsonc", ".proto",
}
_DASHES = {".sql", ".lua", ".hs", ".adb", ".ads", ".elm"}
_PERCENT = {".tex", ".ltx", ".latex", ".sty", ".cls", ".bib", ".erl", ".m"}
_SEMICOLON = {".lisp", ".el", ".clj", ".cljs", ".rkt", ".scm", ".asm", ".s", ".ini"}
_BLOCK = {
    ".html": ("<!--", "-->"), ".htm": ("<!--", "-->"), ".xml": ("<!--", "-->"),
    ".xhtml": ("<!--", "-->"), ".svg": ("<!--", "-->"), ".vue": ("<!--", "-->"),
    ".svelte": ("<!--", "-->"), ".astro": ("<!--", "-->"), ".md": ("<!--", "-->"),
    ".markdown": ("<!--", "-->"), ".css": ("/*", "*/"), ".ml": ("(*", "*)"),
    ".pas": ("{", "}"), ".pp": ("{", "}"),
}
_SPECIAL_NAMES = {"makefile": "#", "dockerfile": "#", "cmakelists.txt": "#", "gemfile": "#", "rakefile": "#"}


def comment_tokens(path: Path | None) -> tuple[str, str]:
    """(start, end) comment markers for a file; end is "" for line comments."""
    if path is None:
        return ("#", "")
    name = path.name.lower()
    if name in _SPECIAL_NAMES:
        return (_SPECIAL_NAMES[name], "")
    ext = path.suffix.lower()
    if ext in _HASH:
        return ("#", "")
    if ext in _BLOCK:
        return _BLOCK[ext]
    if ext in _SLASHES:
        return ("//", "")
    if ext in _DASHES:
        return ("--", "")
    if ext in _PERCENT:
        return ("%", "")
    if ext in _SEMICOLON:
        return (";", "")
    if ext in (".f90", ".f95", ".f03", ".f08", ".f"):
        return ("!", "")
    if ext in (".cob", ".cbl"):
        return ("*>", "")
    if ext in (".bat", ".cmd"):
        return ("REM", "")
    if ext in (".vim",):
        return ('"', "")
    return ("#", "")


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def toggle_comment(lines: list[str], tokens: tuple[str, str]) -> list[str]:
    """Comment the lines out, or uncomment them if every non-blank one is commented.

    Like VS Code, the marker goes at the smallest indent so the block stays aligned,
    and blank lines are left alone.
    """
    start, end = tokens
    filled = [line for line in lines if line.strip()]
    if not filled:
        return list(lines)

    def is_commented(line: str) -> bool:
        body = line.strip()
        return body.startswith(start) and (not end or body.endswith(end))

    if all(is_commented(line) for line in filled):
        out = []
        for line in lines:
            if not line.strip():
                out.append(line)
                continue
            indent = _indent_of(line)
            body = line[len(indent):][len(start):]
            if body.startswith(" "):
                body = body[1:]
            if end:
                body = body[: len(body) - len(end)]
                if body.endswith(" "):
                    body = body[:-1]
            out.append(indent + body)
        return out

    col = min(len(_indent_of(line)) for line in filled)
    out = []
    for line in lines:
        if not line.strip():
            out.append(line)
            continue
        body = line[col:]
        out.append(line[:col] + start + " " + body + (" " + end if end else ""))
    return out


# ---------- indenting ----------

def indent_lines(lines: list[str], unit: str) -> list[str]:
    return [unit + line if line.strip() else line for line in lines]


def outdent_lines(lines: list[str], unit: str) -> tuple[list[str], list[int]]:
    """Remove one indent level; also returns how many characters each line lost."""
    out: list[str] = []
    removed: list[int] = []
    width = len(unit.expandtabs(4)) if unit != "\t" else 4
    for line in lines:
        if line.startswith("\t"):
            out.append(line[1:])
            removed.append(1)
            continue
        spaces = len(line) - len(line.lstrip(" "))
        take = min(spaces, width)
        out.append(line[take:])
        removed.append(take)
    return out, removed


def indent_unit(text: str) -> str:
    """Tab if the file indents with tabs, else four spaces."""
    for line in text.splitlines()[:500]:
        if line.startswith("\t"):
            return "\t"
        if line.startswith("    "):
            return "    "
    return "    "


# ---------- moving and copying lines ----------

def move_lines(lines: list[str], first: int, last: int, direction: int) -> tuple[list[str], int] | None:
    """Move lines first..last up (-1) or down (+1). Returns (new lines, new first) or None at an edge."""
    if direction < 0 and first == 0:
        return None
    if direction > 0 and last >= len(lines) - 1:
        return None
    block = lines[first : last + 1]
    rest = lines[:first] + lines[last + 1 :]
    new_first = first + direction
    return rest[:new_first] + block + rest[new_first:], new_first


def smart_home_column(line: str, col: int) -> int:
    """Home goes to the first non-blank character, or to column 0 if already there."""
    first = len(_indent_of(line))
    return 0 if col == first else first


def word_at(line: str, col: int) -> tuple[int, int] | None:
    """Span of the word (letters, digits, _) at or just before col."""
    def is_word(ch: str) -> bool:
        return ch.isalnum() or ch == "_"

    if col < len(line) and is_word(line[col]):
        start = col
    elif col > 0 and is_word(line[col - 1]):
        start = col - 1
    else:
        return None
    while start > 0 and is_word(line[start - 1]):
        start -= 1
    end = start
    while end < len(line) and is_word(line[end]):
        end += 1
    return start, end
