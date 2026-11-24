"""Development tool: walk every link the portal offers, read-only, and index what each holds.

``capture`` fetches the pages the app already reads. This reads *everything else*, for the
questions no existing capture answers — first among them, which comp-off credit each ``CO-``
leave was adjusted against, and whether any page lists comp-off that lapsed unused. Fact 7
in CLAUDE.md is the reason it exists: look at what the portal shows before deciding what a
column means.

**It is read-only by construction.** Every request goes through :class:`Guard`, which allows:

- ``GET`` of any same-site URL whose path and link text name no action (log off, delete,
  approve, save, upload…);
- a postback only from a short allowlist of controls that *show* things: the status filter
  (``cboReports``), the leave list's **Add New** (which opens a blank form), a grid row's
  **Select** (which opens an existing application), and a dropdown change (the leave type,
  which renders the comp-off adjustment list);
- a submit button only when its caption is **Past History**.

Nothing else is ever posted. No ``Save``, ``Submit``, ``Apply`` or ``Update`` control can be
reached, because no code path builds one. The walk is paced at one request per ``delay``
seconds and stops on any sign that the session ended.

Output, under ``--out`` (gitignored ``Research/`` by default — the pages hold bank, tax and
family details, so they must never be committed): one HTML file per page, ``survey.json``
indexing every page's title, grids, buttons, dropdowns, links and postback targets, and
``keywords.txt`` with every hit for the comp-off vocabulary.
"""

from __future__ import annotations

import html
import json
import re
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from loguru import logger
from lxml import etree
from lxml import html as lxml_html

from cerepulse.auth.manager import AuthManager
from cerepulse.capture import MissingCredentialsError, resolve_credentials
from cerepulse.core.config import AppConfig
from cerepulse.core.errors import CerePulseError, SessionExpiredError
from cerepulse.core.logging_setup import workflow
from cerepulse.parsers.comp_off import comp_off_spend_rows
from cerepulse.parsers.menu import MenuIndex, parse_menu
from cerepulse.transport import pages
from cerepulse.transport.client import HttpClient
from cerepulse.transport.webforms import WebFormsState

#: Words that name an action rather than a view. Matched on whole words in link text and
#: button captions, and anywhere in a URL path or query.
ACTION_WORDS = (
    "logoff",
    "log off",
    "logout",
    "log out",
    "signout",
    "sign out",
    "delete",
    "remove",
    "withdraw",
    "approve",
    "reject",
    "save",
    "submit",
    "update",
    "upload",
    "cancel",
    "discard",
    "confirm",
    "apply now",
    "send",
)
_ACTION_IN_URL = re.compile(
    r"log_?off|log_?out|sign_?out|delete|remove|withdraw|approve|reject|upload|"
    r"[?&](?:action|cmd|mode)=(?!view)",
    re.IGNORECASE,
)
_SIGN_OUT_IN_URL = re.compile(r"log_?off|log_?out|sign_?out", re.IGNORECASE)
#: Postbacks that only change what is shown. (control name pattern, argument pattern)
VIEW_POSTBACKS = (
    (re.compile(r"\$cboReports$"), re.compile(r"^$")),
    (re.compile(r"\$Menu1$"), re.compile(r"^Add New$")),
    (re.compile(r"\$GridView\d*$"), re.compile(r"^Select\$\d+$")),
)
VIEW_SUBMITS = (re.compile(r"^\s*past\s*history\s*$", re.IGNORECASE),)
KEYWORDS = (
    "co adjust",
    "adjustment date",
    "comp. off",
    "comp off",
    "comp-off",
    "co+",
    "co-",
    "past history",
    "balance as on",
    "lapse",
    "expire",
    "expiry",
    "utilize",
    "utilise",
)
_POSTBACK = re.compile(r"__doPostBack\(\s*'([^']*)'\s*,\s*'([^']*)'\s*\)")
_SCRIPT_URL = re.compile(
    r"(?:window\.open|location\.href\s*=|location\.replace|window\.location\s*=)\s*\(?\s*'([^']+)'",
    re.IGNORECASE,
)


