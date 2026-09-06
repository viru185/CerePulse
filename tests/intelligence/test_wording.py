"""The portal's three dialects, read as one reason."""

from __future__ import annotations

from datetime import date

import pytest

from cerepulse.intelligence.wording import clean_remark, day_label, days_phrase, same_reason

REASON = "Need to visit home once in a while."


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        (f"{REASON} 🏡✈️ Manager :", f"{REASON} 🏡✈️"),
        (f"{REASON} Manager : HOD :", REASON),
        (f"{REASON} ????", REASON),
        ("Why?? Nobody said.", "Why?? Nobody said."),
        ("Attendance Muster", ""),
        ("  Late   entry (In Cutoff)  ", "Late entry (In Cutoff)"),
        ("Manager : on leave", "Manager: on leave"),
        ("", ""),
    ],
)
def test_the_portal_additions_are_stripped_and_the_person_kept(raw: str, cleaned: str) -> None:
    assert clean_remark(raw) == cleaned


def test_the_emoji_and_the_mangled_copy_are_one_reason() -> None:
    assert same_reason(f"{REASON} 🏡✈️ Manager :", f"{REASON} ????")


def test_different_reasons_stay_different() -> None:
    assert not same_reason("night work, lfo tag rectification.", REASON)


def test_a_blank_side_agrees() -> None:
    assert same_reason("", REASON)
    assert same_reason("Attendance Muster", REASON)


@pytest.mark.parametrize(
    ("days", "phrase"),
    [(0.5, "Half day"), (1, "1 day"), (1.0, "1 day"), (2, "2 days"), (5.5, "5.5 days")],
)
def test_days_are_said_not_counted(days: float, phrase: str) -> None:
    assert days_phrase(days) == phrase


def test_the_day_label_has_no_leading_zero() -> None:
    assert day_label(date(2026, 8, 4)) == "Tue 4 Aug"
