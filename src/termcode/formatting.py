"""Format-on-save: fix indentation before a file is saved (and so before it runs).

If a real formatter is installed (black/ruff, clang-format, prettier, gofmt,
rustfmt) it is used. Otherwise a built-in, dependency-free indenter runs:

* Python  - rebuilds indentation as 4 spaces per block using the tokenizer,
            so meaning never changes (levels map 1:1).
* braces  - C, C++, Java, JS/TS, Go, Rust, CSS: indent by bracket depth,
            ignoring brackets inside strings and comments.
* JSON    - pretty-printed with 4 spaces.
* other   - trailing whitespace removed, final newline added.

Built-in results are only applied if they change nothing but whitespace.
Code that can't be parsed (syntax error, unbalanced braces) is left as is and
the reason is reported, so saving is never blocked.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import io
import json
import re
import shutil
import subprocess
import tokenize

INDENT = "    "


@dataclass(frozen=True)
class FormatResult:
    text: str
    changed: bool
    tool: str | None = None
    error: str | None = None


class FormatError(ValueError):
    """The code couldn't be understood well enough to re-indent safely."""


# ---------------------------------------------------------------- external tools

_EXTERNAL: dict[str, list[tuple[str, Callable[[str], list[str]]]]] = {
    "python": [
        ("ruff", lambda fn: ["format", "--stdin-filename", fn, "-"]),
        ("black", lambda fn: ["-q", "--stdin-filename", fn, "-"]),
    ],
    "c": [("clang-format", lambda fn: [f"--assume-filename={fn}"])],
    "cpp": [("clang-format", lambda fn: [f"--assume-filename={fn}"])],
    "java": [("clang-format", lambda fn: [f"--assume-filename={fn}"])],
    "javascript": [("prettier", lambda fn: ["--stdin-filepath", fn])],
    "typescript": [("prettier", lambda fn: ["--stdin-filepath", fn])],
    "css": [("prettier", lambda fn: ["--stdin-filepath", fn])],
    "go": [("gofmt", lambda fn: [])],
    "rust": [("rustfmt", lambda fn: ["--emit", "stdout", "--edition", "2021"])],
}


