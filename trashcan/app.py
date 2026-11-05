"""The floating trash can window."""

from __future__ import annotations

import sys
import threading

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QTimer,
    QUrl,
    Qt,
    QThread,
    Property,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction, QDesktopServices, QFont, QIcon, QPainter, QColor
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QWidget,
)

from . import APP_NAME, __version__, icon
from .config import NOTIFY_ALL, NOTIFY_NONE, NOTIFY_PROBLEMS, Config, config_path, journal_path
from .core import Batch, TrashMover
from .journal import Journal
from .longpath import extended

RESIZE_MARGIN = 8  # logical px along each edge that starts a resize
CORNER_MARGIN = 22  # corners claim a bigger zone, and keep the proportions
MIN_SIDE = 56


class DropWorker(QObject):
    """Runs one drop off the GUI thread."""

    progress = Signal(int, int, str)
    confirm_needed = Signal(int)
    finished = Signal(object)

    def __init__(self, paths: list[str], config: Config) -> None:
        super().__init__()
        self.paths = paths
        self.config = config
        self.cancel = threading.Event()
        self._answered = threading.Event()
        self._answer = True
        self.result: Batch | None = None

    @Slot(bool)
    def answer(self, proceed: bool) -> None:
        self._answer = proceed
        self._answered.set()

    def _confirm(self, count: int) -> bool:
        limit = self.config.confirm_above_items
        if limit <= 0 or count <= limit:
            return True
        self._answered.clear()
        self.confirm_needed.emit(count)
        self._answered.wait()
        return self._answer

    @Slot()
    def run(self) -> None:
        mover = TrashMover(
            self.config.trash_root_path,
            dedupe_numbered_variants=self.config.dedupe_numbered_variants,
            progress=lambda done, total, name: self.progress.emit(done, total, name),
            cancel=self.cancel,
        )
        try:
            batch = mover.process(self.paths, confirm=self._confirm)
        except Exception as exc:  # a crash here must not take the app down
            batch = Batch(trash_root=str(self.config.trash_root_path), sources=self.paths)
            batch.errors.append({"path": "", "error": f"unexpected failure: {exc}"})
        self.result = batch
        self.finished.emit(batch)


