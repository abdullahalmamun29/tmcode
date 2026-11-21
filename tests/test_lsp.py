import asyncio
import shutil
import sys
from pathlib import Path

import pytest

from termcode.lsp import (
    CompletionItem,
    LspManager,
    choose_server,
    from_lsp_column,
    hover_text,
    parse_completions,
    parse_locations,
    plain_snippet,
    rank_completions,
    to_lsp_column,
    uri_to_path,
    path_to_uri,
)

FAKE = [sys.executable, str(Path(__file__).with_name("fake_lsp_server.py"))]


def test_utf16_columns_round_trip() -> None:
    line = "a😀b"  # the emoji is 2 UTF-16 units
    assert to_lsp_column(line, 2, "utf-16") == 3
    assert from_lsp_column(line, 3, "utf-16") == 2
    assert to_lsp_column(line, 2, "utf-32") == 2
    assert to_lsp_column(line, 2, "utf-8") == 5
    assert from_lsp_column(line, 5, "utf-8") == 2


def test_choose_server_installed_missing_and_overrides(tmp_path: Path) -> None:
    has = lambda *names: (lambda n: f"/bin/{n}" if n in names else None)  # noqa: E731
    py = choose_server(Path("a.py"), {}, has("pyright-langserver"))
    assert py is not None and py.command == ["/bin/pyright-langserver", "--stdio"] and py.language_id == "python"
    # basedpyright wins when both are there
    assert choose_server(Path("a.py"), {}, has("pyright-langserver", "basedpyright-langserver")).command[0].endswith("basedpyright-langserver")
    c = choose_server(Path("x.c++"), {}, has())
    assert c is not None and c.command is None and c.language_id == "cpp" and "clangd" in c.install
    assert choose_server(Path("a.txt"), {}, has()) is None
    assert choose_server(Path("a.py"), {".py": False}, has("pyright-langserver")) is None
    assert choose_server(Path("a.py"), {"*": False}, has("pyright-langserver")) is None
    custom = choose_server(Path("a.py"), {".py": "pylsp -v"}, has("pylsp"))
    assert custom is not None and custom.command == ["/bin/pylsp", "-v"]


def test_server_name() -> None:
    from termcode.lsp import server_name

    assert server_name(["/usr/bin/pyright-langserver", "--stdio"]) == "pyright"
    assert server_name(["/usr/bin/clangd"]) == "clangd"
    assert server_name(["/usr/bin/python3", "-u", "/x/my_server.py"]) == "my_server"
    assert server_name(["typescript-language-server", "--stdio"]) == "typescript-language-server"


def test_parse_completions_snippets_and_ranking() -> None:
    result = {"items": [
        {"label": "print", "kind": 3, "insertText": "print(${1:value})$0", "insertTextFormat": 2, "sortText": "b"},
        {"label": "printf", "kind": 3, "textEdit": {"newText": "printf",
            "range": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 7}}}, "sortText": "a"},
        {"label": "sprint", "kind": 6},
    ]}
    items = parse_completions(result, "    pri", 0, 7, "utf-16")
    assert items[0] == CompletionItem("print", "print(value)", 4, "", "function", "b")
    assert items[1].insert == "printf" and items[1].start_col == 4
    ranked = rank_completions(items, "pri")
    assert [i.label for i in ranked] == ["printf", "print", "sprint"]  # prefix matches first, by sortText
    assert plain_snippet("f(${1:a}, ${2:b})$0") == "f(a, b)"


def test_hover_and_locations() -> None:
    assert hover_text({"contents": {"kind": "markdown", "value": "```py\ndef f()\n```\n---\nDocs\\_here"}}) == "def f()\n\nDocs_here"
    assert hover_text({"contents": ["one", {"language": "c", "value": "int x"}]}) == "one\n\nint x"
    assert hover_text(None) == ""
    assert parse_locations({"uri": "file:///a.py", "range": {"start": {"line": 2, "character": 1}}}) == [("file:///a.py", 2, 1)]
    assert parse_locations([{"targetUri": "file:///b.py", "targetSelectionRange": {"start": {"line": 5, "character": 0}}}]) == [("file:///b.py", 5, 0)]


def test_uri_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "dir with space" / "ä.py"
    assert uri_to_path(path_to_uri(p)) == p.resolve()


@pytest.mark.asyncio
async def test_manager_with_a_real_server_process(tmp_path: Path) -> None:
    src = tmp_path / "a.py"
    src.write_text("x = 1\ny = BAD\n", encoding="utf-8")
    seen: list[Path] = []
    got = asyncio.Event()

    def on_diag(path: Path) -> None:
        seen.append(path)
        got.set()

    manager = LspManager(on_diag, config={".py": FAKE}, which=lambda n: n if n == FAKE[0] else shutil.which(n))
    try:
        assert await manager.open(src, src.read_text(), tmp_path)
        await asyncio.wait_for(got.wait(), 10)
        diags = manager.diagnostics[src.resolve()]
        assert [(d.start, d.end, d.message) for d in diags] == [((1, 4), (1, 7), "BAD is not allowed")]

        # Edits are sent after a short pause; fixing the line clears the problem.
        got.clear()
        manager.change(src, "x = 1\ny = 2\n")
        await asyncio.wait_for(got.wait(), 10)
        assert manager.diagnostics[src.resolve()] == []

        items = await manager.completions(src, 1, 4)
        assert [i.label for i in rank_completions(items, "alp")] == ["alpha_function", "alpine"]
        assert items[0].insert == "alpha_function(x)"
        assert "Does alpha." in await manager.hover(src, 0, 0)
        loc = await manager.definition(src, 1, 0)
        assert loc is not None and loc.path == src.resolve() and (loc.row, loc.col) == (0, 4)
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_pull_diagnostics_and_falling_back_to_the_next_server(tmp_path: Path) -> None:
    """Newer servers (TypeScript 7) wait to be asked for problems; a broken first server is skipped."""
    from termcode.lsp import ServerChoice

    src = tmp_path / "a.ts"
    src.write_text("let x = BAD\n", encoding="utf-8")
    got = asyncio.Event()
    manager = LspManager(lambda p: got.set(), config={})
    broken = (sys.executable, "-c", "import sys; sys.exit(3)")
    choice = ServerChoice("typescript", "TS", "typescript", (broken, (*FAKE, "--pull")), "")
    manager.choice_for = lambda path: choice  # type: ignore[method-assign]
    try:
        assert await manager.open(src, src.read_text(), tmp_path)
        assert manager.server_label(src) == "fake_lsp_server"
        await asyncio.wait_for(got.wait(), 10)
        assert [d.message for d in manager.diagnostics[src.resolve()]] == ["BAD is not allowed"]
        got.clear()
        manager.change(src, "let x = 1\n")
        await asyncio.wait_for(got.wait(), 10)
        assert manager.diagnostics[src.resolve()] == []
    finally:
        await manager.shutdown()

    # When nothing starts, the reason is kept for the user.
    manager = LspManager(lambda p: None, config={})
    manager.choice_for = lambda path: ServerChoice("typescript", "TS", "typescript", (broken,), "")  # type: ignore[method-assign]
    assert not await manager.open(src, "x", tmp_path)
    assert "exited right away" in manager.errors["typescript"]
