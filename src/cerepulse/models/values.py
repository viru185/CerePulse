"""Value objects shared across the domain models.

The one that matters is :class:`Duration`. The portal reports every duration in an
``HH.MM`` format that *looks* decimal but is not: a "Tot. Hrs." of ``9.01`` means nine
hours and one minute, not 9.01 hours. This was confirmed against the captured data —
a 9:50 AM to 6:51 PM span renders as ``9.01``, and 9:21 AM to 6:31 PM renders as ``9.10``.
Reading these as decimals would corrupt every downstream calculation, so the parsing lives
here in one audited place and the rest of the code works in whole minutes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, order=True)
class Duration:
    """A span of time, stored as whole minutes.

    Minutes are the canonical unit everywhere in the app; the ``HH.MM`` portal format only
    exists at the parsing boundary via :meth:`from_hhmm`.
    """

    minutes: int

    def __post_init__(self) -> None:
        if not isinstance(self.minutes, int):
            raise TypeError(f"Duration minutes must be an int, got {type(self.minutes).__name__}")

    @classmethod
    def from_hhmm(cls, text: str) -> Duration:
        """Parse the portal's ``HH.MM`` format, where the fractional part is minutes.

        ``"9.01"`` -> 9h 01m -> 541 minutes. ``"181.31"`` (a monthly total) -> 181h 31m.
        An empty or dash cell is zero. A value with no dot is treated as whole hours.
        """
        cleaned = text.strip()
        if not cleaned or cleaned in {"-", "---", "--"}:
            return cls(0)

        negative = cleaned.startswith("-")
        cleaned = cleaned.lstrip("-+")

        hours_part, _, minutes_part = cleaned.partition(".")
        try:
            hours = int(hours_part or "0")
            # A single digit after the dot means tens of minutes: "9.5" is 9h50m, matching
            # how the portal pads its own values ("9.05" for five past).
            minutes = int((minutes_part + "0")[:2]) if minutes_part else 0
        except ValueError as exc:
            raise ValueError(f"{text!r} is not a valid HH.MM duration") from exc

        if minutes >= 60:
            raise ValueError(f"{text!r} has an impossible minutes part ({minutes})")

        total = hours * 60 + minutes
        return cls(-total if negative else total)

    @classmethod
    def from_minutes(cls, minutes: int) -> Duration:
        return cls(minutes)

    @property
    def hours(self) -> int:
        return abs(self.minutes) // 60

    @property
    def remainder_minutes(self) -> int:
        return abs(self.minutes) % 60

    @property
    def is_negative(self) -> bool:
        return self.minutes < 0

    @property
    def decimal_hours(self) -> float:
        """True decimal hours, for arithmetic and charts — 9h30m is 9.5."""
        return self.minutes / 60

    def __add__(self, other: Duration) -> Duration:
        return Duration(self.minutes + other.minutes)

    def __sub__(self, other: Duration) -> Duration:
        return Duration(self.minutes - other.minutes)

    def __neg__(self) -> Duration:
        return Duration(-self.minutes)

    def __bool__(self) -> bool:
        return self.minutes != 0

    def as_clock(self) -> str:
        """Render as ``H:MM``, with a leading sign when negative.

        No longer the default. It still matches how the portal writes ``Tot. Hrs.``, which
        is what makes it the right form for round-tripping and for tests that pin the
        parser, but ``8:30`` sitting next to a real ``6:24 PM`` reads as another clock time
        rather than as a length of time.
        """
        sign = "-" if self.is_negative else ""
        return f"{sign}{self.hours}:{self.remainder_minutes:02d}"

    def as_words(self) -> str:
        """Render as ``8h 30m`` — unmistakably an interval rather than a time of day.

        Whole hours drop the minutes and sub-hour spans drop the hours, because ``8h 00m``
        and ``0h 45m`` both make the reader parse a component that carries nothing. Zero is
        ``0m``: it is a measured nothing, and an empty string would read as missing data.
        """
        sign = "-" if self.is_negative else ""
        if self.hours and self.remainder_minutes:
            return f"{sign}{self.hours}h {self.remainder_minutes:02d}m"
        if self.hours:
            return f"{sign}{self.hours}h"
        return f"{sign}{self.remainder_minutes}m"

    def __str__(self) -> str:
        return self.as_words()


ZERO = Duration(0)
