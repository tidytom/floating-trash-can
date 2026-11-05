"""Build dist/FloatingTrashCan.exe - one file that runs without Python.

    python tools/make_exe.py

Installs PyInstaller if it isn't already there, refreshes the icon, and packs
everything into a single executable you can copy anywhere.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "FloatingTrashCan"


def pyinstaller_command() -> list[str]:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",  # no console window behind the app
        "--name",
        NAME,
        "--exclude-module",
        "tkinter",
        "--exclude-module",
        "pytest",
    ]
    icon = ROOT / "assets" / "trashcan.ico"
    if icon.exists():
        command += ["--icon", str(icon)]
    command.append(str(ROOT / "TrashCan.pyw"))
    return command


def _run(command: list[str], what: str) -> None:
    print(f"{what}...")
    finished = subprocess.run(command, cwd=ROOT)
    if finished.returncode != 0:
        raise SystemExit(f"\n{what} failed (exit code {finished.returncode}).")


def main() -> int:
    for package, module in (("PySide6-Essentials", "PySide6"), ("pyinstaller", "PyInstaller")):
        if importlib.util.find_spec(module) is None:
            _run([sys.executable, "-m", "pip", "install", package], f"Installing {package}")

    _run([sys.executable, str(ROOT / "tools" / "make_icon.py")], "Refreshing the icon")
    _run(pyinstaller_command(), "Building")

    built = ROOT / "dist" / (NAME + (".exe" if sys.platform == "win32" else ""))
    print(f"\nDone: {built}")
    print("Copy it anywhere you like and double-click it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
