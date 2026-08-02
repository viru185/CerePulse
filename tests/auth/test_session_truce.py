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
from cerepulse.core.errors import SessionExpiredError, SessionTakenError
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
