"""The Salary pages, on synthetic HTML shaped exactly like the 6 September 2026 captures."""

from __future__ import annotations

import pytest

from cerepulse.core.errors import ParserError
from cerepulse.parsers.pay import (
    monthly_periods,
    parse_ctc,
    parse_money,
    parse_monthly,
    parse_payslip,
    payslip_periods,
    payslip_template_name,
    period_to_date,
    template_macros,
)

CTC = """
<html><body>
<table class="SnowyPine"><tr><td>
  <table>
    <tr><td>For Year : 2026 - 2027</td></tr>
    <tr><td>Employee Code : X001</td><td>Employee Name : Someone</td></tr>
  </table>
</td></tr></table>
<table>
  <tr><td>Earnings</td><td></td><td>Monthly Amount</td><td>Yearly Amount</td></tr>
  <tr><td>E Basic</td><td></td><td>10,000.00</td><td>1,20,000.00</td></tr>
  <tr><td>E HRA</td><td></td><td>4,000.00</td><td>48,000.00</td></tr>
  <tr><td>(A) Total Gross</td><td>14,000.00</td><td>1,68,000.00</td></tr>
  <tr><td>Outside Payroll Benefits</td><td></td><td></td><td></td></tr>
  <tr><td>Yearly Bonus</td><td></td><td>833.33</td><td>10,000.00</td></tr>
  <tr><td>(C) Total Outside Payroll Benefits</td><td>833.33</td><td>10,000.00</td></tr>
  <tr><td>(A+C) Cost To Company</td><td>14,833.33</td><td>1,78,000.00</td></tr>
</table>
</body></html>
"""

MONTHLY = """
<html><body>
<select id="ctl00_BodyContentPlaceHolder_drpPeriod" name="ctl00$BodyContentPlaceHolder$drpPeriod">
  <option selected="selected" value="202604">April , 2026 - March , 2027</option>
  <option value="202602">February , 2026 - March , 2026</option>
</select>
<table id="ctl00_BodyContentPlaceHolder_GridView1" class="tblGrid">
  <tr><th>Particulars</th><th>Apr 2026</th><th>May 2026</th><th>Total</th></tr>
  <tr><td>E Basic</td><td>10,000.00</td><td>10,000.00</td><td>20,000.00</td></tr>
  <tr><td>PROV.FUND</td><td>1,200.00</td><td></td><td>1,200.00</td></tr>
  <tr><td>Net Salary</td><td>8,800.00</td><td>10,000.00</td><td>18,800.00</td></tr>
</table>
</body></html>
"""

PAYSLIP_PAGE = """
<html><body>
<select id="ctl00_BodyContentPlaceHolder_drpPeriod">
  <option selected="selected" value="202607">July , 2026</option>
  <option value="202606">June , 2026</option>
</select>
<input type="hidden" id="ctl00_BodyContentPlaceHolder_hdnPayF" value="Format2.html" />
<input type="hidden" id="ctl00_BodyContentPlaceHolder_hdnEmpPanNo" value="ABCDE1234F" />
</body></html>
"""

TEMPLATE = """
<div id="tdPrint"><span data-macro="@@emp_code"></span>
<div data-macro="@@AllEarnings"></div><div data-macro="@@AllEarnings"></div></div>
"""

EARNINGS = """
<table class="table table-bordered tb-outborder">
<tr><th>Earnings</th><th>Amount</th><th>Deductions &amp; Recoveries</th><th>Amount</th>
    <th>Variables</th><th>Amount</th></tr>
<tr><td>E Basic</td><td>10,000.00</td><td>PROV.FUND</td><td>1,200.00</td>
    <td>P.F Column</td><td>10,000.00</td></tr>
<tr><td>E HRA</td><td>4,000.00</td><td>E.S.I.C</td><td>105.00</td>
    <td>CTC</td><td>14,833.33</td></tr>
<tr><td>CONVEYANCE ALLOW</td><td>1,600.00</td><td></td><td></td>
    <td>Gross Basic</td><td>10,000.00</td></tr>
<tr><td>Amount Total :</td><td>15,600.00</td><td>Amount Total :</td><td>1,305.00</td>
    <td></td><td></td></tr>
<tr><td>Net Pay :</td><td>14,295.00</td><td></td><td></td><td></td><td></td></tr>
<tr><td colspan="6">Net Pay : Fourteen Thousand Two Hundred Ninety Five Only</td></tr>
</table>
"""


