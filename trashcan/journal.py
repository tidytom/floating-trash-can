"""Append-only history of every batch, and of every undo applied to one.

The file is JSON lines so a crash can never corrupt more than the record being
written, and old entries stay readable when the format grows.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .core import Batch, UndoResult, undo_batch

RECORD_BATCH = "batch"
RECORD_UNDO = "undo"


@dataclass
class Journal:
    path: Path

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        # The history is never trimmed, so it can grow for years.  Parsing it
        # is cached against the file's size and timestamp, because the menu
        # asks what the last undoable batch was on every right-click.
        self._cache: list[Batch] | None = None
        self._cache_key: tuple[int, int] | None = None
        # Undo runs on a worker thread while the window may read the history.
        self._lock = threading.RLock()

    def _stamp(self) -> tuple[int, int] | None:
        try:
            info = self.path.stat()
        except OSError:
            return None
        return (info.st_size, info.st_mtime_ns)

    # -- writing ----------------------------------------------------------

    def _append(self, record: dict) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def record_batch(self, batch: Batch) -> None:
        self._append({"type": RECORD_BATCH, "batch": batch.to_dict()})

    def record_undo(self, batch_id: str, result: UndoResult | None = None) -> None:
        record = {
            "type": RECORD_UNDO,
            "batch_id": batch_id,
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        if result is not None:
            record["summary"] = result.summary()
            # A partial undo must not close the batch off, or the items that
            # failed could never be tried again.
            record["complete"] = not result.errors
        self._append(record)

    # -- reading ----------------------------------------------------------

    def load(self) -> list[Batch]:
        """All batches, oldest first, with undo state applied."""
        with self._lock:
            return self._load()

    def _load(self) -> list[Batch]:
        stamp = self._stamp()
        if stamp is not None and stamp == self._cache_key and self._cache is not None:
            return self._cache

        batches: list[Batch] = []
        by_id: dict[str, Batch] = {}
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return []

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue  # skip a torn line rather than lose the whole history
            kind = record.get("type")
            if kind == RECORD_BATCH:
                batch = Batch.from_dict(record.get("batch", {}))
                if not batch.id:
                    continue
                batches.append(batch)
                by_id[batch.id] = batch
            elif kind == RECORD_UNDO:
                batch = by_id.get(str(record.get("batch_id", "")))
                if batch is not None:
                    batch.undone_at = str(record.get("at", ""))
                    if record.get("complete", True):
                        batch.undone = True
                        batch.partly_undone = False
                    else:
                        batch.partly_undone = True

        self._cache, self._cache_key = batches, stamp
        return batches

    def recent(self, limit: int | None = None) -> list[Batch]:
        """Newest first.  Everything, unless a limit is asked for."""
        newest_first = list(reversed(self.load()))
        return newest_first[:limit] if limit else newest_first

    def last_undoable(self) -> Batch | None:
        for batch in reversed(self.load()):
            if not batch.undone and (batch.items or batch.removed_dirs):
                return batch
        return None

    def find(self, batch_id: str) -> Batch | None:
        for batch in self.load():
            if batch.id == batch_id:
                return batch
        return None

    # -- operations -------------------------------------------------------

    def undo(self, batch: Batch) -> UndoResult:
        """Undo a batch and note it in the history."""
        result = undo_batch(batch)
        if result.errors:
            batch.partly_undone = True
        else:
            batch.undone = True
        self.record_undo(batch.id, result)
        return result

    def clear(self) -> None:
        """Forget every batch.  Nothing on disk is touched, only the history."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        self._cache = None
        self._cache_key = None
