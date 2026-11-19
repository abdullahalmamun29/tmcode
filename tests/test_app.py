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
    FolderPickerModal,
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

    # Plain `termcode` from home directory opens the Home tab (no folder opened)
    home = Path.home().resolve()
    root_home, initial_home = parse_cli_target(None, cwd=home)
    assert root_home is None and initial_home is None
    # `termcode .` and `termcode ~` from home also show the start page
    assert parse_cli_target(".", cwd=home) == (None, None)
    assert parse_cli_target(str(home), cwd=tmp_path) == (None, None)

    # Plain `termcode` from any non-home folder opens that folder directly
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

        # Switch workspace folder via Ctrl+O (FolderPickerModal)
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert isinstance(app.screen, FolderPickerModal)
        app.screen.query_one("#folder-path-input", Input).value = str(ws2)
        await pilot.click("#open-folder-btn")
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


@pytest.mark.asyncio
async def test_cli_startup_binary_file_resets_active_file_and_cannot_clobber(
    tmp_path: Path,
) -> None:
    raw_bytes = b"\x7fELF\x00\x01\x02\x03binary"
    blob = tmp_path / "a.out"
    blob.write_bytes(raw_bytes)

    app = TermCodeApp(tmp_path, initial_file=blob)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        assert app.active_file is None

        app.query_one("#editor", TextArea).text = "new code\n"
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        # Must prompt for filename instead of silently clobbering a.out
        assert isinstance(app.screen, TextInputModal)
        assert blob.read_bytes() == raw_bytes


@pytest.mark.asyncio
async def test_save_as_prompts_before_overwriting_existing_file(tmp_path: Path) -> None:
    existing = tmp_path / "existing.py"
    existing.write_text("original = 1\n", encoding="utf-8")

    app = TermCodeApp(tmp_path)
    async with app.run_test(size=(120, 36)) as pilot:
        app.query_one("#editor", TextArea).text = "replacement = 2\n"
        await pilot.pause()

        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, TextInputModal)
        app.screen.query_one("#modal-input", Input).value = "existing.py"
        await pilot.press("enter")
        await pilot.pause()

        # Must prompt for overwrite confirmation
        assert isinstance(app.screen, ConfirmModal)
        await pilot.click("#cancel-btn")
        await pilot.pause()
        assert existing.read_text(encoding="utf-8") == "original = 1\n"

        # Now confirm overwrite
        await pilot.press("ctrl+s")
        await pilot.pause()
        app.screen.query_one("#modal-input", Input).value = "existing.py"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        await pilot.click("#confirm-btn")
        await pilot.pause()
        assert existing.read_text(encoding="utf-8") == "replacement = 2\n"
        assert app.active_file == existing.resolve()


@pytest.mark.asyncio
async def test_focus_returns_to_editor_after_interactive_run_finishes(tmp_path: Path) -> None:
    ask_py = tmp_path / "ask.py"
    ask_py.write_text('name = input("Name: ")\nprint(f"Hi {name}")\n', encoding="utf-8")

    app = TermCodeApp(tmp_path, initial_file=ask_py)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("f5")
        await _wait_until(lambda: "Name:" in app.output_history)

        stdin_input = app.query_one("#stdin-input", Input)
        stdin_input.focus()
        stdin_input.value = "Jobayer"
        await pilot.press("enter")
        await _wait_until(lambda: "Hi Jobayer" in app.output_history and not app.runner.is_running)
        await pilot.pause()

        assert app.focused is app.query_one("#editor", TextArea)


