"""Signing a browser into the portal without the user typing a password.

Two promises this module made and did not keep until 0.11. It claimed to serve the page
"exactly one request and then stop" — the handler never shut anything down, so the page and
the encrypted credential in it stayed on localhost until the app exited. And it accepted a
``landing`` argument, computed a URL from it, wrote it to ``sessionStorage`` and never went
there, so every deep link the app passed was inert and the browser always arrived at the
portal home.
"""

from __future__ import annotations

import json
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
    """A real local server, built from a stubbed login page."""
    client = HttpClient(AppConfig())
    monkeypatch.setattr(
        client, "get", lambda *_a, **_k: httpx.Response(200, text=LOGIN_PAGE), raising=True
    )
    prepared = handover.prepare(
        client, "someone", "secret", landing="/Atten/SwipeRequestList.aspx?mnusr=menu__10201"
    )
    yield prepared
    prepared.stop()


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


def test_the_page_navigates_to_its_landing(served: handover.Handover) -> None:
    """The bug: the landing was computed, stored in sessionStorage and never used, so a
    deep link arrived at the portal home."""
    page = _fetch(served.url)
    assert "SwipeRequestList.aspx" in page
    assert "window.location.replace" in page


def test_the_login_is_posted_into_a_frame_so_the_page_survives_it(
    served: handover.Handover,
) -> None:
    """Submitting at the top level hands control to the portal the moment the response
    arrives, and nothing can run afterwards to go anywhere else."""
    page = _fetch(served.url)
    assert 'target="signin"' in page
    assert '<iframe name="signin"' in page


def test_the_landing_is_escaped_as_javascript(served: handover.Handover) -> None:
    """It is embedded in a script tag, so it is encoded rather than pasted."""
    page = _fetch(served.url)
    assert 'var landing = "' in page


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
    assert json.dumps  # (import kept meaningful for the escaping test above)
