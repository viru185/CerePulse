"""Row <-> domain conversion.

Kept separate from the repositories so persistence concerns never leak into the domain
models, which stay frozen and storage-agnostic (Chapter 11 section 2).

Durations are stored as integer minutes, matching :class:`Duration`'s internal unit, so no
lossy HH.MM round-trip happens at the database boundary. Dates and times are ISO strings —
SQLite has no native types for them and ISO sorts correctly as text.
"""

from __future__ import annotations

import sqlite3
from datetime import date, time

from cerepulse.models.attendance import AttendanceDay, DayStatus, Punch, PunchDirection
from cerepulse.models.leave import Holiday, LeaveBalance, LeaveTransaction
from cerepulse.models.swipe import SwipeRequest, SwipeStatus
from cerepulse.models.values import Duration

# --- primitives -----------------------------------------------------------------------


def to_iso_date(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def from_iso_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def to_iso_time(value: time | None) -> str | None:
    return value.isoformat() if value is not None else None


def from_iso_time(value: str | None) -> time | None:
    return time.fromisoformat(value) if value else None


# --- attendance -----------------------------------------------------------------------


def attendance_day_to_row(
    day: AttendanceDay, *, employee_code: str, synced_at: str
) -> tuple[object, ...]:
    return (
        employee_code,
        day.day.isoformat(),
        day.weekday,
        day.status.value,
        day.shift_code,
        to_iso_time(day.shift_in),
        to_iso_time(day.shift_out),
        to_iso_time(day.first_in),
        to_iso_time(day.last_out),
        day.user_type_1,
        day.user_type_2,
        day.portion,
        day.total_hours.minutes,
        day.late_mark.minutes,
        day.ot_hours.minutes,
        day.remarks,
        int(day.detail_loaded),
        synced_at,
    )


def row_to_attendance_day(row: sqlite3.Row, punches: tuple[Punch, ...] = ()) -> AttendanceDay:
    return AttendanceDay(
        day=date.fromisoformat(row["day"]),
        weekday=row["weekday"],
        status=_day_status(row["status"]),
        shift_code=row["shift_code"],
        shift_in=from_iso_time(row["shift_in"]),
        shift_out=from_iso_time(row["shift_out"]),
        first_in=from_iso_time(row["first_in"]),
        last_out=from_iso_time(row["last_out"]),
        user_type_1=row["user_type_1"],
        user_type_2=row["user_type_2"],
        portion=row["portion"],
        total_hours=Duration(row["total_minutes"]),
        late_mark=Duration(row["late_minutes"]),
        ot_hours=Duration(row["ot_minutes"]),
        remarks=row["remarks"],
        punches=punches,
        detail_loaded=bool(row["detail_loaded"]),
    )


def punch_to_row(
    punch: Punch, *, employee_code: str, day: date, sequence: int
) -> tuple[object, ...]:
    return (
        employee_code,
        day.isoformat(),
        sequence,
        punch.at.isoformat(),
        punch.direction.value,
        punch.ip_address,
        punch.machine,
        punch.approver_remark,
    )


def row_to_punch(row: sqlite3.Row) -> Punch:
    return Punch(
        at=time.fromisoformat(row["at"]),
        direction=PunchDirection.parse(row["direction"]),
        ip_address=row["ip_address"],
        machine=row["machine"],
        approver_remark=row["approver_remark"],
    )


# --- leave ----------------------------------------------------------------------------


def leave_balance_to_row(
    balance: LeaveBalance, *, employee_code: str, synced_at: str
) -> tuple[object, ...]:
    return (
        employee_code,
        balance.leave_type,
        balance.available_balance,
        balance.consumed_days,
        balance.credit_days,
        to_iso_date(balance.as_of),
        synced_at,
    )


def row_to_leave_balance(row: sqlite3.Row) -> LeaveBalance:
    return LeaveBalance(
        leave_type=row["leave_type"],
        available_balance=row["available_balance"],
        consumed_days=row["consumed_days"],
        credit_days=row["credit_days"],
        as_of=from_iso_date(row["as_of"]),
    )


def leave_transaction_to_row(
    transaction: LeaveTransaction, *, employee_code: str
) -> tuple[object, ...]:
    return (
        employee_code,
        transaction.leave_type,
        to_iso_date(transaction.transaction_date),
        transaction.opening_balance,
        transaction.consumed_days,
        transaction.credit_days,
        transaction.available_balance,
        transaction.remark,
        int(transaction.is_credit),
        leave_transaction_hash(transaction),
    )


def leave_transaction_hash(transaction: LeaveTransaction) -> str:
    """Stable identity for a ledger row.

    The portal gives transactions no id and the same type can move twice on one date, so
    identity is the whole row. This is what stops a re-sync from duplicating the ledger.
    """
    return "|".join(
        (
            transaction.leave_type,
            to_iso_date(transaction.transaction_date) or "",
            f"{transaction.opening_balance:g}",
            f"{transaction.consumed_days:g}",
            f"{transaction.credit_days:g}",
            f"{transaction.available_balance:g}",
            transaction.remark,
        )
    )


def row_to_leave_transaction(row: sqlite3.Row) -> LeaveTransaction:
    return LeaveTransaction(
        leave_type=row["leave_type"],
        opening_balance=row["opening_balance"],
        consumed_days=row["consumed_days"],
        credit_days=row["credit_days"],
        available_balance=row["available_balance"],
        transaction_date=from_iso_date(row["transaction_date"]),
        remark=row["remark"],
        is_credit=bool(row["is_credit"]),
    )


# --- swipe & holidays -----------------------------------------------------------------


def swipe_request_to_row(
    request: SwipeRequest, *, employee_code: str, synced_at: str
) -> tuple[object, ...]:
    return (
        employee_code,
        request.for_date.isoformat(),
        request.direction,
        to_iso_time(request.in_time),
        to_iso_time(request.out_time),
        request.remark,
        request.status.value,
        to_iso_date(request.approve_date),
        request.category,
        synced_at,
    )


def row_to_swipe_request(row: sqlite3.Row) -> SwipeRequest:
    return SwipeRequest(
        for_date=date.fromisoformat(row["for_date"]),
        direction=row["direction"],
        in_time=from_iso_time(row["in_time"]),
        out_time=from_iso_time(row["out_time"]),
        remark=row["remark"],
        status=_swipe_status(row["status"]),
        approve_date=from_iso_date(row["approve_date"]),
        category=row["category"],
    )


def holiday_to_row(holiday: Holiday) -> tuple[object, ...]:
    return (holiday.day.isoformat(), holiday.weekday, holiday.name)


def row_to_holiday(row: sqlite3.Row) -> Holiday:
    return Holiday(
        day=date.fromisoformat(row["day"]),
        weekday=row["weekday"],
        name=row["name"],
    )


# --- enum decoding --------------------------------------------------------------------


def _day_status(value: str) -> DayStatus:
    """Decode a stored status, tolerating one written by a newer build."""
    try:
        return DayStatus(value)
    except ValueError:
        return DayStatus.UNKNOWN


def _swipe_status(value: str) -> SwipeStatus:
    try:
        return SwipeStatus(value)
    except ValueError:
        return SwipeStatus.UNKNOWN
