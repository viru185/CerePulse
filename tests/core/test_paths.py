from __future__ import annotations

from pathlib import Path

import pytest

from cerepulse.core import paths


def test_env_override_wins(isolated_data_root: Path) -> None:
    assert paths.data_root() == isolated_data_root


def test_derived_paths_sit_under_the_root(isolated_data_root: Path) -> None:
    assert paths.config_file().parent == isolated_data_root / "config"
    assert paths.database_file().parent == isolated_data_root / "cache"
    assert paths.logs_dir() == isolated_data_root / "logs"


def test_ensure_dirs_is_idempotent(isolated_data_root: Path) -> None:
    paths.ensure_dirs()
    paths.ensure_dirs()
    for directory in (paths.config_dir(), paths.logs_dir(), paths.cache_dir()):
        assert directory.is_dir()


def test_portable_marker_relocates_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A portable build keeps its data beside the executable, not in the user profile."""
    install = tmp_path / "app"
    install.mkdir()
    (install / "portable.marker").touch()

    monkeypatch.delenv("CEREPULSE_DATA_DIR", raising=False)
    monkeypatch.setattr(paths, "install_dir", lambda: install)
    paths.data_root.cache_clear()

    assert paths.is_portable() is True
    assert paths.data_root() == install / "Data"


def test_not_portable_without_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "install_dir", lambda: tmp_path)
    assert paths.is_portable() is False
