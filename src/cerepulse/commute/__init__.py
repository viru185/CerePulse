"""Travel time between the office and home.

A second network integration, kept out of ``transport/`` on purpose: that package is the
SpineHR WebForms client — cookies, ``__VIEWSTATE``, menu privilege tokens — and none of it
applies to a JSON maps API. Nothing here knows about attendance, and nothing in attendance
depends on this: a provider outage costs one card on Today and nothing else.
"""

from cerepulse.commute.locations import coordinates_in, describe_paste, is_short_link
from cerepulse.commute.models import Place, TravelEstimate
from cerepulse.commute.tomtom import (
    KeyCheck,
    KeyVerdict,
    TomTomClient,
    TravelMode,
    expand_short_link,
)

__all__ = [
    "KeyCheck",
    "KeyVerdict",
    "Place",
    "TomTomClient",
    "TravelEstimate",
    "TravelMode",
    "coordinates_in",
    "describe_paste",
    "expand_short_link",
    "is_short_link",
]
