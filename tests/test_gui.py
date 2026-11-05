"""Smoke tests for the window itself, run against Qt's offscreen backend."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QMimeData, QPoint, QPointF, QRect, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QDragEnterEvent, QDropEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from trashcan.app import FloatingTrash  # noqa: E402
from trashcan.config import Config  # noqa: E402
from trashcan.history import HistoryDialog  # noqa: E402
from trashcan.journal import Journal  # noqa: E402
from trashcan.settings_dialog import SettingsDialog  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qapp, tmp_path: Path):
    config = Config(trash_root=str(tmp_path / "trash"), confirm_above_items=0)
    config.save(tmp_path / "config.json")
    win = FloatingTrash(config, Journal(tmp_path / "journal.jsonl"))
    win.resize(128, 128)
    yield win
    win.close()


def drop_urls(window: FloatingTrash, paths: list[Path]) -> None:
    """Feed the widget a real drag-and-drop, the way Explorer would."""
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    pos = QPointF(window.width() / 2, window.height() / 2)

    enter = QDragEnterEvent(pos.toPoint(), Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.dragEnterEvent(enter)
    assert enter.isAccepted(), "the window should accept a file drag"

    drop = QDropEvent(pos, Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.dropEvent(drop)


def wait_until_idle(qapp, window: FloatingTrash, timeout_ms: int = 5000) -> None:
    from PySide6.QtCore import QElapsedTimer

    clock = QElapsedTimer()
    clock.start()
    while window._busy and clock.elapsed() < timeout_ms:
        qapp.processEvents()
    assert not window._busy, "the batch never finished"
    qapp.processEvents()


def wait_until_undone(qapp, window: FloatingTrash, timeout_ms: int = 5000) -> None:
    """Undo runs on a worker thread, so wait for it like a drop."""
    from PySide6.QtCore import QElapsedTimer

    clock = QElapsedTimer()
    clock.start()
    while window.undo_running() and clock.elapsed() < timeout_ms:
        qapp.processEvents()
    assert not window.undo_running(), "the undo never finished"
    qapp.processEvents()


def test_drop_moves_files_and_records_history(qapp, window: FloatingTrash, tmp_path: Path):
    source = tmp_path / "desk"
    source.mkdir()
    a = source / "a.txt"
    a.write_text("aaa")
    folder = source / "stuff"
    (folder / "deep").mkdir(parents=True)
    (folder / "deep" / "b.txt").write_text("bbb")

    drop_urls(window, [a, folder])
    wait_until_idle(qapp, window)

    trash = window.config.trash_root_path
    assert (trash / "a.txt").read_text() == "aaa"
    assert (trash / "b.txt").read_text() == "bbb"
    assert not folder.exists()

    history = window.journal.recent()
    assert len(history) == 1 and history[0].moved == 2
    assert window._flash_text, "the window should report what it did"


def test_undo_last_from_the_window(qapp, window: FloatingTrash, tmp_path: Path):
    src = tmp_path / "desk"
    src.mkdir()
    note = src / "note.txt"
    note.write_text("keep me")

    drop_urls(window, [note])
    wait_until_idle(qapp, window)
    assert not note.exists()

    window.undo_last()
    wait_until_undone(qapp, window)

    assert note.read_text() == "keep me"
    assert window.journal.recent()[0].undone


def test_drag_enter_opens_the_lid(qapp, window: FloatingTrash, tmp_path: Path):
    target = tmp_path / "x.txt"
    target.write_text("x")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(target))])
    event = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)

    window.dragEnterEvent(event)
    window._lid_anim.setCurrentTime(window._lid_anim.duration())

    assert window._drag_hover and window.get_lid_open() == pytest.approx(1.0)

    window.dragLeaveEvent(event)
    window._lid_anim.setCurrentTime(window._lid_anim.duration())
    assert not window._drag_hover and window.get_lid_open() == pytest.approx(0.0)


def test_dragging_plain_text_is_ignored(qapp, window: FloatingTrash):
    mime = QMimeData()
    mime.setText("just some words")
    event = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)

    window.dragEnterEvent(event)

    assert not event.isAccepted() and not window._drag_hover


def test_window_renders_at_every_size(qapp, window: FloatingTrash):
    for side in (56, 96, 128, 256, 512):
        window.resize(side, side)
        qapp.processEvents()
        shot = window.grab()
        assert not shot.isNull() and shot.width() >= side * window.devicePixelRatio() - 1


def test_geometry_is_remembered(qapp, tmp_path: Path):
    config = Config(trash_root=str(tmp_path / "t"))
    win = FloatingTrash(config, Journal(tmp_path / "j.jsonl"))
    win.resize(200, 180)
    win.move(300, 240)

    win.close()  # closing flushes the pending geometry save

    reloaded = Config.load()
    assert (reloaded.window_width, reloaded.window_height) == (200, 180)
    assert (reloaded.window_x, reloaded.window_y) == (300, 240)


def test_offscreen_geometry_falls_back_to_the_primary_screen(qapp, tmp_path: Path):
    config = Config(trash_root=str(tmp_path / "t"), window_x=-99999, window_y=-99999)
    win = FloatingTrash(config, Journal(tmp_path / "j.jsonl"))

    assert win.x() > -99999, "a window saved on a monitor that is gone comes back into view"
    win.close()


def test_resize_edges_are_detected(qapp, window: FloatingTrash):
    window.resize(120, 120)

    assert window._edges_at(QPoint(2, 2)) == (Qt.Edge.LeftEdge | Qt.Edge.TopEdge)
    assert window._edges_at(QPoint(118, 118)) == (Qt.Edge.RightEdge | Qt.Edge.BottomEdge)
    assert window._edges_at(QPoint(60, 2)) == Qt.Edge.TopEdge
    assert window._edges_at(QPoint(60, 60)) == Qt.Edge(0), "the middle drags the window"
    assert window._cursor_for(Qt.Edge.RightEdge) == Qt.CursorShape.SizeHorCursor
    assert window._cursor_for(Qt.Edge.RightEdge | Qt.Edge.BottomEdge) == Qt.CursorShape.SizeFDiagCursor


def test_corners_are_easier_to_hit_than_edges(qapp, window: FloatingTrash):
    """A corner is more than the few pixels where two edge strips overlap."""
    window.resize(200, 200)
    corner = window._corner_margin()
    assert corner > 8, "the corner zone should be wider than an edge strip"

    inside_corner = QPoint(200 - corner + 2, 200 - corner + 2)
    assert window._edges_at(inside_corner) == (Qt.Edge.RightEdge | Qt.Edge.BottomEdge)
    assert window._is_corner(window._edges_at(inside_corner))

    just_past = QPoint(200 - corner - 6, 200 - corner - 6)
    assert window._edges_at(just_past) == Qt.Edge(0), "further in is still a drag"


def test_corner_zone_shrinks_with_a_small_window(qapp, window: FloatingTrash):
    """It must not swallow a tiny window whole."""
    window.resize(60, 60)

    assert window._corner_margin() < 30
    assert window._edges_at(QPoint(30, 30)) == Qt.Edge(0), "the centre still drags"


def test_corner_drag_keeps_the_proportions(qapp, window: FloatingTrash):
    start = QRect(100, 100, 200, 200)
    edges = Qt.Edge.RightEdge | Qt.Edge.BottomEdge

    # Drag mostly sideways: height follows width rather than staying put.
    result = window._corner_geometry(edges, start, 80, 10, 1.0)

    assert result.width() == result.height() == 280
    assert result.topLeft() == start.topLeft(), "the opposite corner stays anchored"


def test_corner_drag_keeps_a_non_square_ratio(qapp, window: FloatingTrash):
    start = QRect(0, 0, 300, 150)  # 2:1

    result = window._corner_geometry(Qt.Edge.RightEdge | Qt.Edge.BottomEdge, start, 100, 0, 2.0)

    assert (result.width(), result.height()) == (400, 200), "2:1 is preserved"


def test_dragging_the_top_left_corner_anchors_the_bottom_right(qapp, window: FloatingTrash):
    start = QRect(100, 100, 200, 200)

    result = window._corner_geometry(Qt.Edge.LeftEdge | Qt.Edge.TopEdge, start, -50, -50, 1.0)

    assert result.width() == result.height() == 250
    assert result.bottomRight() == start.bottomRight()


def test_corner_drag_stops_at_the_minimum_size(qapp, window: FloatingTrash):
    start = QRect(0, 0, 120, 120)

    result = window._corner_geometry(Qt.Edge.RightEdge | Qt.Edge.BottomEdge, start, -500, -500, 1.0)

    assert result.width() == result.height() >= 56
    assert result.width() == result.height(), "even clamped it stays square"


def test_edges_still_resize_freely(qapp, window: FloatingTrash):
    """Only corners lock the ratio; an edge should still reshape the window."""
    window.resize(150, 150)

    assert not window._is_corner(window._edges_at(QPoint(75, 1)))
    assert not window._is_corner(window._edges_at(QPoint(1, 75)))


def test_menu_offers_undo_only_when_there_is_something_to_undo(qapp, window: FloatingTrash, tmp_path: Path):
    labels = lambda menu: [a.text() for a in menu.actions()]
    menu = window.build_menu()
    undo = next(a for a in menu.actions() if a.text().startswith("Undo last drop"))
    assert not undo.isEnabled()
    assert any("History" in text for text in labels(menu))

    note = tmp_path / "n.txt"
    note.write_text("n")
    drop_urls(window, [note])
    wait_until_idle(qapp, window)

    undo = next(a for a in window.build_menu().actions() if a.text().startswith("Undo last drop"))
    assert undo.isEnabled() and "moved" in undo.text()


def test_history_dialog_lists_and_undoes_a_chosen_batch(qapp, window: FloatingTrash, tmp_path: Path):
    desk = tmp_path / "desk"
    desk.mkdir()
    first, second = desk / "first.txt", desk / "second.txt"
    first.write_text("1")
    second.write_text("2")

    drop_urls(window, [first])
    wait_until_idle(qapp, window)
    drop_urls(window, [second])
    wait_until_idle(qapp, window)

    dialog = HistoryDialog(window.journal)
    assert dialog.table.rowCount() == 2

    # Row 1 is the older batch: undo just that one.
    dialog.table.selectRow(1)
    older = dialog.selected_batches()[0]
    dialog.journal.undo(older)
    dialog.reload()

    assert first.exists(), "the chosen batch was undone"
    assert not second.exists(), "the other batch was left alone"
    assert dialog.table.item(1, 3).text() == "Undone"
    assert dialog.table.item(0, 3).text() == "Applied"
    dialog.close()


def test_history_details_describe_each_item(qapp, window: FloatingTrash, tmp_path: Path):
    trash = window.config.trash_root_path
    trash.mkdir(parents=True, exist_ok=True)
    (trash / "dup.txt").write_text("same")
    desk = tmp_path / "desk"
    desk.mkdir()
    (desk / "dup.txt").write_text("same")

    drop_urls(window, [desk / "dup.txt"])
    wait_until_idle(qapp, window)

    dialog = HistoryDialog(window.journal)
    dialog.table.selectRow(0)
    node = dialog.details.topLevelItem(0)

    assert node.text(1) == "duplicate deleted"
    dialog.close()


def test_settings_dialog_writes_back(qapp, window: FloatingTrash, tmp_path: Path):
    dialog = SettingsDialog(window.config)
    new_root = tmp_path / "other trash"
    dialog.folder_edit.setText(str(new_root))
    dialog.opacity.setValue(60)
    dialog.dedupe_variants.setChecked(False)

    dialog.accept()

    assert window.config.trash_root == str(new_root)
    assert window.config.opacity == pytest.approx(0.6)
    assert window.config.dedupe_numbered_variants is False
    assert new_root.is_dir()


class _FakeTray:
    """Stands in for the tray icon so notifications can be counted."""

    def __init__(self):
        self.messages = []

    def isVisible(self):
        return True

    def showMessage(self, title, message, icon=None, msecs=0):
        self.messages.append((title, message))


def _with_tray(window: FloatingTrash) -> _FakeTray:
    tray = _FakeTray()
    window.tray = tray
    return tray


def test_notifications_can_be_turned_off_entirely(qapp, window: FloatingTrash, tmp_path: Path):
    tray = _with_tray(window)
    window.config.notifications = "none"
    note = tmp_path / "n.txt"
    note.write_text("n")

    drop_urls(window, [note])
    wait_until_idle(qapp, window)

    assert tray.messages == []
    assert window._flash_text, "the window still shows the result under the can"


def test_problems_only_stays_quiet_on_a_clean_drop(qapp, window: FloatingTrash, tmp_path: Path):
    tray = _with_tray(window)
    window.config.notifications = "problems"
    note = tmp_path / "n.txt"
    note.write_text("n")

    drop_urls(window, [note])
    wait_until_idle(qapp, window)

    assert tray.messages == []


def test_problems_only_still_speaks_up_when_something_fails(qapp, window: FloatingTrash, tmp_path: Path):
    tray = _with_tray(window)
    window.config.notifications = "problems"

    drop_urls(window, [tmp_path / "does-not-exist.txt"])
    wait_until_idle(qapp, window)

    assert len(tray.messages) == 1
    assert "problem" in tray.messages[0][0].lower()


def test_every_drop_notifies_by_default(qapp, window: FloatingTrash, tmp_path: Path):
    tray = _with_tray(window)
    note = tmp_path / "n.txt"
    note.write_text("n")

    drop_urls(window, [note])
    wait_until_idle(qapp, window)

    assert len(tray.messages) == 1 and tray.messages[0][0] == "Trashed"


def test_big_drops_are_still_confirmed_with_notifications_off(qapp, window: FloatingTrash, tmp_path: Path, monkeypatch):
    """Turning off pop-ups must not stop it asking about a huge drop."""
    window.config.notifications = "none"
    window.config.confirm_above_items = 2
    _with_tray(window)
    folder = tmp_path / "many"
    folder.mkdir()
    for i in range(5):
        (folder / f"f{i}.txt").write_text(str(i))

    asked: list[int] = []
    from PySide6.QtWidgets import QMessageBox

    def fake_question(parent, title, text, *args, **kwargs):
        asked.append(1)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))

    drop_urls(window, [folder])
    wait_until_idle(qapp, window)

    assert asked, "the confirmation is a question, not a notification"
    assert len(list(window.config.trash_root_path.iterdir())) == 5


def test_notification_menu_reflects_and_sets_the_choice(qapp, window: FloatingTrash):
    window.config.notifications = "problems"

    menu = window.build_menu()
    submenu = next(a.menu() for a in menu.actions() if a.text() == "Notifications")
    checked = [a.text() for a in submenu.actions() if a.isChecked()]
    assert checked == ["Only problems"]

    next(a for a in submenu.actions() if a.text() == "Never").trigger()
    assert window.config.notifications == "none"


def test_an_unknown_notification_setting_falls_back(qapp):
    assert Config(notifications="nonsense").notifications == "all"
