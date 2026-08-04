"""Reading a point out of whatever Google Maps put on the clipboard.

Pure text in, coordinates out — no network, no provider, no Qt. It exists because "find my
house by typing its address" is the weakest link in the whole feature: a search returns *its
best guess*, and a wrong guess still produces a perfectly believable travel time. A point
the user pinned themselves has nothing to guess, which is why every form Google hands out is
worth accepting.

The forms, and where each comes from:

``23.157234, 72.664512``
    Right-click a spot on the desktop map; the top menu item *is* the coordinates and
    clicking copies them.

``https://www.google.com/maps/place/The+Elixir/@23.15,72.66,17z/…!3d23.157!4d72.664``
    The address bar after a search. Two pairs live in that URL and they are **not the same
    point**: ``@`` is where the map camera sits, drifting as you scroll and zoom, while
    ``!3d``/``!4d`` is the place itself. The place wins wherever both appear — otherwise a
    pin quietly becomes "wherever I happened to be looking".

``23°09'26.0"N 72°39'52.2"E``
    Degrees-minutes-seconds, still shown on some Google surfaces.

``7JMJ5M2R+2M`` / ``5M2R+2M Gandhinagar``
    Plus Codes, which Google pushes hard on Indian place cards. A full code decodes with no
    network at all. A short one is only meaningful near a reference point and is recovered
    against the configured office — exact while home and office share a metro, and able to
    pick the wrong grid cell beyond roughly 40 km. That is the one boundary in this module.

``maps.app.goo.gl/…``
    The phone's Share button. The coordinates are hidden behind a redirect, so this module
    only *recognises* it; following the redirect is a network call and belongs to the
    TomTom/HTTP layer, not to a parser.

Decoding Plus Codes is delegated to Google's own ``openlocationcode`` package rather than a
hand-rolled grid: this is the accuracy-critical path, and a subtly wrong refinement step
would land a few streets away while looking entirely correct.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

from openlocationcode import openlocationcode as _olc

#: The place a Google Maps URL is about. Preferred over the camera position.
_PLACE_PIN = re.compile(r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)")

#: Where the map camera is pointing — a fallback, never the first choice.
_CAMERA = re.compile(r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)")

#: The query-parameter forms: ``?q=``, ``?query=``, ``?ll=``, ``&daddr=``, ``&destination=``.
_QUERY = re.compile(
    r"[?&](?:q|query|ll|daddr|destination)=(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

#: A bare pair, which is what the right-click copy gives. Anchored to the whole string so a
#: house number and a postcode inside an ordinary address can never be read as a location.
_BARE = re.compile(r"^\(?\s*(-?\d{1,3}(?:\.\d+)?)\s*[,;]\s*(-?\d{1,3}(?:\.\d+)?)\s*\)?$")

#: One degrees-minutes-seconds component, e.g. ``23°09'26.0"N``. Seconds optional; some
#: sources stop at minutes. Accepts the typographic quote variants Google itself uses.
_DMS = re.compile(
    r"(\d{1,3})\s*°\s*(\d{1,2})\s*['′’]\s*(?:(\d{1,2}(?:\.\d+)?)\s*[\"″”]\s*)?([NSEW])",
    re.IGNORECASE,
)

#: A Plus Code anywhere in the text: four-plus grid characters, ``+``, then the refinement.
#: The alphabet is Google's own 20 letters, which is what keeps ordinary words from matching.
_PLUS_CODE = re.compile(r"\b([23456789CFGHJMPQRVWX]{4,8}\+[23456789CFGHJMPQRVWX]{2,3})\b")

#: A shortened link, which hides its coordinates behind a redirect.
_SHORT_LINK = re.compile(r"(maps\.app\.goo\.gl|goo\.gl/maps)", re.IGNORECASE)


def coordinates_in(
    text: str, *, near: tuple[float, float] | None = None
) -> tuple[float, float] | None:
    """Pull a latitude/longitude out of pasted text, or ``None`` when there is none.

    ``None`` means "this is an address — go and search for it", not an error: the same field
    accepts both kinds of input and the caller decides which path a paste is on.

    ``near`` is the reference for recovering a *short* Plus Code. Without one, short codes
    return ``None`` rather than being recovered against an arbitrary point — a guess about
    the reference is a guess about the answer, which is what this module exists to end.
    """
    raw = text.strip()
    if not raw:
        return None

    # Percent-encoding first: a shared URL writes its comma as %2C, and every pattern below
    # expects the real character.
    decoded = unquote(raw)

    # URL forms before the bare pair, most-authoritative first.
    for pattern in (_PLACE_PIN, _QUERY, _CAMERA):
        found = pattern.search(decoded)
        if found is not None:
            point = _valid(found.group(1), found.group(2))
            if point is not None:
                return point

    bare = _BARE.match(decoded)
    if bare is not None:
        return _valid(bare.group(1), bare.group(2))

    dms = _from_dms(decoded)
    if dms is not None:
        return dms

    return _from_plus_code(decoded, near=near)


def is_short_link(text: str) -> bool:
    """Whether this is a shortened Google link whose coordinates live behind a redirect."""
    return bool(_SHORT_LINK.search(text))


def looks_like_maps_url(text: str) -> bool:
    """A Google Maps URL that *should* have carried a point but did not.

    Worth telling apart from an address: searching TomTom for a pasted URL would "work" and
    return something, which is precisely the silent wrong answer this field must not give.
    """
    lowered = text.strip().lower()
    return lowered.startswith(("http://", "https://")) and "google" in lowered


def describe_paste(text: str) -> str:
    """What to tell someone whose paste could not be read as a point.

    Short links get their own sentence — "nothing matched" would send somebody off
    correcting an address that was never the problem.
    """
    if is_short_link(text):
        return (
            "That is a shortened Google link, which hides its coordinates. Open it in a "
            "browser and copy the full address bar — or right-click the place on the map "
            "and click the numbers at the top of the menu."
        )
    if looks_like_maps_url(text):
        return (
            "That Google Maps link carries no coordinates. Open the place on the map, then "
            "copy the address bar once the URL shows an @ followed by numbers."
        )
    return (
        "Nothing matched that. Paste coordinates (right-click the spot in Google Maps and "
        "click the numbers at the top), a full Google Maps link, a Plus Code, or an address."
    )


def _from_dms(text: str) -> tuple[float, float] | None:
    """``23°09'26.0"N 72°39'52.2"E`` — two components, hemispheres in either order."""
    parts = _DMS.findall(text)
    if len(parts) < 2:
        return None

    values: dict[str, float] = {}
    for degrees, minutes, seconds, hemisphere in parts[:2]:
        decimal = int(degrees) + int(minutes) / 60 + (float(seconds) if seconds else 0.0) / 3600
        letter = hemisphere.upper()
        if letter in ("S", "W"):
            decimal = -decimal
        values["lat" if letter in ("N", "S") else "lon"] = decimal

    if "lat" not in values or "lon" not in values:
        return None
    return _valid(values["lat"], values["lon"])


