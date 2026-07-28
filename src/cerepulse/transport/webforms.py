"""ASP.NET WebForms page state and Microsoft AJAX delta handling.

WebForms is a stateful, event-driven protocol: the browser posts the *entire* form back to
the same page, and the server rebuilds its control tree from that before running the
requested event. So every request depends on carrying forward a valid snapshot of the
page's hidden state.

Two rules follow, and both are load-bearing:

1. **Never hard-code field names.** :meth:`WebFormsState.from_html` collects every input,
   select and textarea in the form, so the payload stays correct when the vendor adds a
   field. On the captured login page this reproduces all 33 posted fields exactly.
2. **Refresh state after every response.** ``__VIEWSTATE`` and ``__EVENTVALIDATION`` are
   rotated by the server; reusing a stale pair is rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from typing import Self

from lxml import html as lxml_html

from cerepulse.core.errors import ParserError

#: ``__doPostBack('target','argument')`` as emitted into href/onclick attributes.
_POSTBACK_RE = re.compile(r"__doPostBack\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]*)['\"]\s*\)")

#: ``Sys.WebForms.PageRequestManager._initialize('ctl00$ScriptManager1', 'form1', ...)``
_SCRIPT_MANAGER_RE = re.compile(
    r"PageRequestManager\._initialize\(\s*['\"]([^'\"]+)['\"]",
)

#: UpdatePanel entries inside the ``_initialize`` registration array: ``'tctl00$Panel1'``.
_UPDATE_PANEL_RE = re.compile(r"['\"]t(ctl00[^'\"]*)['\"]")

# Input types the browser never submits, so neither do we.
_SKIPPED_INPUT_TYPES = {"submit", "button", "image", "reset", "file"}
# Input types that are only submitted when the user selected them.
_SELECTABLE_INPUT_TYPES = {"checkbox", "radio"}

#: Delta record type emitted by the server for a refreshed hidden field.
HIDDEN_FIELD = "hiddenField"


@dataclass(frozen=True, slots=True)
class DeltaRecord:
    """One record of a Microsoft AJAX partial-update response."""

    type: str
    id: str
    content: str


@dataclass(slots=True)
class WebFormsState:
    """A mutable snapshot of one WebForms page's postable state."""

    fields: dict[str, str] = field(default_factory=dict)
    action: str = ""
    form_id: str = "form1"

    # --- construction ---------------------------------------------------------------

    @classmethod
    def from_html(cls, html: str, *, form_id: str = "form1") -> Self:
        """Parse every submittable control out of the named form."""
        document = _parse(html)

        forms = document.xpath(f"//form[@id={form_id!r}]") or document.xpath("//form")
        if not forms:
            raise ParserError("No <form> element found in the response")
        form = forms[0]

        state = cls(fields={}, action=form.get("action", ""), form_id=form.get("id", form_id))
        state._collect(form)
        return state

    def _collect(self, form: lxml_html.HtmlElement) -> None:
        for element in form.iter("input", "select", "textarea"):
            name = element.get("name")
            if not name:
                continue

            tag = element.tag
            if tag == "input":
                input_type = (element.get("type") or "text").lower()
                if input_type in _SKIPPED_INPUT_TYPES:
                    continue
                if input_type in _SELECTABLE_INPUT_TYPES and element.get("checked") is None:
                    continue
                self.fields[name] = element.get("value", "")
            elif tag == "select":
                self.fields[name] = _selected_option(element)
            else:  # textarea
                self.fields[name] = element.text or ""

    # --- reading --------------------------------------------------------------------

    def require(self, name: str) -> str:
        """Return a field that must be present, with a diagnostic if it is not."""
        try:
            return self.fields[name]
        except KeyError as exc:
            raise ParserError(f"Required form field {name!r} is missing from the page") from exc

    @property
    def viewstate(self) -> str:
        return self.fields.get("__VIEWSTATE", "")

    @property
    def event_validation(self) -> str:
        return self.fields.get("__EVENTVALIDATION", "")

    # --- building a request ---------------------------------------------------------

    def postback(self, target: str, argument: str = "", **overrides: str) -> dict[str, str]:
        """Build the payload for ``__doPostBack(target, argument)``.

        Mirrors what the browser submits: the whole form, with the event fields set to
        identify which control was activated.
        """
        payload = dict(self.fields)
        payload["__EVENTTARGET"] = target
        payload["__EVENTARGUMENT"] = argument
        payload.update(overrides)
        return payload

    # --- keeping state current ------------------------------------------------------

    def update_from_html(self, html: str) -> None:
        """Replace state from a full page response."""
        refreshed = type(self).from_html(html, form_id=self.form_id)
        self.fields = refreshed.fields
        self.action = refreshed.action

    def update_from_delta(self, records: list[DeltaRecord]) -> int:
        """Apply ``hiddenField`` records from a partial-update response.

        Returns the number of fields updated. A delta that refreshes no hidden fields is
        legitimate (not every postback rotates state), so this does not raise.
        """
        updated = 0
        for record in records:
            if record.type == HIDDEN_FIELD and record.id:
                self.fields[record.id] = record.content
                updated += 1
        return updated


