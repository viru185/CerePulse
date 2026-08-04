"""Voice — *how* CerePulse says things, kept separate from *what* it says.

An app you open every morning for a year should not read like a database report. ninetofive
earned most of its affection from a handful of lines that noticed you had done something,
so the same idea is carried over here — with two rules the original did not have.

**A quip is only ever added, never substituted.** Every line the analysis produced stays
exactly as written; voice appends one sentence. The consequence matters: no amount of
personality can change a number, hide a warning, or make a fact ambiguous, because the
factual half of the sentence is untouched by anything in this module.

**Bad news stays plain.** Short hours, expiring leave, missing punches, anomalies and swipe
reminders are on :data:`NEVER_PLAYFUL` and get no quip at any tone setting. Someone who
already left three hours short does not want the app to be clever about it, and a joke
attached to a warning reads as the app not understanding what it just told you.

Line choice is **deterministic on the day**, seeded from the date and the insight rather
than from a random source. The background refresh re-analyzes every fifteen minutes; if the
wording were random, the same unchanged day would reword itself four times an hour, which
reads as an app that is unsure of itself.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from enum import Enum
from zlib import crc32

from cerepulse.intelligence.day import DayAnalysis, DayState
from cerepulse.intelligence.insights import Insight, InsightKind
from cerepulse.intelligence.segments import IssueKind
from cerepulse.models.values import Duration


class Tone(Enum):
    """How much personality to use. Configured as ``ui.tone``."""

    PLAYFUL = "playful"
    PLAIN = "plain"

    @classmethod
    def parse(cls, value: str) -> Tone:
        """Resolve a configured string, defaulting to playful rather than raising."""
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            return cls.PLAYFUL


#: Kinds that stay plain whatever the setting. Everything here is either a warning, a
#: correction the user has to act on, or a day that may have gone badly for reasons the
#: portal cannot see.
NEVER_PLAYFUL = frozenset(
    {
        InsightKind.EARLY_EXIT,
        InsightKind.SHORT_HOURS,
        InsightKind.SWIPE_NEEDED,
        InsightKind.MISSING_PUNCH,
        InsightKind.LEAVE_EXPIRING,
        InsightKind.HOURS_BANK_DEFICIT,
        InsightKind.ANOMALY,
    }
)

#: Issues meaning a headline figure was inferred rather than measured. A whole day goes
#: plain when one of these is present: congratulating someone on an hour of overtime the app
#: invented from a missing punch is worse than saying nothing. A grid-only day counts —
#: its break is unknown, so a remark about lunch would be about a number nobody has.
_REPAIRED = frozenset({IssueKind.INFERRED_OUT, IssueKind.ORPHAN_OUT, IssueKind.GRID_ONLY})


def voice_day(analysis: DayAnalysis, *, tone: Tone = Tone.PLAYFUL) -> DayAnalysis:
    """Return ``analysis`` with quips added to the insights that can carry one."""
    if tone is Tone.PLAIN:
        return analysis
    voiced = tuple(_speak(insight, analysis) for insight in analysis.insights)
    return replace(analysis, insights=voiced)


def _speak(insight: Insight, analysis: DayAnalysis) -> Insight:
    bucket = _bucket(insight, analysis)
    if bucket is None:
        return insight
    options = _QUIPS.get((insight.kind, bucket))
    if not options:
        return insight

    quip = _pick(options, day=analysis.day, kind=insight.kind, bucket=bucket)
    detail = f"{insight.detail} {quip}".strip() if insight.detail else quip
    return replace(insight, detail=detail)


# --- which line to reach for -----------------------------------------------------------


def _bucket(insight: Insight, analysis: DayAnalysis) -> str | None:
    """Pick the quip bank for an insight, or ``None`` to leave it plain.

    Banding on magnitude is the whole game: "you stayed nine minutes late" and "you stayed
    three hours late" deserve very different sentences, and one bank covering both would
    have to be bland enough to fit either.
    """
    if insight.kind in NEVER_PLAYFUL:
        return None
    if any(issue.kind in _REPAIRED for issue in analysis.issues):
        return None

    if insight.kind is InsightKind.OVERTIME:
        return _band(analysis.extra_worked, (0, "token"), (20, "solid"), (120, "long"))

    if insight.kind is InsightKind.LONG_BREAK:
        # The insight has already established the break overran, so the bank turns on the
        # total rather than the overrun: "a two-hour lunch" is what a person actually
        # perceives, and it does not shift meaning when the allowance is reconfigured.
        return _band(analysis.break_taken, (0, "over"), (105, "leisurely"), (150, "epic"))

    if insight.kind is InsightKind.ON_TRACK:
        # The free-to-go moment: hours done and still clocked in. It is the one place the
        # *break* can be praised, because a break kept inside its allowance produces no
        # insight of its own — there is nothing to warn about — so the app noticed every
        # long lunch and never once a short one.
        #
        # No state test here. ON_TRACK is only ever raised on an INCOMPLETE day (see
        # `_with_insights`), so asking for COMPLETE as well would have made this bucket
        # unreachable — which is exactly what the first attempt did.
        if analysis.break_over.minutes == 0 and analysis.break_taken.minutes > 0:
            return "disciplined"
        return "free"

    # Break headroom deliberately gets nothing. It is a live figure that sits on screen for
    # the whole morning and its detail already runs to two sentences; a third, repeated
    # daily, is the fastest route to someone turning the tone off.

    if insight.kind is InsightKind.NO_PUNCHES:
        # A blank weekday might be sick leave or a bad day; only a blank weekend is safely
        # funny, because there is exactly one reason for it.
        return "weekend" if analysis.day.weekday() >= 5 else None

    if insight.kind is InsightKind.SWIPE_FILED:
        return "filed"

    # The nudges. These are the one place the app may be pointed, because they carry no
    # figure that a joke could soften — "four hours, no break" is the whole message, and a
    # line after it changes nothing about what is true. Banded so a nudge at four hours and
    # one at seven do not read the same, which is the difference between a remark and a nag.
    if insight.kind is InsightKind.NO_BREAK_YET:
        return _band(analysis.worked, (0, "due"), (330, "overdue"))

    if insight.kind is InsightKind.LEAVE_UNUSED:
        return "stale"

    if insight.kind is InsightKind.STILL_WORKING and analysis.state is DayState.INCOMPLETE:
        # Only the two ends of the day are worth a remark. At half past two with three
        # hours left there is nothing to say, and saying it anyway is how an app becomes
        # something people want to turn off.
        if analysis.work_remaining.minutes <= 45:
            return "closing"
        if analysis.work_remaining.minutes >= 300:
            return "early"
        return None

    return None


def _band(value: Duration, *thresholds: tuple[int, str]) -> str:
    """Return the label of the highest threshold ``value`` clears."""
    label = thresholds[0][1]
    for minutes, name in thresholds:
        if value.minutes >= minutes:
            label = name
    return label


def _pick(options: tuple[str, ...], *, day: date, kind: InsightKind, bucket: str) -> str:
    """Choose one line, stably for a given day and insight.

    ``crc32`` rather than ``hash``: Python salts string hashing per process, so the built-in
    would hand back a different line every time the app restarts — the exact flicker this is
    written to avoid.
    """
    seed = crc32(f"{day.toordinal()}:{kind.value}:{bucket}".encode())
    return options[seed % len(options)]


# --- the lines --------------------------------------------------------------------------
#
# Written to be read once a day for a year, so nothing here is a punchline. They observe
# something true and move on; anything that tries harder than that gets old by March.

_QUIPS: dict[tuple[InsightKind, str], tuple[str, ...]] = {
    (InsightKind.OVERTIME, "token"): (
        "Rounding error in your favour.",
        "Barely counts, but it counts.",
        "The company thanks you for the rounding.",
    ),
    (InsightKind.OVERTIME, "solid"): (
        "Noted, and appreciated.",
        "Someone owes you a coffee.",
        "Banked. Spend it wisely.",
    ),
    (InsightKind.OVERTIME, "long"): (
        "That is a genuinely long day. Go home.",
        "Above and beyond, and then some.",
        "Whatever it was, it had better have shipped.",
    ),
    (InsightKind.LONG_BREAK, "over"): (
        "The queue was long, presumably.",
        "These things happen.",
        "Barely worth mentioning, so consider it barely mentioned.",
    ),
    (InsightKind.LONG_BREAK, "leisurely"): (
        "A proper lunch, then.",
        "Unhurried. Respect.",
        "Someone enjoyed themselves.",
    ),
    (InsightKind.LONG_BREAK, "epic"): (
        "That was less a break and more an intermission.",
        "Hope it was a good restaurant.",
        "Whoever you were with, they talk a lot.",
    ),
    (InsightKind.ON_TRACK, "free"): (
        "Everything past this point is a gift.",
        "The rest of today is yours.",
        "You may leave at any time. No notes.",
    ),
    (InsightKind.ON_TRACK, "disciplined"): (
        "Hours in, break inside its allowance. A tidy day.",
        "Target met and the break behaved. Nothing to tidy up.",
        "Full day, honest lunch. That is the whole job.",
    ),
    (InsightKind.NO_PUNCHES, "weekend"): (
        "It is the weekend. That is rather the point.",
        "No punches, no problem.",
        "Correct behaviour for a Saturday or Sunday.",
    ),
    (InsightKind.SWIPE_FILED, "filed"): (
        "Nothing more for you to do.",
        "Already handled.",
        "Off your plate.",
    ),
    (InsightKind.STILL_WORKING, "early"): (
        "Long way to go. Pace yourself.",
        "Settle in.",
        "Plenty of day left.",
    ),
    (InsightKind.STILL_WORKING, "closing"): (
        "Nearly there.",
        "The end is in sight.",
        "Home stretch.",
    ),
    (InsightKind.NO_BREAK_YET, "due"): (
        "The kettle is right there.",
        "Even ten minutes counts.",
        "Nobody is giving out medals for this.",
    ),
    (InsightKind.NO_BREAK_YET, "overdue"): (
        "This is no longer impressive, it is just a long time.",
        "Whatever it is, it will survive fifteen minutes without you.",
        "Go and look at something that is not a screen.",
    ),
    (InsightKind.LEAVE_UNUSED, "stale"): (
        "Leave you never take is a pay cut you volunteered for.",
        "The work will still be here. It is very good at that.",
        "Somebody should book something.",
    ),
}


__all__ = ["NEVER_PLAYFUL", "Tone", "voice_day"]
