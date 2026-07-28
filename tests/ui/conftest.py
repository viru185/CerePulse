"""UI test fixtures.

Qt runs offscreen so these tests need no display and work unchanged in CI.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

# Must be set before QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402 — import after the platform is set


@pytest.fixture(scope="session")
def qapp() -> Iterator[QApplication]:
    """One QApplication for the whole session; Qt forbids a second."""
    application = QApplication.instance() or QApplication([])
    yield application  # type: ignore[misc]
    application.processEvents()  # type: ignore[union-attr]
