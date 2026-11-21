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
    # Less common C++ spellings, e.g. hello.c++
    for name in ("hello.c++", "hello.C++", "a.cp"):
        assert detect_language(Path(name)) == Language.CPP
        assert editor_language_for_path(Path(name)) == "cpp"
        assert language_label_for_path(Path(name)) == "C++"
    for name in ("a.hxx", "a.h++"):
        assert editor_language_for_path(Path(name)) == "cpp"
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
    with pytest.raises(RunnerError, match="doesn.t know how to run"):
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


@pytest.mark.asyncio
async def test_multi_language_plans_and_node_bash_execution(tmp_path: Path) -> None:
    # Verify plan dispatch for Go, Rust, Java, TypeScript
    rs_file = tmp_path / "main.rs"
    rs_file.write_text('fn main() { println!("rs"); }\n', encoding="utf-8")
    rs_plan = build_run_plan(rs_file, tmp_path, tmp_path / "t", which=lambda n: f"/usr/bin/{n}")
    assert rs_plan.compile_cmd is not None and "rustc" in rs_plan.compile_cmd[0]

    go_file = tmp_path / "main.go"
    go_file.write_text("package main\n", encoding="utf-8")
    go_plan = build_run_plan(go_file, tmp_path, tmp_path / "t", which=lambda n: f"/usr/bin/{n}")
    assert go_plan.run_cmd[:2] == ["/usr/bin/go", "run"]

    # Real Node.js and Bash execution
    js_file = tmp_path / "hello.js"
    js_file.write_text("console.log('node-ok');\n", encoding="utf-8")
    sh_file = tmp_path / "hello.sh"
    sh_file.write_text("echo 'bash-ok'\n", encoding="utf-8")

    runner = AsyncRunner()
    for path, expected in [(js_file, "node-ok"), (sh_file, "bash-ok")]:
        chunks: list[str] = []
        res = await runner.run(path, tmp_path, chunks.append)
        assert res.exit_code == 0 and not res.compile_failed
        assert expected in "".join(chunks)


def _which_all(name: str) -> str:
    return f"/usr/bin/{name}"


def test_many_more_languages_run(tmp_path: Path) -> None:
    cases = {
        "a.kt": ("kotlinc", "java"),
        "a.swift": (None, "swift"),
        "a.hs": (None, "runghc"),
        "a.exs": (None, "elixir"),
        "a.jl": (None, "julia"),
        "a.zig": (None, "zig"),
        "a.f90": ("gfortran", None),
        "a.dart": (None, "dart"),
        "a.r": (None, "Rscript"),
    }
    for name, (compiler, runner) in cases.items():
        src = tmp_path / name
        src.write_text("", encoding="utf-8")
        plan = build_run_plan(src, tmp_path, tmp_path / "t", which=_which_all, user_commands={})
        if compiler:
            assert plan.compile_cmd and plan.compile_cmd[0] == f"/usr/bin/{compiler}", name
        if runner:
            assert plan.run_cmd[0] == f"/usr/bin/{runner}", name
        assert str(src) in " ".join((plan.compile_cmd or []) + plan.run_cmd), name


def test_second_recipe_used_when_first_tool_missing(tmp_path: Path) -> None:
    src = tmp_path / "a.scala"
    src.write_text("", encoding="utf-8")
    plan = build_run_plan(src, tmp_path, tmp_path / "t",
                          which=lambda n: "/usr/bin/scala" if n == "scala" else None, user_commands={})
    assert plan.run_cmd == ["/usr/bin/scala", str(src)]


def test_missing_tool_message_names_language(tmp_path: Path) -> None:
    src = tmp_path / "a.kt"
    src.write_text("", encoding="utf-8")
    with pytest.raises(RunnerError, match="Install Kotlin"):
        build_run_plan(src, tmp_path, tmp_path / "t", which=lambda _n: None, user_commands={})


