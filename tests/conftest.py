from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from cerepulse.core import paths

# Must be set before any QApplication is constructed, so it lives at import time in the
# root conftest rather than in a package that may load second.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point every test at a throwaway data root so the real profile is never touched."""
    root = tmp_path / "data"
    monkeypatch.setenv("CEREPULSE_DATA_DIR", str(root))
    paths.data_root.cache_clear()
    yield root
    paths.data_root.cache_clear()


@pytest.fixture(scope="session")
def qapp() -> Iterator[object]:
    """One QApplication for the whole session; Qt forbids a second.

    Shared beyond ``tests/ui`` because the tray is a Qt object too, and it was previously
    untested precisely because there was no application to construct it against.
    """
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()  # type: ignore[union-attr]
