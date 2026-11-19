"""Inline find / replace bar that works on whichever editor is active."""

from __future__ import annotations

from collections.abc import Callable

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Static, TextArea
from textual.widgets.text_area import Selection

from termcode import theme
from termcode.search import (
    Match,
    SearchError,
    find_matches,
    next_match_index,
    replace_all,
    replace_one,
)


class FindBar(Vertical):
    """Find (Ctrl+F) and replace (Ctrl+R) for the active editor.

    Enter / Down = next match, Up = previous, Esc = close.
    In the replace field, Enter replaces the current match and moves on.
    """

    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
        Binding("down", "next", "Next", show=False),
        Binding("up", "prev", "Previous", show=False),
    ]

    def __init__(self, get_editor: Callable[[], TextArea], *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._get_editor = get_editor
        self.case_sensitive = False
        self.regex = False
        self._matches: list[Match] = []
        self._current: int | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="find-row"):
            yield Input(placeholder="Find", id="find-input")
            yield Static("", id="find-count")
            case_btn = Button("Aa", id="find-case", classes="find-btn")
            case_btn.tooltip = "Match case"
            yield case_btn
            regex_btn = Button(".*", id="find-regex", classes="find-btn")
            regex_btn.tooltip = "Use regular expression"
            yield regex_btn
            prev_btn = Button("↑", id="find-prev", classes="find-btn")
            prev_btn.tooltip = "Previous match (Up)"
            yield prev_btn
            next_btn = Button("↓", id="find-next", classes="find-btn")
            next_btn.tooltip = "Next match (Enter / Down)"
            yield next_btn
            close_btn = Button("×", id="find-close", classes="find-btn")
            close_btn.tooltip = "Close (Esc)"
            yield close_btn
        with Horizontal(id="replace-row"):
            yield Input(placeholder="Replace with", id="replace-input")
            yield Button("Replace", id="replace-one", classes="find-btn")
            yield Button("All", id="replace-all", classes="find-btn")

    # ---- public API ----

    @property
    def is_open(self) -> bool:
        return bool(self.display)

    @property
    def query_text(self) -> str:
        return self.query_one("#find-input", Input).value

    def open(self, *, replace: bool) -> None:
        editor = self._get_editor()
        self.display = True
        self.query_one("#replace-row").display = replace
        find_input = self.query_one("#find-input", Input)
        selected = editor.selected_text
        if selected and "\n" not in selected:
            find_input.value = selected
        find_input.focus()
        find_input.select_all()
        self._search(from_cursor=True)

    def action_close(self) -> None:
        self.display = False
        self._matches = []
        self._current = None
        self._get_editor().focus()

    def action_next(self) -> None:
        self._step(backwards=False)

    def action_prev(self) -> None:
        self._step(backwards=True)

    # ---- events ----

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "find-input":
            event.stop()
            self._search(from_cursor=True)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.input.id == "find-input":
            self._step(backwards=False)
        elif event.input.id == "replace-input":
            self.replace_current()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        bid = event.button.id
        if bid == "find-case":
            self.case_sensitive = not self.case_sensitive
            event.button.set_class(self.case_sensitive, "-on")
            self._search(from_cursor=True)
        elif bid == "find-regex":
            self.regex = not self.regex
            event.button.set_class(self.regex, "-on")
            self._search(from_cursor=True)
        elif bid == "find-prev":
            self._step(backwards=True)
        elif bid == "find-next":
            self._step(backwards=False)
        elif bid == "find-close":
            self.action_close()
        elif bid == "replace-one":
            self.replace_current()
        elif bid == "replace-all":
            self.replace_everything()

    # ---- search core ----

    def _selection_offsets(self, editor: TextArea) -> tuple[int, int]:
        doc = editor.document
        start, end = sorted((editor.selection.start, editor.selection.end))
        return doc.get_index_from_location(start), doc.get_index_from_location(end)

    def _recompute(self) -> bool:
        editor = self._get_editor()
        try:
            self._matches = find_matches(
                editor.text,
                self.query_text,
                case_sensitive=self.case_sensitive,
                regex=self.regex,
            )
        except SearchError:
            self._matches = []
            self._current = None
            self._show_count(error="Invalid regex")
            return False
        return True

    def _search(self, *, from_cursor: bool) -> None:
        if not self._recompute():
            return
        editor = self._get_editor()
        start, _ = self._selection_offsets(editor)
        self._current = next_match_index(self._matches, start) if from_cursor else None
        self._select_current()

    def _step(self, *, backwards: bool) -> None:
        if not self._recompute():
            return
        editor = self._get_editor()
        start, end = self._selection_offsets(editor)
        cursor = start if backwards else end
        self._current = next_match_index(self._matches, cursor, backwards=backwards)
        self._select_current()

    def _select_current(self) -> None:
        if self._current is None or not self._matches:
            self._show_count()
            return
        editor = self._get_editor()
        doc = editor.document
        s, e = self._matches[self._current]
        editor.selection = Selection(doc.get_location_from_index(s), doc.get_location_from_index(e))
        editor.scroll_cursor_visible(center=True)
        self._show_count()

    def _show_count(self, *, error: str | None = None) -> None:
        label = self.query_one("#find-count", Static)
        if error:
            label.update(Text(error, style=theme.FAIL))
        elif not self.query_text:
            label.update("")
        elif not self._matches:
            label.update(Text("No results", style=theme.FAIL))
        else:
            pos = (self._current or 0) + 1
            label.update(Text(f"{pos} of {len(self._matches)}", style=theme.MUTED))

    # ---- replace ----

    def replace_current(self) -> None:
        if not self._recompute() or not self._matches:
            self._show_count()
            return
        editor = self._get_editor()
        start, end = self._selection_offsets(editor)
        if (start, end) not in self._matches:
            # Nothing selected yet: first jump to a match, replace on the next press.
            self._step(backwards=False)
            return
        doc = editor.document
        matched = editor.text[start:end]
        try:
            new = replace_one(
                matched,
                self.query_text,
                self.query_one("#replace-input", Input).value,
                case_sensitive=self.case_sensitive,
                regex=self.regex,
            )
        except SearchError as exc:
            self._show_count(error=str(exc))
            return
        editor.replace(new, doc.get_location_from_index(start), doc.get_location_from_index(end))
        after = start + len(new)
        editor.selection = Selection.cursor(editor.document.get_location_from_index(after))
        self._step(backwards=False)

    def replace_everything(self) -> None:
        editor = self._get_editor()
        try:
            new_text, count = replace_all(
                editor.text,
                self.query_text,
                self.query_one("#replace-input", Input).value,
                case_sensitive=self.case_sensitive,
                regex=self.regex,
            )
        except SearchError as exc:
            self._show_count(error=str(exc))
            return
        if count == 0:
            self._show_count()
            return
        cursor = editor.cursor_location
        # One edit over the whole document, so a single Ctrl+Z undoes it.
        editor.replace(new_text, (0, 0), editor.document.end)
        row = min(cursor[0], editor.document.line_count - 1)
        col = min(cursor[1], len(editor.document.get_line(row)))
        editor.selection = Selection.cursor((row, col))
        self._recompute()
        self._current = None
        self.app.notify(f"Replaced {count} occurrence{'s' if count != 1 else ''}")
        self._show_count()
