"""The Pay screen: masked until asked, off until enabled, and a PDF from its own figures."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from PySide6.QtWidgets import QApplication

from cerepulse.models.pay import CtcLine, CtcStatement, PayLine, Payslip
from cerepulse.services.pay import PaySnapshot
from cerepulse.ui.theme import PALETTES
from cerepulse.ui.views.pay import MASK, PayView, format_inr, payslip_html, write_payslip_pdf

SLIP = Payslip(
    period=date(2026, 7, 1),
    earnings=(PayLine("E Basic", 10000.0), PayLine("E HRA", 4000.0)),
    deductions=(PayLine("PROV.FUND", 1200.0),),
    variables=(PayLine("CTC", 14833.33),),
    gross=14000.0,
    total_deductions=1200.0,
    net_pay=12800.0,
    in_words="Twelve Thousand Eight Hundred Only",
)
SNAPSHOT = PaySnapshot(
    ctc=CtcStatement(
        "2026-2027", (CtcLine("(A+C) Cost To Company", 14833.33, 178000.0, "", True),)
    ),
    monthly=None,
    payslips=(SLIP,),
    last_synced=datetime(2026, 9, 6, 9),
)


def test_indian_grouping() -> None:
    assert format_inr(123456.0) == "₹1,23,456.00"
    assert format_inr(1234567.5) == "₹12,34,567.50"
    assert format_inr(999.0) == "₹999.00"
    assert format_inr(-1200.0) == "-₹1,200.00"
    assert format_inr(None) == "—"
    assert format_inr(5.0, masked=True) == MASK


def test_figures_are_masked_until_revealed(qapp: QApplication) -> None:
    view = PayView(PALETTES["dark"])
    view.set_enabled_state(True)
    view.show_snapshot(SNAPSHOT)
    assert view.net_pay._value.text() == MASK  # noqa: SLF001
    assert view._earnings.text_at(0, 1) == MASK  # noqa: SLF001
    assert view._earnings.text_at(2, 0) == "Gross"  # noqa: SLF001
    assert "Twelve Thousand" not in view.slip_net._caption.text()  # noqa: SLF001

    view._reveal.setChecked(True)  # noqa: SLF001
    assert view.net_pay._value.text() == "₹12,800.00"  # noqa: SLF001
    assert view._earnings.text_at(0, 1) == "₹10,000.00"  # noqa: SLF001
    assert view._earnings.text_at(2, 1) == "₹14,000.00", "the ledger ends in its total"  # noqa: SLF001
    assert view._ctc.text_at(0, 2) == "₹1,78,000.00"  # noqa: SLF001
    assert view.ctc_yearly._value.text() == "₹1,78,000.00"  # noqa: SLF001
    assert "Twelve Thousand" in view.slip_net._caption.text()  # noqa: SLF001

    # Leaving the screen masks it again.
    view.hideEvent(None)
    assert not view.revealed
    assert view.net_pay._value.text() == MASK  # noqa: SLF001


def test_off_state_hides_the_figures_and_offers_settings(qapp: QApplication) -> None:
    view = PayView(PALETTES["light"])
    view.show_snapshot(SNAPSHOT)
    view.set_enabled_state(False)
    assert not view._content.isVisibleTo(view)  # noqa: SLF001
    assert view._off.isVisibleTo(view)  # noqa: SLF001
    assert view._settings_link.isVisibleTo(view)  # noqa: SLF001


def test_empty_cache_says_so(qapp: QApplication) -> None:
    view = PayView(PALETTES["light"])
    view.set_enabled_state(True)
    view.show_snapshot(PaySnapshot(None, None, (), None))
    assert view._empty.isVisibleTo(view)  # noqa: SLF001
    assert not view._content.isVisibleTo(view)  # noqa: SLF001


def test_the_pdf_carries_figures_and_nothing_personal(qapp: QApplication, tmp_path: Path) -> None:
    html = payslip_html(SLIP)
    assert "₹12,800.00" in html
    assert "Twelve Thousand" in html
    assert "July 2026" in html
    target = tmp_path / "slip.pdf"
    write_payslip_pdf(SLIP, str(target))
    assert target.read_bytes().startswith(b"%PDF")
