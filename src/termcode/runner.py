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
    OTHER = "other"  # any other language: recipe table, #! line, or user command


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
    ".c++": Language.CPP,
    ".cp": Language.CPP,
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
    ".c++": "cpp",
    ".cp": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".h++": "cpp",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "javascript",
    ".tsx": "javascript",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".svelte": "html",
    ".astro": "html",
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
    ".c++": "C++",
    ".cp": "C++",
    ".tex": "LaTeX",
    ".ltx": "LaTeX",
    ".latex": "LaTeX",
    ".bib": "BibTeX",
    ".sty": "LaTeX",
    ".cls": "LaTeX",
    ".hpp": "C++",
    ".hh": "C++",
    ".hxx": "C++",
    ".h++": "C++",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".svelte": "Svelte",
    ".astro": "Astro",
    ".ejs": "EJS",
    ".njk": "Nunjucks",
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


# ---------- any other language ----------
#
# Each entry: label, then recipes tried in order. A recipe is
# (compile command or None, run command). Placeholders: {src} source file,
# {out} compiled program, {tmp} scratch folder, {dir} source folder,
# {stem} file name without extension.
Recipe = tuple[list[str] | None, list[str]]

_EXTRA_RUNNERS: dict[str, tuple[str, list[Recipe]]] = {
    ".kt": ("Kotlin", [(["kotlinc", "{src}", "-include-runtime", "-d", "{tmp}/app.jar"], ["java", "-jar", "{tmp}/app.jar"])]),
    ".kts": ("Kotlin", [(None, ["kotlin", "{src}"]), (None, ["kotlinc", "-script", "{src}"])]),
    ".swift": ("Swift", [(None, ["swift", "{src}"])]),
    ".dart": ("Dart", [(None, ["dart", "run", "{src}"])]),
    ".cs": ("C#", [(None, ["dotnet", "run", "{src}"]), (None, ["dotnet-script", "{src}"])]),
    ".fsx": ("F#", [(None, ["dotnet", "fsi", "{src}"])]),
    ".hs": ("Haskell", [(None, ["runghc", "{src}"]), (None, ["runhaskell", "{src}"])]),
    ".ex": ("Elixir", [(None, ["elixir", "{src}"])]),
    ".exs": ("Elixir", [(None, ["elixir", "{src}"])]),
    ".erl": ("Erlang", [(None, ["escript", "{src}"])]),
    ".r": ("R", [(None, ["Rscript", "{src}"])]),
    ".jl": ("Julia", [(None, ["julia", "{src}"])]),
    ".scala": ("Scala", [(None, ["scala-cli", "run", "{src}"]), (None, ["scala", "{src}"])]),
    ".sc": ("Scala", [(None, ["scala-cli", "run", "{src}"]), (None, ["scala", "{src}"])]),
    ".zig": ("Zig", [(None, ["zig", "run", "{src}"])]),
    ".nim": ("Nim", [(None, ["nim", "r", "--hints:off", "{src}"])]),
    ".cr": ("Crystal", [(None, ["crystal", "run", "{src}"])]),
    ".d": ("D", [(None, ["rdmd", "{src}"]), (None, ["dmd", "-run", "{src}"])]),
    ".f90": ("Fortran", [(["gfortran", "{src}", "-o", "{out}"], ["{out}"])]),
    ".f95": ("Fortran", [(["gfortran", "{src}", "-o", "{out}"], ["{out}"])]),
    ".f03": ("Fortran", [(["gfortran", "{src}", "-o", "{out}"], ["{out}"])]),
    ".f": ("Fortran", [(["gfortran", "{src}", "-o", "{out}"], ["{out}"])]),
    ".pas": ("Pascal", [(["fpc", "-FE{tmp}", "-o{out}", "{src}"], ["{out}"])]),
    ".pp": ("Pascal", [(["fpc", "-FE{tmp}", "-o{out}", "{src}"], ["{out}"])]),
    ".adb": ("Ada", [(["gnatmake", "-q", "-D", "{tmp}", "-o", "{out}", "{src}"], ["{out}"])]),
    ".cob": ("COBOL", [(["cobc", "-x", "-o", "{out}", "{src}"], ["{out}"])]),
    ".cbl": ("COBOL", [(["cobc", "-x", "-o", "{out}", "{src}"], ["{out}"])]),
    ".ml": ("OCaml", [(None, ["ocaml", "{src}"])]),
    ".clj": ("Clojure", [(None, ["clojure", "-M", "{src}"]), (None, ["clj", "-M", "{src}"])]),
    ".groovy": ("Groovy", [(None, ["groovy", "{src}"])]),
    ".ps1": ("PowerShell", [
        (None, ["pwsh", "-NoProfile", "-File", "{src}"]),
        (None, ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "{src}"]),
    ]),
    ".bat": ("Batch", [(None, ["cmd", "/c", "{src}"])]),
    ".cmd": ("Batch", [(None, ["cmd", "/c", "{src}"])]),
    ".zsh": ("Zsh", [(None, ["zsh", "{src}"])]),
    ".fish": ("Fish", [(None, ["fish", "{src}"])]),
    ".tcl": ("Tcl", [(None, ["tclsh", "{src}"])]),
    ".rkt": ("Racket", [(None, ["racket", "{src}"])]),
    ".scm": ("Scheme", [(None, ["guile", "--no-auto-compile", "{src}"]), (None, ["chez", "--script", "{src}"])]),
    ".lisp": ("Common Lisp", [(None, ["sbcl", "--script", "{src}"])]),
    ".raku": ("Raku", [(None, ["raku", "{src}"])]),
    ".awk": ("AWK", [(None, ["awk", "-f", "{src}"])]),
    ".m": ("Octave", [(None, ["octave", "--no-gui", "--quiet", "{src}"])]),
    ".coffee": ("CoffeeScript", [(None, ["coffee", "{src}"])]),
    ".jsx": ("JSX", [(None, ["tsx", "{src}"]), (None, ["bun", "{src}"]), (None, ["deno", "run", "{src}"])]),
    ".tsx": ("TSX", [(None, ["tsx", "{src}"]), (None, ["bun", "{src}"]), (None, ["deno", "run", "{src}"])]),
    ".mojo": ("Mojo", [(None, ["mojo", "run", "{src}"])]),
    ".odin": ("Odin", [(None, ["odin", "run", "{src}", "-file"])]),
    ".vala": ("Vala", [(None, ["vala", "{src}"])]),
    ".sql": ("SQL (SQLite)", [(None, ["sqlite3", ":memory:", ".read '{src}'"])]),
}


