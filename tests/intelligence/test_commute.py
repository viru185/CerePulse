"""Turning a travel time into "when am I home", and saying so honestly.

Pure, so every clock here is injected. The arithmetic is small; what these pin is what the
caption is and is not allowed to claim.
"""

from __future__ import annotations

from datetime import datetime

from cerepulse.commute.models import TravelEstimate
from cerepulse.intelligence.commute import arrival_at, describe
from cerepulse.models.values import Duration

LEAVING = datetime(2026, 8, 4, 18, 51)


def estimate(minutes: int = 43, delay: int = 0, fetched: datetime | None = None) -> TravelEstimate:
    return TravelEstimate(
        duration=Duration(minutes),
        distance_m=27400,
        delay=Duration(delay),
        fetched_at=fetched,
    )


def test_the_journey_is_added_to_the_departure() -> None:
    arrival = arrival_at(LEAVING, estimate(43))
    assert arrival.at == datetime(2026, 8, 4, 19, 34)


def test_the_buffer_is_added_too() -> None:
    """Reaching the vehicle and parking are facts about a building, not about the road."""
    arrival = arrival_at(LEAVING, estimate(43), buffer=Duration(5))
    assert arrival.at == datetime(2026, 8, 4, 19, 39)
    assert arrival.total == Duration(48)


def test_the_departure_is_carried_not_recomputed() -> None:
    """An arrival time with no departure beside it is unfalsifiable, and the user is the
    only one who can tell whether the exit time is right."""
    assert arrival_at(LEAVING, estimate()).leave_at == LEAVING


def test_an_arrival_after_midnight_still_lands_on_the_right_day() -> None:
    arrival = arrival_at(datetime(2026, 8, 4, 23, 40), estimate(45))
    assert arrival.at == datetime(2026, 8, 5, 0, 25)


def test_the_caption_names_the_departure_it_assumed() -> None:
    assert "leaving 6:51 PM" in describe(arrival_at(LEAVING, estimate()))


def test_traffic_is_called_out_when_there_is_some() -> None:
    caption = describe(arrival_at(LEAVING, estimate(43, delay=18)))
    assert "18 min of it traffic" in caption


def test_a_clear_road_says_so_in_words() -> None:
    """Silence left "no congestion" indistinguishable from "congestion not measured" — and
    the answer was fetched with live traffic either way, so the card knows which it is."""
    assert "no traffic delay" in describe(arrival_at(LEAVING, estimate(43, delay=0)))


def test_the_distance_is_always_stated() -> None:
    """The API returned it; withholding it made the card say less than the app knew."""
    assert "27.4 km" in describe(arrival_at(LEAVING, estimate(43)))


def test_the_caption_says_how_stale_the_number_is() -> None:
    """A number that ages must say when it was taken, or the reader assumes it was this
    second."""
    fetched = datetime(2026, 8, 4, 18, 20)
    caption = describe(
        arrival_at(LEAVING, estimate(fetched=fetched)), now=datetime(2026, 8, 4, 18, 45)
    )
    assert "25 min ago" in caption


def test_a_fresh_number_says_just_now() -> None:
    fetched = datetime(2026, 8, 4, 18, 45)
    caption = describe(
        arrival_at(LEAVING, estimate(fetched=fetched)), now=datetime(2026, 8, 4, 18, 45)
    )
    assert "just now" in caption


def test_an_hours_old_number_gives_the_clock_time() -> None:
    """ "93 min ago" is arithmetic homework; "at 5:12 PM" is an answer."""
    fetched = datetime(2026, 8, 4, 17, 12)
    caption = describe(
        arrival_at(LEAVING, estimate(fetched=fetched)), now=datetime(2026, 8, 4, 18, 45)
    )
    assert "at 5:12 PM" in caption


def test_an_unfetched_estimate_claims_no_age() -> None:
    assert "checked" not in describe(arrival_at(LEAVING, estimate(fetched=None)))


def test_long_journeys_read_in_hours() -> None:
    assert "1h 12m" in describe(arrival_at(LEAVING, estimate(72)))


def test_a_round_hour_drops_the_minutes() -> None:
    assert "1h ·" in describe(arrival_at(LEAVING, estimate(60)))
