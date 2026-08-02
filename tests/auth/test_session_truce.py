"""Telling an idle timeout from having the session taken away.

SpineHR allows one session per user, so signing into the portal in a browser ends the
app's. Answering that the way an ordinary timeout is answered — signing straight back in —
takes the session off the browser the user is in the middle of using, and the two spend the
afternoon trading it back and forth. Over HTTP the two cases are the same redirect to the
login page; the only thing that separates them is whether the app was still active.
"""

from __future__ import annotations

import httpx
import pytest

from cerepulse.auth.manager import IDLE_TIMEOUT_SECONDS, AuthManager, SessionState
from cerepulse.core.config import AppConfig
from cerepulse.core.errors import SessionExpiredError, SessionTakenError, TransportError
from cerepulse.transport.client import HttpClient

LOGIN_REDIRECT = httpx.Response(302, headers={"location": "/login.aspx"})


@pytest.fixture
def auth() -> AuthManager:
    config = AppConfig()
    return AuthManager(HttpClient(config), config)


def _active(auth: AuthManager, *, seconds_ago: float) -> None:
    """Pretend the client last spoke to the portal that long ago."""
    import time

    auth._client._last_request_at = time.monotonic() - seconds_ago


def test_a_session_that_dies_while_we_are_active_was_taken(auth: AuthManager) -> None:
    _active(auth, seconds_ago=30)

    with pytest.raises(SessionTakenError):
        auth.check_response(LOGIN_REDIRECT)


def test_a_session_that_dies_after_a_long_idle_simply_timed_out(auth: AuthManager) -> None:
    _active(auth, seconds_ago=IDLE_TIMEOUT_SECONDS + 60)

    with pytest.raises(SessionExpiredError) as raised:
        auth.check_response(LOGIN_REDIRECT)
    assert not isinstance(raised.value, SessionTakenError)


def test_the_boundary_errs_towards_a_timeout(auth: AuthManager) -> None:
    """Getting it wrong this way costs one silent sign-in; the other way pauses the app
    for no reason."""
    _active(auth, seconds_ago=IDLE_TIMEOUT_SECONDS + 1)

    with pytest.raises(SessionExpiredError) as raised:
        auth.check_response(LOGIN_REDIRECT)
    assert not isinstance(raised.value, SessionTakenError)


def test_never_having_spoken_is_not_an_eviction(auth: AuthManager) -> None:
    """A session cannot be taken from a client that has not used one."""
    assert not auth.looks_evicted()


def test_either_way_the_session_is_marked_expired(auth: AuthManager) -> None:
    _active(auth, seconds_ago=5)

    with pytest.raises(SessionExpiredError):
        auth.check_response(LOGIN_REDIRECT)
    assert auth.state is SessionState.EXPIRED


def test_a_taken_session_is_still_an_expired_session(auth: AuthManager) -> None:
    """Subclassing matters: every existing `except SessionExpiredError` still catches it,
    so nothing that used to recover silently starts crashing instead."""
    assert issubclass(SessionTakenError, SessionExpiredError)


def test_the_coordinator_does_not_sign_back_in_after_an_eviction() -> None:
    """The whole point. Re-authenticating here is what starts the tug of war."""
    from cerepulse.services.sync import SyncCoordinator

    attempts = 0
    reauths = 0

    class FakeAuth:
        state = SessionState.EXPIRED

        def reauthenticate(self) -> None:
            nonlocal reauths
            reauths += 1

    class FakeGateway:
        def forget_menu(self) -> None: ...

    def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise SessionTakenError("taken")

    coordinator = SyncCoordinator(
        auth=FakeAuth(),  # type: ignore[arg-type]
        gateway=FakeGateway(),  # type: ignore[arg-type]
        attendance=None,  # type: ignore[arg-type]
        leave=None,  # type: ignore[arg-type]
    )

    with pytest.raises(SessionTakenError):
        coordinator.run(operation)

    assert attempts == 1, "the operation must not be replayed"
    assert reauths == 0, "the session must not be taken back"


def test_an_ordinary_expiry_is_still_replayed_once() -> None:
    from cerepulse.services.sync import SyncCoordinator

    attempts = 0

    class FakeAuth:
        state = SessionState.EXPIRED

        def reauthenticate(self) -> None: ...

    class FakeGateway:
        def forget_menu(self) -> None: ...

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise SessionExpiredError("timed out")
        return "ok"

    coordinator = SyncCoordinator(
        auth=FakeAuth(),  # type: ignore[arg-type]
        gateway=FakeGateway(),  # type: ignore[arg-type]
        attendance=None,  # type: ignore[arg-type]
        leave=None,  # type: ignore[arg-type]
    )

    assert coordinator.run(operation) == "ok"
    assert attempts == 2


# --- the whole sync, not just one step -----------------------------------------------------
#
# 0.9 shipped the truce with `run` covered and `sync_all` not, so nobody noticed that every
# step wrapped `run` in a handler which swallowed the eviction back up again. Testing the
# unit and not the path it is used from is exactly how that happens.


