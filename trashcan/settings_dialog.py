"""Settings window."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME
from .longpath import extended
from .config import NOTIFY_ALL, NOTIFY_NONE, NOTIFY_PROBLEMS, Config, data_dir

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "FloatingTrashCan"


def startup_enabled() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except OSError:
        return False


def startup_command() -> str:
    """The command Windows runs at login for a source (not .exe) install.

    At login the working folder is not this project, so ``-m trashcan`` would
    not find the package. The full path to TrashCan.pyw always works.
    """
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    python = pythonw if pythonw.exists() else Path(sys.executable)
    launcher = Path(__file__).resolve().parent.parent / "TrashCan.pyw"
    if launcher.exists():
        return f'"{python}" "{launcher}"'
    # Installed with pip: the package is importable from anywhere.
    return f'"{python}" -m trashcan'


def set_startup(enabled: bool) -> None:
    """Add or remove the Windows "run at login" entry."""
    if os.name != "nt":
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            if getattr(sys, "frozen", False):
                command = f'"{sys.executable}"'
            else:
                command = startup_command()
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass


class SettingsDialog(QDialog):
    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle(f"{APP_NAME} - settings")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.folder_edit = QLineEdit(config.trash_root)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(browse)
        holder = QWidget()
        holder.setLayout(folder_row)
        form.addRow("Trash folder", holder)

        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(25, 100)
        self.opacity.setValue(int(round(config.opacity * 100)))
        self.opacity_label = QLabel(f"{self.opacity.value()}%")
        self.opacity.valueChanged.connect(lambda v: self.opacity_label.setText(f"{v}%"))
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(self.opacity, 1)
        opacity_row.addWidget(self.opacity_label)
        opacity_holder = QWidget()
        opacity_holder.setLayout(opacity_row)
        form.addRow("Window opacity", opacity_holder)

        self.on_top = QCheckBox("Float above all other windows")
        self.on_top.setChecked(config.always_on_top)
        form.addRow("", self.on_top)

        self.dedupe_variants = QCheckBox("Also compare against earlier “name (1)” copies")
        self.dedupe_variants.setChecked(config.dedupe_numbered_variants)
        self.dedupe_variants.setToolTip(
            "A file whose name is already taken is compared byte for byte before anything "
            "is written. With this on, the numbered copies of that name are checked too, so "
            "dropping the same file twice never leaves two identical copies behind."
        )
        form.addRow("Duplicates", self.dedupe_variants)

        self.notifications = QComboBox()
        for label, value in (
            ("After every drop", NOTIFY_ALL),
            ("Only when something goes wrong", NOTIFY_PROBLEMS),
            ("Never", NOTIFY_NONE),
        ):
            self.notifications.addItem(label, value)
        index = self.notifications.findData(config.notifications)
        self.notifications.setCurrentIndex(index if index >= 0 else 0)
        self.notifications.setToolTip(
            "Pop-ups from the tray icon. The result is always shown under the can "
            "either way, and the question below is asked whatever this is set to."
        )
        form.addRow("Pop-up notifications", self.notifications)

        self.confirm_above = QSpinBox()
        self.confirm_above.setRange(0, 1000000)
        self.confirm_above.setSingleStep(50)
        self.confirm_above.setValue(config.confirm_above_items)
        self.confirm_above.setSpecialValueText("never ask")
        form.addRow("Ask before drops over", self.confirm_above)

        self.startup = QCheckBox("Start with Windows")
        self.startup.setChecked(startup_enabled())
        self.startup.setEnabled(os.name == "nt")
        form.addRow("", self.startup)

        layout.addLayout(form)
        note = QLabel(f"Settings and undo history live in {data_dir()}")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")
        layout.addWidget(note)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def _browse(self) -> None:
        start = self.folder_edit.text() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Choose the trash folder", start)
        if chosen:
            self.folder_edit.setText(str(Path(chosen)))

    def accept(self) -> None:
        folder = self.folder_edit.text().strip()
        if not folder:
            QMessageBox.warning(self, APP_NAME, "Please choose a trash folder.")
            return
        path = Path(os.path.expandvars(folder)).expanduser()
        try:
            extended(path).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            if (
                QMessageBox.question(
                    self,
                    APP_NAME,
                    f"{path} cannot be created right now:\n\n{exc}\n\nSave it anyway?",
                )
                != QMessageBox.StandardButton.Yes
            ):
                return

        self.config.trash_root = folder
        self.config.opacity = self.opacity.value() / 100.0
        self.config.always_on_top = self.on_top.isChecked()
        self.config.dedupe_numbered_variants = self.dedupe_variants.isChecked()
        self.config.confirm_above_items = self.confirm_above.value()
        self.config.notifications = self.notifications.currentData()
        if os.name == "nt":
            try:
                set_startup(self.startup.isChecked())
            except OSError as exc:
                QMessageBox.warning(self, APP_NAME, f"Could not change the startup entry:\n{exc}")
        super().accept()
