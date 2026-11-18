"""Headless Textual UI and CLI integration tests for TermCode."""

import asyncio
from pathlib import Path
import pytest

from textual.widgets import DirectoryTree, Input, TextArea

from termcode.app import TermCodeApp
from termcode.cli import parse_cli_target
from termcode.modals import (
    CommandPaletteModal,
    ConfirmModal,
    FilePickerModal,
    TextInputModal,
    UnsavedChangesModal,
)


async def _wait_until(predicate, timeout: float = 8.0, interval: float = 0.05) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    assert predicate(), "Timed out waiting for condition"


def test_parse_cli_target(tmp_path: Path) -> None:
    f = tmp_path / "demo.py"
    f.write_text("print(1)\n", encoding="utf-8")

    root, initial = parse_cli_target(None, cwd=tmp_path)
    assert root == tmp_path.resolve() and initial is None

    root2, initial2 = parse_cli_target(str(tmp_path))
    assert root2 == tmp_path.resolve() and initial2 is None

    root3, initial3 = parse_cli_target(str(f))
    assert root3 == tmp_path.resolve() and initial3 == f.resolve()

    with pytest.raises(SystemExit):
        parse_cli_target(str(tmp_path / "does_not_exist"))


@pytest.mark.asyncio
async def test_untitled_buffer_write_and_save(tmp_path: Path) -> None:
    app = TermCodeApp(tmp_path)
    async with app.run_test(size=(120, 36)) as pilot:
        editor = app.query_one("#editor", TextArea)
        editor.text = "print('saved!')\n"
        await pilot.pause()
        assert app.is_dirty

        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, TextInputModal)
        app.screen.query_one("#modal-input", Input).value = "hello.py"
        await pilot.press("enter")
        await pilot.pause()

        saved = tmp_path / "hello.py"
        assert saved.is_file()
        assert saved.read_text(encoding="utf-8") == "print('saved!')\n"
        assert app.active_file == saved
        assert not app.is_dirty


@pytest.mark.asyncio
async def test_slash_key_in_editor_vs_outside_and_ctrl_k(tmp_path: Path) -> None:
    app = TermCodeApp(tmp_path)
    async with app.run_test(size=(120, 36)) as pilot:
        editor = app.query_one("#editor", TextArea)
        editor.focus()
        await pilot.pause()

        # Review Focus #5: '/' inside editor inserts literal '/'
        await pilot.press("slash")
        await pilot.pause()
        assert not isinstance(app.screen, CommandPaletteModal)
        assert "/" in editor.text

        # Ctrl+K opens '/' menu even from inside editor
        await pilot.press("ctrl+k")
        await pilot.pause()
        assert isinstance(app.screen, CommandPaletteModal)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, CommandPaletteModal)

        # Pressing '/' when focused on file tree opens '/' menu
        tree = app.query_one("#file-tree", DirectoryTree)
        tree.focus()
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        assert isinstance(app.screen, CommandPaletteModal)
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_run_python_c_cpp_and_interactive_stdin(tmp_path: Path) -> None:
    py_f = tmp_path / "hello.py"
    py_f.write_text("print('py-tui-ok')\n", encoding="utf-8")
    c_f = tmp_path / "hello.c"
    c_f.write_text('#include <stdio.h>\nint main(){printf("c-tui-ok\\n");return 0;}\n', encoding="utf-8")
    cpp_f = tmp_path / "hello.cpp"
    cpp_f.write_text('#include <iostream>\nint main(){std::cout << "cpp-tui-ok\\n";return 0;}\n', encoding="utf-8")
    ask_py = tmp_path / "ask.py"
    ask_py.write_text('name = input("Your name: ")\nprint(f"Welcome {name}")\n', encoding="utf-8")

    app = TermCodeApp(tmp_path, initial_file=py_f)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("f5")
        await _wait_until(lambda: "py-tui-ok" in app.output_history and not app.runner.is_running)

        app.load_file(c_f)
        await pilot.pause()
        await pilot.press("f5")
        await _wait_until(lambda: "c-tui-ok" in app.output_history and not app.runner.is_running)

        app.load_file(cpp_f)
        await pilot.pause()
        await pilot.press("f5")
        await _wait_until(lambda: "cpp-tui-ok" in app.output_history and not app.runner.is_running)

        # Interactive stdin inside the Output panel input bar
        app.load_file(ask_py)
        await pilot.pause()
        await pilot.press("f5")
        await _wait_until(lambda: "Your name:" in app.output_history)

        stdin_input = app.query_one("#stdin-input", Input)
        assert not stdin_input.disabled
        stdin_input.focus()
        stdin_input.value = "Jobayer"
        await pilot.press("enter")
        await _wait_until(lambda: "Welcome Jobayer" in app.output_history and not app.runner.is_running)


