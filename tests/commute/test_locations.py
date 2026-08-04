"""Every way Google Maps hands out a point, and the ways it must not be misread.

This module is where the commute's accuracy is actually defended. A pasted pin is the one
input with nothing to resolve, so the parsing of it has to be exact — and the failure that
matters most is not "did not parse" but "parsed to somewhere else while looking right".
"""

from __future__ import annotations

from cerepulse.commute.locations import (
    coordinates_in,
    describe_paste,
    is_short_link,
    looks_like_maps_url,
)

#: One real place — the approximate point used in these fixtures.
LAT, LON = 23.157234, 72.664512

#: The office, as the reference for short Plus Codes.
OFFICE = (23.1601, 72.6845)


def close(point: tuple[float, float] | None, lat: float, lon: float, *, digits: int = 3) -> bool:
    assert point is not None
    return round(point[0] - lat, digits) == 0 and round(point[1] - lon, digits) == 0


# --- bare coordinates -------------------------------------------------------------------


def test_the_right_click_copy_parses() -> None:
    assert coordinates_in("23.157234, 72.664512") == (LAT, LON)


def test_parentheses_and_semicolons_are_tolerated() -> None:
    assert coordinates_in("(23.157234; 72.664512)") == (LAT, LON)


def test_negative_hemispheres_survive() -> None:
    assert coordinates_in("-33.8688, 151.2093") == (-33.8688, 151.2093)


# --- what must NOT read as coordinates --------------------------------------------------


def test_a_house_number_and_postcode_are_not_a_location() -> None:
    """The most dangerous misread in the module: an ordinary address that parses to a
    plausible point routes somebody to the wrong continent while looking exact."""
    assert coordinates_in("101 Sun Avenue, Bopal, 380058") is None
    assert coordinates_in("The Elixir, PDPU Rd, Gandhinagar, Gujarat 382426") is None


def test_null_island_is_refused() -> None:
    """(0, 0) is both a real point in the Atlantic and what an unset field parses to."""
    assert coordinates_in("0, 0") is None
    assert coordinates_in("0.0, 0.0") is None


def test_out_of_range_values_are_refused() -> None:
    assert coordinates_in("123.5, 72.6") is None  # latitude beyond 90
    assert coordinates_in("23.1, 231.0") is None  # longitude beyond 180


def test_empty_text_is_none() -> None:
    assert coordinates_in("") is None
    assert coordinates_in("   ") is None


# --- the full Maps URL ------------------------------------------------------------------

URL_BOTH = (
    "https://www.google.com/maps/place/The+Elixir/@23.9999,72.9999,17z/"
    "data=!3m1!4b1!4m6!3m5!1s0x0:0x0!8m2!3d23.157234!4d72.664512"
)


def test_the_place_pin_wins_over_the_camera() -> None:
    """The @ pair is where the map camera sits and drifts as you scroll; !3d/!4d is the
    place. Taking the camera turns a pin into "wherever I happened to be looking"."""
    assert coordinates_in(URL_BOTH) == (LAT, LON)


def test_a_url_with_only_a_camera_still_yields_its_point() -> None:
    url = "https://www.google.com/maps/@23.157234,72.664512,17z"
    assert coordinates_in(url) == (LAT, LON)


def test_the_query_parameter_forms_parse() -> None:
    assert coordinates_in("https://maps.google.com/?q=23.157234,72.664512") == (LAT, LON)
    assert coordinates_in(
        "https://www.google.com/maps/dir/?api=1&destination=23.157234,72.664512"
    ) == (LAT, LON)


def test_percent_encoded_commas_are_decoded_first() -> None:
    """A shared link writes its comma as %2C."""
    assert coordinates_in("https://maps.google.com/?q=23.157234%2C72.664512") == (LAT, LON)


def test_a_maps_url_with_no_point_is_not_searched_as_an_address() -> None:
    """Searching TomTom for a URL would return something, which is exactly the silent wrong
    answer this field exists to end."""
    url = "https://www.google.com/maps/place/The+Elixir"
    assert coordinates_in(url) is None
    assert looks_like_maps_url(url)
    assert "no coordinates" in describe_paste(url)


# --- degrees-minutes-seconds ------------------------------------------------------------


def test_dms_parses() -> None:
    assert close(coordinates_in("23°09'26.0\"N 72°39'52.2\"E"), 23.157222, 72.664500)


def test_dms_hemispheres_in_either_order() -> None:
    assert close(coordinates_in("72°39'52.2\"E 23°09'26.0\"N"), 23.157222, 72.664500)


def test_southern_and_western_hemispheres_go_negative() -> None:
    assert close(coordinates_in("33°52'08\"S 151°12'33\"E"), -33.868889, 151.209167)


def test_typographic_quotes_are_accepted() -> None:
    """Google itself renders the typographic variants."""
    assert close(coordinates_in("23°09′26.0″N 72°39′52.2″E"), 23.157222, 72.664500)


def test_two_components_of_the_same_axis_are_not_a_point() -> None:
    assert coordinates_in("23°09'26.0\"N 24°10'00.0\"N") is None


# --- Plus Codes -------------------------------------------------------------------------


def test_a_full_plus_code_decodes_with_no_reference() -> None:
    point = coordinates_in("7JMJ5M2R+2M")
    assert close(point, 23.1501, 72.6917)


def test_a_short_code_recovers_against_the_office() -> None:
    point = coordinates_in("5M2R+2M Gandhinagar", near=OFFICE)
    assert close(point, 23.1501, 72.6917)


def test_a_short_code_with_no_reference_is_none_not_a_guess() -> None:
    """Recovering against an arbitrary point is a guess about the reference, which is a
    guess about the answer."""
    assert coordinates_in("5M2R+2M Gandhinagar") is None


def test_ordinary_words_do_not_read_as_plus_codes() -> None:
    """The code alphabet excludes vowels for exactly this reason; the boundary must too."""
    assert coordinates_in("Meet at the cafe, 3rd floor") is None


# --- short links ------------------------------------------------------------------------


def test_short_links_are_recognised_not_parsed() -> None:
    link = "https://maps.app.goo.gl/AbCdEf123"
    assert coordinates_in(link) is None
    assert is_short_link(link)


def test_the_short_link_message_blames_the_link_not_the_address() -> None:
    message = describe_paste("https://maps.app.goo.gl/AbCdEf123")
    assert "shortened" in message
    assert "address bar" in message


def test_the_generic_message_lists_what_would_work() -> None:
    message = describe_paste("gibberish text")
    assert "coordinates" in message
    assert "Plus Code" in message
