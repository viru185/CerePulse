"""Helpers for building punch logs in tests."""

from __future__ import annotations

from datetime import datetime, time

import pytest

from cerepulse.models.attendance import Punch, PunchDirection

DAY = datetime(2026, 7, 28).date()


def punch(clock: str, direction: str) -> Punch:
    """``punch("09:21", "in")`` — terse enough to write a whole day inline."""
    hour, minute = (int(part) for part in clock.split(":"))
    return Punch(at=time(hour, minute), direction=PunchDirection.parse(direction))


def punches(*pairs: tuple[str, str]) -> list[Punch]:
    return [punch(clock, direction) for clock, direction in pairs]


def at(clock: str) -> datetime:
    """A ``now`` on the analyzed day."""
    hour, minute = (int(part) for part in clock.split(":"))
    return datetime.combine(DAY, time(hour, minute))


@pytest.fixture
def day() -> object:
    return DAY
