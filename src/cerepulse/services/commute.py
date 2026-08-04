"""When the arrival estimate is worth spending a call on, and when it is not.

The quota is not the constraint. One call per working day is roughly 22 a month against an
allowance of 20,000, so this exists for a different reason: **calls that cannot change the
answer are waste**, and the most common source of them is a person clicking Refresh three
times because the first click did not visibly do anything.

Three guards, in this order, and one rule above them: **Refresh always answers.** It never
greys out and it never refuses. What varies is whether answering costs a request.

1. *In-flight* — a second caller while one is running gets the running one's result. This is
   an explicit flag rather than a reliance on ``TaskRunner``, whose pool is single-slot and
   therefore *serialises* ten clicks into ten sequential calls, one after another, each
   answering a question the previous one already answered.
2. *Freshness floor* — an estimate under a minute old for the same departure is re-served
   as-is, with its own "checked at" intact so the screen never implies it was just taken.
3. *Daily budget* — a hard ceiling. Reached, the app stops calling and says so.

Estimates are held in memory and never written to the database. That keeps us inside
TomTom's terms, which restrict caching results beyond their own cache headers, without
having to reason about those headers per response — and it costs exactly one call after a
restart.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from loguru import logger

from cerepulse.commute.models import Place, TravelEstimate
from cerepulse.commute.tomtom import KeyCheck, TomTomClient, TravelMode
from cerepulse.core.config import AppConfig
from cerepulse.core.errors import CommuteError
from cerepulse.intelligence.commute import Arrival, arrival_at
from cerepulse.models.values import Duration

#: An estimate younger than this, for the same departure, is re-served rather than re-fetched.
#: A minute is long enough to swallow a burst of clicking and short enough that nobody is
#: looking at a stale road.
MIN_REFRESH_SECONDS = 60

#: How close to the predicted exit the app will spend its one automatic call. Earlier than
#: this and the answer describes a departure hours away that nobody is acting on yet.
LEAD_MINUTES = 30

#: How far the predicted exit has to move before the automatic call is worth repeating.
RESETTLE_MINUTES = 15

#: Departures are snapped to this, so ordinary minute-by-minute drift in the prediction does
#: not read as a new question.
BUCKET_MINUTES = 15


def _bucket(when: datetime) -> datetime:
    """Round a departure down to its bucket."""
    return when.replace(
        minute=(when.minute // BUCKET_MINUTES) * BUCKET_MINUTES, second=0, microsecond=0
    )


@dataclass(frozen=True, slots=True)
class CommuteView:
    """What the card renders. Every failure mode is a state here, not an exception."""

    arrival: Arrival | None = None
    #: Set when there is nothing to show and the reason is worth saying out loud.
    message: str = ""
    #: Whether the user could fix it in Settings — drives whether the card offers a button.
    needs_setup: bool = False
    #: True while a fetch is running, so the card can say so without disabling anything.
    updating: bool = False

    @property
    def is_ready(self) -> bool:
        return self.arrival is not None


class CommuteService:
    """Arrival estimates, with the call discipline above. Never touched from the GUI thread."""

    def __init__(
        self,
        *,
        config: AppConfig,
        api_key: str = "",
        client: TomTomClient | None = None,
    ) -> None:
        self._config = config
        self._api_key = api_key
        self._client = client
        self._lock = threading.Lock()
        self._held: TravelEstimate | None = None
        self._held_for: datetime | None = None
        self._spent = 0
        self._spent_on: date | None = None

    # --- configuration --------------------------------------------------------------

    def use_config(self, config: AppConfig) -> None:
        """Adopt a newly saved configuration, so Settings applies without a restart."""
        previous = self._config.commute
        self._config = config
        if (previous.destination_lat, previous.destination_lon, previous.mode) != (
            config.commute.destination_lat,
            config.commute.destination_lon,
            config.commute.mode,
        ):
            # A different journey entirely. Holding the old one would answer the previous
            # question with a fresh-looking timestamp.
            self._forget()

    def use_key(self, api_key: str) -> None:
        self._api_key = api_key.strip()
        self._client = None
        self._forget()

    def _forget(self) -> None:
        with self._lock:
            self._held = None
            self._held_for = None

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key) and self._destination().is_located

    def validate_key(self, api_key: str) -> KeyCheck:
        """Check a key the user just pasted. Deliberately not subject to the budget — a key
        nobody can verify is worse than one extra call."""
        return TomTomClient(api_key).validate()

    # --- estimates ------------------------------------------------------------------

    def estimate(
        self, leave_at: datetime, *, force: bool = False, now: datetime | None = None
    ) -> CommuteView:
        """The arrival estimate for a departure, fetching only when that could change it.

        ``force`` is what the Refresh button passes. It bypasses the *automatic* trigger's
        judgement about whether now is a sensible time to ask — it does not bypass the
        freshness floor, the in-flight guard or the budget, because those exist precisely to
        make repeated pressing harmless.
        """
        if not self._api_key:
            return CommuteView(
                message="Add a TomTom API key in Settings to see when you'll get home.",
                needs_setup=True,
            )
        destination = self._destination()
        if not destination.is_located:
            return CommuteView(
                message="Set your home address in Settings to see when you'll get home.",
                needs_setup=True,
            )

        moment = now or datetime.now()
        departure = _bucket(leave_at)
        with self._lock:
            held, held_for = self._held, self._held_for

        if held is not None and held_for == departure:
            # Held for this exact departure. Re-serve it unless the caller pressed Refresh
            # *and* the floor has passed, in which case the road is worth re-reading.
            if not force or self._is_fresh(held, moment):
                return self._view(held, leave_at)

        return self._fetch(destination, departure, leave_at, moment)

    def _fetch(
        self, destination: Place, departure: datetime, leave_at: datetime, now: datetime
    ) -> CommuteView:
        # One at a time. Whoever holds the lock does the work; everyone else takes the result
        # it leaves behind rather than starting a second identical journey.
        with self._lock:
            if (
                self._held is not None
                and self._held_for == departure
                and self._is_fresh(self._held, now)
            ):
                return self._view(self._held, leave_at)

            if not self._within_budget():
                message = (
                    f"Checked {self._spent} times today, which is the daily limit. "
                    "Raise it in Settings if you need more."
                )
                if self._held is not None:
                    return replace(self._view(self._held, leave_at), message=message)
                return CommuteView(message=message)

            try:
                estimate = self._tomtom().route(
                    self._origin(),
                    destination,
                    mode=TravelMode.parse(self._config.commute.mode),
                    depart_at=departure,
                )
            except CommuteError as exc:
                logger.warning("Commute estimate failed: {}", exc)
                # A provider outage costs this one card and nothing else. If an older
                # estimate is held, showing it with its real age beats showing nothing.
                if self._held is not None:
                    return replace(
                        self._view(self._held, leave_at),
                        message="Could not reach TomTom just now — this is the last one.",
                    )
                return CommuteView(message=str(exc))

            self._count()
            # Stamped with *this* service's clock rather than the provider's. The freshness
            # floor and the "checked N minutes ago" caption both compare against it, and two
            # clocks in one comparison is how a floor silently stops holding.
            estimate = replace(estimate, fetched_at=now)
            self._held, self._held_for = estimate, departure
            logger.info(
                "Commute: {} min for {:.1f} km (call {} today)",
                estimate.duration.minutes,
                estimate.distance_km,
                self._spent,
            )
            return self._view(estimate, leave_at)

    def should_ask(self, leave_at: datetime, *, now: datetime) -> bool:
        """Whether the automatic trigger should spend a call yet.

        True only inside the lead window, and then only once per departure bucket. The
        Refresh button never consults this — it is about when to ask *unprompted*, and a
        person who presses the button has already said now is a good time.
        """
        if not self.is_configured:
            return False
        if leave_at < now:
            return False
        if leave_at - now > timedelta(minutes=LEAD_MINUTES):
            return False
        with self._lock:
            if self._held_for is None:
                return True
            drift = abs((_bucket(leave_at) - self._held_for).total_seconds()) / 60
            return drift >= RESETTLE_MINUTES

    # --- internals ------------------------------------------------------------------

    def _view(self, estimate: TravelEstimate, leave_at: datetime) -> CommuteView:
        return CommuteView(
            arrival=arrival_at(
                leave_at,
                estimate,
                buffer=Duration(max(0, self._config.commute.buffer_minutes)),
            )
        )

    def _is_fresh(self, estimate: TravelEstimate, now: datetime) -> bool:
        if estimate.fetched_at is None:
            return False
        return (now - estimate.fetched_at).total_seconds() < MIN_REFRESH_SECONDS

    def _within_budget(self) -> bool:
        today = date.today()
        if self._spent_on != today:
            self._spent, self._spent_on = 0, today
        return self._spent < max(1, self._config.commute.max_calls_per_day)

    def _count(self) -> None:
        self._spent += 1

    def _tomtom(self) -> TomTomClient:
        if self._client is None:
            self._client = TomTomClient(self._api_key)
        return self._client

    def _origin(self) -> Place:
        commute = self._config.commute
        return Place(
            label=commute.origin,
            resolved=commute.origin,
            latitude=commute.origin_lat,
            longitude=commute.origin_lon,
        )

    def _destination(self) -> Place:
        commute = self._config.commute
        return Place(
            label=commute.destination,
            resolved=commute.destination,
            latitude=commute.destination_lat,
            longitude=commute.destination_lon,
        )
