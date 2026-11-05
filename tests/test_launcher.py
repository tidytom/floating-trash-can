"""Tests for TrashCan.pyw, the double-click entry point."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parent.parent / "TrashCan.pyw"


@pytest.fixture
def launcher():
    spec = importlib.util.spec_from_loader(
        "trashcan_launcher", importlib.machinery.SourceFileLoader("trashcan_launcher", str(LAUNCHER))
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ready_machine_starts_the_app_without_installing(launcher, monkeypatch):
    import trashcan.app

    started: list[bool] = []
    monkeypatch.setattr(launcher, "dependencies_present", lambda: True)
    monkeypatch.setattr(
        launcher, "_install_with_progress", lambda: pytest.fail("nothing should be installed when it is ready")
    )
    monkeypatch.setattr(trashcan.app, "main", lambda argv=None: started.append(True) or 0)

    assert launcher.main() == 0
    assert started == [True]


def test_missing_dependencies_trigger_the_installer(launcher, monkeypatch):
    import trashcan.app

    calls: list[str] = []
    monkeypatch.setattr(launcher, "is_frozen", lambda: False)
    monkeypatch.setattr(launcher, "dependencies_present", lambda: False)
    monkeypatch.setattr(launcher, "_install_with_progress", lambda: (calls.append("install"), (True, ""))[1])
    monkeypatch.setattr(trashcan.app, "main", lambda argv=None: 0)

    assert launcher.main() == 0
    assert calls == ["install"]


def test_a_failed_install_reports_instead_of_starting(launcher, monkeypatch):
    reported: list[str] = []
    monkeypatch.setattr(launcher, "is_frozen", lambda: False)
    monkeypatch.setattr(launcher, "dependencies_present", lambda: False)
    monkeypatch.setattr(launcher, "_install_with_progress", lambda: (False, "no internet connection"))
    monkeypatch.setattr(launcher, "_report_failure", reported.append)

    assert launcher.main() == 1
    assert reported == ["no internet connection"]


def test_install_falls_back_to_a_quiet_install_without_tkinter(launcher, monkeypatch):
    """Tkinter ships with Windows Python, but never assume it is there."""
    monkeypatch.setitem(sys.modules, "tkinter", None)  # makes `import tkinter` fail
    called: list[bool] = []
    monkeypatch.setattr(launcher, "ensure_dependencies", lambda *a: called.append(True) or (True, ""))

    assert launcher._install_with_progress() == (True, "")
    assert called == [True], "it should still install, just without a progress window"


def test_exe_build_is_windowed_and_single_file():
    """The packaged exe must not open a console behind the trash can."""
    import importlib.util as iu

    spec = iu.spec_from_file_location("make_exe", LAUNCHER.parent / "tools" / "make_exe.py")
    module = iu.module_from_spec(spec)
    spec.loader.exec_module(module)

    command = module.pyinstaller_command()

    assert "--windowed" in command and "--onefile" in command
    assert command[-1].endswith("TrashCan.pyw")
    assert "--name" in command and "FloatingTrashCan" in command
