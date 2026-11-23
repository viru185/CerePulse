"""A quote and a picture for the day: fetched when the provider's day turns, retried when not.

The two endpoints are free and keyless, and the discipline is the same as the commute's:
never more than the question needs. Each source is asked once per *its own* day — ZenQuotes
changes its quote at 00:00 UTC, the anime quote is random so the local day decides, and Bing
dates its image — and the answer is cached on disk.

What changed in 0.17, and why the tile looked frozen before it:

- **The day was the wrong day.** The cache was keyed by the local date, and the app refreshes
  at local midnight, which is 18:30 UTC the day before in India. ZenQuotes was still serving
  yesterday's quote, and it was stored as today's: the same quote two days running.
- **A failure was final until tomorrow.** Fetched in the seconds after waking, before the
  Wi-Fi was back, both requests failed and nothing asked again until the next midnight. Now a
  failure is retried after 5, 15 and then every 60 minutes, and a wake or the network coming
  back asks at once (the window does that).
- **The switches did nothing until a restart.** The window now asks again whenever Settings
  are saved, and the tile shows or hides there and then.

Never touched from the GUI thread: it reads and writes files and talks to the network.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from loguru import logger

from cerepulse import __about__ as about
from cerepulse.core.config import AppConfig
from cerepulse.core.errors import DailyError
from cerepulse.daily import animechan, bing, zenquotes
from cerepulse.daily.models import Picture, Quote

QuoteFetcher = Callable[[date], Quote]
#: (path, title, copyright, link) — or with the provider's own date as a fifth element.
PictureFetcher = Callable[[date, Path], tuple[object, ...]]

#: Quote sources the Settings switch offers. "alternate" takes turns by date.
QUOTE_SOURCES = ("anime", "zenquotes", "alternate")
#: Minutes to wait before asking again after a failure: the first retry soon, because the
#: usual cause is a laptop that has just woken and not reconnected, then hourly.
BACKOFF_MINUTES = (5, 15, 60)


@dataclass(frozen=True, slots=True)
class DailyView:
    quote: Quote | None
    picture: Picture | None
    #: Every part that is switched on is today's. False while a retry is still owed, which
    #: is how the window knows to ask again rather than wait for midnight.
    settled: bool = True

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
        fetch_anime: QuoteFetcher | None = None,
        fetch_picture: PictureFetcher | None = None,
    ) -> None:
        self._config = config
        self._dir = cache_dir / "daily"
        agent = f"{about.NAME}/{about.VERSION}"
        self._fetch_quote = fetch_quote or (
            lambda day: zenquotes.fetch_today(day=day, user_agent=agent)
        )
        self._fetch_anime = fetch_anime or (
            lambda day: animechan.fetch_today(day=day, user_agent=agent)
        )
        self._fetch_picture = fetch_picture or (
            lambda day, into: bing.fetch_today(day=day, into=into, user_agent=agent)
        )
        #: Per source: when the next attempt is allowed, and how many in a row have failed.
        self._next_try: dict[str, datetime] = {}
        self._failures: dict[str, int] = {}

    def use_config(self, config: AppConfig) -> None:
        self._config = config

    def today(self, now: datetime) -> DailyView:
        settled = True
        quote: Quote | None = None
        picture: Picture | None = None
        if self._config.daily.quote:
            quote, fresh = self._quote_for(now)
            settled = settled and fresh
        if self._config.daily.picture:
            picture, fresh = self._picture_for(now)
            settled = settled and fresh
        return DailyView(quote=quote, picture=picture, settled=settled)

    def source_for(self, day: date) -> str:
        """Which provider answers on ``day``, by the Settings switch."""
        chosen = self._config.daily.quote_source
        if chosen == "alternate":
            return "anime" if day.toordinal() % 2 == 0 else "zenquotes"
        return chosen if chosen in ("anime", "zenquotes") else "anime"

    # --- quote ----------------------------------------------------------------------------

    def _quote_for(self, now: datetime) -> tuple[Quote | None, bool]:
        day = now.date()
        source = self.source_for(day)
        # ZenQuotes' day is the UTC day; anything else is today's local date.
        provider_day = _utc_date(now) if source == "zenquotes" else day
        name = f"quote-{source}.json"
        cached = self._read(name)
        held = _quote_from(cached) if cached else None
        if (
            held is not None
            and cached is not None
            and cached.get("provider_day") == provider_day.isoformat()
        ):
            return held, True
        if not self._may_try(source, now):
            return held, False

        fetch = self._fetch_anime if source == "anime" else self._fetch_quote
        try:
            quote = fetch(day)
        except DailyError as exc:
            logger.warning("{}", exc)
            self._failed(source, now)
            return held, False
        self._succeeded(source)
        quote = replace(quote, source=source)
        self._write(
            name,
            {
                "day": day.isoformat(),
                "provider_day": provider_day.isoformat(),
                "fetched_at": now.isoformat(timespec="seconds"),
                "text": quote.text,
                "author": quote.author,
                "work": quote.work,
                "source": source,
                "credit": quote.credit,
                "credit_url": quote.credit_url,
            },
        )
        return quote, True

    # --- picture --------------------------------------------------------------------------

    def _picture_for(self, now: datetime) -> tuple[Picture | None, bool]:
        day = now.date()
        cached = self._read("picture.json")
        held = _picture_from(cached) if cached else None
        if held is not None and not held.path.exists():
            held = None
        if (
            held is not None
            and cached is not None
            and cached.get("provider_day") == day.isoformat()
        ):
            return held, True
        if not self._may_try("picture", now):
            return held, False

        try:
            result = self._fetch_picture(day, self._dir)
        except DailyError as exc:
            logger.warning("{}", exc)
            self._failed("picture", now)
            return held, False
        path, title, copyright, link = (Path(str(result[0])), *map(str, result[1:4]))
        published = result[4] if len(result) > 4 and isinstance(result[4], date) else day
        if published < day:
            # Bing has not turned its day over yet. Keep what arrived — it is still the
            # newest there is — and look again later rather than at tomorrow's midnight.
            self._failed("picture", now)
        else:
            self._succeeded("picture")
        self._write(
            "picture.json",
            {
                "day": day.isoformat(),
                "provider_day": published.isoformat(),
                "fetched_at": now.isoformat(timespec="seconds"),
                "path": str(path),
                "title": title,
                "copyright": copyright,
                "link": link,
            },
        )
        self._prune_pictures(keep=path)
        picture = Picture(path=path, title=title, copyright=copyright, copyright_url=link, day=day)
        return picture, published >= day

    def _prune_pictures(self, *, keep: Path) -> None:
        for old in self._dir.glob("picture-*.jpg"):
            if old != keep:
                try:
                    old.unlink()
                except OSError:  # pragma: no cover — a locked file is not worth a warning
                    pass

    # --- retrying -------------------------------------------------------------------------

    def _may_try(self, source: str, now: datetime) -> bool:
        due = self._next_try.get(source)
        return due is None or now >= due

    def _failed(self, source: str, now: datetime) -> None:
        count = self._failures.get(source, 0)
        wait = BACKOFF_MINUTES[min(count, len(BACKOFF_MINUTES) - 1)]
        self._failures[source] = count + 1
        self._next_try[source] = now + timedelta(minutes=wait)

    def _succeeded(self, source: str) -> None:
        self._failures.pop(source, None)
        self._next_try.pop(source, None)

    def retry_now(self) -> None:
        """Forget the backoff: the network has just come back, so ask at once."""
        self._next_try.clear()

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


def _utc_date(now: datetime) -> date:
    """The UTC calendar date at ``now``, which is local time when it carries no zone."""
    return now.astimezone(UTC).date()


def _quote_from(payload: dict[str, object]) -> Quote | None:
    text = str(payload.get("text") or "")
    if not text:
        return None
    try:
        day = date.fromisoformat(str(payload.get("day")))
    except ValueError:
        return None
    defaults = Quote(text=text, author="", day=day)
    return Quote(
        text=text,
        author=str(payload.get("author") or ""),
        day=day,
        credit=str(payload.get("credit") or defaults.credit),
        credit_url=str(payload.get("credit_url") or defaults.credit_url),
        work=str(payload.get("work") or ""),
        source=str(payload.get("source") or defaults.source),
    )


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


__all__ = ["QUOTE_SOURCES", "DailyService", "DailyView"]
