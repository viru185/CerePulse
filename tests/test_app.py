"""Composition root — assembly, identity resolution, and credential wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from cerepulse.app import build_app
from cerepulse.core.config import AppConfig
from cerepulse.core.errors import AuthenticationError
from cerepulse.repository.employee import Employee


@pytest.fixture
def app_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CEREPULSE_DATA_DIR", str(tmp_path))
    from cerepulse.core import paths

    paths.data_root.cache_clear()
    context = build_app(config=AppConfig(), database_path=tmp_path / "test.db")
    yield context
    context.close()
    paths.data_root.cache_clear()


def test_everything_is_wired(app_context) -> None:  # type: ignore[no-untyped-def]
    assert app_context.attendance is not None
    assert app_context.leave is not None
    assert app_context.sync is not None
    assert app_context.gateway is not None


def test_the_database_is_migrated_on_build(app_context) -> None:  # type: ignore[no-untyped-def]
    from cerepulse.repository.schema import SCHEMA_VERSION, current_version

    assert current_version(app_context.database.connection) == SCHEMA_VERSION


def test_services_share_one_database(app_context) -> None:  # type: ignore[no-untyped-def]
    """A month written through one service must be visible to the others."""
    app_context.employees.save(Employee(code="CIPL00364", name="Test"))
    assert app_context.employee_code == "CIPL00364"


def test_the_cached_employee_wins_over_the_login_name(
    app_context,
) -> None:  # type: ignore[no-untyped-def]
    """The login username and the employee code are not guaranteed to match."""
    app_context.employees.save(Employee(code="FROMPORTAL"))
    assert app_context.employee_code == "FROMPORTAL"


def test_employee_code_falls_back_to_config(tmp_path: Path) -> None:
    from dataclasses import replace

    base = AppConfig()
    config = replace(base, portal=replace(base.portal, username="CIPL00364"))
    with build_app(config=config, database_path=tmp_path / "x.db") as context:
        assert context.employee_code == "CIPL00364"


def test_the_credential_provider_refuses_when_nothing_is_saved(
    app_context,
) -> None:  # type: ignore[no-untyped-def]
    """Recovery must fail with a clear message rather than looping on empty credentials."""
    assert app_context.auth.credential_provider is not None
    with pytest.raises(AuthenticationError, match="no saved credentials"):
        app_context.auth.credential_provider()


def test_signing_in_without_saved_credentials_raises(
    app_context,
) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(AuthenticationError, match="No saved credentials"):
        app_context.sign_in_with_saved_credentials()


def test_closing_releases_both_resources(tmp_path: Path) -> None:
    from cerepulse.core.errors import RepositoryError

    context = build_app(config=AppConfig(), database_path=tmp_path / "y.db")
    context.close()

    with pytest.raises(RepositoryError, match="not open"):
        _ = context.database.connection
