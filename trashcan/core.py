"""File handling for the trash can: flatten folders, dedupe, move, undo.

Deliberately GUI free so it can be tested (and scripted) on its own.

Rules implemented here:

* A dropped file is moved into the trash root.
* A dropped folder is flattened: every file inside it, at any depth, is moved
  into the trash root, then the folders it left behind are deleted.
* If the destination name is already taken, the two files are compared byte for
  byte (cheap size check first, then sha256).  Identical means the drop is a
  duplicate, so the source is deleted instead of copied.  Different means the
  drop is filed as "name (1).ext".
* Every batch is recorded item by item so it can be undone later.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path, PurePath
from typing import Callable, Iterable, Iterator, Sequence

from .longpath import display, extended

CHUNK = 1024 * 1024
MAX_NAME_VARIANTS = 10000

# Item actions
MOVE = "move"
DEDUPE = "dedupe"

ProgressFn = Callable[[int, int, str], None]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_of(path: Path, chunk: int = CHUNK) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


class _Fingerprint:
    """Size + hash of a file, computed at most once."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.size = path.stat().st_size
        self._sha: str | None = None

    @property
    def sha(self) -> str:
        if self._sha is None:
            self._sha = sha256_of(self.path)
        return self._sha

    def matches(self, other: Path) -> bool:
        """True when *other* holds exactly the same bytes."""
        try:
            if other.stat().st_size != self.size:
                return False
            return sha256_of(other) == self.sha
        except OSError:
            return False


def numbered_names(name: str, limit: int = MAX_NAME_VARIANTS) -> Iterator[str]:
    """"report.txt", "report (1).txt", "report (2).txt", ... like Explorer."""
    pure = PurePath(name)
    stem, suffix = pure.stem, pure.suffix
    yield name
    for index in range(1, limit + 1):
        yield f"{stem} ({index}){suffix}"


@dataclass
class Item:
    """One file's worth of work, and everything undo needs to reverse it."""

    action: str  # MOVE or DEDUPE
    src: str  # where the file came from
    dst: str  # where it lives now (for DEDUPE: the identical file we kept)
    size: int = 0
    sha256: str | None = None  # recorded for DEDUPE, so undo can verify
    renamed: bool = False  # filed under a "(n)" name because of a clash

    @classmethod
    def from_dict(cls, raw: dict) -> "Item":
        return cls(
            action=str(raw.get("action", MOVE)),
            src=str(raw.get("src", "")),
            dst=str(raw.get("dst", "")),
            size=int(raw.get("size", 0) or 0),
            sha256=raw.get("sha256"),
            renamed=bool(raw.get("renamed", False)),
        )


