"""Which comp-off credit each ``CO-`` leave used, and the portal's own comp-off balance.

The leave register says how much comp-off was credited and consumed, and nothing about which
credit a given day off drew on. The application form does. Opening a ``CO-`` application
from the leave list (``GridView1`` ``Select$N``, the list's own view-only postback) renders
the form, and its hidden fields carry the credit chosen when it was filed::

    hdnCOAdjDate              31-May-26
    hdnCOAdjustmenttype1      Half
    hdnCOAdjustmentHalfType1  Second
    HdnTransNo                7732

The same form's date picker asks two read-only page methods for the balance and the credits
on offer — ``COAdjDateCount`` and ``FillCoAdjDates`` — and they answer outside a browser.
"""

from __future__ import annotations

import html as html_lib
import re
from datetime import date
from typing import Any

from lxml import html as lxml_html

from cerepulse.core.errors import ParserError
from cerepulse.models.leave import CompOffLink
from cerepulse.parsers.primitives import clean, parse_date, parse_float
from cerepulse.parsers.tables import parse_document

#: The leave list's grid, and the code its Leave Type column gives a comp-off spend.
LEAVE_GRID = "GridView"
SPEND_CODE = "CO-"

_POSTBACK = re.compile(r"__doPostBack\(\s*'([^']*)'\s*,\s*'([^']*)'\s*\)")
_BALANCE = re.compile(r":\s*(-?\d+(?:\.\d+)?)\s*$")


def comp_off_spend_rows(html: str) -> list[tuple[str, str, str]]:
    """``(app id, postback target, argument)`` for every ``CO-`` row on a leave-list page."""
    document = parse_document(html)
    found: list[tuple[str, str, str]] = []
    for row in document.xpath(f"//table[contains(@id,{LEAVE_GRID!r})]//tr"):
        cells = [clean(cell.text_content()) for cell in row.xpath("./td")]
        if not any(cell.upper() == SPEND_CODE for cell in cells):
            continue
        markup = html_lib.unescape(lxml_html.tostring(row, encoding="unicode"))
        match = _POSTBACK.search(markup)
        app_id = next((cell for cell in cells if cell.isdigit()), "")
        if match is None or not app_id:
            continue
        found.append((app_id, match.group(1), match.group(2)))
    return found


def hidden_field(html: str, suffix: str) -> str | None:
    """The value of the input whose name ends in ``$<suffix>``, or ``None`` if absent."""
    document = parse_document(html)
    for node in document.xpath("//input[@name]"):
        name = str(node.get("name"))
        if name == suffix or name.endswith(f"${suffix}"):
            return str(node.get("value") or "")
    return None


def employee_key(html: str) -> str:
    """The portal's internal employee number, which its page methods take as ``mEmpCode``.

    Not the employee code on the banner: a short number the leave pages keep in a hidden
    field and send back with every lookup.
    """
    for suffix in ("hdnEmpCode", "empCode", "hdnempCode"):
        value = hidden_field(html, suffix)
        if value:
            return value.strip()
    raise ParserError("The leave page carries no employee number for its lookups")


def parse_comp_off_link(html: str, *, app_id: str) -> list[CompOffLink]:
    """The credit an opened ``CO-`` application drew on. Empty when it recorded none.

    Raises when the page is not that application's form: a link saved against the wrong
    application would put a day off on a credit it never touched, and nothing downstream
    could tell.
    """
    shown = hidden_field(html, "HdnTransNo")
    if shown is None:
        raise ParserError(f"Application {app_id} did not open as a leave form")
    if shown.strip() != app_id:
        raise ParserError(f"Asked for application {app_id}, the portal opened {shown.strip()}")

    raw = hidden_field(html, "hdnCOAdjDate") or hidden_field(html, "hdnSelectedCoAdjDate") or ""
    kind = clean(hidden_field(html, "hdnCOAdjustmenttype1") or "").casefold()
    half = clean(hidden_field(html, "hdnCOAdjustmentHalfType1") or "") if kind == "half" else ""
    amount = 0.5 if kind == "half" else 1.0

    dates = [parse_date(part) for part in re.split(r"[,;]", raw) if clean(part)]
    links = [
        CompOffLink(app_id=app_id, credit_date=when, days=amount, half=half)
        for when in dates
        if when is not None
    ]
    if not links:
        return [CompOffLink(app_id=app_id, credit_date=None, days=0.0)]
    return links


def parse_balance_answer(payload: Any) -> float:
    """``{"d": "CO Balance As On Date 24-Nov-2026 : 5"}`` → ``5.0``."""
    text = payload.get("d") if isinstance(payload, dict) else None
    if not isinstance(text, str):
        raise ParserError("The comp-off balance lookup answered without a balance")
    match = _BALANCE.search(text.strip())
    if match is None:
        raise ParserError(f"The comp-off balance lookup answered {text[:60]!r}")
    return float(match.group(1))


def parse_dates_answer(payload: Any) -> list[tuple[date, float]]:
    """``{"d": [{"Text": "02-Oct-26", "Value": "1"}, ...]}`` → ``[(date, 1.0), ...]``.

    ``Value`` is the amount on offer; a half credit says ``0.5``. An entry whose text is not
    a date — a "Select" placeholder — is skipped rather than failed on.
    """
    items = payload.get("d") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ParserError("The comp-off date lookup answered without a list")
    offered: list[tuple[date, float]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        when = parse_date(str(item.get("Text", "")))
        if when is None:
            continue
        offered.append((when, parse_float(str(item.get("Value", "")), default=1.0) or 1.0))
    return sorted(offered)
