"""Authentication and session lifecycle (Chapter 05).

The observed login sequence::

    GET  /login.aspx          -> page + hidden state, including a fresh hEnSa
    POST /login.aspx          -> 302 to /start_new.aspx  (success)
                                 200 re-rendering login  (bad credentials)
    GET  /start_new.aspx      -> session established

Success is therefore identified by the redirect, which is why the transport layer does not
follow redirects. On success the server issues ``.ASPXFORMSAUTH`` and ``CompCode``
alongside the pre-existing ``ASP.NET_SessionId``.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum

import httpx
from loguru import logger

from cerepulse.auth.crypto import encrypt_password
from cerepulse.core.config import AppConfig
from cerepulse.core.errors import AuthenticationError, ProtocolError, SessionExpiredError
from cerepulse.transport import pages
from cerepulse.transport.client import HttpClient
from cerepulse.transport.webforms import WebFormsState

#: Cookie the portal sets once forms authentication succeeds.
AUTH_COOKIE = ".ASPXFORMSAUTH"

#: The control that raises the login event.
LOGIN_BUTTON = "btnLogin"


class SessionState(Enum):
    """Chapter 05 section 7. Every transition is logged."""

    ANONYMOUS = "anonymous"
    AUTHENTICATING = "authenticating"
    AUTHENTICATED = "authenticated"
    REFRESHING = "refreshing"
    EXPIRED = "expired"


class AuthManager:
    """Owns the session state machine. Contains no UI logic (Chapter 05 section 6)."""

    def __init__(self, client: HttpClient, config: AppConfig) -> None:
        self._client = client
        self._config = config
        self._state = SessionState.ANONYMOUS
        self._username = ""
        #: Called with no arguments when a replay needs fresh credentials.
        self.credential_provider: Callable[[], tuple[str, str]] | None = None

    # --- state ----------------------------------------------------------------------

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def username(self) -> str:
        return self._username

    @property
    def is_authenticated(self) -> bool:
        return self._state is SessionState.AUTHENTICATED and self._client.has_cookie(AUTH_COOKIE)

    def _transition(self, new_state: SessionState) -> None:
        if new_state is not self._state:
            logger.info("Session {} -> {}", self._state.value, new_state.value)
            self._state = new_state

    # --- login ----------------------------------------------------------------------

    def login(self, username: str, password: str) -> None:
        """Authenticate, or raise :class:`AuthenticationError`.

        The password never appears in logs — it is encrypted before it reaches the payload,
        and the logging layer redacts ``txtPassword`` regardless.
        """
        self._transition(SessionState.AUTHENTICATING)
        self._username = username

        state = self._load_login_state()
        payload = self._build_login_payload(state, username, password)

        response = self._client.post(
            pages.LOGIN,
            data=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": self._client.base_url,
                "Referer": self._client.url_for(pages.LOGIN),
            },
        )

        if response.status_code in (302, 303):
            self._complete_login(response)
            return

        if response.status_code == 200:
            self._transition(SessionState.ANONYMOUS)
            raise AuthenticationError(_extract_login_error(response.text))

        self._transition(SessionState.ANONYMOUS)
        raise ProtocolError(f"Unexpected status {response.status_code} from the login page")

    def _load_login_state(self) -> WebFormsState:
        response = self._client.get(pages.LOGIN)
        if response.status_code != 200:
            raise ProtocolError(f"Login page returned {response.status_code}")
        return WebFormsState.from_html(response.text)

    def _build_login_payload(
        self, state: WebFormsState, username: str, password: str
    ) -> dict[str, str]:
        """Echo the whole form back, overriding only what identifies this login attempt."""
        h_en_sa = state.require("hEnSa")
        portal = self._config.portal

        overrides = {
            "__LASTFOCUS": LOGIN_BUTTON,
            "txtUser": username,
            "txtPassword": encrypt_password(password, h_en_sa),
        }
        # Only set the tenant selectors when the page actually offers them.
        if "dpCompanyCodeList" in state.fields:
            overrides["dpCompanyCodeList"] = portal.company_code
        if "dpConnectAs" in state.fields:
            overrides["dpConnectAs"] = portal.connect_as

        payload = state.postback(LOGIN_BUTTON, "", **overrides)
        logger.debug("Login payload assembled with {} fields", len(payload))
        return payload

    def _complete_login(self, response: httpx.Response) -> None:
        location = response.headers.get("location", "")
        if pages.LOGIN.lstrip("/").lower() in location.lower():
            # Redirected back to login — a rejection expressed as a redirect.
            self._transition(SessionState.ANONYMOUS)
            raise AuthenticationError("Sign-in failed. Check your username and password.")

        if not self._client.has_cookie(AUTH_COOKIE):
            self._transition(SessionState.ANONYMOUS)
            raise ProtocolError("Login redirected but no authentication cookie was issued")

        home = self._client.get(pages.HOME)
        if home.status_code != 200:
            self._transition(SessionState.ANONYMOUS)
            raise ProtocolError(f"Landing page returned {home.status_code} after login")

        self._transition(SessionState.AUTHENTICATED)
        logger.info("Authenticated as {}", self._username)

    # --- session upkeep -------------------------------------------------------------

    def validate_session(self) -> bool:
        """Cheap liveness check, run before critical workflows rather than every action."""
        if not self._client.has_cookie(AUTH_COOKIE):
            self._transition(SessionState.EXPIRED)
            return False

        response = self._client.get(pages.HOME)
        if _is_login_redirect(response):
            self._transition(SessionState.EXPIRED)
            return False

        self._transition(SessionState.AUTHENTICATED)
        return True

    def check_response(self, response: httpx.Response) -> httpx.Response:
        """Raise :class:`SessionExpiredError` if the portal bounced us to the login page."""
        if _is_login_redirect(response):
            self._transition(SessionState.EXPIRED)
            raise SessionExpiredError("The portal redirected to the login page")
        return response

    def reauthenticate(self) -> None:
        """Re-run the login sequence using the configured credential provider."""
        if self.credential_provider is None:
            raise SessionExpiredError("Session expired and no credentials are available")

        self._transition(SessionState.REFRESHING)
        self._client.clear_cookies()
        username, password = self.credential_provider()
        self.login(username, password)

    def logout(self) -> None:
        try:
            self._client.get(pages.LOGOFF)
        except Exception:  # noqa: BLE001 — logout is best-effort; local state still clears
            logger.warning("Log-off request failed; clearing local session anyway")
        finally:
            self._client.clear_cookies()
            self._transition(SessionState.ANONYMOUS)


def _is_login_redirect(response: httpx.Response) -> bool:
    if response.status_code in (301, 302, 303, 307):
        return pages.LOGIN.lstrip("/").lower() in response.headers.get("location", "").lower()
    # Some expiry paths render the login page with a 200 instead of redirecting.
    if response.status_code == 200:
        body = response.text
        return 'name="txtPassword"' in body and 'name="hEnSa"' in body
    return False


def _extract_login_error(html: str) -> str:
    """Pull the portal's own error text out of the re-rendered login page, if present."""
    from lxml import html as lxml_html

    try:
        document = lxml_html.fromstring(html)
    except Exception:  # noqa: BLE001 — fall back to a generic message
        return "Sign-in failed. Check your username and password."

    for element in document.xpath("//*[contains(@id, 'lblErrorMsg')]"):
        text = element.text_content().strip().lstrip("*").strip()
        if text:
            return text
    return "Sign-in failed. Check your username and password."
