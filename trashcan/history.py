"""Batch history: browse every drop and undo the ones you pick."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME
from .core import DEDUPE, Batch, UndoResult
from .journal import Journal

UNDONE_COLOUR = QColor(120, 128, 140)


def _pretty_time(stamp: str) -> str:
    try:
        return datetime.fromisoformat(stamp).strftime("%a %d %b  %H:%M:%S")
    except ValueError:
        return stamp or "-"


def _pretty_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:,.0f} {unit}" if unit == "B" else f"{value:,.1f} {unit}"
        value /= 1024
    return f"{size} B"


class UndoWorker(QObject):
    """Undoes one or more batches off the GUI thread.

    Putting back a big batch can take a long time (especially across drives),
    so it must not freeze the window. ``finished`` carries a list of
    ``(batch, UndoResult)`` pairs, in the order they were undone.
    """

    finished = Signal(object)

    def __init__(self, journal: Journal, batches: list[Batch]) -> None:
        super().__init__()
        self.journal = journal
        self.batches = batches
        self.results: list | None = None

    @Slot()
    def run(self) -> None:
        results = []
        for batch in self.batches:
            try:
                result = self.journal.undo(batch)
            except Exception as exc:  # report it rather than lose the thread
                result = UndoResult(batch_id=batch.id)
                result.errors.append({"path": "", "error": f"unexpected failure: {exc}"})
            results.append((batch, result))
        self.results = results
        self.finished.emit(results)


def start_undo(owner: QObject, journal: Journal, batches: list[Batch], on_done) -> tuple[QThread, UndoWorker]:
    """Run an UndoWorker on its own thread and call *on_done(results)* on the GUI thread."""
    thread = QThread(owner)
    worker = UndoWorker(journal, batches)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(on_done)
    worker.finished.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    thread.start()
    return thread, worker


class HistoryDialog(QDialog):
    """A list of batches on the left, what that batch did on the right."""

    def __init__(self, journal: Journal, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.journal = journal
        self.batches: list[Batch] = []
        self._undo_thread: QThread | None = None
        self._undo_worker: UndoWorker | None = None

        self.setWindowTitle(f"{APP_NAME} - history")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(940, 560)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Every drop is listed newest first. Pick one (or several) and choose "
                "Undo to put those files back where they came from."
            )
        )

        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        self.table = QTableWidget(0, 4, splitter)
        self.table.setHorizontalHeaderLabels(["When", "Items", "What happened", "Status"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        splitter.addWidget(self.table)

        self.details = QTreeWidget(splitter)
        self.details.setHeaderLabels(["File", "What", "Where it is now"])
        self.details.setRootIsDecorated(False)
        self.details.setAlternatingRowColors(True)
        splitter.addWidget(self.details)
        splitter.setSizes([520, 420])
        layout.addWidget(splitter, 1)

        buttons = QHBoxLayout()
        self.undo_button = QPushButton("Undo selected")
        self.undo_button.clicked.connect(self.undo_selected)
        self.undo_button.setDefault(True)
        buttons.addWidget(self.undo_button)

        self.open_button = QPushButton("Open trash folder")
        self.open_button.clicked.connect(self.open_folder)
        buttons.addWidget(self.open_button)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.reload)
        buttons.addWidget(self.refresh_button)

        buttons.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        buttons.addWidget(box)
        layout.addLayout(buttons)

        self.reload()

    # -- data -------------------------------------------------------------

    def reload(self) -> None:
        self.batches = self.journal.recent()
        self.table.setRowCount(len(self.batches))
        for row, batch in enumerate(self.batches):
            if batch.undone:
                status = "Undone"
            elif batch.partly_undone:
                status = "Partly undone"
            elif batch.cancelled:
                status = "Cancelled"
            else:
                status = "Applied"
            cells = [
                _pretty_time(batch.started_at),
                str(len(batch.items)),
                batch.summary(),
                status,
            ]
            for column, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                if batch.undone:
                    cell.setForeground(UNDONE_COLOUR)
                self.table.setItem(row, column, cell)
        if self.batches:
            self.table.selectRow(0)
        self._selection_changed()

    def selected_batches(self) -> list[Batch]:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        return [self.batches[row] for row in rows if 0 <= row < len(self.batches)]

    def _selection_changed(self) -> None:
        selected = self.selected_batches()
        self.details.clear()
        undoable = [b for b in selected if not b.undone and (b.items or b.removed_dirs)]
        self.undo_button.setEnabled(bool(undoable) and not self.undo_running())

        if len(selected) != 1:
            self.undo_button.setText(f"Undo selected ({len(undoable)})" if undoable else "Undo selected")
            return
        self.undo_button.setText("Undo this batch")

        batch = selected[0]
        for item in batch.items:
            if item.action == DEDUPE:
                what = "duplicate deleted"
                where = f"already in trash as {Path(item.dst).name}"
            else:
                what = "renamed and moved" if item.renamed else "moved"
                where = Path(item.dst).name
            node = QTreeWidgetItem([item.src, what, where])
            node.setToolTip(0, f"{item.src}\n{_pretty_size(item.size)}")
            self.details.addTopLevelItem(node)
        for directory in batch.removed_dirs:
            self.details.addTopLevelItem(QTreeWidgetItem([directory, "empty folder removed", "-"]))
        for error in batch.errors:
            node = QTreeWidgetItem([str(error.get("path", "")), "failed", str(error.get("error", ""))])
            node.setForeground(1, QColor(220, 120, 120))
            self.details.addTopLevelItem(node)
        for column in range(3):
            self.details.resizeColumnToContents(column)

    # -- actions ----------------------------------------------------------

    def open_folder(self) -> None:
        selected = self.selected_batches()
        root = selected[0].trash_root if selected else ""
        if root:
            QDesktopServices.openUrl(QUrl.fromLocalFile(root))

    def undo_selected(self) -> None:
        targets = [b for b in self.selected_batches() if not b.undone and (b.items or b.removed_dirs)]
        if not targets:
            return

        count = sum(len(b.items) for b in targets)
        question = (
            f"Undo {len(targets)} batches, putting {count} items back where they came from?"
            if len(targets) > 1
            else f"Undo this batch, putting {count} items back where they came from?"
        )
        if QMessageBox.question(self, APP_NAME, question) != QMessageBox.StandardButton.Yes:
            return

        # Newest first, so batches that touched the same name unwind in order.
        ordered = sorted(targets, key=lambda b: b.started_at, reverse=True)
        self.undo_button.setEnabled(False)
        self.undo_button.setText("Undoing...")
        self.refresh_button.setEnabled(False)
        # Keep the worker referenced, or Python frees it before it runs.
        self._undo_thread, self._undo_worker = start_undo(self, self.journal, ordered, self._undo_finished)

    def undo_running(self) -> bool:
        return self._undo_thread is not None

    @Slot(object)
    def _undo_finished(self, results: list) -> None:
        thread, self._undo_thread, self._undo_worker = self._undo_thread, None, None
        if thread is not None:
            thread.quit()
            thread.wait()  # the work is done; this only lets the thread end cleanly
        self.refresh_button.setEnabled(True)
        lines: list[str] = []
        problems: list[str] = []
        for batch, result in results:
            lines.append(f"{_pretty_time(batch.started_at)}: {result.summary()}")
            problems.extend(f"{e.get('path', '')}: {e.get('error', '')}" for e in result.errors)

        self.reload()
        message = "\n".join(lines)
        if problems:
            message += "\n\nCould not put these back:\n" + "\n".join(problems[:12])
            if len(problems) > 12:
                message += f"\n...and {len(problems) - 12} more"
            QMessageBox.warning(self, APP_NAME, message)
        else:
            QMessageBox.information(self, APP_NAME, message)

    def reject(self) -> None:
        # Closing mid-undo would end the thread under the worker's feet.
        if self.undo_running():
            QMessageBox.information(self, APP_NAME, "Please wait until the undo has finished.")
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self.undo_running():
            event.ignore()
            QMessageBox.information(self, APP_NAME, "Please wait until the undo has finished.")
            return
        super().closeEvent(event)
