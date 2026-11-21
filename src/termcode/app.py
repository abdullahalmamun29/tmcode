"""Main Textual TUI application for TermCode."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widgets.text_area import Selection
from textual.widgets import Button, DirectoryTree, Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from termcode.buffers import EditorPaneState, OpenBuffer
from termcode.completions import (
    compute_closing_brace_dedent,
    compute_enter_indent,
    extract_prefix,
    get_completions,
)
from termcode import __version__, theme
from termcode.findbar import FindBar
from termcode.formatting import format_code
from termcode.highlighting import pygments_highlights, pygments_lexer_for, register_extra_languages
from termcode.pairs import CLOSERS, PAIRS, plan_backspace, plan_enter, plan_typed
from termcode.modals import (
    CommandPaletteModal,
    ConfirmModal,
    FilePickerModal,
    FolderPickerModal,
    TextInputModal,
    UnsavedChangesModal,
    UpdateModal,
)
from termcode.update import (
    InstallKind,
    check_for_update,
    default_state_path,
    detect_install,
    skip_version,
)
from termcode.search import parse_goto
from termcode.runner import (
    AsyncRunner,
    RunResult,
    RunnerError,
    build_run_plan,
    editor_language_for_path,
    language_label_for_path,
    run_in_foreground,
    user_shell_command,
    wait_ignoring_ctrl_c,
)
from termcode.workspace import (
    IGNORED_DIRS,
    WorkspaceError,
    create_file,
    create_folder,
    delete_path,
    current_git_branch,
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
OUTPUT_FLUSH_INTERVAL = 1 / 30
DEFAULT_SIDEBAR_WIDTH = 30
MIN_SIDEBAR_WIDTH = 16
MAX_SIDEBAR_WIDTH = 60
# Dragging a panel smaller than this collapses it (VS Code behaviour).
COLLAPSE_SIDEBAR_BELOW = 10
COLLAPSE_TERMINAL_BELOW = 3

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


class ResizeHover:
    """Mixin for drag handles: highlighted look (thick line + grip) while hovered or dragged.

    The resize mouse pointer itself comes from the `pointer:` rule in styles.tcss.
    """

    _dragging: bool

    def on_enter(self, event: events.Enter) -> None:
        self.add_class("-hover")  # type: ignore[attr-defined]
        self.refresh()  # type: ignore[attr-defined]

    def on_leave(self, event: events.Leave) -> None:
        if not self._dragging:
            self._end_hover()

    def _end_hover(self) -> None:
        self.remove_class("-hover")  # type: ignore[attr-defined]
        self.refresh()  # type: ignore[attr-defined]


class TerminalResizeHandle(ResizeHover, Static):
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
            self.add_class("-dragging")
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
            wanted = self._start_height + (self._start_y - event.screen_y)
            if wanted < COLLAPSE_TERMINAL_BELOW:
                self._dragging = False
                self.remove_class("-dragging")
                self.release_mouse()
                app.set_output_visible(False)
            else:
                app.set_terminal_height(wanted)
            event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._dragging:
            self._dragging = False
            self.remove_class("-dragging")
            self.release_mouse()
            if not self.is_mouse_over:
                self._end_hover()
            event.stop()


class OutputEdge(TerminalResizeHandle):
    """The line along the top of the Output panel: the obvious thing to grab."""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("", id=id)

    def render(self) -> Text:
        width = max(self.size.width, 1)
        if self.has_class("-hover") or self.has_class("-dragging"):
            # Thick line with a grip in the middle: reads as "drag me up/down".
            grip = " ⇕ "
            left = (width - len(grip)) // 2
            return Text("━" * left + grip + "━" * (width - left - len(grip)))
        return Text("─" * width)


class SidebarResizeHandle(ResizeHover, Static):
    """The sidebar's right edge: drag horizontally to resize, drag shut to hide."""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("", id=id)
        self._dragging = False
        self._start_x = 0
        self._start_width = DEFAULT_SIDEBAR_WIDTH

    def render(self) -> Text:
        height = max(self.size.height, 1)
        if self.has_class("-hover") or self.has_class("-dragging"):
            rows = ["┃"] * height
            rows[height // 2] = "⇔"
            return Text("\n".join(rows))
        return Text("\n".join("│" for _ in range(height)))

    def on_mouse_down(self, event: events.MouseDown) -> None:
        app = self.app
        if event.button != 1 or not isinstance(app, TermCodeApp):
            return
        self._dragging = True
        self._start_x = event.screen_x
        self._start_width = app.sidebar_width
        self.add_class("-dragging")
        self.capture_mouse()
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        app = self.app
        if not self._dragging or not isinstance(app, TermCodeApp):
            return
        wanted = self._start_width + (event.screen_x - self._start_x)
        if wanted < COLLAPSE_SIDEBAR_BELOW:
            self._stop()
            app.set_sidebar_visible(False)
        else:
            app.set_sidebar_width(wanted)
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._dragging:
            self._stop()
            event.stop()

    def _stop(self) -> None:
        self._dragging = False
        self.remove_class("-dragging")
        self.release_mouse()
        if not self.is_mouse_over:
            self._end_hover()


class CodeTextArea(TextArea):
    """VS Code-style TextArea: Ctrl+A select-all, Ctrl+C selection/line copy, C/C++ colors, auto-indent, autocomplete."""

    BINDINGS = [
        Binding("ctrl+a", "select_all", "Select All", show=False),
    ]

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        register_extra_languages(self)
        self.register_theme(theme.EDITOR_THEME)
        self.register_theme(theme.OUTPUT_THEME)
        self.theme = theme.OUTPUT_THEME_NAME if self.read_only else theme.EDITOR_THEME_NAME

    # Pygments lexer used when the file's language has no tree-sitter grammar.
    _fallback_lexer = None

    def set_fallback_lexer(self, lexer) -> None:  # type: ignore[no-untyped-def]
        if lexer is None and self._fallback_lexer is None:
            return
        self._fallback_lexer = lexer
        self._build_highlight_map()
        self.refresh()

    def _build_highlight_map(self) -> None:
        if self._fallback_lexer is None or self.language is not None:
            super()._build_highlight_map()
            return
        self._line_cache.clear()
        self._highlights.clear()
        try:
            self._highlights.update(pygments_highlights(self.text, self._fallback_lexer))
        except Exception:
            pass  # never let coloring break editing

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

        if event.key == "escape" and isinstance(app, TermCodeApp):
            find_bar = app.query_one("#find-bar", FindBar)
            if find_bar.is_open:
                find_bar.action_close()
                event.prevent_default()
                event.stop()
                return

        if self._handle_pairs(event):
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

    def _handle_pairs(self, event: events.Key) -> bool:
        """Auto-close brackets/quotes, step over closers, delete empty pairs. True if handled."""
        char = event.character or ""
        selection = self.selection
        has_selection = selection.start != selection.end

        if has_selection:
            if char not in PAIRS:
                return False
            # Wrap the selection: select `abc`, type ( → (abc), still selected.
            start, end = sorted((selection.start, selection.end))
            inner = self.selected_text
            self.replace(char + inner + PAIRS[char], start, end)
            end_col = end[1] + 1 if start[0] == end[0] else end[1]
            self.selection = Selection((start[0], start[1] + 1), (end[0], end_col))
            return True

        row, col = self.cursor_location
        line = self.document.get_line(row)

        if event.key == "backspace":
            span = plan_backspace(line, col)
            if span is None:
                return False
            self.delete((row, span[0]), (row, span[1]))
            return True

        if event.key == "enter":
            unit = "\t" if line.startswith("\t") else "    "
            planned = plan_enter(line, col, unit)
            if planned is None:
                return False
            text, offset = planned
            self.insert(text)
            self.move_cursor((row + 1, offset - 1))
            return True

        if char in PAIRS or char in CLOSERS:
            edit = plan_typed(char, line, col, self.language)
            if edit is None:
                return False
            if edit.text:
                self.replace(edit.text, (row, edit.start), (row, edit.end))
            self.move_cursor((row, edit.cursor))
            return True
        return False


class FilteredDirectoryTree(DirectoryTree):
    """DirectoryTree that hides dotfiles and noisy build/cache folders by default,
    and draws the file that is open in the editor in the accent color."""

    ICON_NODE = "▸ "
    ICON_NODE_EXPANDED = "▾ "
    ICON_FILE = "  "
    # Class-level default: labels can render during __init__, before it's set.
    active_path: Path | None = None

    def __init__(self, path: str | Path, *, show_hidden: bool = False, id: str | None = None) -> None:
        super().__init__(path, id=id)
        self.show_hidden = show_hidden
        # The folder name already heads the sidebar; don't repeat it as a tree node.
        self.show_root = False
        self.active_path: Path | None = None

    def render_label(self, node, base_style, style):  # type: ignore[no-untyped-def]
        label = super().render_label(node, base_style, style)
        path = getattr(node.data, "path", None)
        if self.active_path is not None and path == self.active_path:
            label.stylize(f"bold {theme.ACCENT}")
        return label

    def show_active(self, path: Path | None) -> None:
        """Highlight path as the open file and, when it's visible, put the cursor on it."""
        if path == self.active_path:
            return
        self.active_path = path
        self._clear_line_cache()
        self.refresh()
        if path is None or self.has_focus:
            return  # don't yank the cursor while the user is navigating the tree
        stack = [self.root]
        while stack:
            node = stack.pop()
            if getattr(node.data, "path", None) == path:
                self.move_cursor(node)
                return
            if node.is_expanded:
                stack.extend(node.children)

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        if self.show_hidden:
            return paths
        return [
            p
            for p in paths
            if not p.name.startswith(".") and p.name not in IGNORED_DIRS
        ]


class TabItem(Horizontal):
    """One editor tab: the file name plus a close button (× clean, ● unsaved)."""

    def __init__(self, title: str, pane_id: str, tab_index: int, *, dirty: bool, active: bool) -> None:
        super().__init__(classes="editor-tab")
        self._title = title
        self._dirty = dirty
        self.pane_id = pane_id
        self.tab_index = tab_index
        self.set_class(active, "-active")
        self.set_class(dirty, "-dirty")

    def compose(self) -> ComposeResult:
        yield TabButton(self._title, self.pane_id, self.tab_index, classes="editor-tab-btn")
        close = TabCloseButton("●" if self._dirty else "×", self.pane_id, self.tab_index)
        close.tooltip = "Close (Ctrl+W)"
        yield close

    def update_tab(self, title: str, pane_id: str, tab_index: int, *, dirty: bool, active: bool) -> None:
        self.pane_id, self.tab_index = pane_id, tab_index
        self.set_class(active, "-active")
        self.set_class(dirty, "-dirty")
        for child in self.children:
            if isinstance(child, (TabButton, TabCloseButton)):
                child.pane_id, child.tab_index = pane_id, tab_index
            if isinstance(child, TabCloseButton):
                child.label = "●" if dirty else "×"
            elif isinstance(child, TabButton):
                child.label = title


class TabCloseButton(Button):
    """The × on a tab."""

    def __init__(self, label: str, pane_id: str, tab_index: int) -> None:
        super().__init__(label, classes="editor-tab-close")
        self.pane_id = pane_id
        self.tab_index = tab_index


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
    TITLE = "TmCode"
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
        Binding("ctrl+f", "find", "Find", show=False, priority=True),
        Binding("ctrl+r", "replace", "Replace", show=False, priority=True),
        Binding("ctrl+g", "goto_line", "GoTo", show=False, priority=True),
        Binding("shift+f5", "run_in_terminal", "RunTerm", show=False, priority=True),
        Binding("ctrl+t", "open_shell", "Shell", show=False, priority=True),
        Binding("f6", "format_file", "Format", show=False, priority=True),
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
        self._flushed_output: str = ""
        self._pending_output: list[str] = []
        self._flush_scheduled: bool = False
        self._last_flush: float = 0.0
        self.terminal_height: int = DEFAULT_TERMINAL_HEIGHT
        self.sidebar_width: int = DEFAULT_SIDEBAR_WIDTH
        # What the user chose; the start page hides Output regardless.
        self.output_visible: bool = True
        self.format_on_save: bool = True
        self._recent_store: Path | None = recent_store
        # Set when the user picks "Update now"; cli.main runs the update after exit.
        self.update_to: str | None = None
        self.install_kind: InstallKind = detect_install()
        self._recent_folders: list[Path] = []
        self._completion_editor: CodeTextArea | None = None
        self._completion_prefix: str = ""
        self._completion_items: list[str] = []
        self._just_accepted_completion: bool = False

    @property
    def output_history(self) -> str:
        """Everything printed to the Output panel, including not-yet-rendered chunks."""
        if self._pending_output:
            return self._flushed_output + "".join(self._pending_output)
        return self._flushed_output

    @output_history.setter
    def output_history(self, value: str) -> None:
        self._pending_output.clear()
        self._flushed_output = value

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
                with Vertical(id="sidebar-folder-bar"):
                    yield Static("", id="sidebar-folder", markup=False)
                    with Horizontal(id="sidebar-actions"):
                        new_file_btn = Button("+ file", id="sidebar-new-file-btn", classes="sidebar-tool-btn")
                        new_file_btn.tooltip = "New file (Ctrl+N)"
                        yield new_file_btn
                        new_dir_btn = Button("+ dir", id="sidebar-new-dir-btn", classes="sidebar-tool-btn")
                        new_dir_btn.tooltip = "New folder (F3)"
                        yield new_dir_btn
                        yield Static("", classes="spacer")
                        hide_side_btn = Button("«", id="sidebar-hide-btn", classes="sidebar-tool-btn")
                        hide_side_btn.tooltip = "Hide sidebar (Ctrl+B)"
                        yield hide_side_btn
                with Vertical(id="no-folder-box"):
                    yield Static(
                        "No folder open.",
                        id="no-folder-text",
                        markup=False,
                    )
                    yield Button("Open folder", id="sidebar-open-folder-btn")
                    yield Static("or press Ctrl+O", id="no-folder-hint", markup=False)
                yield FilteredDirectoryTree(str(initial_tree_root), id="file-tree")
            yield SidebarResizeHandle(id="sidebar-handle")

            with Vertical(id="main-pane"):
                with Horizontal(id="editors-row"):
                    with Vertical(id="left-editor-col"):
                        with Horizontal(id="tab-bar"):
                            with Horizontal(id="left-tabs"):
                                yield Static("Home", id="active-tab", markup=False)
                            yield Static("", id="tab-meta", markup=False)
                            split_btn = Button("Split", id="split-toggle-btn", classes="term-tool-btn")
                            split_btn.tooltip = "Split editor (F12)"
                            yield split_btn

                        with Vertical(id="welcome-view"):
                            with Vertical(id="welcome-card"):
                                yield Static(
                                    Text.assemble(
                                        ("tmcode", f"bold {theme.TEXT}"),
                                        (f"  {__version__}", theme.FAINT),
                                    ),
                                    id="welcome-title",
                                )
                                yield Static(
                                    "Open a folder to start writing and running code.",
                                    id="welcome-subtitle",
                                    markup=False,
                                )
                                yield Button(
                                    "Open folder   Ctrl+O",
                                    id="home-open-folder-btn",
                                )
                                with Vertical(id="home-recent-section"):
                                    yield Static(
                                        "Recent",
                                        classes="welcome-section-title",
                                        markup=False,
                                    )
                                    yield OptionList(id="recent-list")
                                    yield Static(
                                        "Folders you open will show up here.",
                                        id="recent-empty",
                                        markup=False,
                                    )
                                yield Static(
                                    "Run tmcode inside a project folder to skip this screen.",
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

                yield FindBar(self._active_editor, id="find-bar")

                with Vertical(id="output-pane"):
                    edge = OutputEdge(id="output-edge")
                    edge.tooltip = "Drag to resize"
                    yield edge
                    with Horizontal(id="output-header-bar"):
                        handle = TerminalResizeHandle("", id="output-header")
                        handle.tooltip = "Drag to resize (or Ctrl+Up / Ctrl+Down)"
                        yield handle
                        yield Button("▴", id="term-grow-btn", classes="term-tool-btn")
                        yield Button("▾", id="term-shrink-btn", classes="term-tool-btn")
                        yield Button("Copy", id="term-copy-btn", classes="term-tool-btn")
                        yield Button("Clear", id="term-clear-btn", classes="term-tool-btn")
                        hide_out_btn = Button("×", id="term-hide-btn", classes="term-tool-btn")
                        hide_out_btn.tooltip = "Hide Output (Ctrl+J)"
                        yield hide_out_btn
                    yield CodeTextArea(
                        "",
                        read_only=True,
                        show_line_numbers=False,
                        soft_wrap=True,
                        theme="vscode_dark",
                        id="output-log",
                    )
                    yield Input(
                        placeholder="Type input for the program, then Enter",
                        disabled=True,
                        id="stdin-input",
                    )

        yield OptionList(id="completion-popup")
        with Horizontal(id="status-bar"):
            files_btn = Button("Files", id="status-files-btn", classes="status-toggle -on")
            files_btn.tooltip = "Show or hide the sidebar (Ctrl+B)"
            yield files_btn
            out_btn = Button("Output", id="status-output-btn", classes="status-toggle -on")
            out_btn.tooltip = "Show or hide Output (Ctrl+J)"
            yield out_btn
            yield Static("", id="status-left", markup=False)
            yield Static("", id="status-right", markup=False)

    def on_mount(self) -> None:
        self.query_one("#stdin-input", Input).display = False
        self.query_one("#right-editor-col").display = False
        self.query_one("#completion-popup").display = False
        self.query_one("#find-bar").display = False
        self._set_run_state("idle")
        self._refresh_recent_list()
        if not self.is_headless:
            self.run_worker(self._check_for_update, thread=True, group="update-check", exit_on_error=False)

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
            self.query_one("#sidebar-folder", Static).update(self.workspace_root.name)

        self.query_one("#welcome-view").display = show_welcome
        self.query_one("#editor").display = not show_welcome
        self.query_one("#output-pane").display = not show_welcome and self.output_visible
        self._sync_layout_toggles()
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
            row = Text(no_wrap=True, overflow="ellipsis")
            row.append(folder.name, theme.TEXT)
            row.append(f"   {display_path}", theme.FAINT)
            recent_list.add_option(Option(row, id=str(idx)))

    def _apply_editor_language(self, editor: CodeTextArea, path: Path | None) -> None:
        wanted = editor_language_for_path(path)
        if wanted and wanted in editor.available_languages:
            editor.set_fallback_lexer(None)
            editor.language = wanted
        else:
            editor.language = None
            # No tree-sitter grammar: color it with Pygments (hundreds of languages).
            lexer = pygments_lexer_for(path.name, editor.text) if path is not None else None
            editor.set_fallback_lexer(lexer)

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

        existing = [c for c in container.children if isinstance(c, TabItem)]
        for idx, buf in enumerate(pane.tabs):
            active = idx == pane.active_index
            if idx < len(existing):
                existing[idx].update_tab(buf.title, pane_id, idx, dirty=buf.is_dirty, active=active)
            else:
                container.mount(TabItem(buf.title, pane_id, idx, dirty=buf.is_dirty, active=active))
        for extra in existing[len(pane.tabs):]:
            extra.remove()

    def _render_pane(self, pane_id: str) -> None:
        pane = self._pane_for_id(pane_id)
        editor = self._editor_for_pane(pane_id)
        buf = pane.active_buffer

        self._suppress_dirty = True
        try:
            if buf is None:
                editor.set_fallback_lexer(None)
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
            # Show the folder path only when the file lives below the root;
            # otherwise it would just repeat the tab title.
            active_path = self.active_file
            hint = ""
            if active_path is not None:
                if self.workspace_root is not None:
                    try:
                        parent = active_path.parent.relative_to(self.workspace_root).as_posix()
                        hint = "" if parent == "." else f"{parent}/"
                    except ValueError:
                        hint = str(active_path.parent)
                else:
                    hint = str(active_path.parent)
            meta.update(hint)
            tree = self.query_one("#file-tree", FilteredDirectoryTree)
            tree.show_active(active_path.resolve() if active_path is not None else None)

        sep = "   "
        left = Text(no_wrap=True, overflow="ellipsis")
        if self.workspace_root is not None:
            left.append(self.workspace_root.name, theme.TEXT)
            branch = current_git_branch(self.workspace_root)
            if branch:
                left.append(f"{sep}⎇ {branch}", theme.MUTED)
        else:
            left.append("No folder", theme.MUTED)
        status_left.update(left)

        right = Text(no_wrap=True)
        if not on_welcome:
            editor = self._active_editor()
            row, col = editor.cursor_location
            lang_label = language_label_for_path(self.active_file)
            right.append(f"Ln {row + 1}, Col {col + 1}{sep}{lang_label}{sep}", theme.MUTED)
        right.append("Ctrl+K", theme.TEXT)
        right.append(" commands", theme.MUTED)
        status_right.update(right)

    def _set_run_state(self, state: str, detail: str = "") -> None:
        """Paint the Output header and its left stripe for idle/running/ok/fail."""
        glyphs = {
            "idle": ("", theme.MUTED),
            "running": ("● running", theme.BUSY),
            "ok": ("✓", theme.OK),
            "fail": ("✗", theme.FAIL),
            "stopped": ("■ stopped", theme.MUTED),
        }
        label, color = glyphs.get(state, glyphs["idle"])
        header = Text(no_wrap=True, overflow="ellipsis")
        header.append("Output", f"bold {theme.TEXT}")
        if label:
            header.append("   ")
            header.append(label, f"bold {color}")
        if detail:
            header.append(f"   {detail}", theme.MUTED)
        self.query_one("#output-header", Static).update(header)
        pane = self.query_one("#output-pane")
        for s in glyphs:
            pane.set_class(s == state, f"-{s}")

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
        """Queue a chunk for the Output panel; chunks are flushed in batches.

        Programs that print in a tight loop deliver thousands of tiny chunks.
        Re-rendering the whole log per chunk is quadratic and froze the UI, so
        chunks are buffered and appended at most ~30 times per second.
        """
        if not text:
            return
        clean = Text.from_ansi(text).plain
        if not clean:
            return
        self._pending_output.append(clean)
        if self._flush_scheduled:
            return
        wait = self._last_flush + OUTPUT_FLUSH_INTERVAL - time.monotonic()
        if wait <= 0:
            self._flush_output()  # quiet stream: show it immediately
        else:
            self._flush_scheduled = True
            self.set_timer(wait, self._scheduled_flush)

    def _scheduled_flush(self) -> None:
        self._flush_scheduled = False
        self._flush_output()

    def _flush_output(self) -> None:
        if not self._pending_output:
            return
        self._last_flush = time.monotonic()
        # Query the base screen so output keeps flowing while a modal is open.
        try:
            log = self.screen_stack[0].query_one("#output-log", CodeTextArea)
        except (NoMatches, IndexError):  # app is shutting down
            return
        chunk = "".join(self._pending_output)
        self._pending_output.clear()
        follow = log.scroll_y >= log.max_scroll_y - 1
        self._flushed_output += chunk
        if len(self._flushed_output) > MAX_OUTPUT_HISTORY_CHARS:
            # Trim well below the cap so the full reload happens rarely.
            keep = int(MAX_OUTPUT_HISTORY_CHARS * 0.8)
            self._flushed_output = self._flushed_output[-keep:]
            log.load_text(self._flushed_output)
        else:
            log.insert(chunk, log.document.end, maintain_selection_offset=False)
            log.history.clear()
        if follow:
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
                        self._format_before_save(b, b.path)
                        try:
                            write_text_file(b.path, b.content)
                            b.mark_saved()
                        except WorkspaceError as exc:
                            self.notify(str(exc), severity="error")
                            return
                remaining_untitled = [b for b in dirty_bufs if b.is_dirty]
                if remaining_untitled:
                    self._prompt_save_as(title="Save file", after_save=on_continue)
                else:
                    self._update_chrome()
                    on_continue()

        self.push_screen(UnsavedChangesModal(label), _after_choice)

    def _save_current(self, *, after_save: Callable[[], None] | None = None) -> None:
        self._sync_buffer_from_editor(self.active_pane_id)
        pane = self._active_pane()
        buf = pane.ensure_untitled()
        if buf.path is None:
            self._prompt_save_as(title="Save file", after_save=after_save)
            return

        formatted = self._format_before_save(buf, buf.path)
        try:
            write_text_file(buf.path, buf.content)
        except WorkspaceError as exc:
            self.notify(str(exc), severity="error")
            return

        buf.mark_saved()
        self._update_chrome()
        self.notify(f"Saved and formatted {buf.path.name}" if formatted else f"Saved {buf.path.name}")
        if after_save is not None:
            after_save()

    # ---- Format on save ----

    def _editor_showing(self, buf: OpenBuffer) -> CodeTextArea | None:
        for pane_id in ("left", "right"):
            if pane_id == "right" and not self.is_split:
                continue
            if self._pane_for_id(pane_id).active_buffer is buf:
                return self._editor_for_pane(pane_id)
        return None

    def _replace_editor_text(self, editor: CodeTextArea, new_text: str) -> None:
        """Swap in formatted text as one undoable edit, keeping the cursor on its code."""
        row, col = editor.cursor_location
        old_line = editor.document.get_line(row)
        old_indent = len(old_line) - len(old_line.lstrip())
        with editor.prevent(TextArea.Changed):
            editor.replace(new_text, (0, 0), editor.document.end)
        row = min(row, editor.document.line_count - 1)
        new_line = editor.document.get_line(row)
        new_indent = len(new_line) - len(new_line.lstrip())
        col = max(0, min(col + new_indent - old_indent, len(new_line)))
        editor.cursor_location = (row, col)

    def _format_buffer(self, buf: OpenBuffer, path: Path) -> tuple[bool, str | None]:
        """Format buf in place (and its editor). Returns (changed, error)."""
        result = format_code(buf.content, editor_language_for_path(path), filename=path.name)
        if result.error or not result.changed:
            return False, result.error
        editor = self._editor_showing(buf)
        if editor is not None:
            self._replace_editor_text(editor, result.text)
            buf.content = editor.text
            buf.cursor = editor.cursor_location
        else:
            buf.content = result.text
        return True, None

    def _format_before_save(self, buf: OpenBuffer, path: Path) -> bool:
        """Run before every save (Ctrl+S, F5/Shift+F5, save-as, save-all). Never blocks saving."""
        if not self.format_on_save:
            return False
        changed, error = self._format_buffer(buf, path)
        if error:
            self.notify(f"Saved without formatting. {error}", severity="warning", timeout=6)
        return changed

    def _format_clean_before_run(self) -> None:
        """Run keys format even an already-saved file; a change makes it dirty, so it gets saved."""
        buf = self._active_pane().active_buffer
        if not self.format_on_save or buf is None or buf.path is None or buf.is_dirty:
            return  # dirty or untitled files are formatted by the save that follows
        _, error = self._format_buffer(buf, buf.path)
        if error:
            self.notify(f"Running without formatting. {error}", severity="warning", timeout=6)

    def action_format_file(self) -> None:
        if not self._editor_ready():
            return
        self._sync_buffer_from_editor(self.active_pane_id)
        buf = self._active_pane().active_buffer
        if buf is None:
            return
        path = buf.path or Path("untitled.txt")
        changed, error = self._format_buffer(buf, path)
        if error:
            self.notify(f"Can't format: {error}", severity="warning", timeout=6)
        else:
            self.notify("Formatted" if changed else "Already tidy")
        self._update_chrome()

    def action_toggle_format_on_save(self) -> None:
        self.format_on_save = not self.format_on_save
        self.notify(f"Format on save is {'on' if self.format_on_save else 'off'}")

    def _prompt_save_as(
        self,
        *,
        title: str = "Save as",
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
                self._format_before_save(buf, dest)
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
                        title="Overwrite existing file?",
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
        if isinstance(event.button, TabCloseButton):
            self._close_tab_at(event.button.pane_id, event.button.tab_index)
            return
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
        elif btn_id in ("sidebar-hide-btn", "status-files-btn"):
            self.action_toggle_sidebar()
        elif btn_id in ("term-hide-btn", "status-output-btn"):
            self.action_toggle_terminal()
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
            if self.focused is not event.text_area:
                self.hide_completions()
            elif self._just_accepted_completion:
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
        self._format_clean_before_run()
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
        self.set_output_visible(True)  # a run always brings Output back
        stdin_input = self.query_one("#stdin-input", Input)

        try:
            plan_preview = build_run_plan(source, ws_root, source.parent)
        except RunnerError as exc:
            self.append_output(f"[Error] {exc}\n")
            self._set_run_state("fail", "could not start")
            self.notify(str(exc), severity="error")
            return

        self.append_output(f"$ {plan_preview.display_cmd}\n")
        self._set_run_state("running", f"{source.name}   F7 to stop")
        stdin_input.disabled = False
        stdin_input.display = True

        try:
            result = await self.runner.run(source, ws_root, self.append_output)
        except RunnerError as exc:
            self.append_output(f"[Error] {exc}\n")
            self._set_run_state("fail", "could not start")
            self.notify(str(exc), severity="error")
        else:
            secs = f"{result.duration_seconds:.2f}s"
            if result.cancelled:
                self.append_output(f"[Stopped after {secs}]\n")
                self._set_run_state("stopped", f"{source.name} after {secs}")
            elif result.compile_failed:
                self.append_output(f"[Compile failed (exit {result.exit_code}) in {secs}]\n")
                self._set_run_state("fail", f"compile failed   {source.name}")
            else:
                self.append_output(f"[Exited {result.exit_code} in {secs}]\n")
                state = "ok" if result.exit_code == 0 else "fail"
                self._set_run_state(state, f"exit {result.exit_code}   {source.name} in {secs}")
        finally:
            stdin_input.value = ""
            stdin_input.disabled = True
            stdin_input.display = False
            if self._on_main_screen() and self.query_one("#editor").display:
                self._active_editor().focus()

    # ---- Real terminal: hand the whole screen to a program or the shell ----

    @staticmethod
    def _tty_write(text: str) -> None:
        """Write straight to the real terminal (Textual captures sys.stdout)."""
        out = sys.__stdout__
        if out is not None:
            out.write(text)
            out.flush()

    @staticmethod
    def _tty_wait_for_enter() -> None:
        stdin = sys.__stdin__
        if stdin is None:
            return
        try:
            stdin.readline()
        except (KeyboardInterrupt, EOFError, OSError):
            pass

    def _suspended(self, body: Callable[[], None]) -> bool:
        """Run body with the real terminal; False if this terminal can't do that."""
        try:
            with self.suspend():
                body()
        except SuspendNotSupported:
            self.notify("This terminal can't hand over the screen.", severity="warning")
            return False
        return True

    def action_open_shell(self) -> None:
        """Ctrl+T: open the user's own shell; `exit` comes back to TmCode."""
        if not self._on_main_screen():
            return
        if self.runner.is_running:
            self.notify("Stop the running program first (F7).", severity="warning")
            return
        self.hide_completions()
        cwd = self.workspace_root or Path.home()
        cmd = user_shell_command()

        def _shell() -> None:
            self._tty_write(
                f"\x1b[2J\x1b[H\x1b[2mTmCode is paused. You're in {cwd}. "
                "Type exit to come back.\x1b[0m\n"
            )
            try:
                wait_ignoring_ctrl_c(subprocess.Popen(cmd, cwd=cwd))
            except OSError as exc:
                self._tty_write(f"Could not start {cmd[0]}: {exc}\nPress Enter to return. ")
                self._tty_wait_for_enter()

        if self._suspended(_shell):
            self._refresh_tree()  # the shell may have created or deleted files
            self._reload_clean_buffers()

    def action_run_in_terminal(self) -> None:
        """Shift+F5: save, then run the file with the full terminal (Code::Blocks style)."""
        if not self._editor_ready():
            return
        if self.runner.is_running:
            self.notify("A program is already running. Press F7 to stop it.", severity="warning")
            return
        self._sync_buffer_from_editor(self.active_pane_id)
        self._format_clean_before_run()
        if self.active_file is None or self.is_dirty:
            self._save_current(after_save=self._run_in_terminal_now)
        else:
            self._run_in_terminal_now()

    def _run_in_terminal_now(self) -> None:
        source = self.active_file
        if source is None:
            return
        ws_root = self.workspace_root or source.parent
        outcome: dict[str, object] = {}

        def _run() -> None:
            self._tty_write(f"\x1b[2J\x1b[H\x1b[2m$ running {source.name}\x1b[0m\n\n")
            try:
                outcome["result"] = run_in_foreground(source, ws_root)
            except RunnerError as exc:
                outcome["error"] = str(exc)
                self._tty_write(f"\x1b[31m{exc}\x1b[0m\n")
            result = outcome.get("result")
            if isinstance(result, RunResult):
                color = "32" if result.exit_code == 0 and not result.cancelled else "31"
                status = (
                    "stopped" if result.cancelled
                    else "failed to compile" if result.compile_failed
                    else f"exited {result.exit_code}"
                )
                summary = f"{source.name} {status} in {result.duration_seconds:.2f}s"
            else:
                color, summary = "31", f"{source.name} could not start"
            self._tty_write(
                f"\n\x1b[{color}m── {summary} ──\x1b[0m\x1b[2m press Enter to return \x1b[0m"
            )
            self._tty_wait_for_enter()

        if not self._suspended(_run):
            return
        result = outcome.get("result")
        if isinstance(result, RunResult):
            secs = f"{result.duration_seconds:.2f}s"
            if result.cancelled:
                self._set_run_state("stopped", f"{source.name} after {secs}   ran in terminal")
            elif result.compile_failed:
                self._set_run_state("fail", f"compile failed   {source.name}   ran in terminal")
            else:
                state = "ok" if result.exit_code == 0 else "fail"
                self._set_run_state(
                    state, f"exit {result.exit_code}   {source.name} in {secs}   ran in terminal"
                )
            self.append_output(f"[Ran {source.name} in terminal: exit {result.exit_code} in {secs}]\n")
        elif "error" in outcome:
            self._set_run_state("fail", "could not start")
            self.notify(str(outcome["error"]), severity="error")
        self._active_editor().focus()

    def _reload_clean_buffers(self) -> None:
        """After the shell, pick up on-disk edits to files that have no unsaved changes."""
        for pane_id in ("left", "right"):
            if pane_id == "right" and not self.is_split:
                continue
            pane = self._pane_for_id(pane_id)
            self._sync_buffer_from_editor(pane_id)
            changed = False
            for buf in pane.tabs:
                if buf.path is None or buf.is_dirty or not buf.path.is_file():
                    continue
                try:
                    disk = read_text_file(buf.path)
                except WorkspaceError:
                    continue
                if disk != buf.content:
                    buf.content = disk
                    buf.saved_content = disk
                    changed = True
            if changed:
                self._render_pane(pane_id)
        self._update_chrome()

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
                title="Delete permanently?",
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

    def _close_tab_at(self, pane_id: str, index: int) -> None:
        """Close any tab from its ×; unsaved tabs are shown first, then asked about."""
        if not self._on_main_screen():
            return
        self.hide_completions()
        pane = self._pane_for_id(pane_id)
        self._sync_buffer_from_editor(pane_id)
        if not (0 <= index < len(pane.tabs)):
            return
        if index != pane.active_index and not pane.tabs[index].is_dirty:
            pane.close_tab(index)  # background tab, nothing to lose
            self._rebuild_tab_strip(pane_id)
            self._update_chrome()
            return
        if index != pane.active_index:
            self._switch_to_tab(pane_id, index)
        self.active_pane_id = pane_id
        self.action_close_tab()

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

    def _editor_ready(self) -> bool:
        return self._on_main_screen() and not self.query_one("#welcome-view").display

    def action_find(self) -> None:
        if not self._editor_ready():
            return
        self.hide_completions()
        self.query_one("#find-bar", FindBar).open(replace=False)

    def action_replace(self) -> None:
        if not self._editor_ready():
            return
        self.hide_completions()
        self.query_one("#find-bar", FindBar).open(replace=True)

    def action_goto_line(self) -> None:
        if not self._editor_ready():
            return
        self.hide_completions()
        editor = self._active_editor()
        line_count = editor.document.line_count

        def _go(raw: str | None) -> None:
            if raw is None:
                return
            target = parse_goto(raw)
            if target is None:
                self.notify("Type a line number, like 42 or 42:7.", severity="warning")
                return
            row = min(target[0], line_count - 1)
            col = min(target[1], len(editor.document.get_line(row)))
            editor.cursor_location = (row, col)
            editor.scroll_cursor_visible(center=True)
            editor.focus()

        row = editor.cursor_location[0] + 1
        self.push_screen(
            TextInputModal(
                title="Go to line",
                prompt=f"Line 1–{line_count}, optionally with a column (42:7). You're on line {row}.",
                placeholder="42",
                confirm_label="Go",
            ),
            _go,
        )

    def action_toggle_sidebar(self) -> None:
        if not self._on_main_screen():
            return
        self.set_sidebar_visible(not self.query_one("#sidebar").display)

    def action_toggle_terminal(self) -> None:
        if not self._on_main_screen():
            return
        if self.query_one("#welcome-view").display:
            self.notify("Open a folder or file to use Output.")
            return
        self.set_output_visible(not self.query_one("#output-pane").display)

    # ---- Layout ----

    def set_sidebar_visible(self, visible: bool) -> None:
        self.query_one("#sidebar").display = visible
        self.query_one("#sidebar-handle").display = visible
        if visible:
            self.set_sidebar_width(max(self.sidebar_width, MIN_SIDEBAR_WIDTH))
        elif self.focused is not None and self.query_one("#sidebar") in self.focused.ancestors:
            self._focus_main()
        self._sync_layout_toggles()

    def set_sidebar_width(self, width: int) -> None:
        max_w = MAX_SIDEBAR_WIDTH
        if self.size.width:
            max_w = max(MIN_SIDEBAR_WIDTH, min(MAX_SIDEBAR_WIDTH, self.size.width - 30))
        self.sidebar_width = max(MIN_SIDEBAR_WIDTH, min(width, max_w))
        sidebar = self.query_one("#sidebar")
        sidebar.styles.width = self.sidebar_width
        sidebar.styles.min_width = None
        sidebar.styles.max_width = None

    def set_output_visible(self, visible: bool) -> None:
        self.output_visible = visible
        on_welcome = self.query_one("#welcome-view").display
        out_pane = self.query_one("#output-pane")
        out_pane.display = visible and not on_welcome
        if visible and self.terminal_height < MIN_TERMINAL_HEIGHT + 2:
            self.set_terminal_height(DEFAULT_TERMINAL_HEIGHT)
        if not visible and self.focused is not None and out_pane in self.focused.ancestors:
            self._focus_main()
        self._sync_layout_toggles()

    def _focus_main(self) -> None:
        if self.query_one("#editor").display:
            self._active_editor().focus()
        else:
            self.query_one("#home-open-folder-btn").focus()

    def _sync_layout_toggles(self) -> None:
        self.query_one("#status-files-btn").set_class(self.query_one("#sidebar").display, "-on")
        self.query_one("#status-output-btn").set_class(
            bool(self.query_one("#output-pane").display), "-on"
        )

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
        if not self.query_one("#output-pane").display:
            self.set_output_visible(True)
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
        self._flush_output()
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

    # ---------- update check ----------

    def _update_state_path(self) -> Path:
        if self._recent_store is not None:
            return self._recent_store.parent / "update.json"
        return default_state_path()

    def _check_for_update(self) -> None:
        """Runs in a thread at startup; network at most once a day, silent on failure."""
        latest = check_for_update(__version__, self.install_kind, self._update_state_path())
        if latest:
            self.call_from_thread(self._prompt_update, latest)

    def _prompt_update(self, latest: str) -> None:
        how = {
            InstallKind.BINARY: "Update now downloads it and closes TmCode.",
            InstallKind.PIPX: "Update now runs pipx upgrade tmcode and closes TmCode.",
            InstallKind.UV: "Update now runs uv tool upgrade tmcode and closes TmCode.",
            InstallKind.PIP: "Update now runs pip install --upgrade tmcode and closes TmCode.",
        }.get(self.install_kind, "")

        def _chosen(choice: str | None) -> None:
            if choice == "update":
                def _go() -> None:
                    self.update_to = latest
                    self._shutdown_and_exit()

                self._guard_all_unsaved(_go)
            elif choice == "skip-version":
                skip_version(self._update_state_path(), latest)
                self.notify(f"Skipped {latest}. You'll be asked again when a newer version is out.")

        self.push_screen(UpdateModal(latest, __version__, how), _chosen)

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
