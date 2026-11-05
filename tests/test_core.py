"""Tests for the move/dedupe/flatten rules."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trashcan.core import DEDUPE, MOVE, TrashMover, numbered_names, remove_empty_dirs


@pytest.fixture
def trash(tmp_path: Path) -> Path:
    root = tmp_path / "trash keep for duplicates"
    root.mkdir()
    return root


@pytest.fixture
def desk(tmp_path: Path) -> Path:
    d = tmp_path / "desktop"
    d.mkdir()
    return d


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_numbered_names_follow_explorer_style():
    names = list(x for _, x in zip(range(3), numbered_names("report.txt")))
    assert names == ["report.txt", "report (1).txt", "report (2).txt"]

    no_ext = list(x for _, x in zip(range(2), numbered_names("README")))
    assert no_ext == ["README", "README (1)"]


def test_plain_file_is_moved(trash: Path, desk: Path):
    src = write(desk / "notes.txt", "hello")
    batch = TrashMover(trash).process([src])

    assert not src.exists()
    assert (trash / "notes.txt").read_text() == "hello"
    assert batch.moved == 1 and batch.deduped == 0 and not batch.errors


def test_same_name_identical_bytes_deletes_the_drop(trash: Path, desk: Path):
    write(trash / "photo.jpg", "AAAA")
    src = write(desk / "photo.jpg", "AAAA")

    batch = TrashMover(trash).process([src])

    assert not src.exists(), "the duplicate should be deleted, not copied"
    assert [p.name for p in trash.iterdir()] == ["photo.jpg"]
    assert batch.deduped == 1 and batch.moved == 0
    assert batch.items[0].action == DEDUPE
    assert batch.items[0].sha256


def test_same_name_different_bytes_is_renamed(trash: Path, desk: Path):
    write(trash / "photo.jpg", "AAAA")
    src = write(desk / "photo.jpg", "BBBB")

    batch = TrashMover(trash).process([src])

    assert (trash / "photo.jpg").read_text() == "AAAA"
    assert (trash / "photo (1).jpg").read_text() == "BBBB"
    assert batch.moved == 1 and batch.renamed == 1
    assert batch.items[0].action == MOVE


def test_same_size_different_bytes_is_not_treated_as_duplicate(trash: Path, desk: Path):
    write(trash / "a.bin", "1234")
    src = write(desk / "a.bin", "5678")

    TrashMover(trash).process([src])

    assert (trash / "a.bin").read_text() == "1234"
    assert (trash / "a (1).bin").read_text() == "5678"


def test_different_names_with_identical_bytes_are_both_kept(trash: Path, desk: Path):
    write(trash / "one.txt", "same")
    src = write(desk / "two.txt", "same")

    batch = TrashMover(trash).process([src])

    assert (trash / "one.txt").exists() and (trash / "two.txt").exists()
    assert batch.deduped == 0, "the binary check only applies to matching names"


def test_numbered_variant_is_also_binary_checked(trash: Path, desk: Path):
    write(trash / "doc.txt", "version one")
    write(trash / "doc (1).txt", "version two")
    src = write(desk / "doc.txt", "version two")

    batch = TrashMover(trash).process([src])

    assert not src.exists()
    assert batch.deduped == 1
    assert sorted(p.name for p in trash.iterdir()) == ["doc (1).txt", "doc.txt"]


def test_numbered_variant_check_can_be_turned_off(trash: Path, desk: Path):
    write(trash / "doc.txt", "version one")
    write(trash / "doc (1).txt", "version two")
    src = write(desk / "doc.txt", "version two")

    batch = TrashMover(trash, dedupe_numbered_variants=False).process([src])

    assert batch.moved == 1
    assert (trash / "doc (2).txt").read_text() == "version two"


def test_folder_is_flattened_and_emptied(trash: Path, desk: Path):
    folder = desk / "project"
    write(folder / "a.txt", "a")
    write(folder / "sub" / "b.txt", "b")
    write(folder / "sub" / "deep" / "c.txt", "c")
    (folder / "sub" / "empty").mkdir()

    batch = TrashMover(trash).process([folder])

    assert sorted(p.name for p in trash.iterdir()) == ["a.txt", "b.txt", "c.txt"]
    assert not folder.exists(), "the drained folder tree should be gone"
    assert batch.moved == 3
    assert str(folder) in batch.removed_dirs


def test_flatten_collisions_inside_one_folder(trash: Path, desk: Path):
    folder = desk / "photos"
    write(folder / "one" / "img.jpg", "X")
    write(folder / "two" / "img.jpg", "X")  # identical
    write(folder / "three" / "img.jpg", "Y")  # different

    batch = TrashMover(trash).process([folder])

    assert sorted(p.name for p in trash.iterdir()) == ["img (1).jpg", "img.jpg"]
    assert batch.moved == 2 and batch.deduped == 1
    assert not folder.exists()


def test_folder_with_only_empty_dirs_is_removed(trash: Path, desk: Path):
    folder = desk / "hollow"
    (folder / "x" / "y").mkdir(parents=True)

    batch = TrashMover(trash).process([folder])

    assert not folder.exists()
    assert len(batch.removed_dirs) == 3


def test_dropping_the_trash_folder_itself_is_refused(trash: Path):
    write(trash / "keep.txt", "keep")

    batch = TrashMover(trash).process([trash])

    assert (trash / "keep.txt").exists()
    assert batch.errors and "already inside" in batch.errors[0]["error"]


def test_dropping_a_file_from_inside_the_trash_is_refused(trash: Path):
    src = write(trash / "keep.txt", "keep")

    batch = TrashMover(trash).process([src])

    assert src.exists() and batch.moved == 0 and batch.errors


def test_missing_path_is_reported_not_raised(trash: Path, desk: Path):
    batch = TrashMover(trash).process([desk / "ghost.txt"])

    assert batch.errors and batch.moved == 0


def test_one_bad_file_does_not_stop_the_batch(trash: Path, desk: Path, monkeypatch):
    good = write(desk / "good.txt", "g")
    bad = write(desk / "bad.txt", "b")

    real_move = __import__("shutil").move

    def flaky(src, dst, *a, **kw):
        if Path(src).name == "bad.txt":
            raise OSError("locked by another program")
        return real_move(src, dst, *a, **kw)

    monkeypatch.setattr("trashcan.core.shutil.move", flaky)
    batch = TrashMover(trash).process([good, bad])

    assert (trash / "good.txt").exists()
    assert bad.exists()
    assert batch.moved == 1 and len(batch.errors) == 1


def test_trash_root_is_created_on_demand(tmp_path: Path, desk: Path):
    root = tmp_path / "brand" / "new" / "trash"
    src = write(desk / "f.txt", "f")

    TrashMover(root).process([src])

    assert (root / "f.txt").exists()


def test_duplicate_paths_in_one_drop_are_only_handled_once(trash: Path, desk: Path):
    src = write(desk / "dup.txt", "d")

    batch = TrashMover(trash).process([src, src])

    assert batch.moved == 1 and not batch.errors


def test_progress_and_cancel(trash: Path, desk: Path):
    import threading

    for i in range(5):
        write(desk / f"f{i}.txt", str(i))
    seen: list[tuple[int, int]] = []
    cancel = threading.Event()

    def progress(done: int, total: int, name: str) -> None:
        seen.append((done, total))
        if done == 2:
            cancel.set()

    batch = TrashMover(trash, progress=progress, cancel=cancel).process(sorted(desk.iterdir()))

    assert seen[0] == (1, 5)
    assert batch.cancelled and batch.moved == 2


def test_remove_empty_dirs_leaves_non_empty_alone(tmp_path: Path):
    root = tmp_path / "r"
    write(root / "keep" / "file.txt", "x")
    (root / "gone").mkdir()

    removed = remove_empty_dirs(root)

    assert str(root / "gone") in removed
    assert (root / "keep" / "file.txt").exists()
    assert root.exists()


def test_symlink_is_moved_without_following_it(trash: Path, desk: Path):
    real = write(desk / "real.txt", "important")
    link = desk / "link.txt"
    link.symlink_to(real)

    batch = TrashMover(trash).process([link])

    assert real.read_text() == "important", "the link's target must be left alone"
    assert not link.exists()
    assert (trash / "link.txt").is_symlink()
    assert batch.moved == 1


def test_broken_symlink_does_not_break_the_batch(trash: Path, desk: Path):
    broken = desk / "broken.txt"
    broken.symlink_to(desk / "gone.txt")
    good = write(desk / "good.txt", "g")

    batch = TrashMover(trash).process([broken, good])

    assert (trash / "good.txt").exists()
    assert batch.moved == 2 and not batch.errors


def test_a_directory_holding_the_name_forces_a_rename(trash: Path, desk: Path):
    (trash / "notes.txt").mkdir()
    src = write(desk / "notes.txt", "n")

    TrashMover(trash).process([src])

    assert (trash / "notes.txt").is_dir()
    assert (trash / "notes (1).txt").read_text() == "n"


def test_confirm_callback_can_abandon_a_drop(trash: Path, desk: Path):
    src = write(desk / "big.txt", "x")
    seen: list[int] = []

    batch = TrashMover(trash).process([src], confirm=lambda n: seen.append(n) or False)

    assert seen == [1]
    assert src.exists() and batch.cancelled and not batch.items
