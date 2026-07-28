"""Local persistence — SQLite behind repository interfaces.

The UI never talks to this layer directly and never sees SQL (Chapter 08 section 2).
"""

from __future__ import annotations

from cerepulse.repository.attendance import AttendanceRepository
from cerepulse.repository.database import Database, open_database
from cerepulse.repository.employee import EmployeeRepository
from cerepulse.repository.leave import (
    HolidayRepository,
    LeaveRepository,
    SwipeRequestRepository,
    SyncMetadataRepository,
    attendance_scope,
)
from cerepulse.repository.schema import SCHEMA_VERSION, migrate

__all__ = [
    "SCHEMA_VERSION",
    "AttendanceRepository",
    "Database",
    "EmployeeRepository",
    "HolidayRepository",
    "LeaveRepository",
    "SwipeRequestRepository",
    "SyncMetadataRepository",
    "attendance_scope",
    "migrate",
    "open_database",
]