def _run_external(
    text: str,
    language: str,
    filename: str,
    which: Callable[[str], str | None],
    run: Callable[..., object],
) -> FormatResult | None:
    for name, args in _EXTERNAL.get(language, []):
        exe = which(name)
        if not exe:
            continue
        try:
            proc = run(
                [exe, *args(filename)],
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            continue  # broken install: fall through to the next option
        rc = getattr(proc, "returncode", 1)
        if rc != 0:
            err = (getattr(proc, "stderr", b"") or b"").decode("utf-8", "replace").strip()
            first = err.splitlines()[0] if err else f"{name} exited {rc}"
            return FormatResult(text, False, name, f"{name}: {first}")
        out = (getattr(proc, "stdout", b"") or b"").decode("utf-8", "replace")
        return FormatResult(out, out != text, name, None)
    return None


# ---------------------------------------------------------------- python

def _expand_leading_tabs(text: str) -> str:
    """Expand tabs in indentation the way Python measures them (next multiple of 8)."""
    out = []
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip(" \t")
        out.append(line[: len(line) - len(stripped)].expandtabs(8) + stripped)
    return "".join(out)


def reindent_python(text: str) -> str:
    """Re-indent Python to 4 spaces per block without changing its meaning."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except TabError:
        # Tabs mixed with spaces: measure tabs as Python does, then re-indent.
        expanded = _expand_leading_tabs(text)
        if expanded == text:
            raise FormatError("inconsistent use of tabs and spaces") from None
        return reindent_python(expanded)
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        lineno = getattr(exc, "lineno", None) or (exc.args[1][0] if len(exc.args) > 1 else "?")
        msg = exc.msg if isinstance(exc, SyntaxError) else exc.args[0]
        raise FormatError(f"line {lineno}: {msg}") from exc

    depth = 0
    starts: dict[int, int] = {}         # row -> block depth, for first rows of statements
    cont_owner: dict[int, int] = {}     # continuation row -> its statement's first row
    frozen: set[int] = set()            # rows inside multi-line strings: leave untouched
    keep_trailing: set[int] = set()     # rows where a multi-line string begins
    pending_comments: list[int] = []
    at_line_start = True
    current: int | None = None

    for tok in tokens:
        ttype = tok.type
        if ttype == tokenize.INDENT:
            depth += 1
            continue
        if ttype == tokenize.DEDENT:
            depth -= 1
            continue
        if ttype in (tokenize.NEWLINE, tokenize.ENDMARKER):
            at_line_start, current = True, None
            if ttype == tokenize.ENDMARKER:
                for row in pending_comments:
                    starts[row] = depth
            continue
        if ttype == tokenize.NL:
            continue
        if ttype == tokenize.COMMENT and at_line_start:
            pending_comments.append(tok.start[0])
            continue
        if at_line_start:
            at_line_start = False
            current = tok.start[0]
            starts[current] = depth
            for row in pending_comments:  # comments line up with the next statement
                starts[row] = depth
            pending_comments = []
        if tok.start[0] != tok.end[0]:
            keep_trailing.add(tok.start[0])
            frozen.update(range(tok.start[0] + 1, tok.end[0] + 1))
        for row in range(tok.start[0], tok.end[0] + 1):
            if current is not None and row != current and row not in starts:
                cont_owner.setdefault(row, current)

    shift: dict[int, int] = {}
    out: list[str] = []
    for row, line in enumerate(text.splitlines(keepends=True), start=1):
        body = line.rstrip("\r\n")
        nl = line[len(body):]
        if row in frozen:
            out.append(line)
            continue
        stripped = body.lstrip(" \t")
        old_indent = body[: len(body) - len(stripped)]
        if row not in keep_trailing:
            stripped = stripped.rstrip()
        if not stripped:
            out.append(nl)
            continue
        old_width = len(old_indent.expandtabs(8))
        if row in starts:
            new_indent = INDENT * starts[row]
            shift[row] = len(new_indent) - old_width
        elif row in cont_owner:
            new_indent = " " * max(0, old_width + shift.get(cont_owner[row], 0))
        else:
            new_indent = old_indent
        out.append(new_indent + stripped + nl)
    return "".join(out)


# ---------------------------------------------------------------- brace languages

BRACE_LANGUAGES = frozenset({"c", "cpp", "java", "javascript", "typescript", "go", "rust", "css"})
_CASE_LANGUAGES = frozenset({"c", "cpp", "java", "javascript", "typescript", "go"})
_CASE_RE = re.compile(r"^(case\b.*|default\s*):")
_RUST_CHAR_RE = re.compile(r"'(\\.[^']*|[^'\\])'")


def _scan(s: str, language: str, in_block: bool, in_raw: bool) -> tuple[int, int, bool, bool]:
    """Scan one line: (bracket delta, leading closers, in_block_comment, in_raw_string)."""
    i, n = 0, len(s)
    delta = leading = 0
    seen_code = False
    while i < n:
        if in_block:
            end = s.find("*/", i)
            if end < 0:
                return delta, leading, True, in_raw
            i, in_block = end + 2, False
            continue
        if in_raw:  # JS template literal / Go raw string spanning lines
            end = s.find("`", i)
            if end < 0:
                return delta, leading, in_block, True
            i, in_raw = end + 1, False
            continue
        c = s[i]
        if c in " \t":
            i += 1
            continue
        if s.startswith("//", i):
            break
        if s.startswith("/*", i):
            in_block, i = True, i + 2
            continue
        if c == "'" and language == "rust" and not _RUST_CHAR_RE.match(s, i):
            i += 1  # lifetime like 'a, not a char literal
            seen_code = True
            continue
        if c in "\"'":
            j = i + 1
            while j < n and s[j] != c:
                j += 2 if s[j] == "\\" else 1
            i, seen_code = j + 1, True
            continue
        if c == "`" and language in ("javascript", "typescript", "go"):
            end = s.find("`", i + 1)
            if end < 0:
                return delta, leading, in_block, True
            i, seen_code = end + 1, True
            continue
        if c in "{([":
            delta += 1
            seen_code = True
        elif c in "})]":
            delta -= 1
            if not seen_code:
                leading += 1
        else:
            seen_code = True
        i += 1
    return delta, leading, in_block, in_raw


def reindent_braces(text: str, language: str) -> str:
    """Re-indent a C-family file by bracket depth (4 spaces per level)."""
    depth = 0
    in_block = in_raw = False
    case_levels: list[int] = []  # depths of switch bodies that have case labels
    out: list[str] = []
    for row, line in enumerate(text.splitlines(keepends=True), start=1):
        body = line.rstrip("\r\n")
        nl = line[len(body):]
        stripped = body.strip()
        if in_raw:  # inside a multi-line string: copy verbatim
            _, _, in_block, in_raw = _scan(body, language, in_block, in_raw)
            out.append(line)
            continue
        if not stripped:
            out.append(nl)
            continue
        if language in ("c", "cpp") and stripped.startswith("#") and not in_block:
            out.append(stripped + nl)  # preprocessor stays at column 0
            continue
        started_in_comment = in_block
        delta, leading, in_block, in_raw = _scan(stripped, language, in_block, in_raw)
        line_depth = max(depth - leading, 0)

        is_case = (
            language in _CASE_LANGUAGES and not started_in_comment and bool(_CASE_RE.match(stripped))
        )
        while case_levels and case_levels[-1] > line_depth:
            case_levels.pop()
        extra = sum(1 for lvl in case_levels if lvl < line_depth or (lvl == line_depth and not is_case))
        if is_case and (not case_levels or case_levels[-1] != line_depth):
            case_levels.append(line_depth)

        indent = INDENT * (line_depth + extra)
        if started_in_comment:
            out.append(indent + (" " + stripped if stripped.startswith("*") else stripped) + nl)
        else:
            out.append(indent + stripped + nl)
        depth += delta
        if depth < 0:
            raise FormatError(f"line {row}: closing bracket without an opening one")
    if depth > 0:
        raise FormatError(f"{depth} bracket(s) never closed")
    if in_block:
        raise FormatError("a /* comment is never closed")
    return "".join(out)


# ---------------------------------------------------------------- entry point

def _strip_trailing(text: str) -> str:
    return "".join(
        line.rstrip("\r\n").rstrip() + line[len(line.rstrip("\r\n")):]
        for line in text.splitlines(keepends=True)
    )


def _final_newline(text: str) -> str:
    return text if not text or text.endswith("\n") else text + "\n"


def _only_whitespace_changed(a: str, b: str) -> bool:
    return re.sub(r"\s+", "", a) == re.sub(r"\s+", "", b)


def format_code(
    text: str,
    language: str | None,
    *,
    filename: str = "untitled",
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., object] = subprocess.run,
) -> FormatResult:
    """Format text for language. Never raises; on failure returns the original text."""
    lang = language or ""
    external = _run_external(text, lang, filename, which, run)
    if external is not None:
        return external

    try:
        if lang == "json":
            if not text.strip():
                return FormatResult(text, False, "built-in")
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise FormatError(f"line {exc.lineno}: {exc.msg}") from exc
            new = json.dumps(data, indent=4, ensure_ascii=False) + "\n"
            return FormatResult(new, new != text, "built-in")
        if lang == "python":
            new = reindent_python(text)
        elif lang in BRACE_LANGUAGES:
            new = reindent_braces(text, lang)
        elif lang == "markdown":
            new = text  # trailing double spaces are line breaks in Markdown
        else:
            new = _strip_trailing(text)
        new = _final_newline(new)
    except FormatError as exc:
        return FormatResult(text, False, "built-in", str(exc))

    if not _only_whitespace_changed(text, new):
        return FormatResult(text, False, "built-in", "formatting would change code; skipped")
    if lang == "python":
        try:
            compile(text, filename, "exec")
        except TabError:
            pass  # tabs mixed with spaces: exactly what re-indenting fixes
        except SyntaxError as exc:
            return FormatResult(text, False, "built-in", f"line {exc.lineno}: {exc.msg}")
        try:
            compile(new, filename, "exec")
        except SyntaxError:
            return FormatResult(text, False, "built-in", "formatting would break the code; skipped")
    return FormatResult(new, new != text, "built-in")
