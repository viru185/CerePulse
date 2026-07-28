"""Service layer — orchestrates transport, cache, and analysis.

Dependency flow is one-way: ``ui -> services -> {repository, intelligence, transport}``.
Business rules live here rather than in the UI (Chapter 06 section 6).
"""

from __future__ import annotations

from cerepulse.services.attendance import AttendanceService, MonthView
from cerepulse.services.leave import LeaveService, LeaveView
from cerepulse.services.portal import PortalGateway
from cerepulse.services.sync import SyncCoordinator, SyncReport

__all__ = [
    "AttendanceService",
    "LeaveService",
    "LeaveView",
    "MonthView",
    "PortalGateway",
    "SyncCoordinator",
    "SyncReport",
]
