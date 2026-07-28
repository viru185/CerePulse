"""Navigation menu parsing.

Portal pages are gated on a server-side role check. Requesting
``/Atten/MyAttendanceReport.aspx`` directly returns *"You do not have sufficient privileges
(ROLE) to view this page"* even with a valid session — the real menu link carries a
privilege token::

    /Atten/MyAttendanceReport.aspx?mnusr=menu__10101

Some entries carry further opaque parameters (``reqtype``, ``odtype``, ``cInfo``) that look
like encrypted server-side blobs. None of these can be safely invented, so the menu is
parsed out of the landing page and pages are resolved **by label** rather than by URL.
That is exactly the dynamic discovery Chapter 02 section 10 calls for: if the vendor
renumbers a menu, this keeps working.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from lxml import html as lxml_html

from cerepulse.core.errors import ParserError

#: Marks an anchor as a privilege-bearing menu link.
PRIVILEGE_PARAM = "mnusr="


@dataclass(frozen=True, slots=True)
class MenuEntry:
    """One leaf of the navigation menu."""

    section: str  # e.g. "Time > Attendance"
    label: str  # e.g. "My Attendance"
    url: str  # absolute URL including the privilege token
    menu_id: str  # e.g. "menu__10101"

    @property
    def full_name(self) -> str:
        return f"{self.section} | {self.label}" if self.section else self.label


class MenuIndex:
    """Lookup over the parsed menu, keyed on label (case-insensitive)."""

    def __init__(self, entries: list[MenuEntry]) -> None:
        self.entries = entries

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[MenuEntry]:
        return iter(self.entries)

    def find(self, label: str, *, section: str | None = None) -> MenuEntry | None:
        """Return the first entry matching ``label``, optionally within a section."""
        wanted = label.strip().casefold()
        section_wanted = section.strip().casefold() if section else None
        for entry in self.entries:
            if entry.label.strip().casefold() != wanted:
                continue
            if section_wanted and section_wanted not in entry.section.casefold():
                continue
            return entry
        return None

    def require(self, label: str, *, section: str | None = None) -> MenuEntry:
        """Like :meth:`find`, but raises with a useful diagnostic when absent."""
        entry = self.find(label, section=section)
        if entry is None:
            available = ", ".join(sorted({e.label for e in self.entries})[:12])
            raise ParserError(
                f"Menu entry {label!r} not found"
                + (f" in section {section!r}" if section else "")
                + f". Available labels include: {available}"
            )
        return entry


def parse_menu(html: str) -> MenuIndex:
    """Extract every privilege-bearing menu link from the landing page."""
    try:
        document = lxml_html.fromstring(html)
    except Exception as exc:  # noqa: BLE001 — lxml raises several parser error types
        raise ParserError("Landing page could not be parsed as HTML") from exc

    entries: list[MenuEntry] = []
    seen: set[str] = set()

    for anchor in document.xpath("//a[@href]"):
        href = anchor.get("href", "")
        if PRIVILEGE_PARAM not in href or href in seen:
            continue
        seen.add(href)

        entries.append(
            MenuEntry(
                section=(anchor.get("data-menunav1_2") or "").strip(),
                label=(anchor.get("data-menunav3") or anchor.text_content()).strip(),
                url=href,
                menu_id=_menu_id(href),
            )
        )

    if not entries:
        raise ParserError("No navigation menu links found on the landing page")
    return MenuIndex(entries)


def _menu_id(href: str) -> str:
    _, _, tail = href.partition(PRIVILEGE_PARAM)
    return tail.partition("&")[0]
