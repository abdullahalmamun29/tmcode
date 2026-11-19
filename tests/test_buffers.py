"""Unit tests for multi-tab and split-pane buffer management."""

from pathlib import Path

from termcode.buffers import EditorPaneState, OpenBuffer


def test_open_switch_deduplicate_and_close_tabs(tmp_path: Path) -> None:
    pane = EditorPaneState()
    f1 = (tmp_path / "a.py").resolve()
    f2 = (tmp_path / "b.c").resolve()

    b1 = pane.open_file(f1, "x = 1\n")
    assert pane.active_buffer is b1
    assert len(pane.tabs) == 1

    b2 = pane.open_file(f2, "int main(){}\n")
    assert pane.active_buffer is b2
    assert len(pane.tabs) == 2

    # Re-opening f1 focuses existing tab instead of duplicating
    b1_again = pane.open_file(f1, "ignored")
    assert b1_again is b1
    assert pane.active_index == 0
    assert len(pane.tabs) == 2

    # Cycle next tab
    pane.next_tab()
    assert pane.active_buffer is b2

    # Close active tab falls back cleanly to remaining tab
    pane.close_tab(1)
    assert len(pane.tabs) == 1
    assert pane.active_buffer is b1


def test_dirty_tracking_and_rename_in_pane(tmp_path: Path) -> None:
    pane = EditorPaneState()
    f1 = (tmp_path / "note.txt").resolve()
    f2 = (tmp_path / "script.py").resolve()

    buf = pane.open_file(f1, "hello\n")
    assert not buf.is_dirty
    buf.content = "hello world\n"
    assert buf.is_dirty

    pane.handle_rename(f1, f2)
    assert buf.path == f2
    assert buf.content == "hello world\n"
    assert buf.is_dirty


def test_merge_panes_on_unsplit_preserves_unsaved_buffers(tmp_path: Path) -> None:
    left = EditorPaneState()
    right = EditorPaneState()
    f1 = (tmp_path / "a.py").resolve()
    f2 = (tmp_path / "b.py").resolve()

    left.open_file(f1, "a = 1\n")
    b2 = right.open_file(f2, "b = 1\n")
    b2.content = "b = 2\n"

    left.merge_from(right)
    assert len(left.tabs) == 2
    assert left.find_by_path(f2) is b2
    assert b2.is_dirty
