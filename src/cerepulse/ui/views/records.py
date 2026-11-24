"""Records — what happened, the balances and comp-off credits, and the year's holidays.

One screen, three views behind tabs, What happened first. It used to be one long column — six
balance cards, insights, a credits table, the best breaks, the holiday calendar, and only
then the timeline — so the question the screen is opened with most ("what happened to my
time?") was the one answered at the bottom. Each view now answers one question.

* **What happened** leads with what needs doing — the few things only the user can settle —
  then one stream of everything that was not an ordinary day, grouped by day, filtered by
  kind and by state on two separate controls (one dropdown of both used to make "swipe
  requests still waiting" impossible to ask).
* **Balances & comp-off** is how much leave there is, and every comp-off credit with what
  became of it: usable, booked by a leave awaiting approval, waiting for its own approval,
  used, or lapsed.
* **Holidays & breaks** is the published calendar and the cheapest breaks it allows.

Built from the kit throughout, so nothing here sizes or colours itself.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cerepulse.intelligence.attention import duplicate_requests
from cerepulse.intelligence.comp_off import CompOffCredit, CompOffLedger, CreditState
from cerepulse.intelligence.insights import Severity
from cerepulse.intelligence.leave import LeaveOutlook
from cerepulse.intelligence.optimizer import BreakPlan
from cerepulse.intelligence.records import (
    Record,
    RecordKind,
    RecordState,
    Tone,
    holiday_calendar,
)
from cerepulse.intelligence.sandwich import SandwichAssessment
from cerepulse.models.leave import Holiday, LeaveCategory
from cerepulse.services.leave import LeaveView as LeaveData
from cerepulse.ui import formatting as fmt
from cerepulse.ui import kit
from cerepulse.ui.kit.text import measure
from cerepulse.ui.theme import Palette, Space
from cerepulse.ui.widgets import Banner, EmptyState, FlowLayout, InsightStrip, empty_layout

#: The three views, in the order the screen is used.
TABS = ("What happened", "Balances & comp-off", "Holidays & breaks")
HAPPENED, BALANCES, CALENDAR = range(3)

BREAK_COLUMNS = ("When", "Days off", "Leave used", "Value", "Book these days")
HOLIDAY_COLUMNS = ("Date", "Day", "Holiday", "")

#: Balance cards per row.
CARDS_PER_ROW = 3

#: Order the balance cards so the ones with a deadline lead.
CARD_ORDER = (
    LeaveCategory.PLANNED,
    LeaveCategory.COMP_OFF,
    LeaveCategory.CARRY_FORWARD,
    LeaveCategory.MEDICAL,
    LeaveCategory.CASUAL,
    LeaveCategory.OTHER,
)

#: What each kind of leave is called on its card. The portal's own code follows it, since
#: that is what SpineHR's screens say.
CATEGORY_NAMES = {
    LeaveCategory.PLANNED: "Planned leave",
    LeaveCategory.COMP_OFF: "Comp-off",
    LeaveCategory.CARRY_FORWARD: "Carry-forward",
    LeaveCategory.MEDICAL: "Medical",
    LeaveCategory.CASUAL: "Casual",
}

#: The timeline filters, on **two** axes rather than one: a kind, and a state.
#:
#: They used to share a single dropdown of eight entries, three describing a state and five
#: a kind. That made them mutually exclusive when they are nothing of the sort — there was no
#: way to ask for "leave that still needs doing". Two controls that combine is the same
#: information and a question the old one could not express.
KIND_FILTERS: tuple[tuple[str, Callable[[Record], bool]], ...] = (
    ("All", lambda record: True),
    # A day of comp-off is spent through a leave application, and answers both "which days
    # did I take off" and "where did my comp-off go" — so it matches both filters.
    ("Leave", lambda record: record.is_kind(RecordKind.LEAVE)),
    (
        "Comp-off",
        lambda record: record.is_kind(
            RecordKind.COMP_OFF_EARNED, RecordKind.COMP_OFF_SPENT, RecordKind.COMP_OFF_LAPSED
        ),
    ),
    ("Outdoor duty", lambda record: record.is_kind(RecordKind.OUTDOOR_DUTY)),
    ("Swipes", lambda record: record.is_kind(RecordKind.SWIPE_REQUEST)),
    # Absence had no entry under the old single dropdown, so the one kind that always needs
    # an explanation was the one kind that could not be filtered to.
    ("Absence", lambda record: record.is_kind(RecordKind.ABSENCE)),
)

#: "Needs doing" leads because it is the only one that is urgent.
STATE_FILTERS: tuple[tuple[str, Callable[[Record], bool]], ...] = (
    ("Any state", lambda record: True),
    ("Needs doing", lambda record: record.needs_action),
    ("Waiting on approval", lambda record: record.pending),
    ("Settled", lambda record: record.is_settled),
)

#: How far back the timeline reaches. Whole calendar months, because "the last 2 months"
#: read as a day count would slide a request off the screen mid-conversation about it.
#: ``0`` means the calendar year; ``None`` means everything the cache holds.
PERIODS: tuple[tuple[str, int | None], ...] = (
    ("Last 2 months", 2),
    ("Last 6 months", 6),
    ("This year", 0),
    ("Everything", None),
)

#: The most rows one render will build. The timeline is a widget per row, and "Everything"
#: against a long history is real widget count; past this the answer is a narrower period,
#: not more scrolling. The cut always announces itself — a silent truncation reads as
#: "this is all there is", which is worse than the big list it avoids.
MAX_ROWS = 200

#: How many of the things that need doing are listed above the timeline. The rest are one
#: filter away; a panel that grows without bound stops being a summary.
NEEDS_DOING_SHOWN = 4

#: A credit's days-left bar turns from green to amber inside this many days.
CREDIT_WARNING_DAYS = 60


def period_start(months: int | None, *, today: date) -> date | None:
    """The first day the selected period covers, or ``None`` for no bound."""
    if months is None:
        return None
    if months == 0:
        return date(today.year, 1, 1)
    year, month = today.year, today.month - (months - 1)
    while month < 1:
        year, month = year - 1, month + 12
    return date(year, month, 1)


class RecordsView(QWidget):
    """What happened, the balances behind it, and the year's holidays."""

    refresh_requested = Signal()
    open_portal = Signal()
    day_selected = Signal(object)  # date
    #: The period combo moved — the window reloads the timeline's days from the cache.
    period_changed = Signal()

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._records: list[Record] = []
        self._sandwiches: list[SandwichAssessment] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        page = kit.ScrollArea()
        outer.addWidget(page)
        body = page.body

        body.addLayout(self._build_header())
        self._tabs = kit.Tabs(list(TABS))
        self._tabs.changed.connect(self._show_view)
        body.addWidget(self._tabs, 0, Qt.AlignmentFlag.AlignLeft)

        self.banner = Banner()
        body.addWidget(self.banner)

        # Shown and hidden rather than stacked: a stacked widget is as tall as its tallest
        # page, so a short view sat on top of a page of empty space.
        self._views = [self._build_happened(), self._build_balances(), self._build_calendar()]
        for view in self._views:
            body.addWidget(view)
        body.addStretch(1)
        self._show_view(HAPPENED)

    # --- construction ---------------------------------------------------------------

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(Space.SNUG)
        row.addWidget(kit.Text("Records", role="title"))
        row.addStretch(1)
        row.addWidget(kit.Button("Refresh", on_click=self.refresh_requested.emit))
        row.addWidget(
            kit.Button("Open in SpineHR", variant="primary", on_click=self.open_portal.emit)
        )
        return row

    def _build_happened(self) -> QWidget:
        view = QWidget()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.GAP)

        # The few things only the user can settle, before everything else. They are in the
        # timeline too; here they cannot be scrolled past.
        self._attention = kit.Surface(variant="panel")
        panel = QVBoxLayout(self._attention)
        panel.setContentsMargins(Space.GAP, 14, Space.GAP, 14)
        panel.setSpacing(Space.TIGHT)
        self._attention_header = kit.SectionHeader("Needs doing", role="body-strong")
        panel.addWidget(self._attention_header)
        self._attention_rows = QVBoxLayout()
        self._attention_rows.setSpacing(0)
        panel.addLayout(self._attention_rows)
        layout.addWidget(self._attention)

        filters_host = kit.Box()
        filters = FlowLayout(filters_host, spacing=Space.SNUG)
        self._kind = kit.Tabs([label for label, _ in KIND_FILTERS])
        self._kind.changed.connect(lambda _index: self._render_timeline())
        filters.addWidget(self._kind)
        self._state = kit.ComboBox()
        for label, _predicate in STATE_FILTERS:
            self._state.addItem(label)
        self._state.currentIndexChanged.connect(lambda _index: self._render_timeline())
        filters.addWidget(self._state)
        # The period re-loads from the cache rather than re-filtering what is on screen, so
        # it signals the window instead of rendering locally like the other two.
        self._period = kit.ComboBox()
        for label, _months in PERIODS:
            self._period.addItem(label)
        self._period.currentIndexChanged.connect(lambda _index: self.period_changed.emit())
        filters.addWidget(self._period)
        layout.addWidget(filters_host)

        self._timeline = QVBoxLayout()
        self._timeline.setSpacing(0)
        timeline_host = kit.Box()
        timeline_host.setLayout(self._timeline)
        layout.addWidget(timeline_host)

        # The cut always announces itself; a silently bounded list reads as complete.
        self._overflow = kit.Text(role="caption", tone="muted", wrap=True)
        self._overflow.setVisible(False)
        layout.addWidget(self._overflow)

        self._nothing = EmptyState(
            "Nothing recorded.",
            "Leave, outdoor duty, comp-off and swipe requests all appear here once the "
            "months they belong to have synced.",
        )
        layout.addWidget(self._nothing)
        layout.addWidget(
            kit.Text(
                "Newest first. Each event once: the application owns the row, and the "
                "attendance muster only fills in what it lacks.",
                role="caption",
                tone="faint",
                wrap=True,
            )
        )
        return view

    def _build_balances(self) -> QWidget:
        view = QWidget()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.GAP)

        # The total leads: adding the cards up in your head is not an answer to "how much
        # leave do I have".
        self.total = kit.StatCard("Leave you can take", large=True)
        layout.addWidget(self.total)
        self._cards = QGridLayout()
        self._cards.setSpacing(Space.ROW)
        cards_host = kit.Box()
        cards_host.setLayout(self._cards)
        layout.addWidget(cards_host)

        self._insights = InsightStrip(self._palette)
        layout.addWidget(self._insights)

        # Every comp-off credit and what became of it. The card above gives the one number;
        # this answers which credits make it up, which a leave spent, and which were lost.
        self._credits_section = kit.Box()
        credits = QVBoxLayout(self._credits_section)
        credits.setContentsMargins(0, Space.SNUG, 0, 0)
        credits.setSpacing(Space.ROW)
        self._credits_header = kit.SectionHeader(
            "Comp-off credits", meta="Each credit, and what became of it"
        )
        self._portal_check = kit.Pill(tone="good")
        self._credits_header.add_action(self._portal_check)
        credits.addWidget(self._credits_header)
        self._credit_groups = QVBoxLayout()
        self._credit_groups.setSpacing(Space.ROW)
        credits.addLayout(self._credit_groups)
        self._credits_note = kit.Text(role="caption", tone="muted", wrap=True)
        credits.addWidget(self._credits_note)
        layout.addWidget(self._credits_section)
        return view

    def _build_calendar(self) -> QWidget:
        view = QWidget()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.ROW)

        layout.addWidget(kit.SectionHeader("Best breaks you can book"))
        self.breaks = kit.TableWidget(BREAK_COLUMNS, fit_rows=True)
        layout.addWidget(self.breaks)
        self._breaks_note = kit.Text(role="caption", tone="muted", wrap=True)
        layout.addWidget(self._breaks_note)

        # The whole published year, not the month on screen, so "when is the next day off"
        # is answerable without opening the portal.
        layout.addSpacing(Space.SNUG)
        layout.addWidget(kit.SectionHeader("Company holidays"))
        self.holidays = kit.TableWidget(HOLIDAY_COLUMNS, fit_rows=True)
        layout.addWidget(self.holidays)
        self._holidays_note = kit.Text(role="caption", tone="muted", wrap=True)
        layout.addWidget(self._holidays_note)
        return view

    def _show_view(self, index: int) -> None:
        for position, view in enumerate(self._views):
            view.setVisible(position == index)

    def show_view(self, index: int) -> None:
        """Bring one of the three views forward, as its tab would."""
        self._tabs.set_current(index)
        self._show_view(index)

    def period_start(self, *, today: date | None = None) -> date | None:
        """The first day the selected period covers, or ``None`` for everything."""
        _label, months = PERIODS[max(0, self._period.currentIndex())]
        return period_start(months, today=today or date.today())

    # --- rendering ------------------------------------------------------------------

    def show_leave(self, data: LeaveData) -> None:
        self._sandwiches = data.sandwiches
        self._render_total(data.outlooks)
        self._render_cards(data.outlooks)
        self._insights.set_insights(data.insights)
        self._render_credits(data.comp_off)
        self._render_breaks(data.breaks)

    def show_records(self, records: list[Record]) -> None:
        self._records = records
        self._render_attention()
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
            f"{_days(len(duplicates))} carry more than one live request for the same punch: "
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
        # The days inside the window, not the whole balance of every type that has one credit
        # close to lapsing. Comp-off expires a credit at a time and the difference is large.
        at_risk = sum(outlook.at_risk_days for outlook in live)

        self.total.set_value(_days(total), tone="good" if total > 0 else "muted")
        parts = [f"across {len(live)} kind{'s' if len(live) != 1 else ''} of leave"]
        if at_risk:
            parts.append(f"{_days(at_risk)} of it lapses within 60 days")
        expired = [outlook for outlook in outlooks if outlook.is_expired]
        if expired:
            parts.append(f"{len(expired)} kind(s) already lapsed and not counted")
        self.total.set_caption(" · ".join(parts))

    def _render_cards(self, outlooks: list[LeaveOutlook]) -> None:
        empty_layout(self._cards)
        ordered = sorted(outlooks, key=lambda outlook: CARD_ORDER.index(outlook.balance.category))
        for index, outlook in enumerate(ordered):
            self._cards.addWidget(
                self._card_for(outlook), index // CARDS_PER_ROW, index % CARDS_PER_ROW
            )

    def _card_for(self, outlook: LeaveOutlook) -> kit.StatCard:
        balance = outlook.balance
        tone = None
        if outlook.is_expired:
            tone = "bad"
        elif outlook.is_at_risk:
            tone = "rest"
        elif balance.available_balance > 0:
            tone = "good"

        name = CATEGORY_NAMES.get(balance.category)
        title = f"{name} · {balance.leave_type}" if name else balance.leave_type
        caption = _expiry_caption(outlook)
        if outlook.comp_off is not None:
            extra = _comp_off_caption(outlook.comp_off)
            caption = " · ".join(part for part in (caption, extra) if part)
        card = kit.StatCard(
            title, value=f"{balance.available_balance:g}", caption=caption, tone=tone
        )
        card.setMinimumWidth(170)
        return card

    def _render_credits(self, ledger: CompOffLedger | None) -> None:
        """Every comp-off credit of the financial year, grouped by what became of it."""
        empty_layout(self._credit_groups)
        groups = _credit_groups(ledger)
        self._credits_section.setVisible(bool(groups))
        if ledger is None or not groups:
            return

        for title, meta, credits in groups:
            self._credit_groups.addWidget(
                _CreditGroup(title, meta, credits, ledger.assessed_on, self._palette)
            )

        if ledger.portal_agrees is True and ledger.portal is not None:
            self._portal_check.set_state(
                f"SpineHR's own lookup also says {ledger.portal.balance:g} today", "good"
            )
        elif ledger.portal_agrees is False and ledger.portal is not None:
            self._portal_check.set_state(
                f"SpineHR's own lookup says {ledger.portal.balance:g} today", "bad"
            )
        else:
            self._portal_check.set_state("", "muted")
        self._credits_note.setText(_credits_note(ledger))

    def show_holidays(self, holidays: list[Holiday], *, today: date | None = None) -> None:
        """The published calendar, past dimmed and the next one named."""
        entries = holiday_calendar(holidays, today=today or date.today())
        self.holidays.setRowCount(len(entries))
        self.holidays.setVisible(bool(entries))

        if not entries:
            self._holidays_note.setText(
                "The holiday calendar has not synced yet. Refresh to fetch it."
            )
            return

        for row, entry in enumerate(entries):
            holiday = entry.holiday
            mark = "Next" if entry.is_next else ("Passed" if entry.has_passed else "")
            values = (
                holiday.day.strftime("%d %b %Y").lstrip("0"),
                holiday.weekday,
                holiday.name or "Company holiday",
                mark,
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                # Dimmed rather than hidden: half of what this list answers is what has
                # already been taken.
                if entry.has_passed:
                    item.setForeground(QColor(self._palette.text_faint))
                elif entry.is_next:
                    item.setForeground(QColor(self._palette.good))
                self.holidays.setItem(row, column, item)

        remaining = sum(1 for entry in entries if not entry.has_passed)
        upcoming = next((entry for entry in entries if entry.is_next), None)
        if upcoming is None:
            self._holidays_note.setText(f"All {len(entries)} published holidays have passed.")
        else:
            when = upcoming.day.strftime("%A %d %B").lstrip("0")
            self._holidays_note.setText(
                f"{remaining} of {len(entries)} still to come — next is "
                f"{upcoming.holiday.name or 'a company holiday'} on {when}."
            )

    def _render_breaks(self, plans: list[BreakPlan]) -> None:
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

    def _render_attention(self) -> None:
        empty_layout(self._attention_rows)
        needing = [record for record in self._records if record.needs_action]
        self._attention.setVisible(bool(needing))
        if not needing:
            return
        count = len(needing)
        self._attention_header.set_meta(
            f"{count} thing{'s' if count != 1 else ''} only you can settle — "
            "they stay here until they are"
        )
        for record in needing[:NEEDS_DOING_SHOWN]:
            row = _RecordRow(record, self._palette, dated=True)
            row.clicked.connect(lambda day=record.day: self.day_selected.emit(day))
            self._attention_rows.addWidget(row)
        if count > NEEDS_DOING_SHOWN:
            self._attention_rows.addWidget(
                kit.Text(
                    f"and {count - NEEDS_DOING_SHOWN} more — pick Needs doing below",
                    role="caption",
                    tone="muted",
                )
            )

    def _render_timeline(self) -> None:
        empty_layout(self._timeline)

        _kind_label, by_kind = KIND_FILTERS[self._kind.current()]
        _state_label, by_state = STATE_FILTERS[max(0, self._state.currentIndex())]
        shown = [record for record in self._records if by_kind(record) and by_state(record)]
        self._nothing.setVisible(not shown)

        # Grouped under the day rather than dated per row: three things on one Thursday are
        # one date and three lines, and the date stops competing with the title.
        group: _DayGroup | None = None
        for record in shown[:MAX_ROWS]:
            if group is None or group.day != record.day:
                group = _DayGroup(record.day)
                self._timeline.addWidget(group)
            row = _RecordRow(record, self._palette)
            row.clicked.connect(lambda day=record.day: self.day_selected.emit(day))
            group.add(row)

        if len(shown) > MAX_ROWS:
            self._overflow.setText(
                f"Showing the {MAX_ROWS} most recent of {len(shown)} — narrow the period "
                "or the filters to reach the rest."
            )
        self._overflow.setVisible(len(shown) > MAX_ROWS)


# --- the timeline ---------------------------------------------------------------------------


class _DayHeader(QWidget):
    """The date, once, beside everything that happened on it. Not clickable."""

    def __init__(self, day: date, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(0)
        self.date = kit.Text(fmt.day_label(day), self, role="body-strong")
        layout.addWidget(self.date)
        # The year only when it is not this one: "2026" under every date was noise.
        if day.year != date.today().year:
            layout.addWidget(kit.Text(str(day.year), self, role="caption", tone="faint"))
        layout.addStretch(1)
        self.setFixedWidth(measure("Wed 30 Sep", role="body-strong") + Space.GAP)


class _DayGroup(QWidget):
    """One day: its date on the left, a row per thing that happened on the right."""

    def __init__(self, day: date, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.day = day
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(kit.Divider(self))
        row = QHBoxLayout()
        row.setContentsMargins(0, Space.TIGHT, 0, Space.TIGHT)
        row.setSpacing(Space.SNUG)
        row.addWidget(_DayHeader(day, self), 0, Qt.AlignmentFlag.AlignTop)
        self._rows = QVBoxLayout()
        self._rows.setSpacing(0)
        row.addLayout(self._rows, 1)
        column.addLayout(row)

    def add(self, row: QWidget) -> None:
        self._rows.addWidget(row)


#: Which icon, and in which tone, each kind of event is drawn with.
KIND_ICONS: dict[RecordKind, tuple[str, str]] = {
    RecordKind.LEAVE: ("leave", "adjust"),
    RecordKind.OUTDOOR_DUTY: ("duty", "work"),
    RecordKind.COMP_OFF_EARNED: ("earned", "good"),
    RecordKind.COMP_OFF_SPENT: ("leave", "good"),
    RecordKind.COMP_OFF_LAPSED: ("lapse", "bad"),
    RecordKind.SWIPE_REQUEST: ("swipe", "work"),
    RecordKind.ABSENCE: ("absence", "bad"),
}

#: The pill's tone, by state: approved green, pending amber, rejected and lapsed red.
STATE_TONES: dict[RecordState, str] = {
    RecordState.APPROVED: "good",
    RecordState.PENDING: "rest",
    RecordState.REJECTED: "bad",
    RecordState.LAPSED: "bad",
    RecordState.CANCELLED: "muted",
}

ASIDE_TONES = {Tone.MUTED: "muted", Tone.FAINT: "faint", Tone.WARN: "rest", Tone.BAD: "bad"}


class _RecordRow(kit.ListRow):
    """One event: an icon for its kind, what it was, why, what became of it, its state.

    The pill is coloured by *state* — approved green, pending amber, rejected red — and the
    icon by kind, so the two ends of a row answer two different questions and a rejection is
    never the same colour as an approval. Only requests get a pill: a half day the portal
    marked for a late arrival has no approval state, and a blank pill would read as one that
    failed to load.
    """

    def __init__(
        self,
        record: Record,
        palette: Palette,
        parent: QWidget | None = None,
        *,
        dated: bool = False,
    ) -> None:
        icon, icon_tone = KIND_ICONS.get(record.kind, ("info", "muted"))
        kind = record.kind.label
        if record.decided_on is not None:
            kind = f"{kind} · decided {fmt.day_label(record.decided_on)}"
        title = f"{fmt.day_label(record.day)} · {record.title}" if dated else record.title
        super().__init__(
            palette,
            icon=icon,
            icon_tone=icon_tone,
            title=title,
            detail=record.detail,
            aside=record.aside,
            aside_tone=ASIDE_TONES.get(record.aside_tone, "muted"),
            kind=kind,
            state=record.status if record.state is not RecordState.NONE else "",
            state_tone=STATE_TONES.get(record.state, "muted"),
            tooltip=record.note,
            clickable=True,
            parent=parent,
        )


# --- comp-off credits -----------------------------------------------------------------------


def _credit_groups(
    ledger: CompOffLedger | None,
) -> list[tuple[str, str, list[CompOffCredit]]]:
    """The credits worth showing, grouped as the user asks about them, empty groups dropped.

    The financial year's credits and any older one still open. Credits from an earlier year
    are history once settled, and the register stops showing them in April too.
    """
    if ledger is None:
        return []
    start = _financial_year_start(ledger)
    usable = list(ledger.available)
    booked = [c for c in ledger.credits if c.state is CreditState.BOOKED]
    pending = [c for c in ledger.credits if c.state is CreditState.PENDING]
    settled = sorted(
        (
            c
            for c in ledger.credits
            if c.state in (CreditState.USED, CreditState.LAPSED) and c.earned_on >= start
        ),
        key=lambda credit: credit.earned_on,
        reverse=True,
    )
    used = sum(1 for c in settled if c.state is CreditState.USED)
    lost = sum(c.remaining for c in settled if c.state is CreditState.LAPSED)
    groups = [
        ("Usable now", f"{_days(ledger.usable)} · spend the top one first", usable),
        ("Booked", f"{_days(ledger.booked_days)} held by leave awaiting approval", booked),
        ("Awaiting approval", f"{_days(ledger.pending_days)} · not spendable yet", pending),
        (
            "Used or lapsed this year",
            " · ".join(
                part
                for part in (
                    f"{used} used" if used else "",
                    f"{_days(lost)} lapsed" if lost else "",
                )
                if part
            ),
            settled,
        ),
    ]
    return [group for group in groups if group[2]]


class _CreditGroup(kit.Surface):
    """One group of credits: a header strip, then a row per credit."""

    def __init__(
        self,
        title: str,
        meta: str,
        credits: list[CompOffCredit],
        today: date,
        palette: Palette,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, variant="card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.GAP, Space.ROW, Space.GAP, Space.SNUG)
        layout.setSpacing(Space.TIGHT)
        layout.addWidget(kit.SectionHeader(title, meta=meta, role="body-strong"))
        for credit in credits:
            layout.addWidget(kit.Divider(self))
            layout.addWidget(_CreditRow(credit, today, palette, self))


class _CreditRow(QWidget):
    """Earned, how much, what became of it and why it was earned, and its use-by date."""

    def __init__(
        self, credit: CompOffCredit, today: date, palette: Palette, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, Space.TIGHT, 0, Space.TIGHT)
        row.setSpacing(Space.GAP)

        earned = kit.Text(fmt.day_label(credit.earned_on), self, role="body-strong")
        earned.setFixedWidth(measure("Wed 30 Sep", "Mon 21 Sep", role="body-strong"))
        row.addWidget(earned)
        amount = kit.Text(_amount(credit.days), self, tone="muted")
        amount.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        amount.setFixedWidth(measure("½ day", "1 day", "2 days"))
        row.addWidget(amount)

        words = QVBoxLayout()
        words.setSpacing(0)
        fate, fate_tone = _credit_fate(credit, today)
        self.fate = kit.Text(fate, self, tone=fate_tone, wrap=True)
        words.addWidget(self.fate)
        if credit.reason:
            words.addWidget(kit.Text(credit.reason, self, role="caption", tone="muted", wrap=True))
        row.addLayout(words, 1)

        clock = QVBoxLayout()
        clock.setSpacing(Space.TIGHT)
        line = QHBoxLayout()
        line.setSpacing(Space.SNUG)
        use_by = "Use by" if credit.state is CreditState.AVAILABLE else "Last day"
        line.addWidget(
            kit.Text(f"{use_by} {fmt.day_label(credit.expires_on)}", self, role="caption")
        )
        line.addStretch(1)
        left, left_tone, fraction = _credit_clock(credit, today)
        self.left = kit.Text(left, self, role="caption", tone=left_tone)
        line.addWidget(self.left)
        clock.addLayout(line)
        meter = kit.Meter(palette, self, height=4)
        meter.set_fraction(min(1.0, fraction), left_tone)
        clock.addWidget(meter)
        holder = kit.Box(self)
        holder.setLayout(clock)
        # A floor, so the bars line up down the list, and room to grow past it: a fixed
        # width left out the gap between the two labels and clipped the date.
        holder.setMinimumWidth(
            measure("Last day Wed 30 Sep", role="caption")
            + Space.SNUG
            + measure("38 days left", role="caption")
        )
        row.addWidget(holder)


def _credit_fate(credit: CompOffCredit, today: date) -> tuple[str, str | None]:
    """What became of a credit, in the words its group does not already say."""
    spent_on = ", ".join(fmt.day_label(spend.day) for spend in credit.spent_by)
    if credit.state is CreditState.AVAILABLE:
        if credit.spent_by:
            return f"{_amount(credit.remaining)} of it left after {spent_on}", None
        return "Available", None
    if credit.state is CreditState.BOOKED:
        apps = ", ".join(sorted({f"#{s.app_id}" for s in credit.spent_by if s.app_id}))
        return f"For {spent_on}" + (f" · leave {apps}" if apps else ""), None
    if credit.state is CreditState.USED:
        return f"Used {spent_on}", "muted"
    if credit.state is CreditState.LAPSED:
        partly = f" · {_amount(credit.remaining)} of it" if credit.spent_by else ""
        return f"Lapsed unused{partly}", "bad"
    applied = f" · comp-off #{credit.app_id} in process" if credit.app_id else ""
    return f"Awaiting approval{applied}", "muted"


def _credit_clock(credit: CompOffCredit, today: date) -> tuple[str, str, float]:
    """The words at the end of the bar, their tone, and how full the bar is."""
    window = max(1, (credit.expires_on - credit.earned_on).days)
    if credit.state is CreditState.AVAILABLE:
        left = credit.days_left(today)
        tone = "rest" if left <= CREDIT_WARNING_DAYS else "good"
        return f"{left} day{'s' if left != 1 else ''} left", tone, left / window
    if credit.state is CreditState.BOOKED:
        return "held", "faint", 1.0
    if credit.state is CreditState.PENDING:
        return "pending", "faint", 0.0
    if credit.state is CreditState.USED:
        return "used", "good", 1.0
    return "lost", "bad", 1.0


def _amount(days: float) -> str:
    return "½ day" if days == 0.5 else _days(days)


# --- captions ---------------------------------------------------------------------------------


def open_url(url: str) -> None:
    """Open a portal page in the default browser."""
    QDesktopServices.openUrl(QUrl(url))


def _expiry_caption(outlook: LeaveOutlook) -> str:
    """When this balance goes, and — for comp-off — how much of it goes first.

    Comp-off expires a credit at a time, so a single date describes only the soonest one. The
    card names that date and then says how many more are queued behind it, because "expires
    12 Sep" against a two-day balance reads as though all two days go on the 12th.
    """
    from cerepulse.intelligence.leave import ExpiryBasis

    if outlook.basis is ExpiryBasis.UNKNOWN:
        # Nothing dated to count from. Saying so beats inventing a deadline.
        return "expiry unknown — the portal does not date these"
    if outlook.expires_on is None:
        return "no expiry"
    if outlook.is_expired:
        return f"lapsed {fmt.day_label(outlook.expires_on)}"

    when = fmt.day_label(outlook.expires_on)
    ledger = outlook.comp_off
    available = ledger.available if ledger is not None else ()
    lead = f"{_days(available[0].remaining)} expires" if len(available) > 1 else "expires"
    parts = [f"{lead} {when}"]
    if outlook.days_remaining is not None:
        parts.append(f"{outlook.days_remaining} days")
    if len(available) > 1:
        parts.append(f"{len(available) - 1} more later")
    return " · ".join(parts)


def _comp_off_caption(ledger: CompOffLedger) -> str:
    """What the comp-off figure leaves out: booked, awaiting approval, lost this year."""
    parts = []
    if ledger.booked_days:
        parts.append(f"{_days(ledger.booked_days)} booked")
    if ledger.pending_days:
        parts.append(f"{_days(ledger.pending_days)} awaiting approval")
    lapsed = sum(c.remaining for c in ledger.lapsed_since(_financial_year_start(ledger)))
    if lapsed:
        parts.append(f"{_days(lapsed)} lapsed this year")
    if ledger.portal_agrees is False and ledger.portal is not None:
        parts.append(f"SpineHR counts {ledger.portal.balance:g}")
    return " · ".join(parts)


def _financial_year_start(ledger: CompOffLedger) -> date:
    today = ledger.assessed_on
    return date(today.year if today.month >= 4 else today.year - 1, 4, 1)


def _credits_note(ledger: CompOffLedger) -> str:
    """Where the use-by dates and the credit-to-leave links come from."""
    note = (
        "Use by is 90 days from the day it was earned, or 31 March if that comes first — "
        "SpineHR does not publish the approval date its rule counts from."
    )
    if ledger.inferred:
        count = len(ledger.inferred)
        note += (
            " Which credit a leave used comes from SpineHR where it says; for "
            f"{count} leave{'s' if count != 1 else ''} it is inferred — the oldest credit "
            "still valid on the day."
        )
    if ledger.portal_agrees is False and ledger.portal is not None:
        note += (
            f" SpineHR's own lookup counts {ledger.portal.balance:g} usable today against "
            f"{ledger.usable:g} here; SpineHR's figure is the one it will enforce."
        )
    return note


def _booking_text(plan: BreakPlan) -> str:
    if not plan.leave_days:
        return "nothing to book"
    return ", ".join(day.strftime("%d %b").lstrip("0") for day in plan.leave_days)


def _days(count: float) -> str:
    shown = f"{count:g}"
    return f"{shown} day" if count == 1 else f"{shown} days"
