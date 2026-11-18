"""Reusable modal dialogs for TermCode."""

from __future__ import annotations

from pathlib import Path

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList
from textual.widgets.option_list import Option

from termcode.commands import CommandSpec, filter_commands


class CommandPaletteModal(ModalScreen[str | None]):
    """Searchable `/` action menu showing every command and its keyboard shortcut."""

    BINDINGS = [
        Binding("escape", "cancel", "Close", show=False),
    ]

    def __init__(self, initial_query: str = "/") -> None:
        super().__init__()
        self._initial_query = initial_query
        self._filtered: list[CommandSpec] = list(filter_commands(initial_query))

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-dialog", classes="modal-box"):
            yield Label("Command Menu  (type to filter, Enter to run, Esc to close)", classes="modal-title")
            yield Input(
                value=self._initial_query,
                placeholder="/run, /save, /new-file...",
                id="palette-input",
            )
            yield OptionList(id="palette-list")

    def on_mount(self) -> None:
        self._refresh_options(self._initial_query)
        inp = self.query_one("#palette-input", Input)
        inp.focus()
        inp.cursor_position = len(inp.value)

    def _refresh_options(self, query: str) -> None:
        self._filtered = filter_commands(query)
        option_list = self.query_one("#palette-list", OptionList)
        option_list.clear_options()
        for spec in self._filtered:
            label = f"{spec.command:<17} [{spec.shortcut:<6}]  {spec.description}"
            option_list.add_option(Option(label, id=spec.action))
        if self._filtered:
            option_list.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "palette-input":
            self._refresh_options(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "palette-input":
            self._select_highlighted()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id is not None:
            self.dismiss(str(event.option_id))

    def on_key(self, event: events.Key) -> None:
        option_list = self.query_one("#palette-list", OptionList)
        if event.key == "down":
            option_list.action_cursor_down()
            event.prevent_default()
            event.stop()
        elif event.key == "up":
            option_list.action_cursor_up()
            event.prevent_default()
            event.stop()

    def _select_highlighted(self) -> None:
        option_list = self.query_one("#palette-list", OptionList)
        idx = option_list.highlighted
        if idx is not None and 0 <= idx < len(self._filtered):
            self.dismiss(self._filtered[idx].action)
        elif self._filtered:
            self.dismiss(self._filtered[0].action)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class TextInputModal(ModalScreen[str | None]):
    """Single-line text input dialog for filenames, folders, renames, and directory paths."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(
        self,
        title: str,
        prompt: str,
        initial: str = "",
        placeholder: str = "",
        confirm_label: str = "OK",
    ) -> None:
        super().__init__()
        self._title = title
        self._prompt = prompt
        self._initial = initial
        self._placeholder = placeholder
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="text-dialog", classes="modal-box"):
            yield Label(self._title, classes="modal-title")
            yield Label(self._prompt, classes="modal-subtitle")
            yield Input(value=self._initial, placeholder=self._placeholder, id="modal-input")
            with Horizontal(classes="modal-buttons"):
                yield Button(self._confirm_label, variant="primary", id="ok-btn")
                yield Button("Cancel", id="cancel-btn")

    def on_mount(self) -> None:
        inp = self.query_one("#modal-input", Input)
        inp.focus()
        inp.cursor_position = len(inp.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok-btn":
            self.dismiss(self.query_one("#modal-input", Input).value)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class FilePickerModal(ModalScreen[Path | None]):
    """Quick-open fuzzy file picker for selecting any file in the current workspace."""

    BINDINGS = [
        Binding("escape", "cancel", "Close", show=False),
    ]

    def __init__(self, root: Path, files: list[Path]) -> None:
        super().__init__()
        self._root = root
        self._all_files = files
        self._filtered: list[Path] = list(files)

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-dialog", classes="modal-box"):
            yield Label(f"Open File in {self._root.name}/", classes="modal-title")
            yield Input(placeholder="Type part of a filename or path...", id="picker-input")
            yield OptionList(id="picker-list")

    def on_mount(self) -> None:
        self._refresh_options("")
        self.query_one("#picker-input", Input).focus()

    def _rel_label(self, path: Path) -> str:
        try:
            return path.relative_to(self._root).as_posix()
        except ValueError:
            return str(path)

    def _refresh_options(self, query: str) -> None:
        needle = query.strip().lower()
        if not needle:
            self._filtered = list(self._all_files)
        else:
            self._filtered = [
                p for p in self._all_files if needle in self._rel_label(p).lower()
            ]
        option_list = self.query_one("#picker-list", OptionList)
        option_list.clear_options()
        for idx, path in enumerate(self._filtered):
            option_list.add_option(Option(self._rel_label(path), id=str(idx)))
        if self._filtered:
            option_list.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "picker-input":
            self._refresh_options(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "picker-input":
            self._select_highlighted()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id is not None:
            idx = int(event.option_id)
            if 0 <= idx < len(self._filtered):
                self.dismiss(self._filtered[idx])

    def on_key(self, event: events.Key) -> None:
        option_list = self.query_one("#picker-list", OptionList)
        if event.key == "down":
            option_list.action_cursor_down()
            event.prevent_default()
            event.stop()
        elif event.key == "up":
            option_list.action_cursor_up()
            event.prevent_default()
            event.stop()

    def _select_highlighted(self) -> None:
        option_list = self.query_one("#picker-list", OptionList)
        idx = option_list.highlighted
        if idx is not None and 0 <= idx < len(self._filtered):
            self.dismiss(self._filtered[idx])
        elif self._filtered:
            self.dismiss(self._filtered[0])
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmModal(ModalScreen[bool]):
    """Confirmation dialog for destructive actions such as deleting files or folders."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, title: str, message: str, confirm_label: str = "Delete") -> None:
        super().__init__()
        self._title = title
        self._message = message
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog", classes="modal-box"):
            yield Label(self._title, classes="modal-title")
            yield Label(self._message, classes="modal-subtitle")
            with Horizontal(classes="modal-buttons"):
                yield Button(self._confirm_label, variant="error", id="confirm-btn")
                yield Button("Cancel", id="cancel-btn")

    def on_mount(self) -> None:
        self.query_one("#cancel-btn", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-btn")

    def action_cancel(self) -> None:
        self.dismiss(False)


class UnsavedChangesModal(ModalScreen[str]):
    """Three-way confirmation when leaving a buffer with unsaved changes."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, filename: str) -> None:
        super().__init__()
        self._filename = filename

    def compose(self) -> ComposeResult:
        with Vertical(id="unsaved-dialog", classes="modal-box"):
            yield Label("Unsaved Changes", classes="modal-title")
            yield Label(
                f"Save changes to '{self._filename}' before continuing?",
                classes="modal-subtitle",
            )
            with Horizontal(classes="modal-buttons"):
                yield Button("Save", variant="primary", id="save-btn")
                yield Button("Discard", variant="error", id="discard-btn")
                yield Button("Cancel", id="cancel-btn")

    def on_mount(self) -> None:
        self.query_one("#save-btn", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            self.dismiss("save")
        elif event.button.id == "discard-btn":
            self.dismiss("discard")
        else:
            self.dismiss("cancel")

    def action_cancel(self) -> None:
        self.dismiss("cancel")
