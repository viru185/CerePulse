"""The TomTom client: geocoding, traffic-aware routing, and checking a key works.

Why TomTom, and why the user brings their own key: its free tier is 20,000 routing requests
a month with live traffic and no credit card, and one key covers geocoding too — but a key
shipped inside the build would be a published key. CerePulse's releases are public and a
PyInstaller folder is a zip of bytecode, so extracting a string from it is a minute's work,
and TomTom's terms require keys stay confidential. Obfuscation changes how long that takes,
not whether it happens.

The key travels as a query parameter because that is what every TomTom endpoint accepts.
That would put it in clear text in any logged URL, so :func:`cerepulse.core.logging_setup.
redact` strips ``?key=`` and the errors raised here carry a redacted URL rather than the
real one. Nothing in this module may log or raise a string built from ``self._key``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any
from urllib.parse import quote

import httpx
from loguru import logger

from cerepulse.commute.models import Place, TravelEstimate
from cerepulse.core.errors import CommuteError
from cerepulse.models.values import Duration

BASE = "https://api.tomtom.com"

#: Somewhere unambiguous to point a key check at. Any successful response proves the key,
#: so this is about spending the cheapest possible call, not about the answer.
_PROBE = "Gandhinagar, Gujarat, India"

#: Bias geocoding to India. Without it "Sector 21" is a coin toss between three continents,
#: and the resulting route is wrong in a way that still looks like a number.
DEFAULT_COUNTRY = "IN"

TIMEOUT_SECONDS = 12.0


class TravelMode(Enum):
    """How the journey is made. These are TomTom's own tokens, not a private vocabulary.

    ``motorcycle`` and ``bicycle`` matter here rather than being completeness: the commute
    this was built for is a two-wheeler one, and routing it as a car gets both the roads and
    the traffic model wrong.
    """

    CAR = "car"
    MOTORCYCLE = "motorcycle"
    BUS = "bus"
    BICYCLE = "bicycle"
    PEDESTRIAN = "pedestrian"

    @property
    def label(self) -> str:
        return {
            TravelMode.CAR: "Car",
            TravelMode.MOTORCYCLE: "Motorcycle",
            TravelMode.BUS: "Bus",
            TravelMode.BICYCLE: "Bicycle",
            TravelMode.PEDESTRIAN: "Walking",
        }[self]

    @classmethod
    def parse(cls, value: str) -> TravelMode:
        """A typo resolves to a car rather than refusing to route at all."""
        try:
            return cls(value.strip().lower())
        except ValueError:
            logger.warning("Unknown travel mode {!r}; routing as a car", value)
            return cls.CAR


class KeyVerdict(Enum):
    """What a key check actually established."""

    VALID = "valid"
    REJECTED = "rejected"
    RATE_LIMITED = "rate_limited"
    #: TomTom could not be reached. Says nothing about the key either way, and must not be
    #: reported as a rejection — refusing to store a good key because the wifi dropped is a
    #: failure people cannot diagnose.
    UNREACHABLE = "unreachable"


@dataclass(frozen=True, slots=True)
class KeyCheck:
    verdict: KeyVerdict
    message: str

    @property
    def is_usable(self) -> bool:
        """Proven to work. Rate-limited counts: the key was accepted, then throttled."""
        return self.verdict in (KeyVerdict.VALID, KeyVerdict.RATE_LIMITED)

    @property
    def is_uncertain(self) -> bool:
        """Nothing was established, so the caller should offer to store it anyway."""
        return self.verdict is KeyVerdict.UNREACHABLE


class TomTomClient:
    """Thin, synchronous, and always called from a worker thread — never the GUI thread."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
    ) -> None:
        self._key = api_key.strip()
        self._timeout = timeout
        self._client = client

    # --- public ---------------------------------------------------------------------

    def validate(self) -> KeyCheck:
        """Spend one cheap call to find out whether this key works.

        Checked when the key is pasted rather than when it is first needed, because a key
        that fails at 6 PM on the one evening somebody relied on it is worse than useless.

        Four outcomes, not two. Telling "this key is wrong" apart from "there is no internet"
        is the whole point: the first must not be stored, the second must not block storing a
        perfectly good key.
        """
        if not self._key:
            return KeyCheck(KeyVerdict.REJECTED, "No key entered.")
        try:
            self._get(
                f"/search/2/geocode/{quote(_PROBE)}.json",
                {"limit": "1", "countrySet": DEFAULT_COUNTRY},
            )
        except _HttpStatus as exc:
            if exc.status in (401, 403):
                return KeyCheck(
                    KeyVerdict.REJECTED,
                    "TomTom rejected that key. Check it was copied whole, and that the "
                    "key's product includes Search and Routing.",
                )
            if exc.status == 429:
                return KeyCheck(
                    KeyVerdict.RATE_LIMITED,
                    "The key works, but TomTom is rate-limiting it right now.",
                )
            return KeyCheck(KeyVerdict.UNREACHABLE, f"TomTom answered with HTTP {exc.status}.")
        except CommuteError as exc:
            return KeyCheck(KeyVerdict.UNREACHABLE, f"Could not reach TomTom: {exc}")
        return KeyCheck(KeyVerdict.VALID, "Key works. Traffic-aware arrival times are on.")

    def geocode(self, query: str, *, country: str = DEFAULT_COUNTRY) -> Place | None:
        """Resolve an address to a point, or ``None`` when nothing matched.

        ``None`` rather than an exception: a typed address that finds nothing is an ordinary
        thing for a person to do, and it wants a message under the field, not a stack trace.
        """
        text = query.strip()
        if not text:
            return None
        payload = self._get(
            f"/search/2/geocode/{quote(text)}.json",
            {"limit": "1", "countrySet": country},
        )
        results = payload.get("results") or []
        if not results:
            return None

        first = results[0]
        position = first.get("position") or {}
        latitude, longitude = position.get("lat"), position.get("lon")
        if latitude is None or longitude is None:
            return None
        address = (first.get("address") or {}).get("freeformAddress") or text
        return Place(
            label=text,
            resolved=str(address),
            latitude=float(latitude),
            longitude=float(longitude),
        )

    def route(
        self,
        origin: Place,
        destination: Place,
        *,
        mode: TravelMode = TravelMode.CAR,
        depart_at: datetime | None = None,
    ) -> TravelEstimate:
        """How long the journey takes, leaving at ``depart_at``.

        The departure time is the predicted *exit* time rather than now, so the answer
        describes the journey the user is going to make rather than the one they would make
        if they walked out mid-afternoon. TomTom rejects a departure in the past, so a stale
        one falls back to live traffic instead of failing the whole estimate.
        """
        params: dict[str, str] = {"traffic": "true", "travelMode": mode.value}
        if depart_at is not None and depart_at > datetime.now():
            # Seconds and microseconds dropped: they add nothing and would defeat any
            # caller that snaps departures to a bucket to avoid re-asking.
            params["departAt"] = depart_at.replace(second=0, microsecond=0).isoformat()

        payload = self._get(
            f"/routing/1/calculateRoute/{origin.coordinates}:{destination.coordinates}/json",
            params,
        )
        routes = payload.get("routes") or []
        if not routes:
            raise CommuteError("TomTom found no route between those two places.")

        summary = routes[0].get("summary") or {}
        seconds = summary.get("travelTimeInSeconds")
        metres = summary.get("lengthInMeters")
        if seconds is None or metres is None:
            raise CommuteError("TomTom's route was missing its travel time.")

        return TravelEstimate(
            duration=Duration(_to_minutes(seconds)),
            distance_m=int(metres),
            delay=Duration(_to_minutes(summary.get("trafficDelayInSeconds") or 0)),
            fetched_at=datetime.now(),
        )

    # --- plumbing -------------------------------------------------------------------

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        url = f"{BASE}{path}"
        query = {**params, "key": self._key}
        try:
            if self._client is not None:
                response = self._client.get(url, params=query, timeout=self._timeout)
            else:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.get(url, params=query, timeout=self._timeout)
        except httpx.HTTPError as exc:
            # `exc` can carry the request URL, key and all. Only the type is reported.
            raise CommuteError(f"{type(exc).__name__} contacting TomTom") from None

        if response.status_code != httpx.codes.OK:
            raise _HttpStatus(response.status_code)
        try:
            payload = response.json()
        except ValueError as exc:
            raise CommuteError("TomTom returned something that was not JSON") from exc
        if not isinstance(payload, dict):
            raise CommuteError("TomTom returned an unexpected response shape")
        return payload


class _HttpStatus(CommuteError):
    """A non-200 from TomTom, carrying the code so ``validate`` can read it.

    A ``CommuteError`` so every caller that does not care about the distinction — which is
    all of them except the key check — handles it without knowing this type exists.
    """

    def __init__(self, status: int) -> None:
        super().__init__(f"TomTom answered with HTTP {status}")
        self.status = status


def _to_minutes(seconds: float) -> int:
    """Round to the nearest minute. The app works in whole minutes throughout."""
    return int(round(float(seconds) / 60))


#: Belt and braces for anything that formats a URL by hand. The sink redacts as well.
_KEY_IN_URL = re.compile(r"([?&]key=)[^&\s]+", re.IGNORECASE)


def without_key(text: str) -> str:
    return _KEY_IN_URL.sub(r"\1<redacted>", text)
