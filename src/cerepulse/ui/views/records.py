"""Records — balances, and one timeline of everything that was not an ordinary day.

Replaces the separate Leave and Requests screens. They were split by which portal page the
data came from, which is the vendor's filing system rather than the user's: a week in June
was outdoor duty on the muster, a comp-off credit in the leave ledger and a swipe request in
a third list, and piecing it together meant reading two screens and holding the dates in
your head. Both screens were also the same shape — a header, a banner, a short capped table
and one stretch-1 table — so merging them costs nothing structurally.

Balances sit on top because "how much leave do I have" is what the screen is opened with.
Everything else is one stream ordered by time and filtered by kind.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from cerepulse.intelligence.attention import duplicate_requests
from cerepulse.intelligence.insights import Severity
from cerepulse.intelligence.leave import LeaveOutlook
from cerepulse.intelligence.optimizer import BreakPlan
from cerepulse.intelligence.records import Record, RecordKind
from cerepulse.intelligence.sandwich import SandwichAssessment
from cerepulse.models.leave import LeaveCategory
from cerepulse.services.leave import LeaveView as LeaveData
from cerepulse.ui import formatting as fmt
from cerepulse.ui.theme import Palette, Space
from cerepulse.ui.widgets import (
    Banner,
    Card,
    EmptyState,
    InsightStrip,
    SectionTitle,
    StatusChip,
    data_table,
)

BREAK_COLUMNS = ("When", "Days off", "Leave used", "Value", "Book these days")

#: Order the balance cards so the ones with a deadline lead.
CARD_ORDER = (
    LeaveCategory.PLANNED,
    LeaveCategory.COMP_OFF,
    LeaveCategory.CARRY_FORWARD,
    LeaveCategory.MEDICAL,
    LeaveCategory.CASUAL,
    LeaveCategory.OTHER,
)

#: The timeline filters. "Needs doing" first, because it is the only one that is urgent.
FILTERS: tuple[tuple[str, Callable[[Record], bool]], ...] = (
    ("Everything", lambda record: True),
    ("Needs doing", lambda record: record.needs_action),
    ("Waiting on approval", lambda record: record.pending),
    ("Leave", lambda record: record.kind is RecordKind.LEAVE),
    ("Outdoor duty", lambda record: record.kind is RecordKind.OUTDOOR_DUTY),
    (
        "Comp-off",
        lambda record: record.kind in (RecordKind.COMP_OFF_EARNED, RecordKind.COMP_OFF_SPENT),
    ),
    ("Swipe requests", lambda record: record.kind is RecordKind.SWIPE_REQUEST),
    ("Holidays", lambda record: record.kind is RecordKind.HOLIDAY),
)


class RecordsView(QWidget):
    """Leave balances, the breaks they can buy, and everything that happened."""

    refresh_requested = Signal()
    open_portal = Signal()
    day_selected = Signal(object)  # date

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._records: list[Record] = []
        self._sandwiches: list[SandwichAssessment] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(Space.SECTION, 20, Space.SECTION, Space.SECTION)
        layout.setSpacing(Space.GAP)

        layout.addLayout(self._build_header())

        self.banner = Banner()
        layout.addWidget(self.banner)

        # The total leads: adding six cards up in your head is not an answer to "how much
        # leave do I have".
        self.total = Card("Total available")
        layout.addWidget(self.total)

        self._cards = QHBoxLayout()
        self._cards.setSpacing(Space.ROW)
        cards_host = QWidget()
        cards_host.setLayout(self._cards)
        layout.addWidget(cards_host)

        self._insights = InsightStrip(palette)
        layout.addWidget(self._insights)

        layout.addWidget(SectionTitle("Best breaks you can book"))
        self.breaks = data_table(BREAK_COLUMNS, fit_rows=True)
        layout.addWidget(self.breaks)
        self._breaks_note = QLabel()
        self._breaks_note.setObjectName("CardCaption")
        self._breaks_note.setWordWrap(True)
        layout.addWidget(self._breaks_note)

        layout.addLayout(self._build_timeline_header())
        self._timeline = QVBoxLayout()
        self._timeline.setSpacing(Space.TIGHT)
        timeline_host = QWidget()
        timeline_host.setLayout(self._timeline)
        layout.addWidget(timeline_host)

        self._nothing = EmptyState(
            "Nothing recorded.",
            "Leave, outdoor duty, comp-off and swipe requests all appear here once the "
            "months they belong to have synced.",
        )
        layout.addWidget(self._nothing)

        layout.addStretch(1)

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(Space.SNUG)
        row.addWidget(SectionTitle("Records"))
        row.addStretch(1)

        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_requested)
        portal = QPushButton("Open in SpineHR")
        portal.setObjectName("Primary")
        portal.clicked.connect(self.open_portal)
        row.addWidget(refresh)
        row.addWidget(portal)
        return row

    def _build_timeline_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(Space.SNUG)
        row.addWidget(SectionTitle("What happened"))
        self._filter = QComboBox()
        self._filter.setMinimumWidth(180)
        for label, _predicate in FILTERS:
            self._filter.addItem(label)
        self._filter.currentIndexChanged.connect(lambda _index: self._render_timeline())
        row.addWidget(self._filter)
        row.addStretch(1)
        return row

    # --- rendering ------------------------------------------------------------------

    def show_leave(self, data: LeaveData) -> None:
        self._sandwiches = data.sandwiches
        self._render_total(data.outlooks)
        self._render_cards(data.outlooks)
        self._insights.set_insights(data.insights)
        self._render_breaks(data.breaks)

    def show_records(self, records: list[Record]) -> None:
        self._records = records
        self._render_timeline()

    def show_requests(self, requests: list[object]) -> None:
        """Only the duplicate warning now — the requests themselves are in the timeline."""
        duplicates = duplicate_requests(requests)  # type: ignore[arg-type]
        if not duplicates:
            self.banner.clear_message("duplicates")
            return
        days = ", ".join(
            f"{day.strftime('%d %b').lstrip('0')} ({mode})" for day, mode in sorted(duplicates)[:4]
        )
        more = f" and {len(duplicates) - 4} more" if len(duplicates) > 4 else ""
        self.banner.show_message(
            f"{len(duplicates)} day(s) carry more than one live request for the same punch: "
            f"{days}{more}. Cancelling the extra keeps the approver's queue honest.",
            Severity.WARNING,
            key="duplicates",
        )

    def _render_total(self, outlooks: list[LeaveOutlook]) -> None:
        """Everything available, and how much of it is on a deadline.

        A single number would be the wrong answer on its own: some of a balance can be
        comp-off that lapses in a fortnight, and a total that hides that reads as more
        freedom than the user actually has.
        """
        live = [outlook for outlook in outlooks if not outlook.is_expired]
        total = sum(outlook.balance.available_balance for outlook in live)
        at_risk = sum(outlook.balance.available_balance for outlook in live if outlook.is_at_risk)

        self.total.set_value(
            _days(total), accent=self._palette.good if total > 0 else self._palette.text_muted
        )
        parts = [f"across {len(live)} leave type(s)"]
        if at_risk:
            parts.append(f"{_days(at_risk)} expiring soon")
        expired = [outlook for outlook in outlooks if outlook.is_expired]
        if expired:
            parts.append(f"{len(expired)} type(s) already lapsed and not counted")
        self.total.set_caption("  ·  ".join(parts))

    def _render_cards(self, outlooks: list[LeaveOutlook]) -> None:
        while self._cards.count():
            item = self._cards.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

        ordered = sorted(outlooks, key=lambda outlook: CARD_ORDER.index(outlook.balance.category))
        for outlook in ordered:
            self._cards.addWidget(self._card_for(outlook))

    def _card_for(self, outlook: LeaveOutlook) -> Card:
        balance = outlook.balance
        accent = None
        if outlook.is_expired:
            accent = self._palette.bad
        elif outlook.is_at_risk:
            accent = self._palette.rest
        elif balance.available_balance > 0:
            accent = self._palette.good

        card = Card(balance.leave_type, value=f"{balance.available_balance:g}", accent=accent)
        card.set_caption(_expiry_caption(outlook))
        card.setMinimumWidth(170)
        return card

    def _render_breaks(self, plans: list[BreakPlan]) -> None:
        from PySide6.QtWidgets import QTableWidgetItem

        self.breaks.setRowCount(len(plans))
        self.breaks.setVisible(bool(plans))

        if not plans:
            self._breaks_note.setText(
                "No bookable breaks in the next six months — either the balance is spent "
                "or the holiday calendar has not synced yet."
            )
            return

        for row, plan in enumerate(plans):
            sandwich = self._sandwiches[row] if row < len(self._sandwiches) else None
            charged = sandwich if sandwich is not None and sandwich.applies else None
            values = (
                plan.label,
                str(plan.total_days),
                f"{plan.cost}  (+{charged.extra_cost} sandwiched)" if charged else str(plan.cost),
                f"{plan.efficiency:.1f}×",
                _booking_text(plan),
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if 1 <= column <= 3:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                if column == 3:
                    item.setForeground(QColor(self._palette.good))
                if column == 2 and charged:
                    item.setForeground(QColor(self._palette.rest))
                self.breaks.setItem(row, column, item)

        best = max(plans, key=lambda plan: plan.efficiency)
        note = (
            f"Value is days off per day of leave spent. The best here turns "
            f"{_days(best.cost)} into {best.total_days}. Nothing is booked — apply in SpineHR."
        )
        if any(entry.applies for entry in self._sandwiches):
            note += (
                "  Costs include the sandwich rule you configured in Settings; CerePulse "
                "has not read that rule anywhere, so check it against your own policy."
            )
        self._breaks_note.setText(note)

    def _render_timeline(self) -> None:
        while self._timeline.count():
            item = self._timeline.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

        _label, predicate = FILTERS[max(0, self._filter.currentIndex())]
        shown = [record for record in self._records if predicate(record)]
        self._nothing.setVisible(not shown)

        for record in shown:
            row = _RecordRow(record, self._palette)
            row.clicked.connect(lambda day=record.day: self.day_selected.emit(day))
            self._timeline.addWidget(row)


class _RecordRow(QWidget):
    """One entry: when, what kind, and what it said."""

    clicked = Signal()

    def __init__(self, record: Record, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, Space.TIGHT // 2, 0, Space.TIGHT // 2)
        layout.setSpacing(Space.ROW)

        when = QLabel(record.day.strftime("%a %d %b").lstrip("0"))
        when.setFixedWidth(96)
        when.setStyleSheet(f"color: {palette.text_muted};")
        layout.addWidget(when)

        chip = StatusChip(record.kind.label, _kind_colour(record, palette))
        chip.setFixedWidth(124)
        layout.addWidget(chip)

        text = QVBoxLayout()
        text.setSpacing(1)
        title = QLabel(record.title)
        title.setStyleSheet("font-weight: 600;")
        text.addWidget(title)
        if record.detail:
            detail = QLabel(record.detail)
            detail.setObjectName("CardCaption")
            detail.setWordWrap(True)
            text.addWidget(detail)
        layout.addLayout(text, 1)

    def mouseReleaseEvent(self, event: object) -> None:  # noqa: N802 — Qt override
        self.clicked.emit()
        super().mouseReleaseEvent(event)  # type: ignore[arg-type]


def open_url(url: str) -> None:
    """Open a portal page in the default browser."""
    QDesktopServices.openUrl(QUrl(url))


def _kind_colour(record: Record, palette: Palette) -> str:
    if record.needs_action:
        return palette.bad
    if record.pending:
        return palette.rest
    return {
        RecordKind.LEAVE: palette.adjust,
        RecordKind.OUTDOOR_DUTY: palette.adjust,
        RecordKind.COMP_OFF_EARNED: palette.good,
        RecordKind.COMP_OFF_SPENT: palette.work,
        RecordKind.SWIPE_REQUEST: palette.good,
        RecordKind.HOLIDAY: palette.text_muted,
        RecordKind.ABSENCE: palette.bad,
    }.get(record.kind, palette.text_muted)


def _expiry_caption(outlook: LeaveOutlook) -> str:
    from cerepulse.intelligence.leave import ExpiryBasis

    if outlook.basis is ExpiryBasis.UNKNOWN:
        # The portal's comp-off summary row is undated, so there is no earned date to count
        # from. Saying so beats inventing a deadline.
        return "expiry unknown — the portal does not date these"
    if outlook.expires_on is None:
        return "no expiry"
    if outlook.is_expired:
        return f"lapsed {fmt.day_label(outlook.expires_on)}"
    if outlook.days_remaining is not None:
        return f"expires {fmt.day_label(outlook.expires_on)} · {outlook.days_remaining} day(s)"
    return f"expires {fmt.day_label(outlook.expires_on)}"


def _booking_text(plan: BreakPlan) -> str:
    if not plan.leave_days:
        return "nothing to book"
    return ", ".join(day.strftime("%d %b").lstrip("0") for day in plan.leave_days)


def _days(count: float) -> str:
    shown = f"{count:g}"
    return f"{shown} day" if count == 1 else f"{shown} days"


def _record_days(records: list[Record]) -> set[date]:
    return {record.day for record in records}
