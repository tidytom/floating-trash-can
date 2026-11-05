"""First-run dependency install.

The app needs PySide6. Rather than making anyone run pip by hand, the launcher
calls :func:`ensure_dependencies`, which checks whether it is already there and
installs it if not.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from typing import Callable, Sequence

REQUIREMENTS: tuple[str, ...] = ("PySide6-Essentials>=6.5",)

StatusFn = Callable[[str], None]

# Keep pip's console window from flashing up when launched from a .pyw file.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def is_frozen() -> bool:
    """True inside a PyInstaller build, where everything is already bundled."""
    return bool(getattr(sys, "frozen", False))


def in_virtualenv() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def dependencies_present() -> bool:
    try:
        return importlib.util.find_spec("PySide6.QtWidgets") is not None
    except (ImportError, ValueError):
        return False


def _run_pip(args: Sequence[str], on_status: StatusFn | None = None) -> tuple[bool, str]:
    command = [sys.executable, "-m", "pip", *args]
    if on_status:
        # args[0] is the pip subcommand ("install"), not something to announce.
        packages = [a for a in args[1:] if not a.startswith("-")]
        on_status("Installing " + ", ".join(packages) if packages else "Working...")
    try:
        finished = subprocess.run(
            command,
            capture_output=True,
            text=True,
            creationflags=_NO_WINDOW,
        )
    except OSError as exc:
        return False, f"could not start pip: {exc}"
    output = (finished.stdout or "") + (finished.stderr or "")
    return finished.returncode == 0, output.strip()


def ensure_pip(on_status: StatusFn | None = None) -> bool:
    if importlib.util.find_spec("pip") is not None:
        return True
    if on_status:
        on_status("Setting up pip...")
    try:
        import ensurepip

        ensurepip.bootstrap()
        return importlib.util.find_spec("pip") is not None
    except Exception:
        return False


def ensure_dependencies(on_status: StatusFn | None = None) -> tuple[bool, str]:
    """Make sure PySide6 is importable, installing it if it isn't.

    Returns ``(ready, message)``.  A false *ready* means the app cannot start
    and *message* explains why in terms a person can act on.
    """
    if is_frozen() or dependencies_present():
        return True, ""

    if not ensure_pip(on_status):
        return False, "Python's package installer (pip) is missing, so the display components could not be installed."

    ok, output = _run_pip(["install", *REQUIREMENTS], on_status)

    if not ok and not in_virtualenv():
        # A system-wide Python often refuses to write to its own folder; a
        # per-user install needs no admin rights.
        if on_status:
            on_status("Retrying as a per-user install...")
        ok, output = _run_pip(["install", "--user", *REQUIREMENTS], on_status)

    if ok:
        # The freshly installed package may not be on the import path yet.
        importlib.invalidate_caches()
        if not dependencies_present():
            _add_user_site()
        if dependencies_present():
            return True, ""
        return False, "The display components installed, but Python still cannot find them. Restarting usually fixes this."

    tail = "\n".join(output.splitlines()[-6:])
    return False, f"The display components could not be installed.\n\n{tail}"


def _add_user_site() -> None:
    """Make a just-completed --user install visible to this process."""
    try:
        import site

        for path in site.getusersitepackages() if hasattr(site, "getusersitepackages") else []:
            if isinstance(path, str) and path not in sys.path:
                sys.path.append(path)
        user_site = site.getusersitepackages()
        if isinstance(user_site, str) and user_site not in sys.path:
            sys.path.append(user_site)
        importlib.invalidate_caches()
    except Exception:
        pass
