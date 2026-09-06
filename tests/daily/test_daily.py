"""The quote and picture of the day: parsing, once-a-day caching, and offline fallback."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from cerepulse.core.config import AppConfig
from cerepulse.core.errors import DailyError
from cerepulse.daily import bing, zenquotes
from cerepulse.daily.models import Quote
from cerepulse.services.daily import DailyService

# Verbatim shapes from the two endpoints on 6 September 2026.
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
DAY = date(2026, 9, 6)


def test_the_quote_payload_parses() -> None:
    quote = zenquotes.parse(ZEN, day=DAY)
    assert quote.text.startswith("A man with outward courage")
    assert quote.author == "Lao Tzu"
    assert "ZenQuotes" in quote.credit


def test_the_picture_payload_parses_and_the_url_is_made_absolute() -> None:
    url, title, copyright, link = bing.parse(BING)
    assert url.startswith("https://www.bing.com/th?id=OHR.LakeFyans")
    assert title == "A reservoir of reflections"
    assert "Getty" in copyright
    assert link.startswith("https://www.bing.com/search")


def test_an_unexpected_shape_is_an_error_not_a_blank() -> None:
    with pytest.raises(DailyError):
        zenquotes.parse({"unexpected": True}, day=DAY)
    with pytest.raises(DailyError):
        bing.parse({"images": []})


def service(tmp_path: Path, quote_calls: list[date], picture_calls: list[date]) -> DailyService:
    def fetch_quote(day: date) -> Quote:
        quote_calls.append(day)
        return Quote(text=f"quote for {day}", author="Someone", day=day)

    def fetch_picture(day: date, into: Path) -> tuple[Path, str, str, str]:
        picture_calls.append(day)
        into.mkdir(parents=True, exist_ok=True)
        path = into / f"picture-{day:%Y%m%d}.jpg"
        path.write_bytes(b"jpeg")
        return path, "Title", "© Someone", "https://example.test"

    return DailyService(
        config=AppConfig(), cache_dir=tmp_path, fetch_quote=fetch_quote, fetch_picture=fetch_picture
    )


def test_each_source_is_asked_once_a_day(tmp_path: Path) -> None:
    """Two free endpoints; one question each per day is the whole budget."""
    quotes: list[date] = []
    pictures: list[date] = []
    daily = service(tmp_path, quotes, pictures)

    first = daily.today(datetime(2026, 9, 6, 12, 0))
    daily.today(datetime(2026, 9, 6, 18, 0))
    assert quotes == [DAY] and pictures == [DAY]
    assert first.quote is not None and first.quote.text == "quote for 2026-09-06"
    assert first.picture is not None and first.picture.path.exists()

    daily.today(datetime(2026, 9, 7, 9, 0))
    assert quotes == [DAY, date(2026, 9, 7)]
    assert not (tmp_path / "daily" / "picture-20260906.jpg").exists(), "yesterday's picture goes"


def test_a_failed_fetch_serves_the_cached_day_rather_than_nothing(tmp_path: Path) -> None:
    quotes: list[date] = []
    pictures: list[date] = []
    daily = service(tmp_path, quotes, pictures)
    daily.today(datetime(2026, 9, 6, 12, 0))

    def offline(_day: date) -> Quote:
        raise DailyError("no network")

    daily._fetch_quote = offline  # type: ignore[assignment]
    view = daily.today(datetime(2026, 9, 7, 9, 0))
    assert view.quote is not None and view.quote.day == DAY


def test_a_switched_off_source_is_never_asked(tmp_path: Path) -> None:
    from dataclasses import replace

    quotes: list[date] = []
    pictures: list[date] = []
    daily = service(tmp_path, quotes, pictures)
    config = AppConfig()
    daily.use_config(replace(config, daily=replace(config.daily, quote=False, picture=False)))

    assert daily.today(datetime(2026, 9, 6, 12, 0)).is_empty
    assert quotes == [] and pictures == []