def _slip_payload(table: str = EARNINGS) -> dict[str, object]:
    return {
        "d": {
            "isSuccess": True,
            "data": {
                "290|202607": [
                    {"MacroName": "@@emp_code", "MacroValue": "X001"},
                    {"MacroName": "@@AllEarnings", "MacroValue": table},
                ]
            },
        }
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,23,456.00", 123456.0),
        ("14,833.33", 14833.33),
        ("833.33", 833.33),
        ("", None),
        ("—", None),
        ("-1,200.00", -1200.0),
    ],
)
def test_money_reads_indian_grouping(text: str, expected: float | None) -> None:
    assert parse_money(text) == expected


def test_ctc_reads_sections_and_totals() -> None:
    ctc = parse_ctc(CTC)
    assert ctc.for_year == "2026-2027"
    assert [line.label for line in ctc.lines] == [
        "E Basic",
        "E HRA",
        "(A) Total Gross",
        "Yearly Bonus",
        "(C) Total Outside Payroll Benefits",
        "(A+C) Cost To Company",
    ]
    assert ctc.lines[0].section == "Earnings"
    assert ctc.lines[3].section == "Outside Payroll Benefits"
    assert ctc.cost_to_company is not None
    assert ctc.cost_to_company.yearly == 178000.0
    assert ctc.cost_to_company.monthly == 14833.33
    assert ctc.gross is not None and ctc.gross.yearly == 168000.0
    assert [line.is_total for line in ctc.lines] == [False, False, True, False, True, True]


def test_ctc_without_the_table_is_a_parser_error() -> None:
    with pytest.raises(ParserError):
        parse_ctc("<html><body><p>Runtime Error</p></body></html>")


def test_monthly_grid_reads_one_row_per_component() -> None:
    report = parse_monthly(MONTHLY, period_label="April , 2026 - March , 2027")
    assert report.months == ("Apr 2026", "May 2026", "Total")
    assert [row.label for row in report.rows] == ["E Basic", "PROV.FUND", "Net Salary"]
    assert report.rows[1].values == (1200.0, None, 1200.0)
    assert report.row("net") is not None and report.row("net").values[-1] == 18800.0
    assert monthly_periods(MONTHLY)[0] == ("202604", "April , 2026 - March , 2027")


def test_monthly_grid_missing_means_the_refresh_was_not_posted() -> None:
    with pytest.raises(ParserError, match="Refresh"):
        parse_monthly("<html><body><p>Select a period</p></body></html>")


def test_payslip_page_offers_periods_and_a_template() -> None:
    assert payslip_periods(PAYSLIP_PAGE) == [("202607", "July , 2026"), ("202606", "June , 2026")]
    assert payslip_template_name(PAYSLIP_PAGE) == "Format2.html"
    assert template_macros(TEMPLATE) == ["@@emp_code", "@@AllEarnings"]
    assert period_to_date("202607").isoformat() == "2026-07-01"


def test_payslip_splits_the_three_columns_and_reads_the_totals() -> None:
    slip = parse_payslip(_slip_payload(), period="202607")
    assert slip.label == "July 2026"
    assert [(line.label, line.amount) for line in slip.earnings] == [
        ("E Basic", 10000.0),
        ("E HRA", 4000.0),
        ("CONVEYANCE ALLOW", 1600.0),
    ]
    assert [(line.label, line.amount) for line in slip.deductions] == [
        ("PROV.FUND", 1200.0),
        ("E.S.I.C", 105.0),
    ]
    assert [line.label for line in slip.variables] == ["P.F Column", "CTC", "Gross Basic"]
    assert slip.gross == 15600.0
    assert slip.total_deductions == 1305.0
    assert slip.net_pay == 14295.0
    assert slip.in_words == "Fourteen Thousand Two Hundred Ninety Five Only"


def test_payslip_failure_flag_is_a_parser_error() -> None:
    with pytest.raises(ParserError):
        parse_payslip({"d": {"isSuccess": False, "data": {}}}, period="202607")
    with pytest.raises(ParserError):
        parse_payslip(_slip_payload(table=""), period="202607")
    with pytest.raises(ParserError):
        parse_payslip(_slip_payload(), period="July")
