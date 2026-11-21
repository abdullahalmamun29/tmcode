"""CLI entrypoint for TermCode (`termcode [path]`)."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from termcode import __version__
from termcode.app import TermCodeApp
from termcode.update import detect_install, run_update


def _is_home(path: Path) -> bool:
    try:
        return path == Path.home().resolve()
    except RuntimeError:  # no resolvable home directory
        return False


def parse_cli_target(arg: str | None, cwd: Path | None = None) -> tuple[Path | None, Path | None]:
    """Resolve optional CLI path into (workspace_root, initial_file).

    The home directory is never opened as a workspace: nobody keeps a project
    directly in ~, and scanning all of it would be slow. Whether you run
    `termcode`, `termcode .` or `termcode ~` there, you get the start page
    asking which folder to open. Any other directory opens directly.
    """
    base = (cwd or Path.cwd()).resolve()
    if arg is None:
        return (None, None) if _is_home(base) else (base, None)

    target = Path(arg).expanduser()
    if not target.is_absolute():
        target = base / target

    if not target.exists():
        raise SystemExit(f"tmcode: path not found: {arg}")

    resolved = target.resolve()
    if resolved.is_dir():
        return (None, None) if _is_home(resolved) else (resolved, None)
    return resolved.parent, resolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tmcode",
        description="TmCode: a lightweight terminal code editor. Edit and run code in any language.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Optional folder or file to open (from home directory, opens the Home folder selector; from any other folder, opens it directly).",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Update TmCode to the latest version and exit.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    args = parser.parse_args(argv)
    if args.update:
        return run_update(detect_install())
    workspace_root, initial_file = parse_cli_target(args.path)
    app = TermCodeApp(workspace_root=workspace_root, initial_file=initial_file)
    app.run()
    if app.update_to:
        return run_update(app.install_kind, app.update_to)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
