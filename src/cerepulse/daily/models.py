"""What a day's quote and picture are, once fetched."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Quote:
    text: str
    author: str
    #: The calendar day this was the quote for; the provider changes it at 00:00 UTC.
    day: date
    #: The credit the free tier requires, shown wherever the quote is.
    credit: str = "Inspirational quotes provided by ZenQuotes API"
    credit_url: str = "https://zenquotes.io/"


@dataclass(frozen=True, slots=True)
class Picture:
    #: Where the image is on disk. Downloaded once per day; the tile never streams.
    path: Path
    title: str
    #: The provider's own copyright line, shown verbatim.
    copyright: str
    copyright_url: str
    day: date
