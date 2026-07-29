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


# --- account persistence --------------------------------------------------------------


def test_signing_in_persists_the_username(app_context, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Regression: the username was never written, so Remember me could not work.

    The password lives in the Credential Manager keyed *by* username — without keeping the
    username there is nothing to look it up by. Losing it silently broke the "signed in as"
    label, silent sign-in on launch, and session recovery all at once.
    """
    monkeypatch.setattr(app_context.auth, "login", lambda *_: None)
    monkeypatch.setattr(app_context.gateway, "fetch_employee", lambda: Employee(code="CIPL00364"))
    stored: dict[str, str] = {}
    monkeypatch.setattr(
        "cerepulse.core.secrets.store_password",
        lambda user, pwd: stored.__setitem__(user, pwd) or True,
    )

    app_context.sign_in("CIPL00364", "secret", remember=True)

    assert app_context.config.portal.username == "CIPL00364"
    assert app_context.config.portal.remember_me is True
    assert app_context.saved_username == "CIPL00364"
    assert stored == {"CIPL00364": "secret"}


def test_the_credential_provider_sees_a_later_sign_in(app_context, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The provider is bound to the context, not to the config it was built with."""
    monkeypatch.setattr(app_context.auth, "login", lambda *_: None)
    monkeypatch.setattr(app_context.gateway, "fetch_employee", lambda: Employee(code="CIPL00364"))
    monkeypatch.setattr("cerepulse.core.secrets.store_password", lambda *_: True)
    monkeypatch.setattr("cerepulse.core.secrets.get_password", lambda user: "secret")

    app_context.sign_in("CIPL00364", "secret", remember=True)

    assert app_context.auth.credential_provider is not None
    assert app_context.auth.credential_provider() == ("CIPL00364", "secret")


def test_signing_out_and_forgetting_clears_the_username(app_context, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(app_context.auth, "login", lambda *_: None)
    monkeypatch.setattr(app_context.auth, "logout", lambda: None)
    monkeypatch.setattr(app_context.gateway, "fetch_employee", lambda: Employee(code="CIPL00364"))
    monkeypatch.setattr("cerepulse.core.secrets.store_password", lambda *_: True)
    cleared: list[str] = []
    monkeypatch.setattr("cerepulse.core.secrets.clear_password", cleared.append)

    app_context.sign_in("CIPL00364", "secret", remember=True)
    app_context.sign_out(forget=True)

    assert cleared == ["CIPL00364"]
    assert app_context.config.portal.username == ""
    assert app_context.config.portal.remember_me is False
