"""Startup update check, like Codex / Claude Code.

When TmCode starts it asks (at most once a day, in the background) what the
latest version is. If it's newer, the app offers: Update now / Skip /
Skip until next version. "Skip until next version" is remembered in
update.json next to recent.json, so the prompt only returns for a newer release.

Binary installs (install.sh / install.ps1) check GitHub Releases and update by
re-running the installer. pipx / uv / pip installs check PyPI and use their own
upgrade command. Running from a source checkout never checks.
Set TMCODE_NO_UPDATE_CHECK=1 to turn the check off.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from urllib.request import Request, urlopen

REPO = "abdullahalmamun29/tmcode"
GITHUB_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
PYPI_JSON = "https://pypi.org/pypi/tmcode/json"
INSTALL_SH = f"https://raw.githubusercontent.com/{REPO}/main/install.sh"
INSTALL_PS1 = f"https://raw.githubusercontent.com/{REPO}/main/install.ps1"

CHECK_INTERVAL = 24 * 60 * 60  # seconds between network checks
FETCH_TIMEOUT = 3.0


class InstallKind(Enum):
    BINARY = "binary"   # standalone build from install.sh / install.ps1
    PIPX = "pipx"
    UV = "uv"
    PIP = "pip"
    SOURCE = "source"   # editable / source checkout: never auto-update


@dataclass
class UpdateState:
    last_check: float = 0.0
    latest: str | None = None
    skipped: str | None = None


# ---------- versions ----------

def parse_version(text: str) -> tuple[int, ...] | None:
    parts: list[int] = []
    for piece in text.strip().lstrip("vV").split("."):
        digits = ""
        for ch in piece:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) or None


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


# ---------- how TmCode was installed ----------

def detect_install(
    *,
    frozen: bool | None = None,
    prefix: str | None = None,
    package_file: str | None = None,
) -> InstallKind:
    if frozen if frozen is not None else getattr(sys, "frozen", False):
        return InstallKind.BINARY
    pkg_parts = Path(package_file or __file__).parts
    if "site-packages" not in pkg_parts and "dist-packages" not in pkg_parts:
        return InstallKind.SOURCE
    where = Path(prefix or sys.prefix).as_posix().lower()
    if "/pipx/venvs/" in where:
        return InstallKind.PIPX
    if "/uv/tools/" in where:
        return InstallKind.UV
    return InstallKind.PIP


# ---------- state file ----------

def default_state_path() -> Path:
    from termcode.workspace import default_recent_store

    return default_recent_store().parent / "update.json"


def load_state(path: Path) -> UpdateState:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return UpdateState(
            last_check=float(data.get("last_check", 0.0)),
            latest=data.get("latest"),
            skipped=data.get("skipped"),
        )
    except (OSError, ValueError, TypeError, AttributeError):
        return UpdateState()


def save_state(path: Path, state: UpdateState) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(state)), encoding="utf-8")
    except OSError:
        pass


def skip_version(path: Path, version: str) -> None:
    state = load_state(path)
    state.skipped = version
    save_state(path, state)


# ---------- network ----------

def parse_latest(kind: InstallKind, body: str) -> str | None:
    try:
        data = json.loads(body)
        raw = data["tag_name"] if kind is InstallKind.BINARY else data["info"]["version"]
    except (ValueError, KeyError, TypeError):
        return None
    version = str(raw).lstrip("vV")
    return version if parse_version(version) else None


def fetch_latest(kind: InstallKind) -> str | None:
    url = GITHUB_LATEST if kind is InstallKind.BINARY else PYPI_JSON
    request = Request(url, headers={"User-Agent": "tmcode-update-check", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=FETCH_TIMEOUT) as response:  # noqa: S310 (fixed https URL)
            return parse_latest(kind, response.read().decode("utf-8", "replace"))
    except Exception:
        return None


def check_for_update(
    current: str,
    kind: InstallKind,
    state_path: Path,
    *,
    now: float | None = None,
    fetch: Callable[[InstallKind], str | None] = fetch_latest,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Return the newer version to offer, or None. Hits the network at most once a day."""
    environment = env if env is not None else os.environ
    if kind is InstallKind.SOURCE or environment.get("TMCODE_NO_UPDATE_CHECK"):
        return None
    moment = time.time() if now is None else now
    state = load_state(state_path)
    elapsed = moment - state.last_check
    if state.last_check <= 0 or elapsed >= CHECK_INTERVAL or elapsed < 0:  # never / stale / clock moved back
        latest = fetch(kind)
        state.last_check = moment
        if latest:
            state.latest = latest
        save_state(state_path, state)
    latest = state.latest
    if latest and is_newer(latest, current) and latest != state.skipped:
        return latest
    return None


# ---------- updating ----------

def update_command(kind: InstallKind, platform: str | None = None) -> list[str] | None:
    plat = platform or sys.platform
    if kind is InstallKind.BINARY:
        if plat == "win32":
            # Wait a moment so the running tmcode.exe has exited before it's replaced.
            return [
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                f"Start-Sleep -Seconds 2; irm {INSTALL_PS1} | iex",
            ]
        return [
            "sh", "-c",
            f"if command -v curl >/dev/null 2>&1; then curl -fsSL {INSTALL_SH} | sh; "
            f"else wget -qO- {INSTALL_SH} | sh; fi",
        ]
    if kind is InstallKind.PIPX:
        return ["pipx", "upgrade", "tmcode"]
    if kind is InstallKind.UV:
        return ["uv", "tool", "upgrade", "tmcode"]
    if kind is InstallKind.PIP:
        return [sys.executable, "-m", "pip", "install", "--upgrade", "tmcode"]
    return None


def manual_update_hint(kind: InstallKind) -> str:
    if kind is InstallKind.SOURCE:
        return "You're running TmCode from source: update with git pull."
    cmd = update_command(kind)
    return "Update with: " + (" ".join(cmd) if cmd else "the install command")


def run_update(kind: InstallKind, version: str | None = None) -> int:
    """Run the update in the normal terminal (after the app has closed)."""
    cmd = update_command(kind)
    if cmd is None:
        print(manual_update_hint(kind))
        return 1
    label = f"TmCode {version}" if version else "the latest TmCode"
    print(f"Updating to {label} ...\n", flush=True)
    if kind is InstallKind.BINARY and sys.platform == "win32":
        # tmcode.exe can't replace itself while running: update in a new window.
        subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        print("The update continues in a new window. Start tmcode again when it finishes.")
        return 0
    try:
        code = subprocess.call(cmd)
    except FileNotFoundError:
        print(f"Couldn't run {cmd[0]}. {manual_update_hint(kind)}")
        return 1
    if code == 0:
        print("\nUpdated. Run tmcode again to use the new version.")
    else:
        print(f"\nUpdate failed (exit {code}). {manual_update_hint(kind)}")
    return code