@dataclass
class Batch:
    """One drop.  The item list is the before/after snapshot undo replays."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started_at: str = field(default_factory=_now)
    finished_at: str = ""
    trash_root: str = ""
    sources: list[str] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    removed_dirs: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    cancelled: bool = False
    # Filled in by the journal from later "undo" records, not by the mover.
    undone: bool = False
    undone_at: str = ""
    # An undo that hit problems leaves the batch here: some items came back,
    # some did not, and it stays available to try again.
    partly_undone: bool = False

    @property
    def moved(self) -> int:
        return sum(1 for i in self.items if i.action == MOVE)

    @property
    def deduped(self) -> int:
        return sum(1 for i in self.items if i.action == DEDUPE)

    @property
    def renamed(self) -> int:
        return sum(1 for i in self.items if i.renamed)

    def summary(self) -> str:
        bits = []
        if self.moved:
            bits.append(f"{self.moved} moved")
        if self.renamed:
            bits.append(f"{self.renamed} renamed")
        if self.deduped:
            bits.append(f"{self.deduped} duplicate{'s' if self.deduped != 1 else ''} deleted")
        if self.removed_dirs:
            bits.append(f"{len(self.removed_dirs)} empty folder{'s' if len(self.removed_dirs) != 1 else ''} removed")
        if self.errors:
            bits.append(f"{len(self.errors)} failed")
        if self.cancelled:
            bits.append("cancelled")
        return ", ".join(bits) or "nothing to do"

    def to_dict(self) -> dict:
        raw = asdict(self)
        for runtime_only in ("undone", "undone_at", "partly_undone"):
            raw.pop(runtime_only, None)
        return raw

    @classmethod
    def from_dict(cls, raw: dict) -> "Batch":
        batch = cls(
            id=str(raw.get("id", "")),
            started_at=str(raw.get("started_at", "")),
            finished_at=str(raw.get("finished_at", "")),
            trash_root=str(raw.get("trash_root", "")),
            sources=[str(s) for s in raw.get("sources", [])],
            items=[Item.from_dict(i) for i in raw.get("items", [])],
            removed_dirs=[str(d) for d in raw.get("removed_dirs", [])],
            errors=list(raw.get("errors", [])),
            cancelled=bool(raw.get("cancelled", False)),
        )
        return batch


@dataclass
class UndoResult:
    batch_id: str = ""
    restored: int = 0
    copied_back: int = 0
    dirs_restored: int = 0
    skipped: int = 0
    errors: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        bits = []
        if self.restored:
            bits.append(f"{self.restored} put back")
        if self.copied_back:
            bits.append(f"{self.copied_back} duplicate{'s' if self.copied_back != 1 else ''} restored")
        if self.dirs_restored:
            bits.append(f"{self.dirs_restored} folder{'s' if self.dirs_restored != 1 else ''} recreated")
        if self.skipped:
            bits.append(f"{self.skipped} skipped")
        if self.errors:
            bits.append(f"{len(self.errors)} failed")
        return ", ".join(bits) or "nothing to undo"


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _unique_path(path: Path) -> Path:
    """First free "name (n).ext" at *path*, used when undo finds the spot taken."""
    if not path.exists():
        return path
    for candidate in numbered_names(path.name):
        target = path.with_name(candidate)
        if not target.exists():
            return target
    raise OSError(f"no free name near {path}")


def remove_empty_dirs(root: Path) -> list[str]:
    """Delete empty folders under (and including) *root*, deepest first."""
    removed: list[str] = []
    if not root.is_dir():
        return removed
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        directory = Path(dirpath)
        try:
            next(directory.iterdir())
        except StopIteration:
            try:
                directory.rmdir()
                removed.append(display(directory))
            except OSError:
                pass
        except OSError:
            pass
    return removed


class TrashMover:
    """Runs drops against a trash root and reports what it did."""

    def __init__(
        self,
        trash_root: Path,
        dedupe_numbered_variants: bool = True,
        progress: ProgressFn | None = None,
        cancel: threading.Event | None = None,
    ) -> None:
        # Extended form for every filesystem call, so a path over the 260
        # character Windows limit still works.
        self.trash_root = extended(trash_root)
        self.dedupe_numbered_variants = dedupe_numbered_variants
        self.progress = progress
        self.cancel = cancel or threading.Event()

    # -- planning ---------------------------------------------------------

    def collect(self, paths: Iterable[str | os.PathLike]) -> tuple[list[Path], list[Path], list[dict]]:
        """Expand a drop into (files to move, folders to clean up, errors)."""
        files: list[Path] = []
        folders: list[Path] = []
        errors: list[dict] = []
        seen: set[Path] = set()

        for raw in paths:
            try:
                path = extended(raw)
                # Re-extend afterwards: resolve() can hand back a plain path.
                resolved = extended(path.resolve())
            except OSError as exc:
                errors.append({"path": display(raw), "error": str(exc)})
                continue
            if resolved in seen:
                continue
            seen.add(resolved)

            root = extended(self.trash_root.resolve()) if self.trash_root.exists() else self.trash_root
            if resolved == root or _is_within(root, resolved) or _is_within(resolved, root):
                errors.append({"path": display(path), "error": "already inside the trash folder - skipped"})
                continue

            if path.is_symlink():
                # Move shortcuts and junctions as they are; never follow them
                # and drag someone else's files along.
                files.append(path.absolute())
            elif resolved.is_file():
                files.append(resolved)
            elif resolved.is_dir():
                folders.append(resolved)
                for dirpath, _dirnames, filenames in os.walk(resolved):
                    for name in filenames:
                        files.append(Path(dirpath) / name)
            else:
                errors.append({"path": display(path), "error": "not found"})
        return files, folders, errors

    # -- destination choice ----------------------------------------------

    def _first_free_name(self, name: str) -> Path:
        for candidate in numbered_names(name):
            dest = self.trash_root / candidate
            if not dest.exists() and not dest.is_symlink():
                return dest
        raise OSError(f"too many files named like {name}")

    def resolve_destination(self, src: Path) -> tuple[str, Path, str | None]:
        """Decide where *src* goes.

        Returns ``(action, path, sha)``.  ``action`` is :data:`MOVE` with the
        destination to move to, or :data:`DEDUPE` with the identical file
        already in the trash that makes the source redundant.
        """
        fingerprint = _Fingerprint(src)
        first = True
        for name in numbered_names(src.name):
            candidate = self.trash_root / name
            if not candidate.exists() and not candidate.is_symlink():
                return MOVE, candidate, None
            # Only same-name files get the binary check.  The first candidate is
            # the literal name; the rest are the "(n)" variants we ourselves
            # would have created for that name.
            if candidate.is_file() and (first or self.dedupe_numbered_variants):
                if fingerprint.matches(candidate):
                    return DEDUPE, candidate, fingerprint.sha
            first = False
        raise OSError(f"too many files named like {src.name}")

    # -- execution --------------------------------------------------------

    def process(
        self,
        paths: Sequence[str | os.PathLike],
        confirm: Callable[[int], bool] | None = None,
    ) -> Batch:
        """Move a drop into the trash root and return the batch record.

        *confirm* is called once with the number of files found, before
        anything is touched; returning False abandons the drop.
        """
        batch = Batch(trash_root=display(self.trash_root), sources=[display(p) for p in paths])
        files, folders, errors = self.collect(paths)
        batch.errors.extend(errors)

        if confirm is not None and files and not confirm(len(files)):
            batch.cancelled = True
            batch.finished_at = _now()
            return batch

        try:
            self.trash_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            batch.errors.append({"path": display(self.trash_root), "error": f"cannot create trash folder: {exc}"})
            batch.finished_at = _now()
            return batch

        total = len(files)
        for index, src in enumerate(files, start=1):
            if self.cancel.is_set():
                batch.cancelled = True
                break
            if self.progress:
                self.progress(index, total, src.name)
            try:
                item = self._handle_file(src)
            except OSError as exc:
                batch.errors.append({"path": display(src), "error": str(exc)})
                continue
            if item is not None:
                batch.items.append(item)

        if not batch.cancelled:
            for folder in folders:
                batch.removed_dirs.extend(remove_empty_dirs(folder))

        batch.finished_at = _now()
        return batch

    def _handle_file(self, src: Path) -> Item | None:
        if src.is_symlink():
            # No content to compare, so just find a free name.
            dest = self._first_free_name(src.name)
            shutil.move(str(src), str(dest))
            return Item(action=MOVE, src=display(src), dst=display(dest), renamed=dest.name != src.name)

        if not src.exists():
            raise OSError("not found")
        size = src.stat().st_size
        action, dest, sha = self.resolve_destination(src)

        if action == DEDUPE:
            # Same name, same bytes: the trash already has this file.  Undo can
            # put it back by copying the kept file, so nothing is lost.
            os.remove(src)
            return Item(action=DEDUPE, src=display(src), dst=display(dest), size=size, sha256=sha)

        try:
            shutil.move(str(src), str(dest))
        except OSError:
            # Moving to another drive copies and then deletes, so a failure
            # part way through can leave a half-written file behind.  Never
            # leave that in the trash: it looks like a real file, and a later
            # drop of the true original would be compared against it and filed
            # under a "(1)" name instead.  Only clear it away while the source
            # is still there to be moved again.
            if dest.exists() and src.exists():
                try:
                    dest.unlink()
                except OSError:
                    pass
            raise

        return Item(
            action=MOVE,
            src=display(src),
            dst=display(dest),
            size=size,
            renamed=dest.name != src.name,
        )


def undo_batch(batch: Batch) -> UndoResult:
    """Put a batch back the way it was.

    Folders are recreated first so files have somewhere to land, then every
    item is reversed newest first.  Anything that cannot be reversed cleanly is
    reported rather than forced.
    """
    result = UndoResult(batch_id=batch.id)

    for directory in sorted(batch.removed_dirs, key=lambda d: len(Path(d).parts)):
        path = extended(directory)
        if path.is_dir():
            continue
        try:
            path.mkdir(parents=True, exist_ok=True)
            result.dirs_restored += 1
        except OSError as exc:
            result.errors.append({"path": directory, "error": str(exc)})

    for item in reversed(batch.items):
        src, dst = extended(item.src), extended(item.dst)
        try:
            if not dst.exists():
                if src.exists():
                    # Already back where it belongs, from an earlier attempt at
                    # undoing this batch.  Nothing to do, and not a problem.
                    result.skipped += 1
                    continue
                result.errors.append({"path": item.dst, "error": "no longer in the trash folder"})
                continue

            target = src
            if src.exists():
                if item.action == DEDUPE:
                    # The original is already back where it belongs.
                    result.skipped += 1
                    continue
                target = _unique_path(src)

            target.parent.mkdir(parents=True, exist_ok=True)

            if item.action == DEDUPE:
                if item.sha256 and sha256_of(dst) != item.sha256:
                    result.errors.append({"path": item.dst, "error": "kept copy has changed since the drop"})
                    continue
                shutil.copy2(str(dst), str(target))
                result.copied_back += 1
            else:
                shutil.move(str(dst), str(target))
                result.restored += 1
        except OSError as exc:
            result.errors.append({"path": item.dst, "error": str(exc)})

    return result
