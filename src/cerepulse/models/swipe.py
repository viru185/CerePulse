"""Swipe-request domain model.

From the swipe-request list (``GridView1`` on SwipeRequestList). CerePulse is read-only, so
it never files a request — but it reads existing ones to answer "did I already apply for
that missed punch, and where does it stand?", which the spec calls for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from enum import Enum


class SwipeStatus(Enum):
    """Approval state of a swipe request."""

    IN_PROCESS = "in_process"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    #: Filed, never decided, and now past the date it could have been. The portal's own
    #: state, not one the app infers — it is one of the five options on the status filter.
    LAPSED = "lapsed"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, text: str) -> SwipeStatus:
        value = text.strip().casefold()
        mapping = {
            "in process": cls.IN_PROCESS,
            "pending": cls.IN_PROCESS,
            "approved": cls.APPROVED,
            "rejected": cls.REJECTED,
            "cancelled": cls.CANCELLED,
            "canceled": cls.CANCELLED,
            "lapsed": cls.LAPSED,
        }
        return mapping.get(value, cls.UNKNOWN)

    @property
    def is_decided(self) -> bool:
        """Whether the request has reached a final state and needs nothing further."""
        return self in (SwipeStatus.APPROVED, SwipeStatus.REJECTED, SwipeStatus.CANCELLED)


@dataclass(frozen=True, slots=True)
class SwipeRequest:
    """One filed swipe/regularization request and its current status."""

    for_date: date
    #: Which punch is being corrected: "In", "Out" — or "Both", where the portal fills both
    #: time columns and the request stands for the whole day.
    direction: str
    in_time: time | None
    out_time: time | None
    remark: str
    status: SwipeStatus
    #: The portal's "Approve Date" column: when the request was decided, not when it was
    #: filed. It reads oddly — seven July requests all carry 31-Jul-26 — but that is one
    #: approver clearing a month's backlog in a sitting, not a misread column. Confirmed
    #: against a capture taken after the sweep, where all seven show Approved on that date.
    #: Empty while a request is still In Process, which is what makes it worth showing.
    approve_date: date | None = None
    category: str = ""
    #: The portal's "Type" column ("Swipe"). Distinguishes this from the other kinds of
    #: regularisation the same grid can carry.
    kind: str = ""

    @property
    def is_open(self) -> bool:
        return self.status is SwipeStatus.IN_PROCESS

    @property
    def times(self) -> tuple[time, ...]:
        """The times this request asks for, in order — one for In or Out, two for Both.

        Every screen used to reach for ``in_time or out_time``, which short-circuits: a Both
        request for 9:00 AM to 6:00 PM rendered as "Both 9:00 AM" and the evening half was
        simply absent. The portal has carried two time columns all along.
        """
        return tuple(when for when in (self.in_time, self.out_time) if when is not None)

    @property
    def asked(self) -> str:
        """What was asked for: ``Both 9:00 AM to 6:00 PM``, ``In 9:00 AM``, or bare ``Out``.

        Here rather than in each view because there were five render sites and they disagreed:
        one printed a single time, four printed the direction alone — and "(Both)" with no
        times says less than "(In)" with none. Formatted on the model the way
        :meth:`Duration.as_clock` is, so the intelligence layer need not reach into ``ui``.
        """
        spoken = " to ".join(when.strftime("%I:%M %p").lstrip("0") for when in self.times)
        return f"{self.direction} {spoken}".strip()

    @property
    def identity(self) -> tuple[date, str, str]:
        """What makes this request *this* request, defined once for everyone who needs it.

        The portal exposes no id, no filed date and no approver, so identity has to be built
        from the fields a person cannot file twice with: the day, which punch, and what they
        wrote. Status is deliberately not part of it — a request that moves from pending to
        approved is the same request, and including it would make every approval look like a
        new row appearing beside the old one.

        This exists because there were two answers to the question. The fetch de-duplicated
        on day, punch and remark; the database keyed on day and punch alone. A portal that
        really does carry two requests for one day and punch lost one of them on save, and
        the mismatch was invisible from either side. One definition, used by both.
        """
        return (self.for_date, self.direction, self.remark)
