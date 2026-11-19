"""Cross-OS language detection, compilation, and interactive execution engine."""

from __future__ import annotations

import asyncio
import codecs
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

try:
    import pty
    import termios
except ImportError:  # Windows
    pty = None  # type: ignore[assignment]
    termios = None  # type: ignore[assignment]


class Language(str, Enum):
    PYTHON = "python"
    C = "c"
    CPP = "cpp"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    BASH = "bash"
    GO = "go"
    RUST = "rust"
    JAVA = "java"
    RUBY = "ruby"
    PHP = "php"
    PERL = "perl"
    LUA = "lua"


@dataclass(frozen=True)
class RunPlan:
    language: Language
    source: Path
    compile_cmd: list[str] | None
    run_cmd: list[str]
    cwd: Path
    display_cmd: str


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    duration_seconds: float
    cancelled: bool
    compile_failed: bool


class RunnerError(Exception):
    """Raised when a file cannot be compiled or executed."""


_RUNNABLE_EXTENSIONS: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".c": Language.C,
    ".cpp": Language.CPP,
    ".cc": Language.CPP,
    ".cxx": Language.CPP,
    ".js": Language.JAVASCRIPT,
    ".mjs": Language.JAVASCRIPT,
    ".cjs": Language.JAVASCRIPT,
    ".ts": Language.TYPESCRIPT,
    ".sh": Language.BASH,
    ".bash": Language.BASH,
    ".go": Language.GO,
    ".rs": Language.RUST,
    ".java": Language.JAVA,
    ".rb": Language.RUBY,
    ".php": Language.PHP,
    ".pl": Language.PERL,
    ".lua": Language.LUA,
}

_EDITOR_LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "javascript",
    ".tsx": "javascript",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".json": "json",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".sql": "sql",
    ".md": "markdown",
    ".markdown": "markdown",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".xml": "xml",
}

_LANGUAGE_LABELS: dict[str, str] = {
    ".py": "Python",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
    ".hh": "C++",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".json": "JSON",
    ".java": "Java",
    ".go": "Go",
    ".rs": "Rust",
    ".sh": "Bash",
    ".bash": "Bash",
    ".zsh": "Shell",
    ".sql": "SQL",
    ".md": "Markdown",
    ".toml": "TOML",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".xml": "XML",
    ".rb": "Ruby",
    ".php": "PHP",
    ".pl": "Perl",
    ".lua": "Lua",
}


def detect_language(path: Path) -> Language | None:
    """Return the runnable Language for path, or None if unsupported."""
    return _RUNNABLE_EXTENSIONS.get(path.suffix.lower())


def editor_language_for_path(path: Path | None) -> str | None:
    """Return the syntax-highlighting language identifier for path."""
    if path is None:
        return None
    return _EDITOR_LANGUAGES.get(path.suffix.lower())


def language_label_for_path(path: Path | None) -> str:
    """Return a short human-readable language badge for the status bar."""
    if path is None:
        return "Untitled"
    return _LANGUAGE_LABELS.get(path.suffix.lower(), "Text")


def _venv_python(venv_dir: Path, platform: str) -> Path:
    if platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a) == str(b)


