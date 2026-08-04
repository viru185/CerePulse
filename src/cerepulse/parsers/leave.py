"""Parse the leave register ledger and the holiday list.

The leave register (``GridView2`` on LeaveBalanceDetail) is empty until a View postback is
submitted. Its columns are::

    +/- | User Leave Type | Opening Balance | Consumed Days | Credit Days |
    Available Balance | Transaction Date | Remark

It interleaves two kinds of row: ledger transactions, and single-cell section separators
such as "Muster Data". Separators are skipped.

The first row per leave type is a summary line carrying that type's current standing (it
has no transaction date); subsequent rows are the dated movements that produced it.
:func:`current_balances` reduces the ledger to one balance per type.
"""

from __future__ import annotations

from cerepulse.core.errors import ParserError
from cerepulse.models.leave import Holiday, LeaveBalance, LeaveTransaction
from cerepulse.parsers.primitives import clean, parse_date, parse_float
from cerepulse.parsers.tables import (
    cell_texts,
    data_rows,
    find_table_opt,
    parse_document,
)

LEAVE_GRID_ID = "ctl00_BodyContentPlaceHolder_GridView2"
HOLIDAY_GRID_ID = "ctl00_BodyContentPlaceHolder_GridView1"

_COL_SIGN, _COL_TYPE, _COL_OPENING, _COL_CONSUMED = 0, 1, 2, 3
_COL_CREDIT, _COL_AVAILABLE, _COL_TXN_DATE, _COL_REMARK = 4, 5, 6, 7
_MIN_LEAVE_COLS = 6


def parse_leave_register(html: str) -> list[LeaveTransaction]:
    """Parse every ledger row. Returns an empty list when the grid is absent or unpopulated."""
    root = parse_document(html)
    table = find_table_opt(root, LEAVE_GRID_ID)
    if table is None:
        return []

    transactions: list[LeaveTransaction] = []
    for row in data_rows(table):
        texts = cell_texts(row)
        # Section separators ("Muster Data") occupy a single populated cell.
        if len(texts) < _MIN_LEAVE_COLS:
            continue
        leave_type = clean(texts[_COL_TYPE])
        if not leave_type:
            continue

        transactions.append(
            LeaveTransaction(
                leave_type=leave_type,
                opening_balance=parse_float(texts[_COL_OPENING]),
                consumed_days=parse_float(texts[_COL_CONSUMED]),
                credit_days=parse_float(texts[_COL_CREDIT]),
                available_balance=parse_float(texts[_COL_AVAILABLE]),
                transaction_date=(
                    parse_date(texts[_COL_TXN_DATE]) if len(texts) > _COL_TXN_DATE else None
                ),
                remark=texts[_COL_REMARK] if len(texts) > _COL_REMARK else "",
                is_credit=clean(texts[_COL_SIGN]) == "+",
            )
        )
    return transactions


def current_balances(transactions: list[LeaveTransaction]) -> list[LeaveBalance]:
    """Reduce the ledger to the current balance for each leave type.

    The undated summary row is authoritative when present. Otherwise the most recent dated
    transaction wins; undated rows sort last so a summary line is never overridden by an
    older movement.
    """
    latest: dict[str, LeaveTransaction] = {}
    for txn in transactions:
        key = txn.leave_type.strip().upper()
        current = latest.get(key)
        if current is None:
            latest[key] = txn
            continue
        if current.transaction_date is None:
            continue  # already holding the summary row
        if txn.transaction_date is None or txn.transaction_date >= current.transaction_date:
            latest[key] = txn

    return [
        LeaveBalance(
            leave_type=txn.leave_type,
            available_balance=txn.available_balance,
            consumed_days=txn.consumed_days,
            credit_days=txn.credit_days,
            as_of=txn.transaction_date,
        )
        for txn in latest.values()
    ]


def parse_holidays(html: str) -> list[Holiday]:
    """Parse the holiday list: ``Date | Day | Remarks``.

    Raises when the page is not the holiday list at all, rather than reporting a company
    with no holidays. The two used to be the same answer and the cost was invisible: a
    fetch that landed on a login page or an expired session returned ``[]``, ``save_all([])``
    wrote nothing, ``mark_synced`` blessed it, and the holiday TTL — a full day, because a
    calendar published once a year does not need re-asking — then suppressed the retry until
    tomorrow. The screen showed no holidays over a portal holding twelve.

    Unlike the swipe list there is no status filter to prove the page arrived, so the grid
    itself is the proof: the portal renders it whether or not it has rows, so a page without
    it is not the page that was asked for.
    """
    root = parse_document(html)
    table = find_table_opt(root, HOLIDAY_GRID_ID)
    if table is None:
        raise ParserError("Holiday page has no holiday grid on it")

    holidays: list[Holiday] = []
    for row in data_rows(table):
        texts = cell_texts(row)
        if len(texts) < 3:
            continue
        day = parse_date(texts[0])
        if day is None:
            continue
        holidays.append(Holiday(day=day, weekday=texts[1], name=texts[2]))
    return holidays
