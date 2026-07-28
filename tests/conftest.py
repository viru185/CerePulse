from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from cerepulse.core import paths


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point every test at a throwaway data root so the real profile is never touched."""
    root = tmp_path / "data"
    monkeypatch.setenv("CEREPULSE_DATA_DIR", str(root))
    paths.data_root.cache_clear()
    yield root
    paths.data_root.cache_clear()
