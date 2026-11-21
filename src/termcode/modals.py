"""Reusable modal dialogs for TermCode."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList
from textual.widgets.option_list import Option

from termcode import theme
from termcode.commands import CommandSpec, filter_commands


def command_label(spec: CommandSpec, desc_width: int = 33) -> Text:
    """One-line palette row: command, muted description, shortcut at the end."""
    desc = spec.description
    if len(desc) > desc_width:
        desc = desc[: desc_width - 1] + "…"
    row = Text(no_wrap=True, overflow="ellipsis")
    row.append(f"{spec.command:<18}", theme.TEXT)
    row.append(f"{desc:<{desc_width}}", theme.MUTED)
    row.append(f" {spec.shortcut:>8}", theme.FAINT)
    return row


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
            yield Label("Commands", classes="modal-title")
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
            option_list.add_option(Option(command_label(spec), id=spec.action))
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
            yield Label(f"Open a file in {self._root.name}", classes="modal-title")
            yield Input(placeholder="Type part of a file name or path", id="picker-input")
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


class UpdateModal(ModalScreen[str]):
    """'A new version is available' prompt: update / skip / skip until next version."""

    BINDINGS = [
        Binding("escape", "skip", "Skip", show=False),
    ]

    def __init__(self, latest: str, current: str, how: str) -> None:
        super().__init__()
        self._latest = latest
        self._current = current
        self._how = how

    def compose(self) -> ComposeResult:
        with Vertical(id="update-dialog", classes="modal-box"):
            yield Label(f"TmCode {self._latest} is available", classes="modal-title")
            yield Label(
                f"You have {self._current}. {self._how}",
                classes="modal-subtitle",
            )
            with Horizontal(classes="modal-buttons"):
                yield Button("Update now", variant="primary", id="update-now-btn")
                yield Button("Skip", id="update-skip-btn")
                yield Button("Skip until next version", id="update-skip-version-btn")

    def on_mount(self) -> None:
        self.query_one("#update-now-btn", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(
            {
                "update-now-btn": "update",
                "update-skip-version-btn": "skip-version",
            }.get(event.button.id or "", "skip")
        )

    def action_skip(self) -> None:
        self.dismiss("skip")


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
            yield Label("Unsaved changes", classes="modal-title")
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


class FolderPickerModal(ModalScreen[Path | None]):
    """Interactive VS Code-style folder browser and path selector."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, start_dir: Path | None = None) -> None:
        super().__init__()
        initial = start_dir if (start_dir and start_dir.is_dir()) else Path.home()
        self._current: Path = initial.resolve()
        self._entries: list[Path] = []

    def compose(self) -> ComposeResult:
        home = Path.home()
        quick_places: list[tuple[str, str, Path]] = [("quick-home", "~ Home", home)]
        for label_name in ("Documents", "Desktop", "Projects"):
            candidate = home / label_name
            if candidate.is_dir():
                quick_places.append((f"quick-{label_name.lower()}", label_name, candidate))
        self._quick_map: dict[str, Path] = {btn_id: p for btn_id, _, p in quick_places}

        with Vertical(id="folder-dialog", classes="modal-box"):
            yield Label("Open folder", classes="modal-title", markup=False)
            yield Input(
                value=str(self._current),
                placeholder="Type a path, or pick a folder below",
                id="folder-path-input",
            )
            with Horizontal(classes="quick-bar"):
                yield Button("↑ Up", id="quick-up", classes="quick-btn")
                for btn_id, label, _ in quick_places:
                    yield Button(label, id=btn_id, classes="quick-btn")
            yield OptionList(id="folder-list")
            with Horizontal(classes="modal-buttons"):
                yield Button("Open this folder", variant="primary", id="open-folder-btn")
                yield Button("Cancel", id="cancel-btn")

    def on_mount(self) -> None:
        self._load_directory(self._current)
        self.query_one("#folder-list", OptionList).focus()

    def _load_directory(self, directory: Path) -> None:
        self._current = directory.resolve()
        path_input = self.query_one("#folder-path-input", Input)
        path_input.value = str(self._current)

        option_list = self.query_one("#folder-list", OptionList)
        option_list.clear_options()
        self._entries = []

        if self._current.parent != self._current:
            self._entries.append(self._current.parent)
            option_list.add_option(Option(Text("↑ ..  parent folder", style=theme.MUTED), id="0"))

        try:
            subdirs = sorted(
                (
                    p
                    for p in self._current.iterdir()
                    if p.is_dir() and not p.name.startswith(".")
                ),
                key=lambda p: p.name.lower(),
            )
        except OSError:
            subdirs = []

        for subdir in subdirs:
            idx = len(self._entries)
            self._entries.append(subdir)
            option_list.add_option(Option(f"{subdir.name}/", id=str(idx)))

        if self._entries:
            option_list.highlighted = 0

    def _resolve_input_path(self) -> Path | None:
        raw = self.query_one("#folder-path-input", Input).value.strip()
        if not raw:
            return self._current
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self._current / candidate
        try:
            resolved = candidate.resolve()
        except OSError:
            return None
        return resolved if resolved.is_dir() else None

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "folder-path-input":
            return
        resolved = self._resolve_input_path()
        if resolved is None:
            self.app.notify(f"Folder not found: {event.value}", severity="error")
            return
        if resolved == self._current:
            self.dismiss(resolved)
        else:
            self._load_directory(resolved)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id is None:
            return
        idx = int(event.option_id)
        if 0 <= idx < len(self._entries):
            self._load_directory(self._entries[idx])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "open-folder-btn":
            resolved = self._resolve_input_path()
            if resolved is None:
                self.app.notify("Enter a valid existing folder path.", severity="error")
                return
            self.dismiss(resolved)
        elif btn_id == "cancel-btn":
            self.dismiss(None)
        elif btn_id == "quick-up":
            if self._current.parent != self._current:
                self._load_directory(self._current.parent)
        elif btn_id in self._quick_map:
            self._load_directory(self._quick_map[btn_id])

    def action_cancel(self) -> None:
        self.dismiss(None)


class ProblemsModal(ModalScreen[str | None]):
    """Problems the language server found in the current file; Enter jumps to one."""

    BINDINGS = [
        Binding("escape", "cancel", "Close", show=False),
    ]

    def __init__(self, options: list[tuple[str, str]]) -> None:
        super().__init__()
        self._options = options

    def compose(self) -> ComposeResult:
        with Vertical(id="problems-dialog", classes="modal-box"):
            yield Label(f"Problems ({len(self._options)})", classes="modal-title")
            yield OptionList(id="problems-list")

    def on_mount(self) -> None:
        option_list = self.query_one("#problems-list", OptionList)
        for label, option_id in self._options:
            option_list.add_option(Option(label, id=option_id))
        if self._options:
            option_list.highlighted = 0
        option_list.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_id)

    def action_cancel(self) -> None:
        self.dismiss(None)
