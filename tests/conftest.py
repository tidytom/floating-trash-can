import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch):
    """Keep every test away from the real config and undo history."""
    monkeypatch.setenv("TRASHCAN_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("QT_QPA_PLATFORM", os.environ.get("QT_QPA_PLATFORM", "offscreen"))
    yield
