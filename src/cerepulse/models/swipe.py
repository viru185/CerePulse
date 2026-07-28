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
        }
        return mapping.get(value, cls.UNKNOWN)


@dataclass(frozen=True, slots=True)
class SwipeRequest:
    """One filed swipe/regularization request and its current status."""

    for_date: date
    direction: str  # "In" or "Out" — the punch being corrected
    in_time: time | None
    out_time: time | None
    remark: str
    status: SwipeStatus
    approve_date: date | None = None
    category: str = ""

    @property
    def is_open(self) -> bool:
        return self.status is SwipeStatus.IN_PROCESS
