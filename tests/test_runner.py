"""Unit and integration tests for TermCode cross-OS Python/C/C++ runner."""

import asyncio
from pathlib import Path
import pytest

from termcode.runner import (
    AsyncRunner,
    Language,
    RunnerError,
    build_run_plan,
    detect_language,
    editor_language_for_path,
    language_label_for_path,
    resolve_python_executable,
)


def test_language_and_editor_detection() -> None:
    assert detect_language(Path("a.py")) == Language.PYTHON
    assert detect_language(Path("a.c")) == Language.C
    assert detect_language(Path("a.cpp")) == Language.CPP
    assert detect_language(Path("a.cc")) == Language.CPP
    assert detect_language(Path("a.cxx")) == Language.CPP
    assert detect_language(Path("a.txt")) is None
    assert editor_language_for_path(Path("a.py")) == "python"
    assert editor_language_for_path(Path("a.h")) == "c"
    assert editor_language_for_path(Path("a.hpp")) == "cpp"
    assert editor_language_for_path(None) is None
    assert language_label_for_path(Path("a.py")) == "Python"
    assert language_label_for_path(Path("a.c")) == "C"
    assert language_label_for_path(Path("a.cpp")) == "C++"
    assert language_label_for_path(Path("notes.txt")) == "Text"


def test_resolve_python_cross_os_paths(tmp_path: Path) -> None:
    win_ws = tmp_path / "win"
    win_py = win_ws / ".venv" / "Scripts" / "python.exe"
    win_py.parent.mkdir(parents=True)
    win_py.write_text("", encoding="utf-8")
    assert resolve_python_executable(win_ws, platform="win32", env={}) == str(win_py)

    posix_ws = tmp_path / "posix"
    posix_py = posix_ws / ".venv" / "bin" / "python"
    posix_py.parent.mkdir(parents=True)
    posix_py.write_text("", encoding="utf-8")
    assert resolve_python_executable(posix_ws, platform="linux", env={}) == str(posix_py)

    # Skips TermCode's own internal venv when checking VIRTUAL_ENV
    own_venv = tmp_path / "own_venv"
    own_py = own_venv / "bin" / "python"
    own_py.parent.mkdir(parents=True)
    own_py.write_text("", encoding="utf-8")
    empty_ws = tmp_path / "empty"
    empty_ws.mkdir()
    resolved = resolve_python_executable(
        empty_ws,
        platform="linux",
        env={"VIRTUAL_ENV": str(own_venv)},
        own_venv=own_venv,
        which=lambda name: "/usr/bin/python3" if name == "python3" else None,
    )
    assert resolved == "/usr/bin/python3"


def test_build_run_plan_win32_exe_and_missing_compiler(tmp_path: Path) -> None:
    c_file = tmp_path / "main.c"
    c_file.write_text("int main(){return 0;}\n", encoding="utf-8")
    plan = build_run_plan(
        c_file,
        tmp_path,
        tmp_path / "tmp",
        platform="win32",
        which=lambda n: f"C:/mingw/{n}.exe",
    )
    assert plan.compile_cmd is not None
    assert plan.run_cmd[0].endswith("a.exe")

    with pytest.raises(RunnerError, match="gcc"):
        build_run_plan(c_file, tmp_path, tmp_path / "tmp", which=lambda _: None)

    txt_file = tmp_path / "notes.txt"
    txt_file.write_text("hello", encoding="utf-8")
    with pytest.raises(RunnerError, match="Unsupported file type"):
        build_run_plan(txt_file, tmp_path, tmp_path / "tmp")


@pytest.mark.asyncio
async def test_run_python_c_cpp_and_no_stray_binaries(tmp_path: Path) -> None:
    py_f = tmp_path / "hi.py"
    py_f.write_text("print('py-ok')\n", encoding="utf-8")
    c_f = tmp_path / "hi.c"
    c_f.write_text('#include <stdio.h>\nint main(){printf("c-ok\\n");return 0;}\n', encoding="utf-8")
    cpp_f = tmp_path / "hi.cpp"
    cpp_f.write_text('#include <iostream>\nint main(){std::cout << "cpp-ok\\n";return 0;}\n', encoding="utf-8")

    runner = AsyncRunner()
    for path, expected in [(py_f, "py-ok"), (c_f, "c-ok"), (cpp_f, "cpp-ok")]:
        chunks: list[str] = []
        res = await runner.run(path, tmp_path, chunks.append)
        assert res.exit_code == 0 and not res.compile_failed and not res.cancelled
        assert expected in "".join(chunks)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["hi.c", "hi.cpp", "hi.py"]


