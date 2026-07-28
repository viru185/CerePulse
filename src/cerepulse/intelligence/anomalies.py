"""Anomaly detection across a month.

Makes the "highlight unusual attendance patterns" requirement concrete. Detection is
deliberately conservative: a false "you have a problem" is worse than a missed nudge, so
every rule needs a clear threshold rather than a hunch.

Weekly offs and holidays are excluded throughout, otherwise every weekend would register as
a zero-hours anomaly.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date
from enum import Enum

from cerepulse.intelligence.day import DayAnalysis
from cerepulse.intelligence.insights import Severity
from cerepulse.intelligence.policy import ShiftPolicy
from cerepulse.intelligence.segments import IssueKind
from cerepulse.models.attendance import AttendanceDay
from cerepulse.models.values import Duration

#: A break longer than this is worth mentioning.
LONG_BREAK_THRESHOLD = Duration(2 * 60)

#: How far a day's in-time must sit from the personal norm to count as drift.
IN_TIME_DRIFT_THRESHOLD = Duration(90)

#: Minimum sample before a personal in-time norm is meaningful.
_MIN_SAMPLE_FOR_DRIFT = 5


class AnomalyKind(Enum):
    MISSING_PUNCH = "missing_punch"
    SINGLE_PUNCH = "single_punch"
    LONG_BREAK = "long_break"
    IN_TIME_DRIFT = "in_time_drift"
    NO_PUNCHES_ON_WORKING_DAY = "no_punches_on_working_day"


@dataclass(frozen=True, slots=True)
class Anomaly:
    """One unusual day."""

    day: date
    kind: AnomalyKind
    severity: Severity
    detail: str


def detect_anomalies(
    days: list[AttendanceDay],
    *,
    analyses: dict[date, DayAnalysis] | None = None,
    policy: ShiftPolicy | None = None,
) -> list[Anomaly]:
    """Scan a month for unusual days, most recent last."""
    policy = policy or ShiftPolicy.default()
    analyses = analyses or {}
    working = [day for day in days if day.status.counts_as_worked]

    found: list[Anomaly] = []
    norm = _in_time_norm(working)

    for day in working:
        found.extend(_day_anomalies(day, analyses.get(day.day), norm))
    return sorted(found, key=lambda anomaly: anomaly.day)


def _day_anomalies(
    day: AttendanceDay, analysis: DayAnalysis | None, norm: int | None
) -> list[Anomaly]:
    found: list[Anomaly] = []

    if day.first_in is None and day.last_out is None and day.total_hours.minutes == 0:
        found.append(
            Anomaly(
                day.day,
                AnomalyKind.NO_PUNCHES_ON_WORKING_DAY,
                Severity.WARNING,
                "Marked as a working day but nothing was logged.",
            )
        )
    elif day.first_in is not None and day.last_out is None:
        found.append(
            Anomaly(
                day.day,
                AnomalyKind.SINGLE_PUNCH,
                Severity.WARNING,
                "Clocked in but never out.",
            )
        )

    if analysis is not None:
        if any(issue.kind is IssueKind.INFERRED_OUT for issue in analysis.issues):
            found.append(
                Anomaly(
                    day.day,
                    AnomalyKind.MISSING_PUNCH,
                    Severity.WARNING,
                    "A punch is missing; hours were inferred.",
                )
            )
        if analysis.break_taken > LONG_BREAK_THRESHOLD:
            found.append(
                Anomaly(
                    day.day,
                    AnomalyKind.LONG_BREAK,
                    Severity.INFO,
                    f"Break of {analysis.break_taken} is unusually long.",
                )
            )

    if norm is not None and day.first_in is not None:
        minutes = day.first_in.hour * 60 + day.first_in.minute
        drift = minutes - norm
        if abs(drift) >= IN_TIME_DRIFT_THRESHOLD.minutes:
            direction = "later" if drift > 0 else "earlier"
            found.append(
                Anomaly(
                    day.day,
                    AnomalyKind.IN_TIME_DRIFT,
                    Severity.INFO,
                    f"Started {Duration(abs(drift))} {direction} than usual.",
                )
            )
    return found


def _in_time_norm(days: list[AttendanceDay]) -> int | None:
    """The employee's typical in-time in minutes past midnight.

    Uses the median so a couple of very late starts do not drag the baseline and mask
    further drift.
    """
    stamps = [
        day.first_in.hour * 60 + day.first_in.minute for day in days if day.first_in is not None
    ]
    if len(stamps) < _MIN_SAMPLE_FOR_DRIFT:
        return None
    return round(statistics.median(stamps))
