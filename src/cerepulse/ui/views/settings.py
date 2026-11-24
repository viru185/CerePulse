"""Settings — grouped into cards across two columns.

A single stacked column of full-width controls wastes most of a 1120px window and pushes
half the settings below the fold. Related settings are grouped into cards instead, laid out
two across, and every control is sized to its content: a spin box holding "8.00" has no
business being a thousand pixels wide.

Changes are collected and applied on Save rather than written per keystroke, so a half-typed
value never becomes the live policy. Save and Revert sit in the title row, pinned above the
page, as the redesign's mockup has them: always reachable, and beside the title of the thing
they save rather than under the last card.
"""

from __future__ import annotations

from dataclasses import replace
from functools import partial

from PySide6.QtCore import QRectF, Qt, QTime, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QResizeEvent
from PySide6.QtWidgets import (
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from cerepulse import __about__ as about
from cerepulse.core.config import AppConfig, CommuteConfig
from cerepulse.core.config.models import THEMES
from cerepulse.intelligence.sandwich import SandwichRule
from cerepulse.ui import kit
from cerepulse.ui.theme import THEME_LABELS, Palette, Space, palette_for
from cerepulse.ui.widgets import Banner, FlowLayout, add_reveal_toggle

#: Control widths, so nothing stretches to fill the card. Spin boxes need room for their
#: suffix *and* their stepper buttons, or the arrows sit on top of the number.
NUMBER_WIDTH = 122
WIDE_NUMBER_WIDTH = 148
CHOICE_WIDTH = 190
#: The quote sources, as Settings names them. Anime first: it is the default.
QUOTE_SOURCE_LABELS = (
    ("anime", "Anime (Animechan)"),
    ("zenquotes", "Inspirational (ZenQuotes)"),
    ("alternate", "Take turns, day by day"),
)
TIME_WIDTH = 108

#: Floor for each grid column. Without it the column holding the widest label claims the
#: extra space and the other column's cards are clipped, since setColumnStretch only shares
#: out what is left *after* minimum sizes are met.
COLUMN_MIN_WIDTH = 380
#: Space between cards, the page's left and right margins together, and what a vertical
#: scrollbar takes — the arithmetic deciding between one column and two.
GRID_GAP = 14
PAGE_MARGINS = 2 * Space.PAGE
SCROLLBAR_ROOM = 12


class AddressField(QWidget):
    """One end of the journey: a field that takes anything Google Maps can copy.

    An address, a full Maps link, bare coordinates, a DMS string or a Plus Code all go in
    the same box — the parsing decides which path a paste is on, not the user. What comes
    back differs by path, and the difference is the whole design:

    * a **pinned point** is saved as soon as it is named, because there is nothing to
      choose between;
    * a **typed address** returns candidates, and nothing is saved until one is picked —
      the best match is a guess, and a guess must be the user's to confirm.

    "Open in Google Maps" is the confirmation of last resort: it costs nothing, needs no
    key, and it is the only check that is genuinely conclusive, because you look at the map
    and see your building.
    """

    #: (which end, the pasted text). The text rides in the signal so the worker never
    #: touches a Qt widget.
    lookup_requested = Signal(str, str)
    #: (which end, the chosen Place).
    place_picked = Signal(str, object)
    #: (which end,) — open the saved point in the browser.
    open_maps_requested = Signal(str)

    def __init__(self, end: str, placeholder: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._end = end
        self._candidates: list[object] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.field = kit.LineEdit(placeholder=placeholder)
        self.field.setMinimumWidth(320)
        row.addWidget(self.field, 1)
        row.addWidget(
            kit.Button(
                "Find", on_click=lambda: self.lookup_requested.emit(self._end, self.field.text())
            )
        )
        layout.addLayout(row)

        # The candidate picker, hidden until a text search returns more than nothing.
        pick_row = QHBoxLayout()
        pick_row.setSpacing(8)
        self.candidates = kit.ComboBox()
        self.candidates.setMinimumWidth(320)
        pick_row.addWidget(self.candidates, 1)
        self.use = kit.Button("Use this", variant="primary", on_click=self._pick)
        pick_row.addWidget(self.use)
        self._pick_host = kit.Box()
        self._pick_host.setLayout(pick_row)
        self._pick_host.setVisible(False)
        layout.addWidget(self._pick_host)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.status = kit.Text(role="caption", tone="muted", wrap=True)
        status_row.addWidget(self.status, 1)
        self.open_maps = kit.Button(
            "Open in Google Maps",
            variant="quiet",
            on_click=lambda: self.open_maps_requested.emit(self._end),
        )
        self.open_maps.setVisible(False)
        status_row.addWidget(self.open_maps)
        layout.addLayout(status_row)

    def show_candidates(self, places: list[object]) -> None:
        """Offer a choice, and save nothing until it is made."""
        self._candidates = list(places)
        self.candidates.clear()
        for place in self._candidates:
            self.candidates.addItem(str(getattr(place, "resolved", place)))
        self._pick_host.setVisible(bool(self._candidates))
        if len(self._candidates) > 1:
            self.status.setText(f"{len(self._candidates)} places matched — pick the right one.")
        elif self._candidates:
            self.status.setText("One match — check it is the right place before using it.")

    def show_status(self, text: str, *, located: bool = False) -> None:
        """The line under the field: what was matched, or what to fix."""
        self.status.setText(text)
        self.open_maps.setVisible(located)
        if located:
            self._pick_host.setVisible(False)

    def _pick(self) -> None:
        index = self.candidates.currentIndex()
        if 0 <= index < len(self._candidates):
            self.place_picked.emit(self._end, self._candidates[index])


class Card(kit.Surface):
    """A titled group of settings."""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent, variant="card")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.GAP + 2, Space.GAP, Space.GAP + 2, Space.GAP)
        layout.setSpacing(10)

        layout.addWidget(kit.Text(title, self, role="body-strong"))
        if subtitle:
            layout.addWidget(kit.Text(subtitle, self, role="caption", tone="muted", wrap=True))

        self.form = QFormLayout()
        self.form.setSpacing(9)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        # Fields keep their own width instead of stretching across the card.
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        layout.addLayout(self.form)
        self.body = layout

    def add(self, label: str, widget: QWidget) -> None:
        self.form.addRow(kit.Text(label, self), widget)

    def add_full(self, widget: QWidget) -> None:
        """Add something spanning both label and field columns, like a checkbox."""
        self.form.addRow(widget)

    def finish(self) -> Card:
        """Pin the content to the top.

        Grid cells stretch every widget to the tallest in the row, so without this a short
        card spreads its rows out to fill the height.
        """
        self.body.addStretch(1)
        return self


