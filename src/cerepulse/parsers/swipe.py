"""Parse the swipe-request list.

Columns on ``GridView1`` of SwipeRequestList, confirmed against a live capture rather than
taken from the vendor's docs::

    (select) | Edit | For Date | Mode | In time | Out time | Remark |
    Approve Date | Status | Type | Swipe Category

The first two columns are UI controls with no data. "Mode" is the punch being corrected
("In" or "Out"), and only the corresponding time column is filled.

The grid shows **one status at a time**, chosen by the ``cboReports`` selector and defaulting
to In Process. Fetching the rest is :meth:`PortalGateway.fetch_swipe_requests`'s job; this
module only reads whichever page it is handed.
"""

from __future__ import annotations

from cerepulse.core.errors import ParserError
from cerepulse.models.swipe import SwipeRequest, SwipeStatus
from cerepulse.parsers.primitives import clean, parse_date, parse_time
from cerepulse.parsers.tables import cell_texts, data_rows, find_table_opt, parse_document

SWIPE_GRID_ID = "ctl00_BodyContentPlaceHolder_GridView1"

#: The status selector. Present whether or not the grid has rows, which is what makes it a
#: usable proof that the page loaded at all.
STATUS_SELECT_ID = "ctl00_BodyContentPlaceHolder_cboReports"

_COL_FOR_DATE, _COL_MODE, _COL_IN, _COL_OUT = 2, 3, 4, 5
_COL_REMARK, _COL_APPROVE_DATE, _COL_STATUS = 6, 7, 8
_COL_KIND, _COL_CATEGORY = 9, 10
_MIN_SWIPE_COLS = 9


def parse_swipe_requests(html: str) -> list[SwipeRequest]:
    """Parse filed swipe requests for whichever status the page is showing.

    Returns an empty list when the page loaded and that status has no requests. Raises when
    the page is not the swipe-request page at all — the two used to be the same answer, and
    the cost of that was silent: a failed fetch saved zero rows, marked the scope synced,
    and the cache TTL then suppressed retrying for the rest of the day. The screen showed
    "no requests" over a portal that had seven.
    """
    root = parse_document(html)
    table = find_table_opt(root, SWIPE_GRID_ID)
    if table is None:
        if not root.xpath(f"//select[@id={STATUS_SELECT_ID!r}]"):
            raise ParserError("Swipe-request page has neither its grid nor its status filter")
        return []

    requests: list[SwipeRequest] = []
    for row in data_rows(table):
        texts = cell_texts(row)
        if len(texts) < _MIN_SWIPE_COLS:
            continue
        for_date = parse_date(texts[_COL_FOR_DATE])
        if for_date is None:
            continue

        requests.append(
            SwipeRequest(
                for_date=for_date,
                direction=clean(texts[_COL_MODE]),
                in_time=parse_time(texts[_COL_IN]),
                out_time=parse_time(texts[_COL_OUT]),
                remark=texts[_COL_REMARK],
                status=SwipeStatus.parse(texts[_COL_STATUS]),
                approve_date=parse_date(texts[_COL_APPROVE_DATE]),
                kind=texts[_COL_KIND] if len(texts) > _COL_KIND else "",
                category=texts[_COL_CATEGORY] if len(texts) > _COL_CATEGORY else "",
            )
        )
    return requests