@pytest.mark.asyncio
async def test_interactive_stdin_python_and_c_unbuffered_prompt(tmp_path: Path) -> None:
    # Review Focus #3: C prompt without trailing newline or fflush(stdout)
    c_f = tmp_path / "ask.c"
    c_f.write_text(
        '#include <stdio.h>\n'
        'int main(){int x=0; printf("Enter x: "); scanf("%d", &x); '
        'printf("Got=%d\\n", x*2); return 0;}\n',
        encoding="utf-8",
    )
    runner = AsyncRunner()
    chunks: list[str] = []
    prompt_seen = asyncio.Event()

    def on_out(text: str) -> None:
        chunks.append(text)
        if "Enter x:" in "".join(chunks):
            prompt_seen.set()

    task = asyncio.create_task(runner.run(c_f, tmp_path, on_out))
    await asyncio.wait_for(prompt_seen.wait(), timeout=5.0)
    await runner.send_input("21")
    res = await asyncio.wait_for(task, timeout=5.0)
    assert res.exit_code == 0
    assert "Got=42" in "".join(chunks)

    # Also test interactive Python input()
    py_f = tmp_path / "ask.py"
    py_f.write_text('name = input("Name: ")\nprint(f"Hi {name}")\n', encoding="utf-8")
    py_chunks: list[str] = []
    py_prompt = asyncio.Event()

    def on_py_out(text: str) -> None:
        py_chunks.append(text)
        if "Name:" in "".join(py_chunks):
            py_prompt.set()

    py_task = asyncio.create_task(runner.run(py_f, tmp_path, on_py_out))
    await asyncio.wait_for(py_prompt.wait(), timeout=5.0)
    await runner.send_input("Jobayer")
    py_res = await asyncio.wait_for(py_task, timeout=5.0)
    assert py_res.exit_code == 0
    assert "Hi Jobayer" in "".join(py_chunks)


@pytest.mark.asyncio
async def test_compile_error_and_stop_infinite_loop(tmp_path: Path) -> None:
    bad_c = tmp_path / "bad.c"
    bad_c.write_text("int main(){ oops }\n", encoding="utf-8")
    runner = AsyncRunner()
    chunks: list[str] = []
    res = await runner.run(bad_c, tmp_path, chunks.append)
    assert res.compile_failed and res.exit_code != 0

    loop_py = tmp_path / "loop.py"
    loop_py.write_text(
        "import time\nprint('started')\nwhile True:\n    time.sleep(0.1)\n",
        encoding="utf-8",
    )
    started = asyncio.Event()
    out2: list[str] = []

    def on_out2(text: str) -> None:
        out2.append(text)
        if "started" in "".join(out2):
            started.set()

    task = asyncio.create_task(runner.run(loop_py, tmp_path, on_out2))
    await asyncio.wait_for(started.wait(), timeout=5.0)
    await runner.stop()
    res2 = await asyncio.wait_for(task, timeout=5.0)
    assert res2.cancelled


@pytest.mark.asyncio
async def test_task_cancellation_terminates_child_process(tmp_path: Path) -> None:
    import os

    pid_file = tmp_path / "child.pid"
    script = tmp_path / "sleeper.py"
    script.write_text(
        f"import os, time\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        f"print('ready', flush=True)\n"
        f"while True:\n"
        f"    time.sleep(0.1)\n",
        encoding="utf-8",
    )
    runner = AsyncRunner()
    ready = asyncio.Event()

    def on_out(text: str) -> None:
        if "ready" in text:
            ready.set()

    task = asyncio.create_task(runner.run(script, tmp_path, on_out))
    await asyncio.wait_for(ready.wait(), timeout=5.0)
    child_pid = int(pid_file.read_text(encoding="utf-8").strip())

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Give OS a brief moment to reap the terminated child
    for _ in range(20):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)

    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


@pytest.mark.asyncio
async def test_posix_pty_removes_reader_on_eof_no_spin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import sys

    if sys.platform == "win32":
        pytest.skip("POSIX PTY test")

    # Child closes stdio fds immediately (triggering PTY EIO) while staying alive briefly
    script = tmp_path / "close_stdio.py"
    script.write_text(
        "import os, time\n"
        "print('bye', flush=True)\n"
        "os.close(0); os.close(1); os.close(2)\n"
        "time.sleep(0.2)\n",
        encoding="utf-8",
    )
    read_calls = 0
    real_read = os.read

    def counting_read(fd: int, n: int) -> bytes:
        nonlocal read_calls
        read_calls += 1
        return real_read(fd, n)

    monkeypatch.setattr("termcode.runner.os.read", counting_read)

    runner = AsyncRunner()
    chunks: list[str] = []
    res = await runner.run(script, tmp_path, chunks.append)
    assert res.exit_code == 0
    assert "bye" in "".join(chunks)
    assert read_calls < 50, f"PTY reader busy-spun with {read_calls} os.read calls"
