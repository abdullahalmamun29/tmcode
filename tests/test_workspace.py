"""Unit tests for TermCode workspace filesystem operations."""

from pathlib import Path
import pytest

from termcode.workspace import (
    BinaryFileError,
    FileTooLargeError,
    WorkspaceError,
    create_file,
    create_folder,
    delete_path,
    list_workspace_files,
    read_text_file,
    rename_path,
    write_text_file,
)


def test_read_and_write_text_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "hello.py"
    write_text_file(target, "print('hi')\n")
    assert read_text_file(target) == "print('hi')\n"


def test_read_rejects_binary_and_invalid_utf8(tmp_path: Path) -> None:
    bin_file = tmp_path / "a.out"
    bin_file.write_bytes(b"\x7fELF\x00\x01\x02")
    with pytest.raises(BinaryFileError):
        read_text_file(bin_file)

    bad_utf8 = tmp_path / "bad.txt"
    bad_utf8.write_bytes(b"\xff\xfe\xfd")
    with pytest.raises(BinaryFileError):
        read_text_file(bad_utf8)


def test_read_rejects_file_over_size_limit(tmp_path: Path) -> None:
    big = tmp_path / "big.txt"
    big.write_bytes(b"a" * 100)
    with pytest.raises(FileTooLargeError):
        read_text_file(big, max_bytes=50)


def test_create_file_and_nested_folder(tmp_path: Path) -> None:
    nested = create_file(tmp_path, "utils/math.py")
    assert nested == tmp_path / "utils" / "math.py"
    assert nested.is_file()

    folder = create_folder(tmp_path, "src/core")
    assert folder == tmp_path / "src" / "core"
    assert folder.is_dir()


def test_create_and_rename_reject_empty_or_existing_names(tmp_path: Path) -> None:
    f1 = create_file(tmp_path, "a.py")
    f2 = create_file(tmp_path, "b.py")

    with pytest.raises(WorkspaceError):
        create_file(tmp_path, "   ")
    with pytest.raises(WorkspaceError):
        create_file(tmp_path, "a.py")
    with pytest.raises(WorkspaceError):
        create_folder(tmp_path, "")
    with pytest.raises(WorkspaceError):
        create_folder(tmp_path, "a.py")
    with pytest.raises(WorkspaceError):
        rename_path(f1, "b.py")
    with pytest.raises(WorkspaceError):
        rename_path(f1, "  ")
    with pytest.raises(WorkspaceError):
        rename_path(f1, "sub/evil.py")
    assert f2.exists()


def test_rename_and_delete_file_and_folder(tmp_path: Path) -> None:
    f = create_file(tmp_path, "note.txt")
    write_text_file(f, "x = 1\n")
    renamed = rename_path(f, "script.py")
    assert renamed == tmp_path / "script.py"
    assert not f.exists()
    assert read_text_file(renamed) == "x = 1\n"

    d = create_folder(tmp_path, "sub")
    create_file(d, "inner.c")
    delete_path(d)
    assert not d.exists()

    delete_path(renamed)
    assert not renamed.exists()


def test_list_workspace_files_skips_ignored_dirs(tmp_path: Path) -> None:
    create_file(tmp_path, "main.py")
    create_file(tmp_path, "src/app.cpp")
    create_file(tmp_path, ".git/config")
    create_file(tmp_path, ".venv/bin/python")
    create_file(tmp_path, "__pycache__/main.pyc")

    found = [p.relative_to(tmp_path).as_posix() for p in list_workspace_files(tmp_path)]
    assert found == ["main.py", "src/app.cpp"]
