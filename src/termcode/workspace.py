"""Filesystem operations and safety guards for TermCode workspaces."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

try:
    from platformdirs import user_config_dir
except ImportError:  # pragma: no cover
    user_config_dir = None  # type: ignore[assignment]

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


def default_recent_store() -> Path:
    """Return cross-OS path for TermCode's recent folders JSON file."""
    if user_config_dir is not None:
        return Path(user_config_dir("termcode")) / "recent.json"
    return Path.home() / ".config" / "termcode" / "recent.json"


def load_recent_folders(
    *, store_path: Path | None = None, limit: int = 6
) -> list[Path]:
    """Load existing recently opened workspace folders, newest first."""
    store = store_path or default_recent_store()
    if not store.is_file():
        return []
    try:
        raw = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []

    folders: list[Path] = []
    seen: set[Path] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        p = Path(item)
        if p.is_dir():
            resolved = p.resolve()
            if resolved not in seen:
                seen.add(resolved)
                folders.append(resolved)
                if len(folders) >= limit:
                    break
    return folders


def record_recent_folder(
    folder: Path, *, store_path: Path | None = None, limit: int = 6
) -> None:
    """Record folder at the front of the recent folders list."""
    store = store_path or default_recent_store()
    resolved = folder.resolve()
    existing = [p for p in load_recent_folders(store_path=store, limit=limit) if p != resolved]
    updated = [resolved, *existing][:limit]
    try:
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(
            json.dumps([str(p) for p in updated], indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


_BRANCH_CACHE: dict[Path, tuple[float, str | None]] = {}


def _find_git_head(root: Path) -> Path | None:
    for folder in (root, *root.parents):
        dot_git = folder / ".git"
        if dot_git.is_dir():
            return dot_git / "HEAD"
        if dot_git.is_file():  # worktree or submodule: "gitdir: <path>"
            try:
                text = dot_git.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            if text.startswith("gitdir:"):
                gitdir = Path(text.removeprefix("gitdir:").strip())
                if not gitdir.is_absolute():
                    gitdir = folder / gitdir
                return gitdir / "HEAD"
            return None
    return None


def current_git_branch(root: Path) -> str | None:
    """Return the checked-out branch (or short SHA) for root, without running git.

    Called on every keystroke via the status bar, so it only re-reads HEAD
    when the file's mtime changes.
    """
    head = _find_git_head(root)
    if head is None:
        return None
    try:
        mtime = head.stat().st_mtime
    except OSError:
        return None
    cached = _BRANCH_CACHE.get(head)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        ref = head.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    branch = ref.removeprefix("ref: refs/heads/") if ref.startswith("ref:") else ref[:7]
    _BRANCH_CACHE[head] = (mtime, branch)
    return branch
