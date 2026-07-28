from __future__ import annotations

from pathlib import Path

import pytest

from cerepulse.core.errors import ParserError
from cerepulse.transport.webforms import (
    DeltaRecord,
    WebFormsState,
    async_postback_payload,
    find_postback_targets,
    find_script_manager,
    find_update_panels,
    is_delta_response,
    parse_delta,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def login_html() -> str:
    return (FIXTURES / "login_page.html").read_text(encoding="utf-8")


@pytest.fixture
def login_state(login_html: str) -> WebFormsState:
    return WebFormsState.from_html(login_html)


# --- collecting form state ------------------------------------------------------------


def test_collects_hidden_state(login_state: WebFormsState) -> None:
    assert login_state.viewstate == "FAKEVIEWSTATE/abc+123=="
    assert login_state.event_validation == "FAKEEVENTVALIDATION=="
    assert login_state.fields["__VIEWSTATEGENERATOR"] == "C2EE9ABB"


def test_collects_the_encryption_salt(login_state: WebFormsState) -> None:
    """hEnSa must be discoverable — the whole password scheme depends on it."""
    assert login_state.require("hEnSa") == "1234567890123456"


def test_selects_contribute_their_selected_option(login_state: WebFormsState) -> None:
    assert login_state.fields["dpCompanyCodeList"] == "CEREBU"
    assert login_state.fields["dpConnectAs"] == "User"


def test_text_and_password_inputs_are_present_but_empty(login_state: WebFormsState) -> None:
    assert login_state.fields["txtUser"] == ""
    assert login_state.fields["txtPassword"] == ""


def test_unsubmittable_controls_are_excluded(login_state: WebFormsState) -> None:
    """Browsers don't post reset/submit buttons or unchecked boxes, so neither do we."""
    for name in ("btnUnused", "btnReset", "chkRemember"):
        assert name not in login_state.fields


def test_checked_box_is_included() -> None:
    html = """
      <form id="form1">
        <input type="checkbox" name="on" value="Y" checked="checked" />
        <input type="checkbox" name="off" value="Y" />
      </form>
    """
    fields = WebFormsState.from_html(html).fields
    assert fields == {"on": "Y"}


def test_form_action_is_captured(login_state: WebFormsState) -> None:
    assert login_state.action == "./login.aspx"


def test_missing_form_raises(login_state: WebFormsState) -> None:
    with pytest.raises(ParserError, match="No <form>"):
        WebFormsState.from_html("<html><body>no form here</body></html>")


def test_require_names_the_missing_field(login_state: WebFormsState) -> None:
    with pytest.raises(ParserError, match="nope"):
        login_state.require("nope")


def test_select_without_explicit_selection_falls_back_to_first() -> None:
    html = (
        "<form id='form1'><select name='s'><option value='a'>A</option>"
        "<option value='b'>B</option></select></form>"
    )
    assert WebFormsState.from_html(html).fields["s"] == "a"


# --- building postbacks ---------------------------------------------------------------


def test_postback_sets_event_fields_and_keeps_everything_else(login_state: WebFormsState) -> None:
    payload = login_state.postback("btnLogin")
    assert payload["__EVENTTARGET"] == "btnLogin"
    assert payload["__EVENTARGUMENT"] == ""
    assert payload["__VIEWSTATE"] == login_state.viewstate
    assert len(payload) == len(login_state.fields)


def test_postback_overrides_are_applied(login_state: WebFormsState) -> None:
    payload = login_state.postback("btnLogin", txtUser="CIPL00364", txtPassword="cipher")
    assert payload["txtUser"] == "CIPL00364"
    assert payload["txtPassword"] == "cipher"


def test_postback_does_not_mutate_the_state(login_state: WebFormsState) -> None:
    login_state.postback("btnLogin", txtUser="someone")
    assert login_state.fields["txtUser"] == ""


def test_async_postback_adds_the_script_manager_pairing(login_state: WebFormsState) -> None:
    payload = async_postback_payload(
        login_state,
        script_manager="ctl00$ScriptManager1",
        update_panel="ctl00$UpdatePanel1",
        target="ctl00$GridView1$ctl02$LnkDate",
    )
    assert payload["ctl00$ScriptManager1"] == "ctl00$UpdatePanel1|ctl00$GridView1$ctl02$LnkDate"
    assert payload["__EVENTTARGET"] == "ctl00$GridView1$ctl02$LnkDate"


# --- discovery ------------------------------------------------------------------------


def test_finds_postback_targets(login_html: str) -> None:
    assert find_postback_targets(login_html) == ["btnLogin"]


def test_filters_postback_targets_by_substring() -> None:
    html = """
      <a href="javascript:__doPostBack('ctl00$GridView1$ctl02$LnkDate','')">1</a>
      <a href="javascript:__doPostBack('ctl00$GridView1$ctl03$LnkDate','')">2</a>
      <a href="javascript:__doPostBack('ctl00$btnExport','')">x</a>
    """
    assert find_postback_targets(html, contains="LnkDate") == [
        "ctl00$GridView1$ctl02$LnkDate",
        "ctl00$GridView1$ctl03$LnkDate",
    ]


def test_finds_entity_encoded_postback_targets() -> None:
    """ASP.NET escapes the quotes when emitting __doPostBack into an href attribute.

    Regression test: scanning raw markup matched only the <script> occurrences and missed
    every clickable date in the attendance grid.
    """
    html = (
        '<a id="ctl00_BodyContentPlaceHolder_GridView1_ctl02_LnkDate" class="attenDay_aLink" '
        'href="javascript:__doPostBack(&#39;ctl00$BodyContentPlaceHolder$GridView1$ctl02'
        '$LnkDate&#39;,&#39;&#39;)">01-Jul-26</a>'
    )
    assert find_postback_targets(html, contains="LnkDate") == [
        "ctl00$BodyContentPlaceHolder$GridView1$ctl02$LnkDate"
    ]


def test_finds_script_manager_and_panels() -> None:
    html = (
        "Sys.WebForms.PageRequestManager._initialize('ctl00$ScriptManager1', 'form1', "
        "[['tctl00$BodyContentPlaceHolder$UpdatePanel1',''],"
        "['tctl00$BodyContentPlaceHolder$UpdatePanel2','']], [], [], 90);"
    )
    assert find_script_manager(html) == "ctl00$ScriptManager1"
    assert find_update_panels(html) == [
        "ctl00$BodyContentPlaceHolder$UpdatePanel1",
        "ctl00$BodyContentPlaceHolder$UpdatePanel2",
    ]


def test_no_script_manager_returns_none() -> None:
    assert find_script_manager("<html></html>") is None
    assert find_update_panels("<html></html>") == []


# --- delta responses ------------------------------------------------------------------


def test_parses_a_delta_response() -> None:
    body = "16|updatePanel|panel1|<div>hello</div>|8|hiddenField|__VIEWSTATE|NEWSTATE|"
    assert parse_delta(body) == [
        DeltaRecord(type="updatePanel", id="panel1", content="<div>hello</div>"),
        DeltaRecord(type="hiddenField", id="__VIEWSTATE", content="NEWSTATE"),
    ]


def test_content_containing_pipes_is_preserved() -> None:
    """The length prefix is why we can't just split on '|' — real panels contain pipes."""
    content = "<td>a|b</td><td>c|d</td>"
    records = parse_delta(f"{len(content)}|updatePanel|p1|{content}|")
    assert records[0].content == content


def test_empty_body_yields_no_records() -> None:
    assert parse_delta("") == []


def test_truncated_content_is_rejected() -> None:
    with pytest.raises(ParserError, match="shorter than its declared length"):
        parse_delta("99|updatePanel|p1|too short|")


def test_non_numeric_length_is_rejected() -> None:
    with pytest.raises(ParserError, match="expected a length"):
        parse_delta("abc|updatePanel|p1|content|")


def test_delta_detection() -> None:
    assert is_delta_response("12|updatePanel|p1|<div>hello</div>|") is True
    assert is_delta_response("<!DOCTYPE html><html>...") is False
    assert is_delta_response("") is False


# --- keeping state fresh --------------------------------------------------------------


def test_delta_refreshes_hidden_fields(login_state: WebFormsState) -> None:
    updated = login_state.update_from_delta(
        [
            DeltaRecord(type="hiddenField", id="__VIEWSTATE", content="ROTATED"),
            DeltaRecord(type="hiddenField", id="__EVENTVALIDATION", content="ALSO_ROTATED"),
            DeltaRecord(type="updatePanel", id="panel1", content="<div/>"),
        ]
    )
    assert updated == 2
    assert login_state.viewstate == "ROTATED"
    assert login_state.event_validation == "ALSO_ROTATED"


def test_delta_without_hidden_fields_is_not_an_error(login_state: WebFormsState) -> None:
    original = login_state.viewstate
    assert login_state.update_from_delta([DeltaRecord("updatePanel", "p1", "<div/>")]) == 0
    assert login_state.viewstate == original


def test_update_from_html_replaces_state(login_state: WebFormsState) -> None:
    login_state.update_from_html(
        "<form id='form1'><input type='hidden' name='__VIEWSTATE' value='FRESH'/></form>"
    )
    assert login_state.viewstate == "FRESH"
    assert "hEnSa" not in login_state.fields
