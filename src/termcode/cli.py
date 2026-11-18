"""CLI entrypoint for TermCode (`termcode [path]`)."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from termcode import __version__
from termcode.app import TermCodeApp


def parse_cli_target(arg: str | None, cwd: Path | None = None) -> tuple[Path | None, Path | None]:
    """Resolve optional CLI path into (workspace_root, initial_file).

    When invoked with no path (`termcode`) from the user's home directory,
    returns (None, None) so TermCode opens on the Home tab asking to select a
    folder. From any other directory, opens that directory directly.
    """
    base = (cwd or Path.cwd()).resolve()
    if arg is None:
        try:
            if base == Path.home().resolve():
                return None, None
        except RuntimeError:
            pass
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
        help="Optional folder or file to open (from home directory, opens the Home folder selector; from any other folder, opens it directly).",
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
