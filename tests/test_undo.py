"""Tests for the batch history and undo."""

from __future__ import annotations

from pathlib import Path

import pytest

from trashcan.core import TrashMover, undo_batch
from trashcan.journal import Journal


@pytest.fixture
def trash(tmp_path: Path) -> Path:
    root = tmp_path / "trash"
    root.mkdir()
    return root


@pytest.fixture
def desk(tmp_path: Path) -> Path:
    d = tmp_path / "desktop"
    d.mkdir()
    return d


@pytest.fixture
def journal(tmp_path: Path) -> Journal:
    return Journal(tmp_path / "journal.jsonl")


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_undo_puts_a_moved_file_back(trash: Path, desk: Path):
    src = write(desk / "notes.txt", "hello")
    batch = TrashMover(trash).process([src])

    result = undo_batch(batch)

    assert src.read_text() == "hello"
    assert not (trash / "notes.txt").exists()
    assert result.restored == 1 and not result.errors


def test_undo_restores_a_renamed_file_to_its_original_name(trash: Path, desk: Path):
    write(trash / "photo.jpg", "AAAA")
    src = write(desk / "photo.jpg", "BBBB")
    batch = TrashMover(trash).process([src])

    undo_batch(batch)

    assert src.read_text() == "BBBB"
    assert (trash / "photo.jpg").read_text() == "AAAA"
    assert not (trash / "photo (1).jpg").exists()


def test_undo_recreates_a_deleted_duplicate_from_the_kept_copy(trash: Path, desk: Path):
    write(trash / "photo.jpg", "AAAA")
    src = write(desk / "photo.jpg", "AAAA")
    batch = TrashMover(trash).process([src])
    assert not src.exists()

    result = undo_batch(batch)

    assert src.read_text() == "AAAA", "the deleted duplicate comes back byte for byte"
    assert (trash / "photo.jpg").read_text() == "AAAA", "the kept copy stays put"
    assert result.copied_back == 1


def test_undo_of_a_duplicate_refuses_when_the_kept_copy_changed(trash: Path, desk: Path):
    write(trash / "photo.jpg", "AAAA")
    src = write(desk / "photo.jpg", "AAAA")
    batch = TrashMover(trash).process([src])

    write(trash / "photo.jpg", "edited since")
    result = undo_batch(batch)

    assert not src.exists()
    assert result.errors and "changed" in result.errors[0]["error"]


def test_undo_rebuilds_the_folder_tree(trash: Path, desk: Path):
    folder = desk / "project"
    write(folder / "a.txt", "a")
    write(folder / "sub" / "b.txt", "b")
    (folder / "sub" / "empty").mkdir()
    batch = TrashMover(trash).process([folder])
    assert not folder.exists()

    result = undo_batch(batch)

    assert (folder / "a.txt").read_text() == "a"
    assert (folder / "sub" / "b.txt").read_text() == "b"
    assert (folder / "sub" / "empty").is_dir(), "even the empty folder comes back"
    assert not any(trash.iterdir())
    assert result.restored == 2 and result.dirs_restored == 3


def test_undo_does_not_overwrite_a_file_that_reappeared(trash: Path, desk: Path):
    src = write(desk / "notes.txt", "original")
    batch = TrashMover(trash).process([src])
    write(desk / "notes.txt", "something new since")

    undo_batch(batch)

    assert (desk / "notes.txt").read_text() == "something new since"
    assert (desk / "notes (1).txt").read_text() == "original"


def test_undo_reports_a_file_taken_out_of_the_trash_by_hand(trash: Path, desk: Path):
    src = write(desk / "notes.txt", "hello")
    batch = TrashMover(trash).process([src])
    (trash / "notes.txt").unlink()

    result = undo_batch(batch)

    assert result.errors and "no longer" in result.errors[0]["error"]
    assert result.restored == 0


def test_journal_round_trip(trash: Path, desk: Path, journal: Journal):
    write(trash / "photo.jpg", "AAAA")
    batch = TrashMover(trash).process([write(desk / "photo.jpg", "AAAA"), write(desk / "a.txt", "a")])
    journal.record_batch(batch)

    loaded = journal.load()

    assert len(loaded) == 1
    restored = loaded[0]
    assert restored.id == batch.id
    assert restored.moved == 1 and restored.deduped == 1
    assert restored.items[0].sha256 == batch.items[0].sha256
    assert not restored.undone


