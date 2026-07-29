"""Voice: personality that can never change a fact or soften a warning."""

from __future__ import annotations

from datetime import date

import pytest

from cerepulse.intelligence.day import analyze_day
from cerepulse.intelligence.insights import InsightKind
from cerepulse.intelligence.voice import NEVER_PLAYFUL, Tone, voice_day
from tests.intelligence.conftest import DAY, at, punches

#: DAY is a Tuesday; these bracket it for the weekend-specific lines.
SATURDAY = date(2026, 7, 25)


def find(analysis, kind: InsightKind):  # type: ignore[no-untyped-def]
    return next(i for i in analysis.insights if i.kind is kind)


# --- the contract ---------------------------------------------------------------------


def test_plain_tone_changes_nothing_at_all() -> None:
    analysis = analyze_day(
        punches(("09:00", "in"), ("13:00", "out"), ("14:00", "in"), ("19:30", "out")), day=DAY
    )
    assert voice_day(analysis, tone=Tone.PLAIN) == analysis


def test_a_quip_is_appended_never_substituted() -> None:
    """The factual half of every sentence has to survive verbatim."""
    analysis = analyze_day(
        punches(("09:00", "in"), ("13:00", "out"), ("14:00", "in"), ("19:30", "out")), day=DAY
    )
    plain = find(analysis, InsightKind.OVERTIME)
    voiced = find(voice_day(analysis, tone=Tone.PLAYFUL), InsightKind.OVERTIME)

    assert voiced.title == plain.title
    assert voiced.severity is plain.severity
    assert voiced.detail.startswith(plain.detail)
    assert len(voiced.detail) > len(plain.detail)


def test_bad_news_stays_plain_even_when_playful() -> None:
    """A joke attached to a warning reads as the app not understanding what it just said."""
    analysis = analyze_day(punches(("09:00", "in"), ("15:00", "out")), day=DAY)
    voiced = voice_day(analysis, tone=Tone.PLAYFUL)

    for insight in voiced.insights:
        if insight.kind in NEVER_PLAYFUL:
            assert insight == find(analysis, insight.kind)


def test_a_short_day_gets_no_personality_anywhere() -> None:
    analysis = analyze_day(punches(("09:00", "in"), ("15:00", "out")), day=DAY)
    assert voice_day(analysis, tone=Tone.PLAYFUL) == analysis


def test_a_repaired_day_goes_entirely_plain() -> None:
    """The overtime here was inferred from a punch that is missing. Do not congratulate it."""
    analysis = analyze_day(punches(("09:00", "in"), ("12:00", "in"), ("19:00", "out")), day=DAY)

    assert InsightKind.OVERTIME in {i.kind for i in analysis.insights}
    assert voice_day(analysis, tone=Tone.PLAYFUL) == analysis


# --- determinism ----------------------------------------------------------------------


def test_the_same_day_always_gets_the_same_line() -> None:
    """The background refresh re-analyzes every fifteen minutes; the wording must hold."""
    analysis = analyze_day(
        punches(("09:00", "in"), ("13:00", "out"), ("14:00", "in"), ("19:30", "out")), day=DAY
    )
    first = voice_day(analysis, tone=Tone.PLAYFUL)
    second = voice_day(analysis, tone=Tone.PLAYFUL)

    assert [i.detail for i in first.insights] == [i.detail for i in second.insights]


def test_different_days_do_not_all_read_alike() -> None:
    """Otherwise the one line becomes wallpaper within a week."""
    seen = set()
    for offset in range(14):
        day = date.fromordinal(DAY.toordinal() + offset)
        analysis = analyze_day(
            punches(("09:00", "in"), ("13:00", "out"), ("14:00", "in"), ("19:30", "out")),
            day=day,
        )
        seen.add(find(voice_day(analysis, tone=Tone.PLAYFUL), InsightKind.OVERTIME).detail)

    assert len(seen) > 1


# --- banding --------------------------------------------------------------------------


def test_nine_minutes_and_three_hours_do_not_share_a_line() -> None:
    """One is a rounding error and one is a very long evening."""
    small = analyze_day(
        punches(("09:00", "in"), ("13:00", "out"), ("14:00", "in"), ("18:09", "out")), day=DAY
    )
    huge = analyze_day(
        punches(("09:00", "in"), ("13:00", "out"), ("14:00", "in"), ("21:00", "out")), day=DAY
    )

    voiced_small = find(voice_day(small, tone=Tone.PLAYFUL), InsightKind.OVERTIME)
    voiced_huge = find(voice_day(huge, tone=Tone.PLAYFUL), InsightKind.OVERTIME)
    assert voiced_small.detail != voiced_huge.detail


def test_half_an_hour_over_is_not_called_a_rounding_error() -> None:
    """Banding on the wrong side of the line is how a compliment becomes a slight."""
    analysis = analyze_day(
        punches(("09:00", "in"), ("13:00", "out"), ("14:00", "in"), ("18:35", "out")), day=DAY
    )
    detail = find(voice_day(analysis, tone=Tone.PLAYFUL), InsightKind.OVERTIME).detail
    assert "Rounding error" not in detail
    assert "Barely counts" not in detail


def test_break_headroom_never_gets_a_quip() -> None:
    """It sits on screen all morning; a daily joke there is the first thing to be muted."""
    analysis = analyze_day(punches(("09:00", "in")), day=DAY, now=at("11:00"))
    plain = find(analysis, InsightKind.BREAK_HEADROOM)
    voiced = find(voice_day(analysis, tone=Tone.PLAYFUL), InsightKind.BREAK_HEADROOM)

    assert voiced.detail == plain.detail


def test_the_middle_of_a_long_day_gets_no_commentary() -> None:
    """Nothing to say at half past two, and saying it anyway is how an app gets muted."""
    analysis = analyze_day(punches(("09:00", "in")), day=DAY, now=at("13:00"))
    plain = find(analysis, InsightKind.STILL_WORKING)
    voiced = find(voice_day(analysis, tone=Tone.PLAYFUL), InsightKind.STILL_WORKING)

    assert voiced.detail == plain.detail


def test_the_home_stretch_does() -> None:
    analysis = analyze_day(punches(("09:00", "in")), day=DAY, now=at("17:30"))
    voiced = find(voice_day(analysis, tone=Tone.PLAYFUL), InsightKind.ON_TRACK)
    assert len(voiced.detail) > len(find(analysis, InsightKind.ON_TRACK).detail)


# --- empty days -----------------------------------------------------------------------


def test_an_empty_weekend_is_fair_game() -> None:
    voiced = find(voice_day(analyze_day([], day=SATURDAY)), InsightKind.NO_PUNCHES)
    plain = find(analyze_day([], day=SATURDAY), InsightKind.NO_PUNCHES)
    assert voiced.detail != plain.detail


def test_an_empty_weekday_is_not() -> None:
    """It might be sick leave, or a day that went badly for reasons the portal cannot see."""
    voiced = find(voice_day(analyze_day([], day=DAY)), InsightKind.NO_PUNCHES)
    plain = find(analyze_day([], day=DAY), InsightKind.NO_PUNCHES)
    assert voiced.detail == plain.detail


# --- configuration --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("playful", Tone.PLAYFUL),
        ("plain", Tone.PLAIN),
        ("  PLAIN  ", Tone.PLAIN),
        ("nonsense", Tone.PLAYFUL),
        ("", Tone.PLAYFUL),
    ],
)
def test_a_bad_tone_setting_degrades_to_the_default(value: str, expected: Tone) -> None:
    assert Tone.parse(value) is expected
