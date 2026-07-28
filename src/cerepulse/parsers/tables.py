"""Minimal HTML-table extraction over lxml, shared by the grid parsers."""

from __future__ import annotations

from collections.abc import Iterator

from lxml import html as lxml_html

from cerepulse.core.errors import ParserError
from cerepulse.parsers.primitives import clean


def parse_document(html: str) -> lxml_html.HtmlElement:
    try:
        return lxml_html.fromstring(html)
    except Exception as exc:  # noqa: BLE001 — lxml raises assorted parser errors
        raise ParserError("Response could not be parsed as HTML") from exc


def find_table(root: lxml_html.HtmlElement, table_id: str) -> lxml_html.HtmlElement:
    """Locate a table by its exact id, or raise so a UI change surfaces loudly."""
    matches = root.xpath(f"//table[@id={table_id!r}]")
    if not matches:
        raise ParserError(f"Expected table {table_id!r} was not found in the response")
    return matches[0]


def find_table_opt(root: lxml_html.HtmlElement, table_id: str) -> lxml_html.HtmlElement | None:
    matches = root.xpath(f"//table[@id={table_id!r}]")
    return matches[0] if matches else None


def row_cells(row: lxml_html.HtmlElement) -> list[lxml_html.HtmlElement]:
    """The ``td`` elements of a row (header ``th`` rows return empty)."""
    return row.xpath("./td")


def cell_texts(row: lxml_html.HtmlElement) -> list[str]:
    return [clean(cell.text_content()) for cell in row_cells(row)]


def data_rows(table: lxml_html.HtmlElement) -> Iterator[lxml_html.HtmlElement]:
    """Yield body rows: every ``tr`` that has ``td`` cells, skipping header rows."""
    for row in table.xpath(".//tr"):
        if row.xpath("./td"):
            yield row