def default_run_commands_path() -> Path:
    from termcode.workspace import default_recent_store

    return default_recent_store().parent / "run-commands.json"


def load_user_run_commands(path: Path | None = None) -> dict[str, object]:
    """Your own run commands, e.g. {".foo": "foo-run {file}"} or
    {".bar": {"compile": "barc {file} -o {out}", "run": "{out}"}}."""
    import json

    target = path or default_run_commands_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k).lower() if str(k).startswith(".") else "." + str(k).lower(): v for k, v in data.items()}


def _fill(template: list[str], values: dict[str, str]) -> list[str]:
    out: list[str] = []
    for token in template:
        try:
            out.append(token.format(**values))
        except (KeyError, IndexError, ValueError):
            out.append(token)
    return out


def _plan_from_recipe(
    label: str, recipe: Recipe, source: Path, values: dict[str, str], which: Callable[[str], str | None]
) -> RunPlan | None:
    compile_tpl, run_tpl = recipe
    compile_cmd = _fill(compile_tpl, values) if compile_tpl else None
    run_cmd = _fill(run_tpl, values)
    # Resolve the tools that must be installed (the compiled program itself doesn't count).
    for cmd in (compile_cmd, run_cmd):
        if cmd is None or cmd[0] == values["out"]:
            continue
        exe = which(cmd[0])
        if not exe:
            return None
        cmd[0] = exe
    shown = [Path(c[0]).name if i == 0 else c for i, c in enumerate(run_cmd)]
    display = " ".join(shown).replace(str(source), source.name)
    if compile_cmd:
        compile_shown = " ".join([Path(compile_cmd[0]).name, *compile_cmd[1:]]).replace(str(source), source.name)
        display = f"{compile_shown} && {display}"
    return RunPlan(
        language=Language.OTHER,
        source=source,
        compile_cmd=compile_cmd,
        run_cmd=run_cmd,
        cwd=source.parent,
        display_cmd=display,
    )


def _user_recipe(entry: object, platform: str) -> Recipe | None:
    import shlex

    def split(text: object) -> list[str] | None:
        if not isinstance(text, str) or not text.strip():
            return None
        parts = shlex.split(text, posix=platform != "win32")
        # Users write {file}; recipes use {src}.
        return [p.replace("{file}", "{src}") for p in parts]

    if isinstance(entry, str):
        run = split(entry)
        return (None, run) if run else None
    if isinstance(entry, dict):
        run = split(entry.get("run"))
        if not run:
            return None
        return (split(entry.get("compile")), run)
    return None


_PAGE_EXTENSIONS = {".html", ".htm", ".xhtml", ".svg"}
_STYLE_EXTENSIONS = {".css", ".scss", ".sass", ".less"}


