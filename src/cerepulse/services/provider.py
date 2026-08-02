"""What CerePulse needs from an HR portal, stated apart from how SpineHR provides it.

Everything above ``services/`` already talks to :class:`~cerepulse.services.portal.
PortalGateway` and nothing else, so the seam is really already there — this names it. A
second portal becomes a second implementation of :class:`Provider` rather than a rewrite,
and more usefully today, it draws a line around the surface that is allowed to be
vendor-specific.

That surface is deliberately small, and every method on it is a *question about the
employee's own record*. Nothing here writes: CerePulse is read-only against the HR system by
design, and a protocol with no write methods is the cheapest way to keep it that way — an
implementation cannot file a request through an interface that has no verb for it.

This is a ``typing.Protocol``, so it is structural. ``PortalGateway`` does not inherit from
it and needs no changes to satisfy it; the check is static, and ``tests/services`` asserts
it holds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from cerepulse.models.attendance import AttendanceMonth, Punch
    from cerepulse.models.employee import Employee
    from cerepulse.models.leave import Holiday, LeaveBalance, LeaveTransaction
    from cerepulse.models.swipe import SwipeRequest
    from cerepulse.parsers.attendance import ParsedDay


@runtime_checkable
class Provider(Protocol):
    """A source of attendance and leave for one signed-in employee."""

    def fetch_employee(self) -> Employee:
        """Who the session belongs to. Also the cheapest proof the session still works."""
        ...

    def fetch_month(self, year: int, month: int) -> tuple[AttendanceMonth, list[ParsedDay]]:
        """One month's attendance grid, plus whatever each row needs to fetch its detail.

        The second element is deliberately opaque to callers: it carries the postback
        controls SpineHR needs, and another provider is free to put something else there.
        """
        ...

    def fetch_day_detail(self, day: ParsedDay) -> list[Punch]:
        """One day's punch log. Takes a row from :meth:`fetch_month`, not a date.

        That signature is not an accident of SpineHR's design leaking out — it is the
        honest one. The punch log is reached *through* the month it belongs to, and a
        provider that can answer by date alone can simply ignore everything but the date.
        """
        ...

    def fetch_leave(self) -> tuple[list[LeaveBalance], list[LeaveTransaction]]:
        """Leave balances and the ledger movements behind them."""
        ...

    def fetch_swipe_requests(self) -> list[SwipeRequest]:
        """Attendance-correction requests already filed."""
        ...

    def fetch_holidays(self) -> list[Holiday]:
        """The company holiday calendar."""
        ...

    def available_periods(self, html: str) -> list[tuple[int, int]]:
        """Which months this provider will serve, newest first."""
        ...


__all__ = ["Provider"]