def _from_plus_code(text: str, *, near: tuple[float, float] | None) -> tuple[float, float] | None:
    found = _PLUS_CODE.search(text.upper())
    if found is None:
        return None
    code = found.group(1)

    if _olc.isFull(code):
        decoded = _olc.decode(code)
        return _valid(decoded.latitudeCenter, decoded.longitudeCenter)

    if _olc.isShort(code) and near is not None:
        # Recovery picks the matching grid cell nearest the reference — exact while the two
        # are in the same metro, wrong past ~40 km. The caller passes the office as `near`,
        # which holds for the commutes this app describes; the README states the boundary.
        recovered = _olc.recoverNearest(code, near[0], near[1])
        decoded = _olc.decode(recovered)
        return _valid(decoded.latitudeCenter, decoded.longitudeCenter)

    return None


def _valid(latitude: object, longitude: object) -> tuple[float, float] | None:
    """Range-check a pair, and refuse Null Island.

    (0, 0) is both a real point in the Atlantic and the value an unset field parses to, so
    accepting it would turn a blank setting into a destination.
    """
    try:
        lat, lon = float(latitude), float(longitude)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    if lat == 0.0 and lon == 0.0:
        return None
    return lat, lon


__all__ = ["coordinates_in", "describe_paste", "is_short_link", "looks_like_maps_url"]