def test_shebang_runs_files_without_known_extension(tmp_path: Path) -> None:
    src = tmp_path / "tool"
    src.write_text("#!/usr/bin/env -S ruby -w\nputs 1\n", encoding="utf-8")
    plan = build_run_plan(src, tmp_path, tmp_path / "t", which=_which_all, user_commands={})
    assert plan.run_cmd == ["/usr/bin/ruby", "-w", str(src)]


def test_user_run_commands_override_and_extend(tmp_path: Path) -> None:
    cfg = tmp_path / "run-commands.json"
    cfg.write_text(
        '{".foo": "foorun --fast {file}",'
        ' "py": "mypython {file}",'
        ' ".bar": {"compile": "barc {file} -o {out}", "run": "{out}"}}',
        encoding="utf-8",
    )
    foo = tmp_path / "x.foo"; foo.write_text("", encoding="utf-8")
    py = tmp_path / "x.py"; py.write_text("", encoding="utf-8")
    bar = tmp_path / "x.bar"; bar.write_text("", encoding="utf-8")
    t = tmp_path / "t"

    assert build_run_plan(foo, tmp_path, t, which=_which_all, user_commands=cfg).run_cmd == [
        "/usr/bin/foorun", "--fast", str(foo)]
    assert build_run_plan(py, tmp_path, t, which=_which_all, user_commands=cfg).run_cmd == [
        "/usr/bin/mypython", str(py)]
    plan = build_run_plan(bar, tmp_path, t, which=_which_all, user_commands=cfg, platform="linux")
    assert plan.compile_cmd == ["/usr/bin/barc", str(bar), "-o", str(t / "app")]
    assert plan.run_cmd == [str(t / "app")]


def test_html_opens_in_browser(tmp_path: Path) -> None:
    page = tmp_path / "index.html"
    page.write_text("<h1>hi</h1>", encoding="utf-8")
    linux = build_run_plan(page, tmp_path, tmp_path / "t", platform="linux", which=_which_all, user_commands={})
    assert linux.run_cmd == ["/usr/bin/xdg-open", str(page)]
    assert "browser" in linux.display_cmd
    mac = build_run_plan(page, tmp_path, tmp_path / "t", platform="darwin", which=_which_all, user_commands={})
    assert mac.run_cmd == ["/usr/bin/open", str(page)]
    win = build_run_plan(page, tmp_path, tmp_path / "t", platform="win32", which=_which_all, user_commands={})
    assert win.run_cmd[:4] == ["cmd", "/c", "start", '""'] and win.run_cmd[-1] == str(page)


def test_css_opens_the_page_next_to_it(tmp_path: Path) -> None:
    css = tmp_path / "style.css"
    css.write_text("h1{}", encoding="utf-8")
    with pytest.raises(RunnerError, match="No HTML page"):
        build_run_plan(css, tmp_path, tmp_path / "t", platform="linux", which=_which_all, user_commands={})
    (tmp_path / "about.html").write_text("", encoding="utf-8")
    (tmp_path / "index.html").write_text("", encoding="utf-8")
    plan = build_run_plan(css, tmp_path, tmp_path / "t", platform="linux", which=_which_all, user_commands={})
    assert plan.run_cmd[-1] == str(tmp_path / "index.html")  # index.html wins


def test_jsx_and_tsx_run_with_tsx_or_fallbacks(tmp_path: Path) -> None:
    for name in ("App.jsx", "App.tsx"):
        src = tmp_path / name
        src.write_text("", encoding="utf-8")
        plan = build_run_plan(src, tmp_path, tmp_path / "t", which=_which_all, user_commands={})
        assert plan.run_cmd == ["/usr/bin/tsx", str(src)]
        bun = build_run_plan(src, tmp_path, tmp_path / "t",
                             which=lambda n: "/usr/bin/bun" if n == "bun" else None, user_commands={})
        assert bun.run_cmd == ["/usr/bin/bun", str(src)]


