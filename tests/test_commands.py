"""Unit tests for TermCode slash command registry and filtering."""

from termcode.commands import COMMANDS, filter_commands


def test_all_spec_commands_present_with_unique_shortcuts() -> None:
    names = {c.command for c in COMMANDS}
    assert names == {
        "/run",
        "/run-in-terminal",
        "/shell",
        "/stop",
        "/save",
        "/save-as",
        "/new-file",
        "/new-folder",
        "/open-file",
        "/open-folder",
        "/rename",
        "/delete",
        "/toggle-sidebar",
        "/toggle-terminal",
        "/toggle-hidden",
        "/terminal-grow",
        "/terminal-shrink",
        "/copy-output",
        "/clear-output",
        "/next-tab",
        "/close-tab",
        "/split-editor",
        "/find",
        "/replace",
        "/goto-line",
        "/format-file",
        "/format-on-save",
        "/clean-build",
        "/go-to-definition",
        "/go-back",
        "/info",
        "/problems",
        "/language-server",
        "/toggle-comment",
        "/word-wrap",
        "/quit",
    }

    bindings = [c.binding for c in COMMANDS if c.binding]
    assert len(bindings) == len(set(bindings))

    # Must never collide with basic editor keys or terminal interrupt signals
    forbidden = {"ctrl+a", "ctrl+c", "ctrl+v", "ctrl+x", "ctrl+z", "ctrl+y", "ctrl+d"}
    assert forbidden.isdisjoint(set(bindings))


def test_filter_commands_with_and_without_leading_slash() -> None:
    assert any(c.command == "/run" for c in filter_commands("/run"))
    assert any(c.command == "/run" for c in filter_commands("run"))
    assert any(c.command == "/new-file" for c in filter_commands("new"))
    assert len(filter_commands("")) == len(COMMANDS)
    assert len(filter_commands("/")) == len(COMMANDS)
    assert filter_commands("zzzz-no-match") == []
