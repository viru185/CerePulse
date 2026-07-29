"""Parse the attendance summary grid and the day-detail punch log.

The monthly grid (``GridView1``) has a fixed 16-column layout, confirmed against the
capture::

    Date | Day | Shift Code | Shift InTime | Shift OutTime | InTime | OutTime |
    User Type1 | User Type2 | Portion | Tot. Hrs. | LateMark | Eff. OT Hrs. |
    Remarks | LT | Eff. Hrs.

Each date cell is a LinkButton whose control id encodes the row (``...GridView1_ctl02_
LnkDate``). That id is turned back into the postback target used to fetch the day's detail,
so :func:`parse_month` records it per day.

``Tot. Hrs.``, ``LateMark`` and OT are ``HH.MM`` durations (see :class:`Duration`);
``Portion`` is a genuine decimal day-fraction and is parsed as a float.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from cerepulse.core.errors import ParserError
from cerepulse.models.attendance import (
    AttendanceDay,
    AttendanceMonth,
    DayStatus,
    Punch,
    PunchDirection,
)
from cerepulse.models.values import Duration
from cerepulse.parsers.primitives import clean, parse_date, parse_float, parse_time
from cerepulse.parsers.tables import (
    cell_texts,
    data_rows,
    find_table_opt,
    parse_document,
    row_cells,
)
from cerepulse.transport.webforms import DeltaRecord

SUMMARY_GRID_ID = "ctl00_BodyContentPlaceHolder_GridView1"

#: Present on the attendance page whether or not the grid rendered, so it distinguishes an
#: empty month from a page that is not the attendance page at all.
_MONTH_SELECT_NAME = "ctl00$BodyContentPlaceHolder$drpFromMonth"
PUNCH_GRID_ID = "ctl00_BodyContentPlaceHolder_GridView2"
_EMPLOYEE_CODE_ID = "ctl00_BodyContentPlaceHolder_lblEmpCode"

# Column indices in the summary grid.
_COL_DATE, _COL_DAY, _COL_SHIFT, _COL_SHIFT_IN, _COL_SHIFT_OUT = 0, 1, 2, 3, 4
_COL_IN, _COL_OUT, _COL_UT1, _COL_UT2, _COL_PORTION = 5, 6, 7, 8, 9
_COL_TOTAL, _COL_LATE, _COL_OT, _COL_REMARKS = 10, 11, 12, 13
_MIN_SUMMARY_COLS = 14

# Portal user-type codes -> normalized status.
#
# A day carries *two* type columns and they can disagree: a half day shows up as
# ("ABS", "DP") with a portion of 0.50. Classifying on the first column alone would report
# that as a plain absence and lose six hours of work, so :func:`_day_status` reads both.
# The portal's user-type codes. Anything unmapped becomes UNKNOWN, which is excluded from
# every rollup — so a missing code silently deflates the month rather than erroring. These
# were all observed in live data; OD, LWP and the comp-off pair were originally absent and
# were quietly landing whole days in UNKNOWN.
_STATUS_CODES = {
    "DP": DayStatus.PRESENT,
    "P": DayStatus.PRESENT,
    "PR": DayStatus.PRESENT,
    "WFH": DayStatus.PRESENT,
    "WH": DayStatus.PRESENT,
    # Comp-off earned: the day was worked, which is how the credit arose.
    "CO+": DayStatus.PRESENT,
    "ABS": DayStatus.ABSENT,
    "A": DayStatus.ABSENT,
    "WO": DayStatus.WEEKLY_OFF,
    "WOP": DayStatus.WEEKLY_OFF,
    "HL": DayStatus.HOLIDAY,
    "HD": DayStatus.HOLIDAY,
    "LV": DayStatus.LEAVE,
    "L": DayStatus.LEAVE,
    # Leave without pay, and comp-off taken as leave.
    "LWP": DayStatus.LEAVE,
    "LOP": DayStatus.LEAVE,
    "CO-": DayStatus.LEAVE,
    "CO": DayStatus.LEAVE,
    # Outdoor / on duty: working, but off-site with no swipes to measure.
    "OD": DayStatus.ON_DUTY,
    "ODT": DayStatus.ON_DUTY,
}


def _day_status(user_type_1: str, user_type_2: str, portion: float) -> DayStatus:
    """Combine both user-type columns and the day portion into one status."""
    primary = _STATUS_CODES.get(user_type_1.strip().upper(), DayStatus.UNKNOWN)
    secondary = _STATUS_CODES.get(user_type_2.strip().upper(), DayStatus.UNKNOWN)
    both = {primary, secondary}

    # A weekly off stays a weekly off even when it was also worked (WO/WOP).
    if DayStatus.WEEKLY_OFF in both:
        return DayStatus.WEEKLY_OFF
    if DayStatus.HOLIDAY in both:
        return DayStatus.HOLIDAY

    present = DayStatus.PRESENT in both
    partial = both & {DayStatus.ABSENT, DayStatus.LEAVE}
    if present and partial:
        return DayStatus.HALF_DAY
    if present:
        # A lone present code with a fractional portion is still a half day.
        return DayStatus.HALF_DAY if 0 < portion < 1 else DayStatus.PRESENT
    # Checked before leave and absent: a day marked both on-duty and absent was worked
    # off-site, and the absence is only the lack of swipes.
    if DayStatus.ON_DUTY in both:
        return DayStatus.ON_DUTY
    if DayStatus.LEAVE in both:
        return DayStatus.LEAVE
    if DayStatus.ABSENT in both:
        return DayStatus.ABSENT
    return DayStatus.UNKNOWN


# The ctlNN segment inside a LnkDate control id, e.g. ctl02 in
# ctl00_BodyContentPlaceHolder_GridView1_ctl02_LnkDate.
_CTL_RE = re.compile(rf"{re.escape(SUMMARY_GRID_ID)}_(ctl\d+)_LnkDate")


@dataclass(frozen=True, slots=True)
class ParsedDay:
    """An attendance day plus the postback control that loads its detail."""

    day: AttendanceDay
    detail_ctl: str | None  # e.g. "ctl02", or None if the date is not clickable

    def detail_target(self) -> str | None:
        """The ``__doPostBack`` target that opens this day's detail panel."""
        if self.detail_ctl is None:
            return None
        return f"ctl00$BodyContentPlaceHolder$GridView1${self.detail_ctl}$LnkDate"


