"""Display formatting.

Pure string helpers, kept out of the widgets so they can be tested without a Qt event loop
and so every screen renders a duration or a clock time identically.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from cerepulse.models.values import Duration

#: Shown wherever a value is genuinely unknown, rather than zero.
EMPTY = "—"


def clock(moment: datetime | None) -> str:
    """``6:24 PM``. Windows has no %-I, so the leading zero is stripped by hand."""
    if moment is None:
        return EMPTY
    return moment.strftime("%I:%M %p").lstrip("0")


def clock_time(moment: time | None) -> str:
    """As :func:`clock`, for a bare time-of-day."""
    if moment is None:
        return EMPTY
    return moment.strftime("%I:%M %p").lstrip("0")


def duration(value: Duration | None, *, signed: bool = False) -> str:
    """``9h 03m``, or ``+1h 03m`` / ``-45m`` when a direction matters.

    Every length of time on every screen comes through here or through ``str(Duration)``,
    which now renders the same way. ``9:03`` beside an out-time of ``6:24 PM`` read as two
    clock times, and telling them apart meant knowing which column you were in.
    """
    if value is None:
        return EMPTY
    text = value.as_words()
    if signed and not value.is_negative and value.minutes > 0:
        return f"+{text}"
    return text


#: Kept as a name because a lot of prose calls it; the distinction it used to draw — words
#: for sentences, colons for tables — no longer exists now that both read as words.
duration_words = duration


def countdown(target: datetime | None, *, now: datetime) -> str:
    """Time remaining until ``target``. Reads ``now`` for done rather than a negative."""
    if target is None:
        return EMPTY
    remaining = target - now
    if remaining <= timedelta(0):
        return "now"
    minutes = int(remaining.total_seconds() // 60)
    return Duration(minutes).as_words()


def day_label(value: date | None) -> str:
    """``Wed 29 Jul``."""
    if value is None:
        return EMPTY
    return value.strftime("%a %d %b").replace(" 0", " ")


def long_day_label(value: date | None) -> str:
    """``Wednesday, 29 July 2026``."""
    if value is None:
        return EMPTY
    return value.strftime("%A, %d %B %Y").replace(" 0", " ")


def month_label(year: int, month: int) -> str:
    """``July 2026``."""
    return date(year, month, 1).strftime("%B %Y")


def relative_time(moment: datetime | None, *, now: datetime | None = None) -> str:
    """``just now`` / ``12 min ago`` / ``yesterday`` — for the last-synced badge."""
    if moment is None:
        return "never"

    reference = now or datetime.now()
    elapsed = reference - moment
    seconds = elapsed.total_seconds()

    if seconds < 0:
        return "just now"
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86_400:
        hours = int(seconds // 3600)
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    if seconds < 172_800:
        return "yesterday"
    return moment.strftime("%d %b").lstrip("0")


def percent(part: Duration, whole: Duration) -> float:
    """Fraction of ``whole`` covered by ``part``, clamped to 0..1."""
    if whole.minutes <= 0:
        return 0.0
    return max(0.0, min(1.0, part.minutes / whole.minutes))


def days_word(count: float) -> str:
    """``1 day`` / ``2.5 days``."""
    return f"{count:g} day{'s' if count != 1 else ''}"
