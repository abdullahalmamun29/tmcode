"""Startup update check: every 6 hours, Skip / Skip until next version."""

import json
from pathlib import Path

from termcode.update import (
    CHECK_INTERVAL,
    InstallKind,
    check_for_update,
    detect_install,
    is_newer,
    parse_latest,
    skip_version,
    update_command,
)


def test_version_comparison() -> None:
    assert is_newer("0.2.4", "0.2.3")
    assert is_newer("v0.10.0", "0.9.9")
    assert is_newer("1.0", "0.9.9")
    assert not is_newer("0.2.3", "0.2.3")
    assert not is_newer("0.2.2", "0.2.3")
    assert not is_newer("garbage", "0.2.3")


def test_parse_latest_from_github_and_pypi() -> None:
    assert parse_latest(InstallKind.BINARY, json.dumps({"tag_name": "v0.3.0"})) == "0.3.0"
    assert parse_latest(InstallKind.PIPX, json.dumps({"info": {"version": "0.3.1"}})) == "0.3.1"
    assert parse_latest(InstallKind.BINARY, "not json") is None


def _check(state: Path, fetch, now: float, current: str = "0.2.3"):  # type: ignore[no-untyped-def]
    return check_for_update(current, InstallKind.BINARY, state, now=now, fetch=fetch, env={})


def test_checks_network_at_most_once_a_day(tmp_path: Path) -> None:
    state = tmp_path / "update.json"
    calls: list[int] = []

    def fetch(_kind: InstallKind) -> str:
        calls.append(1)
        return "0.2.4"

    assert _check(state, fetch, now=1000.0) == "0.2.4"
    assert _check(state, fetch, now=1000.0 + 60) == "0.2.4"  # remembered, no new request
    assert len(calls) == 1
    _check(state, fetch, now=1000.0 + CHECK_INTERVAL + 1)
    assert len(calls) == 2


def test_no_prompt_when_up_to_date_or_offline(tmp_path: Path) -> None:
    assert _check(tmp_path / "a.json", lambda _k: "0.2.3", now=1.0) is None
    assert _check(tmp_path / "b.json", lambda _k: None, now=1.0) is None


def test_skip_until_next_version(tmp_path: Path) -> None:
    state = tmp_path / "update.json"
    assert _check(state, lambda _k: "0.2.4", now=1.0) == "0.2.4"
    skip_version(state, "0.2.4")
    assert _check(state, lambda _k: "0.2.4", now=2.0) is None
    # A newer release asks again.
    assert _check(state, lambda _k: "0.2.5", now=2.0 + CHECK_INTERVAL) == "0.2.5"


def test_disabled_by_env_and_for_source_checkouts(tmp_path: Path) -> None:
    state = tmp_path / "update.json"
    fetch = lambda _k: "9.9.9"  # noqa: E731
    assert check_for_update("0.2.3", InstallKind.BINARY, state, now=1.0, fetch=fetch,
                            env={"TMCODE_NO_UPDATE_CHECK": "1"}) is None
    assert check_for_update("0.2.3", InstallKind.SOURCE, state, now=1.0, fetch=fetch, env={}) is None


def test_corrupt_state_file_is_ignored(tmp_path: Path) -> None:
    state = tmp_path / "update.json"
    state.write_text("{oops", encoding="utf-8")
    assert _check(state, lambda _k: "0.2.4", now=1.0) == "0.2.4"


def test_detect_install_kind() -> None:
    site = "/x/lib/python3.12/site-packages/termcode/update.py"
    assert detect_install(frozen=True, prefix="/x", package_file=site) is InstallKind.BINARY
    assert detect_install(frozen=False, prefix="/h/.local/share/pipx/venvs/tmcode", package_file=site) is InstallKind.PIPX
    assert detect_install(frozen=False, prefix="/h/.local/share/uv/tools/tmcode", package_file=site) is InstallKind.UV
    assert detect_install(frozen=False, prefix="/x/venv", package_file=site) is InstallKind.PIP
    assert detect_install(frozen=False, prefix="/x/.venv", package_file="/code/tmcode/src/termcode/update.py") is InstallKind.SOURCE


def test_update_commands() -> None:
    unix = update_command(InstallKind.BINARY, "linux")
    assert unix is not None and unix[0] == "sh" and "install.sh" in unix[-1]
    win = update_command(InstallKind.BINARY, "win32")
    assert win is not None and win[0] == "powershell" and "install.ps1" in win[-1]
    assert update_command(InstallKind.PIPX, "linux") == ["pipx", "upgrade", "tmcode"]
    assert update_command(InstallKind.UV, "linux") == ["uv", "tool", "upgrade", "tmcode"]
    assert update_command(InstallKind.SOURCE, "linux") is None
