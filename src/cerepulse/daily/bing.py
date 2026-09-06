"""Bing's picture of the day, from the same archive its home page reads.

``HPImageArchive.aspx?format=js&idx=0&n=1`` answers with the current image, its title and
the photographer's copyright line, without a key. The image URL is relative to
``https://www.bing.com``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx

from cerepulse.core.errors import DailyError

ARCHIVE_URL = "https://www.bing.com/HPImageArchive.aspx"
HOST = "https://www.bing.com"
TIMEOUT_SECONDS = 15.0


def fetch_today(
    *, day: date, into: Path, market: str = "en-IN", user_agent: str = "CerePulse"
) -> tuple[Path, str, str, str]:
    """Download today's image into ``into`` and return (path, title, copyright, link)."""
    headers = {"User-Agent": user_agent}
    try:
        response = httpx.get(
            ARCHIVE_URL,
            params={"format": "js", "idx": 0, "n": 1, "mkt": market},
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise DailyError(f"Picture of the day is unavailable: {exc}") from exc

    url, title, copyright, link = parse(payload)
    target = into / f"picture-{day:%Y%m%d}.jpg"
    try:
        image = httpx.get(url, timeout=TIMEOUT_SECONDS, follow_redirects=True, headers=headers)
        image.raise_for_status()
        into.mkdir(parents=True, exist_ok=True)
        target.write_bytes(image.content)
    except (httpx.HTTPError, OSError) as exc:
        raise DailyError(f"Picture of the day could not be saved: {exc}") from exc
    return target, title, copyright, link


def parse(payload: object) -> tuple[str, str, str, str]:
    """``images[0]`` carries ``url`` (relative), ``title``, ``copyright``, ``copyrightlink``."""
    images = payload.get("images") if isinstance(payload, dict) else None
    if not isinstance(images, list) or not images or not isinstance(images[0], dict):
        raise DailyError("Picture of the day came back in a shape nobody expected")
    first = images[0]
    url = str(first.get("url") or "")
    if not url:
        raise DailyError("Picture of the day carried no image")
    if url.startswith("/"):
        url = HOST + url
    return (
        url,
        str(first.get("title") or "").strip(),
        str(first.get("copyright") or "").strip(),
        str(first.get("copyrightlink") or "").strip(),
    )
