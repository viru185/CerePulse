"""Parse the swipe-request list.

Columns on ``GridView1`` of SwipeRequestList::

    (select) | Edit | For Date | Mode | In time | Out time | Remark |
    Approve Date | Status | Type | Swipe Category

The first two columns are UI controls with no data. "Mode" is the punch being corrected
("In" or "Out"), and only the corresponding time column is filled.
"""

from __future__ import annotations

from cerepulse.models.swipe import SwipeRequest, SwipeStatus
from cerepulse.parsers.primitives import clean, parse_date, parse_time
from cerepulse.parsers.tables import cell_texts, data_rows, find_table_opt, parse_document

SWIPE_GRID_ID = "ctl00_BodyContentPlaceHolder_GridView1"

_COL_FOR_DATE, _COL_MODE, _COL_IN, _COL_OUT = 2, 3, 4, 5
_COL_REMARK, _COL_APPROVE_DATE, _COL_STATUS = 6, 7, 8
_COL_CATEGORY = 10
_MIN_SWIPE_COLS = 9


def parse_swipe_requests(html: str) -> list[SwipeRequest]:
    """Parse filed swipe requests. Returns an empty list when none exist."""
    root = parse_document(html)
    table = find_table_opt(root, SWIPE_GRID_ID)
    if table is None:
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
                category=texts[_COL_CATEGORY] if len(texts) > _COL_CATEGORY else "",
            )
        )
    return requests
