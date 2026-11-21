"""Language Server Protocol (LSP) client: errors, smart completions, hover and go to definition.

TmCode does not ship language servers. It uses the standard ones when they are
installed (pyright for Python, clangd for C/C++, typescript-language-server,
rust-analyzer, gopls, texlab...), the same ones VS Code and Neovim use.

Everything runs on the app's asyncio loop: one server process per language and
folder, JSON-RPC over the server's stdin/stdout.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

# ---------- which server for which file ----------


@dataclass(frozen=True)
class ServerSpec:
    key: str
    label: str
    commands: tuple[tuple[str, ...], ...]
    install: str


def _hint(linux: str, mac: str | None = None, windows: str | None = None) -> str:
    if sys.platform == "darwin" and mac:
        return mac
    if sys.platform == "win32" and windows:
        return windows
    return linux


SERVERS: dict[str, ServerSpec] = {
    spec.key: spec
    for spec in (
        ServerSpec("python", "Python", (
            ("basedpyright-langserver", "--stdio"), ("pyright-langserver", "--stdio"),
            ("pylsp",), ("jedi-language-server",),
        ), "pipx install basedpyright   (or: npm install -g pyright)"),
        ServerSpec("c", "C/C++", (("clangd",),), _hint(
            "sudo apt install clangd   (or: pipx install clangd)",
            "brew install llvm   (or: xcode-select --install)",
            "winget install LLVM.LLVM",
        )),
        ServerSpec("typescript", "JavaScript/TypeScript", (
            ("typescript-language-server", "--stdio"),
            ("tsc", "--lsp", "--stdio"),   # TypeScript 7 has its own server
        ),
                   "npm install -g typescript-language-server typescript"),
        ServerSpec("rust", "Rust", (("rust-analyzer",),), "rustup component add rust-analyzer"),
        ServerSpec("go", "Go", (("gopls",),), "go install golang.org/x/tools/gopls@latest"),
        ServerSpec("latex", "LaTeX", (("texlab",),), _hint(
            "cargo install texlab   (or download it from github.com/latex-lsp/texlab)",
            "brew install texlab", "winget install texlab",
        )),
        ServerSpec("bash", "Bash", (("bash-language-server", "start"),), "npm install -g bash-language-server"),
        ServerSpec("lua", "Lua", (("lua-language-server",),), _hint(
            "download lua-language-server from github.com/LuaLS/lua-language-server",
            "brew install lua-language-server",
        )),
        ServerSpec("html", "HTML", (("vscode-html-language-server", "--stdio"),),
                   "npm install -g vscode-langservers-extracted"),
        ServerSpec("css", "CSS", (("vscode-css-language-server", "--stdio"),),
                   "npm install -g vscode-langservers-extracted"),
        ServerSpec("json", "JSON", (("vscode-json-language-server", "--stdio"),),
                   "npm install -g vscode-langservers-extracted"),
        ServerSpec("java", "Java", (("jdtls",),), _hint(
            "install Eclipse JDT Language Server (jdtls)", "brew install jdtls",
        )),
        ServerSpec("kotlin", "Kotlin", (("kotlin-language-server",),), "install kotlin-language-server"),
        ServerSpec("dart", "Dart", (("dart", "language-server", "--protocol=lsp"),), "install the Dart SDK"),
        ServerSpec("zig", "Zig", (("zls",),), "install zls (github.com/zigtools/zls)"),
        ServerSpec("haskell", "Haskell", (("haskell-language-server-wrapper", "--lsp"),),
                   "ghcup install hls"),
        ServerSpec("ruby", "Ruby", (("ruby-lsp",), ("solargraph", "stdio")), "gem install ruby-lsp"),
        ServerSpec("php", "PHP", (("intelephense", "--stdio"),), "npm install -g intelephense"),
        ServerSpec("yaml", "YAML", (("yaml-language-server", "--stdio"),), "npm install -g yaml-language-server"),
        ServerSpec("toml", "TOML", (("taplo", "lsp", "stdio"),), "cargo install taplo-cli --features lsp"),
        ServerSpec("csharp", "C#", (("csharp-ls",), ("OmniSharp", "-lsp")), "dotnet tool install -g csharp-ls"),
        ServerSpec("swift", "Swift", (("sourcekit-lsp",),), "install Swift (sourcekit-lsp comes with it)"),
        ServerSpec("ocaml", "OCaml", (("ocamllsp",),), "opam install ocaml-lsp-server"),
    )
}

# extension -> (server key, LSP languageId)
_EXTENSIONS: dict[str, tuple[str, str]] = {
    ".py": ("python", "python"), ".pyi": ("python", "python"), ".pyw": ("python", "python"),
    ".c": ("c", "c"), ".h": ("c", "c"),
    **{e: ("c", "cpp") for e in (".cpp", ".cc", ".cxx", ".c++", ".cp", ".hpp", ".hh", ".hxx", ".h++")},
    ".m": ("c", "objective-c"),
    ".js": ("typescript", "javascript"), ".mjs": ("typescript", "javascript"),
    ".cjs": ("typescript", "javascript"), ".jsx": ("typescript", "javascriptreact"),
    ".ts": ("typescript", "typescript"), ".mts": ("typescript", "typescript"),
    ".cts": ("typescript", "typescript"), ".tsx": ("typescript", "typescriptreact"),
    ".rs": ("rust", "rust"), ".go": ("go", "go"),
    ".tex": ("latex", "latex"), ".ltx": ("latex", "latex"), ".latex": ("latex", "latex"),
    ".sty": ("latex", "latex"), ".cls": ("latex", "latex"), ".bib": ("latex", "bibtex"),
    ".sh": ("bash", "shellscript"), ".bash": ("bash", "shellscript"),
    ".lua": ("lua", "lua"),
    ".html": ("html", "html"), ".htm": ("html", "html"),
    ".css": ("css", "css"), ".scss": ("css", "scss"), ".less": ("css", "less"),
    ".json": ("json", "json"), ".jsonc": ("json", "jsonc"),
    ".java": ("java", "java"), ".kt": ("kotlin", "kotlin"), ".kts": ("kotlin", "kotlin"),
    ".dart": ("dart", "dart"), ".zig": ("zig", "zig"), ".hs": ("haskell", "haskell"),
    ".rb": ("ruby", "ruby"), ".php": ("php", "php"),
    ".yaml": ("yaml", "yaml"), ".yml": ("yaml", "yaml"), ".toml": ("toml", "toml"),
    ".cs": ("csharp", "csharp"), ".swift": ("swift", "swift"),
    ".ml": ("ocaml", "ocaml"), ".mli": ("ocaml", "ocaml"),
}


def language_for(path: Path) -> tuple[str, str] | None:
    """(server key, languageId) for a file, or None if no server is known."""
    return _EXTENSIONS.get(path.suffix.lower())


def default_config_path() -> Path:
    from platformdirs import user_config_dir

    return Path(user_config_dir("termcode")) / "lsp.json"


def load_config(path: Path | None = None) -> dict[str, object]:
    """User overrides from lsp.json, e.g. {".py": "pylsp", ".rs": ["ra-multiplex"], ".tex": false}.

    Use "*": false to turn language servers off everywhere.
    """
    target = path or default_config_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


@dataclass(frozen=True)
class ServerChoice:
    key: str
    label: str
    language_id: str
    commands: tuple[tuple[str, ...], ...]   # installed candidates, best first; empty: none installed
    install: str

    @property
    def command(self) -> list[str] | None:
        return list(self.commands[0]) if self.commands else None


def choose_server(
    path: Path,
    config: Mapping[str, object] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> ServerChoice | None:
    """The server to use for path. None if the language has none or servers are turned off."""
    config = config or {}
    if config.get("*") is False:
        return None
    ext = path.suffix.lower()
    known = language_for(path)
    override = config.get(ext)
    if override is False or override == "off":
        return None
    if isinstance(override, (str, list)) and override:
        import shlex

        parts = shlex.split(override, posix=sys.platform != "win32") if isinstance(override, str) else [str(p) for p in override]
        key = f"custom:{parts[0]}"
        exe = which(parts[0])
        language_id = known[1] if known else ext.lstrip(".")
        return ServerChoice(key, parts[0], language_id, ((exe, *parts[1:]),) if exe else (),
                            f"'{parts[0]}' from your lsp.json is not on PATH")
    if known is None:
        return None
    key, language_id = known
    spec = SERVERS[key]
    installed = []
    for candidate in spec.commands:
        exe = which(candidate[0])
        if exe:
            installed.append((exe, *candidate[1:]))
    return ServerChoice(key, spec.label, language_id, tuple(installed), spec.install)


def server_name(command: list[str]) -> str:
    """Short name for the status bar: pyright, clangd, typescript-language-server..."""
    parts = [Path(p).stem for p in command]
    name = parts[0]
    if name.lower().rstrip("0123456789.") in ("python", "python3", "node", "deno", "bun", "java") and len(parts) > 1:
        name = next((p for p in parts[1:] if not p.startswith("-")), name)
    for suffix in ("-langserver", "-language-server-wrapper"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


# ---------- positions ----------
#
# The editor counts columns in characters; LSP counts UTF-16 units unless the
# server agrees to another encoding.


def to_lsp_column(line: str, col: int, encoding: str) -> int:
    col = max(0, min(col, len(line)))
    if encoding == "utf-32":
        return col
    if encoding == "utf-8":
        return len(line[:col].encode("utf-8"))
    return len(line[:col].encode("utf-16-le")) // 2


def from_lsp_column(line: str, character: int, encoding: str) -> int:
    if character <= 0:
        return 0
    if encoding == "utf-32":
        return min(character, len(line))
    units = 0
    for index, ch in enumerate(line):
        if units >= character:
            return index
        if encoding == "utf-8":
            units += len(ch.encode("utf-8"))
        else:
            units += 2 if ord(ch) > 0xFFFF else 1
    return len(line)


def path_to_uri(path: Path) -> str:
    return path.resolve().as_uri()


def uri_to_path(uri: str) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    raw = url2pathname(unquote(parsed.path)) if sys.platform == "win32" else unquote(parsed.path)
    if parsed.netloc and sys.platform == "win32":
        raw = f"\\\\{parsed.netloc}{raw}"
    return Path(raw)


# ---------- results ----------

ERROR, WARNING, INFO, HINT = 1, 2, 3, 4


@dataclass(frozen=True)
class Diagnostic:
    start: tuple[int, int]
    end: tuple[int, int]
    severity: int
    message: str
    source: str = ""

    @property
    def label(self) -> str:
        return {ERROR: "error", WARNING: "warning", INFO: "info"}.get(self.severity, "hint")


@dataclass(frozen=True)
class CompletionItem:
    label: str
    insert: str
    start_col: int        # replace from here to the cursor
    detail: str = ""
    kind: str = ""
    sort: str = ""


@dataclass(frozen=True)
class Location:
    path: Path
    row: int
    col: int


_KINDS = {
    1: "text", 2: "method", 3: "function", 4: "constructor", 5: "field", 6: "variable",
    7: "class", 8: "interface", 9: "module", 10: "property", 11: "unit", 12: "value",
    13: "enum", 14: "keyword", 15: "snippet", 16: "color", 17: "file", 18: "reference",
    19: "folder", 20: "member", 21: "constant", 22: "struct", 23: "event", 24: "operator",
    25: "type",
}

_SNIPPET_TABSTOP = re.compile(r"\$\{\d+:([^}]*)\}|\$\{\d+\}|\$\d+")


def plain_snippet(text: str) -> str:
    """Turn an LSP snippet like `printf(${1:fmt})$0` into plain text `printf(fmt)`."""
    return _SNIPPET_TABSTOP.sub(lambda m: m.group(1) or "", text).replace("\\$", "$").replace("\\}", "}")


_WORD_TAIL = re.compile(r"[A-Za-z0-9_$]*$")


def word_start(line: str, col: int) -> int:
    match = _WORD_TAIL.search(line[:col])
    return match.start() if match else col


def parse_completions(result: object, line: str, row: int, col: int, encoding: str) -> list[CompletionItem]:
    raw = result.get("items", []) if isinstance(result, dict) else result
    if not isinstance(raw, list):
        return []
    default_start = word_start(line, col)
    items: list[CompletionItem] = []
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("label"):
            continue
        label = str(entry["label"]).strip()
        insert = entry.get("insertText") or label
        start = default_start
        edit = entry.get("textEdit")
        if isinstance(edit, dict):
            insert = edit.get("newText", insert)
            rng = edit.get("range") or edit.get("replace") or edit.get("insert")
            if isinstance(rng, dict) and rng.get("start", {}).get("line") == row:
                start = min(col, from_lsp_column(line, int(rng["start"].get("character", 0)), encoding))
        if entry.get("insertTextFormat") == 2:
            insert = plain_snippet(str(insert))
        detail = entry.get("detail") or (entry.get("labelDetails") or {}).get("description") or ""
        items.append(CompletionItem(
            label=label,
            insert=str(insert),
            start_col=start,
            detail=str(detail).strip().splitlines()[0] if detail else "",
            kind=_KINDS.get(int(entry.get("kind") or 0), ""),
            sort=str(entry.get("sortText") or label),
        ))
    return items


def rank_completions(items: list[CompletionItem], typed: str, limit: int = 12) -> list[CompletionItem]:
    """Keep items that match what was typed (prefix first, then fuzzy), in the server's order."""
    lower = typed.lower()

    def filter_text(item: CompletionItem) -> str:
        return item.label.lstrip("•· ").lower()

    def fuzzy(text: str) -> bool:
        pos = 0
        for ch in lower:
            pos = text.find(ch, pos) + 1
            if pos == 0:
                return False
        return True

    prefix = [i for i in items if filter_text(i).startswith(lower)]
    rest = [i for i in items if i not in prefix and lower and fuzzy(filter_text(i))]
    ordered = sorted(prefix, key=lambda i: (i.sort, i.label)) + sorted(rest, key=lambda i: (i.sort, i.label))
    seen: set[str] = set()
    out: list[CompletionItem] = []
    for item in ordered:
        if item.label in seen:
            continue
        seen.add(item.label)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def hover_text(result: object) -> str:
    """Plain text from a hover result (markdown fences and most markup removed)."""
    if not isinstance(result, dict):
        return ""
    contents = result.get("contents")
    parts: list[str] = []

    def add(value: object) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            parts.append(str(value.get("value", "")))
        elif isinstance(value, list):
            for v in value:
                add(v)

    add(contents)
    text = "\n\n".join(p for p in parts if p.strip())
    lines = []
    for line in text.splitlines():
        if line.strip().startswith("```"):
            continue
        line = re.sub(r"\\([\\`*_{}\[\]()#+\-.!<>])", r"\1", line)
        lines.append(line.rstrip())
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    # Collapse runs of blank lines and horizontal rules.
    out: list[str] = []
    for line in lines:
        if line.strip() in ("---", "***", "___"):
            line = ""
        if not line and out and not out[-1]:
            continue
        out.append(line)
    return "\n".join(out)