@pytest.mark.asyncio
async def test_launch_with_no_folder_shows_welcome_and_folder_picker(tmp_path: Path) -> None:
    proj = tmp_path / "my_project"
    proj.mkdir()
    (proj / ".hidden_dir").mkdir()
    (proj / "__pycache__").mkdir()
    (proj / "app.py").write_text("print('ok')\n", encoding="utf-8")

    app = TermCodeApp(workspace_root=None, recent_store=tmp_path / "recent.json")
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        assert app.workspace_root is None
        assert app.query_one("#welcome-view").display is True
        assert app.query_one("#no-folder-box").display is True
        assert app.query_one("#file-tree").display is False
        assert app.query_one("#stdin-input").display is False
        assert str(app.query_one("#active-tab").render()) == "Home"
        assert len(app.query("#start-list")) == 0

        # Clicking the Home tab's Open Folder button opens FolderPickerModal
        await pilot.click("#home-open-folder-btn")
        await pilot.pause()
        assert isinstance(app.screen, FolderPickerModal)
        app.screen.query_one("#folder-path-input", Input).value = str(proj)
        await pilot.click("#open-folder-btn")
        await pilot.pause()

        assert app.workspace_root == proj.resolve()
        assert app.query_one("#no-folder-box").display is False
        assert app.query_one("#file-tree").display is True

        # FilteredDirectoryTree hides dotfiles and __pycache__ by default
        tree = app.query_one("#file-tree")
        visible = [p.name for p in tree.filter_paths(list(proj.iterdir()))]
        assert visible == ["app.py"]

        # Toggle hidden files via F9
        await pilot.press("f9")
        await pilot.pause()
        visible_all = sorted(p.name for p in tree.filter_paths(list(proj.iterdir())))
        assert ".hidden_dir" in visible_all

        # Toggle terminal panel via Ctrl+J
        out_pane = app.query_one("#output-pane")
        before = out_pane.display
        await pilot.press("ctrl+j")
        await pilot.pause()
        assert out_pane.display is (not before)


