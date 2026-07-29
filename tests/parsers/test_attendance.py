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


# --- status codes ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("OD", DayStatus.ON_DUTY),
        ("LWP", DayStatus.LEAVE),
        ("CO-", DayStatus.LEAVE),
        ("WFH", DayStatus.PRESENT),
        ("DP", DayStatus.PRESENT),
        ("ABS", DayStatus.ABSENT),
    ],
)
def test_portal_codes_map_to_a_status(code: str, expected: DayStatus) -> None:
    """Every code seen in live data must map.

    Unmapped codes fall to UNKNOWN, which is excluded from every rollup — so a missing one
    silently deflates the month rather than failing loudly. OD, LWP and the comp-off pair
    were all landing there.
    """
    from cerepulse.parsers.attendance import _day_status

    assert _day_status(code, "---", 1.0) is expected


def test_comp_off_earned_in_the_second_column_counts_as_worked() -> None:
    from cerepulse.parsers.attendance import _day_status

    assert _day_status("", "CO+", 0.5) is DayStatus.HALF_DAY


def test_on_duty_is_attended_but_not_measurable() -> None:
    assert DayStatus.ON_DUTY.is_attended
    assert not DayStatus.ON_DUTY.counts_as_worked


def test_a_month_with_no_attendance_parses_as_empty() -> None:
    """Before the employee joined, the portal omits the grid rather than emptying it.

    Regression: January failed the history backfill with "GridView1 was not found" because
    a missing grid was treated as a broken page. The period dropdown tells the two apart.
    """
    page = """
      <html><body><form id="form1">
        <select name="ctl00$BodyContentPlaceHolder$drpFromMonth">
          <option selected value="01">January</option>
        </select>
      </form></body></html>
    """
    month, parsed = parse_month(page, year=2026, month=1)

    assert month.days == ()
    assert parsed == []
    assert month.year == 2026


def test_a_page_that_is_not_the_attendance_page_still_raises() -> None:
    """A genuinely wrong page must stay loud rather than silently reporting no data."""
    with pytest.raises(ParserError, match="was not found"):
        parse_month("<html><body>some other page</body></html>", year=2026, month=1)
