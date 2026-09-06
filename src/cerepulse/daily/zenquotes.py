"""ZenQuotes' quote of the day: ``GET https://zenquotes.io/api/today``.

Free, keyless, five calls per thirty seconds — one a day is well inside that — and the
free tier asks for a credit line with a link, which :class:`Quote` carries.
"""

from __future__ import annotations

from datetime import date

import httpx

from cerepulse.core.errors import DailyError
from cerepulse.daily.models import Quote

URL = "https://zenquotes.io/api/today"
TIMEOUT_SECONDS = 10.0


def fetch_today(*, day: date, user_agent: str = "CerePulse") -> Quote:
    try:
        response = httpx.get(
            URL, timeout=TIMEOUT_SECONDS, follow_redirects=True, headers={"User-Agent": user_agent}
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise DailyError(f"Quote of the day is unavailable: {exc}") from exc
    return parse(payload, day=day)


def parse(payload: object, *, day: date) -> Quote:
    """The endpoint returns a one-element list of ``{"q": text, "a": author, "h": html}``."""
    if isinstance(payload, list) and payload:
        payload = payload[0]
    if not isinstance(payload, dict):
        raise DailyError("Quote of the day came back in a shape nobody expected")
    text = str(payload.get("q") or "").strip()
    author = str(payload.get("a") or "").strip()
    if not text:
        raise DailyError("Quote of the day came back empty")
    return Quote(text=text, author=author, day=day)
