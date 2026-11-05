"""Tests for the first-run dependency install."""

from __future__ import annotations

import pytest

from trashcan import bootstrap


def test_no_install_when_already_present(monkeypatch):
    monkeypatch.setattr(bootstrap, "dependencies_present", lambda: True)
    monkeypatch.setattr(
        bootstrap, "_run_pip", lambda *a, **k: pytest.fail("pip should not run when PySide6 is already there")
    )

    ready, message = bootstrap.ensure_dependencies()

    assert ready and message == ""


def test_frozen_build_never_installs(monkeypatch):
    monkeypatch.setattr(bootstrap, "is_frozen", lambda: True)
    monkeypatch.setattr(bootstrap, "dependencies_present", lambda: False)
    monkeypatch.setattr(bootstrap, "_run_pip", lambda *a, **k: pytest.fail("a packaged exe bundles everything"))

    assert bootstrap.ensure_dependencies() == (True, "")


def test_installs_when_missing_then_reports_ready(monkeypatch):
    calls: list[list[str]] = []
    present = {"value": False}

    def fake_pip(args, on_status=None):
        calls.append(list(args))
        present["value"] = True  # the install worked
        return True, "Successfully installed PySide6-Essentials"

    monkeypatch.setattr(bootstrap, "dependencies_present", lambda: present["value"])
    monkeypatch.setattr(bootstrap, "ensure_pip", lambda on_status=None: True)
    monkeypatch.setattr(bootstrap, "_run_pip", fake_pip)

    ready, message = bootstrap.ensure_dependencies()

    assert ready and message == ""
    assert len(calls) == 1
    assert calls[0][0] == "install"
    assert any("PySide6" in arg for arg in calls[0])


def test_falls_back_to_a_per_user_install(monkeypatch):
    """A system-wide Python usually cannot write to its own folder."""
    calls: list[list[str]] = []
    present = {"value": False}

    def fake_pip(args, on_status=None):
        calls.append(list(args))
        if "--user" not in args:
            return False, "ERROR: Could not install packages due to an OSError: [Errno 13] Permission denied"
        present["value"] = True
        return True, "Successfully installed"

    monkeypatch.setattr(bootstrap, "dependencies_present", lambda: present["value"])
    monkeypatch.setattr(bootstrap, "ensure_pip", lambda on_status=None: True)
    monkeypatch.setattr(bootstrap, "in_virtualenv", lambda: False)
    monkeypatch.setattr(bootstrap, "_run_pip", fake_pip)

    ready, _ = bootstrap.ensure_dependencies()

    assert ready
    assert len(calls) == 2 and "--user" in calls[1]


def test_no_user_fallback_inside_a_virtualenv(monkeypatch):
    """--user is meaningless in a venv, so one failure is the answer."""
    calls: list[list[str]] = []

    monkeypatch.setattr(bootstrap, "dependencies_present", lambda: False)
    monkeypatch.setattr(bootstrap, "ensure_pip", lambda on_status=None: True)
    monkeypatch.setattr(bootstrap, "in_virtualenv", lambda: True)
    monkeypatch.setattr(bootstrap, "_run_pip", lambda args, on_status=None: (calls.append(list(args)), (False, "boom"))[1])

    ready, message = bootstrap.ensure_dependencies()

    assert not ready and len(calls) == 1
    assert "boom" in message


def test_failure_message_is_short_and_explains(monkeypatch):
    noise = "\n".join(f"line {i}" for i in range(50)) + "\nERROR: no matching distribution"

    monkeypatch.setattr(bootstrap, "dependencies_present", lambda: False)
    monkeypatch.setattr(bootstrap, "ensure_pip", lambda on_status=None: True)
    monkeypatch.setattr(bootstrap, "in_virtualenv", lambda: True)
    monkeypatch.setattr(bootstrap, "_run_pip", lambda args, on_status=None: (False, noise))

    ready, message = bootstrap.ensure_dependencies()

    assert not ready
    assert "could not be installed" in message
    assert "ERROR: no matching distribution" in message
    assert len(message.splitlines()) <= 10, "the whole pip log would be unreadable"


def test_missing_pip_is_reported_clearly(monkeypatch):
    monkeypatch.setattr(bootstrap, "dependencies_present", lambda: False)
    monkeypatch.setattr(bootstrap, "ensure_pip", lambda on_status=None: False)
    monkeypatch.setattr(bootstrap, "_run_pip", lambda *a, **k: pytest.fail("nothing to run pip with"))

    ready, message = bootstrap.ensure_dependencies()

    assert not ready and "pip" in message


def test_status_messages_reach_the_progress_window(monkeypatch):
    seen: list[str] = []
    present = {"value": False}

    def fake_pip(args, on_status=None):
        if on_status:
            on_status("Installing PySide6-Essentials>=6.5")
        present["value"] = True
        return True, ""

    monkeypatch.setattr(bootstrap, "dependencies_present", lambda: present["value"])
    monkeypatch.setattr(bootstrap, "ensure_pip", lambda on_status=None: True)
    monkeypatch.setattr(bootstrap, "_run_pip", fake_pip)

    bootstrap.ensure_dependencies(seen.append)

    assert seen and "PySide6" in seen[0]


def test_dependencies_present_agrees_with_reality():
    import importlib.util

    expected = importlib.util.find_spec("PySide6") is not None
    assert bootstrap.dependencies_present() is expected


def test_install_that_does_not_take_is_reported(monkeypatch):
    """pip said fine but the import still fails - say so instead of crashing."""
    monkeypatch.setattr(bootstrap, "dependencies_present", lambda: False)
    monkeypatch.setattr(bootstrap, "ensure_pip", lambda on_status=None: True)
    monkeypatch.setattr(bootstrap, "in_virtualenv", lambda: True)
    monkeypatch.setattr(bootstrap, "_run_pip", lambda args, on_status=None: (True, "Successfully installed"))

    ready, message = bootstrap.ensure_dependencies()

    assert not ready and "Restarting" in message


def test_status_line_names_only_the_package(monkeypatch):
    """The progress window should not say "Installing install, ..."."""
    seen: list[str] = []
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())

    bootstrap._run_pip(["install", "--user", "PySide6-Essentials>=6.5"], seen.append)

    assert seen == ["Installing PySide6-Essentials>=6.5"]