class _Recorder:
    """Stands in for both services. Every call is counted; some are told to be evicted."""

    def __init__(self, evicted_from: str = "") -> None:
        self.calls: list[str] = []
        self._evicted_from = evicted_from

    def _record(self, name: str) -> int:
        self.calls.append(name)
        if self._evicted_from and name == self._evicted_from:
            raise SessionTakenError("signed in somewhere else")
        return 0

    # attendance
    def refresh_month(self, *args: object, **kwargs: object) -> int:
        return self._record("attendance")

    def backfill_detail(self, *args: object, **kwargs: object) -> int:
        return self._record("day detail")

    def prune_history(self, *args: object, **kwargs: object) -> int:
        return self._record("cache cleanup")

    # leave
    def load_holidays(self, *args: object, **kwargs: object) -> int:
        return self._record("holidays")

    def refresh_swipe_requests(self, *args: object, **kwargs: object) -> int:
        return self._record("swipe requests")

    def refresh_leave(self, *args: object, **kwargs: object) -> int:
        return self._record("leave")

    def refresh_applications(self, *args: object, **kwargs: object) -> int:
        return self._record("applications")


def _coordinator(services: _Recorder):  # type: ignore[no-untyped-def]
    from cerepulse.services.sync import SyncCoordinator

    class FakeAuth:
        state = SessionState.AUTHENTICATED

        def reauthenticate(self) -> None:
            raise AssertionError("an eviction must never re-authenticate")

    class FakeGateway:
        def forget_menu(self) -> None: ...

    return SyncCoordinator(
        auth=FakeAuth(),  # type: ignore[arg-type]
        gateway=FakeGateway(),  # type: ignore[arg-type]
        attendance=services,  # type: ignore[arg-type]
        leave=services,  # type: ignore[arg-type]
    )


def test_a_full_sync_surfaces_an_eviction_instead_of_recording_it() -> None:
    """The 0.9 bug. Recorded as a step failure, it reached a report nobody inspects for
    session state, and the app carried on hammering a session it no longer owned."""
    services = _Recorder(evicted_from="attendance")

    with pytest.raises(SessionTakenError):
        _coordinator(services).sync_all("CIPL00364")


def test_a_full_sync_abandons_the_run_rather_than_finishing_it() -> None:
    """Every remaining step would fail the same way, and each attempt is another request
    taken back off whoever holds the session now."""
    services = _Recorder(evicted_from="attendance")

    with pytest.raises(SessionTakenError):
        _coordinator(services).sync_all("CIPL00364")

    assert services.calls == ["holidays", "attendance"], "nothing may run after the eviction"


def test_an_ordinary_failure_still_lets_the_rest_of_the_sync_run() -> None:
    """The collecting behaviour is right for independent failures and must survive."""

    class Flaky(_Recorder):
        def refresh_swipe_requests(self, *args: object, **kwargs: object) -> int:
            self.calls.append("swipe requests")
            raise TransportError("the portal is down")

    services = Flaky()
    report = _coordinator(services).sync_all("CIPL00364")

    assert not report.succeeded
    assert "leave" in services.calls
    assert "cache cleanup" in services.calls


# --- the third shape of a dead session -----------------------------------------------------


def _gateway(auth: AuthManager, body: str):  # type: ignore[no-untyped-def]
    """A gateway whose landing page is whatever `body` says it is."""
    from cerepulse.services.portal import PortalGateway

    class FakeClient:
        def get(self, *args: object, **kwargs: object) -> httpx.Response:
            return httpx.Response(200, text=body)

    return PortalGateway(FakeClient(), auth)  # type: ignore[arg-type]


MENU_PAGE = '<a href="/Atten/MyAttendanceReport.aspx?mnusr=menu__10101">Attendance</a>'


def test_a_landing_page_with_no_menu_is_a_dead_session_not_a_layout_change(
    auth: AuthManager,
) -> None:
    """It arrived as `ParserError: No navigation menu links found`, which sent an eviction
    down the "the vendor changed their HTML" path instead of the recovery one. A live
    session always gets a menu."""
    _active(auth, seconds_ago=30)

    with pytest.raises(SessionTakenError):
        _gateway(auth, "<html><body>Signed out.</body></html>").menu()


def test_the_same_bare_page_after_a_long_idle_is_only_a_timeout(auth: AuthManager) -> None:
    _active(auth, seconds_ago=IDLE_TIMEOUT_SECONDS + 60)

    with pytest.raises(SessionExpiredError) as raised:
        _gateway(auth, "<html><body>Signed out.</body></html>").menu()
    assert not isinstance(raised.value, SessionTakenError)


def test_a_real_menu_still_parses(auth: AuthManager) -> None:
    assert len(_gateway(auth, MENU_PAGE).menu()) == 1
