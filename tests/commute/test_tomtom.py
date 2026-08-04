"""The TomTom client, against recorded responses.

Tested the way the HTML parsers are: fixed payloads in, domain objects out, no network. The
response shapes here are TomTom's documented ones for `calculateRoute` and `geocode`.

The key check gets the most attention, because it is the one place where being wrong is
expensive in both directions. Reporting a good key as invalid because the wifi dropped sends
someone hunting a problem that does not exist; storing a bad key silently means the feature
fails at 6 PM on the evening they first relied on it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest

from cerepulse.commute.models import Place, TravelEstimate
from cerepulse.commute.tomtom import KeyVerdict, TomTomClient, TravelMode, without_key
from cerepulse.core.errors import CommuteError
from cerepulse.models.values import Duration

OFFICE = Place("GIFT City", "GIFT City, Gandhinagar", 23.1601, 72.6845)
HOME = Place("Home", "Satellite, Ahmedabad", 23.0225, 72.5714)

ROUTE = {
    "routes": [
        {
            "summary": {
                "lengthInMeters": 27400,
                "travelTimeInSeconds": 2580,  # 43 min
                "trafficDelayInSeconds": 1080,  # 18 min
            }
        }
    ]
}

GEOCODE = {
    "results": [
        {
            "address": {"freeformAddress": "Satellite, Ahmedabad, Gujarat"},
            "position": {"lat": 23.0225, "lon": 72.5714},
        }
    ]
}


def client_for(
    handler: Any, *, key: str = "test-key-0123456789"
) -> tuple[TomTomClient, list[httpx.Request]]:
    """A client wired to a stub transport, plus the requests it made."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request) if callable(handler) else handler

    transport = httpx.MockTransport(record)
    return TomTomClient(key, client=httpx.Client(transport=transport)), seen


def ok(payload: dict[str, Any]) -> Any:
    return lambda _request: httpx.Response(200, json=payload)


def status(code: int) -> Any:
    return lambda _request: httpx.Response(code, json={"error": "nope"})


# --- routing ----------------------------------------------------------------------------


def test_a_route_becomes_a_travel_estimate() -> None:
    client, _ = client_for(ok(ROUTE))
    estimate = client.route(OFFICE, HOME)

    assert estimate.duration == Duration(43)
    assert estimate.distance_m == 27400
    assert estimate.delay == Duration(18)


def test_seconds_round_to_whole_minutes() -> None:
    """The whole codebase works in whole minutes; this is the boundary that enforces it."""
    client, _ = client_for(
        ok({"routes": [{"summary": {"lengthInMeters": 1, "travelTimeInSeconds": 149}}]})
    )
    assert client.route(OFFICE, HOME).duration == Duration(2)


def test_traffic_is_always_requested() -> None:
    """A free-flow number presented as a real arrival time is a wrong answer that looks
    right."""
    client, seen = client_for(ok(ROUTE))
    client.route(OFFICE, HOME)

    assert seen[0].url.params["traffic"] == "true"


def test_the_travel_mode_reaches_the_request() -> None:
    """Routing a two-wheeler as a car gets both the roads and the traffic model wrong."""
    client, seen = client_for(ok(ROUTE))
    client.route(OFFICE, HOME, mode=TravelMode.MOTORCYCLE)

    assert seen[0].url.params["travelMode"] == "motorcycle"


def test_the_departure_time_is_sent_when_it_is_ahead() -> None:
    """The answer should describe the journey they are going to make, not the one they would
    make if they walked out mid-afternoon."""
    client, seen = client_for(ok(ROUTE))
    leaving = datetime.now() + timedelta(hours=2)
    client.route(OFFICE, HOME, depart_at=leaving)

    assert (
        seen[0]
        .url.params["departAt"]
        .startswith(leaving.replace(second=0, microsecond=0).isoformat()[:16])
    )


def test_a_departure_in_the_past_is_dropped_rather_than_sent() -> None:
    """TomTom rejects one outright, and losing the whole estimate over a stale prediction is
    a worse answer than falling back to live traffic."""
    client, seen = client_for(ok(ROUTE))
    client.route(OFFICE, HOME, depart_at=datetime.now() - timedelta(hours=1))

    assert "departAt" not in seen[0].url.params


def test_seconds_are_stripped_from_the_departure() -> None:
    """They add nothing, and they would defeat a caller snapping departures to a bucket."""
    client, seen = client_for(ok(ROUTE))
    client.route(OFFICE, HOME, depart_at=(datetime.now() + timedelta(hours=2)).replace(second=37))

    assert seen[0].url.params["departAt"].endswith(":00")


def test_no_route_is_an_error_not_a_zero() -> None:
    """A zero-minute commute is a plausible-looking number for an impossible journey."""
    client, _ = client_for(ok({"routes": []}))
    with pytest.raises(CommuteError, match="no route"):
        client.route(OFFICE, HOME)


def test_a_summary_missing_its_time_is_an_error() -> None:
    client, _ = client_for(ok({"routes": [{"summary": {"lengthInMeters": 100}}]}))
    with pytest.raises(CommuteError, match="travel time"):
        client.route(OFFICE, HOME)


# --- geocoding --------------------------------------------------------------------------