_DOC = "\\documentclass{article}\n\\begin{document}\nHi\n\\end{document}\n"


def test_latex_builds_pdf_then_opens_it(tmp_path: Path) -> None:
    tex = tmp_path / "paper.tex"
    tex.write_text(_DOC, encoding="utf-8")
    plan = build_run_plan(tex, tmp_path, tmp_path / "t", platform="linux", which=_which_all, user_commands={})
    assert plan.compile_cmd[:2] == ["/usr/bin/latexmk", "-pdf"]
    assert "-interaction=nonstopmode" in plan.compile_cmd and plan.compile_cmd[-1] == "paper.tex"
    assert plan.cwd == tmp_path
    assert plan.run_cmd == ["/usr/bin/xdg-open", str(tmp_path / "paper.pdf")]
    assert "paper.pdf" in plan.display_cmd
    assert language_label_for_path(tex) == "LaTeX"


def test_latex_without_latexmk_uses_the_engine_or_tectonic(tmp_path: Path) -> None:
    tex = tmp_path / "a.tex"
    tex.write_text(_DOC, encoding="utf-8")
    only = lambda *names: (lambda n: f"/usr/bin/{n}" if n in names else None)  # noqa: E731
    plan = build_run_plan(tex, tmp_path, tmp_path / "t", which=only("pdflatex", "xdg-open"), user_commands={})
    assert plan.compile_cmd[0] == "/usr/bin/pdflatex"
    plan = build_run_plan(tex, tmp_path, tmp_path / "t", which=only("tectonic", "xdg-open"), user_commands={})
    assert plan.compile_cmd == ["/usr/bin/tectonic", "a.tex"]
    with pytest.raises(RunnerError, match="TeX Live, MacTeX or MiKTeX"):
        build_run_plan(tex, tmp_path, tmp_path / "t", which=only("xdg-open"), user_commands={})


def test_latex_engine_choice() -> None:
    from termcode.runner import latex_engine_for

    assert latex_engine_for(_DOC) == "pdflatex"
    assert latex_engine_for("\\usepackage{fontspec}\n\\setmainfont{Kalpurush}") == "xelatex"
    assert latex_engine_for("\\usepackage{polyglossia}") == "xelatex"
    assert latex_engine_for("% !TEX program = lualatex\n" + _DOC) == "lualatex"
    assert latex_engine_for("% !TeX program = XeLaTeX\n" + _DOC) == "xelatex"


def test_latex_chapter_file_builds_the_main_document(tmp_path: Path) -> None:
    (tmp_path / "notes.tex").write_text(_DOC, encoding="utf-8")
    (tmp_path / "main.tex").write_text(_DOC, encoding="utf-8")
    chapters = tmp_path / "chapters"
    chapters.mkdir()
    intro = chapters / "intro.tex"
    intro.write_text("\\section{Intro}\n", encoding="utf-8")
    plan = build_run_plan(intro, tmp_path, tmp_path / "t", platform="linux", which=_which_all, user_commands={})
    assert plan.compile_cmd[-1] == "main.tex" and plan.cwd == tmp_path  # main.tex wins
    # An explicit magic comment beats guessing.
    intro.write_text("% !TEX root = ../notes.tex\n\\section{Intro}\n", encoding="utf-8")
    plan = build_run_plan(intro, tmp_path, tmp_path / "t", platform="linux", which=_which_all, user_commands={})
    assert plan.compile_cmd[-1] == "notes.tex"
    lonely = tmp_path / "t2"
    lonely.mkdir()
    part = lonely / "part.tex"
    part.write_text("\\section{x}", encoding="utf-8")
    (tmp_path / "main.tex").unlink()
    (tmp_path / "notes.tex").unlink()
    with pytest.raises(RunnerError, match="no main .tex"):
        build_run_plan(part, tmp_path, tmp_path / "t", platform="linux", which=_which_all, user_commands={})
