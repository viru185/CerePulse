"""Shared parsing primitives for the portal's date, time, and number formats.

Centralized so the quirks live in one tested place: dates are ``DD-MMM-YY`` (sometimes with
a trailing weekday, ``24-Jul-26 Fri``), times are ``H:MM AM/PM`` with irregular padding
(``9:50 AM`` and ``09:21 AM`` both occur), and cells use ``---`` for "no value".
"""

from __future__ import annotations

import re
from datetime import date, datetime, time

# Empty-ish cell markers seen in the grids.
_BLANKS = {"", "-", "--", "---", "n/a", "na"}

_DATE_RE = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{2,4})")
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([AaPp])\.?[Mm]\.?")

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip


def clean(text: str | None) -> str:
    """Collapse whitespace; the grids are full of ``&nbsp;`` and newlines."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def is_blank(text: str | None) -> bool:
    return clean(text).casefold() in _BLANKS


def parse_date(text: str | None) -> date | None:
    """Parse ``DD-MMM-YY``, ignoring any trailing weekday. Returns None for blanks."""
    cleaned = clean(text)
    if is_blank(cleaned):
        return None
    match = _DATE_RE.search(cleaned)
    if not match:
        return None
    day, month_name, year = match.groups()
    month = _MONTHS.get(month_name.casefold())
    if month is None:
        return None
    year_num = int(year)
    if year_num < 100:  # two-digit year -> 2000s
        year_num += 2000
    try:
        return date(year_num, month, int(day))
    except ValueError:
        return None


def parse_time(text: str | None) -> time | None:
    """Parse ``H:MM AM/PM``. Returns None for blanks or unparseable cells."""
    cleaned = clean(text)
    if is_blank(cleaned):
        return None
    match = _TIME_RE.search(cleaned)
    if not match:
        return None
    hour, minute, meridiem = match.groups()
    hour_num = int(hour) % 12
    if meridiem.casefold() == "p":
        hour_num += 12
    try:
        return time(hour_num, int(minute))
    except ValueError:
        return None


def parse_datetime(text: str | None) -> datetime | None:
    """Parse ``DD-MMM-YY H:MM AM/PM`` (the Entry Date Time column)."""
    day = parse_date(text)
    moment = parse_time(text)
    if day is None or moment is None:
        return None
    return datetime.combine(day, moment)


def parse_float(text: str | None, default: float = 0.0) -> float:
    """Parse a genuine decimal cell (day portion, leave days). Not for HH.MM durations."""
    cleaned = clean(text).replace(",", "")
    if is_blank(cleaned):
        return default
    try:
        return float(cleaned)
    except ValueError:
        return default
