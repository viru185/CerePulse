"""One chronological record of everything that was not an ordinary working day.

Leave and Requests were two screens built on the same question — "what happened to my
time?" — split by which portal page the answer came from. That is the vendor's filing
system, not the user's: a week in June was outdoor duty on the muster, a comp-off credit in
the leave ledger, and a swipe request in a third list, and reconstructing it meant reading
three screens and holding the dates in your head.

This merges them into one stream. The source of each entry is kept, because it is sometimes
the explanation — a day that is leave on the muster *and* has a rejected swipe request is a
different situation from either alone — but the ordering is time, not provenance.

Pure: it takes what the caches already hold and returns a list. Nothing here fetches.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from cerepulse.models.attendance import AttendanceDay, DayStatus
from cerepulse.models.leave import Holiday, LeaveTransaction
from cerepulse.models.swipe import SwipeRequest, SwipeStatus


class RecordKind(Enum):
    """What sort of entry this is. The filter on the screen is exactly this list."""

    LEAVE = "leave"
    OUTDOOR_DUTY = "outdoor_duty"
    COMP_OFF_EARNED = "comp_off_earned"
    COMP_OFF_SPENT = "comp_off_spent"
    SWIPE_REQUEST = "swipe_request"
    HOLIDAY = "holiday"
    ABSENCE = "absence"

    @property
    def label(self) -> str:
        return {
            RecordKind.LEAVE: "Leave",
            RecordKind.OUTDOOR_DUTY: "Outdoor duty",
            RecordKind.COMP_OFF_EARNED: "Comp-off earned",
            RecordKind.COMP_OFF_SPENT: "Comp-off taken",
            RecordKind.SWIPE_REQUEST: "Swipe request",
            RecordKind.HOLIDAY: "Holiday",
            RecordKind.ABSENCE: "Absence",
        }[self]


@dataclass(frozen=True, slots=True)
class Record:
    """One thing that happened on one day."""

    day: date
    kind: RecordKind
    title: str
    detail: str = ""
    #: Set for entries that are still waiting on somebody — an unapproved swipe request.
    pending: bool = False
    #: Set for entries the user has to do something about — a rejection, an unexplained day.
    needs_action: bool = False

    @property
    def is_settled(self) -> bool:
        return not (self.pending or self.needs_action)


def build_records(
    *,
    days: list[AttendanceDay] | None = None,
    requests: list[SwipeRequest] | None = None,
    transactions: list[LeaveTransaction] | None = None,
    holidays: list[Holiday] | None = None,
) -> list[Record]:
    """Assemble every non-ordinary day into one list, newest first.

    Ordinary working days are excluded on purpose. A record of everything is a copy of the
    attendance table, and the point of this screen is what stands out from it.
    """
    records: list[Record] = []
    records += _from_days(days or [])
    records += _from_requests(requests or [])
    records += _from_transactions(transactions or [])
    records += _from_holidays(holidays or [], days or [])

    # Newest first, and within a day the things needing attention lead. A rejection and a
    # holiday on the same date should not be ordered by which list they came from.
    return sorted(
        records, key=lambda item: (item.day, item.needs_action, item.pending), reverse=True
    )


def _from_days(days: list[AttendanceDay]) -> list[Record]:
    records: list[Record] = []
    for day in days:
        remark = day.remarks.strip()
        note = "" if remark == _ROUTINE_REMARK else remark

        if day.has_outdoor_duty:
            records.append(
                Record(
                    day.day,
                    RecordKind.OUTDOOR_DUTY,
                    "Outdoor duty",
                    note or "Worked off site; no swipes to measure.",
                )
            )
            continue
        if day.status is DayStatus.LEAVE:
            records.append(Record(day.day, RecordKind.LEAVE, "Leave", note))
        elif day.status is DayStatus.ABSENT:
            records.append(
                Record(
                    day.day,
                    RecordKind.ABSENCE,
                    "Absent",
                    note or "Marked absent by the portal.",
                    needs_action=True,
                )
            )
        elif day.status is DayStatus.HALF_DAY:
            records.append(Record(day.day, RecordKind.LEAVE, "Half day", note))
    return records


def _from_requests(requests: list[SwipeRequest]) -> list[Record]:
    records = []
    for request in requests:
        status = request.status
        detail = f"{request.direction}" + (f" — {request.remark}" if request.remark else "")
        records.append(
            Record(
                request.for_date,
                RecordKind.SWIPE_REQUEST,
                f"Swipe request {_status_word(status)}",
                detail,
                pending=status is SwipeStatus.IN_PROCESS,
                needs_action=status is SwipeStatus.REJECTED,
            )
        )
    return records


def _from_transactions(transactions: list[LeaveTransaction]) -> list[Record]:
    """Comp-off movements from the leave ledger.

    Only comp-off, and only rows carrying a date. The ledger's other rows are running
    balances rather than events, and an entry with no date cannot be placed on a timeline —
    the same reason comp-off expiry is reported as UNKNOWN rather than invented.
    """
    records = []
    for entry in transactions:
        if entry.transaction_date is None or not _is_comp_off(entry.leave_type):
            continue
        if entry.credit_days > 0:
            records.append(
                Record(
                    entry.transaction_date,
                    RecordKind.COMP_OFF_EARNED,
                    f"Comp-off earned — {entry.credit_days:g} day(s)",
                    entry.remark,
                )
            )
        if entry.consumed_days > 0:
            records.append(
                Record(
                    entry.transaction_date,
                    RecordKind.COMP_OFF_SPENT,
                    f"Comp-off taken — {entry.consumed_days:g} day(s)",
                    entry.remark,
                )
            )
    return records


def _from_holidays(holidays: list[Holiday], days: list[AttendanceDay]) -> list[Record]:
    """Company holidays, but only the ones inside the range the attendance covers.

    The holiday calendar runs a full year ahead. Listing all of it would bury the things
    that actually happened under a column of dates nobody has reached yet.
    """
    if not days:
        return []
    first, last = min(day.day for day in days), max(day.day for day in days)
    return [
        Record(holiday.day, RecordKind.HOLIDAY, holiday.name or "Company holiday")
        for holiday in holidays
        if first <= holiday.day <= last
    ]


#: What the portal writes in Remarks on an ordinary day; it says nothing.
_ROUTINE_REMARK = "Attendance Muster"


def _is_comp_off(leave_type: str) -> bool:
    from cerepulse.models.leave import LeaveCategory

    return LeaveCategory.classify(leave_type) is LeaveCategory.COMP_OFF


def _status_word(status: SwipeStatus) -> str:
    return {
        SwipeStatus.IN_PROCESS: "pending",
        SwipeStatus.APPROVED: "approved",
        SwipeStatus.REJECTED: "rejected",
        SwipeStatus.CANCELLED: "cancelled",
    }.get(status, "filed")


__all__ = ["Record", "RecordKind", "build_records"]