def test_undo_through_the_journal_is_remembered(trash: Path, desk: Path, journal: Journal):
    src = write(desk / "notes.txt", "hello")
    batch = TrashMover(trash).process([src])
    journal.record_batch(batch)

    stored = journal.load()[0]
    journal.undo(stored)

    reloaded = journal.load()[0]
    assert reloaded.undone and reloaded.undone_at
    assert src.exists()
    assert journal.last_undoable() is None


def test_history_lets_you_pick_an_older_batch(trash: Path, desk: Path, journal: Journal):
    first = write(desk / "first.txt", "1")
    second = write(desk / "second.txt", "2")
    mover = TrashMover(trash)
    batch_one = mover.process([first])
    batch_two = mover.process([second])
    journal.record_batch(batch_one)
    journal.record_batch(batch_two)

    # Reach past the newest batch and undo only the older one.
    target = journal.find(batch_one.id)
    assert target is not None
    journal.undo(target)

    assert first.exists(), "the chosen batch was undone"
    assert not second.exists(), "the untouched batch stays applied"
    assert (trash / "second.txt").exists()

    recent = journal.recent()
    assert [b.id for b in recent] == [batch_two.id, batch_one.id], "newest first"
    assert recent[1].undone and not recent[0].undone
    assert journal.last_undoable().id == batch_two.id


def test_batches_are_independent_when_undone_out_of_order(trash: Path, desk: Path, journal: Journal):
    write(desk / "same.txt", "v1")
    mover = TrashMover(trash)
    batch_one = mover.process([desk / "same.txt"])
    write(desk / "same.txt", "v2")
    batch_two = mover.process([desk / "same.txt"])
    journal.record_batch(batch_one)
    journal.record_batch(batch_two)
    assert sorted(p.name for p in trash.iterdir()) == ["same (1).txt", "same.txt"]

    journal.undo(journal.find(batch_one.id))
    assert (desk / "same.txt").read_text() == "v1"

    journal.undo(journal.find(batch_two.id))
    assert (desk / "same (1).txt").read_text() == "v2", "second undo sidesteps the taken name"
    assert not any(trash.iterdir())


def test_torn_journal_line_does_not_lose_the_rest(tmp_path: Path, trash: Path, desk: Path):
    journal = Journal(tmp_path / "j.jsonl")
    batch = TrashMover(trash).process([write(desk / "a.txt", "a")])
    journal.record_batch(batch)
    with open(journal.path, "a", encoding="utf-8") as handle:
        handle.write('{"type": "batch", "batch": {"id": "trunc\n')

    assert len(journal.load()) == 1


def test_history_is_never_trimmed(tmp_path: Path, trash: Path, desk: Path):
    """Every drop ever made stays undoable."""
    journal = Journal(tmp_path / "j.jsonl")
    mover = TrashMover(trash)
    ids = []
    for i in range(120):
        batch = mover.process([write(desk / f"f{i}.txt", str(i))])
        journal.record_batch(batch)
        ids.append(batch.id)

    stored = journal.load()

    assert [b.id for b in stored] == ids, "nothing should be dropped from the history"
    assert journal.find(ids[0]) is not None, "the very first drop is still undoable"


def test_the_oldest_batch_can_still_be_undone(tmp_path: Path, trash: Path, desk: Path):
    journal = Journal(tmp_path / "j.jsonl")
    mover = TrashMover(trash)
    first = write(desk / "first.txt", "the original")
    journal.record_batch(mover.process([first]))
    for i in range(50):
        journal.record_batch(mover.process([write(desk / f"later{i}.txt", str(i))]))

    journal.undo(journal.load()[0])

    assert first.read_text() == "the original"


