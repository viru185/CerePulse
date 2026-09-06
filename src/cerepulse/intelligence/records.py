"""One chronological record of everything that was not an ordinary working day.

Leave and Requests were two screens built on the same question — "what happened to my
time?" — split by which portal page the answer came from. That is the vendor's filing
system, not the user's: a week in June was outdoor duty on the muster, a comp-off credit in
the leave ledger, and a swipe request in a third list, and reconstructing it meant reading
three screens and holding the dates in your head.

This merges them into one stream, **one row per event**. The rule that makes that possible:
**the application is the event; the muster is the evidence.** A half day of comp-off is one
thing that happened, even though the portal records it three times — the leave application
that asked for it, the muster day marked ``CO-``, and (for the credit that paid for it) a
ledger row. The application carries the reason as the person typed it, the amount, and the
decision, so it owns the row; the muster days it produced are absorbed into it and consulted
only for what the application lacks. What the muster holds and no application explains — a
half day for a late mark, an absence — stands on its own.

Pure: it takes what the caches already hold and returns a list. Nothing here fetches.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from cerepulse.intelligence.leave import WARNING_WINDOW_DAYS, LeaveLot
from cerepulse.intelligence.wording import clean_remark, day_label, days_phrase, same_reason
from cerepulse.models.application import Application, ApplicationKind
from cerepulse.models.attendance import AttendanceDay, DayStatus
from cerepulse.models.leave import Holiday, LeaveCategory, LeaveTransaction
from cerepulse.models.swipe import SwipeRequest, SwipeStatus


class RecordKind(Enum):
    """What sort of entry this is. The filter on the screen is exactly this list."""

    LEAVE = "leave"
    OUTDOOR_DUTY = "outdoor_duty"
    COMP_OFF_EARNED = "comp_off_earned"
    COMP_OFF_SPENT = "comp_off_spent"
    SWIPE_REQUEST = "swipe_request"
    ABSENCE = "absence"

    @property
    def label(self) -> str:
        """The chip beside a row: a noun, short, and never a repeat of the title."""
        return {
            RecordKind.LEAVE: "Leave",
            RecordKind.OUTDOOR_DUTY: "Outdoor duty",
            RecordKind.COMP_OFF_EARNED: "Comp-off",
            RecordKind.COMP_OFF_SPENT: "Comp-off",
            RecordKind.SWIPE_REQUEST: "Swipe",
            RecordKind.ABSENCE: "Absence",
        }[self]


class RecordState(Enum):
    """Where a request stands. ``NONE`` for the entries that are not requests — a half day
    the portal marked for a late arrival has no approval state and gets no pill."""

    NONE = "none"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    LAPSED = "lapsed"

    @classmethod
    def from_status(cls, status: SwipeStatus) -> RecordState:
        return {
            SwipeStatus.IN_PROCESS: cls.PENDING,
            SwipeStatus.APPROVED: cls.APPROVED,
            SwipeStatus.REJECTED: cls.REJECTED,
            SwipeStatus.CANCELLED: cls.CANCELLED,
            SwipeStatus.LAPSED: cls.LAPSED,
        }.get(status, cls.NONE)

    @property
    def label(self) -> str:
        return "" if self is RecordState.NONE else self.value.capitalize()

    @property
    def needs_action(self) -> bool:
        """A rejection and a lapse both leave the day uncorrected — but they are not the same
        event. A rejection is an answer; a lapse is nobody ever giving one."""
        return self in (RecordState.REJECTED, RecordState.LAPSED)


class Tone(Enum):
    """How an aside should read: which of the palette's voices it is drawn in."""

    MUTED = "muted"
    FAINT = "faint"
    WARN = "warn"
    BAD = "bad"