class SettingsView(QWidget):
    """Edits the configuration and hands a new :class:`AppConfig` back."""

    config_saved = Signal(object)  # AppConfig
    sign_out_requested = Signal(bool)  # forget stored credentials
    clear_cache_requested = Signal()
    sync_history_requested = Signal()
    cancel_history_requested = Signal()
    test_notification_requested = Signal()
    #: (end, text) — resolve whatever was pasted for one end of the journey. The text rides
    #: in the signal rather than being read back from the widget, so nothing off the GUI
    #: thread ever touches a Qt object.
    address_lookup_requested = Signal(str, str)
    #: (end, Place) — the user chose one of the offered candidates.
    place_picked = Signal(str, object)
    #: (end,) — show the saved point in the browser.
    open_in_maps_requested = Signal(str)
    key_check_requested = Signal(str)
    key_guide_requested = Signal()

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # The title row, with Save and Revert in it, stays out of the scroll area so it is
        # always reachable.
        outer.addWidget(self._build_save_bar())

        # Settings should never scroll sideways; the grid narrows instead.
        scroll = kit.ScrollArea(margins=(Space.PAGE, Space.SNUG, Space.PAGE, Space.PAGE))
        outer.addWidget(scroll, 1)
        page = scroll.body
        page.setSpacing(14)

        self.banner = Banner()
        page.addWidget(self.banner)

        # Twelve cards in one grid read as a wall. Grouped under headings, each group its own
        # two-column grid — so a tall card stretches only its neighbour, never a stranger
        # three rows down — with a jump list at the top so nothing is more than a click away.
        self._sections: list[kit.SectionHeader] = []
        # Wraps rather than running in one line: eight buttons sized by their labels needed
        # 1,050 px, which pushed the whole page sideways off a laptop at 175%.
        jump_host = kit.Box()
        jump = FlowLayout(jump_host, spacing=Space.SNUG)
        page.addWidget(jump_host)
        #: Every section's grid and the cards in it, for re-laying out at a new width.
        self._grids: list[tuple[QGridLayout, tuple[tuple[QWidget, int, int, int], ...]]] = []
        self._columns = 2

        def section(title: str, *cards: tuple[QWidget, int, int, int]) -> None:
            heading = kit.SectionHeader(title)
            self._sections.append(heading)
            jump.addWidget(
                kit.Button(
                    title,
                    variant="quiet",
                    small=True,
                    on_click=partial(scroll.ensureWidgetVisible, heading),
                )
            )
            page.addSpacing(6)
            page.addWidget(heading)
            grid = QGridLayout()
            grid.setHorizontalSpacing(GRID_GAP)
            grid.setVerticalSpacing(GRID_GAP)
            self._grids.append((grid, cards))
            _place(grid, cards, columns=2)
            page.addLayout(grid)

        section("Account", (self._build_account(), 0, 0, 1), (self._build_cache(), 0, 1, 1))
        section("Work day", (self._build_shift(), 0, 0, 1), (self._build_leave_rules(), 0, 1, 1))
        section("Notifications", (self._build_notifications(), 0, 0, 2))
        section("Sync & data", (self._build_sync(), 0, 0, 1), (self._build_history(), 0, 1, 1))
        section("Appearance", (self._build_appearance(), 0, 0, 1), (self._build_window(), 0, 1, 1))
        # Journey home keeps the full width: its address fields are the only controls on
        # this page that genuinely need it.
        section("Journey home", (self._build_commute(), 0, 0, 2))
        section("Daily & Pay", (self._build_daily(), 0, 0, 1), (self._build_pay(), 0, 1, 1))
        section("Updates", (self._build_updates(), 0, 0, 2))

        page.addStretch(1)
        self._load(config)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 — Qt override
        """Two columns where two fit, one where they do not.

        Two 380 px columns are 774 px of cards; at the window's minimum width, or a laptop
        at 150% and up with the window not maximised, the page is narrower than that, and a
        grid that will not narrow is one that is cut off at the right.
        """
        super().resizeEvent(event)
        room = self.width() - PAGE_MARGINS - SCROLLBAR_ROOM
        columns = 2 if self._two_columns_fit(room) else 1
        if columns != self._columns:
            self._columns = columns
            for grid, cards in self._grids:
                _place(grid, cards, columns=columns)

    def _two_columns_fit(self, room: int) -> bool:
        """Whether every section's two columns fit, measured from the cards themselves.

        Not from the 380 px floor alone: Appearance needs nearer 600 for its picture row,
        and a test of the floor said two columns fit at 175% while the page was 149 px too
        wide for its window.
        """
        for _grid, cards in self._grids:
            columns, full = [COLUMN_MIN_WIDTH, COLUMN_MIN_WIDTH], 0
            for card, _row, column, span in cards:
                need = card.minimumSizeHint().width()
                if span == 2:
                    full = max(full, need)
                else:
                    columns[column] = max(columns[column], need)
            if max(sum(columns) + GRID_GAP, full) > room:
                return False
        return True

    # --- cards ----------------------------------------------------------------------

    def _build_account(self) -> Card:
        card = Card("Account", "Your password is kept in the Windows Credential Manager.")
        self._username = kit.Text(role="body-strong")
        card.add("Signed in as", self._username)

        self._remember = kit.CheckBox("Remember me on this device")
        card.add_full(self._remember)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(
            kit.Button("Sign out", on_click=lambda: self.sign_out_requested.emit(False))
        )
        buttons.addWidget(
            kit.Button(
                "Sign out and forget password",
                variant="danger",
                on_click=lambda: self.sign_out_requested.emit(True),
            )
        )
        buttons.addStretch(1)
        card.body.addLayout(buttons)
        return card.finish()

    def _build_shift(self) -> Card:
        card = Card(
            "Shift policy",
            "A shift is complete once the span from your first In to your last Out reaches "
            "the shift span, breaks included. The work target and break allowance are shown "
            "beside it, and decide nothing.",
        )
        self._work = _hours()
        self._break = _hours()
        self._span = _hours()
        card.add("Shift span", self._span)
        card.add("Work target", self._work)
        card.add("Break allowance", self._break)
        return card.finish()

    def _build_sync(self) -> Card:
        card = Card("Sync")
        self._interval = _minutes()
        self._ttl = _minutes()
        self._history = kit.SpinBox()
        self._history.setRange(1, 36)
        self._history.setSuffix(" months")
        self._history.setFixedWidth(WIDE_NUMBER_WIDTH)

        card.add("Refresh every", self._interval)
        card.add("Cache is fresh for", self._ttl)
        card.add("Keep history for", self._history)
        return card.finish()

    def _choose_wallpaper(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _filter = QFileDialog.getOpenFileName(
            self, "Choose a background picture", "", "Pictures (*.png *.jpg *.jpeg *.webp *.bmp)"
        )
        if path:
            self._wallpaper_path.setText(path)

    def _build_appearance(self) -> Card:
        card = Card("Appearance")
        self._theme = _choice([(THEME_LABELS[name], name) for name in THEMES])
        # A row of the theme's own colours beside the picker, so choosing one is not a
        # guess from its name.
        self._swatch = ThemeSwatch()
        self._theme.currentIndexChanged.connect(
            lambda _index: self._swatch.set_theme(str(self._theme.currentData()))
        )
        theme_row = QHBoxLayout()
        theme_row.setSpacing(8)
        theme_row.addWidget(self._theme)
        theme_row.addWidget(self._swatch)
        theme_row.addStretch(1)
        theme_host = kit.Box()
        theme_host.setLayout(theme_row)

        # The wallpaper. The app ships no imagery: this is the user's own picture, which is
        # what makes the anime theme *their* character rather than one nobody can bundle.
        self._wallpaper_path = kit.LineEdit(placeholder="No picture — the theme's plain surface")
        self._wallpaper_path.setReadOnly(True)
        self._wallpaper_path.setMinimumWidth(200)
        wallpaper_row = QHBoxLayout()
        wallpaper_row.setSpacing(8)
        wallpaper_row.addWidget(self._wallpaper_path)
        wallpaper_row.addWidget(kit.Button("Choose…", on_click=self._choose_wallpaper))
        wallpaper_row.addWidget(
            kit.Button("Clear", variant="quiet", on_click=lambda: self._wallpaper_path.setText(""))
        )
        wallpaper_host = kit.Box()
        wallpaper_host.setLayout(wallpaper_row)

        self._wallpaper_on = kit.CheckBox("Show the picture")
        self._wallpaper_on.setToolTip("Off keeps the picture on file; on brings it back.")
        self._wallpaper_strength = QSlider(Qt.Orientation.Horizontal)
        self._wallpaper_strength.setRange(0, 100)
        self._wallpaper_strength.setFixedWidth(CHOICE_WIDTH)
        self._wallpaper_strength.setToolTip(
            "How much of the theme's own colour is laid over the picture. Higher keeps the "
            "text easier to read on a busy image."
        )
        self._tone = _choice([("Playful", "playful"), ("Plain", "plain")])
        self._tone.setToolTip(
            "Playful adds a light remark to good news. Warnings stay plain either way."
        )

        card.add("Theme", theme_host)
        card.add("Background picture", wallpaper_host)
        card.add_full(self._wallpaper_on)
        card.add("Picture dimming", self._wallpaper_strength)
        card.add("Wording", self._tone)
        return card.finish()

    def _build_window(self) -> Card:
        """How the window behaves, split from how it looks."""
        card = Card("Window & startup")
        self._background = _choice(
            [
                ("Keep running in the tray", "tray"),
                ("Close fully", "foreground"),
            ]
        )
        self._startup = kit.CheckBox("Start when I sign in to Windows")
        card.add("On window close", self._background)
        card.add_full(self._startup)
        return card.finish()

    def _build_daily(self) -> Card:
        """The two switches for the sidebar tile. Off means no request is made at all."""
        card = Card("Daily", "A quote and a picture, once a day, in the sidebar.")
        self._daily_quote = kit.CheckBox("Quote of the day")
        self._daily_picture = kit.CheckBox("Picture of the day")
        self._quote_source = kit.ComboBox()
        for value, label in QUOTE_SOURCE_LABELS:
            self._quote_source.addItem(label, value)
        self._quote_source.setMinimumWidth(CHOICE_WIDTH)
        # Off means off: the source has nothing to choose between with no quote asked for.
        self._daily_quote.toggled.connect(self._quote_source.setEnabled)
        card.add_full(self._daily_quote)
        card.add("Quotes from", self._quote_source)
        card.add_full(self._daily_picture)
        # The providers' credit, beside the switches rather than under the picture.
        providers = {name: (url, line) for name, url, line in about.PROVIDERS}
        zen, anime, bing = providers["ZenQuotes"], providers["Animechan"], providers["Bing"]
        credit = kit.LinkText(
            f'<a href="{zen[0]}">{zen[1]}</a>. <a href="{anime[0]}">{anime[1]}</a>. '
            f'Pictures are <a href="{bing[0]}">Bing</a>\'s picture of the day, with its own '
            "copyright line in the viewer."
        )
        card.add_full(credit)
        return card.finish()

    def _build_pay(self) -> Card:
        """One switch. The screen it enables fetches only on its own Refresh."""
        card = Card(
            "Pay",
            "Payslips, the CTC statement and the monthly report, read from the portal when "
            "you ask. Kept encrypted to your Windows account and masked on screen.",
        )
        self._pay_enabled = kit.CheckBox("Show the Pay screen")
        card.add_full(self._pay_enabled)
        return card.finish()

    def _build_notifications(self) -> Card:
        """Master toggle, quiet hours, and one switch per alert kind."""
        card = Card(
            "Notifications",
            "Each alert appears at most once a day, and never during quiet hours.",
        )
        self._notify = kit.CheckBox("Show desktop notifications")
        self._notify.toggled.connect(self._sync_notification_state)
        card.add_full(self._notify)

        self._quiet_start = _time_field()
        self._quiet_end = _time_field()
        card.add("Quiet hours from", self._quiet_start)
        card.add("Quiet hours until", self._quiet_end)

        self._alerts: dict[str, kit.CheckBox] = {}
        # Two columns. Eight checkboxes stacked one per line made this the tallest thing on
        # the page and left the whole right half of the card empty; the labels are terse
        # enough to sit two-up without inflating the card's minimum width.
        alerts = QGridLayout()
        alerts.setHorizontalSpacing(18)
        alerts.setVerticalSpacing(6)
        alerts.setColumnStretch(1, 1)
        for index, (field, label) in enumerate(
            (
                ("work_target_reached", "Target reached"),
                ("short_hours_warning", "Short of hours"),
                ("swipe_request_needed", "Swipe request needed"),
                ("swipe_request_decided", "Request decided"),
                ("break_exceeded", "Break ran over"),
                ("leave_expiring", "Leave expiring"),
                # Nudges, listed with the alerts but separately switchable: they are remarks
                # about a habit rather than warnings about a figure, and somebody who wants
                # the facts without the company should be able to say so.
                ("break_reminder", "Hours without a break"),
                ("leave_reminder", "A long time since leave"),
            )
        ):
            box = kit.CheckBox(label)
            self._alerts[field] = box
            alerts.addWidget(box, index // 2, index % 2)
        alerts_host = kit.Box()
        alerts_host.setLayout(alerts)
        card.add_full(alerts_host)

        # The commonest complaint about notifications is that nothing happens, and until
        # this button the app had no way to say which of half a dozen reasons applied.
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(
            kit.Button("Send a test notification", on_click=self.test_notification_requested.emit)
        )
        row.addStretch(1)
        card.body.addLayout(row)
        return card.finish()

    def _build_leave_rules(self) -> Card:
        card = Card(
            "Leave rules",
            "Rules CerePulse cannot read anywhere. Nothing in SpineHR states them, so "
            "these are what you tell the app your employer does — not what it has found "
            "out. Leave them alone if you are not sure.",
        )
        self._sandwich = kit.ComboBox()
        for rule in SandwichRule:
            self._sandwich.addItem(rule.label, rule.value)
        self._sandwich.setToolTip(
            "Many employers charge the weekend or holiday between two leave days against "
            "your balance. CerePulse cannot tell whether yours does, so it assumes not."
        )
        card.add("Sandwich leave", self._sandwich)
        return card.finish()

    def _build_commute(self) -> Card:
        """The journey home, and the key that makes it possible.

        The key needs its own explanation on screen, because "why am I being asked for an
        API key" is a fair question. The honest answer is short: a key shipped inside the
        app would be a key published with it.
        """
        card = Card(
            "Journey home",
            "Works out when you would actually get home, from your predicted leave time. "
            "Needs a free TomTom API key — 20,000 lookups a month, no card. The key is "
            "yours rather than built in, because a key inside a public download is a "
            "published key. It is kept in the Windows Credential Manager, never in a file. "
            "For an exact point, copy it straight out of Google Maps — right-click the "
            "place and click the numbers, or paste the whole link.",
        )

        self._home = AddressField("home", "Address, Google Maps link, coordinates, or Plus Code")
        self._home.lookup_requested.connect(self.address_lookup_requested)
        self._home.place_picked.connect(self.place_picked)
        self._home.open_maps_requested.connect(self.open_in_maps_requested)
        card.add("Home", self._home)

        self._office = AddressField("office", "Where you work — pin it the same way")
        self._office.lookup_requested.connect(self.address_lookup_requested)
        self._office.place_picked.connect(self.place_picked)
        self._office.open_maps_requested.connect(self.open_in_maps_requested)
        card.add("Office", self._office)

        self._mode = _choice(
            [
                ("Motorcycle", "motorcycle"),
                ("Car", "car"),
                ("Bus", "bus"),
                ("Bicycle", "bicycle"),
                ("Walking", "pedestrian"),
            ]
        )
        card.add("Travel by", self._mode)

        self._buffer = kit.SpinBox()
        self._buffer.setRange(0, 60)
        self._buffer.setSuffix(" min")
        self._buffer.setFixedWidth(NUMBER_WIDTH)
        self._buffer.setToolTip(
            "Reaching your vehicle, parking, the walk at either end. A fact about your "
            "building rather than the road, so only you can supply it."
        )
        card.add("Add for parking etc.", self._buffer)

        self._api_key = kit.LineEdit(placeholder="Paste your TomTom key")
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setMinimumWidth(320)
        # Reveals only what was just pasted: a saved key is never put back into the field.
        add_reveal_toggle(self._api_key)
        card.add("TomTom API key", self._api_key)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(
            kit.Button(
                "Check key", on_click=lambda: self.key_check_requested.emit(self._api_key.text())
            )
        )
        row.addWidget(
            kit.Button("How do I get one?", variant="quiet", on_click=self.key_guide_requested.emit)
        )
        row.addStretch(1)
        card.body.addLayout(row)

        self._key_state = kit.Text(role="caption", tone="muted", wrap=True)
        card.add_full(self._key_state)
        return card.finish()

    def address_field(self, end: str) -> AddressField:
        """The field for one end of the journey, addressed by name.

        The main window's lookup handlers work per end, and routing their results through a
        name keeps them from holding widget references across a worker call.
        """
        return self._home if end == "home" else self._office

    def show_key_result(self, text: str) -> None:
        self._key_state.setText(text)

    def _build_history(self) -> Card:
        card = Card(
            "History",
            "Every month the portal serves — the current year, back to January — with each "
            "day's punch log, so trends and month-over-month comparisons have something to "
            "work with. Download again fetches every month afresh.",
        )
        self._year_status = kit.Text(role="caption", tone="muted", wrap=True)
        card.body.addWidget(self._year_status)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(kit.Button("Download again", on_click=self.sync_history_requested.emit))
        row.addWidget(
            kit.Button("Stop", variant="quiet", on_click=self.cancel_history_requested.emit)
        )
        row.addStretch(1)
        card.body.addLayout(row)
        return card.finish()

    def show_year_backfill(self, text: str) -> None:
        """Where the full-year download stands, in one line."""
        self._year_status.setText(text)

    def _build_updates(self) -> Card:
        card = Card(
            "Updates",
            "Beta sees early builds from the development branch. Switching back to stable "
            "does not undo an update — it stops offering new prereleases, and the next "
            "stable release picks you up.",
        )
        self._channel = _choice([("Stable", "stable"), ("Beta", "beta")])
        self._check_startup = kit.CheckBox("Check when CerePulse starts")
        self._auto_download = kit.CheckBox("Download updates in the background")
        self._auto_download.setToolTip("Installing always waits for you to say yes.")

        card.add("Channel", self._channel)
        card.add_full(self._check_startup)
        card.add_full(self._auto_download)
        return card.finish()

    def _build_cache(self) -> Card:
        card = Card(
            "Local data",
            "Attendance and leave are cached on this device so the app opens instantly and "
            "works offline. Clearing it changes nothing in SpineHR.",
        )
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(
            kit.Button(
                "Clear cached data", variant="danger", on_click=self.clear_cache_requested.emit
            )
        )
        row.addStretch(1)
        card.body.addLayout(row)
        return card.finish()

    def _build_save_bar(self) -> QWidget:
        bar = kit.Box()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(Space.PAGE, 28, Space.PAGE, Space.SNUG)
        layout.setSpacing(Space.SNUG)
        layout.addWidget(kit.Text("Settings", role="title"))
        layout.addSpacing(Space.GAP)

        # The outcome lives beside the button that caused it. It used to go to the banner
        # at the top of the scrolling page while the bar sat pinned under it, so the button
        # and its answer were never on screen at the same time.
        self._save_status = kit.Text(role="caption", tone="muted", wrap=True)
        layout.addWidget(self._save_status, 1)

        layout.addWidget(kit.Button("Revert", variant="quiet", on_click=self._revert))
        layout.addWidget(kit.Button("Save changes", variant="primary", on_click=self._save))
        return bar

    def _revert(self) -> None:
        self._load(self._config)
        self.show_save_result("Reverted to the last saved settings.")

    def show_save_result(self, text: str, *, failed: bool = False) -> None:
        """Report a save outcome where the Save button is, styled by how it went."""
        self._save_status.setText(text)
        self._save_status.set_tone("bad" if failed else "muted")

    # --- state ----------------------------------------------------------------------

    def _sync_notification_state(self, enabled: bool) -> None:
        """Grey out the per-alert switches when notifications are off entirely."""
        for box in self._alerts.values():
            box.setEnabled(enabled)
        self._quiet_start.setEnabled(enabled)
        self._quiet_end.setEnabled(enabled)

    def _load(self, config: AppConfig) -> None:
        self._username.setText(config.portal.username or "Not signed in")
        self._remember.setChecked(config.portal.remember_me)

        self._work.setValue(config.shift.work_target_hours)
        self._break.setValue(config.shift.break_target_hours)
        self._span.setValue(config.shift.shift_span_hours)

        self._interval.setValue(config.sync.refresh_interval_minutes)
        self._ttl.setValue(config.sync.cache_ttl_minutes)
        self._history.setValue(config.sync.history_months)

        self._theme.setCurrentIndex(max(0, self._theme.findData(config.ui.theme)))
        self._swatch.set_theme(config.ui.theme)
        self._daily_quote.setChecked(config.daily.quote)
        self._daily_picture.setChecked(config.daily.picture)
        self._quote_source.setCurrentIndex(
            max(0, self._quote_source.findData(config.daily.quote_source))
        )
        self._quote_source.setEnabled(config.daily.quote)
        self._pay_enabled.setChecked(config.pay.enabled)
        self._wallpaper_path.setText(config.ui.background_image)
        self._wallpaper_on.setChecked(config.ui.background_enabled)
        self._wallpaper_strength.setValue(config.ui.background_strength)
        self._background.setCurrentIndex(
            max(0, self._background.findData(config.ui.background_mode))
        )
        self._startup.setChecked(config.ui.start_with_windows)
        self._tone.setCurrentIndex(max(0, self._tone.findData(config.ui.tone)))

        self._channel.setCurrentIndex(max(0, self._channel.findData(config.updates.channel)))
        self._check_startup.setChecked(config.updates.check_on_startup)
        self._auto_download.setChecked(config.updates.download_automatically)

        notifications = config.notifications
        self._notify.setChecked(notifications.enabled)
        self._quiet_start.setTime(_to_qtime(notifications.quiet_hours_start))
        self._quiet_end.setTime(_to_qtime(notifications.quiet_hours_end))
        for field, box in self._alerts.items():
            box.setChecked(bool(getattr(notifications, field, True)))
        self._sync_notification_state(notifications.enabled)

        rule = self._sandwich.findData(config.leave_rules.sandwich_rule)
        self._sandwich.setCurrentIndex(rule if rule >= 0 else 0)

        commute = config.commute
        self._home.field.setText(commute.destination)
        self._home.show_status(
            _located(commute.destination_lat, commute.destination_lon),
            located=commute.destination_lat != 0.0 or commute.destination_lon != 0.0,
        )
        self._office.field.setText(commute.origin)
        self._office.show_status(
            _located(commute.origin_lat, commute.origin_lon),
            located=commute.origin_lat != 0.0 or commute.origin_lon != 0.0,
        )
        self._mode.setCurrentIndex(max(0, self._mode.findData(commute.mode)))
        self._buffer.setValue(commute.buffer_minutes)

    def _save(self) -> None:
        # Checked here, before anything is written: the loader refuses these at launch, and
        # a config it refuses is an app that does not start, with no window to say why.
        if self._span.value() < self._work.value() + self._break.value():
            self.show_save_result(
                "Not saved: the shift span has to be at least the work target plus the "
                "break allowance.",
                failed=True,
            )
            return
        config = self._config
        updated = replace(
            config,
            portal=replace(config.portal, remember_me=self._remember.isChecked()),
            shift=replace(
                config.shift,
                work_target_hours=self._work.value(),
                break_target_hours=self._break.value(),
                shift_span_hours=self._span.value(),
            ),
            sync=replace(
                config.sync,
                refresh_interval_minutes=self._interval.value(),
                cache_ttl_minutes=self._ttl.value(),
                history_months=self._history.value(),
            ),
            ui=replace(
                config.ui,
                theme=self._theme.currentData(),
                background_image=self._wallpaper_path.text().strip(),
                background_enabled=self._wallpaper_on.isChecked(),
                background_strength=self._wallpaper_strength.value(),
                background_mode=self._background.currentData(),
                start_with_windows=self._startup.isChecked(),
                tone=self._tone.currentData(),
            ),
            updates=replace(
                config.updates,
                channel=self._channel.currentData(),
                check_on_startup=self._check_startup.isChecked(),
                download_automatically=self._auto_download.isChecked(),
            ),
            daily=replace(
                config.daily,
                quote=self._daily_quote.isChecked(),
                picture=self._daily_picture.isChecked(),
                quote_source=self._quote_source.currentData(),
            ),
            pay=replace(config.pay, enabled=self._pay_enabled.isChecked()),
            notifications=replace(
                config.notifications,
                enabled=self._notify.isChecked(),
                quiet_hours_start=self._quiet_start.time().toString("HH:mm"),
                quiet_hours_end=self._quiet_end.time().toString("HH:mm"),
                **{field: box.isChecked() for field, box in self._alerts.items()},
            ),
            leave_rules=replace(config.leave_rules, sandwich_rule=self._sandwich.currentData()),
            commute=self._collect_commute(config),
        )
        self._config = updated
        self.config_saved.emit(updated)

    def _collect_commute(self, config: AppConfig) -> CommuteConfig:
        """The commute section on Save.

        The addresses and coordinates are written by the Find flow at the moment a place is
        confirmed, so Save deliberately does not copy the field text over them — that would
        overwrite a picked pin with whatever draft happened to be sitting in the box. The
        one exception is a *cleared* field: emptying it clears the stored point too, or the
        app would keep routing to the old house behind a blank box.
        """
        commute = replace(
            config.commute,
            mode=self._mode.currentData(),
            buffer_minutes=self._buffer.value(),
        )
        if not self._home.field.text().strip():
            commute = replace(commute, destination="", destination_lat=0.0, destination_lon=0.0)
        return commute

    def typed_key(self) -> str:
        """Whatever is in the key field right now, for the window to store."""
        return self._api_key.text().strip()

    def show_stored_key(self, present: bool) -> None:
        """Say a key is on file without ever putting it back on screen.

        Reading it back into the field would make it visible to anything that can screenshot
        the window, and would tempt somebody into copying it out of an app that is supposed
        to be the safe place for it.
        """
        if present and not self._api_key.text():
            self._api_key.setPlaceholderText("A key is saved — paste a new one to replace it")

    def set_config(self, config: AppConfig) -> None:
        self._config = config
        self._load(config)


def _located(latitude: float, longitude: float) -> str:
    if latitude == 0.0 and longitude == 0.0:
        return "Not found yet — type an address and press Find."
    return f"Resolved to {latitude:.4f}, {longitude:.4f}."


# --- control factories ----------------------------------------------------------------


def _hours() -> kit.DoubleSpinBox:
    spin = kit.DoubleSpinBox()
    spin.setRange(0.0, 24.0)
    spin.setSingleStep(0.25)
    spin.setDecimals(2)
    spin.setSuffix(" h")
    spin.setFixedWidth(NUMBER_WIDTH)
    return spin


def _minutes() -> kit.SpinBox:
    spin = kit.SpinBox()
    spin.setRange(1, 240)
    spin.setSuffix(" min")
    spin.setFixedWidth(NUMBER_WIDTH)
    return spin


class ThemeSwatch(QWidget):
    """Five chips of a theme's colours: surface, card, work, break, good."""

    CHIP = 18

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette: Palette = palette_for("dark")
        self.setFixedSize(self.CHIP * 5 + 8, self.CHIP + 4)
        self.setToolTip("How this theme looks")

    def set_theme(self, name: str) -> None:
        self._palette = palette_for(name)
        self.update()

    def paintEvent(self, event: object) -> None:  # noqa: N802 — Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colours = (
            self._palette.surface,
            self._palette.elevated,
            self._palette.work,
            self._palette.rest,
            self._palette.good,
        )
        for index, colour in enumerate(colours):
            painter.setPen(QPen(QColor(self._palette.border), 1))
            painter.setBrush(QColor(colour))
            painter.drawRoundedRect(
                QRectF(index * (self.CHIP + 2) + 1, 2, self.CHIP, self.CHIP), 4, 4
            )


def _choice(options: list[tuple[str, str]]) -> kit.ComboBox:
    combo = kit.ComboBox()
    for label, value in options:
        combo.addItem(label, value)
    combo.setFixedWidth(CHOICE_WIDTH)
    return combo


def _time_field() -> kit.TimeEdit:
    field = kit.TimeEdit(display="HH:mm")
    field.setFixedWidth(TIME_WIDTH)
    return field


def _to_qtime(text: str) -> QTime:
    parsed = QTime.fromString(text, "HH:mm")
    return parsed if parsed.isValid() else QTime(0, 0)


def _place(
    grid: QGridLayout, cards: tuple[tuple[QWidget, int, int, int], ...], *, columns: int
) -> None:
    """Lay a section's cards out in two columns as designed, or stacked in one."""
    for card, *_rest in cards:
        grid.removeWidget(card)
    if columns == 2:
        for card, row, column, span in cards:
            grid.addWidget(card, row, column, 1, span)
        for column in (0, 1):
            grid.setColumnStretch(column, 1)
            grid.setColumnMinimumWidth(column, COLUMN_MIN_WIDTH)
        return
    for index, (card, *_rest) in enumerate(cards):
        grid.addWidget(card, index, 0, 1, 1)
    grid.setColumnStretch(0, 1)
    grid.setColumnStretch(1, 0)
    grid.setColumnMinimumWidth(0, 0)
    grid.setColumnMinimumWidth(1, 0)
