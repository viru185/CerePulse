"""Notification policy, tray readout, and startup registration."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time

import pytest

from cerepulse.core.config import NotificationConfig
from cerepulse.intelligence.day import analyze_day
from cerepulse.intelligence.insights import Insight, InsightKind, Severity
from cerepulse.models.attendance import Punch, PunchDirection
from cerepulse.notify.policy import SILENT, NotificationPolicy, Verdict, notification_title
from cerepulse.notify.startup import RUN_KEY, VALUE_NAME, executable_command, is_frozen
from cerepulse.notify.tray import Tray

DAY = date(2026, 7, 28)
MIDDAY = datetime(2026, 7, 28, 12, 0)


def punches(*pairs: tuple[str, str]) -> list[Punch]:
    out = []
    for clock, direction in pairs:
        hour, minute = (int(part) for part in clock.split(":"))
        out.append(Punch(at=time(hour, minute), direction=PunchDirection.parse(direction)))
    return out


def insight(kind: InsightKind, severity: Severity = Severity.INFO) -> Insight:
    return Insight(kind, severity, f"{kind.value} title", "detail")


@pytest.fixture
def policy() -> NotificationPolicy:
    return NotificationPolicy(NotificationConfig())


# --- gating ---------------------------------------------------------------------------


def test_an_enabled_alert_notifies(policy: NotificationPolicy) -> None:
    assert policy.should_notify(insight(InsightKind.SWIPE_NEEDED), now=MIDDAY)


def test_the_master_toggle_silences_everything() -> None:
    policy = NotificationPolicy(replace(NotificationConfig(), enabled=False))
    assert not policy.should_notify(insight(InsightKind.SWIPE_NEEDED), now=MIDDAY)


def test_a_disabled_per_alert_toggle_is_respected() -> None:
    policy = NotificationPolicy(replace(NotificationConfig(), swipe_request_needed=False))
    assert not policy.should_notify(insight(InsightKind.SWIPE_NEEDED), now=MIDDAY)
    # Other kinds are unaffected.
    assert policy.should_notify(insight(InsightKind.LEAVE_EXPIRING), now=MIDDAY)


@pytest.mark.parametrize("kind", sorted(SILENT, key=lambda k: k.value))
def test_informational_kinds_never_notify(policy: NotificationPolicy, kind: InsightKind) -> None:
    """These belong in the window, not in a toast."""
    assert not policy.should_notify(insight(kind), now=MIDDAY)


# --- repetition -----------------------------------------------------------------------


def test_asking_whether_to_notify_does_not_consume_the_slot(
    policy: NotificationPolicy,
) -> None:
    """The bug this replaced: a toast the tray then dropped still silenced its kind.

    ``should_notify`` used to record the insight as sent before returning ``True``, so any
    delivery failure — a hidden tray, an unsupported desktop — burned the once-a-day slot
    and that alert stayed silent until midnight.
    """
    assert policy.should_notify(insight(InsightKind.ON_TRACK), now=MIDDAY)
    assert policy.should_notify(insight(InsightKind.ON_TRACK), now=MIDDAY)
    assert not policy._sent


def test_the_same_alert_only_fires_once_a_day(policy: NotificationPolicy) -> None:
    """The background refresh runs every 15 minutes and yields the same insights each time."""
    alert = insight(InsightKind.ON_TRACK)
    assert policy.should_notify(alert, now=MIDDAY)
    policy.record_sent(alert, now=MIDDAY)

    assert not policy.should_notify(alert, now=MIDDAY)
    assert not policy.should_notify(alert, now=datetime(2026, 7, 28, 17, 30))


def test_a_new_day_allows_the_alert_again(policy: NotificationPolicy) -> None:
    alert = insight(InsightKind.ON_TRACK)
    policy.record_sent(alert, now=MIDDAY)
    assert policy.should_notify(alert, now=datetime(2026, 7, 29, 12, 0))


def test_different_kinds_do_not_suppress_each_other(policy: NotificationPolicy) -> None:
    policy.record_sent(insight(InsightKind.ON_TRACK), now=MIDDAY)
    assert policy.should_notify(insight(InsightKind.SWIPE_NEEDED), now=MIDDAY)


def test_yesterdays_record_is_forgotten(policy: NotificationPolicy) -> None:
    policy.record_sent(insight(InsightKind.ON_TRACK), now=MIDDAY)
    policy.record_sent(insight(InsightKind.ON_TRACK), now=datetime(2026, 7, 29, 12, 0))
    assert len(policy._sent) == 1


def test_reset_clears_the_record(policy: NotificationPolicy) -> None:
    alert = insight(InsightKind.ON_TRACK)
    policy.record_sent(alert, now=MIDDAY)
    policy.reset()
    assert policy.should_notify(alert, now=MIDDAY)


# --- why nothing appeared ---------------------------------------------------------------


def test_the_verdict_names_the_reason(policy: NotificationPolicy) -> None:
    """ "Nothing happened" is the commonest complaint; this is what answers it."""
    alert = insight(InsightKind.ON_TRACK)
    assert policy.verdict(alert, now=MIDDAY) is Verdict.SEND

    policy.record_sent(alert, now=MIDDAY)
    assert policy.verdict(alert, now=MIDDAY) is Verdict.ALREADY_SENT
    assert policy.verdict(alert, now=datetime(2026, 7, 28, 23, 0)) is Verdict.QUIET_HOURS
    assert policy.verdict(insight(InsightKind.STILL_WORKING), now=MIDDAY) is Verdict.SILENT_KIND


def test_a_switched_off_alert_is_distinguished_from_a_switched_off_app() -> None:
    off = NotificationPolicy(replace(NotificationConfig(), swipe_request_needed=False))
    assert off.verdict(insight(InsightKind.SWIPE_NEEDED), now=MIDDAY) is Verdict.TURNED_OFF

    disabled = NotificationPolicy(replace(NotificationConfig(), enabled=False))
    assert disabled.verdict(insight(InsightKind.SWIPE_NEEDED), now=MIDDAY) is Verdict.DISABLED


# --- quiet hours ----------------------------------------------------------------------


def test_quiet_hours_wrap_past_midnight(policy: NotificationPolicy) -> None:
    """The default window is 21:00 to 08:00, which straddles the date boundary."""
    assert policy.in_quiet_hours(datetime(2026, 7, 28, 22, 0))
    assert policy.in_quiet_hours(datetime(2026, 7, 28, 2, 0))
    assert policy.in_quiet_hours(datetime(2026, 7, 28, 7, 59))
    assert not policy.in_quiet_hours(datetime(2026, 7, 28, 8, 0))
    assert not policy.in_quiet_hours(MIDDAY)


def test_a_daytime_quiet_window_does_not_wrap() -> None:
    policy = NotificationPolicy(
        replace(NotificationConfig(), quiet_hours_start="12:00", quiet_hours_end="14:00")
    )
    assert policy.in_quiet_hours(datetime(2026, 7, 28, 13, 0))
    assert not policy.in_quiet_hours(datetime(2026, 7, 28, 15, 0))
    assert not policy.in_quiet_hours(datetime(2026, 7, 28, 23, 0))


def test_nothing_fires_during_quiet_hours(policy: NotificationPolicy) -> None:
    assert not policy.should_notify(
        insight(InsightKind.SWIPE_NEEDED), now=datetime(2026, 7, 28, 23, 0)
    )


def test_a_suppressed_alert_is_not_marked_as_sent(policy: NotificationPolicy) -> None:
    """Otherwise a quiet-hours alert would be silently swallowed for the whole day."""
    policy.should_notify(insight(InsightKind.SWIPE_NEEDED), now=datetime(2026, 7, 28, 23, 0))
    assert policy.should_notify(insight(InsightKind.SWIPE_NEEDED), now=datetime(2026, 7, 29, 9, 0))


def test_an_equal_window_disables_quiet_hours() -> None:
    policy = NotificationPolicy(
        replace(NotificationConfig(), quiet_hours_start="09:00", quiet_hours_end="09:00")
    )
    assert not policy.in_quiet_hours(datetime(2026, 7, 28, 9, 0))


def test_a_malformed_window_is_ignored_rather_than_crashing() -> None:
    policy = NotificationPolicy(replace(NotificationConfig(), quiet_hours_start="nonsense"))
    assert not policy.in_quiet_hours(MIDDAY)


# --- titles ---------------------------------------------------------------------------


def test_critical_insights_are_marked_in_the_title() -> None:
    assert notification_title(insight(InsightKind.LEAVE_EXPIRING, Severity.CRITICAL)).startswith(
        "Action needed"
    )
    assert not notification_title(insight(InsightKind.ON_TRACK)).startswith("Action needed")


# --- tray readout ---------------------------------------------------------------------


def test_tooltip_before_any_sync() -> None:
    assert "Not synced yet" in Tray.tooltip_text(None, now=MIDDAY)


def test_tooltip_counts_down_while_still_clocked_in() -> None:
    analysis = analyze_day(punches(("09:00", "in")), day=DAY, now=datetime(2026, 7, 28, 13, 0))
    text = Tray.tooltip_text(analysis, now=datetime(2026, 7, 28, 13, 0))

    assert "Leave at 6:00 PM" in text
    assert "5:00 to go" in text
    assert "Worked 4:00" in text


def test_tooltip_says_you_can_go_once_the_target_is_met() -> None:
    analysis = analyze_day(punches(("09:00", "in")), day=DAY, now=datetime(2026, 7, 28, 18, 30))
    assert "Target met" in Tray.tooltip_text(analysis, now=datetime(2026, 7, 28, 18, 30))


def test_tooltip_for_a_finished_day() -> None:
    analysis = analyze_day(
        punches(("09:00", "in"), ("13:00", "out"), ("14:00", "in"), ("18:00", "out")), day=DAY
    )
    text = Tray.tooltip_text(analysis, now=MIDDAY)

    assert "Left at 6:00 PM" in text
    assert "short of target" not in text


def test_tooltip_flags_a_short_day() -> None:
    analysis = analyze_day(punches(("09:00", "in"), ("15:00", "out")), day=DAY)
    assert "short of target" in Tray.tooltip_text(analysis, now=MIDDAY)


def test_tooltip_for_a_day_with_no_punches() -> None:
    analysis = analyze_day([], day=DAY)
    assert "No punches recorded" in Tray.tooltip_text(analysis, now=MIDDAY)


# --- tray delivery ----------------------------------------------------------------------
#
# The tray was previously untested in its entirety, which is exactly why an unshowable
# notification could burn its once-a-day slot and nobody noticed. These instantiate it for
# real; the offscreen Qt platform gives a tray that never becomes visible, which is the
# failing-delivery case the bug depended on.


@pytest.fixture
def tray(qapp: object) -> Tray:
    from PySide6.QtGui import QIcon

    return Tray(QIcon(), NotificationConfig())


def test_an_undeliverable_toast_says_so_rather_than_pretending(tray: Tray) -> None:
    assert tray.delivery_problem() is not None
    assert tray.notify(insight(InsightKind.SWIPE_NEEDED)) is False


def test_a_dropped_toast_does_not_burn_its_slot(tray: Tray) -> None:
    """The whole bug in one test: it must still be sendable once delivery works."""
    alert = insight(InsightKind.SWIPE_NEEDED)

    assert tray.notify_insights([alert], now=MIDDAY) == 0
    assert tray.policy.should_notify(alert, now=MIDDAY)
    assert not tray.policy._sent


def test_notify_insights_counts_what_actually_appeared(tray: Tray) -> None:
    """It used to count what the policy allowed, whether or not anything was shown."""
    assert tray.notify_insights([insight(InsightKind.SWIPE_NEEDED)], now=MIDDAY) == 0


def test_the_self_test_reports_the_reason_rather_than_failing_silently(tray: Tray) -> None:
    outcome = tray.self_test(now=MIDDAY)
    assert "Could not show a notification" in outcome


def test_the_self_test_says_when_notifications_are_switched_off(qapp: object) -> None:
    from PySide6.QtGui import QIcon

    off = Tray(QIcon(), replace(NotificationConfig(), enabled=False))
    assert "switched off" in off.self_test(now=MIDDAY)


# --- startup registration -------------------------------------------------------------


def test_startup_targets_the_per_user_run_key() -> None:
    """Per-user, so it needs no elevation and is where users look to disable it."""
    assert RUN_KEY == r"Software\Microsoft\Windows\CurrentVersion\Run"
    assert VALUE_NAME == "CerePulse"


def test_the_startup_command_is_quoted() -> None:
    command = executable_command()
    assert command.startswith('"') and command.endswith('"')


def test_source_runs_are_not_registered() -> None:
    """A dev entry would point into a virtual environment and break on the next rebuild."""
    from cerepulse.notify.startup import set_registered

    if not is_frozen():
        assert set_registered(True) is False
