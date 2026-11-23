"""Error taxonomy (Chapter 10 section 7).

Every low-level exception is converted into one of these at a module boundary, so the UI
can decide what to show without knowing whether the cause was httpx, lxml, or sqlite3.
``user_message`` is the only text ever shown to a user; the original exception is preserved
in ``__cause__`` for the logs.
"""

from __future__ import annotations


class CerePulseError(Exception):
    """Base for every error this application raises deliberately."""

    user_message = "Something went wrong. Check the logs for details."


# --- Transport ---------------------------------------------------------------------


class TransportError(CerePulseError):
    """Network-level failure: DNS, TLS, connection reset, timeout."""

    user_message = "Network unavailable. Showing cached data."


class NetworkUnreachableError(TransportError):
    """The portal could not be reached at all: no route, no DNS, connection refused.

    Separate from a slow or failing *response* because it says something about every request
    that follows, not just this one. A laptop waking up has no network for a few seconds, and
    a sync that pressed on would spend four retries on each of its remaining steps finding
    that out again.
    """

    user_message = "You appear to be offline. Showing cached data."


class ServerUnavailableError(TransportError):
    """Portal returned 5xx after exhausting retries."""

    user_message = "The HR portal is not responding. Showing cached data."


# --- Authentication ----------------------------------------------------------------


class AuthenticationError(CerePulseError):
    """Credentials were rejected by the portal."""

    user_message = "Sign-in failed. Check your username and password."


class SessionExpiredError(CerePulseError):
    """The session is no longer valid and the operation must be replayed after re-auth."""

    user_message = "Your session expired. Reconnecting..."


class SessionTakenError(SessionExpiredError):
    """The session died while the app was still using it — something else signed in.

    SpineHR allows one session per user, so opening the portal in a browser ends the app's.
    That is a different situation from an ordinary idle timeout and must not be answered the
    same way: silently signing back in would take the session straight back off the browser
    the user is in the middle of using, and the two would trade it back and forth. The app
    steps aside instead and waits to be asked.
    """

    user_message = (
        "SpineHR is signed in somewhere else, so CerePulse has paused. "
        "The portal allows one session at a time."
    )


#: Failures that say something about every request still to come, not just the one that
#: failed. A loop that fetches item after item — days, months, pay documents — must stop on
#: these rather than record them per item and carry on: a lost session means more requests
#: against a session the app no longer holds (or, re-authenticating each time, an
#: authentication storm), a rejected password stays rejected, and an offline laptop is
#: offline for the next item too.
ENDS_THE_RUN: tuple[type[CerePulseError], ...] = (
    SessionExpiredError,
    AuthenticationError,
    NetworkUnreachableError,
)


# --- Protocol / parsing ------------------------------------------------------------


class ProtocolError(CerePulseError):
    """The portal responded, but not in the shape the client requires."""

    user_message = "The HR portal returned an unexpected response."


class ParserError(ProtocolError):
    """Page structure did not match what the parser expects.

    Raised rather than returning empty results, so a vendor-side UI change surfaces as a
    loud diagnostic instead of silently looking like a day with no attendance.
    """

    user_message = "Could not read the attendance page. The portal layout may have changed."


class PrivilegeError(ProtocolError):
    """The portal served its "insufficient privileges (ROLE)" page.

    Distinct from an expired session: signing in again will not help, because the fault is
    a stale menu navigation token. Recovery is to reload the menu and retry.
    """

    user_message = "The portal refused that page. Refreshing usually fixes it."


# --- Storage -----------------------------------------------------------------------


class RepositoryError(CerePulseError):
    """Local database failure: locked, corrupt, or a failed migration."""

    user_message = "Local data store is unavailable."


class MigrationError(RepositoryError):
    """Schema migration could not be applied."""

    user_message = "Could not upgrade the local database."


# --- Commute -----------------------------------------------------------------------


class DailyError(CerePulseError):
    """The quote or picture provider could not answer.

    Outside the portal hierarchy for the same reason :class:`CommuteError` is: a free quote
    API being down must never read as the HR portal being down, and it costs one tile.
    """


class CommuteError(CerePulseError):
    """The travel-time provider could not answer.

    Deliberately outside the transport/protocol hierarchy above: those describe SpineHR, and
    a maps provider being down must never read as the HR portal being down or as a session
    problem. Nothing about attendance depends on this, so it degrades to one card saying it
    could not work out the journey.
    """

    user_message = "Could not work out your journey home."


# --- Configuration -----------------------------------------------------------------


class ConfigError(CerePulseError):
    """Configuration is missing or invalid."""

    user_message = "Configuration is invalid. Check settings."
