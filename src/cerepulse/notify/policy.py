"""Which insights become desktop notifications, and how often.

Two rules do most of the work here, and both exist to keep the app from becoming noise:

* **Once per kind per day.** The background refresh runs every fifteen minutes and produces
  the same insights each time. Without suppression, "target met" would fire four times an
  hour until the user quit the app.
* **Quiet hours.** Overnight notifications are worse than useless. The window is allowed to
  wrap past midnight, which is the normal case.

Everything is decided from configuration and an injected ``now``, so the policy is testable
without a tray, a clock, or a desktop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

from loguru import logger

from cerepulse.core.config import NotificationConfig
from cerepulse.intelligence.insights import Insight, InsightKind, Severity

#: Insight kinds that are worth interrupting someone for, mapped to their config toggle.
TOGGLES: dict[InsightKind, str] = {
    InsightKind.ON_TRACK: "work_target_reached",
    InsightKind.EARLY_EXIT: "short_hours_warning",
    InsightKind.SHORT_HOURS: "short_hours_warning",
    InsightKind.SWIPE_NEEDED: "swipe_request_needed",
    InsightKind.LONG_BREAK: "break_exceeded",
    InsightKind.LEAVE_EXPIRING: "leave_expiring",
}

#: Kinds that never notify — informational only, shown in the window.
SILENT = {
    InsightKind.STILL_WORKING,
    InsightKind.NO_PUNCHES,
    InsightKind.OVERTIME,
    InsightKind.SWIPE_FILED,
    InsightKind.MISSING_PUNCH,
    InsightKind.ANOMALY,
    InsightKind.HOURS_BANK_DEFICIT,
    # Useful on screen while deciding about lunch; not worth a toast.
    InsightKind.BREAK_HEADROOM,
    # A caveat about where the numbers came from, not news.
    InsightKind.GRID_ONLY,
}


@dataclass
class NotificationPolicy:
    """Decides whether an insight should be shown, and remembers what already was."""

    config: NotificationConfig
    #: (kind, day) pairs already notified. Cleared when the day rolls over.
    _sent: set[tuple[InsightKind, date]] = field(default_factory=set)

    def should_notify(self, insight: Insight, *, now: datetime | None = None) -> bool:
        """True when this insight warrants a toast right now."""
        moment = now or datetime.now()

        if not self.config.enabled:
            return False
        if insight.kind in SILENT:
            return False

        toggle = TOGGLES.get(insight.kind)
        if toggle is None or not getattr(self.config, toggle, False):
            return False

        # A critical insight still waits for morning; nothing here is an emergency.
        if self.in_quiet_hours(moment):
            logger.debug("Suppressed {} — quiet hours", insight.kind.value)
            return False

        key = (insight.kind, moment.date())
        if key in self._sent:
            return False

        self._sent.add(key)
        self._forget_older_than(moment.date())
        return True

    def in_quiet_hours(self, moment: datetime) -> bool:
        """Whether ``moment`` falls inside the configured quiet window.

        The window normally wraps past midnight (21:00 to 08:00), so the comparison differs
        depending on whether start is before or after end.
        """
        start = _parse_time(self.config.quiet_hours_start)
        end = _parse_time(self.config.quiet_hours_end)
        if start is None or end is None or start == end:
            return False

        current = moment.time()
        if start < end:
            return start <= current < end
        return current >= start or current < end

    def reset(self) -> None:
        """Forget what has been sent. Used when settings change or the user signs out."""
        self._sent.clear()

    def _forget_older_than(self, today: date) -> None:
        stale = {key for key in self._sent if key[1] != today}
        self._sent -= stale


def notification_title(insight: Insight) -> str:
    """Prefix urgent insights so the severity survives Windows' plain toast styling."""
    if insight.severity is Severity.CRITICAL:
        return f"Action needed — {insight.title}"
    return insight.title


def _parse_time(text: str) -> time | None:
    try:
        hours, _, minutes = text.partition(":")
        return time(int(hours), int(minutes or 0))
    except (ValueError, TypeError):
        logger.warning("Invalid quiet-hours value {!r}; ignoring", text)
        return None
