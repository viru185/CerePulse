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
from datetime import date, time, timedelta
from enum import Enum

from cerepulse.models.application import Application, ApplicationKind
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
    #: Where a request stands, for the kinds of entry that are requests. Empty for the ones
    #: that are not: a holiday has no approval state and should not be given a blank chip.
    status: str = ""

    @property
    def is_settled(self) -> bool:
        return not (self.pending or self.needs_action)


def build_records(
    *,
    days: list[AttendanceDay] | None = None,
    requests: list[SwipeRequest] | None = None,
    transactions: list[LeaveTransaction] | None = None,
    holidays: list[Holiday] | None = None,
    applications: list[Application] | None = None,
) -> list[Record]:
    """Assemble every non-ordinary day into one list, newest first.

    Ordinary working days are excluded on purpose. A record of everything is a copy of the
    attendance table, and the point of this screen is what stands out from it.
    """
    filed = applications or []
    records: list[Record] = []
    records += _from_days(days or [], filed)
    records += _from_requests(requests or [])
    records += _from_transactions(transactions or [], filed)
    records += _from_holidays(holidays or [], days or [])
    # Last, and only what the muster and the ledger did not already account for. An approved
    # week of outdoor duty is on the muster; listing the application beside it would be the
    # same week twice, which is the duplication merging the two screens was meant to end.
    records += _from_applications(filed, records)

    # Newest first, and within a day the things needing attention lead. A rejection and a
    # holiday on the same date should not be ordered by which list they came from.
    return sorted(
        records, key=lambda item: (item.day, item.needs_action, item.pending), reverse=True
    )


@dataclass(frozen=True, slots=True)
class HolidayEntry:
    """One company holiday, placed relative to today."""

    holiday: Holiday
    #: The date is behind us. Rendered dimmed rather than hidden — "what have I already had"
    #: is half of what anyone opens a holiday list to find out.
    has_passed: bool
    #: The soonest one still ahead. Exactly one entry carries this, unless the year is spent.
    is_next: bool

    @property
    def day(self) -> date:
        return self.holiday.day


def holiday_calendar(holidays: list[Holiday], *, today: date) -> list[HolidayEntry]:
    """The published calendar in date order, with the past marked and the next one flagged.

    Separate from :func:`build_records` on purpose. The timeline answers "what happened to my
    time" and bounds holidays to the month on screen, because a calendar running a year ahead
    would bury the things that actually happened. This answers a different question — "what
    days off does the company give, and which are left" — and for that the whole year is the
    point.

    Ascending, unlike the timeline: a calendar is read forwards.
    """
    ordered = sorted(holidays, key=lambda holiday: holiday.day)
    upcoming = next((holiday.day for holiday in ordered if holiday.day >= today), None)
    return [
        HolidayEntry(
            holiday=holiday,
            has_passed=holiday.day < today,
            is_next=holiday.day == upcoming,
        )
        for holiday in ordered
    ]


def _from_days(days: list[AttendanceDay], applications: list[Application]) -> list[Record]:
    """The muster's own view of a day, with the filed application's status where there is one.

    The muster says a day was outdoor duty; only the application says the request behind it
    was approved. Joining them here rather than listing both keeps one line per day and still
    answers "and was it signed off?".
    """
    records: list[Record] = []
    for day in days:
        remark = day.remarks.strip()
        note = "" if remark == _ROUTINE_REMARK else remark

        if day.has_outdoor_duty:
            filed = _covering(applications, day.day, ApplicationKind.OUTDOOR_DUTY)
            records.append(
                Record(
                    day.day,
                    RecordKind.OUTDOOR_DUTY,
                    "Outdoor duty",
                    note or "Worked off site; no swipes to measure.",
                    status=_status_word(filed.status).capitalize() if filed else "",
                    pending=bool(filed and filed.is_open),
                )
            )
            continue
        if day.status in (DayStatus.LEAVE, DayStatus.HALF_DAY):
            status, pending = _leave_state(applications, day.day)
            records.append(
                Record(
                    day.day,
                    RecordKind.LEAVE,
                    "Half day" if day.status is DayStatus.HALF_DAY else "Leave",
                    note,
                    status=status,
                    pending=pending,
                )
            )
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
    return records


def _leave_state(applications: list[Application], when: date) -> tuple[str, bool]:
    """How the application behind a day off stands, as (status word, still waiting).

    Comp-off is checked too: a day taken as comp-off is leave on the muster, and the
    application that authorised it sits in the comp-off list rather than the leave one.
    """
    filed = _covering(applications, when, ApplicationKind.LEAVE) or _covering(
        applications, when, ApplicationKind.COMP_OFF
    )
    if filed is None:
        return "", False
    return _status_word(filed.status).capitalize(), filed.is_open


def _covering(
    applications: list[Application], when: date, kind: ApplicationKind
) -> Application | None:
    """The application of that kind spanning that day, preferring a decided one.

    A day can legitimately be covered twice — an application rejected and refiled — and the
    decided one is the state that actually applies.
    """
    matches = [item for item in applications if item.kind is kind and item.covers(when)]
    if not matches:
        return None
    return next((item for item in matches if item.status.is_decided), matches[0])


