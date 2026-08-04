"""What a journey is made of. Frozen, storage-agnostic, and free of any provider's wording."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from cerepulse.models.values import Duration


@dataclass(frozen=True, slots=True)
class Place:
    """A geocoded point, keeping both what was asked for and what was found.

    Both, because they disagree more often than anyone expects: a house number that resolves
    to the next town over is a wrong answer that produces a perfectly plausible travel time,
    and the only way anyone catches it is by being shown what the address actually matched.
    """

    #: What the user typed. Kept so Settings can show it back unchanged.
    label: str
    #: What the provider matched it to, in the provider's own words.
    resolved: str
    latitude: float
    longitude: float

    @property
    def coordinates(self) -> str:
        """``lat,lon`` — the form every routing endpoint here wants."""
        return f"{self.latitude},{self.longitude}"

    @property
    def is_located(self) -> bool:
        """Whether this is a real point rather than a placeholder from an empty setting.

        Null Island is not a plausible commute, and treating (0, 0) as located would send
        somebody a route across the Atlantic rather than an honest "set your address".
        """
        return not (self.latitude == 0.0 and self.longitude == 0.0)


@dataclass(frozen=True, slots=True)
class TravelEstimate:
    """How long the journey takes, and how much of that is traffic."""

    duration: Duration
    distance_m: int
    #: The part of ``duration`` the provider attributes to congestion. Reported separately
    #: because "50 minutes" and "50 minutes, 18 of them queueing" are different facts, and
    #: only the second one is an argument for leaving now.
    delay: Duration = Duration(0)
    fetched_at: datetime | None = None
    provider: str = "TomTom"

    @property
    def distance_km(self) -> float:
        return self.distance_m / 1000

    @property
    def free_flow(self) -> Duration:
        """What it would take with an empty road."""
        return Duration(max(0, self.duration.minutes - self.delay.minutes))

    @property
    def is_congested(self) -> bool:
        """Enough delay to be worth mentioning rather than rounding noise."""
        return self.delay.minutes >= 5