def _page_for(source: Path) -> Path | None:
    """The page F5 should open: the file itself, or for a stylesheet the HTML next to it."""
    ext = source.suffix.lower()
    if ext in _PAGE_EXTENSIONS:
        return source
    if ext in _STYLE_EXTENSIONS:
        pages = sorted(p for p in source.parent.glob("*.htm*") if p.is_file())
        for preferred in ("index.html", "index.htm"):
            for page in pages:
                if page.name.lower() == preferred:
                    return page
        if pages:
            return pages[0]
        raise RunnerError(f"No HTML page next to {source.name} to open in the browser.")
    return None


def _opener_cmd(target: Path, platform: str, which: Callable[[str], str | None], what: str) -> list[str]:
    """Command that opens a file in the system's default app (browser, PDF viewer)."""
    if platform == "win32":
        return ["cmd", "/c", "start", '""', str(target)]
    candidates = [["open"]] if platform == "darwin" else [["xdg-open"], ["gio", "open"], ["sensible-browser"]]
    for candidate in candidates:
        exe = which(candidate[0])
        if exe:
            return [exe, *candidate[1:], str(target)]
    raise RunnerError(f"Could not find a way to open the {what} (xdg-open). Open {target.name} manually.")


def _browser_plan(page: Path, source: Path, platform: str, which: Callable[[str], str | None]) -> RunPlan:
    return RunPlan(
        language=Language.OTHER,
        source=source,
        compile_cmd=None,
        run_cmd=_opener_cmd(page, platform, which, "browser"),
        cwd=page.parent,
        display_cmd=f"open {page.name} in your browser",
    )


# ---------- LaTeX: build the PDF, then open it ----------

_LATEX_EXTENSIONS = {".tex", ".ltx", ".latex"}
_TEX_ENGINES = ("pdflatex", "xelatex", "lualatex")


def _read_head(path: Path, limit: int = 200_000) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def _is_main_document(path: Path) -> bool:
    return "\\documentclass" in _read_head(path)


def _latex_main_for(source: Path) -> Path:
    """The document to build: the file itself, or for a chapter/section file
    (no \\documentclass) the main .tex next to it, like F5 on CSS opens its page."""
    if _is_main_document(source):
        return source
    head = _read_head(source, 4000)
    # TeXstudio / VS Code LaTeX Workshop magic comment: % !TEX root = main.tex
    for line in head.splitlines()[:20]:
        stripped = line.strip().lstrip("%").strip()
        lower = stripped.lower()
        if lower.startswith("!tex root") and "=" in stripped:
            root = (source.parent / stripped.split("=", 1)[1].strip()).resolve()
            if root.is_file():
                return root
    folders = [source.parent, source.parent.parent] if source.parent.parent != source.parent else [source.parent]
    for folder in folders:
        mains = sorted(
            p for p in folder.glob("*")
            if p.is_file() and p.suffix.lower() in _LATEX_EXTENSIONS and p != source and _is_main_document(p)
        )
        for preferred in ("main.tex", "thesis.tex", "report.tex", "paper.tex"):
            for main in mains:
                if main.name.lower() == preferred:
                    return main
        if mains:
            return mains[0]
    raise RunnerError(
        f"{source.name} has no \\documentclass, and there is no main .tex file next to it to build. "
        "Open the main file and press F5 there."
    )


def latex_engine_for(text: str) -> str:
    """pdflatex unless the document asks for XeLaTeX or LuaLaTeX.

    Honors the `% !TEX program = xelatex` comment; otherwise fontspec,
    polyglossia or system fonts (e.g. Bangla, Arabic, CJK) need XeLaTeX.
    """
    for line in text.splitlines()[:20]:
        stripped = line.strip().lstrip("%").strip().lower().replace(" ", "")
        if stripped.startswith("!texprogram="):
            name = stripped.split("=", 1)[1]
            for engine in _TEX_ENGINES:
                if name.startswith(engine.removesuffix("latex")):
                    return engine
    if "luacode" in text or "\\directlua" in text:
        return "lualatex"
    for marker in ("{fontspec}", "{polyglossia}", "\\setmainfont", "{xeCJK}", "{unicode-math}", "{fontspec,", ",fontspec}"):
        if marker in text:
            return "xelatex"
    return "pdflatex"


