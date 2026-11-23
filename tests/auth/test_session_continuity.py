"""The eviction clock, driven through the real request path rather than set by hand.

The truce tests above set the clock directly, which is why none of them could see that the
clock was stamped before every attempt — failed ones included — and so always read the
current request's own few milliseconds. In production every login redirect therefore looked
like a theft, and after a laptop woke up the app paused itself and stayed paused until it
was restarted. These tests reproduce the sequences from the logs of 22 and 23 November 2026.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import httpx
import pytest

from cerepulse.auth.manager import IDLE_TIMEOUT_SECONDS, AuthManager, SessionState
from cerepulse.core.config import AppConfig
from cerepulse.core.errors import (
    NetworkUnreachableError,
    SessionExpiredError,
    SessionTakenError,
    TransportError,
)
from cerepulse.transport import client as client_module
from cerepulse.transport.client import HttpClient

PAGE = '<a href="/x.aspx?mnusr=menu__1">x</a>'


class Clock:
    """Both clocks the client reads, moved together — or not, as a sleeping laptop does."""

    def __init__(self) -> None:
        self.monotonic = 1000.0
        self.wall = 1_700_000_000.0

    def advance(self, seconds: float, *, asleep: bool = False) -> None:
        # Asleep, the wall clock moves and a monotonic clock may not. Either way the gap
        # must count.
        self.wall += seconds
        if not asleep:
            self.monotonic += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(client_module.time, "monotonic", lambda: fake.monotonic)
    monkeypatch.setattr(client_module.time, "time", lambda: fake.wall)
    monkeypatch.setattr(client_module.time, "sleep", lambda _seconds: None)
    return fake


Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def portal() -> Iterator[tuple[AuthManager, list[Handler]]]:
    """An authenticated session over a transport whose answers the test scripts."""
    script: list[Handler] = []

    def respond(request: httpx.Request) -> httpx.Response:
        return script.pop(0)(request)

    config = AppConfig()
    http = HttpClient(config)
    http._client = httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=False)
    auth = AuthManager(http, config)
    auth._state = SessionState.AUTHENTICATED
    yield auth, script
    http.close()


def ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text=PAGE)


def to_login(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(302, headers={"location": "/login.aspx"})


def offline(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("[Errno 11001] getaddrinfo failed", request=request)


def fetch(auth: AuthManager) -> httpx.Response:
    return auth.check_response(auth._client.get("/page.aspx"))


def test_a_redirect_moments_after_a_live_answer_is_a_theft(portal, clock) -> None:  # type: ignore[no-untyped-def]
    auth, script = portal
    script += [ok, to_login]
    fetch(auth)
    clock.advance(30)

    with pytest.raises(SessionTakenError):
        fetch(auth)


def test_waking_after_hours_is_a_timeout_even_if_the_network_comes_back_slowly(
    portal,  # type: ignore[no-untyped-def]
    clock,  # type: ignore[no-untyped-def]
) -> None:
    """22 Nov, 08:44: idle since 02:19, two ConnectErrors on wake, then the login redirect.
    It read as "signed in somewhere else" and the app sat paused for a day."""
    auth, script = portal
    script += [ok]
    fetch(auth)
    clock.advance(6.5 * 3600, asleep=True)
    script += [offline] * 4 + [offline, offline, to_login]

    with pytest.raises(NetworkUnreachableError):
        fetch(auth)
    with pytest.raises(SessionExpiredError) as raised:
        fetch(auth)
    assert not isinstance(raised.value, SessionTakenError)


def test_any_network_failure_in_between_rules_out_a_theft(portal, clock) -> None:  # type: ignore[no-untyped-def]
    """23 Nov, 18:24: two ConnectErrors, then a redirect inside the idle window. Whatever
    happened while the laptop could not reach the portal, the app cannot know it was a theft."""
    auth, script = portal
    script += [ok]
    fetch(auth)
    clock.advance(60)
    script += [offline, offline, to_login]

    with pytest.raises(SessionExpiredError) as raised:
        fetch(auth)
    assert not isinstance(raised.value, SessionTakenError)


def test_sleep_counts_even_when_the_monotonic_clock_stood_still(portal, clock) -> None:  # type: ignore[no-untyped-def]
    auth, script = portal
    script += [ok, to_login]
    fetch(auth)
    clock.advance(IDLE_TIMEOUT_SECONDS + 60, asleep=True)

    with pytest.raises(SessionExpiredError) as raised:
        fetch(auth)
    assert not isinstance(raised.value, SessionTakenError)


def test_the_first_answer_of_a_run_is_never_a_theft(portal, clock) -> None:  # type: ignore[no-untyped-def]
    """21 Nov, 21:04: the first request at launch, before signing in, was reported as
    "SpineHR is signed in somewhere else"."""
    auth, script = portal
    script += [to_login]

    with pytest.raises(SessionExpiredError) as raised:
        fetch(auth)
    assert not isinstance(raised.value, SessionTakenError)


def test_a_session_already_found_dead_is_not_taken_again(portal, clock) -> None:  # type: ignore[no-untyped-def]
    """23 Nov, 23:16: Pay's three requests, each redirected, each reported as a new theft."""
    auth, script = portal
    script += [ok]
    fetch(auth)
    clock.advance(IDLE_TIMEOUT_SECONDS + 60)
    script += [to_login, to_login, to_login]

    kinds = []
    for _ in range(3):
        with pytest.raises(SessionExpiredError) as raised:
            fetch(auth)
        kinds.append(type(raised.value).__name__)
    assert kinds == ["SessionExpiredError"] * 3


def test_a_session_that_was_never_live_cannot_be_taken(portal, clock) -> None:  # type: ignore[no-untyped-def]
    """A sign-in that failed leaves nothing to take; reading the next redirect as a theft
    reported a rejected password as "signed in somewhere else"."""
    auth, script = portal
    auth._state = SessionState.ANONYMOUS
    script += [ok, to_login]
    fetch(auth)
    clock.advance(5)

    with pytest.raises(SessionExpiredError) as raised:
        fetch(auth)
    assert not isinstance(raised.value, SessionTakenError)


def test_a_connection_failure_is_reported_as_offline(portal, clock) -> None:  # type: ignore[no-untyped-def]
    auth, script = portal
    script += [offline] * 4

    with pytest.raises(NetworkUnreachableError):
        fetch(auth)
    assert issubclass(NetworkUnreachableError, TransportError), "existing handlers still catch it"
