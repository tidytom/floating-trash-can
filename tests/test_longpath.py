"""Tests for Windows extended-length path handling.

The string rules are checked with Windows mode forced on, so they are covered
even when the tests run elsewhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trashcan import longpath
from trashcan.core import TrashMover, undo_batch


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(longpath, "WINDOWS", True)


def test_drive_path_gets_the_prefix(windows):
    assert longpath.extended_str(r"C:\Users\me\file.txt") == "\\\\?\\C:\\Users\\me\\file.txt"


def test_unc_path_uses_the_unc_form(windows):
    assert longpath.extended_str(r"\\server\share\file.txt") == "\\\\?\\UNC\\server\\share\\file.txt"


def test_an_already_extended_path_is_left_alone(windows):
    already = "\\\\?\\C:\\x\\y.txt"
    assert longpath.extended_str(already) == already


def test_device_paths_are_left_alone(windows):
    device = "\\\\.\\PhysicalDrive0"
    assert longpath.extended_str(device) == device


def test_dot_segments_are_normalised_away(windows):
    """The prefix switches off Win32 path parsing, so \"..\" must be gone."""
    assert longpath.extended_str(r"C:\a\b\..\c\.\d.txt") == "\\\\?\\C:\\a\\c\\d.txt"


def test_forward_slashes_become_backslashes(windows):
    assert longpath.extended_str("C:/a/b.txt") == "\\\\?\\C:\\a\\b.txt"


def test_plain_undoes_extended(windows):
    for original in (r"C:\Users\me\a.txt", r"\\server\share\a.txt"):
        assert longpath.plain_str(longpath.extended_str(original)) == original


def test_plain_leaves_an_ordinary_path_alone(windows):
    assert longpath.plain_str(r"C:\a\b.txt") == r"C:\a\b.txt"


def test_empty_string_is_safe(windows):
    assert longpath.extended_str("") == ""
    assert longpath.plain_str("") == ""


def test_a_path_well_over_260_characters(windows):
    deep = "C:\\" + "\\".join(f"folder{i:03d}" for i in range(30)) + "\\document.txt"
    assert len(deep) > 260

    extended = longpath.extended_str(deep)

    assert extended.startswith("\\\\?\\")
    assert longpath.plain_str(extended) == deep


def test_everything_is_a_no_op_off_windows(monkeypatch):
    monkeypatch.setattr(longpath, "WINDOWS", False)
    path = "/home/me/some/file.txt"

    assert longpath.extended_str(path) == path
    assert longpath.extended(path) == Path(path)
    assert longpath.display(path) == path


def test_a_deeply_nested_drop_and_undo(tmp_path: Path):
    """End to end on a path long enough to break plain Win32 calls."""
    trash = tmp_path / "trash"
    trash.mkdir()
    deep = tmp_path / "start"
    for i in range(20):
        deep = deep / f"a-long-folder-name-{i:02d}"
    deep.mkdir(parents=True)
    source = deep / "deeply-buried-document.txt"
    source.write_text("still reachable")
    assert len(str(source)) > 300, "the test path must exceed the old limit"

    batch = TrashMover(trash).process([tmp_path / "start"])

    assert batch.moved == 1 and not batch.errors
    assert (trash / "deeply-buried-document.txt").read_text() == "still reachable"
    assert not (tmp_path / "start").exists()

    result = undo_batch(batch)

    assert not result.errors
    assert source.read_text() == "still reachable", "it goes all the way back"


def test_recorded_paths_stay_readable(tmp_path: Path):
    """The undo history must not be full of \\\\?\\ noise."""
    trash = tmp_path / "trash"
    trash.mkdir()
    source = tmp_path / "note.txt"
    source.write_text("n")

    batch = TrashMover(trash).process([source])

    assert not batch.items[0].src.startswith("\\\\?\\")
    assert not batch.items[0].dst.startswith("\\\\?\\")
    assert not batch.trash_root.startswith("\\\\?\\")
