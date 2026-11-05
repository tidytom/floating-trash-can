"""Command line access to the same engine the window uses.

    python -m trashcan.cli trash "C:\\Users\\me\\Desktop\\old.txt"
    python -m trashcan.cli list
    python -m trashcan.cli undo last
"""

from __future__ import annotations

import argparse
import sys

from .config import Config, journal_path
from .core import TrashMover
from .journal import Journal


def _journal() -> Journal:
    return Journal(journal_path())


def cmd_trash(args: argparse.Namespace) -> int:
    config = Config.load()
    root = args.into or config.trash_root
    mover = TrashMover(
        Config(trash_root=root).trash_root_path,
        dedupe_numbered_variants=config.dedupe_numbered_variants,
        progress=(lambda done, total, name: print(f"[{done}/{total}] {name}")) if args.verbose else None,
    )
    batch = mover.process(args.paths)
    journal = _journal()
    if batch.items or batch.removed_dirs or batch.errors:
        journal.record_batch(batch)
    print(f"{batch.id}  {batch.summary()}")
    for error in batch.errors:
        print(f"  ! {error.get('path', '')}: {error.get('error', '')}", file=sys.stderr)
    return 1 if batch.errors else 0


def cmd_list(args: argparse.Namespace) -> int:
    batches = _journal().recent(args.limit)
    if not batches:
        print("no history yet")
        return 0
    for batch in batches:
        state = "undone " if batch.undone else "applied"
        print(f"{batch.id}  {batch.started_at}  {state}  {batch.summary()}")
    return 0


def cmd_undo(args: argparse.Namespace) -> int:
    journal = _journal()
    batch = journal.last_undoable() if args.batch_id == "last" else journal.find(args.batch_id)
    if batch is None:
        print("no such batch", file=sys.stderr)
        return 1
    if batch.undone:
        print("that batch was already undone", file=sys.stderr)
        return 1
    result = journal.undo(batch)
    print(f"{batch.id}  {result.summary()}")
    for error in result.errors:
        print(f"  ! {error.get('path', '')}: {error.get('error', '')}", file=sys.stderr)
    return 1 if result.errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trashcan", description="Move files into the trash folder, and undo it.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    trash = subparsers.add_parser("trash", help="move files or folders into the trash folder")
    trash.add_argument("paths", nargs="+")
    trash.add_argument("--into", help="override the configured trash folder")
    trash.add_argument("-v", "--verbose", action="store_true")
    trash.set_defaults(func=cmd_trash)

    listing = subparsers.add_parser("list", help="show recent batches")
    listing.add_argument("-n", "--limit", type=int, default=20)
    listing.set_defaults(func=cmd_list)

    undo = subparsers.add_parser("undo", help="undo a batch by id, or 'last'")
    undo.add_argument("batch_id", nargs="?", default="last")
    undo.set_defaults(func=cmd_undo)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