@dataclass(frozen=True, slots=True)
class Record:
    """One thing that happened, dated by the day it started."""

    day: date
    kind: RecordKind
    title: str
    detail: str = ""
    state: RecordState = RecordState.NONE
    #: Set for entries the user has to do something about — a rejection, an unexplained day.
    needs_action: bool = False
    #: When the portal decided it. Only the swipe grid publishes this (fact 14).
    decided_on: date | None = None
    #: Kinds this event is *as well* — a day of comp-off is spent through a leave
    #: application, and answers both "what days off did I take" and "where did my
    #: comp-off go".
    also: frozenset[RecordKind] = frozenset()
    #: A second line in its own voice: a credit's expiry and use.
    aside: str = ""
    aside_tone: Tone = Tone.MUTED
    #: Said on hover: how a figure was arrived at.
    note: str = ""

    @property
    def pending(self) -> bool:
        return self.state is RecordState.PENDING

    @property
    def status(self) -> str:
        return self.state.label

    @property
    def is_settled(self) -> bool:
        return not (self.pending or self.needs_action)

    def is_kind(self, *kinds: RecordKind) -> bool:
        return self.kind in kinds or bool(self.also & set(kinds))


def build_records(
    *,
    days: Sequence[AttendanceDay] | None = None,
    requests: Sequence[SwipeRequest] | None = None,
    transactions: Sequence[LeaveTransaction] | None = None,
    applications: Sequence[Application] | None = None,
    lots: Sequence[LeaveLot] = (),
    today: date | None = None,
) -> list[Record]:
    """Assemble every non-ordinary day into one list, newest first.

    Ordinary working days are excluded on purpose. A record of everything is a copy of the
    attendance table, and the point of this screen is what stands out from it.

    Applications lead and absorb the muster days they produced; comp-off credits that no
    application explains come next and absorb the day that earned them; then whatever the
    muster holds that nothing explains; then swipe requests, which are about a punch rather
    than a day and never overlap the rest. ``lots`` are the comp-off credits' expiry and use,
    so an earned credit can say what became of it; ``today`` is what its countdown is measured
    from.

    Holidays are deliberately absent. They lived here until 0.13.1 — and also in the
    calendar section directly above the timeline, so the same holiday sat on screen twice.
    A company holiday is not something that happened to *your* time.
    """
    by_date = {day.day: day for day in days or []}
    credits = {
        entry.transaction_date: entry
        for entry in transactions or []
        if entry.transaction_date is not None
        and entry.credit_days > 0
        and LeaveCategory.classify(entry.leave_type) is LeaveCategory.COMP_OFF
    }
    lots_by_day = {lot.earned_on: lot for lot in lots}
    absorbed: set[date] = set()
    claimed_credits: set[date] = set()
    records: list[Record] = []

    for filed in applications or []:
        covered = [
            by_date[when]
            for when in _span(filed)
            if when in by_date and _absorbs(filed, by_date[when])
        ]
        credit = None
        if filed.kind is ApplicationKind.COMP_OFF:
            credit = next((credits[when] for when in _span(filed) if when in credits), None)
        records.append(_application_record(filed, covered, credit, lots_by_day, today))
        absorbed.update(day.day for day in covered)
        if credit is not None and credit.transaction_date is not None:
            claimed_credits.add(credit.transaction_date)

    for when, credit in credits.items():
        if when is None or when in claimed_credits:
            continue
        day = by_date.get(when)
        if day is not None and _credit_explains(credit, day):
            absorbed.add(when)
        records.append(_credit_record(when, credit, lots_by_day.get(when), today))

    records += _from_days([day for day in by_date.values() if day.day not in absorbed])
    records += _from_requests(requests or [])

    # Newest first, and within a day the things needing attention lead. A rejection and an
    # absence on the same date should not be ordered by which list they came from.
    return sorted(
        records, key=lambda item: (item.day, item.needs_action, item.pending), reverse=True
    )


# --- applications ---------------------------------------------------------------------------


def _absorbs(filed: Application, day: AttendanceDay) -> bool:
    """Whether this application is the reason the muster marked this day as it did.

    Kind-aware so a genuine mismatch still shows as two rows: a leave application explains a
    day off of any shape; a comp-off application explains the day it was spent on, and the
    day that *earned* it only when the two give the same reason (or the day carries ``CO+``);
    outdoor duty explains a day on duty, or one still marked absent while it waits.
    """
    off = day.status in (DayStatus.LEAVE, DayStatus.HALF_DAY, DayStatus.ABSENT)
    if filed.kind is ApplicationKind.LEAVE:
        return day.took_comp_off or off
    if filed.kind is ApplicationKind.COMP_OFF:
        if day.took_comp_off:
            return True
        return off and ("CO+" in _codes(day) or same_reason(day.remarks, filed.remark))
    return day.has_outdoor_duty or day.status is DayStatus.ABSENT


