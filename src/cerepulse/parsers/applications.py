"""Parse the leave, outdoor-duty and comp-off application lists.

All three are the same page shape, confirmed against live captures of each::

    (select) | Edit | App. Id | From Date | To Date | Apply Days | Remarks | Status |
    [Leave Type] | LeaveCategory

"Leave Type" appears on the leave list only, which is why the columns are counted from the
left and the tail is read positionally rather than by a fixed index.

Two cells carry more than their heading admits:

* **From Date** appends a half-day marker to the weekday — ``14-Jun-26 Sun2nd Half``. The
  date parser stops at the year, so the suffix is harmless, but it is the only record that
  the day was a half.
* **Apply Days** appends the type — ``5.50 OD``, ``1.00 CO+``. The number is what matters;
  the suffix duplicates what the list already is.

Like the swipe grid these render **one status at a time** through ``cboReports``, and are
absent entirely when that status has no rows.
"""

from __future__ import annotations

import re

from cerepulse.core.errors import ParserError
from cerepulse.models.application import Application, ApplicationKind, RequestStatus
from cerepulse.parsers.primitives import parse_date
from cerepulse.parsers.tables import cell_texts, data_rows, find_table_opt, parse_document

APPLICATION_GRID_ID = "ctl00_BodyContentPlaceHolder_GridView1"

#: Present whether or not the grid has rows, so it proves the page loaded.
STATUS_SELECT_ID = "ctl00_BodyContentPlaceHolder_cboReports"

_COL_APP_ID, _COL_FROM, _COL_TO, _COL_DAYS = 2, 3, 4, 5
_COL_REMARK, _COL_STATUS = 6, 7
#: Only the leave grid has a Leave Type column, so it is the tenth column that identifies it.
_LEAVE_TYPE_COLUMNS = 10
_COL_LEAVE_TYPE = 8
_MIN_COLUMNS = 8

#: "5.50 OD", "1.00 CO+", "0.50". Only the leading number is data.
_DAYS = re.compile(r"^\s*(\d+(?:\.\d+)?)")


def parse_applications(html: str, kind: ApplicationKind) -> list[Application]:
    """Parse one status view of one application list.

    Returns an empty list when the page loaded and that status holds nothing — which is the
    common case, since four of the five views are usually empty. Raises when the page is not
    an application list at all, so a vendor change surfaces instead of quietly emptying the
    Records timeline.
    """
    root = parse_document(html)
    table = find_table_opt(root, APPLICATION_GRID_ID)
    if table is None:
        if not root.xpath(f"//select[@id={STATUS_SELECT_ID!r}]"):
            raise ParserError(
                f"The {kind.label.lower()} list has neither its grid nor its status filter"
            )
        return []

    applications: list[Application] = []
    for row in data_rows(table):
        texts = cell_texts(row)
        if len(texts) < _MIN_COLUMNS:
            continue

        start = parse_date(texts[_COL_FROM])
        if start is None:
            continue
        # A missing To Date means a single day, not an open-ended application.
        end = parse_date(texts[_COL_TO]) or start

        applications.append(
            Application(
                app_id=texts[_COL_APP_ID].strip(),
                kind=kind,
                start=start,
                end=end,
                days=_days(texts[_COL_DAYS]),
                remark=texts[_COL_REMARK].strip(),
                status=RequestStatus.parse(texts[_COL_STATUS]),
                leave_type=(
                    texts[_COL_LEAVE_TYPE].strip() if len(texts) >= _LEAVE_TYPE_COLUMNS else ""
                ),
            )
        )
    return applications


def _days(text: str) -> float:
    match = _DAYS.match(text)
    return float(match.group(1)) if match else 0.0
