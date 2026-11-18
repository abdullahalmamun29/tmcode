"""CLI entrypoint for TermCode (`termcode [path]`)."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from termcode import __version__
from termcode.app import TermCodeApp


def parse_cli_target(arg: str | None, cwd: Path | None = None) -> tuple[Path, Path | None]:
    """Resolve optional CLI path into (workspace_root, initial_file)."""
    base = (cwd or Path.cwd()).resolve()
    if arg is None:
        return base, None

    target = Path(arg).expanduser()
    if not target.is_absolute():
        target = base / target

    if not target.exists():
        raise SystemExit(f"termcode: path not found: {arg}")

    resolved = target.resolve()
    if resolved.is_dir():
        return resolved, None
    return resolved.parent, resolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="termcode",
        description="Lightweight VS Code-style terminal editor and runner for Python, C, and C++.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Optional folder or file to open (defaults to current directory).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    args = parser.parse_args(argv)
    workspace_root, initial_file = parse_cli_target(args.path)
    app = TermCodeApp(workspace_root=workspace_root, initial_file=initial_file)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
