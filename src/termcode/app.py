"""Main Textual TUI application for TermCode."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import asyncio
import contextlib
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
from termcode.lsp import Diagnostic, Location, LspManager, rank_completions, word_start
from termcode.editing import (
    comment_tokens,
    indent_lines,
    indent_unit,
    move_lines,
    outdent_lines,
    smart_home_column,
    toggle_comment,
    word_at,
)
from termcode.modals import (
    CommandPaletteModal,
    ConfirmModal,
    FilePickerModal,
    ProblemsModal,
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
    BUILD_FOLDER,
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
        Binding("shift+delete", "cut", "Cut line", show=False),
        Binding("ctrl+shift+k", "delete_line", "Delete line", show=False),
        Binding("alt+up", "move_lines(-1)", "Move line up", show=False),
        Binding("alt+down", "move_lines(1)", "Move line down", show=False),
        Binding("alt+shift+up", "copy_lines(-1)", "Copy line up", show=False),
        Binding("alt+shift+down,ctrl+shift+d", "copy_lines(1)", "Copy line down", show=False),
        Binding("ctrl+slash,ctrl+underscore", "toggle_comment", "Toggle comment", show=False),
        Binding("ctrl+d", "select_next_match", "Select word / next match", show=False),
        Binding("home", "smart_home", "Line start", show=False),
        Binding("shift+home", "smart_home(True)", "Select to line start", show=False),
        Binding("ctrl+home", "document_start", "Top of file", show=False),
        Binding("ctrl+end", "document_end", "End of file", show=False),
        Binding("ctrl+shift+home", "document_start(True)", "Select to top", show=False),
        Binding("ctrl+shift+end", "document_end(True)", "Select to end", show=False),
        Binding("ctrl+delete", "delete_word_right", "Delete word right", show=False),
        Binding("ctrl+shift+z", "redo", "Redo", show=False),
        Binding("ctrl+enter", "insert_line(1)", "New line below", show=False),
        Binding("ctrl+shift+enter", "insert_line(0)", "New line above", show=False),
        Binding("shift+tab", "outdent", "Outdent", show=False),
        Binding("alt+z", "toggle_wrap", "Word wrap", show=False),
    ]

    # The open file; picks comment markers for Ctrl+/.
    file_path: Path | None = None

    # ---------- line helpers ----------

    def _selected_rows(self) -> tuple[int, int]:
        """First and last row touched by the selection (a selection ending at
        column 0 doesn't include that last line, like VS Code)."""
        start, end = sorted(self.selection)
        last = end[0]
        if end[0] > start[0] and end[1] == 0:
            last -= 1
        return start[0], last

    def _replace_rows(self, first: int, last: int, new_lines: list[str]) -> None:
        """Replace whole rows first..last with new_lines as one undo step."""
        end_col = len(self.document.get_line(last))
        self.replace("\n".join(new_lines), (first, 0), (last, end_col))

    def _row_lines(self, first: int, last: int) -> list[str]:
        return [self.document.get_line(r) for r in range(first, last + 1)]

    # ---------- actions ----------

    def action_move_lines(self, direction: int) -> None:
        """Alt+Up / Alt+Down: move the current line (or selected lines)."""
        if self.read_only:
            return
        first, last = self._selected_rows()
        all_lines = self._row_lines(0, self.document.line_count - 1)
        moved = move_lines(all_lines, first, last, direction)
        if moved is None:
            return
        new_lines, new_first = moved
        lo, hi = min(first, new_first), max(last, new_first + (last - first))
        selection = self.selection
        self._replace_rows(lo, hi, new_lines[lo : hi + 1])
        shift = new_first - first
        self.selection = Selection(
            (selection.start[0] + shift, selection.start[1]),
            (selection.end[0] + shift, selection.end[1]),
        )

    def action_copy_lines(self, direction: int) -> None:
        """Alt+Shift+Up / Alt+Shift+Down: duplicate the line (or selected lines)."""
        if self.read_only:
            return
        first, last = self._selected_rows()
        block = self._row_lines(first, last)
        selection = self.selection
        end_col = len(self.document.get_line(last))
        self.insert("\n" + "\n".join(block), (last, end_col))
        if direction > 0:
            shift = last - first + 1
            self.selection = Selection(
                (selection.start[0] + shift, selection.start[1]),
                (selection.end[0] + shift, selection.end[1]),
            )
        else:
            self.selection = selection

    def action_toggle_comment(self) -> None:
        """Ctrl+/: comment or uncomment the line (or selected lines)."""
        if self.read_only:
            return
        first, last = self._selected_rows()
        before = self._row_lines(first, last)
        after = toggle_comment(before, comment_tokens(self.file_path))
        if after == before:
            return
        selection = self.selection
        self._replace_rows(first, last, after)
        # Keep the cursor on the same text: shift columns by what changed on each line.
        def moved(loc: tuple[int, int]) -> tuple[int, int]:
            row, col = loc
            if first <= row <= last:
                delta = len(after[row - first]) - len(before[row - first])
                return row, max(0, min(len(after[row - first]), col + delta))
            return loc
        self.selection = Selection(moved(selection.start), moved(selection.end))

    def action_outdent(self) -> None:
        """Shift+Tab: remove one indent level from the line (or selected lines)."""
        if self.read_only:
            return
        first, last = self._selected_rows()
        before = self._row_lines(first, last)
        after, removed = outdent_lines(before, indent_unit(self.text))
        if after == before:
            return
        selection = self.selection
        self._replace_rows(first, last, after)

        def moved(loc: tuple[int, int]) -> tuple[int, int]:
            row, col = loc
            if first <= row <= last:
                return row, max(0, col - removed[row - first])
            return loc
        self.selection = Selection(moved(selection.start), moved(selection.end))

    def _indent_selection(self) -> None:
        first, last = self._selected_rows()
        before = self._row_lines(first, last)
        unit = indent_unit(self.text)
        after = indent_lines(before, unit)
        selection = self.selection
        self._replace_rows(first, last, after)

        def moved(loc: tuple[int, int]) -> tuple[int, int]:
            row, col = loc
            if first <= row <= last and before[row - first].strip():
                return row, col + len(unit)
            return loc
        self.selection = Selection(moved(selection.start), moved(selection.end))

    def action_smart_home(self, select: bool = False) -> None:
        """Home: first non-blank character, press again for column 0."""
        row, col = self.cursor_location
        target = (row, smart_home_column(self.document.get_line(row), col))
        self.move_cursor(target, select=select)

    def action_document_start(self, select: bool = False) -> None:
        self.move_cursor((0, 0), select=select)

    def action_document_end(self, select: bool = False) -> None:
        last = self.document.line_count - 1
        self.move_cursor((last, len(self.document.get_line(last))), select=select)

    def action_insert_line(self, below: int = 1) -> None:
        """Ctrl+Enter: new line below; Ctrl+Shift+Enter: new line above (keeps indent)."""
        if self.read_only:
            return
        row = self.cursor_location[0]
        line = self.document.get_line(row)
        indent = line[: len(line) - len(line.lstrip(" \t"))]
        if below:
            if line.rstrip().endswith((":", "{", "(", "[")):
                indent += indent_unit(self.text)
            self.insert("\n" + indent, (row, len(line)))
            self.move_cursor((row + 1, len(indent)))
        else:
            self.insert(indent + "\n", (row, 0))
            self.move_cursor((row, len(indent)))

    def action_select_next_match(self) -> None:
        """Ctrl+D: select the word at the cursor; press again to jump to its next match."""
        selected = self.selected_text
        if not selected:
            row, col = self.cursor_location
            span = word_at(self.document.get_line(row), col)
            if span is not None:
                self.selection = Selection((row, span[0]), (row, span[1]))
            return
        if "\n" in selected:
            return
        text = self.text
        lines = text.split("\n")
        _, end = sorted(self.selection)
        offset = sum(len(lines[r]) + 1 for r in range(end[0])) + end[1]
        found = text.find(selected, offset)
        if found < 0:
            found = text.find(selected)  # wrap around
        if found < 0:
            return
        row = text.count("\n", 0, found)
        col = found - (text.rfind("\n", 0, found) + 1)
        self.selection = Selection((row, col), (row, col + len(selected)))
        self.scroll_cursor_visible()

    def action_toggle_wrap(self) -> None:
        """Alt+Z: wrap long lines on/off."""
        self.soft_wrap = not self.soft_wrap
        self.app.notify("Word wrap on" if self.soft_wrap else "Word wrap off", timeout=1.5)

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
        else:
            self._line_cache.clear()
            self._highlights.clear()
            try:
                self._highlights.update(pygments_highlights(self.text, self._fallback_lexer))
            except Exception:
                pass  # never let coloring break editing
        self._add_diagnostic_highlights()

    # ---------- language server problems ----------

    diagnostics: list[Diagnostic] = []

    def set_diagnostics(self, diagnostics: list[Diagnostic]) -> None:
        if not diagnostics and not self.diagnostics:
            return
        self.diagnostics = list(diagnostics)
        self._build_highlight_map()
        self.refresh()

    def _add_diagnostic_highlights(self) -> None:
        if not self.diagnostics:
            return
        line_count = self.document.line_count
        # Least severe first, so errors are drawn last and win.
        for diag in sorted(self.diagnostics, key=lambda d: -d.severity):
            name = "diag.error" if diag.severity == 1 else "diag.warning" if diag.severity == 2 else "diag.info"
            (s_row, s_col), (e_row, e_col) = diag.start, diag.end
            if s_row >= line_count:
                continue
            e_row = min(e_row, line_count - 1)
            for row in range(s_row, e_row + 1):
                line = self.document.get_line(row)
                start = s_col if row == s_row else 0
                end = e_col if row == e_row else len(line)
                start = min(start, len(line))
                end = min(end, len(line))
                if end <= start:
                    # Zero-width problem: mark the character (or word) there.
                    if not line.strip():
                        continue
                    if start >= len(line):
                        start = max(0, len(line.rstrip()) - 1)
                    end = start + 1
                b_start = len(line[:start].encode("utf-8"))
                b_end = len(line[:end].encode("utf-8"))
                self._highlights[row].append((b_start, b_end, name))

    def diagnostic_at(self, row: int, col: int | None = None) -> Diagnostic | None:
        """The most severe problem on this row (preferring one under col)."""
        on_row = [d for d in self.diagnostics if d.start[0] <= row <= d.end[0]]
        if not on_row:
            return None
        if col is not None:
            under = [d for d in on_row if (d.start[0], d.start[1]) <= (row, col) <= (d.end[0], d.end[1])]
            if under:
                on_row = under
        return min(on_row, key=lambda d: (d.severity, d.start))

    async def _on_click(self, event: events.Click) -> None:
        await super()._on_click(event)
        # Ctrl+click goes to the definition, like VS Code.
        if event.ctrl and not self.read_only:
            app = self.app
            if isinstance(app, TermCodeApp):
                app.action_go_to_definition()

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

        if event.key == "tab":
            first, last = self._selected_rows()
            if self.selected_text and last > first:
                self._indent_selection()
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
        Binding("ctrl+backslash", "toggle_split", "Split", show=False, priority=True),
        Binding("f1", "hover_info", "Info", show=False, priority=True),
        Binding("f12", "go_to_definition", "Definition", show=False, priority=True),
        Binding("alt+left", "go_back", "Back", show=False, priority=True),
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
        # Language servers: errors, smart completions, hover, go to definition.
        # Decided on mount (off in headless tests unless a test turns it on).
        self.lsp_enabled: bool | None = None
        self.lsp = LspManager(self._on_lsp_diagnostics)
        self._lsp_hinted: set[str] = set()
        self._lsp_completion_serial = 0
        self._jump_back: list[tuple[Path, tuple[int, int]]] = []
        self._recent_folders: list[Path] = []
        self._completion_editor: CodeTextArea | None = None
        self._completion_prefix: str = ""
        self._completion_items: list[str] = []
        self._completion_edits: list[tuple[str, int]] = []  # (text to insert, start column)
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
                            split_btn.tooltip = "Split editor (Ctrl+\\)"
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
        yield Static("", id="hover-popup", markup=False)
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
        self.query_one("#hover-popup").display = False
        self.query_one("#find-bar").display = False
        if self.lsp_enabled is None:
            self.lsp_enabled = not self.is_headless and not os.environ.get("TMCODE_NO_LSP")
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
        self._apply_language_colors(editor, path)
        self._lsp_attach(editor, path)

    def _apply_language_colors(self, editor: CodeTextArea, path: Path | None) -> None:
        wanted = editor_language_for_path(path)
        editor.file_path = path
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
        if not on_welcome:
            # The problem on the cursor's line replaces the folder name, like an error lens.
            editor = self._active_editor()
            diag = editor.diagnostic_at(*editor.cursor_location)
            if diag is not None:
                color = theme.FAIL if diag.severity == 1 else theme.BUSY if diag.severity == 2 else theme.MUTED
                icon = "✗" if diag.severity == 1 else "⚠" if diag.severity == 2 else "ℹ"
                left = Text(no_wrap=True, overflow="ellipsis")
                left.append(f"{icon} ", color)
                left.append(diag.message.splitlines()[0] if diag.message else diag.label, color)
        status_left.update(left)

        right = Text(no_wrap=True)
        if not on_welcome:
            editor = self._active_editor()
            row, col = editor.cursor_location
            lang_label = language_label_for_path(self.active_file)
            errors = sum(1 for d in editor.diagnostics if d.severity == 1)
            warnings = sum(1 for d in editor.diagnostics if d.severity == 2)
            if errors:
                right.append(f"✗ {errors} ", theme.FAIL)
            if warnings:
                right.append(f"⚠ {warnings} ", theme.BUSY)
            if errors or warnings:
                right.append(sep)
            right.append(f"Ln {row + 1}, Col {col + 1}{sep}{lang_label}", theme.MUTED)
            if self.active_file is not None and self.lsp_enabled:
                server = self.lsp.server_label(self.active_file)
                if server:
                    right.append(f" · {server}", theme.FAINT)
            right.append(sep)
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

    # ---- Language servers ----

    def _lsp_root(self, path: Path) -> Path:
        root = self.workspace_root
        if root is not None:
            try:
                path.resolve().relative_to(root.resolve())
                return root
            except ValueError:
                pass
        return path.parent

    def _lsp_attach(self, editor: CodeTextArea, path: Path | None) -> None:
        if not self.lsp_enabled or path is None:
            editor.set_diagnostics([])
            return
        editor.set_diagnostics(self.lsp.diagnostics.get(path.resolve(), []))
        choice = self.lsp.choice_for(path)
        if choice is None:
            return
        if choice.command is None:
            if choice.key not in self._lsp_hinted:
                self._lsp_hinted.add(choice.key)
                self.notify(
                    f"For error checking and smart completion in {choice.label}, install its "
                    f"language server:\n{choice.install}",
                    title="Smarter editing available",
                    timeout=8,
                )
            return
        self.run_worker(self._lsp_open(path, editor.text), group="lsp", exit_on_error=False)

    async def _lsp_open(self, path: Path, text: str) -> None:
        ok = await self.lsp.open(path, text, self._lsp_root(path))
        if not ok:
            choice = self.lsp.choice_for(path)
            key = f"failed:{choice.key if choice else path.suffix}"
            if choice is not None and key not in self._lsp_hinted:
                self._lsp_hinted.add(key)
                why = self.lsp.errors.get(choice.key, "")
                self.notify(
                    f"The {choice.label} language server didn't start." + (f"\n{why[:300]}" if why else ""),
                    severity="warning",
                    timeout=10,
                )
        self._update_chrome()

    def _lsp_saved(self, path: Path, text: str) -> None:
        if self.lsp_enabled:
            self.run_worker(self.lsp.saved(path, text), group="lsp", exit_on_error=False)

    def _lsp_forget(self, path: Path | None) -> None:
        if not self.lsp_enabled or path is None:
            return
        for pane in (self.left_pane, self.right_pane):
            if any(b.path is not None and b.path.resolve() == path.resolve() for b in pane.tabs):
                return
        self.run_worker(self.lsp.close(path), group="lsp", exit_on_error=False)

    def _on_lsp_diagnostics(self, path: Path) -> None:
        found = self.lsp.diagnostics.get(path, [])
        for pane_id in ("left", "right"):
            if pane_id == "right" and not self.is_split:
                continue
            editor = self._editor_for_pane(pane_id)
            if editor.file_path is not None and editor.file_path.resolve() == path:
                editor.set_diagnostics(found)
        self._update_chrome()

    def _editor_file(self) -> tuple[CodeTextArea, Path] | None:
        if self.query_one("#welcome-view").display or self.active_file is None:
            return None
        return self._active_editor(), self.active_file

    def action_hover_info(self) -> None:
        """F1: show the type and docs of the symbol under the cursor."""
        target = self._editor_file()
        if target is None:
            return
        editor, path = target
        if self.lsp.client_for(path) is None:
            self._explain_no_lsp(path)
            return
        self.run_worker(self._show_hover(editor, path), group="lsp-hover", exclusive=True, exit_on_error=False)

    async def _show_hover(self, editor: CodeTextArea, path: Path) -> None:
        where = editor.cursor_location
        text = await self.lsp.hover(path, *where)
        diag = editor.diagnostic_at(*where)
        if diag is not None:
            problem = f"{diag.label}: {diag.message}"
            text = f"{problem}\n\n{text}" if text else problem
        if editor.cursor_location != where:
            return
        if not text:
            self.notify("No info for this spot.", timeout=2)
            return
        self.show_hover(editor, text)

    def show_hover(self, editor: CodeTextArea, text: str) -> None:
        popup = self.query_one("#hover-popup", Static)
        lines = text.splitlines()[:14]
        width = min(max((len(l) for l in lines), default=10) + 2, max(20, self.size.width - 4), 80)
        popup.update(Text("\n".join(lines), style=theme.TEXT, overflow="fold"))
        popup.styles.width = width
        cx, cy = editor.cursor_screen_offset
        height = min(len(lines), 14) + 2
        pos_x = max(0, min(cx, self.size.width - width - 1))
        pos_y = cy + 1 if cy + 1 + height <= self.size.height - 1 else max(0, cy - height)
        popup.styles.offset = (pos_x, pos_y)
        popup.display = True

    def hide_hover(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#hover-popup").display = False

    def action_go_to_definition(self) -> None:
        """F12 / Ctrl+click: jump to where the symbol under the cursor is defined."""
        target = self._editor_file()
        if target is None:
            return
        editor, path = target
        if self.lsp.client_for(path) is None:
            self._explain_no_lsp(path)
            return
        self.run_worker(self._go_to_definition(editor, path), group="lsp-def", exclusive=True, exit_on_error=False)

    async def _go_to_definition(self, editor: CodeTextArea, path: Path) -> None:
        start = editor.cursor_location
        location = await self.lsp.definition(path, *start)
        if location is None:
            self.notify("No definition found here.", timeout=2)
            return
        self._jump_back.append((path, start))
        del self._jump_back[:-50]
        self._jump_to(location)

    def _jump_to(self, location: Location) -> None:
        current = self.active_file
        if current is None or current.resolve() != location.path.resolve():
            if not location.path.is_file() or not self.load_file(location.path):
                self.notify(f"Can't open {location.path}", severity="warning")
                return
        editor = self._active_editor()
        row = min(location.row, editor.document.line_count - 1)
        col = min(location.col, len(editor.document.get_line(row)))
        editor.move_cursor((row, col), center=True)
        editor.focus()

    def action_go_back(self) -> None:
        """Alt+Left: back to where you were before the last go to definition."""
        if not self._jump_back:
            return
        path, (row, col) = self._jump_back.pop()
        self._jump_to(Location(path, row, col))

    def action_show_problems(self) -> None:
        """List the problems in this file; pick one to jump to it."""
        target = self._editor_file()
        if target is None:
            return
        editor, path = target
        if not editor.diagnostics:
            if self.lsp.client_for(path) is None:
                self._explain_no_lsp(path)
            else:
                self.notify("No problems in this file.", timeout=2)
            return
        options = [
            (f"{d.start[0] + 1}:{d.start[1] + 1}  {d.label}  {d.message.splitlines()[0]}", f"{i}")
            for i, d in enumerate(editor.diagnostics)
        ]

        def _picked(choice: str | None) -> None:
            if choice is None:
                return
            diag = editor.diagnostics[int(choice)] if int(choice) < len(editor.diagnostics) else None
            if diag is not None:
                editor.move_cursor(diag.start, center=True)
                editor.focus()

        self.push_screen(ProblemsModal(options), _picked)

    def action_lsp_info(self) -> None:
        target = self._editor_file()
        if target is None:
            self.notify("Open a file to see its language server.")
            return
        _editor, path = target
        label = self.lsp.server_label(path)
        if label:
            self.notify(f"{path.name} is checked by {label}.", title="Language server")
        else:
            self._explain_no_lsp(path)

    def _explain_no_lsp(self, path: Path) -> None:
        if not self.lsp_enabled:
            self.notify("Language servers are turned off (TMCODE_NO_LSP).")
            return
        choice = self.lsp.choice_for(path)
        if choice is None:
            self.notify(f"TmCode doesn't know a language server for {path.suffix or path.name} files. "
                        "You can add one in lsp.json.", timeout=6)
        elif choice.command is None:
            self.notify(f"Install the {choice.label} language server to get this:\n{choice.install}",
                        title="Smarter editing available", timeout=8)
        else:
            self.notify(f"The {choice.label} language server is still starting...", timeout=3)

    # ---- Inline Autocomplete Popup ----

    def is_completion_open_for(self, editor: CodeTextArea) -> bool:
        popup = self.query_one("#completion-popup", OptionList)
        return bool(popup.display and self._completion_editor is editor and self._completion_items)

    def hide_completions(self, *, keep_pending: bool = False) -> None:
        popup = self.query_one("#completion-popup", OptionList)
        popup.display = False
        self._completion_editor = None
        self._completion_prefix = ""
        self._completion_items = []
        self._completion_edits = []
        if not keep_pending:
            self._lsp_completion_serial += 1  # drop server answers still on the way

    def _update_completions(self, editor: CodeTextArea) -> None:
        if editor.read_only or not self._on_main_screen():
            self.hide_completions()
            return
        row, col = editor.cursor_location
        line = editor.document.get_line(row)
        prefix = extract_prefix(line, col)
        items = get_completions(prefix, editor.language, editor.text, limit=7)
        self._request_lsp_completions(editor, row, col, line)
        if not items:
            self.hide_completions(keep_pending=True)
            return
        self._show_completion_popup(
            editor, prefix, [(word, "") for word in items], [(word, col - len(prefix)) for word in items]
        )

    def _request_lsp_completions(self, editor: CodeTextArea, row: int, col: int, line: str) -> None:
        path = editor.file_path
        if not self.lsp_enabled or path is None or self.lsp.client_for(path) is None:
            return
        before = line[:col]
        trigger = next((t for t in self.lsp.trigger_characters(path) if t.strip() and before.endswith(t)), None)
        typed = before[word_start(line, col):]
        if not typed and trigger is None:
            return
        self._lsp_completion_serial += 1
        serial = self._lsp_completion_serial
        self.run_worker(
            self._lsp_completions(editor, path, row, col, trigger, serial),
            group="lsp-complete", exclusive=True, exit_on_error=False,
        )

    async def _lsp_completions(
        self, editor: CodeTextArea, path: Path, row: int, col: int, trigger: str | None, serial: int
    ) -> None:
        await asyncio.sleep(0.08)  # let fast typing settle
        if serial != self._lsp_completion_serial:
            return
        found = await self.lsp.completions(path, row, col, trigger)
        if serial != self._lsp_completion_serial or editor.cursor_location != (row, col) or self.focused is not editor:
            return
        line = editor.document.get_line(row)
        typed = line[word_start(line, col):col]
        ranked = rank_completions(found, typed, limit=12)
        if not ranked:
            return
        self._show_completion_popup(
            editor,
            typed,
            [(item.label, item.kind) for item in ranked],
            [(item.insert, item.start_col) for item in ranked],
        )

    def _show_completion_popup(
        self,
        editor: CodeTextArea,
        prefix: str,
        labels: list[tuple[str, str]],
        edits: list[tuple[str, int]],
    ) -> None:
        items = [label for label, _kind in labels]
        popup = self.query_one("#completion-popup", OptionList)
        popup.clear_options()
        for index, (label, kind) in enumerate(labels):
            text = Text(no_wrap=True, overflow="ellipsis")
            text.append(label)
            if kind:
                text.append(f"  {kind}", theme.MUTED)
            popup.add_option(Option(text, id=f"c{index}"))
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
        self._completion_edits = edits

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
        word, start_col = self._completion_edits[idx]
        row, col = target.cursor_location
        start_col = max(0, min(start_col, col))
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
                            self._lsp_saved(b.path, b.content)
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

        self._lsp_saved(buf.path, buf.content)
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

    def action_editor_toggle_comment(self) -> None:
        editor = self._active_editor()
        editor.action_toggle_comment()
        editor.focus()

    def action_editor_toggle_wrap(self) -> None:
        editor = self._active_editor()
        editor.action_toggle_wrap()
        editor.focus()

    def action_clean_builds(self) -> None:
        """Delete the saved programs (.tmcode) next to the current file; the next run rebuilds."""
        if self.active_file is None:
            self.notify("Open a file first.")
            return
        if self.runner.is_running:
            self.notify("Stop the running program first (F7).", severity="warning")
            return
        folder = self.active_file.parent / BUILD_FOLDER
        if not folder.is_dir():
            self.notify("Nothing to clean: no saved builds next to this file.")
            return
        try:
            shutil.rmtree(folder)
        except OSError as exc:
            self.notify(f"Could not delete {folder.name}: {exc}", severity="error")
            return
        self.notify("Deleted saved builds. The next run compiles fresh.")

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
        if self.lsp_enabled and buf.path is not None:
            self.lsp.change(buf.path, buf.content)
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
            self.hide_hover()
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
            self._lsp_forget(buf.path)
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
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.lsp.shutdown(), 4.0)
        self.exit()

    def on_unmount(self) -> None:
        # Never leave a language server running behind us.
        self.lsp.kill_all()