def _from_applications(applications: list[Application], already: list[Record]) -> list[Record]:
    """Applications the muster and the ledger do not already account for.

    Which is exactly the interesting set: the ones still waiting on somebody, the ones that
    were refused, and the ones for dates the portal has not reached yet. An approved past
    application is already on the timeline as the day it produced.

    Matched across the application's **whole span**, not only its start. A week of outdoor
    duty beginning on a Sunday has no muster row for its first day — the portal records
    nothing for a day nobody was expected to work — so keying on the start alone listed the
    week twice: once as the days, once as the application that produced them.
    """
    claimed = {(record.day, record.kind) for record in already}
    records: list[Record] = []
    for filed in applications:
        kinds = _APPLICATION_KINDS[filed.kind]
        if any((when, kind) in claimed for when in _span(filed) for kind in kinds):
            continue
        records.append(
            Record(
                filed.start,
                kinds[0],
                f"{filed.kind.label} applied for",
                _application_detail(filed),
                pending=filed.is_open,
                needs_action=filed.status in (SwipeStatus.REJECTED, SwipeStatus.LAPSED),
                status=_status_word(filed.status).capitalize(),
            )
        )
    return records


def _span(filed: Application) -> list[date]:
    return [
        filed.start + timedelta(days=offset) for offset in range((filed.end - filed.start).days + 1)
    ]


def _application_detail(filed: Application) -> str:
    span = (
        f"{filed.days:g} day(s)"
        if filed.is_single_day
        else f"{filed.days:g} day(s), to {filed.end.strftime('%d %b').lstrip('0')}"
    )
    parts = [span]
    if filed.leave_type:
        parts.append(filed.leave_type)
    if filed.remark:
        parts.append(filed.remark)
    return " — ".join(parts)


#: How a filed application shows up on a timeline whose kinds are about what *happened*, and
#: every kind of entry it could already be represented by. First entry is what it renders as.
#:
#: Comp-off has two, because a comp-off application means one of two different things: the
#: day was worked and earned a credit (a comp-off entry), or the credit was spent as a day
#: off (which the muster records as leave). Matching only the first listed the same March
#: day twice.
_APPLICATION_KINDS: dict[ApplicationKind, tuple[RecordKind, ...]] = {
    ApplicationKind.LEAVE: (RecordKind.LEAVE,),
    ApplicationKind.OUTDOOR_DUTY: (RecordKind.OUTDOOR_DUTY,),
    ApplicationKind.COMP_OFF: (RecordKind.COMP_OFF_EARNED, RecordKind.LEAVE),
}


def _from_requests(requests: list[SwipeRequest]) -> list[Record]:
    """Filed requests, with everything the portal gives about where each one stands.

    A rejection and a lapse both leave the day uncorrected, so both need doing something
    about — but they are not the same event and must not read as one. A rejection is an
    answer; a lapse is nobody ever giving one.
    """
    records = []
    for request in requests:
        status = request.status
        records.append(
            Record(
                request.for_date,
                RecordKind.SWIPE_REQUEST,
                f"{request.kind or 'Swipe'} request",
                _request_detail(request),
                pending=status is SwipeStatus.IN_PROCESS,
                needs_action=status in (SwipeStatus.REJECTED, SwipeStatus.LAPSED),
                status=_status_word(status).capitalize(),
            )
        )
    return records


def _request_detail(request: SwipeRequest) -> str:
    """What was asked for, and when it was decided.

    The approve date only appears once there is a decision to date. On a pending row the
    portal leaves it empty, and printing "decided —" beside a request nobody has looked at
    would be worse than leaving it out.
    """
    asked = request.in_time or request.out_time
    parts = [request.direction + (f" {_clock(asked)}" if asked else "")]
    if request.remark:
        parts.append(request.remark)
    if request.approve_date and request.status is not SwipeStatus.IN_PROCESS:
        parts.append(f"decided {request.approve_date.strftime('%d %b').lstrip('0')}")
    return " — ".join(part for part in parts if part.strip())


def _clock(when: time) -> str:
    """``6:24 PM``. Windows has no ``%-I``, so the leading zero comes off by hand.

    Duplicated from the UI's formatter rather than imported: the intelligence layer does not
    depend on ``ui``, and one lstrip is cheaper than inverting that.
    """
    return when.strftime("%I:%M %p").lstrip("0")


def _from_transactions(
    transactions: list[LeaveTransaction], applications: list[Application]
) -> list[Record]:
    """Comp-off movements from the leave ledger.

    Only comp-off, and only rows carrying a date. The ledger's other rows are running
    balances rather than events, and an entry with no date cannot be placed on a timeline —
    the same reason comp-off expiry is reported as UNKNOWN rather than invented.

    A credit's status comes from the application that earned it, when one is on file.
    """
    records = []
    for entry in transactions:
        if entry.transaction_date is None or not _is_comp_off(entry.leave_type):
            continue
        if entry.credit_days > 0:
            filed = _covering(applications, entry.transaction_date, ApplicationKind.COMP_OFF)
            records.append(
                Record(
                    entry.transaction_date,
                    RecordKind.COMP_OFF_EARNED,
                    f"Comp-off earned — {entry.credit_days:g} day(s)",
                    entry.remark,
                    status=_status_word(filed.status).capitalize() if filed else "",
                    pending=bool(filed and filed.is_open),
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
        SwipeStatus.LAPSED: "lapsed",
    }.get(status, "filed")


__all__ = ["HolidayEntry", "Record", "RecordKind", "build_records", "holiday_calendar"]
