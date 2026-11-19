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
        command="/run-in-terminal",
        action="run_in_terminal",
        shortcut="Shift+F5",
        binding="shift+f5",
        description="Run with the full terminal (colors, curses, key input)",
    ),
    CommandSpec(
        command="/shell",
        action="open_shell",
        shortcut="Ctrl+T",
        binding="ctrl+t",
        description="Open your shell here; type exit to come back",
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
        description="Show or hide the Explorer sidebar",
    ),
    CommandSpec(
        command="/toggle-terminal",
        action="toggle_terminal",
        shortcut="Ctrl+J",
        binding="ctrl+j",
        description="Show or hide the bottom Output panel",
    ),
    CommandSpec(
        command="/toggle-hidden",
        action="toggle_hidden",
        shortcut="F9",
        binding="f9",
        description="Show or hide hidden dotfiles in Explorer",
    ),
    CommandSpec(
        command="/terminal-grow",
        action="terminal_grow",
        shortcut="Ctrl+Up",
        binding="ctrl+up",
        description="Increase Output panel height",
    ),
    CommandSpec(
        command="/terminal-shrink",
        action="terminal_shrink",
        shortcut="Ctrl+Down",
        binding="ctrl+down",
        description="Decrease Output panel height",
    ),
    CommandSpec(
        command="/copy-output",
        action="copy_output",
        shortcut="F10",
        binding="f10",
        description="Copy selected or full Output panel text",
    ),
    CommandSpec(
        command="/clear-output",
        action="clear_output",
        shortcut="Ctrl+L",
        binding="ctrl+l",
        description="Clear the Output panel text",
    ),
    CommandSpec(
        command="/next-tab",
        action="next_tab",
        shortcut="F11",
        binding="f11",
        description="Switch to the next open file tab",
    ),
    CommandSpec(
        command="/close-tab",
        action="close_tab",
        shortcut="Ctrl+W",
        binding="ctrl+w",
        description="Close the current file tab",
    ),
    CommandSpec(
        command="/split-editor",
        action="toggle_split",
        shortcut="F12",
        binding="f12",
        description="Toggle side-by-side split editor",
    ),
    CommandSpec(
        command="/find",
        action="find",
        shortcut="Ctrl+F",
        binding="ctrl+f",
        description="Find in the current file",
    ),
    CommandSpec(
        command="/replace",
        action="replace",
        shortcut="Ctrl+R",
        binding="ctrl+r",
        description="Find and replace in the current file",
    ),
    CommandSpec(
        command="/goto-line",
        action="goto_line",
        shortcut="Ctrl+G",
        binding="ctrl+g",
        description="Jump to a line (and column)",
    ),
    CommandSpec(
        command="/format-file",
        action="format_file",
        shortcut="F6",
        binding="f6",
        description="Fix indentation in the current file now",
    ),
    CommandSpec(
        command="/format-on-save",
        action="toggle_format_on_save",
        shortcut="",
        binding="",
        description="Turn formatting on save (and run) on or off",
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
