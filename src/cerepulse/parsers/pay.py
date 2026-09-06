"""The Salary pages, read from captures taken on 6 September 2026.

Three pages, three shapes:

* **CTC** (``CTCReport.aspx``) renders on a plain GET: an employee block, then a table of
  ``Earnings | Monthly Amount | Yearly Amount`` with subtotal rows — ``(A) Total Gross``,
  ``(C) Total Outside Payroll Benefits``, ``(A+C) Cost To Company``.
* **Monthly Report** (``MonthlyCompSummRpt.aspx``) renders nothing until its Refresh is
  posted; then ``GridView1`` is ``Particulars`` by month, one row per component.
* **Payslip** (``PrintPayslipNX.aspx``) is not a page at all but two JSON page methods:
  ``GetTemplate`` returns the slip's HTML template, ``GetSalarySlipData`` the values for its
  ``data-macro`` placeholders. ``@@AllEarnings`` is itself an HTML table of three column
  pairs — earnings, deductions, variables — with totals and the net pay in words. The
  page's own "Export to PDF" postback answers 500 from anything but a browser, so the PDF
  the app offers is rendered locally from these figures.
"""

from __future__ import annotations

import re
from datetime import date

from lxml import html

from cerepulse.core.errors import ParserError
from cerepulse.models.pay import (
    CtcLine,
    CtcStatement,
    MonthlyRow,
    MonthlyStatement,
    PayLine,
    Payslip,
)

MONTHLY_GRID_ID = "ctl00_BodyContentPlaceHolder_GridView1"
PAYSLIP_PERIOD_SELECT_ID = "ctl00_BodyContentPlaceHolder_drpPeriod"
PAYSLIP_TEMPLATE_FIELD_ID = "ctl00_BodyContentPlaceHolder_hdnPayF"
EARNINGS_MACRO = "@@AllEarnings"

_MONEY = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def parse_money(text: str) -> float | None:
    """``1,23,456.00`` → 123456.0; blank or dashes → None. Indian grouping is just commas."""
    match = _MONEY.search(text or "")
    if match is None:
        return None
    return float(match.group(0).replace(",", ""))


def _clean(text: str) -> str:
    return " ".join((text or "").split())


# --- CTC ----------------------------------------------------------------------------------------


def parse_ctc(page: str) -> CtcStatement:
    doc = html.fromstring(page)
    # The employee block is one cell of "Label : value" runs; the year is the first of them.
    year = re.search(r"For Year\s*:\s*(\d{4}\s*-\s*\d{4})", _clean(doc.text_content()))
    for_year = year.group(1).replace(" ", "") if year else ""

    table = None
    for candidate in doc.xpath("//table"):
        first = candidate.xpath("./tr[1]/td|./tbody/tr[1]/td")
        if first and _clean(first[0].text_content()) == "Earnings":
            table = candidate
            break
    if table is None:
        raise ParserError("The CTC page carried no earnings table")

    lines: list[CtcLine] = []
    section = "Earnings"
    for tr in table.xpath("./tr|./tbody/tr"):
        cells = [_clean(td.text_content()) for td in tr.xpath("./td")]
        if not cells or not cells[0] or cells[0] == "Earnings":
            continue
        label = cells[0]
        amounts = [parse_money(c) for c in cells[1:] if c]
        if not amounts:
            # A section heading, e.g. "Outside Payroll Benefits".
            section = label
            continue
        monthly = amounts[0] if len(amounts) > 1 else None
        yearly = amounts[-1]
        lines.append(
            CtcLine(
                label=label,
                monthly=monthly,
                yearly=yearly,
                section=section,
                is_total=label.startswith("(") or "total" in label.lower(),
            )
        )
    if not lines:
        raise ParserError("The CTC earnings table held no figures")
    return CtcStatement(for_year=for_year, lines=tuple(lines))


# --- monthly report -------------------------------------------------------------------------------


def parse_monthly(page: str, *, period_label: str = "") -> MonthlyStatement:
    doc = html.fromstring(page)
    grids = doc.xpath(f"//table[@id='{MONTHLY_GRID_ID}']")
    if not grids:
        raise ParserError("The monthly report carried no grid — was its Refresh posted?")
    rows = grids[0].xpath("./tr|./tbody/tr")
    header = [_clean(c.text_content()) for c in rows[0].xpath("./th|./td")]
    months = tuple(header[1:])
    parsed: list[MonthlyRow] = []
    for tr in rows[1:]:
        cells = [_clean(c.text_content()) for c in tr.xpath("./td|./th")]
        if not cells or not cells[0]:
            continue
        values = tuple(parse_money(c) for c in cells[1 : 1 + len(months)])
        parsed.append(MonthlyRow(label=cells[0], values=values))
    return MonthlyStatement(period_label=period_label, months=months, rows=tuple(parsed))