def parse_month(html: str, *, year: int, month: int) -> tuple[AttendanceMonth, list[ParsedDay]]:
    """Parse the monthly summary. Returns the month and the per-day postback controls."""
    root = parse_document(html)
    table = find_table_opt(root, SUMMARY_GRID_ID)
    if table is None:
        # A month with no attendance at all — before the employee joined, for instance —
        # renders the page without a grid rather than with an empty one. Distinguish that
        # from a genuinely broken page by checking the period dropdowns are still there: if
        # they are, this really is the attendance page and it really has nothing to show.
        if not root.xpath(f"//select[@name={_MONTH_SELECT_NAME!r}]"):
            raise ParserError(f"Expected table {SUMMARY_GRID_ID!r} was not found in the response")
        return AttendanceMonth(employee_code="", year=year, month=month), []

    employee_code = ""
    code_nodes = root.xpath(f"//*[@id={_EMPLOYEE_CODE_ID!r}]")
    if code_nodes:
        employee_code = clean(code_nodes[0].text_content())

    parsed: list[ParsedDay] = []
    for row in data_rows(table):
        texts = cell_texts(row)
        day_value = parse_date(texts[_COL_DATE]) if texts else None
        if day_value is None:
            # The trailing totals row has an empty date cell — skip it.
            continue
        if len(texts) < _MIN_SUMMARY_COLS:
            raise ParserError(
                f"Attendance row for {texts[_COL_DATE]!r} has {len(texts)} columns, "
                f"expected at least {_MIN_SUMMARY_COLS}"
            )
        parsed.append(_parse_summary_row(row, texts, day_value))

    return (
        AttendanceMonth(
            employee_code=employee_code,
            year=year,
            month=month,
            days=tuple(item.day for item in parsed),
        ),
        parsed,
    )


def _parse_summary_row(row: object, texts: list[str], day_value: date) -> ParsedDay:
    portion = parse_float(texts[_COL_PORTION])
    status = _day_status(texts[_COL_UT1], texts[_COL_UT2], portion)
    day = AttendanceDay(
        day=day_value,
        weekday=texts[_COL_DAY],
        status=status,
        shift_code=texts[_COL_SHIFT],
        shift_in=parse_time(texts[_COL_SHIFT_IN]),
        shift_out=parse_time(texts[_COL_SHIFT_OUT]),
        first_in=parse_time(texts[_COL_IN]),
        last_out=parse_time(texts[_COL_OUT]),
        user_type_1=texts[_COL_UT1],
        user_type_2=texts[_COL_UT2],
        portion=portion,
        total_hours=Duration.from_hhmm(texts[_COL_TOTAL]),
        late_mark=Duration.from_hhmm(texts[_COL_LATE]),
        ot_hours=Duration.from_hhmm(texts[_COL_OT]),
        remarks=texts[_COL_REMARKS] if len(texts) > _COL_REMARKS else "",
    )
    return ParsedDay(day=day, detail_ctl=_detail_ctl(row))


def _detail_ctl(row: object) -> str | None:
    for anchor in row.xpath(".//a[@id]"):  # type: ignore[attr-defined]
        match = _CTL_RE.search(anchor.get("id", ""))
        if match:
            return match.group(1)
    return None


def parse_punches(delta_or_html: str) -> list[Punch]:
    """Parse the day-detail punch log from either a delta response or plain HTML.

    The live path yields a Microsoft AJAX delta whose ``updatePanel`` record holds the
    panel HTML; fixtures may hold the panel HTML directly. Both are supported so the parser
    can be unit-tested without reconstructing a delta envelope.
    """
    html = _panel_html(delta_or_html)
    root = parse_document(html)
    table = find_table_opt(root, PUNCH_GRID_ID)
    if table is None:
        return []

    punches: list[Punch] = []
    for row in data_rows(table):
        cells = row_cells(row)
        texts = [clean(cell.text_content()) for cell in cells]
        if len(texts) < 3:
            continue
        at = parse_time(texts[1])
        if at is None:
            continue
        try:
            direction = PunchDirection.parse(texts[2])
        except ValueError:
            continue
        punches.append(
            Punch(
                at=at,
                direction=direction,
                ip_address=texts[3] if len(texts) > 3 else "",
                machine=texts[4] if len(texts) > 4 else "",
                approver_remark=texts[7] if len(texts) > 7 else "",
            )
        )
    return punches


def _panel_html(delta_or_html: str) -> str:
    """Return the update-panel HTML, unwrapping a delta envelope when present."""
    from cerepulse.transport.webforms import is_delta_response, parse_delta

    if not is_delta_response(delta_or_html):
        return delta_or_html
    records: list[DeltaRecord] = parse_delta(delta_or_html)
    panels = [record.content for record in records if record.type == "updatePanel"]
    return "\n".join(panels) if panels else delta_or_html
