"""Tests for running programs with the real terminal (Shift+F5) and the shell (Ctrl+T)."""

from pathlib import Path
import shutil
import sys

import pytest

from termcode.runner import RunnerError, run_in_foreground, user_shell_command


def test_foreground_python_exit_code_and_output(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "prog.py"
    src.write_text("print('hello from fg')\nraise SystemExit(3)\n", encoding="utf-8")
    result = run_in_foreground(src, tmp_path)
    assert result.exit_code == 3
    assert not result.compile_failed and not result.cancelled
    assert result.duration_seconds >= 0
    assert "hello from fg" in capfd.readouterr().out


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not installed")
def test_foreground_c_compile_failure(tmp_path: Path) -> None:
    src = tmp_path / "bad.c"
    src.write_text("int main( { return 0; }\n", encoding="utf-8")
    result = run_in_foreground(src, tmp_path)
    assert result.compile_failed and result.exit_code != 0


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not installed")
def test_foreground_c_runs_compiled_binary(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "ok.c"
    src.write_text('#include <stdio.h>\nint main(void){puts("c-fg-ok");return 0;}\n', encoding="utf-8")
    result = run_in_foreground(src, tmp_path)
    assert result.exit_code == 0 and not result.compile_failed
    assert "c-fg-ok" in capfd.readouterr().out


def test_foreground_unsupported_file_raises(tmp_path: Path) -> None:
    src = tmp_path / "notes.txt"
    src.write_text("hi", encoding="utf-8")
    with pytest.raises(RunnerError):
        run_in_foreground(src, tmp_path)


def test_user_shell_command_prefers_shell_env() -> None:
    if sys.platform == "win32":
        assert user_shell_command({"COMSPEC": r"C:\cmd.exe"}) == [r"C:\cmd.exe"]
    else:
        assert user_shell_command({"SHELL": "/bin/zsh"}) == ["/bin/zsh"]
        assert user_shell_command({})[0].endswith("sh")
