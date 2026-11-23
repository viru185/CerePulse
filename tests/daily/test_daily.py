"""The quote and picture of the day: parsing, each source's own day, retrying, and switches.

Every fetcher here is injected — the providers are real web services and no test may reach
them (the root conftest blocks them outright). Clocks carry a zone, so "the UTC day" means
the same thing on a laptop in India and a CI runner in UTC.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from cerepulse.core.config import AppConfig
from cerepulse.core.errors import DailyError
from cerepulse.daily import animechan, bing, zenquotes
from cerepulse.daily.models import Quote
from cerepulse.services.daily import DailyService

IST = timezone(timedelta(hours=5, minutes=30))

# Verbatim shapes from the endpoints: ZenQuotes and Bing on 6 September 2026, Animechan on
# 24 November 2026.
ZEN = [
    {
        "q": "A man with outward courage dares to die.",
        "a": "Lao Tzu",
        "h": "<blockquote>…</blockquote>",
    }
]
BING = {
    "images": [
        {
            "startdate": "20260905",
            "url": "/th?id=OHR.LakeFyans_EN-IN6678174820_1920x1080.jpg&rf=LaDigue_1920x1080.jpg&pid=hp",  # noqa: E501
            "copyright": "Lake Fyans, Grampians National Park (© tracielouise/Getty Images)",
            "copyrightlink": "https://www.bing.com/search?q=Grampians",
            "title": "A reservoir of reflections",
        }
    ]
}
ANIME = {
    "status": "success",
    "data": {
        "content": "Just help them, Just do it, That way when you die, you won't die alone.",
        "anime": {"id": 988, "name": "Jujutsu Kaisen", "altName": "Jujutsu Kaisen"},
        "character": {"id": 2546, "name": "Wasuke Itadori"},
    },
}
DAY = date(2026, 9, 6)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


# --- parsing ------------------------------------------------------------------------------


def test_the_quote_payload_parses() -> None:
    quote = zenquotes.parse(ZEN, day=DAY)
    assert quote.text.startswith("A man with outward courage")
    assert quote.author == "Lao Tzu"
    assert "ZenQuotes" in quote.credit


def test_the_anime_payload_parses_with_its_character_and_show() -> None:
    quote = animechan.parse(ANIME, day=DAY)
    assert quote.text.startswith("Just help them")
    assert quote.attribution == "Wasuke Itadori, Jujutsu Kaisen"
    assert quote.source == "anime"
    assert "Animechan" in quote.credit


def test_the_old_flat_anime_shape_is_read_too() -> None:
    flat = {"anime": "Naruto", "character": "Itachi Uchiha", "quote": "Forgive me, Sasuke."}
    assert animechan.parse(flat, day=DAY).attribution == "Itachi Uchiha, Naruto"


def test_the_picture_payload_parses_and_carries_bings_own_date() -> None:
    url, title, copyright, link, published = bing.parse(BING)
    assert url.startswith("https://www.bing.com/th?id=OHR.LakeFyans")
    assert title == "A reservoir of reflections"
    assert "Getty" in copyright
    assert link.startswith("https://www.bing.com/search")
    assert published == date(2026, 9, 5)


def test_an_unexpected_shape_is_an_error_not_a_blank() -> None:
    with pytest.raises(DailyError):
        zenquotes.parse({"unexpected": True}, day=DAY)
    with pytest.raises(DailyError):
        animechan.parse({"status": "success", "data": {"content": ""}}, day=DAY)
    with pytest.raises(DailyError):
        bing.parse({"images": []})


# --- the service --------------------------------------------------------------------------


class Providers:
    """Fakes for all three, recording what was asked and able to fail on demand."""

    def __init__(self) -> None:
        self.zen: list[date] = []
        self.anime: list[date] = []
        self.pictures: list[date] = []
        self.offline = False
        #: Bing's own date for the image it returns; None means "the day asked for".
        self.published: date | None = None

    def fetch_zen(self, day: date) -> Quote:
        self.zen.append(day)
        if self.offline:
            raise DailyError("no network")
        return Quote(text=f"zen for {day}", author="Someone", day=day)

    def fetch_anime(self, day: date) -> Quote:
        self.anime.append(day)
        if self.offline:
            raise DailyError("no network")
        return Quote(text=f"anime for {day}", author="Spike", day=day, work="Bebop", source="anime")

    def fetch_picture(self, day: date, into: Path) -> tuple[object, ...]:
        self.pictures.append(day)
        if self.offline:
            raise DailyError("no network")
        into.mkdir(parents=True, exist_ok=True)
        path = into / f"picture-{day:%Y%m%d}.jpg"
        path.write_bytes(b"jpeg")
        return path, "Title", "© Someone", "https://example.test", self.published or day


def service(tmp_path: Path, fakes: Providers, *, source: str = "anime") -> DailyService:
    config = AppConfig()
    config = replace(config, daily=replace(config.daily, quote_source=source))
    return DailyService(
        config=config,
        cache_dir=tmp_path,
        fetch_quote=fakes.fetch_zen,
        fetch_anime=fakes.fetch_anime,
        fetch_picture=fakes.fetch_picture,
    )


def test_anime_is_the_default_source() -> None:
    assert AppConfig().daily.quote_source == "anime"


def test_each_source_is_asked_once_a_day(tmp_path: Path) -> None:
    fakes = Providers()
    daily = service(tmp_path, fakes)

    first = daily.today(at(DAY, 9))
    daily.today(at(DAY, 18))
    assert fakes.anime == [DAY] and fakes.pictures == [DAY]
    assert first.quote is not None and first.quote.attribution == "Spike, Bebop"
    assert first.picture is not None and first.picture.path.exists()
    assert first.settled

    daily.today(at(DAY + timedelta(days=1), 9))
    assert fakes.anime == [DAY, DAY + timedelta(days=1)]
    assert not (tmp_path / "daily" / "picture-20260906.jpg").exists(), "yesterday's picture goes"


def test_zenquotes_is_asked_when_its_own_day_turns_not_at_local_midnight(tmp_path: Path) -> None:
    """ZenQuotes changes its quote at 00:00 UTC — 05:30 in India. Fetched at local midnight
    it still served yesterday's quote, which was then stored as today's: the same quote two
    days running."""
    fakes = Providers()
    daily = service(tmp_path, fakes, source="zenquotes")
    daily.today(at(DAY, 10))  # 04:30 UTC on the 6th

    tomorrow = DAY + timedelta(days=1)
    daily.today(at(tomorrow, 0, 30))  # 19:00 UTC on the 6th: ZenQuotes' day has not turned
    assert fakes.zen == [DAY]
    daily.today(at(tomorrow, 6, 0))  # 00:30 UTC on the 7th: now it has
    assert fakes.zen == [DAY, tomorrow]


def test_a_failure_serves_the_cached_quote_and_is_retried_soon(tmp_path: Path) -> None:
    """A wake from sleep fetched before the Wi-Fi was back, failed, and nothing asked again
    until the next midnight: yesterday's tile all day."""
    fakes = Providers()
    daily = service(tmp_path, fakes)
    daily.today(at(DAY, 9))
    tomorrow = DAY + timedelta(days=1)

    fakes.offline = True
    view = daily.today(at(tomorrow, 9, 0))
    assert view.quote is not None and view.quote.text == f"anime for {DAY}", (
        "yesterday's, not nothing"
    )
    assert not view.settled, "and the window is told a retry is owed"

    daily.today(at(tomorrow, 9, 2))
    assert fakes.anime.count(tomorrow) == 1, "not before the backoff runs out"

    fakes.offline = False
    view = daily.today(at(tomorrow, 9, 6))
    assert view.settled
    assert view.quote is not None and view.quote.text == f"anime for {tomorrow}"


