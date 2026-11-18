"""Filesystem operations and safety guards for TermCode workspaces."""

from __future__ import annotations

import os
from pathlib import Path
import shutil

MAX_FILE_BYTES: int = 2 * 1024 * 1024  # 2 MB
IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        ".pytest_cache",
        ".mypy_cache",
    }
)


class WorkspaceError(Exception):
    """Base error for safe workspace file and folder operations."""


class BinaryFileError(WorkspaceError):
    """Raised when attempting to load a binary or non-UTF-8 file as text."""


class FileTooLargeError(WorkspaceError):
    """Raised when attempting to load a file larger than the editor limit."""


def _validate_relative_name(raw_name: str, *, allow_nested: bool) -> Path:
    cleaned = raw_name.strip()
    if not cleaned:
        raise WorkspaceError("Name cannot be empty.")

    rel = Path(cleaned)
    if rel.is_absolute():
        raise WorkspaceError("Please enter a relative name, not an absolute path.")

    parts = rel.parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise WorkspaceError(f"Invalid path: {raw_name!r}")

    if not allow_nested and (len(parts) != 1 or "/" in cleaned or "\\" in cleaned):
        raise WorkspaceError("Rename target must be a single file or folder name.")

    return rel


def read_text_file(path: Path, max_bytes: int = MAX_FILE_BYTES) -> str:
    """Read a UTF-8 text file, rejecting binary files and files over max_bytes."""
    try:
        stat = path.stat()
    except OSError as exc:
        raise WorkspaceError(f"Cannot read {path.name}: {exc}") from exc

    if not path.is_file():
        raise WorkspaceError(f"Not a file: {path}")

    if stat.st_size > max_bytes:
        limit_mb = max_bytes / (1024 * 1024)
        raise FileTooLargeError(
            f"File is too large to open ({stat.st_size:,} bytes; limit {limit_mb:.1f} MB)."
        )

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise WorkspaceError(f"Cannot read {path.name}: {exc}") from exc

    if b"\x00" in raw[:8192]:
        raise BinaryFileError(f"Cannot open binary file: {path.name}")

    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise BinaryFileError(f"Cannot open non-UTF-8 file: {path.name}") from exc


def write_text_file(path: Path, content: str) -> None:
    """Write UTF-8 text content to path, creating parent directories if needed."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise WorkspaceError(f"Cannot save {path.name}: {exc}") from exc


def create_file(base_dir: Path, relative_name: str) -> Path:
    """Create a new empty file (including nested parent dirs) under base_dir."""
    rel = _validate_relative_name(relative_name, allow_nested=True)
    target = base_dir / rel
    if target.exists():
        raise WorkspaceError(f"Already exists: {target.name}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
    except OSError as exc:
        raise WorkspaceError(f"Cannot create file {rel}: {exc}") from exc
    return target


def create_folder(base_dir: Path, relative_name: str) -> Path:
    """Create a new folder (including nested parent dirs) under base_dir."""
    rel = _validate_relative_name(relative_name, allow_nested=True)
    target = base_dir / rel
    if target.exists():
        raise WorkspaceError(f"Already exists: {target.name}")
    try:
        target.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise WorkspaceError(f"Cannot create folder {rel}: {exc}") from exc
    return target


def rename_path(target: Path, new_name: str) -> Path:
    """Rename a file or folder within its parent directory."""
    if not target.exists():
        raise WorkspaceError(f"Path does not exist: {target}")
    rel = _validate_relative_name(new_name, allow_nested=False)
    dest = target.parent / rel
    if dest == target:
        return target
    if dest.exists():
        raise WorkspaceError(f"Already exists: {dest.name}")
    try:
        target.rename(dest)
    except OSError as exc:
        raise WorkspaceError(f"Cannot rename {target.name}: {exc}") from exc
    return dest


def delete_path(target: Path) -> None:
    """Delete a file or recursively delete a directory."""
    if not target.exists():
        raise WorkspaceError(f"Path does not exist: {target}")
    try:
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
    except OSError as exc:
        raise WorkspaceError(f"Cannot delete {target.name}: {exc}") from exc


def list_workspace_files(root: Path, limit: int = 2000) -> list[Path]:
    """Return sorted file paths under root, skipping ignored directories."""
    results: list[Path] = []
    if not root.is_dir():
        return results

    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)
        cur_path = Path(current)
        for filename in sorted(files):
            results.append(cur_path / filename)
            if len(results) >= limit:
                return results
    return results
