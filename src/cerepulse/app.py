"""Composition root: builds the object graph once, for every entry point to share.

Wiring lives here rather than in the UI so the GUI, the CLI, and tests all construct the
same application the same way. Nothing in this module makes decisions — it only assembles.

The context owns the HTTP client and the database, so closing it releases both. Sign-in is
deliberately separate from construction: the window should open and render cached data
before any credential is needed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from types import TracebackType
from typing import Self

from loguru import logger

from cerepulse.auth.manager import AuthManager
from cerepulse.core import paths, secrets
from cerepulse.core.config import AppConfig, load_config, save_config
from cerepulse.core.errors import AuthenticationError, CerePulseError
from cerepulse.repository.attendance import AttendanceRepository
from cerepulse.repository.database import Database, open_database
from cerepulse.repository.employee import Employee, EmployeeRepository
from cerepulse.repository.leave import (
    HolidayRepository,
    LeaveRepository,
    SwipeRequestRepository,
    SyncMetadataRepository,
)
from cerepulse.services.attendance import AttendanceService
from cerepulse.services.leave import LeaveService
from cerepulse.services.portal import PortalGateway
from cerepulse.services.sync import SyncCoordinator
from cerepulse.transport.client import HttpClient


@dataclass(slots=True)
class AppContext:
    """Every long-lived component, assembled and ready to use."""

    config: AppConfig
    database: Database
    client: HttpClient
    auth: AuthManager
    gateway: PortalGateway
    attendance: AttendanceService
    leave: LeaveService
    sync: SyncCoordinator
    employees: EmployeeRepository

    # --- lifecycle ------------------------------------------------------------------

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self.client.close()
        self.database.close()

    # --- identity -------------------------------------------------------------------

    @property
    def employee_code(self) -> str:
        """Who we are working as.

        Prefers the code the portal itself reported, since the login username and the
        employee code are not guaranteed to match.
        """
        cached = self.employees.find_any()
        if cached is not None:
            return cached.code
        return self.auth.username or self.config.portal.username

    def sign_in(self, username: str, password: str, *, remember: bool = False) -> str:
        """Authenticate and record the employee. Returns the resolved employee code.

        The username is persisted to config on success. Only the password belongs in the
        Credential Manager, and it is stored *under* the username — so without keeping the
        username there is nothing to look the password up by. Omitting it silently broke
        Remember me, the "signed in as" label, and session recovery all at once.
        """
        self.auth.login(username, password)
        if remember:
            secrets.store_password(username, password)

        employee = self.gateway.fetch_employee()
        self.employees.save(employee)
        self._remember_account(username, remember=remember)
        logger.info("Signed in as {}", employee.code)
        return employee.code

    def sign_in_with_saved_credentials(self) -> str:
        """Sign in using the stored password, if there is one."""
        username = self.saved_username
        password = secrets.get_password(username) if username else None
        if not username or not password:
            raise AuthenticationError("No saved credentials are available")
        return self.sign_in(username, password, remember=True)

    @property
    def saved_username(self) -> str:
        """The username to try on launch, preferring config over a live session."""
        return self.config.portal.username or self.auth.username

    def sign_out(self, *, forget: bool = False) -> None:
        self.auth.logout()
        if forget:
            username = self.saved_username
            if username:
                secrets.clear_password(username)
            self._remember_account("", remember=False)

    def _remember_account(self, username: str, *, remember: bool) -> None:
        """Persist the account details config is allowed to hold. Never the password."""
        portal = self.config.portal
        if portal.username == username and portal.remember_me == remember:
            return

        self.config = replace(
            self.config,
            portal=replace(portal, username=username, remember_me=remember),
        )
        try:
            save_config(self.config)
        except CerePulseError as exc:
            # Not fatal: the session is live either way, the user just gets asked again
            # next launch.
            logger.warning("Could not persist the account details: {}", exc)


def build_app(
    *,
    config: AppConfig | None = None,
    database_path: Path | str | None = None,
) -> AppContext:
    """Assemble the application. ``database_path`` is overridable for tests."""
    resolved = config or load_config()
    paths.ensure_dirs()

    database = open_database(database_path or paths.database_file())
    client = HttpClient(resolved)
    auth = AuthManager(client, resolved)
    gateway = PortalGateway(client, auth)

    attendance_repo = AttendanceRepository(database)
    leave_repo = LeaveRepository(database)
    swipe_repo = SwipeRequestRepository(database)
    holiday_repo = HolidayRepository(database)
    sync_repo = SyncMetadataRepository(database)
    employee_repo = EmployeeRepository(database)

    attendance = AttendanceService(
        gateway=gateway,
        attendance=attendance_repo,
        swipes=swipe_repo,
        holidays=holiday_repo,
        sync_meta=sync_repo,
        employees=employee_repo,
        config=resolved,
    )
    leave = LeaveService(
        gateway=gateway,
        leave=leave_repo,
        swipes=swipe_repo,
        holidays=holiday_repo,
        sync_meta=sync_repo,
        config=resolved,
    )
    sync = SyncCoordinator(auth=auth, gateway=gateway, attendance=attendance, leave=leave)

    context = AppContext(
        config=resolved,
        database=database,
        client=client,
        auth=auth,
        gateway=gateway,
        attendance=attendance,
        leave=leave,
        sync=sync,
        employees=employee_repo,
    )

    # Bound to the context, not to the config snapshot: signing in replaces the config on
    # the context, and a provider closed over the original would keep reading the empty
    # username it was built with.
    auth.credential_provider = _saved_credential_provider(context)
    return context


def _saved_credential_provider(context: AppContext) -> Callable[[], tuple[str, str]]:
    def provide() -> tuple[str, str]:
        username = context.saved_username
        password = secrets.get_password(username) if username else None
        if not username or not password:
            raise AuthenticationError("The session expired and no saved credentials are available")
        return username, password

    return provide


__all__ = ["AppContext", "Employee", "build_app"]
