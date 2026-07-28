from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx

from cerepulse.auth.crypto import decrypt_password
from cerepulse.auth.manager import AuthManager, SessionState
from cerepulse.core.config import AppConfig
from cerepulse.core.errors import AuthenticationError, ProtocolError, SessionExpiredError
from cerepulse.transport.client import HttpClient

BASE = "https://cerebulb.spinehr.in"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
AUTH_COOKIE_HEADER = ".ASPXFORMSAUTH=TICKET; path=/; HttpOnly"


@pytest.fixture
def login_html() -> str:
    return (FIXTURES / "login_page.html").read_text(encoding="utf-8")


@pytest.fixture
def auth() -> Iterator[AuthManager]:
    config = AppConfig.from_dict({"network": {"max_retries": 0, "backoff_factor": 0.0}})
    with HttpClient(config) as client:
        yield AuthManager(client, config)


def _mock_successful_login(login_html: str) -> respx.Route:
    respx.get(f"{BASE}/login.aspx").mock(return_value=httpx.Response(200, text=login_html))
    post = respx.post(f"{BASE}/login.aspx").mock(
        return_value=httpx.Response(
            302,
            headers=[("location", "/start_new.aspx"), ("set-cookie", AUTH_COOKIE_HEADER)],
        )
    )
    respx.get(f"{BASE}/start_new.aspx").mock(
        return_value=httpx.Response(200, text="<html>dashboard</html>")
    )
    return post


# --- successful login -----------------------------------------------------------------


@respx.mock
def test_login_succeeds_on_redirect(auth: AuthManager, login_html: str) -> None:
    _mock_successful_login(login_html)
    auth.login("CIPL00364", "hunter2")
    assert auth.state is SessionState.AUTHENTICATED
    assert auth.is_authenticated is True
    assert auth.username == "CIPL00364"


@respx.mock
def test_login_posts_every_form_field(auth: AuthManager, login_html: str) -> None:
    """The vendor rebuilds its control tree from the whole form, so all of it goes back."""
    post = _mock_successful_login(login_html)
    auth.login("CIPL00364", "hunter2")

    sent = dict(httpx.QueryParams(post.calls.last.request.content.decode()))
    for name in ("__VIEWSTATE", "__EVENTVALIDATION", "__VIEWSTATEGENERATOR", "hEnSa", "txtHdnPass"):
        assert name in sent
    assert sent["__EVENTTARGET"] == "btnLogin"
    assert sent["__LASTFOCUS"] == "btnLogin"
    assert sent["dpCompanyCodeList"] == "CEREBU"
    assert sent["dpConnectAs"] == "User"


@respx.mock
def test_password_is_encrypted_with_the_pages_salt(auth: AuthManager, login_html: str) -> None:
    post = _mock_successful_login(login_html)
    auth.login("CIPL00364", "hunter2")

    sent = dict(httpx.QueryParams(post.calls.last.request.content.decode()))
    assert sent["txtPassword"] != "hunter2"
    assert decrypt_password(sent["txtPassword"], sent["hEnSa"]) == "hunter2"


@respx.mock
def test_login_visits_the_landing_page(auth: AuthManager, login_html: str) -> None:
    _mock_successful_login(login_html)
    home = respx.get(f"{BASE}/start_new.aspx")
    auth.login("CIPL00364", "hunter2")
    assert home.called


# --- failure paths --------------------------------------------------------------------


@respx.mock
def test_rejected_credentials_raise(auth: AuthManager, login_html: str) -> None:
    """A failed sign-in re-renders the login page with 200 rather than redirecting."""
    respx.get(f"{BASE}/login.aspx").mock(return_value=httpx.Response(200, text=login_html))
    respx.post(f"{BASE}/login.aspx").mock(return_value=httpx.Response(200, text=login_html))

    with pytest.raises(AuthenticationError):
        auth.login("CIPL00364", "wrong")
    assert auth.state is SessionState.ANONYMOUS
    assert auth.is_authenticated is False


@respx.mock
def test_portal_error_text_is_surfaced(auth: AuthManager, login_html: str) -> None:
    failed = login_html.replace(
        '<span id="lblErrorMsg"></span>',
        '<span id="lblErrorMsg">* Invalid User Name or Password.</span>',
    )
    respx.get(f"{BASE}/login.aspx").mock(return_value=httpx.Response(200, text=login_html))
    respx.post(f"{BASE}/login.aspx").mock(return_value=httpx.Response(200, text=failed))

    with pytest.raises(AuthenticationError, match="Invalid User Name or Password"):
        auth.login("CIPL00364", "wrong")


