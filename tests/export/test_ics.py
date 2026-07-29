"""iCalendar output.

Most of these pin the unglamorous parts. A calendar file that looks right in an editor and
imports as one event, or as three copies, is the normal failure mode.
"""

from __future__ import annotations

from datetime import date, datetime

from cerepulse.export.ics import CRLF, CalendarEvent, build_calendar

STAMP = datetime(2026, 7, 29, 11, 30, 0)
DIWALI = CalendarEvent(day=date(2026, 11, 8), summary="Diwali", category="HOLIDAY")


def lines(text: str) -> list[str]:
    return text.split(CRLF)


def render(*events: CalendarEvent) -> str:
    return build_calendar(events, stamp=STAMP)


# --- structure --------------------------------------------------------------------------


def test_a_calendar_wraps_its_events() -> None:
    text = render(DIWALI)
    assert text.startswith("BEGIN:VCALENDAR")
    assert "END:VCALENDAR" in text
    assert lines(text).count("BEGIN:VEVENT") == 1


def test_an_empty_calendar_is_still_a_valid_document() -> None:
    text = build_calendar([], stamp=STAMP)
    assert "BEGIN:VCALENDAR" in text
    assert "BEGIN:VEVENT" not in text


def test_lines_end_with_crlf_and_the_document_is_terminated() -> None:
    """Bare newlines import as one event and silently drop the rest."""
    text = render(DIWALI)
    assert text.endswith(CRLF)
    assert "\n" not in text.replace(CRLF, "")


def test_an_all_day_event_ends_on_the_following_day() -> None:
    """RFC 5545 DTEND is exclusive; getting it wrong shows the event a day short."""
    text = render(DIWALI)
    assert "DTSTART;VALUE=DATE:20261108" in text
    assert "DTEND;VALUE=DATE:20261109" in text


def test_a_multi_day_event_spans_its_whole_range() -> None:
    text = render(CalendarEvent(day=date(2026, 12, 24), summary="Break", span=4))
    assert "DTSTART;VALUE=DATE:20261224" in text
    assert "DTEND;VALUE=DATE:20261228" in text


def test_events_come_out_in_date_order() -> None:
    later = CalendarEvent(day=date(2026, 12, 25), summary="Christmas")
    earlier = CalendarEvent(day=date(2026, 1, 26), summary="Republic Day")
    text = build_calendar([later, earlier], stamp=STAMP)

    assert text.index("Republic Day") < text.index("Christmas")


# --- re-import behaviour ------------------------------------------------------------------


def test_the_same_input_produces_the_same_bytes() -> None:
    """An unchanged export must not look like a changed one."""
    assert render(DIWALI) == render(DIWALI)


def test_a_uid_is_stable_across_exports() -> None:
    """Re-importing should update the existing entry, not add a second copy of everything."""
    again = CalendarEvent(day=date(2026, 11, 8), summary="Diwali", category="HOLIDAY")
    assert DIWALI.uid == again.uid


def test_two_events_on_one_day_do_not_collide() -> None:
    holiday = CalendarEvent(day=date(2026, 11, 8), summary="Diwali", category="HOLIDAY")
    deadline = CalendarEvent(day=date(2026, 11, 8), summary="CF expires", category="DEADLINE")
    assert holiday.uid != deadline.uid


# --- escaping and folding -----------------------------------------------------------------


def test_commas_and_semicolons_are_escaped() -> None:
    """An unescaped comma turns one event into two malformed ones."""
    text = render(CalendarEvent(day=date(2026, 11, 8), summary="Diwali, day 2; evening"))
    assert "SUMMARY:Diwali\\, day 2\\; evening" in text


def test_backslashes_are_escaped_before_anything_else() -> None:
    text = render(CalendarEvent(day=date(2026, 11, 8), summary=r"a\b,c"))
    assert r"SUMMARY:a\\b\,c" in text


def test_newlines_become_the_literal_escape() -> None:
    text = render(CalendarEvent(day=date(2026, 11, 8), summary="x", description="one\ntwo"))
    assert "DESCRIPTION:one\\ntwo" in text


def test_long_lines_are_folded_within_the_octet_limit() -> None:
    text = render(CalendarEvent(day=date(2026, 11, 8), summary="Festival " * 20))
    for line in lines(text):
        assert len(line.encode("utf-8")) <= 75


def test_a_continuation_line_starts_with_a_single_space() -> None:
    text = render(CalendarEvent(day=date(2026, 11, 8), summary="Festival " * 20))
    folded = [line for line in lines(text) if line.startswith(" ")]

    assert folded
    assert all(not line.startswith("  ") for line in folded)


def test_folding_never_splits_a_character_in_half() -> None:
    """The limit is in octets, and an accented character takes two."""
    text = render(CalendarEvent(day=date(2026, 11, 8), summary="é" * 60))
    for line in lines(text):
        assert len(line.encode("utf-8")) <= 75
    assert text.count("é") == 60