def _application_record(
    filed: Application,
    covered: list[AttendanceDay],
    credit: LeaveTransaction | None,
    lots_by_day: dict[date, LeaveLot],
    today: date | None,
) -> Record:
    amount = filed.days or (credit.credit_days if credit else 0.0) or float(len(covered)) or 1.0
    span = "" if filed.is_single_day else f", to {filed.end.strftime('%d %b').lstrip('0')}"
    spent = any(day.took_comp_off for day in covered)
    also: frozenset[RecordKind] = frozenset()

    if filed.kind is ApplicationKind.LEAVE:
        spent = spent or LeaveCategory.classify(filed.leave_type) is LeaveCategory.COMP_OFF
        kind = RecordKind.COMP_OFF_SPENT if spent else RecordKind.LEAVE
        if spent:
            also = frozenset({RecordKind.LEAVE})
        title = f"{days_phrase(amount)} of {filed.leave_type or 'leave'}{span}"
    elif filed.kind is ApplicationKind.COMP_OFF:
        # The comp-off list is where credits are *earned*; spending one is a leave
        # application typed CO-. The muster is the only thing that can say otherwise.
        spent = spent or "CO-" in filed.leave_type.upper()
        kind = RecordKind.COMP_OFF_SPENT if spent else RecordKind.COMP_OFF_EARNED
        title = f"{days_phrase(amount)} {'taken' if spent else 'earned'}{span}"
    else:
        kind = RecordKind.OUTDOOR_DUTY
        title = f"{days_phrase(amount)}{span}"

    # The application has the reason as typed, emoji and all; the muster's copy is mangled.
    detail = clean_remark(filed.remark)
    if not detail:
        detail = next(
            (clean_remark(day.remarks) for day in covered if clean_remark(day.remarks)), ""
        )
    if not detail and credit is not None:
        detail = _credit_reason(credit)

    aside, tone, note = "", Tone.MUTED, ""
    if kind is RecordKind.COMP_OFF_EARNED and credit is not None and credit.transaction_date:
        lot = lots_by_day.get(credit.transaction_date)
        if lot is not None:
            aside, tone = _credit_aside(lot, today)
            note = CAVEAT

    state = RecordState.from_status(filed.status)
    return Record(
        filed.start,
        kind,
        title,
        detail,
        state=state,
        needs_action=state.needs_action,
        also=also,
        aside=aside,
        aside_tone=tone,
        note=note,
    )


def _span(filed: Application) -> list[date]:
    return [
        filed.start + timedelta(days=offset) for offset in range((filed.end - filed.start).days + 1)
    ]


# --- credits ----------------------------------------------------------------------------------


#: Said wherever a comp-off deadline is shown: the rule counts from approval, the portal
#: publishes no approval date (fact 14), so the earned date stands in.
CAVEAT = "Counted from the date it was earned; the portal does not publish an approval date."


def _credit_explains(credit: LeaveTransaction, day: AttendanceDay) -> bool:
    """A credit with no application: the half day beside it is the day that earned it only
    when the two say the same thing, or the muster marks the day ``CO+``."""
    if day.status not in (DayStatus.LEAVE, DayStatus.HALF_DAY):
        return False
    return "CO+" in _codes(day) or same_reason(day.remarks, _credit_reason(credit))


def _credit_reason(credit: LeaveTransaction) -> str:
    # The register writes "<muster note> |<application remark>"; the last part is the reason.
    return clean_remark(credit.remark.rsplit("|", 1)[-1])


def _credit_record(
    when: date, credit: LeaveTransaction, lot: LeaveLot | None, today: date | None
) -> Record:
    aside, tone = _credit_aside(lot, today) if lot is not None else ("", Tone.MUTED)
    return Record(
        when,
        RecordKind.COMP_OFF_EARNED,
        f"{days_phrase(credit.credit_days)} earned",
        _credit_reason(credit),
        aside=aside,
        aside_tone=tone,
        note=CAVEAT if lot is not None else "",
    )


