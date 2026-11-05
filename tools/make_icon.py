"""Render assets/trashcan.ico from the vector icon (used for the built .exe).

    python tools/make_icon.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from trashcan import icon  # noqa: E402


def main() -> int:
    app = QApplication([])  # noqa: F841 - a QApplication must exist to paint
    out = Path(__file__).resolve().parent.parent / "assets"
    out.mkdir(exist_ok=True)

    pixmap = icon.make_pixmap(256)
    target = out / "trashcan.ico"
    if not pixmap.save(str(target), "ICO"):
        print(f"could not write {target}", file=sys.stderr)
        return 1
    pixmap.save(str(out / "trashcan.png"), "PNG")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
