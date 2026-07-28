"""Pure analysis engine — the product's core.

Deterministic functions over domain models: no network, no database, no Qt. Every entry
point takes an injected ``now`` or ``today`` instead of reading the clock, so the whole
layer is exhaustively testable without a portal session.
"""

from __future__ import annotations

from cerepulse.intelligence.anomalies import Anomaly, AnomalyKind, detect_anomalies
from cerepulse.intelligence.day import DayAnalysis, DayState, Explanation, analyze_day
from cerepulse.intelligence.insights import (
    Action,
    ActionKind,
    Insight,
    InsightKind,
    Severity,
)
from cerepulse.intelligence.leave import (
    ExpiryBasis,
    LeaveOutlook,
    LeavePolicy,
    analyze_leave,
    leave_insights,
)
from cerepulse.intelligence.month import (
    DayRollup,
    MonthAnalysis,
    WeekAnalysis,
    analyze_month,
    analyze_week,
    week_start_for,
)
from cerepulse.intelligence.policy import ShiftPolicy
from cerepulse.intelligence.segments import (
    IssueKind,
    Pairing,
    PunchIssue,
    WorkSegment,
    pair_punches,
)

__all__ = [
    "Action",
    "ActionKind",
    "Anomaly",
    "AnomalyKind",
    "DayAnalysis",
    "DayRollup",
    "DayState",
    "ExpiryBasis",
    "Explanation",
    "Insight",
    "InsightKind",
    "IssueKind",
    "LeaveOutlook",
    "LeavePolicy",
    "MonthAnalysis",
    "Pairing",
    "PunchIssue",
    "Severity",
    "ShiftPolicy",
    "WeekAnalysis",
    "WorkSegment",
    "analyze_day",
    "analyze_leave",
    "analyze_month",
    "analyze_week",
    "detect_anomalies",
    "leave_insights",
    "pair_punches",
    "week_start_for",
]
