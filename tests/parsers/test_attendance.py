"""Attendance grid and punch-log parsing."""

from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pytest

from cerepulse.core.errors import ParserError
from cerepulse.models.attendance import DayStatus, PunchDirection
from cerepulse.parsers.attendance import parse_month, parse_punches

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def month_html() -> str:
    return (FIXTURES / "attendance_month.html").read_text(encoding="utf-8")


@pytest.fixture
def panel_html() -> str:
    return (FIXTURES / "day_detail_panel.html").read_text(encoding="utf-8")


# --- monthly summary ------------------------------------------------------------------


def test_parses_days_and_skips_the_totals_row(month_html: str) -> None:
    month, parsed = parse_month(month_html, year=2026, month=7)
    assert [day.day for day in month.days] == [
        date(2026, 7, 1),
        date(2026, 7, 3),
        date(2026, 7, 5),
    ]
    assert len(parsed) == 3


def test_reads_the_employee_code(month_html: str) -> None:
    month, _ = parse_month(month_html, year=2026, month=7)
    assert month.employee_code == "EMP00001"


def test_total_hours_use_the_hhmm_format(month_html: str) -> None:
    """9.01 + 6.09 + 0.00 is 15h10m, which is what the portal's own totals row shows."""
    month, _ = parse_month(month_html, year=2026, month=7)
    assert month.total_hours.as_clock() == "15:10"


def test_times_and_portion_parse(month_html: str) -> None:
    month, _ = parse_month(month_html, year=2026, month=7)
    first = month.days[0]
    assert first.first_in == time(9, 50)
    assert first.last_out == time(18, 51)
    assert first.shift_in == time(8, 0)
    assert first.portion == 1.0
    assert first.total_hours.as_clock() == "9:01"


def test_a_mixed_user_type_is_a_half_day(month_html: str) -> None:
    """ABS in column one with DP in column two is a half day, not an absence."""
    month, _ = parse_month(month_html, year=2026, month=7)
    third = month.days[1]
    assert third.user_type_1 == "ABS"
    assert third.user_type_2 == "DP"
    assert third.status is DayStatus.HALF_DAY
    assert third.is_present


def test_weekly_off_is_classified(month_html: str) -> None:
    month, _ = parse_month(month_html, year=2026, month=7)
    assert month.days[2].status is DayStatus.WEEKLY_OFF
    assert not month.days[2].is_present


def test_present_days_counts_half_days(month_html: str) -> None:
    month, _ = parse_month(month_html, year=2026, month=7)
    assert month.present_days == 2


def test_blank_time_cells_become_none(month_html: str) -> None:
    month, _ = parse_month(month_html, year=2026, month=7)
    assert month.days[2].first_in is None
    assert month.days[2].last_out is None


def test_day_lookup(month_html: str) -> None:
    month, _ = parse_month(month_html, year=2026, month=7)
    assert month.day_on(date(2026, 7, 3)) is month.days[1]
    assert month.day_on(date(2026, 7, 2)) is None


# --- detail postback targets ----------------------------------------------------------


def test_each_day_yields_its_postback_target(month_html: str) -> None:
    """The ctlNN in the LinkButton id is the only link between a row and its detail."""
    _, parsed = parse_month(month_html, year=2026, month=7)
    assert parsed[0].detail_ctl == "ctl02"
    assert parsed[0].detail_target() == "ctl00$BodyContentPlaceHolder$GridView1$ctl02$LnkDate"
    assert parsed[1].detail_target() == "ctl00$BodyContentPlaceHolder$GridView1$ctl03$LnkDate"


def test_missing_grid_raises() -> None:
    with pytest.raises(ParserError, match="was not found"):
        parse_month("<html><body>nothing</body></html>", year=2026, month=7)


def test_short_row_raises() -> None:
    html = """
      <table id="ctl00_BodyContentPlaceHolder_GridView1">
        <tr><th>Date</th></tr>
        <tr><td>01-Jul-26</td><td>Wed</td></tr>
      </table>
    """
    with pytest.raises(ParserError, match="expected at least"):
        parse_month(html, year=2026, month=7)


# --- punch log ------------------------------------------------------------------------


def test_parses_the_punch_log(panel_html: str) -> None:
    punches = parse_punches(panel_html)
    assert len(punches) == 7
    assert punches[0].at == time(9, 21)
    assert punches[0].direction is PunchDirection.IN
    assert punches[-1].at == time(18, 31)
    assert punches[-1].direction is PunchDirection.OUT


def test_consecutive_same_direction_punches_are_preserved(panel_html: str) -> None:
    """A missed Out punch is real data — the parser reports it and pairing deals with it."""
    directions = [punch.direction for punch in parse_punches(panel_html)]
    assert directions == [
        PunchDirection.IN,
        PunchDirection.OUT,
        PunchDirection.IN,
        PunchDirection.IN,
        PunchDirection.OUT,
        PunchDirection.IN,
        PunchDirection.OUT,
    ]


def test_punch_metadata_is_kept(panel_html: str) -> None:
    punch = parse_punches(panel_html)[0]
    assert punch.ip_address == "10.0.0.1"
    assert punch.machine == "IN"


def test_punches_unwrap_a_delta_envelope(panel_html: str) -> None:
    """The live path delivers the panel inside a delta; fixtures pass it bare."""
    delta = f"{len(panel_html)}|updatePanel|ctl00_Panel1|{panel_html}|"
    assert len(parse_punches(delta)) == 7


def test_no_punch_grid_yields_no_punches() -> None:
    assert parse_punches("<div>No details available</div>") == []
