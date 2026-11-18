"""Slash command registry and filtering for TermCode."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandSpec:
    command: str
    action: str
    shortcut: str
    binding: str
    description: str


COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec(
        command="/run",
        action="run_file",
        shortcut="F5",
        binding="f5",
        description="Save and run active file in Output panel",
    ),
    CommandSpec(
        command="/stop",
        action="stop_run",
        shortcut="F7",
        binding="f7",
        description="Stop currently running program",
    ),
    CommandSpec(
        command="/save",
        action="save_file",
        shortcut="Ctrl+S",
        binding="ctrl+s",
        description="Save active file",
    ),
    CommandSpec(
        command="/save-as",
        action="save_file_as",
        shortcut="F8",
        binding="f8",
        description="Save current buffer under a new name",
    ),
    CommandSpec(
        command="/new-file",
        action="new_file",
        shortcut="Ctrl+N",
        binding="ctrl+n",
        description="Create a new file in the selected folder",
    ),
    CommandSpec(
        command="/new-folder",
        action="new_folder",
        shortcut="F3",
        binding="f3",
        description="Create a new folder in the selected folder",
    ),
    CommandSpec(
        command="/open-file",
        action="open_file",
        shortcut="Ctrl+P",
        binding="ctrl+p",
        description="Quick-select and open a file in this folder",
    ),
    CommandSpec(
        command="/open-folder",
        action="open_folder",
        shortcut="Ctrl+O",
        binding="ctrl+o",
        description="Switch to another folder as workspace root",
    ),
    CommandSpec(
        command="/rename",
        action="rename_item",
        shortcut="F2",
        binding="f2",
        description="Rename the selected file or folder",
    ),
    CommandSpec(
        command="/delete",
        action="delete_item",
        shortcut="F4",
        binding="f4",
        description="Delete the selected file or folder",
    ),
    CommandSpec(
        command="/toggle-sidebar",
        action="toggle_sidebar",
        shortcut="Ctrl+B",
        binding="ctrl+b",
        description="Show or hide the file explorer sidebar",
    ),
    CommandSpec(
        command="/clear-output",
        action="clear_output",
        shortcut="Ctrl+L",
        binding="ctrl+l",
        description="Clear the Output panel",
    ),
    CommandSpec(
        command="/quit",
        action="request_quit",
        shortcut="Ctrl+Q",
        binding="ctrl+q",
        description="Exit TermCode",
    ),
)


def filter_commands(query: str) -> list[CommandSpec]:
    """Filter commands by leading-slash-optional substring match on command or description."""
    cleaned = query.strip().lower()
    if cleaned.startswith("/"):
        cleaned = cleaned[1:]
    if not cleaned:
        return list(COMMANDS)

    prefix_matches: list[CommandSpec] = []
    other_matches: list[CommandSpec] = []
    for spec in COMMANDS:
        cmd_body = spec.command[1:].lower()
        haystack = f"{cmd_body} {spec.description.lower()} {spec.shortcut.lower()}"
        if cmd_body.startswith(cleaned):
            prefix_matches.append(spec)
        elif cleaned in haystack:
            other_matches.append(spec)
    return prefix_matches + other_matches
