"""Development tool: sign in and dump raw portal HTML as parser fixtures.

The architecture docs describe the protocol abstractly but contain no real HTML, so the
attendance and leave parsers cannot be written blind. This command produces the fixtures
they are written against, and proves the login path end to end while doing it.

Pages are reached **through the menu**, not by hard-coded path: the portal enforces a
server-side role check and returns a privileges error for a direct request. See
:mod:`cerepulse.parsers.menu`.

Credentials come from a gitignored ``.secrets.toml``::

    [portal]
    username = "..."
    password = "..."

Output goes to ``Research/captures/`` (gitignored), alongside ``manifest.json`` and
``menu.json`` summarizing what was found.
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from cerepulse.auth.manager import AuthManager
from cerepulse.core import secrets
from cerepulse.core.config import AppConfig
from cerepulse.core.errors import CerePulseError, ParserError
from cerepulse.core.logging_setup import workflow
from cerepulse.parsers.menu import MenuIndex, parse_menu
from cerepulse.transport import pages
from cerepulse.transport.client import HttpClient
from cerepulse.transport.webforms import (
    WebFormsState,
    async_postback_headers,
    async_postback_payload,
    find_postback_targets,
    find_script_manager,
    find_update_panels,
    is_delta_response,
    parse_delta,
)

#: Pages to capture, as (output name, menu label, menu section).
TARGETS: tuple[tuple[str, str, str], ...] = (
    ("attendance_report", "My Attendance", "Time > Attendance"),
    ("attendance_calendar", "Opt your Holiday", "Time > Attendance"),
    ("swipe_requests", "Apply", "Time > Swipe"),
    ("leave_balance", "Entitlement", "Leave > My Info"),
    ("leave_register", "My Leave Register", "Leave > My Info"),
    ("leave_list", "Apply", "Leave > Leave"),
    ("holiday_list", "Holiday List", "Self Service > Quick Info"),
)

#: Text the portal renders when a page is requested without its privilege token.
PRIVILEGE_ERROR = "sufficient  privileges"


class MissingCredentialsError(CerePulseError):
    """No portal credentials could be resolved from any source."""


@dataclass
class PageReport:
    """What we learned about one captured page — the useful half of the output."""

    name: str
    url: str
    status: int
    bytes: int
    file: str = ""
    form_fields: int = 0
    script_manager: str | None = None
    update_panels: list[str] = field(default_factory=list)
    date_targets: int = 0
    sample_date_target: str | None = None
    tables: int = 0
    error: str | None = None


def run_capture(*, out_dir: str, secrets_path: str, config: AppConfig) -> int:
    """Entry point for ``cerepulse capture``. Returns a process exit code."""
    try:
        username, password = _resolve_credentials(Path(secrets_path), config)
    except MissingCredentialsError as exc:
        # Printed rather than logged: this is setup guidance, and the log redaction
        # filter would (correctly, but unhelpfully) scrub the example password line.
        print(exc, file=sys.stderr)
        return 2

    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    reports: list[PageReport] = []

    with workflow("capture"), HttpClient(config) as client:
        auth = AuthManager(client, config)

        logger.info("Signing in as {} ...", username)
        try:
            auth.login(username, password)
        except CerePulseError as exc:
            logger.error("Login failed: {}", exc)
            return 1
        logger.success("Login succeeded — the AES password scheme is correct.")

        try:
            menu = _capture_menu(client, auth, destination, reports)
        except CerePulseError as exc:
            logger.error("Could not read the navigation menu: {}", exc)
            auth.logout()
            return 1

        for name, label, section in TARGETS:
            reports.append(_capture_page(client, auth, menu, name, label, section, destination))

        reports.append(_capture_day_detail(client, auth, menu, destination))
        auth.logout()

    manifest = destination / "manifest.json"
    manifest.write_text(
        json.dumps([asdict(report) for report in reports], indent=2), encoding="utf-8"
    )
    _summarize(reports, manifest)
    return 0 if all(report.error is None for report in reports) else 1


# --- individual captures --------------------------------------------------------------


def _capture_menu(
    client: HttpClient, auth: AuthManager, destination: Path, reports: list[PageReport]
) -> MenuIndex:
    """Fetch the landing page and index its menu — every later URL comes from here."""
    logger.info("Capturing the landing page and navigation menu")
    response = auth.check_response(client.get(pages.HOME))
    body = response.text
    (destination / "home.html").write_text(body, encoding="utf-8")

    menu = parse_menu(body)
    (destination / "menu.json").write_text(
        json.dumps([asdict(entry) for entry in menu], indent=2), encoding="utf-8"
    )
    logger.info("  {} menu entries indexed", len(menu))

    reports.append(
        PageReport(
            name="home",
            url=pages.HOME,
            status=response.status_code,
            bytes=len(body),
            file="home.html",
            tables=body.lower().count("<table"),
        )
    )
    return menu


def _capture_page(
    client: HttpClient,
    auth: AuthManager,
    menu: MenuIndex,
    name: str,
    label: str,
    section: str,
    destination: Path,
) -> PageReport:
    logger.info("Capturing {} ({} > {})", name, section, label)
    try:
        entry = menu.require(label, section=section)
    except ParserError as exc:
        logger.error("  {}", exc)
        return PageReport(name=name, url="", status=0, bytes=0, error=str(exc))

    try:
        # Follow redirects here: several menu entries are aliases that bounce to a
        # canonical page (BalanceLeave.aspx -> LeaveBalanceDetail.aspx). A bounce to the
        # login page still surfaces, because check_response also inspects 200 bodies.
        response = auth.check_response(client.get(entry.url, follow_redirects=True))
    except CerePulseError as exc:
        logger.error("  failed: {}", exc)
        return PageReport(name=name, url=entry.url, status=0, bytes=0, error=str(exc))

    body = response.text
    target_file = destination / f"{name}.html"
    target_file.write_text(body, encoding="utf-8")

    report = PageReport(
        name=name,
        url=entry.url,
        status=response.status_code,
        bytes=len(body),
        file=target_file.name,
        script_manager=find_script_manager(body),
        update_panels=find_update_panels(body),
        tables=body.lower().count("<table"),
    )

    date_targets = find_postback_targets(body, contains="LnkDate")
    report.date_targets = len(date_targets)
    report.sample_date_target = date_targets[-1] if date_targets else None

    try:
        report.form_fields = len(WebFormsState.from_html(body).fields)
    except CerePulseError:
        report.form_fields = 0

    if PRIVILEGE_ERROR in body:
        report.error = "Portal returned a privileges error — the menu token may be stale"
        logger.error("  {}", report.error)
    else:
        logger.info(
            "  {} bytes, {} form fields, {} tables, {} date links",
            report.bytes,
            report.form_fields,
            report.tables,
            report.date_targets,
        )
    return report


def _capture_day_detail(
    client: HttpClient, auth: AuthManager, menu: MenuIndex, destination: Path
) -> PageReport:
    """Click the most recent date in the attendance grid and save the delta response.

    This is the only way to see the day-detail panels (punches, leave, swipe requests),
    since they arrive as a partial update rather than in the initial page.
    """
    logger.info("Capturing day detail via an async postback")
    name = "day_detail"

    try:
        entry = menu.require("My Attendance", section="Time > Attendance")
        page = auth.check_response(client.get(entry.url, follow_redirects=True))
    except CerePulseError as exc:
        return PageReport(name=name, url="", status=0, bytes=0, error=str(exc))

    body = page.text
    targets = find_postback_targets(body, contains="LnkDate")
    script_manager = find_script_manager(body)
    panels = find_update_panels(body)

    if not targets:
        message = "No LnkDate postback targets on the attendance page"
        logger.warning("  {}", message)
        return PageReport(name=name, url=entry.url, status=page.status_code, bytes=0, error=message)
    if not script_manager:
        message = "No ScriptManager found; cannot form an async postback"
        logger.warning("  {}", message)
        return PageReport(name=name, url=entry.url, status=page.status_code, bytes=0, error=message)

    target = targets[-1]
    payload = async_postback_payload(
        WebFormsState.from_html(body),
        script_manager=script_manager,
        update_panel=panels[0] if panels else "",
        target=target,
    )
    response = client.post(
        entry.url, data=payload, headers=async_postback_headers(client.url_for(entry.url))
    )

    delta_body = response.text
    (destination / "day_detail_delta.txt").write_text(delta_body, encoding="utf-8")

    report = PageReport(
        name=name,
        url=entry.url,
        status=response.status_code,
        bytes=len(delta_body),
        file="day_detail_delta.txt",
        script_manager=script_manager,
        update_panels=panels,
        date_targets=len(targets),
        sample_date_target=target,
    )

    if is_delta_response(delta_body):
        records = parse_delta(delta_body)
        logger.info("  delta parsed: {} records", len(records))
        (destination / "day_detail_records.json").write_text(
            json.dumps(
                [{"type": r.type, "id": r.id, "bytes": len(r.content)} for r in records], indent=2
            ),
            encoding="utf-8",
        )
    else:
        report.error = "Response was not in delta format (check the ScriptManager/panel pair)"
        logger.warning("  {}", report.error)
    return report


# --- credentials ----------------------------------------------------------------------


def _resolve_credentials(secrets_path: Path, config: AppConfig) -> tuple[str, str]:
    """Resolve credentials from the secrets file, then the environment, then the keyring."""
    if secrets_path.exists():
        with secrets_path.open("rb") as handle:
            data: dict[str, Any] = tomllib.load(handle)
        portal = data.get("portal", data)
        username = str(portal.get("username", "")).strip()
        password = str(portal.get("password", ""))
        if username and password:
            logger.debug("Using credentials from {}", secrets_path)
            return username, password

    username = os.environ.get("CEREPULSE_USERNAME", config.portal.username).strip()
    password = os.environ.get("CEREPULSE_PASSWORD", "")
    if username and password:
        logger.debug("Using credentials from the environment")
        return username, password

    if username:
        stored = secrets.get_password(username)
        if stored:
            logger.debug("Using credentials from the Windows Credential Manager")
            return username, stored

    raise MissingCredentialsError(
        f"No portal credentials found.\n\n"
        f"  Copy .secrets.example.toml to {secrets_path} and fill in your SpineHR\n"
        f"  username and password. That file is gitignored.\n\n"
        f"  Alternatively set the CEREPULSE_USERNAME and CEREPULSE_PASSWORD\n"
        f"  environment variables."
    )


def _summarize(reports: list[PageReport], manifest: Path) -> None:
    logger.info("=" * 74)
    for report in reports:
        if report.error:
            logger.error("{:<22} FAILED  {}", report.name, report.error)
        else:
            logger.success(
                "{:<22} {:>4}  {:>8} bytes  {:>3} tables  {:>3} date links",
                report.name,
                report.status,
                report.bytes,
                report.tables,
                report.date_targets,
            )
    logger.info("Manifest written to {}", manifest)
