"""Main Textual TUI application for TermCode."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DirectoryTree, Footer, Input, Label, RichLog, Static, TextArea

from termcode.modals import (
    CommandPaletteModal,
    ConfirmModal,
    FilePickerModal,
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
    WorkspaceError,
    create_file,
    create_folder,
    delete_path,
    list_workspace_files,
    read_text_file,
    rename_path,
    write_text_file,
)


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


class TermCodeApp(App[None]):
    """Lightweight VS Code-style terminal editor and runner for Python, C, and C++."""

    CSS_PATH = "styles.tcss"
    TITLE = "TermCode"

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
        Binding("ctrl+b", "toggle_sidebar", "Files", priority=True),
        Binding("ctrl+l", "clear_output", "Clear", show=False, priority=True),
        Binding("ctrl+q", "request_quit", "Quit", priority=True),
    ]

    def __init__(self, workspace_root: Path, initial_file: Path | None = None) -> None:
        super().__init__()
        self.workspace_root: Path = workspace_root.resolve()
        self.active_file: Path | None = initial_file.resolve() if initial_file else None
        self.selected_path: Path | None = self.active_file or self.workspace_root
        self.is_dirty: bool = False
        self._saved_content: str = ""
        self._suppress_dirty: bool = False
        self.runner: AsyncRunner = AsyncRunner()
        self.output_history: str = ""

    def compose(self) -> ComposeResult:
        yield Static("", id="top-bar")
        with Horizontal(id="workspace-body"):
            with Vertical(id="sidebar"):
                yield Label("FILES  (press / for menu)", id="sidebar-header")
                yield DirectoryTree(str(self.workspace_root), id="file-tree")
            with Vertical(id="main-pane"):
                yield TextArea.code_editor(
                    "",
                    language=None,
                    show_line_numbers=True,
                    id="editor",
                )
                with Vertical(id="output-pane"):
                    yield Static("OUTPUT [Idle]", id="output-header")
                    yield RichLog(id="output-log", highlight=False, markup=False, auto_scroll=True)
                    yield Input(
                        placeholder="Program input (enabled while running)...",
                        disabled=True,
                        id="stdin-input",
                    )
        yield Footer()

    def on_mount(self) -> None:
        if self.active_file is not None:
            self.load_file(self.active_file)
        else:
            self._update_top_bar()
        self.query_one("#editor", TextArea).focus()

    def _on_main_screen(self) -> bool:
        return len(self.screen_stack) == 1

    def _apply_editor_language(self, path: Path | None) -> None:
        editor = self.query_one("#editor", TextArea)
        wanted = editor_language_for_path(path)
        if wanted and wanted in editor.available_languages:
            editor.language = wanted
        else:
            editor.language = None

    def _update_top_bar(self) -> None:
        bar = self.query_one("#top-bar", Static)
        if self.active_file is not None:
            try:
                rel = self.active_file.relative_to(self.workspace_root).as_posix()
            except ValueError:
                rel = self.active_file.name
        else:
            rel = "Untitled"
        lang_label = language_label_for_path(self.active_file)
        dirty_mark = " *" if self.is_dirty else ""
        bar.update(f"TermCode  |  {self.workspace_root}  |  {rel} [{lang_label}]{dirty_mark}")

    def _refresh_tree(self) -> None:
        tree = self.query_one("#file-tree", DirectoryTree)
        tree.path = self.workspace_root
        tree.reload()

    def _target_dir_for_create(self) -> Path:
        if self.selected_path is not None and self.selected_path.exists():
            if self.selected_path.is_dir():
                return self.selected_path
            return self.selected_path.parent
        if self.active_file is not None and self.active_file.parent.exists():
            return self.active_file.parent
        return self.workspace_root

    def _target_path_for_edit(self) -> Path | None:
        if (
            self.selected_path is not None
            and self.selected_path.exists()
            and self.selected_path.resolve() != self.workspace_root.resolve()
        ):
            return self.selected_path
        if self.active_file is not None and self.active_file.exists():
            return self.active_file
        return None

    def append_output(self, text: str) -> None:
        """Append text chunk to the Output panel log and internal history."""
        if not text:
            return
        self.output_history += text
        log = self.query_one("#output-log", RichLog)
        lines = text.splitlines()
        if not lines and text:
            lines = [""]
        for line in lines:
            log.write(Text.from_ansi(line))

    def load_file(self, path: Path) -> bool:
        """Load a text file into the editor, rejecting binary or oversized files."""
        resolved = path.resolve()
        try:
            content = read_text_file(resolved)
        except WorkspaceError as exc:
            self.notify(str(exc), severity="error")
            return False

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
        self._update_top_bar()
        editor.focus()
        return True

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
        self._update_top_bar()
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
            self._update_top_bar()
            self._refresh_tree()
            self.notify(f"Saved {dest.name}")
            if after_save is not None:
                after_save()

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
            if isinstance(self.focused, (TextArea, Input)):
                return
            event.prevent_default()
            event.stop()
            self.action_open_palette()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "editor" or self._suppress_dirty:
            return
        dirty_now = event.text_area.text != self._saved_content
        if dirty_now != self.is_dirty:
            self.is_dirty = dirty_now
            self._update_top_bar()

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
        self._save_current()

    def action_save_file_as(self) -> None:
        if not self._on_main_screen():
            return
        self._prompt_save_as()

    def action_run_file(self) -> None:
        if not self._on_main_screen():
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
        header = self.query_one("#output-header", Static)
        stdin_input = self.query_one("#stdin-input", Input)

        try:
            plan_preview = build_run_plan(source, self.workspace_root, source.parent)
        except RunnerError as exc:
            self.append_output(f"[Error] {exc}\n")
            self.notify(str(exc), severity="error")
            return

        self.append_output(f"$ {plan_preview.display_cmd}\n")
        header.update(f"OUTPUT [Running {source.name}... F7 /stop]")
        stdin_input.disabled = False
        stdin_input.placeholder = "Program running — type input and press Enter..."

        try:
            result = await self.runner.run(source, self.workspace_root, self.append_output)
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
            header.update("OUTPUT [Idle]")
            stdin_input.value = ""
            stdin_input.disabled = True
            stdin_input.placeholder = "Program input (enabled while running)..."

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

        def _switch(raw_path: str | None) -> None:
            if raw_path is None:
                return
            cleaned = raw_path.strip()
            if not cleaned:
                return
            candidate = Path(cleaned).expanduser()
            if not candidate.is_absolute():
                candidate = self.workspace_root / candidate
            candidate = candidate.resolve()
            if not candidate.is_dir():
                self.notify(f"Folder not found: {candidate}", severity="error")
                return

            def _do_switch() -> None:
                self.workspace_root = candidate
                self.selected_path = candidate
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
                self._refresh_tree()
                self._update_top_bar()
                self.notify(f"Opened folder {candidate}")

            self._guard_unsaved(_do_switch)

        self.push_screen(
            TextInputModal(
                title="Open Folder",
                prompt="Enter a folder path (supports ~ and ..):",
                initial=str(self.workspace_root),
                confirm_label="Open",
            ),
            _switch,
        )

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

            # Review Focus #4: Update active buffer path & language without losing edits
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
            self._update_top_bar()
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

            # Review Focus #4: Reset active file cleanly if it was deleted
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
            self._update_top_bar()
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

    def action_clear_output(self) -> None:
        if not self._on_main_screen():
            return
        self.output_history = ""
        self.query_one("#output-log", RichLog).clear()

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