def _credit_aside(lot: LeaveLot, today: date | None) -> tuple[str, Tone]:
    """What became of a credit: ``expires Mon 2 Nov · 57 days · unused``."""
    used = ", ".join(
        f"{day_label(day)} ({amount:g})" if amount != lot.days else day_label(day)
        for day, amount in lot.used_on
    )
    if lot.unattributed:
        used = ", ".join(
            part for part in (used, f"{lot.unattributed:g} before the cached history") if part
        )
    if lot.is_spent:
        return f"used {used}" if used else "used", Tone.FAINT
    if today is not None and lot.has_lapsed(today):
        return f"lapsed {day_label(lot.expires_on)} · {days_phrase(lot.remaining)} lost", Tone.BAD
    parts = [f"expires {day_label(lot.expires_on)}"]
    tone = Tone.MUTED
    if today is not None:
        remaining = lot.days_remaining(today)
        parts.append(f"{remaining} days")
        if remaining <= WARNING_WINDOW_DAYS:
            tone = Tone.WARN
    if used:
        parts.append(f"used {used}")
        parts.append(f"{lot.remaining:g} left")
    else:
        parts.append("unused")
    return " · ".join(parts), tone


# --- the muster on its own -------------------------------------------------------------------


def _from_days(days: list[AttendanceDay]) -> list[Record]:
    """Days the muster marks that no application explains.

    Which is a short list: a half day for a late mark, a day off nobody filed for, an
    absence. None of these has a state — an application that could have given one would
    have absorbed the day.
    """
    records: list[Record] = []
    for day in days:
        note = clean_remark(day.remarks)
        half = day.status is DayStatus.HALF_DAY
        if day.has_outdoor_duty:
            records.append(
                Record(
                    day.day,
                    RecordKind.OUTDOOR_DUTY,
                    "Half day" if half else "Full day",
                    note or "Worked off site; no swipes to measure.",
                )
            )
        elif day.took_comp_off:
            # The only record of a comp-off being spent. It used to be read off the ledger's
            # consumption column, which this portal leaves at zero on every row, so "Comp-off
            # taken" had never appeared once and the day showed as plain leave.
            records.append(
                Record(
                    day.day,
                    RecordKind.COMP_OFF_SPENT,
                    f"{days_phrase(day.comp_off_days_taken)} taken",
                    note,
                    also=frozenset({RecordKind.LEAVE}),
                )
            )
        elif day.status in (DayStatus.LEAVE, DayStatus.HALF_DAY):
            records.append(
                Record(day.day, RecordKind.LEAVE, "Half day" if half else "Full day", note)
            )
        elif day.status is DayStatus.ABSENT:
            records.append(
                Record(
                    day.day,
                    RecordKind.ABSENCE,
                    "Marked absent",
                    note or "Nothing on file explains this day.",
                    needs_action=True,
                )
            )
    return records


def _codes(day: AttendanceDay) -> set[str]:
    return {day.user_type_1.strip().upper(), day.user_type_2.strip().upper()}


# --- swipe requests ---------------------------------------------------------------------------


def _from_requests(requests: Sequence[SwipeRequest]) -> list[Record]:
    """Filed requests, with everything the portal gives about where each one stands.

    The title is what was asked for; the grid's own Type leads it only when it is something
    other than a swipe. The decision date is carried as a date, not glued into the reason —
    and only once there is a decision: the portal leaves it empty on a pending row, and
    "decided —" beside a request nobody has looked at would be worse than leaving it out.
    """
    records = []
    for request in requests:
        state = RecordState.from_status(request.status)
        kind = request.kind.strip()
        title = (
            request.asked if not kind or kind.casefold() == "swipe" else f"{kind}: {request.asked}"
        )
        decided = request.approve_date if request.status is not SwipeStatus.IN_PROCESS else None
        records.append(
            Record(
                request.for_date,
                RecordKind.SWIPE_REQUEST,
                title,
                clean_remark(request.remark),
                state=state,
                needs_action=state.needs_action,
                decided_on=decided,
            )
        )
    return records


# --- holidays ----------------------------------------------------------------------------------


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


__all__ = [
    "CAVEAT",
    "HolidayEntry",
    "Record",
    "RecordKind",
    "RecordState",
    "Tone",
    "build_records",
    "holiday_calendar",
]
