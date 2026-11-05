"""Windows paths longer than 260 characters.

Most Windows APIs stop at MAX_PATH (260 characters) unless the path is handed
over in "extended-length" form, prefixed with ``\\\\?\\``.  Deeply nested
folders blow past that easily, and without this a drop would simply fail.

So every path is converted to extended form before it is touched, and back to
its ordinary form before it is written to the undo history or shown to anyone.
The prefix also switches off Win32 path parsing, so the path has to be
absolute and normalised first.

On anything other than Windows all of this is a no-op.
"""

from __future__ import annotations

import ntpath
import os
from pathlib import Path

WINDOWS = os.name == "nt"

PREFIX = "\\\\?\\"
UNC_PREFIX = "\\\\?\\UNC\\"
DEVICE_PREFIX = "\\\\.\\"


def is_extended(text: str) -> bool:
    return text.startswith(PREFIX) or text.startswith(DEVICE_PREFIX)


def extended_str(text: str) -> str:
    """Add the ``\\\\?\\`` prefix, normalising the path as Win32 would have."""
    if not WINDOWS or not text or is_extended(text):
        return text
    absolute = ntpath.abspath(text)
    if absolute.startswith("\\\\"):
        # \\server\share\... becomes \\?\UNC\server\share\...
        return UNC_PREFIX + absolute[2:]
    return PREFIX + absolute


def plain_str(text: str) -> str:
    """Strip the prefix again, for storing and for showing to people."""
    if not text:
        return text
    if text.startswith(UNC_PREFIX):
        return "\\\\" + text[len(UNC_PREFIX):]
    if text.startswith(PREFIX):
        return text[len(PREFIX):]
    return text


def extended(path: Path | str) -> Path:
    """The form to hand to the filesystem."""
    if not WINDOWS:
        return Path(path)
    return Path(extended_str(str(path)))


def plain(path: Path | str) -> Path:
    """The form to record and display."""
    if not WINDOWS:
        return Path(path)
    return Path(plain_str(str(path)))


def display(path: Path | str) -> str:
    return str(plain(path))