def parse_delta(text: str) -> list[DeltaRecord]:
    """Parse a Microsoft AJAX partial-update response.

    The wire format is a flat sequence of ``length|type|id|content|`` records, where
    ``length`` is the character count of ``content``. Content is *not* escaped, so the
    length prefix is the only safe way to find each record's end — splitting on ``|``
    corrupts any record whose content contains a pipe.
    """
    records: list[DeltaRecord] = []
    position = 0
    size = len(text)

    while position < size:
        length_text, position = _read_until_pipe(text, position)
        if length_text is None:
            break
        if not length_text.isdigit():
            raise ParserError(f"Malformed delta response: expected a length, got {length_text!r}")
        length = int(length_text)

        record_type, position = _read_until_pipe(text, position)
        record_id, position = _read_until_pipe(text, position)
        if record_type is None or record_id is None:
            raise ParserError("Malformed delta response: truncated record header")

        content = text[position : position + length]
        if len(content) != length:
            raise ParserError("Malformed delta response: content shorter than its declared length")
        position += length

        if position < size and text[position] == "|":
            position += 1

        records.append(DeltaRecord(type=record_type, id=record_id, content=content))

    return records


def find_postback_targets(html: str, *, contains: str = "") -> list[str]:
    """Return the unique ``__doPostBack`` targets on a page, in document order.

    ``contains`` filters to targets whose identifier includes a substring, e.g. ``LnkDate``
    to find the clickable dates in the attendance grid.

    Entities are decoded first: ASP.NET emits these calls into ``href`` attributes with the
    quotes escaped (``__doPostBack(&#39;...&#39;,&#39;&#39;)``), so scanning the raw markup
    would silently miss every grid link while still matching the ones in ``<script>`` blocks.
    """
    seen: dict[str, None] = {}
    for target, _argument in _POSTBACK_RE.findall(unescape(html)):
        if contains and contains.lower() not in target.lower():
            continue
        seen.setdefault(target, None)
    return list(seen)


def find_script_manager(html: str) -> str | None:
    """Return the ScriptManager's unique ID, needed to form an async postback."""
    match = _SCRIPT_MANAGER_RE.search(html)
    return match.group(1) if match else None


def find_update_panels(html: str) -> list[str]:
    """Return the UpdatePanel IDs registered with the ScriptManager.

    ``_initialize`` lists them in its third argument as ``['t<panelId>', '']`` pairs — the
    leading ``t`` is a type marker the client library strips, so we strip it too.
    """
    match = _SCRIPT_MANAGER_RE.search(html)
    if not match:
        return []
    window = html[match.end() : match.end() + 4000]
    return list(dict.fromkeys(_UPDATE_PANEL_RE.findall(window)))


def async_postback_payload(
    state: WebFormsState,
    *,
    script_manager: str,
    update_panel: str,
    target: str,
    argument: str = "",
) -> dict[str, str]:
    """Build the payload for an UpdatePanel partial postback.

    An async postback is an ordinary postback plus one extra field, named after the
    ScriptManager, whose value is ``<UpdatePanelID>|<EventTarget>``. That pairing is what
    tells the server to render a delta instead of the whole page.
    """
    return state.postback(target, argument, **{script_manager: f"{update_panel}|{target}"})


def async_postback_headers(referer: str) -> dict[str, str]:
    """Headers that mark a request as a Microsoft AJAX partial postback."""
    return {
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        "X-MicrosoftAjax": "Delta=true",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": referer,
        "Accept": "*/*",
    }


def is_delta_response(text: str) -> bool:
    """Cheap check for whether a body looks like a partial update rather than a page."""
    head = text.lstrip()[:64]
    prefix, _, _ = head.partition("|")
    return bool(prefix) and prefix.isdigit()


def _read_until_pipe(text: str, position: int) -> tuple[str | None, int]:
    separator = text.find("|", position)
    if separator == -1:
        return None, len(text)
    return text[position:separator], separator + 1


def _selected_option(select: lxml_html.HtmlElement) -> str:
    """Value a browser would submit for a <select>: the selected option, else the first."""
    options = select.xpath(".//option")
    if not options:
        return ""
    for option in options:
        if option.get("selected") is not None:
            return option.get("value", option.text_content().strip())
    if select.get("multiple") is not None:
        return ""
    first = options[0]
    return first.get("value", first.text_content().strip())


def _parse(html: str) -> lxml_html.HtmlElement:
    try:
        return lxml_html.fromstring(html)
    except Exception as exc:  # lxml raises a family of parser errors
        raise ParserError("Response body could not be parsed as HTML") from exc