#: A page and its body, or None when the guard refused it or the request failed.
Fetched = tuple["SurveyPage", str] | None


class Refused(CerePulseError):
    """The guard declined a request. Logged and skipped, never sent."""


# --- the guard -------------------------------------------------------------------------


def names_an_action(text: str) -> str | None:
    """The action word in ``text``, if any. Whole words, so "Cancellation" is a view."""
    lowered = " ".join(text.casefold().split())
    for word in ACTION_WORDS:
        if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", lowered):
            return word
    return None


@dataclass
class Guard:
    """The only thing that decides whether a request may be sent."""

    host: str
    refused: list[str] = field(default_factory=list)

    def check_get(self, url: str, label: str = "", *, from_menu: bool = False) -> None:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or parts.netloc.casefold() != self.host:
            self._refuse(f"GET {url}: not the portal")
        # A menu entry is a page, whatever it is called: "My Approvers" lists who approves,
        # and "Submit Tax Declaration - Sheet" shows a form. Only signing out is refused
        # there. Links found *inside* pages are judged by their URL and their wording.
        if from_menu:
            if _SIGN_OUT_IN_URL.search(parts.path):
                self._refuse(f"GET {url}: signs out")
            return
        if _ACTION_IN_URL.search(f"{parts.path}?{parts.query}"):
            self._refuse(f"GET {url}: the URL names an action")
        word = names_an_action(label)
        if word:
            self._refuse(f"GET {url}: link text {label!r} names an action ({word})")

    def check_postback(self, target: str, argument: str, *, is_select: bool = False) -> None:
        if is_select:
            if names_an_action(target.rsplit("$", 1)[-1]):
                self._refuse(f"postback {target}: the dropdown names an action")
            return
        for control, allowed in VIEW_POSTBACKS:
            if control.search(target) and allowed.fullmatch(argument):
                return
        self._refuse(f"postback {target}({argument!r}): not a view-only control")

    def check_submit(self, button: str, caption: str) -> None:
        if any(pattern.match(caption) for pattern in VIEW_SUBMITS):
            return
        self._refuse(f"submit {button}={caption!r}: only 'Past History' may be pressed")

    def _refuse(self, reason: str) -> None:
        self.refused.append(reason)
        raise Refused(reason)


# --- what a page holds -----------------------------------------------------------------


@dataclass
class SurveyPage:
    id: str
    via: str
    url: str
    final_url: str = ""
    status: int = 0
    content_type: str = ""
    bytes: int = 0
    file: str = ""
    title: str = ""
    heading: str = ""
    tables: list[dict[str, object]] = field(default_factory=list)
    buttons: list[dict[str, str]] = field(default_factory=list)
    selects: list[dict[str, object]] = field(default_factory=list)
    links: list[dict[str, str]] = field(default_factory=list)
    postbacks: list[dict[str, str]] = field(default_factory=list)
    keywords: dict[str, list[str]] = field(default_factory=dict)
    error: str | None = None