def test_geocoding_keeps_what_was_typed_and_what_was_found() -> None:
    """They disagree more often than anyone expects, and a house number that resolves to the
    next town produces a perfectly plausible travel time."""
    client, _ = client_for(ok(GEOCODE))
    place = client.geocode("satellite ahmedabad")

    assert place is not None
    assert place.label == "satellite ahmedabad"
    assert place.resolved == "Satellite, Ahmedabad, Gujarat"
    assert place.latitude == 23.0225


def test_geocoding_is_biased_to_india() -> None:
    """Without it "Sector 21" is a coin toss between three continents."""
    client, seen = client_for(ok(GEOCODE))
    client.geocode("Sector 21")

    assert seen[0].url.params["countrySet"] == "IN"


def test_an_address_that_matches_nothing_is_none_not_an_error() -> None:
    """An ordinary thing for a person to type. It wants a message under the field."""
    client, _ = client_for(ok({"results": []}))
    assert client.geocode("qqqqqqq") is None


def test_an_empty_address_never_reaches_the_network() -> None:
    client, seen = client_for(ok(GEOCODE))
    assert client.geocode("   ") is None
    assert seen == []


# --- the key check ----------------------------------------------------------------------


def test_a_working_key_is_valid() -> None:
    client, _ = client_for(ok(GEOCODE))
    check = client.validate()

    assert check.verdict is KeyVerdict.VALID
    assert check.is_usable


@pytest.mark.parametrize("code", [401, 403])
def test_a_rejected_key_is_not_stored(code: int) -> None:
    client, _ = client_for(status(code))
    check = client.validate()

    assert check.verdict is KeyVerdict.REJECTED
    assert not check.is_usable
    assert not check.is_uncertain


def test_a_throttled_key_still_counts_as_working() -> None:
    """It was accepted and then throttled — that is a working key having a busy minute."""
    client, _ = client_for(status(429))
    check = client.validate()

    assert check.verdict is KeyVerdict.RATE_LIMITED
    assert check.is_usable


def test_an_unreachable_provider_never_reports_the_key_as_wrong() -> None:
    """The distinction that matters. Refusing to store a good key because the wifi dropped
    is a failure people cannot diagnose from the message they are given."""

    def offline(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("getaddrinfo failed")

    client, _ = client_for(offline)
    check = client.validate()

    assert check.verdict is KeyVerdict.UNREACHABLE
    assert not check.is_usable
    assert check.is_uncertain


def test_an_empty_key_is_rejected_without_a_call() -> None:
    client, seen = client_for(ok(GEOCODE), key="")
    assert client.validate().verdict is KeyVerdict.REJECTED
    assert seen == []


def test_a_server_error_is_uncertain_rather_than_a_rejection() -> None:
    """TomTom having a bad day says nothing about the key."""
    client, _ = client_for(status(503))
    assert client.validate().verdict is KeyVerdict.UNREACHABLE


# --- the key must not leak --------------------------------------------------------------


def test_a_transport_failure_reports_no_url() -> None:
    """httpx exceptions carry the request URL, and the key rides in the query string."""

    def offline(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("getaddrinfo failed for api.tomtom.com?key=sup3rsecret")

    client, _ = client_for(offline, key="sup3rsecret")
    with pytest.raises(CommuteError) as caught:
        client.route(OFFICE, HOME)

    assert "sup3rsecret" not in str(caught.value)


def test_the_url_scrubber_removes_the_key() -> None:
    url = "https://api.tomtom.com/routing/1/calculateRoute/a:b/json?traffic=true&key=sup3rsecret"
    assert "sup3rsecret" not in without_key(url)
    assert "traffic=true" in without_key(url)


def test_the_log_sink_removes_the_key_too() -> None:
    """Belt and braces: anything that formats a URL by hand is covered by the sink."""
    from cerepulse.core.logging_setup import redact

    assert "sup3rsecret" not in redact("GET https://api.tomtom.com/x?key=sup3rsecret&limit=1")


def test_redaction_does_not_maul_words_ending_in_key() -> None:
    """Anchored on ? or & for exactly this reason — a bare substring would hit "monkey"."""
    from cerepulse.core.logging_setup import redact

    assert redact("the monkey=loud and turkey=quiet") == "the monkey=loud and turkey=quiet"


# --- travel modes -----------------------------------------------------------------------


def test_an_unknown_mode_falls_back_to_a_car() -> None:
    """A typo in a config file must not stop the app routing at all."""
    assert TravelMode.parse("hovercraft") is TravelMode.CAR
    assert TravelMode.parse("MOTORCYCLE") is TravelMode.MOTORCYCLE


# --- the estimate's own reporting -------------------------------------------------------


def test_free_flow_is_the_journey_without_the_queue() -> None:
    estimate = TravelEstimate(duration=Duration(43), distance_m=1, delay=Duration(18))
    assert estimate.free_flow == Duration(25)


def test_a_couple_of_minutes_of_delay_is_not_congestion() -> None:
    """Otherwise every journey is "congested" and the word stops meaning anything."""
    assert not TravelEstimate(Duration(43), 1, Duration(2)).is_congested
    assert TravelEstimate(Duration(43), 1, Duration(18)).is_congested


def test_an_unset_place_is_not_treated_as_null_island() -> None:
    """(0, 0) is in the Atlantic. Routing to it would answer a question nobody asked."""
    assert not Place("", "", 0.0, 0.0).is_located
    assert HOME.is_located
