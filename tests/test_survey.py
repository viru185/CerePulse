"""The survey may look at anything and change nothing.

The guard is the whole of that promise, so it is tested on its own: every action-shaped
request a portal page could offer is refused before a byte is sent, and the handful of
view-only postbacks the comp-off questions need are the only ones let through.
"""

from __future__ import annotations

import pytest

from cerepulse.survey import Guard, Refused, SurveyPage, describe_page, discovered_urls
from cerepulse.survey import _co_minus_rows as co_minus_rows

HOST = "cerebulb.spinehr.in"
BASE = f"https://{HOST}"


@pytest.fixture
def guard() -> Guard:
    return Guard(host=HOST)


@pytest.mark.parametrize(
    "url",
    [
        f"{BASE}/Leave/LeaveList.aspx?mnusr=menu__9201",
        f"{BASE}/Leave/LeaveCancelList.aspx?mnusr=menu__9203",  # "Cancellation" is a list
        f"{BASE}/InvDecSheet.aspx?mnusr=menu__3008",
        f"{BASE}/Leave/EmpLeaveApp.aspx?LeaveAppType=abc&trno=1&UnqId=2",
    ],
)
def test_menu_pages_may_be_read(guard: Guard, url: str) -> None:
    guard.check_get(url, "Submit Tax Declaration - Sheet", from_menu=True)


@pytest.mark.parametrize(
    "url",
    [
        f"{BASE}/LogOff.aspx",
        f"{BASE}/logout.aspx?x=1",
        f"{BASE}/WF/Approve.aspx?id=7",
        f"{BASE}/Leave/DeleteApp.aspx?id=7",
        f"{BASE}/Upload.aspx",
        f"{BASE}/Leave/LeaveList.aspx?action=withdraw&id=7",
        "https://elsewhere.example.com/page.aspx",
        "javascript:void(0)",
    ],
)
def test_links_whose_url_names_an_action_or_leaves_the_site_are_refused(
    guard: Guard, url: str
) -> None:
    with pytest.raises(Refused):
        guard.check_get(url, "")
    assert guard.refused


def test_a_menu_page_is_read_whatever_its_name_but_signing_out_is_not(guard: Guard) -> None:
    guard.check_get(f"{BASE}/MyApprover.aspx?mnusr=menu__6001", "My Approvers", from_menu=True)
    guard.check_get(f"{BASE}/WF/WFApproverSumm.aspx?pg=lfse", "My Workflow", from_menu=True)
    with pytest.raises(Refused):
        guard.check_get(f"{BASE}/LogOff.aspx", "Log Off", from_menu=True)
    with pytest.raises(Refused):
        guard.check_get("https://elsewhere.example.com/x.aspx", "x", from_menu=True)


@pytest.mark.parametrize("label", ["Delete", "Withdraw", "Cancel", "Save", "Log Off", "Approve"])
def test_links_inside_pages_are_judged_by_their_words(guard: Guard, label: str) -> None:
    with pytest.raises(Refused):
        guard.check_get(f"{BASE}/Leave/Something.aspx", label)


def test_a_word_inside_a_longer_word_is_not_an_action(guard: Guard) -> None:
    guard.check_get(f"{BASE}/Leave/LeaveCancelList.aspx", "Cancellation")
    guard.check_get(f"{BASE}/Leave/x.aspx", "Updates and news")  # "update" stands alone only


@pytest.mark.parametrize(
    ("target", "argument"),
    [
        ("ctl00$BodyContentPlaceHolder$cboReports", ""),
        ("ctl00$BodyContentPlaceHolder$Menu1", "Add New"),
        ("ctl00$BodyContentPlaceHolder$GridView1", "Select$3"),
    ],
)
def test_view_only_postbacks_are_allowed(guard: Guard, target: str, argument: str) -> None:
    guard.check_postback(target, argument)