def describe_page(page: SurveyPage, body: str) -> None:
    """Fill in everything a later reader would grep for."""
    try:
        document = lxml_html.fromstring(body)
    except (ValueError, etree.ParserError):
        page.error = "not parseable as HTML"
        return
    page.title = " ".join(" ".join(document.xpath("//title//text()")).split())
    headings = document.xpath(
        "//h1//text() | //h2//text() | //*[contains(@class,'breadcrumb')]//text()"
    )
    page.heading = " ".join(" ".join(str(text) for text in headings).split())[:200]

    for table in document.xpath("//table[@id]"):
        rows = table.xpath("./tr | ./tbody/tr | ./thead/tr")
        if not rows:
            continue
        header = [" ".join(cell.text_content().split()) for cell in rows[0].xpath("./th|./td")]
        page.tables.append({"id": table.get("id"), "headers": header, "rows": len(rows) - 1})

    for element in document.xpath("//input[@type='submit' or @type='button' or @type='image']"):
        page.buttons.append(
            {
                "name": element.get("name", ""),
                "value": element.get("value", ""),
                "onclick": (element.get("onclick") or "")[:200],
            }
        )
    for element in document.xpath("//select[@name]"):
        options = [" ".join(option.text_content().split()) for option in element.xpath(".//option")]
        page.selects.append(
            {
                "name": element.get("name"),
                "options": options[:40],
                "option_count": len(options),
                "autopostback": "__doPostBack" in (element.get("onchange") or ""),
            }
        )
    for anchor in document.xpath("//a[@href]"):
        text = " ".join(anchor.text_content().split())
        page.links.append({"text": text[:80], "href": anchor.get("href", "")[:400]})

    seen: set[tuple[str, str]] = set()
    # Unescaped first: ASP.NET writes these into href attributes as &#39;…&#39;, which is why
    # the first survey found no Add New on a page that has one.
    for match in _POSTBACK.finditer(html.unescape(body)):
        key = (match.group(1), match.group(2))
        if key not in seen:
            seen.add(key)
            page.postbacks.append({"target": key[0], "argument": key[1]})

    text = " ".join(document.text_content().split())
    lowered = text.casefold()
    for keyword in KEYWORDS:
        hits = [m.start() for m in re.finditer(re.escape(keyword), lowered)]
        if hits:
            page.keywords[keyword] = [text[max(0, i - 60) : i + 80] for i in hits[:5]]


