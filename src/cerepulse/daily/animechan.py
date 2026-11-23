"""Anime quotes from Animechan: ``GET https://api.animechan.io/v1/quotes/random``.

Keyless, and the free tier allows a hundred requests a day per address — the tile asks once
a day. The endpoint is random rather than "of the day", so it is the once-a-day cache that
makes it one; switching away and back the same day shows the same quote.

Checked live on 24 November 2026:
``{"status": "success", "data": {"content": "...", "anime": {"id", "name", "altName"},
"character": {"id", "name"}}}``. The older animechan hosts answered a flat
``{"anime", "character", "quote"}``, and tutorials still show it, so that shape is read too.
"""

from __future__ import annotations

from datetime import date

import httpx

from cerepulse.core.errors import DailyError
from cerepulse.daily.models import Quote

URL = "https://api.animechan.io/v1/quotes/random"
TIMEOUT_SECONDS = 10.0
CREDIT = "Anime quotes provided by Animechan"
CREDIT_URL = "https://animechan.io/"


def fetch_today(*, day: date, user_agent: str = "CerePulse") -> Quote:
    try:
        response = httpx.get(
            URL, timeout=TIMEOUT_SECONDS, follow_redirects=True, headers={"User-Agent": user_agent}
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise DailyError(f"Anime quote of the day is unavailable: {exc}") from exc
    return parse(payload, day=day)


def parse(payload: object, *, day: date) -> Quote:
    if not isinstance(payload, dict):
        raise DailyError("Anime quote came back in a shape nobody expected")
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise DailyError("Anime quote came back in a shape nobody expected")

    text = str(data.get("content") or data.get("quote") or "").strip()
    character = _name(data.get("character"))
    anime = _name(data.get("anime"))
    if not text:
        raise DailyError("Anime quote came back empty")
    return Quote(
        text=text,
        author=character,
        day=day,
        credit=CREDIT,
        credit_url=CREDIT_URL,
        work=anime,
        source="anime",
    )


def _name(value: object) -> str:
    """``{"name": "Wasuke Itadori"}`` in v1, a bare string in the old shape."""
    if isinstance(value, dict):
        value = value.get("name")
    return str(value or "").strip()
