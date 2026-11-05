"""Persisted settings and the location of the app's data files."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

# A folder in the user's own profile, so it works on any PC. Change it in
# Settings. %USERPROFILE% is expanded when the path is used.
DEFAULT_TRASH_ROOT = r"%USERPROFILE%\TrashCan" if os.name == "nt" else "~/TrashCan"

# How chatty the tray pop-ups are.
NOTIFY_ALL = "all"
NOTIFY_PROBLEMS = "problems"
NOTIFY_NONE = "none"
NOTIFICATION_CHOICES = (NOTIFY_ALL, NOTIFY_PROBLEMS, NOTIFY_NONE)


def data_dir() -> Path:
    """Directory holding config.json and the undo journal.

    ``TRASHCAN_DATA_DIR`` overrides it, which is what the tests use.
    """
    override = os.environ.get("TRASHCAN_DATA_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "FloatingTrashCan"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "floating-trashcan"


def config_path() -> Path:
    return data_dir() / "config.json"


def journal_path() -> Path:
    return data_dir() / "journal.jsonl"


@dataclass
class Config:
    """Everything the user can change, saved as json."""

    trash_root: str = DEFAULT_TRASH_ROOT

    # Window: position/size are stored in logical (DPI independent) pixels so
    # the same config restores correctly on a 1080p and a 4K screen.
    window_x: int | None = None
    window_y: int | None = None
    window_width: int = 128
    window_height: int = 128
    opacity: float = 0.95
    always_on_top: bool = True

    # When a name is taken and the bytes differ we file the drop as "name (1).ext".
    # With this on, those numbered variants are also binary-checked, so dropping
    # the same file twice never leaves two identical copies behind.
    dedupe_numbered_variants: bool = True

    # Ask before processing a drop bigger than this many files (0 disables).
    # This is a question, not a notification, so it is asked whatever the
    # notification setting says.
    confirm_above_items: int = 500

    # "all" pops up after every drop, "problems" only when something went
    # wrong, "none" never pops up at all.
    notifications: str = NOTIFY_ALL

    def __post_init__(self) -> None:
        if self.notifications not in NOTIFICATION_CHOICES:
            self.notifications = NOTIFY_ALL
        self.opacity = min(1.0, max(0.25, float(self.opacity)))
        self.window_width = max(48, int(self.window_width))
        self.window_height = max(48, int(self.window_height))

    @property
    def trash_root_path(self) -> Path:
        return Path(os.path.expandvars(self.trash_root)).expanduser()

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or config_path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path: Path | None = None) -> None:
        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(path)