def parse_locations(result: object) -> list[tuple[str, int, int]]:
    """(uri, line, character) for each location in a definition result."""
    if result is None:
        return []
    entries = result if isinstance(result, list) else [result]
    out: list[tuple[str, int, int]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        uri = entry.get("uri") or entry.get("targetUri")
        rng = entry.get("targetSelectionRange") or entry.get("range") or entry.get("targetRange")
        if not uri or not isinstance(rng, dict):
            continue
        start = rng.get("start", {})
        out.append((str(uri), int(start.get("line", 0)), int(start.get("character", 0))))
    return out


# ---------- JSON-RPC client ----------


class LspClient:
    """One running language server."""

    def __init__(
        self,
        command: list[str],
        root: Path,
        on_notification: Callable[[LspClient, str, dict], None],
    ) -> None:
        self.command = command
        self.root = root
        self.on_notification = on_notification
        self.proc: asyncio.subprocess.Process | None = None
        self.capabilities: dict = {}
        self.encoding = "utf-16"
        self.ready = asyncio.Event()
        self.failed = False
        self.error = ""   # why it failed to start, from the server
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None
        self._write_lock = asyncio.Lock()

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None and not self.failed

    async def start(self, timeout: float = 30.0) -> bool:
        try:
            kwargs: dict[str, object] = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
            self.proc = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=str(self.root),
                **kwargs,  # type: ignore[arg-type]
            )
        except (OSError, ValueError) as exc:
            self.failed = True
            self.error = str(exc)
            self.ready.set()
            return False
        self._reader = asyncio.create_task(self._read_loop())
        result = await self.request("initialize", self._initialize_params(), timeout=timeout)
        if not isinstance(result, dict):
            if not self.error:
                exited = self.proc.returncode is not None
                self.error = "it exited right away" if exited else "it did not answer"
            self.failed = True
            self.ready.set()
            await self.stop()
            return False
        self.capabilities = result.get("capabilities") or {}
        self.encoding = self.capabilities.get("positionEncoding") or "utf-16"
        await self.notify("initialized", {})
        self.ready.set()
        return True

    def _initialize_params(self) -> dict:
        root_uri = path_to_uri(self.root)
        return {
            "processId": os.getpid(),
            "clientInfo": {"name": "TmCode"},
            "rootUri": root_uri,
            "rootPath": str(self.root),
            "workspaceFolders": [{"uri": root_uri, "name": self.root.name}],
            "capabilities": {
                "general": {"positionEncodings": ["utf-32", "utf-16"]},
                "workspace": {"configuration": True, "workspaceFolders": True},
                "textDocument": {
                    "synchronization": {"didSave": True, "dynamicRegistration": False},
                    "publishDiagnostics": {"relatedInformation": False},
                    "diagnostic": {"dynamicRegistration": False, "relatedDocumentSupport": False},
                    "completion": {
                        "completionItem": {
                            "snippetSupport": False,
                            "labelDetailsSupport": True,
                            "documentationFormat": ["plaintext", "markdown"],
                        },
                        "contextSupport": True,
                    },
                    "hover": {"contentFormat": ["plaintext", "markdown"]},
                    "definition": {"linkSupport": True},
                },
                "window": {"workDoneProgress": False},
            },
            "initializationOptions": {},
        }

    async def _send(self, message: dict) -> None:
        if self.proc is None or self.proc.stdin is None or self.proc.returncode is not None:
            raise ConnectionError("server not running")
        body = json.dumps(message, ensure_ascii=False).encode("utf-8")
        async with self._write_lock:
            self.proc.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
            await self.proc.stdin.drain()

    async def request(self, method: str, params: object, timeout: float = 10.0) -> object:
        """Send a request; returns the result, or None on error, timeout or a dead server."""
        if self.proc is None or self.proc.returncode is not None:
            return None
        self._next_id += 1
        msg_id = self._next_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        try:
            await self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout)
        except (asyncio.TimeoutError, ConnectionError, OSError):
            if msg_id in self._pending and method not in ("initialize", "shutdown"):
                with contextlib.suppress(Exception):
                    await self.notify("$/cancelRequest", {"id": msg_id})
            return None
        finally:
            self._pending.pop(msg_id, None)

    async def notify(self, method: str, params: object) -> None:
        with contextlib.suppress(ConnectionError, OSError):
            await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _read_loop(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        stream = self.proc.stdout
        try:
            while True:
                length = None
                while True:
                    header = await stream.readline()
                    if not header:
                        return
                    header = header.strip()
                    if not header:
                        break
                    name, _, value = header.decode("ascii", errors="replace").partition(":")
                    if name.strip().lower() == "content-length":
                        length = int(value.strip())
                if length is None:
                    continue
                body = await stream.readexactly(length)
                try:
                    message = json.loads(body)
                except ValueError:
                    continue
                if isinstance(message, dict):
                    await self._dispatch(message)
        except (asyncio.IncompleteReadError, ConnectionError, OSError, ValueError):
            return
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_result(None)

    async def _dispatch(self, message: dict) -> None:
        method = message.get("method")
        if method is None:
            future = self._pending.get(message.get("id"))  # type: ignore[arg-type]
            if future is not None and not future.done():
                if "error" in message:
                    error = message.get("error") or {}
                    if not self.capabilities:  # still starting: remember why
                        self.error = str(error.get("message", "")).strip()
                    future.set_result(None)
                else:
                    future.set_result(message.get("result"))
            return
        if "id" in message:
            await self._answer_server_request(message["id"], method, message.get("params") or {})
            return
        try:
            self.on_notification(self, method, message.get("params") or {})
        except Exception:
            pass

    async def _answer_server_request(self, msg_id: object, method: str, params: dict) -> None:
        result: object = None
        error = None
        if method == "workspace/configuration":
            result = [None for _ in params.get("items", [])]
        elif method == "workspace/workspaceFolders":
            result = [{"uri": path_to_uri(self.root), "name": self.root.name}]
        elif method == "workspace/applyEdit":
            result = {"applied": False}
        elif method in ("client/registerCapability", "client/unregisterCapability",
                        "window/workDoneProgress/create", "window/showMessageRequest",
                        "window/showDocument"):
            result = None
        else:
            error = {"code": -32601, "message": f"TmCode does not handle {method}"}
        reply: dict = {"jsonrpc": "2.0", "id": msg_id}
        if error:
            reply["error"] = error
        else:
            reply["result"] = result
        with contextlib.suppress(ConnectionError, OSError):
            await self._send(reply)

    async def stop(self) -> None:
        if self.proc is None:
            return
        if self.proc.returncode is None and not self.failed:
            await self.request("shutdown", None, timeout=2.0)
            await self.notify("exit", None)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.proc.wait(), 2.0)
        self.kill()
        if self._reader is not None:
            self._reader.cancel()

    def kill(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, OSError):
                self.proc.kill()


