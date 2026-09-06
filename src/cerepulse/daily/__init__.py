"""A quote and a picture for the day, from two keyless public endpoints.

Beside ``commute/`` rather than inside ``transport/`` for the same reason: ``transport/`` is
the SpineHR WebForms client, and none of that applies to a JSON endpoint. Nothing in
attendance depends on either of these, so an outage costs one sidebar tile.

No key ships in the build — that is a rule — which is why these two providers and not
others: ZenQuotes' free tier needs only a credit line, and Bing's daily image is served
without one. NASA's picture of the day wants ``DEMO_KEY`` embedded and is out.
"""

from cerepulse.daily.models import Picture, Quote

__all__ = ["Picture", "Quote"]