def resolve_python_executable(
    workspace_root: Path,
    *,
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    own_venv: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> str | None:
    """Resolve the best Python interpreter for running user code across OSes."""
    plat = platform or sys.platform
    environment = env if env is not None else os.environ
    internal_venv = own_venv if own_venv is not None else Path(sys.prefix)

    # 1. Workspace-local .venv or venv
    for name in (".venv", "venv"):
        candidate = _venv_python(workspace_root / name, plat)
        if candidate.is_file():
            return str(candidate)

    # 2. Active VIRTUAL_ENV (ignoring TermCode's own internal venv)
    active_venv = environment.get("VIRTUAL_ENV")
    if active_venv:
        venv_path = Path(active_venv)
        if not _same_dir(venv_path, internal_venv):
            candidate = _venv_python(venv_path, plat)
            if candidate.is_file():
                return str(candidate)

    # 3. System Python on PATH
    candidates = ("python", "python3") if plat == "win32" else ("python3", "python")
    for exe in candidates:
        found = which(exe)
        if found:
            return found

    return None


def build_run_plan(
    source: Path,
    workspace_root: Path,
    temp_dir: Path,
    *,
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> RunPlan:
    """Build compile and execute commands for a Python, C, or C++ file."""
    plat = platform or sys.platform
    lang = detect_language(source)
    if lang is None:
        ext = source.suffix or "(no extension)"
        supported = ", ".join(sorted(_RUNNABLE_EXTENSIONS))
        raise RunnerError(
            f"Unsupported file type for running: {ext}. Supported: {supported}"
        )

    cwd = source.parent
    if lang is Language.PYTHON:
        python_exe = resolve_python_executable(
            workspace_root, platform=plat, env=env, which=which
        )
        if not python_exe:
            raise RunnerError("Could not find a Python interpreter (python3/python) on PATH.")
        return RunPlan(
            language=lang,
            source=source,
            compile_cmd=None,
            run_cmd=[python_exe, str(source)],
            cwd=cwd,
            display_cmd=f"{Path(python_exe).name} {source.name}",
        )

    binary_name = "a.exe" if plat == "win32" else "a.out"
    output_bin = temp_dir / binary_name

    if lang is Language.C:
        compiler = which("gcc")
        if not compiler:
            raise RunnerError(
                "Could not find 'gcc' compiler on PATH. Install GCC (or MinGW on Windows)."
            )
        return RunPlan(
            language=lang,
            source=source,
            compile_cmd=[compiler, "-Wall", "-Wextra", str(source), "-o", str(output_bin)],
            run_cmd=[str(output_bin)],
            cwd=cwd,
            display_cmd=f"gcc -Wall -Wextra {source.name} && ./{binary_name}",
        )

    if lang is Language.CPP:
        compiler = which("g++")
        if not compiler:
            raise RunnerError(
                "Could not find 'g++' compiler on PATH. Install G++ (or MinGW on Windows)."
            )
        return RunPlan(
            language=lang,
            source=source,
            compile_cmd=[
                compiler,
                "-std=c++17",
                "-Wall",
                "-Wextra",
                str(source),
                "-o",
                str(output_bin),
            ],
            run_cmd=[str(output_bin)],
            cwd=cwd,
            display_cmd=f"g++ -std=c++17 -Wall -Wextra {source.name} && ./{binary_name}",
        )

    if lang is Language.RUST:
        compiler = which("rustc")
        if not compiler:
            raise RunnerError("Could not find 'rustc' compiler on PATH. Install Rust (rustup).")
        return RunPlan(
            language=lang,
            source=source,
            compile_cmd=[compiler, str(source), "-o", str(output_bin)],
            run_cmd=[str(output_bin)],
            cwd=cwd,
            display_cmd=f"rustc {source.name} && ./{binary_name}",
        )

    if lang is Language.GO:
        go_exe = which("go")
        if not go_exe:
            raise RunnerError("Could not find 'go' on PATH. Install Go to run .go files.")
        return RunPlan(
            language=lang,
            source=source,
            compile_cmd=None,
            run_cmd=[go_exe, "run", str(source)],
            cwd=cwd,
            display_cmd=f"go run {source.name}",
        )

    if lang is Language.TYPESCRIPT:
        for tool in ("tsx", "ts-node", "node"):
            exe = which(tool)
            if exe:
                return RunPlan(
                    language=lang,
                    source=source,
                    compile_cmd=None,
                    run_cmd=[exe, str(source)],
                    cwd=cwd,
                    display_cmd=f"{tool} {source.name}",
                )
        raise RunnerError("Could not find 'tsx', 'ts-node', or 'node' on PATH for TypeScript.")

    if lang is Language.BASH:
        for tool in ("bash", "sh"):
            exe = which(tool)
            if exe:
                return RunPlan(
                    language=lang,
                    source=source,
                    compile_cmd=None,
                    run_cmd=[exe, str(source)],
                    cwd=cwd,
                    display_cmd=f"{tool} {source.name}",
                )
        raise RunnerError("Could not find 'bash' or 'sh' on PATH.")

    simple_interpreters: dict[Language, tuple[str, str]] = {
        Language.JAVASCRIPT: ("node", "Node.js"),
        Language.JAVA: ("java", "Java (JDK 11+)"),
        Language.RUBY: ("ruby", "Ruby"),
        Language.PHP: ("php", "PHP"),
        Language.PERL: ("perl", "Perl"),
        Language.LUA: ("lua", "Lua"),
    }
    exe_name, friendly = simple_interpreters[lang]
    exe = which(exe_name)
    if not exe:
        raise RunnerError(f"Could not find '{exe_name}' on PATH. Install {friendly} to run {source.suffix} files.")
    return RunPlan(
        language=lang,
        source=source,
        compile_cmd=None,
        run_cmd=[exe, str(source)],
        cwd=cwd,
        display_cmd=f"{exe_name} {source.name}",
    )


class AsyncRunner:
    """Manages asynchronous compilation and interactive execution of a single program."""

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._master_fd: int | None = None
        self._cancelled: bool = False
        self._running: bool = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def run(
        self,
        source: Path,
        workspace_root: Path,
        on_output: Callable[[str], None],
    ) -> RunResult:
        """Compile (if needed) and execute source, streaming output to on_output."""
        if self._running:
            raise RunnerError("A program is already running. Stop it first (F7 / /stop).")

        self._running = True
        self._cancelled = False
        start = time.monotonic()

        try:
            with tempfile.TemporaryDirectory(prefix="termcode_") as tmp:
                plan = build_run_plan(source, workspace_root, Path(tmp))

                # Step 1: Compile (C / C++)
                if plan.compile_cmd is not None:
                    compile_rc = await self._run_compile(plan.compile_cmd, plan.cwd, on_output)
                    if self._cancelled:
                        return RunResult(
                            exit_code=compile_rc,
                            duration_seconds=time.monotonic() - start,
                            cancelled=True,
                            compile_failed=False,
                        )
                    if compile_rc != 0:
                        return RunResult(
                            exit_code=compile_rc,
                            duration_seconds=time.monotonic() - start,
                            cancelled=False,
                            compile_failed=True,
                        )

                # Step 2: Execute program (POSIX PTY or Windows pipes)
                run_env = os.environ.copy()
                run_env["PYTHONUNBUFFERED"] = "1"
                if sys.platform != "win32" and pty is not None and termios is not None:
                    rc = await self._run_posix_pty(plan.run_cmd, plan.cwd, run_env, on_output)
                else:
                    rc = await self._run_pipes(plan.run_cmd, plan.cwd, run_env, on_output)

                return RunResult(
                    exit_code=rc,
                    duration_seconds=time.monotonic() - start,
                    cancelled=self._cancelled,
                    compile_failed=False,
                )
        except asyncio.CancelledError:
            self._cancelled = True
            await self.stop()
            raise
        finally:
            self._proc = None
            self._master_fd = None
            self._running = False

    async def _run_compile(
        self,
        cmd: list[str],
        cwd: Path,
        on_output: Callable[[str], None],
    ) -> int:
        kwargs: dict[str, object] = {
            "cwd": str(cwd),
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.STDOUT,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True

        proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)  # type: ignore[arg-type]
        self._proc = proc
        stdout, _ = await proc.communicate()
        if stdout:
            text = stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
            on_output(text)
        self._proc = None
        return proc.returncode if proc.returncode is not None else -1

    async def _run_posix_pty(
        self,
        cmd: list[str],
        cwd: Path,
        env: dict[str, str],
        on_output: Callable[[str], None],
    ) -> int:
        assert pty is not None and termios is not None
        master_fd, slave_fd = pty.openpty()
        loop = asyncio.get_running_loop()
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        eof_event = asyncio.Event()

        try:
            # Disable local PTY echo so TermCode echoes user-submitted stdin once cleanly.
            attrs = termios.tcgetattr(slave_fd)
            attrs[3] &= ~termios.ECHO
            termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)
        except OSError:
            pass

        os.set_blocking(master_fd, False)
        self._master_fd = master_fd

        try:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=str(cwd),
                    env=env,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    close_fds=True,
                    start_new_session=True,
                )
            finally:
                os.close(slave_fd)

            self._proc = proc

            def _read_available() -> None:
                while True:
                    try:
                        chunk = os.read(master_fd, 4096)
                    except BlockingIOError:
                        return
                    except OSError:
                        eof_event.set()
                        try:
                            loop.remove_reader(master_fd)
                        except Exception:
                            pass
                        return
                    if not chunk:
                        eof_event.set()
                        try:
                            loop.remove_reader(master_fd)
                        except Exception:
                            pass
                        return
                    decoded = decoder.decode(chunk).replace("\r\n", "\n")
                    if decoded:
                        on_output(decoded)

            loop.add_reader(master_fd, _read_available)
            rc = await proc.wait()

            # Drain any trailing output written right before process exit.
            try:
                await asyncio.wait_for(eof_event.wait(), timeout=0.25)
            except asyncio.TimeoutError:
                _read_available()

            tail = decoder.decode(b"", final=True).replace("\r\n", "\n")
            if tail:
                on_output(tail)
            return rc
        finally:
            try:
                loop.remove_reader(master_fd)
            except Exception:
                pass
            self._master_fd = None
            try:
                os.close(master_fd)
            except OSError:
                pass

    async def _run_pipes(
        self,
        cmd: list[str],
        cwd: Path,
        env: dict[str, str],
        on_output: Callable[[str], None],
    ) -> int:
        kwargs: dict[str, object] = {
            "cwd": str(cwd),
            "env": env,
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.STDOUT,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True

        proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)  # type: ignore[arg-type]
        self._proc = proc
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        assert proc.stdout is not None
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                break
            decoded = decoder.decode(chunk).replace("\r\n", "\n")
            if decoded:
                on_output(decoded)

        tail = decoder.decode(b"", final=True).replace("\r\n", "\n")
        if tail:
            on_output(tail)
        return await proc.wait()

    async def send_input(self, line: str) -> None:
        """Send one line of input (with newline) to the running program's stdin."""
        if not self._running:
            return
        payload = (line + "\n").encode("utf-8")
        if self._master_fd is not None:
            try:
                os.write(self._master_fd, payload)
            except OSError:
                pass
            return
        if self._proc is not None and self._proc.stdin is not None:
            try:
                self._proc.stdin.write(payload)
                await self._proc.stdin.drain()
            except OSError:
                pass

    async def stop(self) -> None:
        """Terminate the currently running process cleanly across OSes."""
        if not self._running:
            return
        self._cancelled = True
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return

        if sys.platform == "win32":
            try:
                proc.terminate()
            except ProcessLookupError:
                return
            try:
                await asyncio.wait_for(proc.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                try:
                    subprocess.run(
                        ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                except OSError:
                    proc.kill()
        else:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            except OSError:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    return
            try:
                await asyncio.wait_for(proc.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass


def user_shell_command(env: Mapping[str, str] | None = None) -> list[str]:
    """The user's interactive shell: $SHELL on Unix, %COMSPEC% on Windows."""
    environ = os.environ if env is None else env
    if sys.platform == "win32":
        return [environ.get("COMSPEC") or "cmd.exe"]
    shell = environ.get("SHELL")
    if shell:
        return [shell]
    return [shutil.which("bash") or "/bin/sh"]


def wait_ignoring_ctrl_c(proc: subprocess.Popen[bytes]) -> tuple[int, bool]:
    """Wait for a child that shares our terminal; returns (exit_code, saw_ctrl_c).

    Ctrl+C reaches both TermCode and the child. TermCode must survive it, so
    the KeyboardInterrupt is swallowed and the child decides whether to exit.
    """
    interrupted = False
    while True:
        try:
            return proc.wait(), interrupted
        except KeyboardInterrupt:
            interrupted = True


def run_in_foreground(source: Path, workspace_root: Path) -> RunResult:
    """Compile (if needed) and run source with the real terminal (blocking).

    Used by Shift+F5 while the TUI is suspended, so the program gets a true
    TTY: colors, \\r progress bars, curses and single-key input all work.
    Ctrl+C stops the program and is reported as cancelled.
    """
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="termcode_") as tmp:
        plan = build_run_plan(source, workspace_root, Path(tmp))

        def _wait(proc: subprocess.Popen[bytes]) -> tuple[int, bool]:
            rc, interrupted = wait_ignoring_ctrl_c(proc)
            return rc, interrupted

        if plan.compile_cmd is not None:
            compile_rc, cancelled = _wait(subprocess.Popen(plan.compile_cmd, cwd=plan.cwd))
            if cancelled or compile_rc != 0:
                return RunResult(
                    exit_code=compile_rc,
                    duration_seconds=time.monotonic() - start,
                    cancelled=cancelled,
                    compile_failed=not cancelled,
                )

        rc, cancelled = _wait(subprocess.Popen(plan.run_cmd, cwd=plan.cwd))
        return RunResult(
            exit_code=rc,
            duration_seconds=time.monotonic() - start,
            cancelled=cancelled,
            compile_failed=False,
        )