@respx.mock
def test_redirect_back_to_login_is_a_rejection(auth: AuthManager, login_html: str) -> None:
    respx.get(f"{BASE}/login.aspx").mock(return_value=httpx.Response(200, text=login_html))
    respx.post(f"{BASE}/login.aspx").mock(
        return_value=httpx.Response(302, headers={"location": "/login.aspx?err=1"})
    )
    with pytest.raises(AuthenticationError):
        auth.login("CIPL00364", "hunter2")


@respx.mock
def test_redirect_without_auth_cookie_is_a_protocol_error(
    auth: AuthManager, login_html: str
) -> None:
    respx.get(f"{BASE}/login.aspx").mock(return_value=httpx.Response(200, text=login_html))
    respx.post(f"{BASE}/login.aspx").mock(
        return_value=httpx.Response(302, headers={"location": "/start_new.aspx"})
    )
    with pytest.raises(ProtocolError, match="no authentication cookie"):
        auth.login("CIPL00364", "hunter2")


@respx.mock
def test_missing_salt_is_a_parser_error(auth: AuthManager, login_html: str) -> None:
    """Losing hEnSa means the vendor changed the scheme — fail loudly, don't send plaintext."""
    stripped = login_html.replace(
        '<input name="hEnSa" type="hidden" id="hEnSa" value="1234567890123456" />', ""
    )
    respx.get(f"{BASE}/login.aspx").mock(return_value=httpx.Response(200, text=stripped))
    with pytest.raises(ProtocolError, match="hEnSa"):
        auth.login("CIPL00364", "hunter2")


# --- session lifecycle ----------------------------------------------------------------


@respx.mock
def test_validate_session_detects_expiry_via_redirect(auth: AuthManager, login_html: str) -> None:
    _mock_successful_login(login_html)
    auth.login("CIPL00364", "hunter2")

    respx.get(f"{BASE}/start_new.aspx").mock(
        return_value=httpx.Response(302, headers={"location": "/login.aspx"})
    )
    assert auth.validate_session() is False
    assert auth.state is SessionState.EXPIRED


@respx.mock
def test_validate_session_without_cookie_is_false(auth: AuthManager) -> None:
    assert auth.validate_session() is False
    assert auth.state is SessionState.EXPIRED


@respx.mock
def test_check_response_raises_on_login_redirect(auth: AuthManager) -> None:
    response = httpx.Response(302, headers={"location": "/login.aspx"})
    with pytest.raises(SessionExpiredError):
        auth.check_response(response)
    assert auth.state is SessionState.EXPIRED


@respx.mock
def test_check_response_detects_a_login_page_served_as_200(
    auth: AuthManager, login_html: str
) -> None:
    """Some expiry paths render the login form with 200 instead of redirecting."""
    with pytest.raises(SessionExpiredError):
        auth.check_response(httpx.Response(200, text=login_html))


@respx.mock
def test_check_response_passes_normal_pages_through(auth: AuthManager) -> None:
    response = httpx.Response(200, text="<html>attendance</html>")
    assert auth.check_response(response) is response


@respx.mock
def test_reauthenticate_uses_the_credential_provider(auth: AuthManager, login_html: str) -> None:
    _mock_successful_login(login_html)
    auth.credential_provider = lambda: ("CIPL00364", "hunter2")
    auth.reauthenticate()
    assert auth.state is SessionState.AUTHENTICATED


@respx.mock
def test_reauthenticate_without_a_provider_raises(auth: AuthManager) -> None:
    with pytest.raises(SessionExpiredError, match="no credentials"):
        auth.reauthenticate()


@respx.mock
def test_logout_clears_state(auth: AuthManager, login_html: str) -> None:
    _mock_successful_login(login_html)
    auth.login("CIPL00364", "hunter2")
    respx.get(f"{BASE}/LogOff.aspx").mock(return_value=httpx.Response(200))

    auth.logout()
    assert auth.state is SessionState.ANONYMOUS
    assert auth.is_authenticated is False


@respx.mock
def test_logout_survives_a_network_failure(auth: AuthManager, login_html: str) -> None:
    """Losing the network must not strand the app in a signed-in state."""
    _mock_successful_login(login_html)
    auth.login("CIPL00364", "hunter2")
    respx.get(f"{BASE}/LogOff.aspx").mock(side_effect=httpx.ConnectError("down"))

    auth.logout()
    assert auth.state is SessionState.ANONYMOUS
