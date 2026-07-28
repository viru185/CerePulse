"""Menu parsing — load-bearing, because pages are unreachable without their token."""

from __future__ import annotations

import pytest

from cerepulse.core.errors import ParserError
from cerepulse.parsers.menu import parse_menu

# Mirrors the real landing page's structure; tokens are fabricated.
HOST = "https://cerebulb.spinehr.in"

MENU_HTML = f"""
<nav>
  <a data-firstlvl='10000' data-secondlvl='10100' data-thirdlvl-p='10101'
     data-MenuNav1_2='Time &gt; Attendance' data-MenuNav3='My Attendance'
     href='{HOST}/Atten/MyAttendanceReport.aspx?mnusr=menu__10101'>My Attendance</a>
  <a href='#' class='menu-separator'>&nbsp;|&nbsp;</a>
  <a data-MenuNav1_2='Time &gt; Swipe' data-MenuNav3='Apply'
     href='{HOST}/Atten/SwipeRequestList.aspx?mnusr=menu__10201'>Apply</a>
  <a data-MenuNav1_2='Leave &gt; Leave' data-MenuNav3='Apply'
     href='{HOST}/Leave/LeaveList.aspx?mnusr=menu__9201'>Apply</a>
  <a data-MenuNav1_2='Leave &gt; My Info' data-MenuNav3='My Leave Register'
     href='{HOST}/Leave/LeaveBalanceDetail.aspx?reqFor=OPAQUE&amp;mnusr=menu__9102'>Register</a>
  <a href='{HOST}/LogOff.aspx'>Log off</a>
</nav>
"""


@pytest.fixture
def menu():  # type: ignore[no-untyped-def]
    return parse_menu(MENU_HTML)


def test_indexes_only_privilege_bearing_links(menu) -> None:  # type: ignore[no-untyped-def]
    """Plain links like Log off carry no mnusr token and are not menu destinations."""
    assert len(menu) == 4
    assert all("mnusr=" in entry.url for entry in menu)


def test_resolves_the_attendance_page(menu) -> None:  # type: ignore[no-untyped-def]
    entry = menu.require("My Attendance", section="Time > Attendance")
    assert entry.url.endswith("/Atten/MyAttendanceReport.aspx?mnusr=menu__10101")
    assert entry.menu_id == "menu__10101"


def test_label_prefers_the_data_attribute_over_link_text(menu) -> None:  # type: ignore[no-untyped-def]
    """The visible text is 'Register'; the menu name is 'My Leave Register'."""
    assert menu.find("My Leave Register") is not None
    assert menu.find("Register") is None


def test_section_disambiguates_duplicate_labels(menu) -> None:  # type: ignore[no-untyped-def]
    """'Apply' exists under both Swipe and Leave — section is what separates them."""
    swipe = menu.require("Apply", section="Time > Swipe")
    leave = menu.require("Apply", section="Leave > Leave")
    assert "SwipeRequestList" in swipe.url
    assert "LeaveList" in leave.url


def test_lookup_is_case_insensitive(menu) -> None:  # type: ignore[no-untyped-def]
    assert menu.find("my attendance") is not None


def test_opaque_extra_parameters_are_preserved(menu) -> None:  # type: ignore[no-untyped-def]
    """reqFor is a server-side blob we can neither decode nor invent — carry it verbatim."""
    entry = menu.require("My Leave Register")
    assert "reqFor=OPAQUE" in entry.url
    assert entry.menu_id == "menu__9102"


def test_missing_entry_lists_what_is_available(menu) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ParserError, match="Available labels include"):
        menu.require("Payslip")


def test_wrong_section_is_a_miss(menu) -> None:  # type: ignore[no-untyped-def]
    assert menu.find("My Attendance", section="Leave > Leave") is None


def test_page_without_a_menu_raises() -> None:
    with pytest.raises(ParserError, match="No navigation menu links"):
        parse_menu("<html><body><a href='/x.aspx'>x</a></body></html>")


def test_full_name_reads_as_a_breadcrumb(menu) -> None:  # type: ignore[no-untyped-def]
    assert menu.require("My Attendance").full_name == "Time > Attendance | My Attendance"
