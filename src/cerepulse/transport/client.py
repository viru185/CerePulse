"""The single persistent HTTP client (Chapter 03).

One client for the application lifetime, so connections and the cookie jar are reused
(Chapter 03 section 11). Redirects are deliberately **not** followed: the login flow
distinguishes success from failure by a ``302`` to ``start_new.aspx`` versus a ``200``
re-rendering the login page, and letting httpx follow it would erase that signal.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self
from urllib.parse import urljoin

import httpx
from loguru import logger

from cerepulse.core.config import AppConfig
from cerepulse.core.errors import NetworkUnreachableError, ServerUnavailableError, TransportError

#: Status codes worth retrying. Auth and client errors are never retried (section 8).
RETRYABLE_STATUS = frozenset({502, 503, 504})


@dataclass(frozen=True, slots=True)
class Continuity:
    """What happened between the portal's last two answers."""

    #: Seconds between them, counting any time asleep. ``None`` for the first answer.
    idle_seconds: float | None
    #: Whether a request failed at the network level in between: the laptop was offline,
    #: asleep, or between networks. Nothing the portal said after that can be read as
    #: evidence of who else was using the session.
    network_failed: bool


#: How long an idle connection is kept for reuse. Longer than any pause between two things
#: a person does, which is the point: the default of five seconds meant every interaction
#: began with a fresh TLS handshake.
KEEPALIVE_SECONDS = 120.0

_BROWSER_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
    "image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
)


class HttpClient:
    """Thin, retrying wrapper around ``httpx.Client`` scoped to one portal host."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        network = config.network
        self.base_url = config.portal.base_url.rstrip("/")
        #: When the portal last *answered*, on both clocks. See :attr:`continuity`.
        self._answered_at: tuple[float, float] | None = None
        #: Whether a request has failed at the network level since that answer.
        self._unreachable_since_answer = False
        #: What was true just before the most recent answer arrived.
        self._continuity = Continuity(idle_seconds=None, network_failed=False)

        self._client = httpx.Client(
            timeout=httpx.Timeout(
                connect=network.connect_timeout,
                read=network.read_timeout,
                write=network.write_timeout,
                pool=network.pool_timeout,
            ),
            # httpx defaults `keepalive_expiry` to 5 seconds, which is shorter than any gap
            # between two things a person does. Every request was therefore paying for a
            # fresh TCP and TLS handshake — five of them on a single day's sync, against a
            # remote host, for no reason. The portal keeps connections alive far longer than
            # this; the ceiling here is ours, not theirs.
            limits=httpx.Limits(
                max_keepalive_connections=8,
                max_connections=16,
                keepalive_expiry=KEEPALIVE_SECONDS,
            ),
            # `httpx[http2]` has been a declared dependency since the first release and the
            # flag was never passed, so the extra was installed and unused.
            http2=True,
            follow_redirects=False,
            headers={
                "User-Agent": network.user_agent,
                "Accept": _BROWSER_ACCEPT,
                "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
                "Upgrade-Insecure-Requests": "1",
            },
        )

    # --- lifecycle ------------------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- activity -------------------------------------------------------------------

    @property
    def continuity(self) -> Continuity:
        """How the most recent answer followed the one before it.

        The portal ends a session after twenty minutes of inactivity, so this is what
        separates "our session timed out, as expected" from "our session was taken while we
        were still using it". The two look identical over HTTP — both are a redirect to the
        login page — and they call for opposite responses.

        It is measured between *answers*, not attempts. The clock used to be stamped before
        each attempt was sent, failed ones included, so it always read the current request's
        own few milliseconds: every login redirect looked like an eviction. After a laptop
        woke up, a handful of ``getaddrinfo`` failures were enough to make a session that had
        been idle for six hours read as stolen a moment ago, and the app paused itself until
        it was restarted.
        """
        return self._continuity

    def _answered(self) -> None:
        """Record an answer, and what preceded it, for :attr:`continuity`."""
        now = (time.monotonic(), time.time())
        idle = None
        if self._answered_at is not None:
            # The larger of the two: the monotonic clock is immune to the wall clock being
            # reset, and the wall clock is certain to include time spent asleep.
            idle = max(now[0] - self._answered_at[0], now[1] - self._answered_at[1])
        self._continuity = Continuity(
            idle_seconds=idle, network_failed=self._unreachable_since_answer
        )
        self._answered_at = now
        self._unreachable_since_answer = False

    # --- cookies --------------------------------------------------------------------

    @property
    def cookies(self) -> httpx.Cookies:
        return self._client.cookies

    def has_cookie(self, name: str) -> bool:
        return self._client.cookies.get(name) is not None

    def clear_cookies(self) -> None:
        self._client.cookies.clear()

    # --- requests -------------------------------------------------------------------

    def url_for(self, path: str) -> str:
        """Resolve a portal-relative path against the configured base URL."""
        if path.startswith(("http://", "https://")):
            return path
        return urljoin(f"{self.base_url}/", path.lstrip("/"))

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send a request, retrying only transient failures (Chapter 03 section 8)."""
        url = self.url_for(path)
        attempts = self.config.network.max_retries + 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            started = time.monotonic()
            try:
                response = self._client.request(method, url, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                self._unreachable_since_answer = True
                elapsed_ms = (time.monotonic() - started) * 1000
                logger.warning(
                    "{} {} failed after {:.0f}ms (attempt {}/{}): {}",
                    method,
                    url,
                    elapsed_ms,
                    attempt + 1,
                    attempts,
                    type(exc).__name__,
                )
                if not self._sleep_before_retry(attempt, attempts):
                    error = (
                        NetworkUnreachableError
                        if isinstance(exc, httpx.ConnectError)
                        else TransportError
                    )
                    raise error(f"{method} {url} failed: {exc}") from exc
                continue

            self._answered()
            elapsed_ms = (time.monotonic() - started) * 1000
            logger.debug(
                "{} {} -> {} in {:.0f}ms (attempt {}/{})",
                method,
                url,
                response.status_code,
                elapsed_ms,
                attempt + 1,
                attempts,
            )

            if response.status_code in RETRYABLE_STATUS:
                last_error = ServerUnavailableError(f"{url} returned {response.status_code}")
                if not self._sleep_before_retry(attempt, attempts):
                    raise ServerUnavailableError(
                        f"{method} {url} returned {response.status_code} after {attempts} attempts"
                    )
                continue

            return response

        # Unreachable in practice: the loop either returns or raises.
        raise TransportError(f"{method} {url} failed") from last_error

    def _sleep_before_retry(self, attempt: int, attempts: int) -> bool:
        """Back off exponentially. Returns False when no attempts remain."""
        if attempt >= attempts - 1:
            return False
        delay = self.config.network.backoff_factor * (2**attempt)
        logger.debug("Retrying in {:.2f}s", delay)
        time.sleep(delay)
        return True