# ---------- documents and servers for the app ----------


@dataclass
class _Document:
    client: LspClient
    language_id: str
    version: int = 1
    text: str = ""
    pending: str | None = None
    flush_task: asyncio.Task | None = field(default=None, repr=False)
    pull_task: asyncio.Task | None = field(default=None, repr=False)


class LspManager:
    """Starts servers on demand and keeps them in sync with the open files."""

    CHANGE_DELAY = 0.25

    def __init__(
        self,
        on_diagnostics: Callable[[Path], None],
        *,
        config: Mapping[str, object] | None = None,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.on_diagnostics = on_diagnostics
        self.config = dict(config) if config is not None else load_config()
        self.which = which
        self.clients: dict[tuple[str, Path], LspClient] = {}
        self.docs: dict[Path, _Document] = {}
        self.diagnostics: dict[Path, list[Diagnostic]] = {}
        self.labels: dict[LspClient, str] = {}
        self.errors: dict[str, str] = {}   # server key -> why no server started
        self._starting: dict[tuple[str, Path], asyncio.Task] = {}

    # -- servers --

    def choice_for(self, path: Path) -> ServerChoice | None:
        return choose_server(path, self.config, self.which)

    def client_for(self, path: Path) -> LspClient | None:
        doc = self.docs.get(path.resolve())
        return doc.client if doc and doc.client.alive else None

    def server_label(self, path: Path) -> str:
        client = self.client_for(path)
        return self.labels.get(client, "") if client else ""

    async def _client(self, choice: ServerChoice, root: Path) -> LspClient | None:
        key = (choice.key, root)
        task = self._starting.get(key)
        if task is None:
            task = asyncio.create_task(self._start_first_working(choice, root))
            self._starting[key] = task
        client = await task
        if client is not None and not client.alive:
            # It crashed since; start a fresh one.
            self._starting.pop(key, None)
            self.clients.pop(key, None)
            return await self._client(choice, root)
        return client

    async def _start_first_working(self, choice: ServerChoice, root: Path) -> LspClient | None:
        """Start the first installed server that works (e.g. TypeScript 7 needs `tsc --lsp`)."""
        errors: list[str] = []
        for command in choice.commands:
            client = LspClient(list(command), root, self._on_notification)
            if await client.start():
                self.clients[(choice.key, root)] = client
                self.labels[client] = server_name(list(command))
                return client
            errors.append(f"{server_name(list(command))}: {client.error or 'failed to start'}")
        self.errors[choice.key] = "; ".join(errors)
        return None

    # -- documents --

    async def open(self, path: Path, text: str, root: Path) -> bool:
        """Start (if needed) the file's server and open the file in it. False if none runs."""
        path = path.resolve()
        doc = self.docs.get(path)
        if doc is not None and doc.client.alive:
            if doc.text != text:
                self.change(path, text)
            return True
        choice = self.choice_for(path)
        if choice is None or choice.command is None:
            return False
        client = await self._client(choice, root.resolve())
        if client is None:
            return False
        if path in self.docs and self.docs[path].client is client:
            return True
        self.docs[path] = _Document(client, choice.language_id, 1, text)
        await client.notify("textDocument/didOpen", {
            "textDocument": {"uri": path_to_uri(path), "languageId": choice.language_id,
                             "version": 1, "text": text},
        })
        self._pull_diagnostics(path)
        return True

    def change(self, path: Path, text: str) -> None:
        """Queue the new text; sent after a short pause in typing."""
        doc = self.docs.get(path.resolve())
        if doc is None or text == (doc.pending if doc.pending is not None else doc.text):
            return
        doc.pending = text
        if doc.flush_task is None or doc.flush_task.done():
            doc.flush_task = asyncio.create_task(self._flush_later(path.resolve()))

    async def _flush_later(self, path: Path) -> None:
        await asyncio.sleep(self.CHANGE_DELAY)
        await self.flush(path)

    async def flush(self, path: Path) -> None:
        doc = self.docs.get(path.resolve())
        if doc is None or doc.pending is None:
            return
        text, doc.pending = doc.pending, None
        doc.version += 1
        doc.text = text
        await doc.client.notify("textDocument/didChange", {
            "textDocument": {"uri": path_to_uri(path), "version": doc.version},
            "contentChanges": [{"text": text}],
        })
        self._pull_diagnostics(path)

    async def saved(self, path: Path, text: str) -> None:
        path = path.resolve()
        doc = self.docs.get(path)
        if doc is None:
            return
        self.change(path, text)
        await self.flush(path)
        await doc.client.notify("textDocument/didSave", {"textDocument": {"uri": path_to_uri(path)}})

    async def close(self, path: Path) -> None:
        path = path.resolve()
        doc = self.docs.pop(path, None)
        self.diagnostics.pop(path, None)
        if doc is not None:
            await doc.client.notify("textDocument/didClose", {"textDocument": {"uri": path_to_uri(path)}})

    # -- features --

    def _line(self, path: Path, row: int) -> str:
        doc = self.docs.get(path.resolve())
        text = None
        if doc is not None:
            text = doc.pending if doc.pending is not None else doc.text
        if text is None:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return ""
        lines = text.split("\n")
        return lines[row] if 0 <= row < len(lines) else ""

    def _position(self, client: LspClient, path: Path, row: int, col: int) -> dict:
        return {"line": row, "character": to_lsp_column(self._line(path, row), col, client.encoding)}

    def trigger_characters(self, path: Path) -> set[str]:
        client = self.client_for(path)
        if client is None:
            return set()
        provider = client.capabilities.get("completionProvider") or {}
        return set(provider.get("triggerCharacters") or [])

    async def completions(self, path: Path, row: int, col: int, trigger: str | None = None) -> list[CompletionItem]:
        client = self.client_for(path)
        if client is None or not client.capabilities.get("completionProvider"):
            return []
        await self.flush(path)
        context = {"triggerKind": 2, "triggerCharacter": trigger} if trigger else {"triggerKind": 1}
        result = await client.request("textDocument/completion", {
            "textDocument": {"uri": path_to_uri(path.resolve())},
            "position": self._position(client, path, row, col),
            "context": context,
        }, timeout=5.0)
        return parse_completions(result, self._line(path, row), row, col, client.encoding)

    async def hover(self, path: Path, row: int, col: int) -> str:
        client = self.client_for(path)
        if client is None or not client.capabilities.get("hoverProvider"):
            return ""
        await self.flush(path)
        result = await client.request("textDocument/hover", {
            "textDocument": {"uri": path_to_uri(path.resolve())},
            "position": self._position(client, path, row, col),
        })
        return hover_text(result)

    async def definition(self, path: Path, row: int, col: int) -> Location | None:
        client = self.client_for(path)
        if client is None or not client.capabilities.get("definitionProvider"):
            return None
        await self.flush(path)
        result = await client.request("textDocument/definition", {
            "textDocument": {"uri": path_to_uri(path.resolve())},
            "position": self._position(client, path, row, col),
        })
        for uri, line, character in parse_locations(result):
            target = uri_to_path(uri)
            if target is None:
                continue
            return Location(target, line, from_lsp_column(self._line(target, line), character, client.encoding))
        return None

    # -- problems: pushed by the server, or pulled by us (newer servers, e.g. TypeScript 7) --

    def _pull_diagnostics(self, path: Path) -> None:
        doc = self.docs.get(path.resolve())
        if doc is None or not doc.client.capabilities.get("diagnosticProvider"):
            return
        if doc.pull_task is not None and not doc.pull_task.done():
            doc.pull_task.cancel()
        doc.pull_task = asyncio.create_task(self._pull(path.resolve(), doc))

    async def _pull(self, path: Path, doc: _Document) -> None:
        version = doc.version
        result = await doc.client.request("textDocument/diagnostic", {
            "textDocument": {"uri": path_to_uri(path)},
        }, timeout=30.0)
        if not isinstance(result, dict) or doc.version != version or self.docs.get(path) is not doc:
            return
        if result.get("kind") == "unchanged":
            return
        self._store_diagnostics(doc.client, path, result.get("items") or [])

    def _on_notification(self, client: LspClient, method: str, params: dict) -> None:
        if method != "textDocument/publishDiagnostics":
            return
        path = uri_to_path(str(params.get("uri", "")))
        if path is None:
            return
        self._store_diagnostics(client, path.resolve(), params.get("diagnostics") or [])

    def _store_diagnostics(self, client: LspClient, path: Path, raw_items: list) -> None:
        found: list[Diagnostic] = []
        for raw in raw_items:
            try:
                rng = raw["range"]
                s_row, e_row = int(rng["start"]["line"]), int(rng["end"]["line"])
                s_col = from_lsp_column(self._line(path, s_row), int(rng["start"]["character"]), client.encoding)
                e_col = from_lsp_column(self._line(path, e_row), int(rng["end"]["character"]), client.encoding)
            except (KeyError, TypeError, ValueError):
                continue
            message = str(raw.get("message", "")).strip()
            found.append(Diagnostic((s_row, s_col), (e_row, e_col), int(raw.get("severity") or ERROR),
                                    message, str(raw.get("source") or "")))
        found.sort(key=lambda d: (d.start, d.severity))
        self.diagnostics[path] = found
        self.on_diagnostics(path)

    # -- shutdown --

    async def shutdown(self) -> None:
        clients = list(self.clients.values())
        self.clients.clear()
        self.docs.clear()
        await asyncio.gather(*(c.stop() for c in clients), return_exceptions=True)

    def kill_all(self) -> None:
        for client in self.clients.values():
            client.kill()
