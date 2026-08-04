"""The published holiday calendar, placed against today.

Separate from the records timeline on purpose, and these tests pin why. The timeline bounds
holidays to the month on screen so a year of future dates cannot bury what actually happened
— which meant the full calendar had nowhere to live and "when is my next day off" could only
be answered by opening the portal.
"""

from __future__ import annotations

from datetime import date

from cerepulse.intelligence.records import holiday_calendar
from cerepulse.models.leave import Holiday

# The real published 2026 calendar, from the captured holiday list.
CALENDAR = [
    Holiday(day=date(2026, 1, 14), weekday="Wednesday", name="Uttrayan"),
    Holiday(day=date(2026, 3, 4), weekday="Wednesday", name="Holi 2nd day-Dhuleti"),
    Holiday(day=date(2026, 8, 15), weekday="Saturday", name="Independence Day"),
    Holiday(day=date(2026, 8, 28), weekday="Friday", name="Rakshabandhan"),
    Holiday(day=date(2026, 12, 25), weekday="Friday", name="Christmas"),
]

TODAY = date(2026, 8, 4)


def test_every_published_holiday_is_listed() -> None:
    """Not only the ones in the month on screen, which is the whole point of this list."""
    assert len(holiday_calendar(CALENDAR, today=TODAY)) == len(CALENDAR)


def test_it_reads_forwards() -> None:
    """A calendar is read in date order. The timeline is newest-first; this is not that."""
    days = [entry.day for entry in holiday_calendar(CALENDAR, today=TODAY)]
    assert days == sorted(days)


def test_the_past_is_marked_not_dropped() -> None:
    """Half of what this answers is what has already been taken."""
    passed = [entry.day for entry in holiday_calendar(CALENDAR, today=TODAY) if entry.has_passed]
    assert passed == [date(2026, 1, 14), date(2026, 3, 4)]


def test_exactly_one_holiday_is_next() -> None:
    entries = holiday_calendar(CALENDAR, today=TODAY)
    upcoming = [entry.day for entry in entries if entry.is_next]
    assert upcoming == [date(2026, 8, 15)]


def test_today_being_a_holiday_counts_as_next_not_passed() -> None:
    """A day off happening right now has not been used up."""
    entries = holiday_calendar(CALENDAR, today=date(2026, 8, 15))
    independence = next(e for e in entries if e.day == date(2026, 8, 15))
    assert independence.is_next
    assert not independence.has_passed


def test_a_spent_year_has_no_next() -> None:
    entries = holiday_calendar(CALENDAR, today=date(2026, 12, 31))
    assert all(entry.has_passed for entry in entries)
    assert not any(entry.is_next for entry in entries)


def test_an_unsynced_calendar_is_empty_rather_than_an_error() -> None:
    assert holiday_calendar([], today=TODAY) == []


def test_the_order_it_arrives_in_does_not_matter() -> None:
    """The repository orders by day, but nothing here should depend on that."""
    shuffled = list(reversed(CALENDAR))
    assert [entry.day for entry in holiday_calendar(shuffled, today=TODAY)] == [
        entry.day for entry in holiday_calendar(CALENDAR, today=TODAY)
    ]