@pytest.mark.asyncio
async def test_terminal_resize_selection_copy_and_clear(tmp_path: Path) -> None:
    app = TermCodeApp(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

        # Resize terminal taller and shorter via keyboard shortcuts and header buttons
        initial_h = app.terminal_height
        await pilot.press("ctrl+up")
        await pilot.pause()
        assert app.terminal_height > initial_h

        grown_h = app.terminal_height
        await pilot.press("ctrl+down")
        await pilot.pause()
        assert app.terminal_height < grown_h

        await pilot.click("#term-grow-btn")
        await pilot.pause()
        assert app.terminal_height > initial_h - 1

        # Output log is a read-only selectable TextArea
        app.append_output("hello world\nanswer = 42\n")
        await pilot.pause()
        out_log = app.query_one("#output-log", TextArea)
        assert out_log.read_only is True
        assert "answer = 42" in out_log.text

        # Select text inside terminal output and copy via F10 (/copy-output)
        out_log.selection = ((1, 0), (1, 11))
        assert out_log.selected_text == "answer = 42"
        await pilot.press("f10")
        await pilot.pause()
        assert app._clipboard == "answer = 42"

        # Pressing '/' while focused on read-only terminal still opens CommandPaletteModal
        out_log.focus()
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        assert isinstance(app.screen, CommandPaletteModal)
        await pilot.press("escape")
        await pilot.pause()

        # Clear terminal via header Clear button
        await pilot.click("#term-clear-btn")
        await pilot.pause()
        assert out_log.text == ""
        assert app.output_history == ""


@pytest.mark.asyncio
async def test_ctrl_a_selects_all_in_editor_and_terminal(tmp_path: Path) -> None:
    sample = tmp_path / "demo.py"
    sample.write_text("line1 = 1\nline2 = 2\n", encoding="utf-8")

    app = TermCodeApp(tmp_path, initial_file=sample)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        editor = app.query_one("#editor", TextArea)
        editor.focus()
        await pilot.pause()

        await pilot.press("ctrl+a")
        await pilot.pause()
        assert editor.selected_text == "line1 = 1\nline2 = 2\n"

        app.append_output("out1\nout2\n")
        await pilot.pause()
        out_log = app.query_one("#output-log", TextArea)
        out_log.focus()
        await pilot.pause()

        await pilot.press("ctrl+a")
        await pilot.pause()
        assert out_log.selected_text == "out1\nout2\n"


@pytest.mark.asyncio
async def test_ctrl_c_copies_selection_or_current_line_never_full_output(tmp_path: Path) -> None:
    app = TermCodeApp(tmp_path)
    async with app.run_test(size=(120, 36)) as pilot:
        app.append_output("first line\nsecond line\nthird line\n")
        await pilot.pause()
        out_log = app.query_one("#output-log", TextArea)
        out_log.focus()
        await pilot.pause()

        # Cursor on line 1 with NO selection + Ctrl+C copies ONLY that single line
        out_log.cursor_location = (1, 0)
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert app._clipboard == "second line"

        # Ctrl+A then Ctrl+C copies ALL text
        await pilot.press("ctrl+a")
        await pilot.pause()
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert app._clipboard == "first line\nsecond line\nthird line\n"


@pytest.mark.asyncio
async def test_c_highlighting_auto_indent_and_autocomplete(tmp_path: Path) -> None:
    c_file = tmp_path / "main.c"
    c_file.write_text("", encoding="utf-8")

    app = TermCodeApp(tmp_path, initial_file=c_file)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        editor = app.query_one("#editor", TextArea)
        assert editor.language == "c"

        # Auto-indent after '{' in C
        editor.focus()
        editor.insert("int main() {")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert editor.text.endswith("{\n    ")

        # Typing 'print' in .c triggers completion popup with 'printf'; Tab accepts it
        for ch in "print":
            await pilot.press(ch)
        await pilot.pause()
        popup = app.query_one("#completion-popup")
        assert popup.display is True

        await pilot.press("tab")
        await pilot.pause()
        assert "printf" in editor.text
        assert popup.display is False


@pytest.mark.asyncio
async def test_sidebar_buttons_multi_tab_and_split_editor(tmp_path: Path) -> None:
    f1 = tmp_path / "one.py"
    f2 = tmp_path / "two.c"
    f1.write_text("a = 1\n", encoding="utf-8")
    f2.write_text("int main(){return 0;}\n", encoding="utf-8")

    app = TermCodeApp(tmp_path, initial_file=f1)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()

        # Sidebar +File button opens New File modal
        await pilot.click("#sidebar-new-file-btn")
        await pilot.pause()
        assert isinstance(app.screen, TextInputModal)
        await pilot.press("escape")
        await pilot.pause()

        # Sidebar +Dir button opens New Folder modal
        await pilot.click("#sidebar-new-dir-btn")
        await pilot.pause()
        assert isinstance(app.screen, TextInputModal)
        await pilot.press("escape")
        await pilot.pause()

        # Edit tab 1, then open tab 2: both stay open with independent buffers
        editor = app.query_one("#editor", TextArea)
        editor.text = "a = 99\n"
        await pilot.pause()
        app.load_file(f2)
        await pilot.pause()
        assert len(app.left_pane.tabs) == 2
        assert app.active_file == f2.resolve()

        # Switch back to tab 1 via F11 (/next-tab): unsaved edit preserved!
        await pilot.press("f11")
        await pilot.pause()
        assert app.active_file == f1.resolve()
        assert editor.text == "a = 99\n"

        # Toggle side-by-side split via F12 (/split-editor)
        await pilot.press("f12")
        await pilot.pause()
        assert app.is_split is True
        assert app.query_one("#right-editor-col").display is True

        # Focus right editor and load f2 side-by-side
        right_ed = app.query_one("#editor-right", TextArea)
        right_ed.focus()
        await pilot.pause()
        app.load_file(f2)
        await pilot.pause()
        assert right_ed.text == "int main(){return 0;}\n"
        assert editor.text == "a = 99\n"

        # Unsplit via F12 merges tabs back safely without losing unsaved edits
        await pilot.press("f12")
        await pilot.pause()
        assert app.is_split is False
        assert app.query_one("#right-editor-col").display is False

        # Close clean tab via Ctrl+W
        app.load_file(f2)
        await pilot.pause()
        await pilot.press("ctrl+w")
        await pilot.pause()
        assert len(app.left_pane.tabs) == 1
        assert app.active_file == f1.resolve()


@pytest.mark.asyncio
async def test_find_replace_and_goto_line(tmp_path: Path) -> None:
    src = tmp_path / "f.py"
    src.write_text("total = 1\nTotal = 2\nx = total + 3\n", encoding="utf-8")
    app = TermCodeApp(tmp_path, src, recent_store=tmp_path / "recent.json")
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        editor = app.query_one("#editor", TextArea)
        bar = app.query_one("#find-bar")
        editor.cursor_location = (0, 0)

        # Ctrl+F opens the bar; typing selects the first match (case-insensitive).
        await pilot.press("ctrl+f")
        await pilot.pause()
        assert bar.display and app.focused.id == "find-input"
        for ch in "total":
            await pilot.press(ch)
        await pilot.pause()
        assert editor.selected_text == "total"
        assert editor.selection.start == (0, 0)

        # Enter / Down go forward, Up goes back, and it wraps.
        await pilot.press("enter")
        await pilot.pause()
        assert editor.selection.start == (1, 0)
        await pilot.press("down")
        await pilot.pause()
        assert editor.selection.start == (2, 4)
        await pilot.press("down")
        await pilot.pause()
        assert editor.selection.start == (0, 0)
        await pilot.press("up")
        await pilot.pause()
        assert editor.selection.start == (2, 4)

        # Match-case toggle narrows results.
        await pilot.click("#find-case")
        await pilot.pause()
        assert "of 2" in str(app.query_one("#find-count").render())

        # Esc closes and returns focus to the editor.
        app.query_one("#find-input").focus()
        await pilot.press("escape")
        await pilot.pause()
        assert not bar.display and app.focused is editor

        # Ctrl+R: replace one, then replace all (case-sensitive still on).
        editor.cursor_location = (0, 0)
        await pilot.press("ctrl+r")
        await pilot.pause()
        app.query_one("#replace-input").value = "sum"
        app.query_one("#replace-input").focus()
        await pilot.press("enter")
        await pilot.pause()
        assert editor.text.startswith("sum = 1\n")
        await pilot.click("#replace-all")
        await pilot.pause()
        assert editor.text == "sum = 1\nTotal = 2\nx = sum + 3\n"

        # Ctrl+Z undoes replace-all in one step.
        editor.focus()
        await pilot.press("ctrl+z")
        await pilot.pause()
        assert editor.text == "sum = 1\nTotal = 2\nx = total + 3\n"

        # Ctrl+G jumps to line:column, clamped to the document.
        await pilot.press("ctrl+g")
        await pilot.pause()
        app.screen.query_one("#modal-input").value = "3:5"
        await pilot.press("enter")
        await pilot.pause()
        assert editor.cursor_location == (2, 4)
        await pilot.press("ctrl+g")
        await pilot.pause()
        app.screen.query_one("#modal-input").value = "999"
        await pilot.press("enter")
        await pilot.pause()
        assert editor.cursor_location[0] == editor.document.line_count - 1


@pytest.mark.asyncio
async def test_shell_and_run_in_terminal_degrade_without_suspend(tmp_path: Path) -> None:
    # The headless test driver can't suspend, so both actions must warn, not crash.
    src = tmp_path / "f.py"
    src.write_text("print(1)\n", encoding="utf-8")
    app = TermCodeApp(tmp_path, src, recent_store=tmp_path / "recent.json")
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+t")
        await pilot.pause()
        await pilot.press("shift+f5")
        await pilot.pause()
        messages = [n.message for n in app._notifications]
        assert sum("can't hand over" in m for m in messages) == 2
        assert app.query_one("#editor").display


@pytest.mark.asyncio
async def test_format_on_save_and_before_run(tmp_path: Path) -> None:
    src = tmp_path / "messy.py"
    src.write_text("def f():\n  return 1\n", encoding="utf-8")
    app = TermCodeApp(tmp_path, src, recent_store=tmp_path / "recent.json")
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        editor = app.query_one("#editor", TextArea)

        # Ctrl+S formats, then saves; one Ctrl+Z undoes the formatting.
        editor.cursor_location = (1, 2)
        editor.insert("x = 2\n  ")
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert src.read_text(encoding="utf-8") == "def f():\n    x = 2\n    return 1\n"
        assert editor.text == "def f():\n    x = 2\n    return 1\n"
        assert editor.cursor_location[0] == 2

        # A saved-but-messy file (edited outside) is formatted, saved, then run on F5.
        src.write_text("def f():\n  return 1\nprint(f())\n", encoding="utf-8")
        app.left_pane.clear()
        app.load_file(src)
        await pilot.pause()
        await pilot.press("f5")
        await _wait_until(lambda: "Exited 0" in app.output_history and not app.runner.is_running)
        assert src.read_text(encoding="utf-8") == "def f():\n    return 1\nprint(f())\n"
        assert "1\n" in app.output_history

        # Syntax errors: saved as typed, with a warning, never blocked.
        editor.load_text("def f(:\n  pass\n")
        app.left_pane.active_buffer.content = editor.text
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert src.read_text(encoding="utf-8") == "def f(:\n  pass\n"
        assert any("without formatting" in n.message for n in app._notifications)

        # The toggle turns it off.
        app.action_toggle_format_on_save()
        editor.load_text("if True:\n  x = 1\n")
        app.left_pane.active_buffer.content = editor.text
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert src.read_text(encoding="utf-8") == "if True:\n  x = 1\n"


@pytest.mark.asyncio
async def test_panels_resize_hide_and_tab_close(tmp_path: Path) -> None:
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    c = tmp_path / "c.py"
    for f in (a, b, c):
        f.write_text("x = 1\n", encoding="utf-8")
    app = TermCodeApp(tmp_path, a, recent_store=tmp_path / "recent.json")
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        sidebar = app.query_one("#sidebar")
        out_pane = app.query_one("#output-pane")

        # Drag the sidebar edge right by 10 columns.
        # (Screen coordinates throughout: the handle moves while you drag it.)
        start_w = sidebar.size.width
        hx = app.query_one("#sidebar-handle").region.x
        await pilot.mouse_down("#sidebar-handle")
        await pilot.hover(None, offset=(hx + 10, 5))
        await pilot.mouse_up(None, offset=(hx + 10, 5))
        await pilot.pause()
        assert sidebar.size.width == start_w + 10

        # Dragging it nearly shut collapses it; the status bar brings it back.
        await pilot.mouse_down("#sidebar-handle")
        await pilot.hover(None, offset=(3, 5))
        await pilot.pause()
        assert not sidebar.display
        assert not app.query_one("#status-files-btn").has_class("-on")
        await pilot.click("#status-files-btn")
        await pilot.pause()
        assert sidebar.display and app.query_one("#status-files-btn").has_class("-on")

        # Output: × hides it, and opening another file must NOT bring it back.
        await pilot.click("#term-hide-btn")
        await pilot.pause()
        assert not out_pane.display
        app.load_file(b)
        await pilot.pause()
        assert not out_pane.display
        await pilot.click("#status-output-btn")
        await pilot.pause()
        assert out_pane.display

        # Tabs: the open file is highlighted in the tree; × closes a background tab.
        app.load_file(c)
        await pilot.pause()
        tree = app.query_one("#file-tree")
        assert tree.active_path == c.resolve()
        assert [t.title for t in app.left_pane.tabs] == ["a.py", "b.py", "c.py"]
        closes = list(app.query(".editor-tab-close"))
        await pilot.click(closes[0])  # close a.py while c.py stays active
        await pilot.pause()
        assert [t.title for t in app.left_pane.tabs] == ["b.py", "c.py"]
        assert app.active_file == c.resolve()
        active_tabs = [t for t in app.query(".editor-tab") if t.has_class("-active")]
        assert len(active_tabs) == 1

        # A dirty tab shows ● and asks before closing.
        editor = app.query_one("#editor", TextArea)
        editor.insert("y = 2\n")
        await pilot.pause()
        assert str(list(app.query(".editor-tab-close"))[-1].label) == "●"
        await pilot.click(list(app.query(".editor-tab-close"))[-1])
        await pilot.pause()
        assert isinstance(app.screen, UnsavedChangesModal)
        await pilot.click("#discard-btn")
        await pilot.pause()
        assert [t.title for t in app.left_pane.tabs] == ["b.py"]


async def test_brackets_and_quotes_auto_close_in_editor(tmp_path: Path) -> None:
    src = tmp_path / "a.py"
    src.write_text("", encoding="utf-8")
    app = TermCodeApp(tmp_path, src, recent_store=tmp_path / "recent.json")
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        editor = app.query_one("#editor", TextArea)
        editor.focus()
        await pilot.pause()

        # Type print("hi") naturally: ( and " auto-close, then ) and " step over.
        await pilot.press(*'print("hi")')
        await pilot.pause()
        assert editor.text == 'print("hi")'
        assert editor.cursor_location == (0, 11)

        # Backspace inside an empty pair removes both halves.
        await pilot.press("space", "x", "space", "=", "space", "[")
        assert editor.text == 'print("hi") x = []'
        await pilot.press("backspace")
        assert editor.text == 'print("hi") x = '

        # Enter between braces opens an indented block.
        editor.load_text("d = ")
        editor.move_cursor((0, 4))
        await pilot.press("{", "enter")
        assert editor.text == "d = {\n    \n}"
        assert editor.cursor_location == (1, 4)

        # A selection gets wrapped.
        editor.load_text("value")
        editor.select_all()
        await pilot.press("(")
        assert editor.text == "(value)"
        assert editor.selected_text == "value"

        # One undo removes an auto-inserted pair.
        editor.load_text("")
        await pilot.press("[")
        assert editor.text == "[]"
        await pilot.press("ctrl+z")
        assert editor.text == ""