class FloatingTrash(QWidget):
    """Frameless, always-on-top drop target."""

    def __init__(self, config: Config, journal: Journal) -> None:
        super().__init__()
        self.config = config
        self.journal = journal

        self._lid = 0.0
        self._drag_hover = False
        self._busy = False
        self._progress: tuple[int, int] = (0, 0)
        self._flash_text = ""
        self._thread: QThread | None = None
        self._worker: DropWorker | None = None
        self._undo_thread: QThread | None = None
        self._undo_worker = None
        self._history_dialog = None
        self._quit_when_done = False
        self._drag_origin: QPoint | None = None
        self._hovered = False
        self._resize_edges = Qt.Edge(0)
        self._resize_start = QRect()
        self._resize_origin = QPoint()
        self._resize_ratio = 1.0

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(icon.make_pixmap(64)))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setMinimumSize(MIN_SIDE, MIN_SIDE)
        self._apply_flags()

        self._lid_anim = QPropertyAnimation(self, b"lidOpen", self)
        self._lid_anim.setDuration(160)
        self._lid_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._clear_flash)

        self._quit_timer = QTimer(self)
        self._quit_timer.setSingleShot(True)
        self._quit_timer.timeout.connect(self._quit_if_idle)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_geometry)

        self._restore_geometry()
        self.setWindowOpacity(self.config.opacity)

    # -- lid animation ----------------------------------------------------

    def get_lid_open(self) -> float:
        return self._lid

    def set_lid_open(self, value: float) -> None:
        self._lid = float(value)
        self.update()

    lidOpen = Property(float, get_lid_open, set_lid_open)

    def _animate_lid(self, target: float) -> None:
        self._lid_anim.stop()
        self._lid_anim.setStartValue(self._lid)
        self._lid_anim.setEndValue(target)
        self._lid_anim.start()

    # -- window plumbing --------------------------------------------------

    def _apply_flags(self) -> None:
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self.config.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        was_visible = self.isVisible()
        self.setWindowFlags(flags)
        if was_visible:
            self.show()

    def _restore_geometry(self) -> None:
        width = max(MIN_SIDE, self.config.window_width)
        height = max(MIN_SIDE, self.config.window_height)
        self.resize(width, height)
        x, y = self.config.window_x, self.config.window_y
        if x is None or y is None or not self._point_on_a_screen(QPoint(int(x), int(y))):
            # First run, or the screen it used to live on is gone.
            screen = QApplication.primaryScreen()
            area = screen.availableGeometry() if screen else None
            if area:
                self.move(area.right() - width - 40, area.bottom() - height - 60)
            return
        self.move(int(x), int(y))

    @staticmethod
    def _point_on_a_screen(point: QPoint) -> bool:
        return any(screen.availableGeometry().contains(point) for screen in QApplication.screens())

    def _save_geometry(self) -> None:
        self.config.window_x = self.x()
        self.config.window_y = self.y()
        self.config.window_width = self.width()
        self.config.window_height = self.height()
        try:
            self.config.save()
        except OSError:
            pass

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._save_timer.start(700)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._save_timer.start(700)

    # -- painting ---------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        side = min(rect.width(), rect.height())

        paint_footer = side >= 92 and (self._flash_text or self._busy)
        footer_height = side * (0.13 if self._busy and not self._flash_text else 0.24)
        can_rect = rect
        if paint_footer:
            can_rect = QRectF(rect.x(), rect.y(), rect.width(), rect.height() - footer_height)

        icon.paint_card(painter, rect, hover=self._drag_hover)
        inset = min(can_rect.width(), can_rect.height()) * 0.17
        target = can_rect.adjusted(inset, inset, -inset, -inset)
        colour = icon.INK_HOVER if self._drag_hover else icon.INK
        icon.paint_can(painter, target, self._lid, colour)

        if self._busy:
            done, total = self._progress
            fraction = done / total if total else 0.0
            icon.paint_progress_ring(painter, can_rect.adjusted(inset * 0.4, inset * 0.4, -inset * 0.4, -inset * 0.4), fraction)

        if paint_footer:
            text = self._flash_text
            if self._busy and not self._flash_text:
                done, total = self._progress
                text = f"{done}/{total}"
            self._draw_footer(painter, rect, side, footer_height, text)

        if self._hovered and not self._busy and not self._drag_hover:
            icon.paint_resize_grip(painter, rect, side)
        painter.end()

    def _draw_footer(self, painter: QPainter, rect: QRectF, side: float, height: float, text: str) -> None:
        """Result line under the can, wrapped over up to three lines."""
        if not text:
            return
        font = QFont(self.font())
        font.setPixelSize(max(8, min(16, int(side * 0.072))))
        painter.setFont(font)
        painter.setPen(QColor(203, 211, 223))

        margin = side * 0.05
        area = QRectF(
            rect.x() + margin,
            rect.bottom() - height,
            rect.width() - margin * 2,
            height - side * 0.035,
        )
        flags = int(
            Qt.AlignmentFlag.AlignHCenter
            | Qt.AlignmentFlag.AlignTop
            | Qt.TextFlag.TextWordWrap
        )
        metrics = painter.fontMetrics()
        max_lines = max(1, int(area.height() // metrics.lineSpacing()))
        if metrics.boundingRect(area.toRect(), flags, text).height() > max_lines * metrics.lineSpacing():
            # Still too tall even wrapped: trim to the lines that do fit.
            text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(area.width()) * max_lines)
        painter.drawText(area, flags, text)

    def _flash(self, text: str, seconds: float = 6.0) -> None:
        self._flash_text = text
        self._flash_timer.start(int(seconds * 1000))
        # The tooltip keeps the last result readable even when the window is
        # too small to show a message.
        self.setToolTip(f"{APP_NAME}\nlast: {text}" if text else APP_NAME)
        self.update()

    def _clear_flash(self) -> None:
        self._flash_text = ""
        self.update()

    # -- mouse: move, resize, menu ---------------------------------------

    def _corner_margin(self) -> int:
        """Corners get a far bigger target than edges.

        The overlap of two edge strips is only a few pixels square, which is
        fiddly to hit, so the corner zone is widened and wins over the edges.
        """
        return max(RESIZE_MARGIN, min(CORNER_MARGIN, int(min(self.width(), self.height()) * 0.24)))

    def _edges_at(self, pos: QPoint) -> Qt.Edges:
        width, height = self.width(), self.height()
        left = pos.x() <= RESIZE_MARGIN
        right = pos.x() >= width - RESIZE_MARGIN
        top = pos.y() <= RESIZE_MARGIN
        bottom = pos.y() >= height - RESIZE_MARGIN

        corner = self._corner_margin()
        near_left = pos.x() <= corner
        near_right = pos.x() >= width - corner
        near_top = pos.y() <= corner
        near_bottom = pos.y() >= height - corner
        if (near_left or near_right) and (near_top or near_bottom):
            left, right, top, bottom = near_left, near_right, near_top, near_bottom

        edges = Qt.Edge(0)
        if left:
            edges |= Qt.Edge.LeftEdge
        elif right:
            edges |= Qt.Edge.RightEdge
        if top:
            edges |= Qt.Edge.TopEdge
        elif bottom:
            edges |= Qt.Edge.BottomEdge
        return edges

    @staticmethod
    def _is_corner(edges: Qt.Edges) -> bool:
        horizontal = bool(edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge))
        vertical = bool(edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge))
        return horizontal and vertical

    @staticmethod
    def _corner_geometry(edges: Qt.Edges, start: QRect, dx: int, dy: int, ratio: float) -> QRect:
        """Geometry for a corner drag, keeping the window's proportions.

        The corner opposite the one being dragged stays put, and whichever
        direction was dragged furthest decides the new size.
        """
        ratio = ratio if ratio > 0 else 1.0
        width = start.width() + (dx if edges & Qt.Edge.RightEdge else -dx)
        height = start.height() + (dy if edges & Qt.Edge.BottomEdge else -dy)

        if abs(width - start.width()) >= abs(height - start.height()) * ratio:
            height = width / ratio
        else:
            width = height * ratio

        if width < MIN_SIDE:
            width, height = MIN_SIDE, MIN_SIDE / ratio
        if height < MIN_SIDE:
            width, height = MIN_SIDE * ratio, MIN_SIDE

        width, height = int(round(width)), int(round(height))
        x = start.x() + (start.width() - width) if edges & Qt.Edge.LeftEdge else start.x()
        y = start.y() + (start.height() - height) if edges & Qt.Edge.TopEdge else start.y()
        return QRect(x, y, width, height)

    def _cursor_for(self, edges: Qt.Edges) -> Qt.CursorShape:
        left = bool(edges & Qt.Edge.LeftEdge)
        right = bool(edges & Qt.Edge.RightEdge)
        top = bool(edges & Qt.Edge.TopEdge)
        bottom = bool(edges & Qt.Edge.BottomEdge)
        if (left and top) or (right and bottom):
            return Qt.CursorShape.SizeFDiagCursor
        if (right and top) or (left and bottom):
            return Qt.CursorShape.SizeBDiagCursor
        if left or right:
            return Qt.CursorShape.SizeHorCursor
        if top or bottom:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.OpenHandCursor

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position().toPoint()
        handle = self.windowHandle()
        edges = self._edges_at(pos)

        if self._is_corner(edges):
            # Corners are driven by hand rather than handed to the window
            # manager, because only we can hold the proportions steady.
            self._resize_edges = edges
            self._resize_start = self.geometry()
            self._resize_origin = event.globalPosition().toPoint()
            self._resize_ratio = self.width() / max(1, self.height())
            return
        if edges and handle is not None and handle.startSystemResize(edges):
            return
        if not edges and handle is not None and handle.startSystemMove():
            return
        # Fallback for platforms without system move/resize.
        self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._resize_edges and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self._resize_origin
            self.setGeometry(
                self._corner_geometry(
                    self._resize_edges, self._resize_start, delta.x(), delta.y(), self._resize_ratio
                )
            )
            return
        if self._drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            return
        self.setCursor(self._cursor_for(self._edges_at(event.position().toPoint())))

    def mouseReleaseEvent(self, event) -> None:
        self._drag_origin = None
        self._resize_edges = Qt.Edge(0)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_trash_folder()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.unsetCursor()
        self.update()

    def contextMenuEvent(self, event) -> None:
        self.build_menu().exec(event.globalPos())

    # -- drag and drop ----------------------------------------------------

    def dragEnterEvent(self, event) -> None:
        if not event.mimeData().hasUrls():
            return
        if not any(url.isLocalFile() for url in event.mimeData().urls()):
            return
        event.acceptProposedAction()
        self._drag_hover = True
        self._animate_lid(1.0)
        self.update()

    def dragLeaveEvent(self, event) -> None:
        self._drag_hover = False
        self._animate_lid(0.0)
        self.update()

    def dropEvent(self, event) -> None:
        self._drag_hover = False
        self._animate_lid(0.0)
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if not paths:
            return
        event.acceptProposedAction()
        if self._busy or self.undo_running():
            self._flash("busy - try again in a moment")
            return
        self.start_batch(paths)

    # -- running a batch --------------------------------------------------

    def start_batch(self, paths: list[str]) -> None:
        self._busy = True
        self._progress = (0, len(paths))
        self._flash_text = ""

        thread = QThread(self)
        worker = DropWorker(paths, self.config)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.confirm_needed.connect(self._on_confirm_needed)
        worker.finished.connect(self._on_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        self._thread, self._worker = thread, worker
        thread.start()
        self.update()

    @Slot(int, int, str)
    def _on_progress(self, done: int, total: int, name: str) -> None:
        self._progress = (done, total)
        self.update()

    @Slot(int)
    def _on_confirm_needed(self, count: int) -> None:
        answer = QMessageBox.question(
            self,
            APP_NAME,
            f"That drop contains {count:,} files.\n\nMove them all into\n{self.config.trash_root_path}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if self._worker is not None:
            self._worker.answer(answer == QMessageBox.StandardButton.Yes)

    @Slot(object)
    def _on_finished(self, batch: Batch) -> None:
        if self._worker is None:
            return  # already saved by save_unfinished_work() while quitting
        self._busy = False
        self._progress = (0, 0)
        self._record(batch)
        self._flash(batch.summary())
        self.notify(
            "Trashed" if not batch.errors else "Trashed with problems",
            batch.summary(),
            problem=bool(batch.errors),
        )
        thread, self._thread, self._worker = self._thread, None, None
        if thread is not None:
            thread.quit()
            thread.wait()  # the work is done; this only lets the thread end cleanly
        self.update()
        if self._quit_when_done:
            self._quit_if_idle()

    def _record(self, batch: Batch) -> None:
        if batch.items or batch.removed_dirs or batch.errors:
            try:
                self.journal.record_batch(batch)
            except OSError as exc:
                self._flash(f"history not saved: {exc}")

    def cancel_batch(self) -> None:
        if self._worker is not None:
            self._worker.cancel.set()
            self._flash("stopping...")

    # -- actions ----------------------------------------------------------

    def notify(self, title: str, message: str, problem: bool = False) -> None:
        """Pop up a tray message, as far as the notification setting allows.

        The window itself always shows the result underneath the can, so
        silencing these loses nothing permanently.
        """
        setting = self.config.notifications
        if setting == NOTIFY_NONE:
            return
        if setting == NOTIFY_PROBLEMS and not problem:
            return
        tray = getattr(self, "tray", None)
        if tray is not None and tray.isVisible():
            tray.showMessage(title, message, QIcon(icon.make_pixmap(64)), 4000)

    def open_trash_folder(self) -> None:
        root = self.config.trash_root_path
        try:
            extended(root).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME, f"Cannot open {root}\n\n{exc}")
            return
        # Explorer wants the ordinary path, not the extended-length one.
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))

    def undo_running(self) -> bool:
        return self._undo_thread is not None

    def undo_last(self) -> None:
        if self._busy or self.undo_running():
            self._flash("busy - try again in a moment")
            return
        batch = self.journal.last_undoable()
        if batch is None:
            self._flash("nothing to undo")
            return
        from .history import start_undo

        self._flash("undoing...", seconds=600)
        # Keep the worker referenced, or Python frees it before it runs.
        self._undo_thread, self._undo_worker = start_undo(self, self.journal, [batch], self._on_undo_finished)

    @Slot(object)
    def _on_undo_finished(self, results: list) -> None:
        thread, self._undo_thread, self._undo_worker = self._undo_thread, None, None
        if thread is not None:
            thread.quit()
            thread.wait()
        if not results:
            return
        _batch, result = results[0]
        self._flash(result.summary())
        self.notify("Undone", result.summary(), problem=bool(result.errors))
        if result.errors:
            QMessageBox.warning(
                self,
                APP_NAME,
                "Some items could not be put back:\n\n"
                + "\n".join(f"{e.get('path', '')}: {e.get('error', '')}" for e in result.errors[:10]),
            )
        if self._quit_when_done:
            self._quit_if_idle()

    def show_history(self) -> None:
        from .history import HistoryDialog

        if self._history_dialog is not None:
            self._history_dialog.raise_()
            return
        dialog = HistoryDialog(self.journal, self)
        self._history_dialog = dialog
        try:
            dialog.exec()
        finally:
            self._history_dialog = None
        self._flash("")
        if self._quit_when_done:
            self._quit_if_idle()

    def show_settings(self) -> None:
        from .settings_dialog import SettingsDialog

        dialog = SettingsDialog(self.config, self)
        if dialog.exec():
            self.config.save()
            self.setWindowOpacity(self.config.opacity)
            self._apply_flags()
            self.update()

    def set_notifications(self, value: str) -> None:
        self.config.notifications = value
        self.config.save()

    def set_opacity(self, value: float) -> None:
        self.config.opacity = value
        self.setWindowOpacity(value)
        self.config.save()

    def toggle_on_top(self, enabled: bool) -> None:
        self.config.always_on_top = enabled
        self._apply_flags()
        self.config.save()

    def reset_size(self) -> None:
        self.resize(128, 128)
        self._save_geometry()

    def recall_window(self) -> None:
        """Bring the can back to a sane spot if it got lost off-screen."""
        screen = QApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - 40, area.bottom() - self.height() - 60)
        self.show()
        self.raise_()
        self.activateWindow()
        self._save_geometry()

    # -- quitting ---------------------------------------------------------

    def _work_running(self) -> bool:
        dialog = self._history_dialog
        return self._busy or self.undo_running() or bool(dialog is not None and dialog.undo_running())

    def request_quit(self) -> None:
        """Quit, but never while a drop or an undo is half done.

        A drop is written to the undo history only when it finishes, so quitting
        in the middle would leave moved files with no record of where they came
        from. Instead we stop the batch, wait for it to save, then quit.
        """
        if not self._work_running():
            QApplication.quit()
            return
        if self._busy:
            question = (
                "A drop is still being processed.\n\n"
                "Stop it and quit? The files already moved are saved to the "
                "history first, so they can still be undone."
            )
        else:
            question = "An undo is still running.\n\nQuit as soon as it has finished?"
        answer = QMessageBox.question(
            self,
            APP_NAME,
            question,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._quit_when_done = True
        if self._busy:
            self.cancel_batch()
        self._quit_if_idle()

    def _quit_if_idle(self) -> None:
        if self._work_running():
            self._flash("finishing, then quitting...", seconds=600)
            self._quit_timer.start(300)  # look again shortly
            return
        self._quit_timer.stop()
        QApplication.quit()

    def save_unfinished_work(self) -> None:
        """Last chance when the app is closed from outside (log off, shutdown).

        Stops a running drop, waits for it, and writes what it did to the
        history. Waits for a running undo, which saves its own record.
        """
        worker, thread = self._worker, self._thread
        if worker is not None and thread is not None:
            worker.cancel.set()
            worker.answer(False)  # in case it is waiting on the "carry on?" question
            thread.quit()
            thread.wait(30000)
            if worker.result is not None:
                self._worker = None  # stops _on_finished saving it a second time
                self._record(worker.result)
        for running in (self._undo_thread, getattr(self._history_dialog, "_undo_thread", None)):
            if running is not None:
                running.quit()
                running.wait(30000)

    # -- menu -------------------------------------------------------------

    def build_menu(self) -> QMenu:
        menu = QMenu(self)

        header = menu.addAction(str(self.config.trash_root_path))
        header.setEnabled(False)
        menu.addSeparator()

        menu.addAction("Open trash folder", self.open_trash_folder)

        last = self.journal.last_undoable()
        undo_label = "Undo last drop"
        if last is not None:
            undo_label = f"Undo last drop ({last.summary()})"
        undo_action = menu.addAction(undo_label, self.undo_last)
        undo_action.setEnabled(last is not None)

        menu.addAction("History and undo...", self.show_history)

        if self._busy:
            menu.addSeparator()
            menu.addAction("Stop this batch", self.cancel_batch)

        menu.addSeparator()

        on_top = QAction("Always on top", menu)
        on_top.setCheckable(True)
        on_top.setChecked(self.config.always_on_top)
        on_top.toggled.connect(self.toggle_on_top)
        menu.addAction(on_top)

        notify_menu = QMenu("Notifications", menu)
        menu.addMenu(notify_menu)
        for label, value in (
            ("After every drop", NOTIFY_ALL),
            ("Only problems", NOTIFY_PROBLEMS),
            ("Never", NOTIFY_NONE),
        ):
            action = QAction(label, notify_menu)
            action.setCheckable(True)
            action.setChecked(self.config.notifications == value)
            action.triggered.connect(lambda _checked=False, v=value: self.set_notifications(v))
            notify_menu.addAction(action)

        opacity_menu = QMenu("Opacity", menu)
        menu.addMenu(opacity_menu)
        for label, value in (("100%", 1.0), ("90%", 0.9), ("75%", 0.75), ("60%", 0.6), ("40%", 0.4)):
            action = QAction(label, opacity_menu)
            action.setCheckable(True)
            action.setChecked(abs(self.config.opacity - value) < 0.02)
            action.triggered.connect(lambda _checked=False, v=value: self.set_opacity(v))
            opacity_menu.addAction(action)

        menu.addAction("Reset size", self.reset_size)
        menu.addAction("Settings...", self.show_settings)

        menu.addSeparator()
        menu.addAction("Quit", self.request_quit)
        return menu

    def closeEvent(self, event) -> None:
        self._save_geometry()
        super().closeEvent(event)


def build_tray(window: FloatingTrash) -> QSystemTrayIcon | None:
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    tray = QSystemTrayIcon(QIcon(icon.make_pixmap(64)), window)
    tray.setToolTip(f"{APP_NAME} - drop files to file them away")

    menu = QMenu()
    menu.addAction("Show trash can", window.recall_window)
    menu.addAction("Open trash folder", window.open_trash_folder)
    menu.addAction("History and undo...", window.show_history)
    menu.addAction("Settings...", window.show_settings)
    menu.addSeparator()
    menu.addAction("Quit", window.request_quit)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: window.recall_window()
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick
        else None
    )
    tray.show()
    window.tray = tray  # keeps it alive and lets notify() find it
    return tray


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if "--version" in argv:
        print(f"{APP_NAME} {__version__}")
        return 0

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(icon.make_pixmap(64)))

    config = Config.load()
    if not config_path().exists():
        config.save()
    window = FloatingTrash(config, Journal(journal_path()))
    build_tray(window)
    # Quitting from a menu never reaches closeEvent, so flush position here too.
    app.aboutToQuit.connect(window._save_geometry)
    # Closed from outside mid-drop: still save what was done so it can be undone.
    app.aboutToQuit.connect(window.save_unfinished_work)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