@pytest.mark.parametrize(
    ("target", "argument"),
    [
        ("ctl00$BodyContentPlaceHolder$btnSave", ""),
        ("ctl00$BodyContentPlaceHolder$Menu1", "Delete"),
        ("ctl00$BodyContentPlaceHolder$GridView1", "Delete$3"),
        ("ctl00$BodyContentPlaceHolder$GridView1", "Cancel$3"),
        ("ctl00$LogOff", ""),
    ],
)
def test_every_other_postback_is_refused(guard: Guard, target: str, argument: str) -> None:
    with pytest.raises(Refused):
        guard.check_postback(target, argument)


def test_a_dropdown_change_is_a_view_unless_its_name_is_an_action(guard: Guard) -> None:
    guard.check_postback("ctl00$BodyContentPlaceHolder$ddlLeaveType", "", is_select=True)
    with pytest.raises(Refused):
        guard.check_postback("ctl00$BodyContentPlaceHolder$Delete", "", is_select=True)


@pytest.mark.parametrize("caption", ["Save", "Submit", "Apply", "Update", "Cancel", "Upload"])
def test_only_past_history_may_be_pressed(guard: Guard, caption: str) -> None:
    guard.check_submit("ctl00$BodyContentPlaceHolder$btnPastHistory", "Past History")
    with pytest.raises(Refused):
        guard.check_submit("ctl00$BodyContentPlaceHolder$btn", caption)


PAGE = """
<html><head><title> Leave &gt; Apply </title></head><body><form id="form1">
<h1>Leave Apply</h1>
<select name="ctl00$Body$ddlType"
  onchange="javascript:setTimeout('__doPostBack(\\'x\\',\\'\\')', 0)">
  <option value="PL">PL</option><option value="CO-">CO-</option></select>
<select name="ctl00$Body$ddlCOAdj"><option>02-Oct-26</option><option>03-Oct-26</option></select>
<input type="submit" name="ctl00$Body$btnSave" value="Save" />
<input type="submit" name="ctl00$Body$btnPH" value="Past History" />
<span>CO Balance As On Date 23-Nov-2026 : 5</span>
<table id="GridView1"><tr><th>Edit</th><th>App. Id</th><th>Leave Type</th></tr>
<tr><td><a href="javascript:__doPostBack('ctl00$Body$GridView1','Select$0')">Edit</a></td>
    <td>8728</td><td>CO-</td></tr>
<tr><td><a href="javascript:__doPostBack('ctl00$Body$GridView1','Select$1')">Edit</a></td>
    <td>7101</td><td>CF</td></tr></table>
<a href="/Leave/History.aspx?id=1">History</a>
<a href="#">Top</a>
<a onclick="window.open('/Leave/PastHistory.aspx?e=1')">Past History</a>
</form></body></html>
"""


def test_a_page_is_indexed_for_what_a_reader_would_search(guard: Guard) -> None:
    page = SurveyPage(id="p001", via="test", url=f"{BASE}/Leave/EmpLeaveApp.aspx")
    describe_page(page, PAGE)

    assert page.title == "Leave > Apply"
    assert {"name": "ctl00$Body$ddlCOAdj", "options": ["02-Oct-26", "03-Oct-26"],
            "option_count": 2, "autopostback": False} in page.selects  # fmt: skip
    assert page.tables[0]["headers"] == ["Edit", "App. Id", "Leave Type"]
    assert any(button["value"] == "Past History" for button in page.buttons)
    assert "balance as on" in page.keywords
    assert {"target": "ctl00$Body$GridView1", "argument": "Select$0"} in page.postbacks


def test_links_found_on_a_page_include_script_opened_windows() -> None:
    page = SurveyPage(id="p001", via="test", url=f"{BASE}/Leave/EmpLeaveApp.aspx")
    describe_page(page, PAGE)
    found = dict(discovered_urls(page, PAGE, f"{BASE}/Leave/EmpLeaveApp.aspx"))

    assert f"{BASE}/Leave/History.aspx?id=1" in found
    assert f"{BASE}/Leave/PastHistory.aspx?e=1" in found
    assert not any(url.endswith("#") for url in found)


def test_only_co_minus_rows_are_opened() -> None:
    assert co_minus_rows(PAGE) == [("8728", "ctl00$Body$GridView1", "Select$0")]
