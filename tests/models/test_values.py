"""Duration — the HH.MM format is the single easiest thing to get wrong in this codebase."""

from __future__ import annotations

import pytest

from cerepulse.models.values import Duration


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        # Confirmed against the live portal: a 9:50 AM -> 6:51 PM span renders as "9.01",
        # and 9:21 AM -> 6:31 PM renders as "9.10". These are NOT decimal hours.
        ("9.01", 9 * 60 + 1),
        ("9.10", 9 * 60 + 10),
        ("9.05", 9 * 60 + 5),
        ("181.31", 181 * 60 + 31),
        ("0.00", 0),
        ("8", 8 * 60),
        ("9.5", 9 * 60 + 50),  # single digit is tens of minutes, matching the portal
    ],
)
def test_parses_portal_hhmm(text: str, minutes: int) -> None:
    assert Duration.from_hhmm(text).minutes == minutes


def test_hhmm_is_not_decimal_hours() -> None:
    """The regression this format invites: 9.01 is 9h01m, not 9.01 hours."""
    assert Duration.from_hhmm("9.01").minutes == 541
    assert Duration.from_hhmm("9.01").minutes != int(9.01 * 60)


@pytest.mark.parametrize("blank", ["", "  ", "-", "---", "--"])
def test_blank_cells_are_zero(blank: str) -> None:
    assert Duration.from_hhmm(blank).minutes == 0


def test_negative_durations_parse() -> None:
    assert Duration.from_hhmm("-1.30").minutes == -90


def test_impossible_minutes_are_rejected() -> None:
    with pytest.raises(ValueError, match="impossible minutes"):
        Duration.from_hhmm("9.75")


def test_garbage_is_rejected() -> None:
    with pytest.raises(ValueError, match="not a valid"):
        Duration.from_hhmm("abc")


def test_arithmetic_stays_in_minutes() -> None:
    total = Duration.from_hhmm("9.01") + Duration.from_hhmm("9.05")
    assert total.minutes == 18 * 60 + 6
    assert total.as_clock() == "18:06"


def test_subtraction_can_go_negative() -> None:
    short = Duration.from_hhmm("7.30") - Duration.from_hhmm("9.00")
    assert short.is_negative
    assert short.as_clock() == "-1:30"


def test_decimal_hours_is_the_real_conversion() -> None:
    assert Duration.from_hhmm("9.30").decimal_hours == 9.5


def test_zero_is_falsey() -> None:
    assert not Duration(0)
    assert Duration(1)


def test_int_is_required() -> None:
    with pytest.raises(TypeError):
        Duration(90.5)  # type: ignore[arg-type]


def test_durations_order() -> None:
    assert Duration.from_hhmm("9.01") < Duration.from_hhmm("9.02")
