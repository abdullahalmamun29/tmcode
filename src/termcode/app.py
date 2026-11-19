"""Main Textual TUI application for TermCode."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import os
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

from termcode.buffers import EditorPaneState, OpenBuffer
from termcode.completions import (
    compute_closing_brace_dedent,
    compute_enter_indent,
    extract_prefix,
    get_completions,
)
from termcode.highlighting import register_extra_languages
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

_GTK_CLIPBOARD_SCRIPT = (
    "import sys, gi\n"
    "gi.require_version('Gtk', '3.0')\n"
    "from gi.repository import Gtk, Gdk\n"
    "text = sys.stdin.read()\n"
    "cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)\n"
    "cb.set_text(text, -1)\n"
    "cb.store()\n"
    "while Gtk.events_pending():\n"
    "    Gtk.main_iteration()\n"
)


def _copy_to_os_clipboard(text: str) -> bool:
    """Copy text to the native OS clipboard across Linux, macOS, and Windows."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False

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

    # Linux X11 fallback using system GTK3 (PyGObject) when xclip/xsel are absent.
    if sys.platform != "win32" and sys.platform != "darwin" and os.environ.get("DISPLAY"):
        sys_py = shutil.which("python3")
        if sys_py:
            try:
                subprocess.run(
                    [sys_py, "-c", _GTK_CLIPBOARD_SCRIPT],
                    input=text.encode("utf-8"),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                    timeout=2,
                )
                return True
            except (OSError, subprocess.SubprocessError):
                pass

    return False


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
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


class CodeTextArea(TextArea):
    """VS Code-style TextArea: Ctrl+A select-all, Ctrl+C selection/line copy, C/C++ colors, auto-indent, autocomplete."""

    BINDINGS = [
        Binding("ctrl+a", "select_all", "Select All", show=False),
    ]

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        register_extra_languages(self)

    def action_copy(self) -> None:
        """Copy selected text, or the current line when nothing is selected (VS Code behavior)."""
        selected = self.selected_text
        if selected:
            self.app.copy_to_clipboard(selected)
            self.app.notify(f"Copied selection ({len(selected)} chars)")
            return
        row = self.cursor_location[0]
        line = self.document.get_line(row)
        self.app.copy_to_clipboard(line)
        self.app.notify("Copied current line")

    async def _on_key(self, event: events.Key) -> None:
        if self.read_only:
            await super()._on_key(event)
            return

        app = self.app
        if isinstance(app, TermCodeApp) and app.is_completion_open_for(self):
            if event.key == "down":
                app.move_completion(1)
                event.prevent_default()
                event.stop()
                return
            if event.key == "up":
                app.move_completion(-1)
                event.prevent_default()
                event.stop()
                return
            if event.key in ("tab", "enter"):
                app.accept_completion(self)
                event.prevent_default()
                event.stop()
                return
            if event.key == "escape":
                app.hide_completions()
                event.prevent_default()
                event.stop()
                return

        if event.key == "enter":
            row, col = self.cursor_location
            line_before = self.document.get_line(row)[:col]
            indent = compute_enter_indent(line_before, self.language)
            self.insert("\n" + indent)
            event.prevent_default()
            event.stop()
            return

        if event.character == "}":
            row, col = self.cursor_location
            line_before = self.document.get_line(row)[:col]
            dedent = compute_closing_brace_dedent(line_before)
            if dedent > 0:
                self.replace("}", (row, col - dedent), (row, col))
                event.prevent_default()
                event.stop()
                return

        await super()._on_key(event)


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


class TabButton(Button):
    """Button representing an editor tab strip item."""

    def __init__(
        self,
        label: str,
        pane_id: str,
        tab_index: int,
        *,
        classes: str | None = None,
    ) -> None:
        super().__init__(label, classes=classes)
        self.pane_id = pane_id
        self.tab_index = tab_index