def monthly_periods(page: str) -> list[tuple[str, str]]:
    """The report's period options as ``(value, label)``, newest first as the page lists them."""
    doc = html.fromstring(page)
    return [
        (opt.get("value", ""), _clean(opt.text_content()))
        for opt in doc.xpath(f"//select[@id='{PAYSLIP_PERIOD_SELECT_ID}']/option")
    ]


# --- payslip -------------------------------------------------------------------------------------


def payslip_periods(page: str) -> list[tuple[str, str]]:
    """``[("202607", "July , 2026"), ...]`` from the period selector, newest first."""
    return monthly_periods(page)


def payslip_template_name(page: str) -> str:
    doc = html.fromstring(page)
    field = doc.xpath(f"//input[@id='{PAYSLIP_TEMPLATE_FIELD_ID}']/@value")
    if not field:
        raise ParserError("The payslip page named no template")
    return str(field[0])


def template_macros(template: str) -> list[str]:
    """The placeholders a template asks to have filled, in document order, deduplicated."""
    seen: list[str] = []
    for name in re.findall(r'data-macro="([^"]+)"', template):
        if name not in seen:
            seen.append(name)
    return seen


def period_to_date(value: str) -> date:
    """``202607`` → 1 July 2026."""
    if not re.fullmatch(r"\d{6}", value):
        raise ParserError(f"Unrecognised payslip period {value!r}")
    return date(int(value[:4]), int(value[4:6]), 1)


def parse_payslip(payload: object, *, period: str) -> Payslip:
    """Read one month's slip out of ``GetSalarySlipData``'s answer.

    The answer is ``{"d": {"isSuccess": bool, "data": {"<emp>|<period>": [{MacroName,
    MacroValue}, ...]}}}``; the figures live in the ``@@AllEarnings`` macro as an HTML table.
    """
    answer = payload.get("d") if isinstance(payload, dict) else None
    if not isinstance(answer, dict) or not answer.get("isSuccess"):
        raise ParserError("The payslip service did not return a slip")
    data = answer.get("data")
    if not isinstance(data, dict) or not data:
        raise ParserError("The payslip service returned no data for the period")
    rows = next(iter(data.values()))
    macros = {
        str(row.get("MacroName")): str(row.get("MacroValue") or "")
        for row in rows
        if isinstance(row, dict)
    }
    table_html = macros.get(EARNINGS_MACRO, "")
    if not table_html.strip():
        raise ParserError("The payslip carried no earnings table")
    return _parse_earnings_table(table_html, period=period_to_date(period))


def _parse_earnings_table(fragment: str, *, period: date) -> Payslip:
    doc = html.fromstring(fragment)
    earnings: list[PayLine] = []
    deductions: list[PayLine] = []
    variables: list[PayLine] = []
    gross = total_deductions = net_pay = 0.0
    in_words = ""

    for tr in doc.xpath("//tr"):
        cells = [_clean(td.text_content()) for td in tr.xpath("./td|./th")]
        if not any(cells):
            continue
        if cells[0] == "Earnings":
            continue
        if len(cells) == 1:
            if cells[0].lower().startswith("net pay"):
                in_words = cells[0].split(":", 1)[-1].strip()
            continue
        pairs = [(cells[i], cells[i + 1]) for i in range(0, len(cells) - 1, 2)]
        for column, (label, amount) in enumerate(pairs):
            if not label:
                continue
            value = parse_money(amount)
            lowered = label.lower()
            if lowered.startswith("amount total"):
                if column == 0 and value is not None:
                    gross = value
                elif column == 1 and value is not None:
                    total_deductions = value
                continue
            if lowered.startswith("net pay"):
                if value is not None:
                    net_pay = value
                continue
            if value is None:
                continue
            (earnings, deductions, variables)[min(column, 2)].append(PayLine(label, value))

    if not earnings:
        raise ParserError("The payslip's earnings table held no earnings")
    return Payslip(
        period=period,
        earnings=tuple(earnings),
        deductions=tuple(deductions),
        variables=tuple(variables),
        gross=gross,
        total_deductions=total_deductions,
        net_pay=net_pay,
        in_words=in_words,
    )


__all__ = [
    "monthly_periods",
    "parse_ctc",
    "parse_money",
    "parse_monthly",
    "parse_payslip",
    "payslip_periods",
    "payslip_template_name",
    "period_to_date",
    "template_macros",
]