def test_reading_the_history_is_cached(tmp_path: Path, trash: Path, desk: Path, monkeypatch):
    """The menu asks on every right-click, so re-parsing each time would drag."""
    journal = Journal(tmp_path / "j.jsonl")
    journal.record_batch(TrashMover(trash).process([write(desk / "a.txt", "a")]))
    journal.load()

    reads = []
    real_read = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (reads.append(self), real_read(self, *a, **k))[1])

    journal.load()
    journal.load()
    assert reads == [], "an unchanged history should not be parsed again"

    journal.record_batch(TrashMover(trash).process([write(desk / "b.txt", "b")]))
    assert len(journal.load()) == 2, "a new batch must invalidate the cache"
    assert len(reads) == 1


def test_clear_forgets_the_history_without_touching_files(tmp_path: Path, trash: Path, desk: Path):
    journal = Journal(tmp_path / "j.jsonl")
    journal.record_batch(TrashMover(trash).process([write(desk / "a.txt", "a")]))
    assert journal.load()

    journal.clear()

    assert journal.load() == []
    assert (trash / "a.txt").exists(), "clearing the log must not delete anything"


def test_empty_journal_reads_clean(tmp_path: Path):
    journal = Journal(tmp_path / "missing.jsonl")
    assert journal.load() == [] and journal.last_undoable() is None


def test_recent_returns_everything_unless_asked_to_limit(tmp_path: Path, trash: Path, desk: Path):
    journal = Journal(tmp_path / "j.jsonl")
    mover = TrashMover(trash)
    for i in range(30):
        journal.record_batch(mover.process([write(desk / f"f{i}.txt", str(i))]))

    assert len(journal.recent()) == 30
    assert len(journal.recent(5)) == 5


def test_a_failed_move_leaves_no_half_written_file(trash: Path, desk: Path, monkeypatch):
    """A cross-drive move copies then deletes; a failure must not leave a stub."""
    src = write(desk / "big.bin", "x" * 1000)

    def dies_midway(source, dest, *a, **k):
        Path(dest).write_text("x" * 400)  # partial copy landed
        raise OSError("device disconnected")

    monkeypatch.setattr("trashcan.core.shutil.move", dies_midway)
    batch = TrashMover(trash).process([src])

    assert batch.errors and batch.moved == 0
    assert src.read_text() == "x" * 1000, "the original is untouched"
    assert not (trash / "big.bin").exists(), "the half-written file must be cleared away"


def test_a_failed_move_does_not_poison_the_next_drop(trash: Path, desk: Path, monkeypatch):
    src = write(desk / "big.bin", "x" * 1000)

    def dies_midway(source, dest, *a, **k):
        Path(dest).write_text("x" * 400)
        raise OSError("device disconnected")

    monkeypatch.setattr("trashcan.core.shutil.move", dies_midway)
    TrashMover(trash).process([src])
    monkeypatch.undo()

    batch = TrashMover(trash).process([src])

    assert batch.moved == 1 and batch.renamed == 0, "it should file under the real name"
    assert (trash / "big.bin").read_text() == "x" * 1000
    assert not (trash / "big (1).bin").exists()


def test_a_partly_failed_undo_can_be_tried_again(trash: Path, desk: Path, journal: Journal):
    a = write(desk / "a.txt", "A")
    b = write(desk / "b.txt", "B")
    batch = TrashMover(trash).process([a, b])
    journal.record_batch(batch)

    # Something outside our control holds one file back.
    held = trash / "a.txt"
    stashed = trash / "held-elsewhere"
    held.rename(stashed)

    first = journal.undo(journal.load()[0])
    assert first.errors and first.restored == 1
    stored = journal.load()[0]
    assert not stored.undone, "a partial undo must not close the batch off"
    assert stored.partly_undone
    assert journal.last_undoable() is not None, "it stays available to retry"

    # The obstruction clears, so the user tries again.
    stashed.rename(held)
    second = journal.undo(journal.load()[0])

    assert a.read_text() == "A", "the item that failed came back on the retry"
    assert not second.errors
    assert second.skipped == 1, "the file already put back is skipped, not re-reported"
    assert journal.load()[0].undone


def test_a_clean_undo_is_still_final(trash: Path, desk: Path, journal: Journal):
    src = write(desk / "a.txt", "A")
    journal.record_batch(TrashMover(trash).process([src]))

    result = journal.undo(journal.load()[0])

    assert not result.errors
    stored = journal.load()[0]
    assert stored.undone and not stored.partly_undone
    assert journal.last_undoable() is None