def discovered_urls(page: SurveyPage, body: str, base: str) -> list[tuple[str, str]]:
    """Same-site page URLs a person could reach from this page by clicking: (url, label)."""
    found: list[tuple[str, str]] = []
    for link in page.links:
        href = link["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        found.append((urljoin(base, href), link["text"]))
    for match in _SCRIPT_URL.finditer(body):
        found.append((urljoin(base, match.group(1)), "(script)"))
    return [
        (url, label)
        for url, label in found
        if urlsplit(url).path.casefold().endswith((".aspx", ".htm", ".html"))
    ]


# --- the walk --------------------------------------------------------------------------


@dataclass
class Survey:
    client: HttpClient
    auth: AuthManager
    guard: Guard
    out: Path
    delay: float = 1.0
    pages: list[SurveyPage] = field(default_factory=list)
    _last: float = 0.0

    def _pace(self) -> None:
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def _save(self, page: SurveyPage, response: httpx.Response) -> str:
        page.final_url = str(response.url)
        page.status = response.status_code
        page.content_type = response.headers.get("content-type", "")
        body = response.text if "html" in page.content_type or not page.content_type else ""
        page.bytes = len(response.content)
        if body:
            slug = re.sub(r"[^a-z0-9]+", "-", page.via.casefold()).strip("-")[:60]
            page.file = f"pages/{page.id}-{slug}.html"
            target = self.out / page.file
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
            describe_page(page, body)
        self.pages.append(page)
        logger.info("  {} {} {} — {}", page.id, page.status, page.title[:50], page.via[:70])
        return body

    def get(
        self, page_id: str, via: str, url: str, label: str = "", *, from_menu: bool = False
    ) -> Fetched:
        """GET a page through the guard. Returns (page, body), or None when refused."""
        try:
            self.guard.check_get(url, label, from_menu=from_menu)
        except Refused as exc:
            logger.info("  refused: {}", exc)
            return None
        page = SurveyPage(id=page_id, via=via, url=url)
        self._pace()
        try:
            response = self.auth.check_response(self.client.get(url, follow_redirects=True))
        except SessionExpiredError:
            raise
        except CerePulseError as exc:
            page.error = str(exc)
            self.pages.append(page)
            logger.warning("  {} failed: {}", page_id, exc)
            return None
        return page, self._save(page, response)

    def postback(
        self,
        page_id: str,
        via: str,
        url: str,
        state: WebFormsState,
        target: str,
        argument: str = "",
        *,
        select_value: str | None = None,
    ) -> Fetched:
        is_select = select_value is not None
        try:
            self.guard.check_postback(target, argument, is_select=is_select)
        except Refused as exc:
            logger.info("  refused: {}", exc)
            return None
        overrides = {target: select_value} if is_select and select_value is not None else {}
        data = state.postback(target, argument, **overrides)
        return self._post(page_id, via, url, data)

    def press(self, page_id: str, via: str, url: str, state: WebFormsState, button: str) -> Fetched:
        caption = state.buttons.get(button, "")
        try:
            self.guard.check_submit(button, caption)
        except Refused as exc:
            logger.info("  refused: {}", exc)
            return None
        return self._post(page_id, via, url, state.submit(button))

    def _post(self, page_id: str, via: str, url: str, data: dict[str, str]) -> Fetched:
        page = SurveyPage(id=page_id, via=via, url=url)
        self._pace()
        try:
            response = self.auth.check_response(
                self.client.post(url, data=data, follow_redirects=True)
            )
        except SessionExpiredError:
            raise
        except CerePulseError as exc:
            page.error = str(exc)
            self.pages.append(page)
            logger.warning("  {} failed: {}", page_id, exc)
            return None
        return page, self._save(page, response)


def run_survey(
    *, out_dir: str, secrets_path: str, config: AppConfig, depth: int = 2, delay: float = 1.0
) -> int:
    """Entry point for ``cerepulse survey``. Returns a process exit code."""
    try:
        username, password = resolve_credentials(Path(secrets_path), config)
    except MissingCredentialsError as exc:
        print(exc)
        return 2

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    host = urlsplit(config.portal.base_url).netloc.casefold()

    with workflow("survey"), HttpClient(config) as client:
        auth = AuthManager(client, config)
        auth.login(username, password)
        survey = Survey(client=client, auth=auth, guard=Guard(host=host), out=out, delay=delay)
        try:
            menu = _walk(survey, depth)
            _probe_comp_off(survey, menu)
        except SessionExpiredError as exc:
            logger.error("Stopped: the session ended ({})", exc)
        finally:
            auth.logout()

    (out / "survey.json").write_text(
        json.dumps([asdict(page) for page in survey.pages], indent=2), encoding="utf-8"
    )
    lines = []
    for page in survey.pages:
        for keyword, snippets in page.keywords.items():
            for snippet in snippets:
                lines.append(f"{page.id}\t{keyword}\t{page.title}\t{snippet}")
    (out / "keywords.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "refused.txt").write_text("\n".join(survey.guard.refused) + "\n", encoding="utf-8")
    logger.info(
        "Surveyed {} page(s), refused {} request(s); index in {}",
        len(survey.pages),
        len(survey.guard.refused),
        out / "survey.json",
    )
    return 0


def _walk(survey: Survey, depth: int) -> MenuIndex:
    home = survey.get("h000", "home", survey.client.url_for(pages.HOME), from_menu=True)
    if home is None:
        raise CerePulseError("The landing page could not be read")
    menu = parse_menu(home[1])
    logger.info("Walking {} menu entries, then links to depth {}", len(menu), depth)

    visited: set[str] = {_key(home[0].final_url)}
    queue: deque[tuple[str, str, str, int, bool]] = deque()
    for entry in menu:
        queue.append((entry.url, f"menu: {entry.full_name}", entry.label, 0, True))

    counter = 0
    while queue:
        url, via, label, level, from_menu = queue.popleft()
        key = _key(url)
        if key in visited:
            continue
        visited.add(key)
        counter += 1
        result = survey.get(f"p{counter:03d}", via, url, label, from_menu=from_menu)
        if result is None or level >= depth:
            continue
        page, body = result
        visited.add(_key(page.final_url))
        for found, text in discovered_urls(page, body, page.final_url or url):
            if _key(found) not in visited:
                via_link = f"link from {page.id}: {text or '(no text)'}"
                queue.append((found, via_link, text, level + 1, False))
    return menu


def _key(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.path.casefold()}?{parts.query}"


def _probe_comp_off(survey: Survey, menu: MenuIndex) -> None:
    """The questions the walk alone cannot reach: they sit behind view-only postbacks."""
    entry = menu.find("Apply", section="Leave > Leave")
    if entry is None:
        logger.warning("No Leave > Leave > Apply menu entry; comp-off probes skipped")
        return
    _probe_apply_form(survey, entry.url)
    for view in ("History", "Approved Applications", "Recent Applications"):
        _probe_co_minus_rows(survey, entry.url, view)


def _probe_apply_form(survey: Survey, list_url: str) -> None:
    """Add New → Type = CO- → read the adjustment dates and the balance; then Past History."""
    listed = survey.get("c001", "leave list (for Add New)", list_url, from_menu=True)
    if listed is None:
        return
    page, body = listed
    target = next((p["target"] for p in page.postbacks if p["argument"] == "Add New"), None)
    if target is None:
        logger.warning("The leave list offers no Add New postback")
        return
    state = WebFormsState.from_html(body)
    opened = survey.postback(
        "c002", "Add New (blank form)", page.final_url, state, target, "Add New"
    )
    if opened is None:
        return
    form_page, form_body = opened
    _probe_co_type(survey, "c003", form_page, form_body)


def _probe_co_type(survey: Survey, page_id: str, form_page: SurveyPage, form_body: str) -> None:
    state = WebFormsState.from_html(form_body)
    url = form_page.final_url
    document = lxml_html.fromstring(form_body)
    for select in document.xpath("//select[@name]"):
        for option in select.xpath(".//option"):
            label = " ".join(option.text_content().split())
            value = option.get("value", label)
            if label.upper().startswith("CO-") or value.upper().startswith("CO-"):
                name = str(select.get("name"))
                typed = survey.postback(
                    page_id, f"Type = {label}", url, state, name, select_value=value
                )
                if typed is not None:
                    _probe_past_history(survey, f"{page_id}h", *typed)
                return
    logger.warning("No CO- option on the form at {}", url)
    _probe_past_history(survey, f"{page_id}h", form_page, form_body)


def _probe_past_history(survey: Survey, page_id: str, page: SurveyPage, body: str) -> None:
    state = WebFormsState.from_html(body)
    for name, caption in state.buttons.items():
        if any(pattern.match(caption) for pattern in VIEW_SUBMITS):
            survey.press(page_id, f"Past History from {page.id}", page.final_url, state, name)
            return
    for found, label in discovered_urls(page, body, page.final_url):
        if "history" in label.casefold() or "history" in found.casefold():
            survey.get(page_id, f"Past History link from {page.id}", found, label)
            return


def _probe_co_minus_rows(survey: Survey, list_url: str, view: str) -> None:
    """Open each CO- application from the given status view, the way its Edit link does."""
    slug = re.sub(r"[^a-z]+", "", view.casefold())[:8]
    listed = survey.get(f"v-{slug}", f"leave list (for {view})", list_url, from_menu=True)
    if listed is None:
        return
    page, body = listed
    state = WebFormsState.from_html(body)
    select = next((n for n in state.fields if n.endswith("$cboReports")), None)
    if select is None:
        return
    shown = survey.postback(
        f"v-{slug}-0", f"leave list, {view}", page.final_url, state, select, select_value=view
    )
    if shown is None:
        return
    view_page, view_body = shown
    rows = comp_off_spend_rows(view_body)
    logger.info("  {} CO- application(s) in {}", len(rows), view)
    for index, (app_id, target, argument) in enumerate(rows):
        # Every open starts from a fresh render of the view: a postback's state is only good
        # for the page it came from.
        again = survey.postback(
            f"v-{slug}-{index + 1}a",
            f"leave list, {view} (again)",
            view_page.final_url,
            WebFormsState.from_html(view_body),
            select,
            select_value=view,
        )
        if again is None:
            continue
        opened = survey.postback(
            f"co-{app_id}",
            f"open CO- application {app_id} ({view})",
            again[0].final_url,
            WebFormsState.from_html(again[1]),
            target,
            argument,
        )
        if opened is not None:
            _probe_past_history(survey, f"co-{app_id}h", *opened)


__all__ = ["Guard", "Refused", "describe_page", "discovered_urls", "names_an_action", "run_survey"]