def test_the_backoff_grows_and_the_network_coming_back_resets_it(tmp_path: Path) -> None:
    fakes = Providers()
    daily = service(tmp_path, fakes)
    fakes.offline = True
    daily.today(at(DAY, 9, 0))
    daily.today(at(DAY, 9, 5))  # second failure: the next wait is 15 minutes
    daily.today(at(DAY, 9, 10))
    assert fakes.anime == [DAY, DAY]

    daily.retry_now()
    fakes.offline = False
    assert daily.today(at(DAY, 9, 11)).settled


def test_a_picture_bing_has_not_replaced_yet_is_kept_and_asked_for_again(tmp_path: Path) -> None:
    fakes = Providers()
    fakes.published = DAY - timedelta(days=1)
    daily = service(tmp_path, fakes)

    view = daily.today(at(DAY, 0, 10))
    assert view.picture is not None, "the newest image there is, meanwhile"
    assert not view.settled

    fakes.published = DAY
    assert daily.today(at(DAY, 0, 20)).settled
    assert fakes.pictures == [DAY, DAY]


def test_switching_source_and_back_the_same_day_asks_nobody_twice(tmp_path: Path) -> None:
    fakes = Providers()
    config = AppConfig()
    daily = service(tmp_path, fakes)
    daily.today(at(DAY, 9))

    daily.use_config(replace(config, daily=replace(config.daily, quote_source="zenquotes")))
    assert daily.today(at(DAY, 10)).quote.source == "zenquotes"  # type: ignore[union-attr]
    daily.use_config(replace(config, daily=replace(config.daily, quote_source="anime")))
    assert daily.today(at(DAY, 11)).quote.source == "anime"  # type: ignore[union-attr]

    assert fakes.anime == [DAY] and fakes.zen == [DAY]


def test_alternate_takes_turns_by_date(tmp_path: Path) -> None:
    daily = service(tmp_path, Providers(), source="alternate")
    sources = {daily.source_for(DAY + timedelta(days=offset)) for offset in range(2)}
    assert sources == {"anime", "zenquotes"}
    assert daily.source_for(DAY) == daily.source_for(DAY + timedelta(days=2))


def test_a_switched_off_source_is_never_asked(tmp_path: Path) -> None:
    fakes = Providers()
    daily = service(tmp_path, fakes)
    config = AppConfig()
    daily.use_config(replace(config, daily=replace(config.daily, quote=False, picture=False)))

    view = daily.today(at(DAY, 12))
    assert view.is_empty and view.settled
    assert fakes.anime == [] and fakes.zen == [] and fakes.pictures == []
