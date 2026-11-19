"""Pure helpers for find/replace and go-to-line (no UI, easy to test)."""

from __future__ import annotations

import bisect
import re

Match = tuple[int, int]  # (start, end) string offsets into the document text

MAX_MATCHES = 10_000


class SearchError(ValueError):
    """Raised for an invalid regular expression."""


def _compile(query: str, *, case_sensitive: bool, regex: bool) -> re.Pattern[str]:
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        return re.compile(query if regex else re.escape(query), flags)
    except re.error as exc:
        raise SearchError(f"Invalid regex: {exc}") from exc


def find_matches(
    text: str,
    query: str,
    *,
    case_sensitive: bool = False,
    regex: bool = False,
) -> list[Match]:
    """Return non-empty, non-overlapping matches of query in text."""
    if not query:
        return []
    pattern = _compile(query, case_sensitive=case_sensitive, regex=regex)
    matches: list[Match] = []
    for m in pattern.finditer(text):
        if m.end() > m.start():
            matches.append((m.start(), m.end()))
            if len(matches) >= MAX_MATCHES:
                break
    return matches


def next_match_index(matches: list[Match], cursor: int, *, backwards: bool = False) -> int | None:
    """Index of the match at/after cursor (or before it when backwards), wrapping around."""
    if not matches:
        return None
    starts = [s for s, _ in matches]
    if backwards:
        idx = bisect.bisect_left(starts, cursor) - 1
        return idx % len(matches)
    idx = bisect.bisect_left(starts, cursor)
    return idx % len(matches)


def replace_one(
    matched_text: str,
    query: str,
    replacement: str,
    *,
    case_sensitive: bool = False,
    regex: bool = False,
) -> str:
    """Replacement for a single match; expands \\1 groups only in regex mode."""
    if not regex:
        return replacement
    pattern = _compile(query, case_sensitive=case_sensitive, regex=True)
    try:
        return pattern.sub(replacement, matched_text, count=1)
    except re.error as exc:
        raise SearchError(f"Invalid replacement: {exc}") from exc


def replace_all(
    text: str,
    query: str,
    replacement: str,
    *,
    case_sensitive: bool = False,
    regex: bool = False,
) -> tuple[str, int]:
    """Replace every match; returns (new_text, count)."""
    if not query:
        return text, 0
    pattern = _compile(query, case_sensitive=case_sensitive, regex=regex)
    repl = replacement if regex else (lambda _m: replacement)
    try:
        return pattern.subn(repl, text)
    except re.error as exc:
        raise SearchError(f"Invalid replacement: {exc}") from exc


def parse_goto(raw: str) -> tuple[int, int] | None:
    """Parse '42' or '42:7' (1-based) into a 0-based (row, col)."""
    parts = raw.strip().split(":")
    if not parts[0].isdigit() or len(parts) > 2:
        return None
    row = max(int(parts[0]) - 1, 0)
    col = 0
    if len(parts) == 2:
        if not parts[1].isdigit():
            return None
        col = max(int(parts[1]) - 1, 0)
    return row, col
