"""iCalendar export — holidays, leave taken, and the deadlines that matter.

Written by hand rather than pulled in as a dependency. The subset needed here is small, and
the parts that actually break interoperability are unglamorous rather than difficult:

* **CRLF line endings.** RFC 5545 requires them. Outlook is forgiving about bare newlines;
  several other clients are not, and the failure mode is an import that silently drops
  every event after the first.
* **Folding at 75 octets.** Long summaries must be wrapped with a continuation line starting
  with a single space, counted in *bytes* rather than characters — a name with an accent in
  it takes two.
* **Escaping.** Commas, semicolons and backslashes are structural in this format. An
  unescaped comma in "Diwali, day 2" turns one event into two malformed ones.
* **A stable UID per event.** Re-importing after a re-export should update the existing
  entry, not add a second copy of every holiday. UIDs are therefore derived from the event's
  own content, not from a counter or a clock.

All-day events use ``VALUE=DATE`` with an exclusive ``DTEND``, which is what the spec says
and what every client expects: a one-day event ends on the following day.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zlib import crc32

from cerepulse import __about__ as about

#: Identifies the writer to importing clients.
PRODID = f"-//{about.NAME}//{about.NAME} {about.VERSION}//EN"

LINE_LIMIT = 75
CRLF = "\r\n"


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    """One all-day entry."""

    day: date
    summary: str
    description: str = ""
    #: Distinguishes a holiday from leave from a deadline when building the UID, so two
    #: different events on the same date cannot collide.
    category: str = "CEREPULSE"
    #: Days the event spans. A single date is 1.
    span: int = 1

    @property
    def uid(self) -> str:
        """Stable across exports, so re-importing updates rather than duplicates."""
        seed = f"{self.category}:{self.day.isoformat()}:{self.summary}"
        return f"{crc32(seed.encode()):08x}-{self.day:%Y%m%d}@cerepulse"


def build_calendar(
    events: Iterable[CalendarEvent],
    *,
    name: str = "CerePulse",
    stamp: datetime | None = None,
) -> str:
    """Render events as an iCalendar document.

    ``stamp`` is injected rather than read from the clock so the same input produces the
    same bytes — which is what makes this testable, and means an unchanged export does not
    look like a changed one.
    """
    moment = (stamp or datetime.now()).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(name)}",
    ]
    for event in sorted(events, key=lambda item: (item.day, item.summary)):
        lines.extend(_event_lines(event, moment))
    lines.append("END:VCALENDAR")

    folded = [folded_line for line in lines for folded_line in _fold(line)]
    # A trailing CRLF: the document is a sequence of terminated lines, not lines separated
    # by terminators, and some parsers drop an unterminated final line.
    return CRLF.join(folded) + CRLF


def _event_lines(event: CalendarEvent, moment: str) -> list[str]:
    end = event.day + timedelta(days=max(1, event.span))
    lines = [
        "BEGIN:VEVENT",
        f"UID:{event.uid}",
        f"DTSTAMP:{moment}",
        f"DTSTART;VALUE=DATE:{event.day:%Y%m%d}",
        # Exclusive, per RFC 5545: a one-day event ends on the following day.
        f"DTEND;VALUE=DATE:{end:%Y%m%d}",
        f"SUMMARY:{_escape(event.summary)}",
        "TRANSP:TRANSPARENT",
    ]
    if event.description:
        lines.append(f"DESCRIPTION:{_escape(event.description)}")
    lines.append(f"CATEGORIES:{_escape(event.category)}")
    lines.append("END:VEVENT")
    return lines


def _escape(text: str) -> str:
    """Escape the characters that are structural in a property value.

    Backslash first, or the escapes inserted after it would themselves be escaped.
    """
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def _fold(line: str) -> list[str]:
    """Split a long line into continuations, counting octets rather than characters.

    A multi-byte character must not be cut in half, so the split point is found by encoding
    a growing prefix rather than by slicing the string at a byte offset.
    """
    if len(line.encode("utf-8")) <= LINE_LIMIT:
        return [line]

    parts: list[str] = []
    remaining = line
    while len(remaining.encode("utf-8")) > LINE_LIMIT:
        cut = 0
        size = 0
        for index, char in enumerate(remaining, start=1):
            width = len(char.encode("utf-8"))
            if size + width > LINE_LIMIT:
                break
            size += width
            cut = index
        parts.append(remaining[: max(1, cut)])
        # Continuation lines carry a leading space, which counts against the limit too.
        remaining = " " + remaining[max(1, cut) :]
    parts.append(remaining)
    return parts


__all__ = ["CRLF", "PRODID", "CalendarEvent", "build_calendar"]