@pytest.mark.asyncio
async def test_create_folder_file_rename_and_delete(tmp_path: Path) -> None:
    app = TermCodeApp(tmp_path)
    async with app.run_test(size=(120, 36)) as pilot:
        # Create folder via F3
        await pilot.press("f3")
        await pilot.pause()
        assert isinstance(app.screen, TextInputModal)
        app.screen.query_one("#modal-input", Input).value = "sub"
        await pilot.press("enter")
        await pilot.pause()
        sub_dir = tmp_path / "sub"
        assert sub_dir.is_dir()

        # Select folder, create file inside it via Ctrl+N
        app.selected_path = sub_dir
        await pilot.press("ctrl+n")
        await pilot.pause()
        assert isinstance(app.screen, TextInputModal)
        app.screen.query_one("#modal-input", Input).value = "note.txt"
        await pilot.press("enter")
        await pilot.pause()
        note_file = sub_dir / "note.txt"
        assert note_file.is_file()
        assert app.active_file == note_file

        # Review Focus #4: Edit buffer, rename active file .txt -> .py, preserve edits & update language
        editor = app.query_one("#editor", TextArea)
        editor.text = "print(123)\n"
        await pilot.pause()
        assert app.is_dirty

        app.selected_path = note_file
        await pilot.press("f2")
        await pilot.pause()
        assert isinstance(app.screen, TextInputModal)
        app.screen.query_one("#modal-input", Input).value = "script.py"
        await pilot.press("enter")
        await pilot.pause()

        renamed = sub_dir / "script.py"
        assert renamed.is_file()
        assert app.active_file == renamed
        assert editor.text == "print(123)\n"
        assert editor.language == "python"

        # Review Focus #4: Delete active file via F4 + confirm -> resets safely
        app.selected_path = renamed
        await pilot.press("f4")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        await pilot.click("#confirm-btn")
        await pilot.pause()
        assert not renamed.exists()
        assert app.active_file is None


@pytest.mark.asyncio
async def test_open_file_open_folder_sidebar_clear_and_binary_guard(tmp_path: Path) -> None:
    ws1 = tmp_path / "ws1"
    ws2 = tmp_path / "ws2"
    ws1.mkdir()
    ws2.mkdir()
    good = ws1 / "good.py"
    good.write_text("x = 1\n", encoding="utf-8")
    blob = ws1 / "blob.bin"
    blob.write_bytes(b"\x7fELF\x00\x01\x02")
    (ws2 / "other.py").write_text("y = 2\n", encoding="utf-8")

    app = TermCodeApp(ws1)
    async with app.run_test(size=(120, 36)) as pilot:
        # Quick-pick file via Ctrl+P
        await pilot.press("ctrl+p")
        await pilot.pause()
        assert isinstance(app.screen, FilePickerModal)
        app.screen.query_one("#picker-input", Input).value = "good"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.active_file == good
        assert app.query_one("#editor", TextArea).text == "x = 1\n"

        # Review Focus #2: Binary file rejected while preserving current editor buffer
        app.load_file(blob)
        await pilot.pause()
        assert app.active_file == good
        assert app.query_one("#editor", TextArea).text == "x = 1\n"

        # Toggle sidebar via Ctrl+B
        sidebar = app.query_one("#sidebar")
        assert sidebar.display is True
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert sidebar.display is False
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert sidebar.display is True

        # Clear output via Ctrl+L
        app.append_output("some line\n")
        assert "some line" in app.output_history
        await pilot.press("ctrl+l")
        await pilot.pause()
        assert app.output_history == ""

        # Switch workspace folder via Ctrl+O
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert isinstance(app.screen, TextInputModal)
        app.screen.query_one("#modal-input", Input).value = str(ws2)
        await pilot.press("enter")
        await pilot.pause()
        assert app.workspace_root == ws2.resolve()


@pytest.mark.asyncio
async def test_unsaved_changes_guard_on_quit(tmp_path: Path) -> None:
    good = tmp_path / "good.py"
    good.write_text("x = 1\n", encoding="utf-8")
    app = TermCodeApp(tmp_path, initial_file=good)
    async with app.run_test(size=(120, 36)) as pilot:
        app.query_one("#editor", TextArea).text = "x = 2\n"
        await pilot.pause()
        assert app.is_dirty

        await pilot.press("ctrl+q")
        await pilot.pause()
        assert isinstance(app.screen, UnsavedChangesModal)
        await pilot.click("#cancel-btn")
        await pilot.pause()
        assert app.is_running

        await pilot.press("ctrl+q")
        await pilot.pause()
        assert isinstance(app.screen, UnsavedChangesModal)
        await pilot.click("#discard-btn")
        await pilot.pause()
        assert not app.is_running
