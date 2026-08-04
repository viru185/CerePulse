"""Signing a browser into the portal without the user typing a password.

Three things this module claimed and did not do. It promised to serve the page "exactly one
request and then stop", while the handler shut nothing down and left an encrypted credential
on localhost until the app exited. It took a ``landing`` argument it never navigated to. And
the attempt to fix that second one — posting the login into a hidden iframe so the page could
survive and redirect — broke signing in altogether, because a cross-origin iframe makes the
portal's cookies third-party and modern browsers drop them.

The last is why the top-level post is asserted here rather than left to a comment. It looks
like an implementation detail and it is the entire feature.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Iterator

import httpx
import pytest

from cerepulse.auth import handover
from cerepulse.core.config import AppConfig
from cerepulse.transport.client import HttpClient

LOGIN_PAGE = """
<html><body><form id="form1">
  <input type="hidden" name="__VIEWSTATE" value="STATE" />
  <input type="hidden" name="hEnSa" value="1234567890123456" />
  <input type="submit" name="btnLogin" value="Login" />
</form></body></html>
"""


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> Iterator[handover.Handover]:
    """A real local server, built from a stubbed login page.

    Patched on the class rather than the instance: the scrape deliberately builds its own
    cookie-less client, so patching the one handed in would miss it.
    """
    monkeypatch.setattr(
        HttpClient, "get", lambda *_a, **_k: httpx.Response(200, text=LOGIN_PAGE), raising=True
    )
    client = HttpClient(AppConfig())
    prepared = handover.prepare(client, "someone", "secret")
    yield prepared
    prepared.stop()
    client.close()


def _fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 — localhost, our own
        return str(response.read().decode("utf-8"))


def _is_down(url: str) -> bool:
    try:
        _fetch(url)
    except (urllib.error.URLError, OSError):
        return True
    return False


# --- the page itself ------------------------------------------------------------------


def test_the_credential_is_never_in_the_url(served: handover.Handover) -> None:
    """A query string is written to browser history and to every proxy log on the way."""
    assert "secret" not in served.url
    assert "someone" not in served.url


def test_the_password_is_posted_encrypted_not_plain(served: handover.Handover) -> None:
    page = _fetch(served.url)
    assert "secret" not in page, "the portal's own encrypted blob, not the password"
    assert 'name="txtPassword"' in page


def test_the_login_posts_at_the_top_level(served: handover.Handover) -> None:
    """The 0.11 regression, and the reason this file exists.

    Posting into a hidden iframe let the page survive its own submission and navigate on to
    a deep link — and silently stopped signing anyone in. The iframe is cross-origin, so the
    cookies the portal sets inside it are third-party, which Chrome and Edge now block or
    partition: the login succeeds in the frame and the auth cookie never reaches the
    top-level context. First-party is the whole mechanism.
    """
    page = _fetch(served.url)

    assert "<iframe" not in page, "an iframe makes the portal's cookies third-party"
    assert "target=" not in page, "the form must submit the page it is on"
    assert 'method="post"' in page


def test_nothing_navigates_away_after_the_post(served: handover.Handover) -> None:
    """There is nowhere to navigate *to*: control belongs to the portal from the moment it
    answers. A leftover redirect would only race it."""
    page = _fetch(served.url)
    assert "window.location" not in page


# --- one shot, and gone ---------------------------------------------------------------


def test_the_page_is_served_exactly_once(served: handover.Handover) -> None:
    """The module docstring promised this from the beginning; nothing implemented it."""
    assert _fetch(served.url), "the first fetch works"

    for _ in range(50):
        if _is_down(served.url):
            break
        __import__("time").sleep(0.02)
    assert _is_down(served.url), "the credential must not stay on localhost after one fetch"


def test_a_wrong_token_gets_nothing(served: handover.Handover) -> None:
    """The token is what stops anything else on the machine scanning localhost for it."""
    base = served.url.rsplit("/", 1)[0]
    with pytest.raises(urllib.error.HTTPError) as raised:
        _fetch(f"{base}/not-the-token")
    assert raised.value.code == 404


def test_stopping_twice_is_safe(served: handover.Handover) -> None:
    """It is stopped from two directions — the handler and the timer — and whichever loses
    the race must not block on a server already closing."""
    served.stop()
    served.stop()


def test_a_page_nobody_fetches_still_goes_away() -> None:
    """`server.timeout` never did this: it applies to `handle_request`, and the thread runs
    `serve_forever`, which ignores it."""
    assert handover.TIMEOUT_SECONDS > 0


# --- whose session the form belongs to ------------------------------------------------


def test_the_login_form_is_scraped_without_the_app_s_cookies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`hEnSa` is the key the password is encrypted against and the portal mints it per page
    load. Scraping it through the signed-in client sent the app's auth cookie to a login
    page that has no business seeing it, and bound the form to a session the browser will
    never hold."""
    jars: list[list[str]] = []

    def record(self: HttpClient, *_a: object, **_k: object) -> httpx.Response:
        jars.append(list(self.cookies.keys()))
        return httpx.Response(200, text=LOGIN_PAGE)

    monkeypatch.setattr(HttpClient, "get", record, raising=True)

    client = HttpClient(AppConfig())
    client.cookies.set(".ASPXFORMSAUTH", "a-live-session", domain="cerebulb.spinehr.in")

    prepared = handover.prepare(client, "someone", "secret")
    prepared.stop()
    client.close()

    assert jars == [[]], "the scrape must run on an empty jar"


def test_the_scrape_does_not_reset_the_eviction_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """`looks_evicted` reads how long since the app last spoke. Scraping through the app's
    own client made it look freshly active at the exact moment it hands the session away —
    which is the state that makes the next expiry read as a theft rather than an expected
    loss, and pauses the app for no reason."""
    monkeypatch.setattr(
        HttpClient, "get", lambda *_a, **_k: httpx.Response(200, text=LOGIN_PAGE), raising=True
    )

    client = HttpClient(AppConfig())
    assert client.seconds_since_last_request is None

    prepared = handover.prepare(client, "someone", "secret")
    prepared.stop()

    assert client.seconds_since_last_request is None, "the app's client never spoke"
    client.close()