class TermCodeApp(App[None]):
    """Lightweight VS Code-style terminal editor and runner for all major languages."""

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
        Binding("f11", "next_tab", "NextTab", show=False, priority=True),
        Binding("ctrl+w", "close_tab", "CloseTab", show=False, priority=True),
        Binding("f12", "toggle_split", "Split", show=False, priority=True),
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
        self._initial_file: Path | None = initial_file.resolve() if initial_file else None
        self.selected_path: Path | None = self._initial_file or self.workspace_root
        self.left_pane: EditorPaneState = EditorPaneState()
        self.right_pane: EditorPaneState = EditorPaneState()
        self.active_pane_id: str = "left"
        self.is_split: bool = False
        self._suppress_dirty: bool = False
        self.runner: AsyncRunner = AsyncRunner()
        self.output_history: str = ""
        self.terminal_height: int = DEFAULT_TERMINAL_HEIGHT
        self._recent_store: Path | None = recent_store
        self._recent_folders: list[Path] = []
        self._completion_editor: CodeTextArea | None = None
        self._completion_prefix: str = ""
        self._completion_items: list[str] = []
        self._just_accepted_completion: bool = False

    @property
    def active_file(self) -> Path | None:
        buf = self._active_pane().active_buffer
        return buf.path if buf is not None else None

    @active_file.setter
    def active_file(self, value: Path | None) -> None:
        buf = self._active_pane().active_buffer
        if buf is not None:
            buf.path = value.resolve() if value is not None else None

    @property
    def is_dirty(self) -> bool:
        buf = self._active_pane().active_buffer
        return buf.is_dirty if buf is not None else False

    @is_dirty.setter
    def is_dirty(self, value: bool) -> None:
        buf = self._active_pane().active_buffer
        if buf is not None and not value:
            buf.saved_content = buf.content

    def _pane_for_id(self, pane_id: str) -> EditorPaneState:
        return self.right_pane if pane_id == "right" else self.left_pane

    def _active_pane(self) -> EditorPaneState:
        return self._pane_for_id(self.active_pane_id)

    def _editor_for_pane(self, pane_id: str) -> CodeTextArea:
        widget_id = "#editor-right" if pane_id == "right" else "#editor"
        return self.query_one(widget_id, CodeTextArea)

    def _active_editor(self) -> CodeTextArea:
        return self._editor_for_pane(self.active_pane_id)

    def compose(self) -> ComposeResult:
        initial_tree_root = self.workspace_root or Path.home()
        with Horizontal(id="workspace-body"):
            with Vertical(id="sidebar"):
                yield Static("EXPLORER", id="sidebar-title", markup=False)
                with Horizontal(id="sidebar-folder-bar"):
                    yield Static("", id="sidebar-folder", markup=False)
                    yield Button("+F", id="sidebar-new-file-btn", classes="sidebar-tool-btn")
                    yield Button("+D", id="sidebar-new-dir-btn", classes="sidebar-tool-btn")
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
                with Horizontal(id="editors-row"):
                    with Vertical(id="left-editor-col"):
                        with Horizontal(id="tab-bar"):
                            with Horizontal(id="left-tabs"):
                                yield Static("Home", id="active-tab", markup=False)
                            yield Static("", id="tab-meta", markup=False)
                            yield Button("Split", id="split-toggle-btn", classes="term-tool-btn")

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
                                    yield Static(
                                        "Recent Folders",
                                        classes="welcome-section-title",
                                        markup=False,
                                    )
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

                    with Vertical(id="right-editor-col"):
                        with Horizontal(id="right-tab-bar"):
                            with Horizontal(id="right-tabs"):
                                pass
                            yield Button("×", id="close-split-btn", classes="term-tool-btn")
                        yield CodeTextArea.code_editor(
                            "",
                            language=None,
                            theme="vscode_dark",
                            show_line_numbers=True,
                            id="editor-right",
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

        yield OptionList(id="completion-popup")
        with Horizontal(id="status-bar"):
            yield Static("", id="status-left", markup=False)
            yield Static("", id="status-right", markup=False)

    def on_mount(self) -> None:
        self.query_one("#stdin-input", Input).display = False
        self.query_one("#right-editor-col").display = False
        self.query_one("#completion-popup").display = False
        self._refresh_recent_list()

        if self.workspace_root is not None:
            record_recent_folder(self.workspace_root, store_path=self._recent_store)
            self._sync_workspace_ui(show_welcome=False)
            if self._initial_file is not None:
                if not self.load_file(self._initial_file):
                    self.left_pane.ensure_untitled()
                    self.selected_path = self.workspace_root
                    self._render_pane("left")
                    self._update_chrome()
            else:
                self.left_pane.ensure_untitled()
                self._render_pane("left")
                self._update_chrome()
                self.query_one("#editor", CodeTextArea).focus()
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
        folder_bar = self.query_one("#sidebar-folder-bar")
        folder_bar.display = has_folder
        if self.workspace_root is not None:
            self.query_one("#sidebar-folder", Static).update(self.workspace_root.name.upper())

        self.query_one("#welcome-view").display = show_welcome
        self.query_one("#editor").display = not show_welcome
        self.query_one("#output-pane").display = not show_welcome
        self.query_one("#split-toggle-btn").display = not show_welcome
        if show_welcome and self.is_split:
            self.is_split = False
            self.query_one("#right-editor-col").display = False

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

    def _apply_editor_language(self, editor: CodeTextArea, path: Path | None) -> None:
        wanted = editor_language_for_path(path)
        if wanted and wanted in editor.available_languages:
            editor.language = wanted
        else:
            editor.language = None

    def _sync_buffer_from_editor(self, pane_id: str) -> None:
        pane = self._pane_for_id(pane_id)
        buf = pane.active_buffer
        if buf is None:
            return
        editor = self._editor_for_pane(pane_id)
        buf.content = editor.text
        buf.cursor = editor.cursor_location

    def _rebuild_tab_strip(self, pane_id: str) -> None:
        container_id = "#right-tabs" if pane_id == "right" else "#left-tabs"
        try:
            container = self.query_one(container_id, Horizontal)
        except Exception:
            return

        pane = self._pane_for_id(pane_id)
        on_welcome = pane_id == "left" and self.query_one("#welcome-view").display

        if pane_id == "left":
            try:
                home_tab = self.query_one("#active-tab", Static)
                home_tab.display = on_welcome
                if on_welcome:
                    home_tab.update("Home")
            except Exception:
                pass

        if on_welcome:
            for child in list(container.children):
                if child.id != "active-tab":
                    child.remove()
            return

        if pane_id == "right":
            try:
                self.query_one("#right-active-tab", Static).display = False
            except Exception:
                pass

        existing_buttons = [
            c
            for c in container.children
            if isinstance(c, TabButton)
        ]
        num_tabs = len(pane.tabs)
        num_existing = len(existing_buttons)

        # Update matching buttons in place
        for idx in range(min(num_tabs, num_existing)):
            buf = pane.tabs[idx]
            dot = " ●" if buf.is_dirty else ""
            btn = existing_buttons[idx]
            btn.label = f"{buf.title}{dot}"
            btn.pane_id = pane_id
            btn.tab_index = idx
            btn.set_class(idx == pane.active_index, "-active")

        # Mount extra buttons if more tabs than existing buttons
        if num_tabs > num_existing:
            for idx in range(num_existing, num_tabs):
                buf = pane.tabs[idx]
                dot = " ●" if buf.is_dirty else ""
                classes = "editor-tab-btn -active" if idx == pane.active_index else "editor-tab-btn"
                btn = TabButton(f"{buf.title}{dot}", pane_id, idx, classes=classes)
                container.mount(btn)

        # Remove extra buttons if fewer tabs than existing buttons
        elif num_existing > num_tabs:
            for idx in range(num_tabs, num_existing):
                existing_buttons[idx].remove()

    def _render_pane(self, pane_id: str) -> None:
        pane = self._pane_for_id(pane_id)
        editor = self._editor_for_pane(pane_id)
        buf = pane.active_buffer

        self._suppress_dirty = True
        try:
            if buf is None:
                editor.text = ""
                editor.language = None
            else:
                editor.text = buf.content
                self._apply_editor_language(editor, buf.path)
                try:
                    editor.cursor_location = buf.cursor
                except Exception:
                    pass
        finally:
            self._suppress_dirty = False

        self._rebuild_tab_strip(pane_id)

    def _update_chrome(self) -> None:
        meta = self.query_one("#tab-meta", Static)
        status_left = self.query_one("#status-left", Static)
        status_right = self.query_one("#status-right", Static)
        on_welcome = self.query_one("#welcome-view").display

        if on_welcome:
            self._rebuild_tab_strip("left")
            meta.update("No Folder Opened" if self.workspace_root is None else str(self.workspace_root))
        else:
            self._rebuild_tab_strip("left")
            if self.is_split:
                self._rebuild_tab_strip("right")
            active_path = self.active_file
            if active_path is not None:
                if self.workspace_root is not None:
                    try:
                        rel = active_path.relative_to(self.workspace_root).as_posix()
                    except ValueError:
                        rel = str(active_path)
                else:
                    rel = str(active_path)
            else:
                rel = str(self.workspace_root) if self.workspace_root else "Not saved"
            meta.update(rel)

        folder_part = self.workspace_root.name if self.workspace_root else "No Folder"
        status_left.update(
            f"{folder_part}  ·  ^K Menu  ·  F5 Run  ·  ^S Save  ·  F12 Split  ·  ^J Terminal"
        )

        if on_welcome:
            status_right.update("TermCode")
        else:
            editor = self._active_editor()
            row, col = editor.cursor_location
            lang_label = language_label_for_path(self.active_file)
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

    # ---- Clipboard & Terminal ----

    def copy_to_clipboard(self, text: str) -> None:
        super().copy_to_clipboard(text)
        _copy_to_os_clipboard(text)

    def set_terminal_height(self, height: int) -> None:
        """Resize the bottom Terminal panel within safe screen bounds."""
        max_h = max(MIN_TERMINAL_HEIGHT + 2, self.size.height - 9) if self.size.height else 30
        clamped = max(MIN_TERMINAL_HEIGHT, min(height, max_h))
        self.terminal_height = clamped
        self.query_one("#output-pane").styles.height = clamped

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
        log = self.query_one("#output-log", CodeTextArea)
        log.load_text(self.output_history)
        log.scroll_end(animate=False)

    # ---- Inline Autocomplete Popup ----

    def is_completion_open_for(self, editor: CodeTextArea) -> bool:
        popup = self.query_one("#completion-popup", OptionList)
        return bool(popup.display and self._completion_editor is editor and self._completion_items)

    def hide_completions(self) -> None:
        popup = self.query_one("#completion-popup", OptionList)
        popup.display = False
        self._completion_editor = None
        self._completion_prefix = ""
        self._completion_items = []

    def _update_completions(self, editor: CodeTextArea) -> None:
        if editor.read_only or not self._on_main_screen():
            self.hide_completions()
            return
        row, col = editor.cursor_location
        line = editor.document.get_line(row)
        prefix = extract_prefix(line, col)
        items = get_completions(prefix, editor.language, editor.text, limit=7)
        if not items:
            self.hide_completions()
            return

        popup = self.query_one("#completion-popup", OptionList)
        popup.clear_options()
        for word in items:
            popup.add_option(Option(word, id=word))
        popup.highlighted = 0

        cx, cy = editor.cursor_screen_offset
        popup_h = min(len(items) + 2, 9)
        max_x = max(0, self.size.width - 34)
        max_y = max(0, self.size.height - popup_h - 2)
        pos_x = max(0, min(cx, max_x))
        pos_y = cy + 1 if (cy + 1) <= max_y else max(0, cy - popup_h)
        popup.styles.offset = (pos_x, pos_y)
        popup.display = True

        self._completion_editor = editor
        self._completion_prefix = prefix
        self._completion_items = items

    def move_completion(self, delta: int) -> None:
        popup = self.query_one("#completion-popup", OptionList)
        if not self._completion_items:
            return
        cur = popup.highlighted if popup.highlighted is not None else 0
        popup.highlighted = (cur + delta) % len(self._completion_items)

    def accept_completion(self, editor: CodeTextArea | None = None) -> None:
        target = editor or self._completion_editor
        if target is None or not self._completion_items:
            self.hide_completions()
            return
        popup = self.query_one("#completion-popup", OptionList)
        idx = popup.highlighted if popup.highlighted is not None else 0
        if not (0 <= idx < len(self._completion_items)):
            self.hide_completions()
            return
        word = self._completion_items[idx]
        prefix = self._completion_prefix
        row, col = target.cursor_location
        start_col = max(0, col - len(prefix))
        self.hide_completions()
        self._just_accepted_completion = True
        target.replace(word, (row, start_col), (row, col))
        target.focus()

    # ---- File & Buffer Operations ----

    def load_file(self, path: Path, *, pane_id: str | None = None) -> bool:
        """Open a text file as a tab in the target (or active) pane."""
        resolved = path.resolve()
        target_pane_id = pane_id or self.active_pane_id
        pane = self._pane_for_id(target_pane_id)

        # Save edits of current tab in this pane before switching
        self._sync_buffer_from_editor(target_pane_id)

        # If already open in this pane, just focus its existing tab (preserving edits)
        existing_idx = pane.index_of_path(resolved)
        if existing_idx is not None:
            self._sync_workspace_ui(show_welcome=False)
            pane.select_tab(existing_idx)
            self.active_pane_id = target_pane_id
            self.selected_path = resolved
            self._render_pane(target_pane_id)
            self._update_chrome()
            self._editor_for_pane(target_pane_id).focus()
            return True

        try:
            content = read_text_file(resolved)
        except WorkspaceError as exc:
            self.notify(str(exc), severity="error")
            return False

        self._sync_workspace_ui(show_welcome=False)
        pane.open_file(resolved, content)
        self.active_pane_id = target_pane_id
        self.selected_path = resolved
        self._render_pane(target_pane_id)
        self._update_chrome()
        self._editor_for_pane(target_pane_id).focus()
        return True

    def _open_workspace_folder(self, candidate: Path) -> None:
        resolved = candidate.resolve()
        if not resolved.is_dir():
            self.notify(f"Folder not found: {resolved}", severity="error")
            return

        def _do_switch() -> None:
            self.workspace_root = resolved
            self.selected_path = resolved
            self.left_pane.clear()
            self.right_pane.clear()
            self.is_split = False
            self.active_pane_id = "left"
            self.query_one("#right-editor-col").display = False
            self.left_pane.ensure_untitled()
            record_recent_folder(resolved, store_path=self._recent_store)
            self._refresh_recent_list()
            self._sync_workspace_ui(show_welcome=False)
            self._render_pane("left")
            self._refresh_tree()
            self._update_chrome()
            self.query_one("#editor", CodeTextArea).focus()
            self.notify(f"Opened {resolved.name}/")

        self._guard_all_unsaved(_do_switch)

    def _guard_unsaved(self, on_continue: Callable[[], None], *, buf: OpenBuffer | None = None) -> None:
        """Prompt to save/discard before leaving a dirty buffer, then run on_continue."""
        self._sync_buffer_from_editor(self.active_pane_id)
        target_buf = buf if buf is not None else self._active_pane().active_buffer
        if target_buf is None or not target_buf.is_dirty:
            on_continue()
            return

        def _after_choice(choice: str | None) -> None:
            if choice == "discard":
                target_buf.saved_content = target_buf.content
                on_continue()
            elif choice == "save":
                self._save_current(after_save=on_continue)

        self.push_screen(UnsavedChangesModal(target_buf.title), _after_choice)

    def _guard_all_unsaved(self, on_continue: Callable[[], None]) -> None:
        """Prompt if any open tab across left or right pane has unsaved edits."""
        self._sync_buffer_from_editor("left")
        if self.is_split:
            self._sync_buffer_from_editor("right")
        dirty_bufs = [
            b for b in (*self.left_pane.tabs, *self.right_pane.tabs) if b.is_dirty
        ]
        if not dirty_bufs:
            on_continue()
            return

        label = dirty_bufs[0].title if len(dirty_bufs) == 1 else f"{len(dirty_bufs)} unsaved files"

        def _after_choice(choice: str | None) -> None:
            if choice == "discard":
                for b in dirty_bufs:
                    b.saved_content = b.content
                on_continue()
            elif choice == "save":
                # Save all named dirty buffers; prompt if active buffer is untitled
                for b in dirty_bufs:
                    if b.path is not None:
                        try:
                            write_text_file(b.path, b.content)
                            b.mark_saved()
                        except WorkspaceError as exc:
                            self.notify(str(exc), severity="error")
                            return
                remaining_untitled = [b for b in dirty_bufs if b.is_dirty]
                if remaining_untitled:
                    self._prompt_save_as(title="Save File", after_save=on_continue)
                else:
                    self._update_chrome()
                    on_continue()

        self.push_screen(UnsavedChangesModal(label), _after_choice)

    def _save_current(self, *, after_save: Callable[[], None] | None = None) -> None:
        self._sync_buffer_from_editor(self.active_pane_id)
        pane = self._active_pane()
        buf = pane.ensure_untitled()
        if buf.path is None:
            self._prompt_save_as(title="Save File", after_save=after_save)
            return

        try:
            write_text_file(buf.path, buf.content)
        except WorkspaceError as exc:
            self.notify(str(exc), severity="error")
            return

        buf.mark_saved()
        self._update_chrome()
        self.notify(f"Saved {buf.path.name}")
        if after_save is not None:
            after_save()

    def _prompt_save_as(
        self,
        *,
        title: str = "Save As",
        after_save: Callable[[], None] | None = None,
    ) -> None:
        target_dir = self._target_dir_for_create()
        pane_id = self.active_pane_id
        pane = self._pane_for_id(pane_id)
        buf = pane.ensure_untitled()
        initial = buf.path.name if buf.path else ""

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
                self._sync_buffer_from_editor(pane_id)
                try:
                    write_text_file(dest, buf.content)
                except WorkspaceError as exc:
                    self.notify(str(exc), severity="error")
                    return

                buf.mark_saved(dest)
                self.selected_path = dest
                self._apply_editor_language(self._editor_for_pane(pane_id), dest)
                self._update_chrome()
                self._refresh_tree()
                self.notify(f"Saved {dest.name}")
                if after_save is not None:
                    after_save()

            if dest.exists() and (buf.path is None or dest != buf.path.resolve()):
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

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        wid = event.widget.id
        if wid == "editor":
            self.active_pane_id = "left"
            self._update_chrome()
        elif wid == "editor-right":
            self.active_pane_id = "right"
            self._update_chrome()
        elif wid != "completion-popup":
            self.hide_completions()

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
        if isinstance(event.button, TabButton):
            self._switch_to_tab(event.button.pane_id, event.button.tab_index)
            return

        btn_id = event.button.id or ""
        if btn_id in ("sidebar-open-folder-btn", "home-open-folder-btn"):
            self.action_open_folder()
        elif btn_id == "sidebar-new-file-btn":
            self.action_new_file()
        elif btn_id == "sidebar-new-dir-btn":
            self.action_new_folder()
        elif btn_id in ("split-toggle-btn", "close-split-btn"):
            self.action_toggle_split()
        elif btn_id == "term-grow-btn":
            self.action_terminal_grow()
        elif btn_id == "term-shrink-btn":
            self.action_terminal_shrink()
        elif btn_id == "term-copy-btn":
            self.action_copy_output()
        elif btn_id == "term-clear-btn":
            self.action_clear_output()
        elif btn_id.startswith("tab-left-"):
            idx = int(btn_id.removeprefix("tab-left-"))
            self._switch_to_tab("left", idx)
        elif btn_id.startswith("tab-right-"):
            idx = int(btn_id.removeprefix("tab-right-"))
            self._switch_to_tab("right", idx)

    def _switch_to_tab(self, pane_id: str, index: int) -> None:
        self.hide_completions()
        self._sync_buffer_from_editor(pane_id)
        pane = self._pane_for_id(pane_id)
        if pane.select_tab(index) is not None:
            self.active_pane_id = pane_id
            self._render_pane(pane_id)
            self._update_chrome()
            self._editor_for_pane(pane_id).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "recent-list" and event.option_id is not None:
            idx = int(event.option_id)
            if 0 <= idx < len(self._recent_folders):
                self._open_workspace_folder(self._recent_folders[idx])
        elif event.option_list.id == "completion-popup":
            self.accept_completion()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id not in ("editor", "editor-right") or self._suppress_dirty:
            return
        pane_id = "right" if event.text_area.id == "editor-right" else "left"
        pane = self._pane_for_id(pane_id)
        buf = pane.ensure_untitled()
        buf.content = event.text_area.text
        buf.cursor = event.text_area.cursor_location
        self._update_chrome()
        if isinstance(event.text_area, CodeTextArea):
            if self._just_accepted_completion:
                self._just_accepted_completion = False
            else:
                self._update_completions(event.text_area)

    def on_text_area_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        if event.text_area.id in ("editor", "editor-right"):
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
        self.load_file(event.path)

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
        self.hide_completions()

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
        self._sync_buffer_from_editor(self.active_pane_id)
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
                self._active_editor().focus()

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
        self.hide_completions()
        if self.workspace_root is None:
            self._sync_workspace_ui(show_welcome=False)
            self.left_pane.ensure_untitled()
            self._render_pane("left")
            self._update_chrome()
            self.query_one("#editor", CodeTextArea).focus()
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
            self.load_file(created)

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
                self.load_file(path)

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

            self._sync_buffer_from_editor("left")
            if self.is_split:
                self._sync_buffer_from_editor("right")
            self.left_pane.handle_rename(old_path, renamed)
            self.right_pane.handle_rename(old_path, renamed)

            if self.left_pane.active_buffer is not None:
                self._apply_editor_language(
                    self._editor_for_pane("left"), self.left_pane.active_buffer.path
                )
            if self.is_split and self.right_pane.active_buffer is not None:
                self._apply_editor_language(
                    self._editor_for_pane("right"), self.right_pane.active_buffer.path
                )

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

            self._sync_buffer_from_editor("left")
            if self.is_split:
                self._sync_buffer_from_editor("right")
            self.left_pane.handle_delete(old_path)
            self.right_pane.handle_delete(old_path)

            if self.left_pane.active_buffer is not None:
                self._apply_editor_language(
                    self._editor_for_pane("left"), self.left_pane.active_buffer.path
                )
            if self.is_split and self.right_pane.active_buffer is not None:
                self._apply_editor_language(
                    self._editor_for_pane("right"), self.right_pane.active_buffer.path
                )

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

    def action_next_tab(self) -> None:
        if not self._on_main_screen() or self.query_one("#welcome-view").display:
            return
        pane_id = self.active_pane_id
        pane = self._pane_for_id(pane_id)
        if len(pane.tabs) < 2:
            return
        self.hide_completions()
        self._sync_buffer_from_editor(pane_id)
        pane.next_tab()
        self._render_pane(pane_id)
        self._update_chrome()
        self._editor_for_pane(pane_id).focus()

    def action_close_tab(self) -> None:
        if not self._on_main_screen() or self.query_one("#welcome-view").display:
            return
        pane_id = self.active_pane_id
        pane = self._pane_for_id(pane_id)
        buf = pane.active_buffer
        if buf is None:
            return

        def _do_close() -> None:
            pane.close_tab(pane.active_index)
            if pane_id == "right" and not pane.tabs:
                self.is_split = False
                self.query_one("#right-editor-col").display = False
                self.active_pane_id = "left"
                self._update_chrome()
                self._editor_for_pane("left").focus()
                return
            if not pane.tabs:
                pane.ensure_untitled()
            self._render_pane(pane_id)
            self._update_chrome()
            self._editor_for_pane(pane_id).focus()

        self._guard_unsaved(_do_close, buf=buf)

    def action_toggle_split(self) -> None:
        if not self._on_main_screen() or self.query_one("#welcome-view").display:
            return
        self.hide_completions()
        right_col = self.query_one("#right-editor-col")
        if not self.is_split:
            self._sync_buffer_from_editor("left")
            self.is_split = True
            right_col.display = True
            if not self.right_pane.tabs:
                # Open a fresh untitled tab (or copy active file reference if 1 tab)
                if len(self.left_pane.tabs) >= 2:
                    # Move the inactive neighbor tab to the right pane for instant side-by-side
                    other_idx = (self.left_pane.active_index + 1) % len(self.left_pane.tabs)
                    moved = self.left_pane.close_tab(other_idx)
                    if moved is not None:
                        self.right_pane.tabs.append(moved)
                        self.right_pane.active_index = 0
                else:
                    self.right_pane.ensure_untitled()
            self._render_pane("left")
            self._render_pane("right")
            self.active_pane_id = "right"
            self._update_chrome()
            self._editor_for_pane("right").focus()
        else:
            self._sync_buffer_from_editor("left")
            self._sync_buffer_from_editor("right")
            self.left_pane.merge_from(self.right_pane)
            self.is_split = False
            right_col.display = False
            self.active_pane_id = "left"
            self._render_pane("left")
            self._update_chrome()
            self._editor_for_pane("left").focus()

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
        log = self.query_one("#output-log", CodeTextArea)
        if log.selected_text:
            self.copy_to_clipboard(log.selected_text)
            self.notify(f"Copied selection ({len(log.selected_text)} chars)")
            return
        if not self.output_history:
            self.notify("Terminal output is empty.")
            return
        self.copy_to_clipboard(self.output_history)
        self.notify("Copied all terminal output")

    def action_clear_output(self) -> None:
        if not self._on_main_screen():
            return
        self.output_history = ""
        self.query_one("#output-log", CodeTextArea).load_text("")

    def action_request_quit(self) -> None:
        if not self._on_main_screen():
            return

        def _finish_quit() -> None:
            self._shutdown_and_exit()

        self._guard_all_unsaved(_finish_quit)

    @work
    async def _shutdown_and_exit(self) -> None:
        if self.runner.is_running:
            await self.runner.stop()
        self.exit()
