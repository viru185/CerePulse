"""One fetch per calendar day per source, cached on disk, offline shows yesterday's.

The two endpoints are free and keyless, and the discipline is the same as the commute's:
never more than the question needs. A quote is asked for once a day; a picture is downloaded
once a day and the previous day's file is removed. A failed fetch serves whatever is cached,
however old — a stale quote is a quote, and a blank tile is what looks broken.

Never touched from the GUI thread: it reads and writes files and talks to the network.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from loguru import logger

from cerepulse import __about__ as about
from cerepulse.core.config import AppConfig
from cerepulse.core.errors import DailyError
from cerepulse.daily import bing, zenquotes
from cerepulse.daily.models import Picture, Quote

QuoteFetcher = Callable[[date], Quote]
PictureFetcher = Callable[[date, Path], tuple[Path, str, str, str]]


@dataclass(frozen=True, slots=True)
class DailyView:
    quote: Quote | None
    picture: Picture | None

    @property
    def is_empty(self) -> bool:
        return self.quote is None and self.picture is None


class DailyService:
    def __init__(
        self,
        *,
        config: AppConfig,
        cache_dir: Path,
        fetch_quote: QuoteFetcher | None = None,
        fetch_picture: PictureFetcher | None = None,
    ) -> None:
        self._config = config
        self._dir = cache_dir / "daily"
        agent = f"{about.NAME}/{about.VERSION}"
        self._fetch_quote = fetch_quote or (
            lambda day: zenquotes.fetch_today(day=day, user_agent=agent)
        )
        self._fetch_picture = fetch_picture or (
            lambda day, into: bing.fetch_today(day=day, into=into, user_agent=agent)
        )

    def use_config(self, config: AppConfig) -> None:
        self._config = config

    def today(self, now: datetime) -> DailyView:
        day = now.date()
        quote = self._quote_for(day) if self._config.daily.quote else None
        picture = self._picture_for(day) if self._config.daily.picture else None
        return DailyView(quote=quote, picture=picture)

    # --- quote ----------------------------------------------------------------------------

    def _quote_for(self, day: date) -> Quote | None:
        cached = self._read("quote.json")
        if cached and cached.get("day") == day.isoformat():
            return _quote_from(cached)
        try:
            quote = self._fetch_quote(day)
        except DailyError as exc:
            logger.warning("{}", exc)
            return _quote_from(cached) if cached else None
        self._write(
            "quote.json",
            {"day": day.isoformat(), "text": quote.text, "author": quote.author},
        )
        return quote

    # --- picture --------------------------------------------------------------------------

    def _picture_for(self, day: date) -> Picture | None:
        cached = self._read("picture.json")
        if cached and cached.get("day") == day.isoformat():
            held = _picture_from(cached)
            if held is not None and held.path.exists():
                return held
        try:
            path, title, copyright, link = self._fetch_picture(day, self._dir)
        except DailyError as exc:
            logger.warning("{}", exc)
            held = _picture_from(cached) if cached else None
            return held if held is not None and held.path.exists() else None
        self._write(
            "picture.json",
            {
                "day": day.isoformat(),
                "path": str(path),
                "title": title,
                "copyright": copyright,
                "link": link,
            },
        )
        self._prune_pictures(keep=path)
        return Picture(path=path, title=title, copyright=copyright, copyright_url=link, day=day)

    def _prune_pictures(self, *, keep: Path) -> None:
        for old in self._dir.glob("picture-*.jpg"):
            if old != keep:
                try:
                    old.unlink()
                except OSError:  # pragma: no cover — a locked file is not worth a warning
                    pass

    # --- the cache ------------------------------------------------------------------------

    def _read(self, name: str) -> dict[str, object] | None:
        try:
            payload = json.loads((self._dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def _write(self, name: str, payload: dict[str, object]) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            (self._dir / name).write_text(json.dumps(payload), encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not cache {}: {}", name, exc)


def _quote_from(payload: dict[str, object]) -> Quote | None:
    text = str(payload.get("text") or "")
    if not text:
        return None
    try:
        day = date.fromisoformat(str(payload.get("day")))
    except ValueError:
        return None
    return Quote(text=text, author=str(payload.get("author") or ""), day=day)


def _picture_from(payload: dict[str, object]) -> Picture | None:
    path = str(payload.get("path") or "")
    if not path:
        return None
    try:
        day = date.fromisoformat(str(payload.get("day")))
    except ValueError:
        return None
    return Picture(
        path=Path(path),
        title=str(payload.get("title") or ""),
        copyright=str(payload.get("copyright") or ""),
        copyright_url=str(payload.get("link") or ""),
        day=day,
    )


__all__ = ["DailyService", "DailyView"]
