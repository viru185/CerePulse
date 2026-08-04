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
        """The single best match for an address, or ``None`` when nothing matched.

        Kept for the key check and any caller that genuinely wants one answer;
        :meth:`search` is what the Settings flow uses, because "the best match" is a guess
        and a guess must be the user's to confirm, not the app's to make.
        """
        found = self.search(query, country=country, limit=1)
        return found[0] if found else None

    def search(self, query: str, *, country: str = DEFAULT_COUNTRY, limit: int = 5) -> list[Place]:
        """The top matches for a typed address or building name, best first.

        TomTom's *fuzzy search*, not its geocoder. Geocoding is built for street addresses
        and largely ignores building and complex names — which is how most Indian addresses
        are actually identified, so "The Elixir, PDPU Road, Raysan" fell back to the road:
        close enough to look right, and not what was asked for. Fuzzy search resolves
        points of interest and addresses through one call.

        A list rather than a single answer, because several places can legitimately match
        and the person typing is the only one who knows which they meant. Empty means
        nothing matched — an ordinary thing to happen to a typo, wanting a message under
        the field rather than a stack trace.
        """
        text = query.strip()
        if not text:
            return []
        payload = self._get(
            f"/search/2/search/{quote(text)}.json",
            {"limit": str(limit), "countrySet": country},
        )
        places = []
        for result in payload.get("results") or []:
            place = _to_place(result, label=text)
            if place is not None:
                places.append(place)
        return places

    def locate(self, latitude: float, longitude: float) -> Place:
        """A point the user pinned themselves, named by whatever stands on it.

        This never fails: the coordinates are already the answer, and the reverse lookup
        only supplies a human-readable name for the confirmation line. Refusing a pin
        because the *naming* call was unreachable would fail the one input that cannot be
        wrong.
        """
        label = f"{latitude:.6f}, {longitude:.6f}"
        resolved = ""
        try:
            payload = self._get(
                f"/search/2/reverseGeocode/{latitude},{longitude}.json", {"limit": "1"}
            )
            addresses = payload.get("addresses") or []
            if addresses:
                resolved = str((addresses[0].get("address") or {}).get("freeformAddress") or "")
        except CommuteError:
            logger.debug("Reverse geocode unavailable; keeping the bare coordinates")
        return Place(
            label=label,
            resolved=resolved or f"the pinned point {label}",
            latitude=latitude,
            longitude=longitude,
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


def _to_place(result: dict[str, Any], *, label: str) -> Place | None:
    position = result.get("position") or {}
    latitude, longitude = position.get("lat"), position.get("lon")
    if latitude is None or longitude is None:
        return None
    # A point of interest carries its name separately from its address; an address result
    # has only the address. The name is what the person searched by, so it leads.
    poi = (result.get("poi") or {}).get("name") or ""
    address = (result.get("address") or {}).get("freeformAddress") or ""
    resolved = f"{poi} — {address}" if poi and address else (poi or address or label)
    return Place(
        label=label,
        resolved=str(resolved),
        latitude=float(latitude),
        longitude=float(longitude),
    )


def expand_short_link(url: str, *, timeout: float = TIMEOUT_SECONDS) -> str | None:
    """Follow a ``maps.app.goo.gl`` redirect and return the full URL it points to.

    The one place this feature talks to Google, and it is a header-only conversation: the
    request asks for the redirect target and never downloads the page. No key, no cookies,
    nothing identifying beyond an HTTP request. ``None`` when the link cannot be expanded —
    offline, expired, or not actually a redirect — and the caller says so in terms of the
    link rather than the address.
    """
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            for _hop in range(5):
                response = client.head(url)
                target = response.headers.get("location")
                if target is None:
                    # Some shorteners answer HEAD with the page itself; a GET's final URL
                    # is then the answer. follow_redirects on one request keeps it bounded.
                    if response.status_code == httpx.codes.OK:
                        followed = client.get(url, follow_redirects=True)
                        return str(followed.url)
                    return None
                if not target.startswith("http"):
                    return None
                url = target
                if "google" in url and "/maps" in url:
                    return url
            return url
    except httpx.HTTPError:
        return None


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
