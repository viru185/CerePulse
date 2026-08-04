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
from enum import Enum

from loguru import logger

from cerepulse.core.config import NotificationConfig
from cerepulse.intelligence.insights import Insight, InsightKind, Severity

#: Insight kinds that are worth interrupting someone for, mapped to their config toggle.
#:
#: ``SHORT_HOURS`` used to be here and is not, because nothing has ever constructed one:
#: ``early_exit`` is ``COMPLETE and worked < target``, which is every finished short day, so
#: ``EARLY_EXIT`` already covers the case. A toggle wired to an insight that cannot exist is
#: a setting that does nothing.
TOGGLES: dict[InsightKind, str] = {
    InsightKind.ON_TRACK: "work_target_reached",
    InsightKind.EARLY_EXIT: "short_hours_warning",
    InsightKind.SWIPE_NEEDED: "swipe_request_needed",
    InsightKind.SWIPE_DECIDED: "swipe_request_decided",
    InsightKind.LONG_BREAK: "break_exceeded",
    InsightKind.LEAVE_EXPIRING: "leave_expiring",
    InsightKind.NO_BREAK_YET: "break_reminder",
    InsightKind.LEAVE_UNUSED: "leave_reminder",
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


class Verdict(Enum):
    """Why an insight will or will not be shown.

    Named outcomes rather than a bare boolean because the commonest support question about
    notifications is "why did nothing happen?", and until now the answer only existed as a
    DEBUG log line nobody had switched on.
    """

    SEND = "send"
    DISABLED = "notifications are turned off"
    SILENT_KIND = "this kind never notifies"
    TURNED_OFF = "this alert is switched off in settings"
    QUIET_HOURS = "quiet hours"
    ALREADY_SENT = "already shown today"

    @property
    def will_send(self) -> bool:
        return self is Verdict.SEND


@dataclass
class NotificationPolicy:
    """Decides whether an insight should be shown, and remembers what already was."""

    config: NotificationConfig
    #: (kind, day) pairs already notified. Cleared when the day rolls over.
    _sent: set[tuple[InsightKind, date]] = field(default_factory=set)

    def should_notify(self, insight: Insight, *, now: datetime | None = None) -> bool:
        """True when this insight warrants a toast right now.

        A pure predicate. It used to record the insight as sent before returning, which
        meant a toast the tray then failed to deliver still burned its once-a-day slot —
        so a single dropped delivery silenced that kind until midnight. Recording is now
        :meth:`record_sent`, called only once something has actually appeared on screen.
        """
        return self.verdict(insight, now=now) is Verdict.SEND

    def verdict(self, insight: Insight, *, now: datetime | None = None) -> Verdict:
        """Why this insight will or will not be shown. Drives the Settings self-test."""
        moment = now or datetime.now()

        if not self.config.enabled:
            return Verdict.DISABLED
        if insight.kind in SILENT:
            return Verdict.SILENT_KIND

        toggle = TOGGLES.get(insight.kind)
        if toggle is None or not getattr(self.config, toggle, False):
            return Verdict.TURNED_OFF

        # A critical insight still waits for morning; nothing here is an emergency.
        if self.in_quiet_hours(moment):
            return Verdict.QUIET_HOURS

        if (insight.kind, moment.date()) in self._sent:
            return Verdict.ALREADY_SENT
        return Verdict.SEND

    def record_sent(self, insight: Insight, *, now: datetime | None = None) -> None:
        """Remember a toast that was genuinely delivered, so it is not repeated today."""
        moment = now or datetime.now()
        self._sent.add((insight.kind, moment.date()))
        self._forget_older_than(moment.date())

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