def _latex_plan(source: Path, platform: str, which: Callable[[str], str | None]) -> RunPlan:
    main = _latex_main_for(source)
    engine = latex_engine_for(_read_head(main))
    pdf = main.with_suffix(".pdf")
    flags = ["-interaction=nonstopmode", "-halt-on-error", "-file-line-error"]

    latexmk = which("latexmk")
    engine_exe = which(engine)
    tectonic = which("tectonic")
    if latexmk and engine_exe:
        # latexmk reruns LaTeX/BibTeX until references and the table of contents settle.
        engine_flag = {"pdflatex": "-pdf", "xelatex": "-xelatex", "lualatex": "-lualatex"}[engine]
        compile_cmd = [latexmk, engine_flag, *flags, main.name]
        shown = f"latexmk {engine_flag} {main.name}"
    elif engine_exe:
        compile_cmd = [engine_exe, *flags, main.name]
        shown = f"{engine} {main.name}"
    elif tectonic:
        compile_cmd = [tectonic, main.name]
        shown = f"tectonic {main.name}"
    else:
        raise RunnerError(
            f"Could not find '{engine}' on PATH. Install a TeX distribution "
            "(TeX Live, MacTeX or MiKTeX) to build .tex files."
        )
    return RunPlan(
        language=Language.OTHER,
        source=source,
        compile_cmd=compile_cmd,
        run_cmd=_opener_cmd(pdf, platform, which, "PDF viewer"),
        cwd=main.parent,
        display_cmd=f"{shown} && open {pdf.name}",
    )


def shebang_command(source: Path, which: Callable[[str], str | None]) -> list[str] | None:
    """Command from a `#!` first line (e.g. #!/usr/bin/env ruby), or None."""
    import shlex

    try:
        with source.open("r", encoding="utf-8", errors="replace") as fh:
            first = fh.readline().strip()
    except OSError:
        return None
    if not first.startswith("#!"):
        return None
    try:
        tokens = shlex.split(first[2:].strip())
    except ValueError:
        return None
    if tokens and Path(tokens[0]).name == "env":
        tokens = tokens[1:]
        if tokens and tokens[0] == "-S":
            tokens = tokens[1:]
    if not tokens:
        return None
    tool = tokens[0]
    exe = tool if Path(tool).is_absolute() and Path(tool).exists() else which(Path(tool).name)
    if not exe:
        return None
    return [exe, *tokens[1:], str(source)]


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
    label = _LANGUAGE_LABELS.get(path.suffix.lower())
    if label:
        return label
    from termcode.highlighting import pygments_lexer_for

    lexer = pygments_lexer_for(path.name)
    return lexer.name if lexer is not None else "Text"


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
    user_commands: dict[str, object] | Path | None = None,
) -> RunPlan:
    """Build compile and run commands for a file in any supported language.

    Order: your run-commands.json, the built-in languages, the recipe table,
    then the file's #! line.
    """
    plat = platform or sys.platform
    ext = source.suffix.lower()
    binary_out = temp_dir / ("app.exe" if plat == "win32" else "app")
    values = {
        "src": str(source),
        "out": str(binary_out),
        "tmp": str(temp_dir),
        "dir": str(source.parent),
        "stem": source.stem,
        "name": source.name,
    }

    commands = load_user_run_commands(user_commands) if not isinstance(user_commands, dict) else user_commands
    if ext and ext in commands:
        recipe = _user_recipe(commands[ext], plat)
        if recipe is None:
            raise RunnerError(f"Your run command for {ext} files is empty or malformed.")
        plan = _plan_from_recipe(ext, recipe, source, values, which)
        if plan is None:
            tool = (recipe[0] or recipe[1])[0]
            raise RunnerError(f"Could not find '{tool}' on PATH (from your run command for {ext} files).")
        return plan

    page = _page_for(source)
    if page is not None:
        return _browser_plan(page, source, plat, which)

    if ext in _LATEX_EXTENSIONS:
        return _latex_plan(source, plat, which)

    lang = detect_language(source)
    if lang is None:
        if ext in _EXTRA_RUNNERS:
            label, recipes = _EXTRA_RUNNERS[ext]
            for recipe in recipes:
                plan = _plan_from_recipe(label, recipe, source, values, which)
                if plan is not None:
                    return plan
            tools = " or ".join(f"'{(r[0] or r[1])[0]}'" for r in recipes)
            raise RunnerError(f"Could not find {tools} on PATH. Install {label} to run {ext} files.")
        shebang = shebang_command(source, which)
        if shebang is not None:
            return RunPlan(
                language=Language.OTHER,
                source=source,
                compile_cmd=None,
                run_cmd=shebang,
                cwd=source.parent,
                display_cmd=f"{Path(shebang[0]).name} {' '.join(shebang[1:-1])} {source.name}".replace("  ", " "),
            )
        shown = ext or "(no extension)"
        raise RunnerError(
            f"TmCode doesn't know how to run {shown} files yet. Add a #! line at the top, "
            f"or add a command to {default_run_commands_path()}"
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
