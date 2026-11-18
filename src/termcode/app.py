"""Main Textual TUI application for TermCode."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
import shutil
import subprocess
import sys

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DirectoryTree, Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from termcode.modals import (
    CommandPaletteModal,
    ConfirmModal,
    FilePickerModal,
    FolderPickerModal,
    TextInputModal,
    UnsavedChangesModal,
)
from termcode.runner import (
    AsyncRunner,
    RunnerError,
    build_run_plan,
    editor_language_for_path,
    language_label_for_path,
)
from termcode.workspace import (
    IGNORED_DIRS,
    WorkspaceError,
    create_file,
    create_folder,
    delete_path,
    list_workspace_files,
    load_recent_folders,
    read_text_file,
    record_recent_folder,
    rename_path,
    write_text_file,
)

MAX_OUTPUT_HISTORY_CHARS = 500_000
MIN_TERMINAL_HEIGHT = 4
DEFAULT_TERMINAL_HEIGHT = 10


def _copy_to_os_clipboard(text: str) -> bool:
    """Best-effort copy to the native OS clipboard if a clipboard helper is installed."""
    candidates: list[list[str]]
    if sys.platform == "darwin":
        candidates = [["pbcopy"]]
    elif sys.platform == "win32":
        candidates = [["clip"]]
    else:
        candidates = [
            ["wl-copy"],
            ["xclip", "-selection", "clipboard"],
            ["xsel", "--clipboard", "--input"],
        ]
    for cmd in candidates:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            subprocess.run(
                cmd,
                input=text.encode("utf-8"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=2,
            )
            return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


class TerminalResizeHandle(Static):
    """Draggable Terminal header bar — drag vertically with mouse to resize Terminal."""

    def __init__(self, content: str, *, id: str | None = None, markup: bool = False) -> None:
        super().__init__(content, id=id, markup=markup)
        self._dragging = False
        self._start_y = 0
        self._start_height = DEFAULT_TERMINAL_HEIGHT

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button != 1:
            return
        app = self.app
        if isinstance(app, TermCodeApp):
            self._dragging = True
            self._start_y = event.screen_y
            self._start_height = app.terminal_height
            self.capture_mouse()
            event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if not self._dragging:
            return
        app = self.app
        if isinstance(app, TermCodeApp):
            delta = self._start_y - event.screen_y
            app.set_terminal_height(self._start_height + delta)
            event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._dragging:
            self._dragging = False
            self.release_mouse()
            event.stop()


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


class CodeTextArea(TextArea):
    """TextArea with VS Code-style Ctrl+A select-all (keeping Home for line start)."""

    BINDINGS = [
        Binding("ctrl+a", "select_all", "Select All", show=False),
    ]


class FilteredDirectoryTree(DirectoryTree):
    """DirectoryTree that hides dotfiles and noisy build/cache folders by default."""

    def __init__(self, path: str | Path, *, show_hidden: bool = False, id: str | None = None) -> None:
        super().__init__(path, id=id)
        self.show_hidden = show_hidden

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        if self.show_hidden:
            return paths
        return [
            p
            for p in paths
            if not p.name.startswith(".") and p.name not in IGNORED_DIRS
        ]


class TermCodeApp(App[None]):
    """Lightweight VS Code-style terminal editor and runner for Python, C, and C++."""

    CSS_PATH = "styles.tcss"
    TITLE = "TermCode"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("ctrl+k", "open_palette", "/Menu", priority=True),
        Binding("ctrl+s", "save_file", "Save", priority=True),
        Binding("f5", "run_file", "Run", priority=True),
        Binding("f7", "stop_run", "Stop", priority=True),
        Binding("ctrl+n", "new_file", "New", priority=True),
        Binding("f3", "new_folder", "NewDir", show=False, priority=True),
        Binding("ctrl+p", "open_file", "Pick", priority=True),
        Binding("ctrl+o", "open_folder", "Folder", priority=True),
        Binding("f2", "rename_item", "Rename", priority=True),
        Binding("f4", "delete_item", "Delete", show=False, priority=True),
        Binding("f8", "save_file_as", "SaveAs", show=False, priority=True),
        Binding("f9", "toggle_hidden", "Hidden", show=False, priority=True),
        Binding("ctrl+up", "terminal_grow", "GrowTerm", show=False, priority=True),
        Binding("ctrl+down", "terminal_shrink", "ShrinkTerm", show=False, priority=True),
        Binding("f10", "copy_output", "CopyOut", show=False, priority=True),
        Binding("ctrl+b", "toggle_sidebar", "Files", priority=True),
        Binding("ctrl+j", "toggle_terminal", "Terminal", priority=True),
        Binding("ctrl+l", "clear_output", "Clear", show=False, priority=True),
        Binding("ctrl+q", "request_quit", "Quit", priority=True),
    ]

    def __init__(
        self,
        workspace_root: Path | None = None,
        initial_file: Path | None = None,
        *,
        recent_store: Path | None = None,
    ) -> None:
        super().__init__()
        self.workspace_root: Path | None = workspace_root.resolve() if workspace_root else None
        self.active_file: Path | None = initial_file.resolve() if initial_file else None
        self.selected_path: Path | None = self.active_file or self.workspace_root
        self.is_dirty: bool = False
        self._saved_content: str = ""
        self._suppress_dirty: bool = False
        self.runner: AsyncRunner = AsyncRunner()
        self.output_history: str = ""
        self.terminal_height: int = DEFAULT_TERMINAL_HEIGHT
        self._recent_store: Path | None = recent_store
        self._recent_folders: list[Path] = []

    def compose(self) -> ComposeResult:
        initial_tree_root = self.workspace_root or Path.home()
        with Horizontal(id="workspace-body"):
            with Vertical(id="sidebar"):
                yield Static("EXPLORER", id="sidebar-title", markup=False)
                yield Static("", id="sidebar-folder", markup=False)
                with Vertical(id="no-folder-box"):
                    yield Static(
                        "You have not yet opened a folder.",
                        id="no-folder-text",
                        markup=False,
                    )
                    yield Button("Open Folder", id="sidebar-open-folder-btn")
                    yield Static("Press Ctrl+O anytime", id="no-folder-hint", markup=False)
                yield FilteredDirectoryTree(str(initial_tree_root), id="file-tree")

            with Vertical(id="main-pane"):
                with Horizontal(id="tab-bar"):
                    yield Static("Home", id="active-tab", markup=False)
                    yield Static("", id="tab-meta", markup=False)

                with Vertical(id="welcome-view"):
                    with Vertical(id="welcome-card"):
                        yield Static("TermCode", id="welcome-title", markup=False)
                        yield Static(
                            "Select a folder to open your workspace",
                            id="welcome-subtitle",
                            markup=False,
                        )
                        yield Button(
                            "Open Folder   (Ctrl+O)",
                            id="home-open-folder-btn",
                        )
                        with Vertical(id="home-recent-section"):
                            yield Static("Recent Folders", classes="welcome-section-title", markup=False)
                            yield OptionList(id="recent-list")
                            yield Static(
                                "No recent folders yet",
                                id="recent-empty",
                                markup=False,
                            )
                        yield Static(
                            "Tip: run 'termcode' inside any project folder to open it directly.",
                            id="welcome-tip",
                            markup=False,
                        )

                yield CodeTextArea.code_editor(
                    "",
                    language=None,
                    theme="vscode_dark",
                    show_line_numbers=True,
                    id="editor",
                )

                with Vertical(id="output-pane"):
                    with Horizontal(id="output-header-bar"):
                        yield TerminalResizeHandle(
                            "TERMINAL  ·  Idle  (drag to resize)",
                            id="output-header",
                            markup=False,
                        )
                        yield Button("▲", id="term-grow-btn", classes="term-tool-btn")
                        yield Button("▼", id="term-shrink-btn", classes="term-tool-btn")
                        yield Button("Copy", id="term-copy-btn", classes="term-tool-btn")
                        yield Button("Clear", id="term-clear-btn", classes="term-tool-btn")
                    yield CodeTextArea(
                        "",
                        read_only=True,
                        show_line_numbers=False,
                        soft_wrap=True,
                        theme="vscode_dark",
                        id="output-log",
                    )
                    yield Input(
                        placeholder="> Type input and press Enter...",
                        disabled=True,
                        id="stdin-input",
                    )

        with Horizontal(id="status-bar"):
            yield Static("", id="status-left", markup=False)
            yield Static("", id="status-right", markup=False)

    def on_mount(self) -> None:
        self.query_one("#stdin-input", Input).display = False
        self._refresh_recent_list()

        if self.workspace_root is not None:
            record_recent_folder(self.workspace_root, store_path=self._recent_store)
            self._sync_workspace_ui(show_welcome=False)
            if self.active_file is not None:
                if not self.load_file(self.active_file):
                    self.active_file = None
                    self.selected_path = self.workspace_root
                    self._update_chrome()
            else:
                self._update_chrome()
                self.query_one("#editor", TextArea).focus()
        else:
            self._sync_workspace_ui(show_welcome=True)
            self._update_chrome()
            self.query_one("#home-open-folder-btn", Button).focus()

    def _on_main_screen(self) -> bool:
        return len(self.screen_stack) == 1

    def _sync_workspace_ui(self, *, show_welcome: bool) -> None:
        has_folder = self.workspace_root is not None
        self.query_one("#no-folder-box").display = not has_folder
        self.query_one("#file-tree").display = has_folder
        folder_label = self.query_one("#sidebar-folder", Static)
        folder_label.display = has_folder
        if self.workspace_root is not None:
            folder_label.update(self.workspace_root.name.upper())

        self.query_one("#welcome-view").display = show_welcome
        self.query_one("#editor").display = not show_welcome
        self.query_one("#output-pane").display = not show_welcome

    def _refresh_recent_list(self) -> None:
        self._recent_folders = load_recent_folders(store_path=self._recent_store)
        recent_list = self.query_one("#recent-list", OptionList)
        empty_label = self.query_one("#recent-empty", Static)
        recent_list.clear_options()
        if not self._recent_folders:
            recent_list.display = False
            empty_label.display = True
            return
        recent_list.display = True
        empty_label.display = False
        home = Path.home()
        for idx, folder in enumerate(self._recent_folders):
            try:
                display_path = "~/" + folder.relative_to(home).as_posix()
            except ValueError:
                display_path = str(folder)
            recent_list.add_option(Option(f"{folder.name}  ({display_path})", id=str(idx)))

    def _apply_editor_language(self, path: Path | None) -> None:
        editor = self.query_one("#editor", TextArea)
        wanted = editor_language_for_path(path)
        if wanted and wanted in editor.available_languages:
            editor.language = wanted
        else:
            editor.language = None

    def _update_chrome(self) -> None:
        tab = self.query_one("#active-tab", Static)
        meta = self.query_one("#tab-meta", Static)
        status_left = self.query_one("#status-left", Static)
        status_right = self.query_one("#status-right", Static)
        editor = self.query_one("#editor", TextArea)
        on_welcome = self.query_one("#welcome-view").display

        if on_welcome:
            tab.update("Home")
            meta.update("No Folder Opened" if self.workspace_root is None else str(self.workspace_root))
        else:
            if self.active_file is not None:
                title = self.active_file.name
                if self.workspace_root is not None:
                    try:
                        rel = self.active_file.relative_to(self.workspace_root).as_posix()
                    except ValueError:
                        rel = str(self.active_file)
                else:
                    rel = str(self.active_file)
            else:
                title = "Untitled"
                rel = str(self.workspace_root) if self.workspace_root else "Not saved"
            dirty_dot = " ●" if self.is_dirty else ""
            tab.update(f"{title}{dirty_dot}")
            meta.update(rel)

        folder_part = self.workspace_root.name if self.workspace_root else "No Folder"
        status_left.update(
            f"{folder_part}  ·  ^K Menu  ·  F5 Run  ·  ^S Save  ·  ^O Folder  ·  ^J Terminal"
        )

        row, col = editor.cursor_location
        lang_label = language_label_for_path(self.active_file) if not on_welcome else "Welcome"
        if on_welcome:
            status_right.update("TermCode")
        else:
            status_right.update(f"Ln {row + 1}, Col {col + 1}  ·  UTF-8  ·  {lang_label}")

    def _refresh_tree(self) -> None:
        if self.workspace_root is None:
            return
        tree = self.query_one("#file-tree", FilteredDirectoryTree)
        tree.path = self.workspace_root
        tree.reload()

    def _target_dir_for_create(self) -> Path:
        if self.selected_path is not None and self.selected_path.exists():
            if self.selected_path.is_dir():
                return self.selected_path
            return self.selected_path.parent
        if self.active_file is not None and self.active_file.parent.exists():
            return self.active_file.parent
        if self.workspace_root is not None:
            return self.workspace_root
        return Path.cwd()

    def _target_path_for_edit(self) -> Path | None:
        if (
            self.selected_path is not None
            and self.selected_path.exists()
            and (
                self.workspace_root is None
                or self.selected_path.resolve() != self.workspace_root.resolve()
            )
        ):
            return self.selected_path
        if self.active_file is not None and self.active_file.exists():
            return self.active_file
        return None

    def copy_to_clipboard(self, text: str) -> None:
        super().copy_to_clipboard(text)
        _copy_to_os_clipboard(text)

    def set_terminal_height(self, height: int) -> None:
        """Resize the bottom Terminal panel within safe screen bounds."""
        max_h = max(MIN_TERMINAL_HEIGHT + 2, self.size.height - 9) if self.size.height else 30
        clamped = max(MIN_TERMINAL_HEIGHT, min(height, max_h))
        self.terminal_height = clamped
        out_pane = self.query_one("#output-pane")
        out_pane.styles.height = clamped

    def append_output(self, text: str) -> None:
        """Append text chunk to the selectable Terminal output and bounded history."""
        if not text:
            return
        clean = Text.from_ansi(text).plain
        if not clean:
            return
        self.output_history += clean
        if len(self.output_history) > MAX_OUTPUT_HISTORY_CHARS:
            self.output_history = self.output_history[-MAX_OUTPUT_HISTORY_CHARS:]
        log = self.query_one("#output-log", TextArea)
        log.load_text(self.output_history)
        log.scroll_end(animate=False)

    def load_file(self, path: Path) -> bool:
        """Load a text file into the editor, rejecting binary or oversized files."""
        resolved = path.resolve()
        try:
            content = read_text_file(resolved)
        except WorkspaceError as exc:
            self.notify(str(exc), severity="error")
            return False

        self._sync_workspace_ui(show_welcome=False)
        editor = self.query_one("#editor", TextArea)
        self._suppress_dirty = True
        try:
            editor.text = content
            self._apply_editor_language(resolved)
        finally:
            self._suppress_dirty = False

        self.active_file = resolved
        self.selected_path = resolved
        self._saved_content = content
        self.is_dirty = False
        self._update_chrome()
        editor.focus()
        return True

    def _open_workspace_folder(self, candidate: Path) -> None:
        resolved = candidate.resolve()
        if not resolved.is_dir():
            self.notify(f"Folder not found: {resolved}", severity="error")
            return

        def _do_switch() -> None:
            self.workspace_root = resolved
            self.selected_path = resolved
            self.active_file = None
            editor = self.query_one("#editor", TextArea)
            self._suppress_dirty = True
            try:
                editor.text = ""
                editor.language = None
            finally:
                self._suppress_dirty = False
            self._saved_content = ""
            self.is_dirty = False
            record_recent_folder(resolved, store_path=self._recent_store)
            self._refresh_recent_list()
            self._sync_workspace_ui(show_welcome=False)
            self._refresh_tree()
            self._update_chrome()
            editor.focus()
            self.notify(f"Opened {resolved.name}/")

        self._guard_unsaved(_do_switch)

    def _guard_unsaved(self, on_continue: Callable[[], None]) -> None:
        """Prompt to save/discard before leaving a dirty buffer, then run on_continue."""
        if not self.is_dirty:
            on_continue()
            return

        name = self.active_file.name if self.active_file else "Untitled"

        def _after_choice(choice: str | None) -> None:
            if choice == "discard":
                self.is_dirty = False
                on_continue()
            elif choice == "save":
                self._save_current(after_save=on_continue)

        self.push_screen(UnsavedChangesModal(name), _after_choice)

    def _save_current(self, *, after_save: Callable[[], None] | None = None) -> None:
        if self.active_file is None:
            self._prompt_save_as(title="Save File", after_save=after_save)
            return

        editor = self.query_one("#editor", TextArea)
        try:
            write_text_file(self.active_file, editor.text)
        except WorkspaceError as exc:
            self.notify(str(exc), severity="error")
            return

        self._saved_content = editor.text
        self.is_dirty = False
        self._update_chrome()
        self.notify(f"Saved {self.active_file.name}")
        if after_save is not None:
            after_save()

    def _prompt_save_as(
        self,
        *,
        title: str = "Save As",
        after_save: Callable[[], None] | None = None,
    ) -> None:
        target_dir = self._target_dir_for_create()
        initial = self.active_file.name if self.active_file else ""

        def _handle_name(raw_name: str | None) -> None:
            if raw_name is None:
                return
            cleaned = raw_name.strip()
            if not cleaned:
                self.notify("Filename cannot be empty.", severity="error")
                return
            dest = Path(cleaned)
            if not dest.is_absolute():
                dest = target_dir / dest
            dest = dest.resolve()

            def _commit_save() -> None:
                editor = self.query_one("#editor", TextArea)
                try:
                    write_text_file(dest, editor.text)
                except WorkspaceError as exc:
                    self.notify(str(exc), severity="error")
                    return

                self.active_file = dest
                self.selected_path = dest
                self._saved_content = editor.text
                self.is_dirty = False
                self._apply_editor_language(dest)
                self._update_chrome()
                self._refresh_tree()
                self.notify(f"Saved {dest.name}")
                if after_save is not None:
                    after_save()

            if dest.exists() and (self.active_file is None or dest != self.active_file.resolve()):
                def _on_overwrite(confirmed: bool | None) -> None:
                    if confirmed:
                        _commit_save()

                self.push_screen(
                    ConfirmModal(
                        title="Overwrite Existing File?",
                        message=f"'{dest.name}' already exists. Overwrite it?",
                        confirm_label="Overwrite",
                    ),
                    _on_overwrite,
                )
            else:
                _commit_save()

        self.push_screen(
            TextInputModal(
                title=title,
                prompt=f"Filename inside {target_dir}:",
                initial=initial,
                placeholder="main.py",
                confirm_label="Save",
            ),
            _handle_name,
        )

    # ---- Events ----

    def on_key(self, event: events.Key) -> None:
        if not self._on_main_screen():
            return
        if event.character == "/" or event.key == "slash":
            focused = self.focused
            if (isinstance(focused, TextArea) and not focused.read_only) or isinstance(
                focused, Input
            ):
                return
            event.prevent_default()
            event.stop()
            self.action_open_palette()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id in ("sidebar-open-folder-btn", "home-open-folder-btn"):
            self.action_open_folder()
        elif btn_id == "term-grow-btn":
            self.action_terminal_grow()
        elif btn_id == "term-shrink-btn":
            self.action_terminal_shrink()
        elif btn_id == "term-copy-btn":
            self.action_copy_output()
        elif btn_id == "term-clear-btn":
            self.action_clear_output()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "recent-list" and event.option_id is not None:
            idx = int(event.option_id)
            if 0 <= idx < len(self._recent_folders):
                self._open_workspace_folder(self._recent_folders[idx])

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "editor" or self._suppress_dirty:
            return
        dirty_now = event.text_area.text != self._saved_content
        if dirty_now != self.is_dirty:
            self.is_dirty = dirty_now
        self._update_chrome()

    def on_text_area_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        if event.text_area.id == "editor":
            self._update_chrome()

    def on_tree_node_highlighted(self, event: DirectoryTree.NodeHighlighted) -> None:
        data = getattr(event.node, "data", None)
        path = getattr(data, "path", None)
        if isinstance(path, Path):
            self.selected_path = path

    def on_directory_tree_directory_selected(
        self, event: DirectoryTree.DirectorySelected
    ) -> None:
        self.selected_path = event.path

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.selected_path = event.path
        target = event.path
        self._guard_unsaved(lambda: self.load_file(target))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "stdin-input":
            return
        if not self.runner.is_running:
            return
        line = event.value
        event.input.value = ""
        self.append_output(f"> {line}\n")
        await self.runner.send_input(line)

    # ---- Actions ----

    def action_open_palette(self) -> None:
        if not self._on_main_screen():
            return

        def _dispatch(action_name: str | None) -> None:
            if not action_name:
                return
            method = getattr(self, f"action_{action_name}", None)
            if callable(method):
                method()

        self.push_screen(CommandPaletteModal("/"), _dispatch)

    def action_save_file(self) -> None:
        if not self._on_main_screen():
            return
        if self.query_one("#welcome-view").display:
            self.notify("Open a folder or create a file first.")
            return
        self._save_current()

    def action_save_file_as(self) -> None:
        if not self._on_main_screen():
            return
        if self.query_one("#welcome-view").display:
            self.notify("Open a folder or create a file first.")
            return
        self._prompt_save_as()

    def action_run_file(self) -> None:
        if not self._on_main_screen():
            return
        if self.query_one("#welcome-view").display:
            self.notify("Open a file or create one (Ctrl+N) before running.")
            return
        if self.runner.is_running:
            self.notify("A program is already running. Press F7 (/stop) to stop it.", severity="warning")
            return
        if self.active_file is None or self.is_dirty:
            self._save_current(after_save=self._start_run_worker)
        else:
            self._start_run_worker()

    @work(exclusive=True)
    async def _start_run_worker(self) -> None:
        if self.active_file is None:
            return
        source = self.active_file
        ws_root = self.workspace_root or source.parent
        out_pane = self.query_one("#output-pane")
        out_pane.display = True
        header = self.query_one("#output-header", Static)
        stdin_input = self.query_one("#stdin-input", Input)

        try:
            plan_preview = build_run_plan(source, ws_root, source.parent)
        except RunnerError as exc:
            self.append_output(f"[Error] {exc}\n")
            self.notify(str(exc), severity="error")
            return

        self.append_output(f"$ {plan_preview.display_cmd}\n")
        header.update(f"TERMINAL  ·  Running {source.name}  (F7 Stop)")
        stdin_input.disabled = False
        stdin_input.display = True

        try:
            result = await self.runner.run(source, ws_root, self.append_output)
        except RunnerError as exc:
            self.append_output(f"[Error] {exc}\n")
            self.notify(str(exc), severity="error")
        else:
            if result.cancelled:
                self.append_output(f"[Stopped after {result.duration_seconds:.2f}s]\n")
            elif result.compile_failed:
                self.append_output(
                    f"[Compile failed (exit {result.exit_code}) in {result.duration_seconds:.2f}s]\n"
                )
            else:
                self.append_output(
                    f"[Exited {result.exit_code} in {result.duration_seconds:.2f}s]\n"
                )
        finally:
            header.update("TERMINAL  ·  Idle  (drag to resize)")
            stdin_input.value = ""
            stdin_input.disabled = True
            stdin_input.display = False
            if self._on_main_screen() and self.query_one("#editor").display:
                self.query_one("#editor", TextArea).focus()

    def action_stop_run(self) -> None:
        if not self._on_main_screen():
            return
        if not self.runner.is_running:
            self.notify("No program is currently running.")
            return
        self._stop_run_worker()

    @work
    async def _stop_run_worker(self) -> None:
        await self.runner.stop()

    def action_new_file(self) -> None:
        if not self._on_main_screen():
            return
        if self.workspace_root is None:
            # Open an untitled scratch editor immediately when no folder is open
            self._sync_workspace_ui(show_welcome=False)
            self._update_chrome()
            self.query_one("#editor", TextArea).focus()
            return

        base_dir = self._target_dir_for_create()

        def _create(raw_name: str | None) -> None:
            if raw_name is None:
                return
            try:
                created = create_file(base_dir, raw_name)
            except WorkspaceError as exc:
                self.notify(str(exc), severity="error")
                return
            self._refresh_tree()
            self._guard_unsaved(lambda: self.load_file(created))

        self.push_screen(
            TextInputModal(
                title="New File",
                prompt=f"Create file inside {base_dir}:",
                placeholder="e.g. main.py or utils/helper.cpp",
                confirm_label="Create",
            ),
            _create,
        )

    def action_new_folder(self) -> None:
        if not self._on_main_screen():
            return
        if self.workspace_root is None:
            self.notify("Open a folder first (Ctrl+O) before creating a subfolder.", severity="warning")
            return
        base_dir = self._target_dir_for_create()

        def _create(raw_name: str | None) -> None:
            if raw_name is None:
                return
            try:
                created = create_folder(base_dir, raw_name)
            except WorkspaceError as exc:
                self.notify(str(exc), severity="error")
                return
            self.selected_path = created
            self._refresh_tree()
            self.notify(f"Created folder {created.name}/")

        self.push_screen(
            TextInputModal(
                title="New Folder",
                prompt=f"Create folder inside {base_dir}:",
                placeholder="e.g. src or algorithms/dp",
                confirm_label="Create",
            ),
            _create,
        )

    def action_open_file(self) -> None:
        if not self._on_main_screen():
            return
        if self.workspace_root is None:
            self.notify("Open a folder first (Ctrl+O) to pick files.", severity="warning")
            return
        files = list_workspace_files(self.workspace_root)
        if not files:
            self.notify("No files in this folder yet. Press Ctrl+N to create one.")
            return

        def _picked(path: Path | None) -> None:
            if path is not None:
                self._guard_unsaved(lambda: self.load_file(path))

        self.push_screen(FilePickerModal(self.workspace_root, files), _picked)

    def action_open_folder(self) -> None:
        if not self._on_main_screen():
            return
        start = self.workspace_root or Path.home()

        def _picked(folder: Path | None) -> None:
            if folder is not None:
                self._open_workspace_folder(folder)

        self.push_screen(FolderPickerModal(start_dir=start), _picked)

    def action_rename_item(self) -> None:
        if not self._on_main_screen():
            return
        target = self._target_path_for_edit()
        if target is None:
            self.notify("Select a file or folder to rename first.", severity="warning")
            return

        def _do_rename(new_name: str | None) -> None:
            if new_name is None:
                return
            old_path = target.resolve()
            try:
                renamed = rename_path(old_path, new_name).resolve()
            except WorkspaceError as exc:
                self.notify(str(exc), severity="error")
                return

            if self.active_file is not None:
                active_res = self.active_file.resolve()
                if active_res == old_path:
                    self.active_file = renamed
                    self._apply_editor_language(renamed)
                elif _is_within(active_res, old_path):
                    rel = active_res.relative_to(old_path)
                    self.active_file = renamed / rel

            self.selected_path = renamed
            self._refresh_tree()
            self._update_chrome()
            self.notify(f"Renamed to {renamed.name}")

        self.push_screen(
            TextInputModal(
                title=f"Rename '{target.name}'",
                prompt="New name:",
                initial=target.name,
                confirm_label="Rename",
            ),
            _do_rename,
        )

    def action_delete_item(self) -> None:
        if not self._on_main_screen():
            return
        target = self._target_path_for_edit()
        if target is None:
            self.notify("Select a file or folder to delete first.", severity="warning")
            return

        kind = "folder and all its contents" if target.is_dir() else "file"

        def _do_delete(confirmed: bool | None) -> None:
            if not confirmed:
                return
            old_path = target.resolve()
            try:
                delete_path(old_path)
            except WorkspaceError as exc:
                self.notify(str(exc), severity="error")
                return

            if self.active_file is not None:
                active_res = self.active_file.resolve()
                if active_res == old_path or _is_within(active_res, old_path):
                    self.active_file = None
                    self._saved_content = ""
                    editor = self.query_one("#editor", TextArea)
                    self.is_dirty = bool(editor.text)
                    self._apply_editor_language(None)

            if self.selected_path is not None and (
                self.selected_path.resolve() == old_path
                or _is_within(self.selected_path, old_path)
            ):
                self.selected_path = self.workspace_root

            self._refresh_tree()
            self._update_chrome()
            self.notify(f"Deleted {old_path.name}")

        self.push_screen(
            ConfirmModal(
                title="Confirm Delete",
                message=f"Permanently delete {kind} '{target.name}'?",
                confirm_label="Delete",
            ),
            _do_delete,
        )

    def action_toggle_sidebar(self) -> None:
        if not self._on_main_screen():
            return
        sidebar = self.query_one("#sidebar")
        sidebar.display = not sidebar.display

    def action_toggle_terminal(self) -> None:
        if not self._on_main_screen():
            return
        out_pane = self.query_one("#output-pane")
        out_pane.display = not out_pane.display

    def action_toggle_hidden(self) -> None:
        if not self._on_main_screen():
            return
        tree = self.query_one("#file-tree", FilteredDirectoryTree)
        tree.show_hidden = not tree.show_hidden
        if self.workspace_root is not None:
            tree.reload()
        state = "shown" if tree.show_hidden else "hidden"
        self.notify(f"Hidden files {state}")

    def action_terminal_grow(self) -> None:
        if not self._on_main_screen():
            return
        out_pane = self.query_one("#output-pane")
        out_pane.display = True
        self.set_terminal_height(self.terminal_height + 3)

    def action_terminal_shrink(self) -> None:
        if not self._on_main_screen():
            return
        out_pane = self.query_one("#output-pane")
        if not out_pane.display:
            return
        self.set_terminal_height(self.terminal_height - 3)

    def action_copy_output(self) -> None:
        if not self._on_main_screen():
            return
        log = self.query_one("#output-log", TextArea)
        text = log.selected_text or self.output_history
        if not text:
            self.notify("Terminal output is empty.")
            return
        self.copy_to_clipboard(text)
        label = "selection" if log.selected_text else "terminal output"
        self.notify(f"Copied {label} to clipboard")

    def action_clear_output(self) -> None:
        if not self._on_main_screen():
            return
        self.output_history = ""
        self.query_one("#output-log", TextArea).load_text("")

    def action_request_quit(self) -> None:
        if not self._on_main_screen():
            return

        def _finish_quit() -> None:
            self._shutdown_and_exit()

        self._guard_unsaved(_finish_quit)

    @work
    async def _shutdown_and_exit(self) -> None:
        if self.runner.is_running:
            await self.runner.stop()
        self.exit()
