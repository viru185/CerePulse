"""Settings — grouped into cards across two columns.

A single stacked column of full-width controls wastes most of a 1120px window and pushes
half the settings below the fold. Related settings are grouped into cards instead, laid out
two across, and every control is sized to its content: a spin box holding "8.00" has no
business being a thousand pixels wide.

Changes are collected and applied on Save rather than written per keystroke, so a half-typed
value never becomes the live policy.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, QTime, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from cerepulse.core.config import AppConfig, CommuteConfig
from cerepulse.intelligence.sandwich import SandwichRule
from cerepulse.ui.widgets import Banner

#: Control widths, so nothing stretches to fill the card. Spin boxes need room for their
#: suffix *and* their stepper buttons, or the arrows sit on top of the number.
NUMBER_WIDTH = 122
WIDE_NUMBER_WIDTH = 148
CHOICE_WIDTH = 190
TIME_WIDTH = 108

#: Floor for each grid column. Without it the column holding the widest label claims the
#: extra space and the other column's cards are clipped, since setColumnStretch only shares
#: out what is left *after* minimum sizes are met.
COLUMN_MIN_WIDTH = 380


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
        self.field = QLineEdit()
        self.field.setPlaceholderText(placeholder)
        self.field.setMinimumWidth(320)
        row.addWidget(self.field, 1)
        find = QPushButton("Find")
        find.clicked.connect(lambda: self.lookup_requested.emit(self._end, self.field.text()))
        row.addWidget(find)
        layout.addLayout(row)

        # The candidate picker, hidden until a text search returns more than nothing.
        pick_row = QHBoxLayout()
        pick_row.setSpacing(8)
        self.candidates = QComboBox()
        self.candidates.setMinimumWidth(320)
        pick_row.addWidget(self.candidates, 1)
        self.use = QPushButton("Use this")
        self.use.setObjectName("Primary")
        self.use.clicked.connect(self._pick)
        pick_row.addWidget(self.use)
        self._pick_host = QWidget()
        self._pick_host.setLayout(pick_row)
        self._pick_host.setVisible(False)
        layout.addWidget(self._pick_host)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.status = QLabel()
        self.status.setObjectName("CardCaption")
        self.status.setWordWrap(True)
        status_row.addWidget(self.status, 1)
        self.open_maps = QPushButton("Open in Google Maps")
        self.open_maps.clicked.connect(lambda: self.open_maps_requested.emit(self._end))
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


class Card(QFrame):
    """A titled group of settings."""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        heading = QLabel(title)
        heading.setStyleSheet("font-size: 14px; font-weight: 700;")
        layout.addWidget(heading)

        if subtitle:
            caption = QLabel(subtitle)
            caption.setObjectName("CardCaption")
            caption.setWordWrap(True)
            layout.addWidget(caption)

        self.form = QFormLayout()
        self.form.setSpacing(9)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        # Fields keep their own width instead of stretching across the card.
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        layout.addLayout(self.form)
        self.body = layout

    def add(self, label: str, widget: QWidget) -> None:
        self.form.addRow(label, widget)

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

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        # Settings should never scroll sideways; the grid narrows instead.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll, 1)

        content = QWidget()
        scroll.setWidget(content)
        page = QVBoxLayout(content)
        page.setContentsMargins(24, 20, 24, 20)
        page.setSpacing(14)

        self.banner = Banner()
        page.addWidget(self.banner)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(0, COLUMN_MIN_WIDTH)
        grid.setColumnMinimumWidth(1, COLUMN_MIN_WIDTH)
        page.addLayout(grid)

        grid.addWidget(self._build_account(), 0, 0)
        grid.addWidget(self._build_shift(), 0, 1)
        grid.addWidget(self._build_notifications(), 1, 0, 2, 1)
        grid.addWidget(self._build_sync(), 1, 1)
        grid.addWidget(self._build_appearance(), 2, 1)
        grid.addWidget(self._build_history(), 3, 0)
        grid.addWidget(self._build_cache(), 3, 1)
        # Leave rules and Updates were a full-width row each — two cards of four controls
        # between them, each leaving half the page empty. Paired, they cost one row instead
        # of two. Journey home keeps the full width: its address fields are the only
        # controls on this page that genuinely need it.
        grid.addWidget(self._build_leave_rules(), 4, 0)
        grid.addWidget(self._build_updates(), 4, 1)
        grid.addWidget(self._build_commute(), 5, 0, 1, 2)

        page.addStretch(1)

        # The save bar stays out of the scroll area so it is always reachable.
        outer.addWidget(self._build_save_bar())
        self._load(config)

    # --- cards ----------------------------------------------------------------------

    def _build_account(self) -> Card:
        card = Card("Account", "Your password is kept in the Windows Credential Manager.")
        self._username = QLabel()
        self._username.setStyleSheet("font-weight: 600;")
        card.add("Signed in as", self._username)

        self._remember = QCheckBox("Remember me on this device")
        card.add_full(self._remember)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        sign_out = QPushButton("Sign out")
        sign_out.clicked.connect(lambda: self.sign_out_requested.emit(False))
        forget = QPushButton("Sign out and forget password")
        forget.clicked.connect(lambda: self.sign_out_requested.emit(True))
        buttons.addWidget(sign_out)
        buttons.addWidget(forget)
        buttons.addStretch(1)
        card.body.addLayout(buttons)
        return card.finish()

    def _build_shift(self) -> Card:
        card = Card(
            "Shift policy",
            "Company policy, not something the portal reports. Defaults assume an "
            "eight-hour day with a one-hour break inside a nine-hour span.",
        )
        self._work = _hours()
        self._break = _hours()
        self._span = _hours()
        card.add("Work target", self._work)
        card.add("Break allowance", self._break)
        card.add("Shift span", self._span)
        return card.finish()

    def _build_sync(self) -> Card:
        card = Card("Sync")
        self._interval = _minutes()
        self._ttl = _minutes()
        self._history = QSpinBox()
        self._history.setRange(1, 36)
        self._history.setSuffix(" months")
        self._history.setFixedWidth(WIDE_NUMBER_WIDTH)

        card.add("Refresh every", self._interval)
        card.add("Cache is fresh for", self._ttl)
        card.add("Keep history for", self._history)
        return card.finish()

    def _build_appearance(self) -> Card:
        card = Card("Appearance")
        self._theme = _choice([("Dark", "dark"), ("Light", "light"), ("Follow Windows", "system")])
        self._background = _choice(
            [
                ("Keep running in the tray", "tray"),
                ("Close fully", "foreground"),
            ]
        )
        self._startup = QCheckBox("Start when I sign in to Windows")
        self._tone = _choice([("Playful", "playful"), ("Plain", "plain")])
        self._tone.setToolTip(
            "Playful adds a light remark to good news. Warnings stay plain either way."
        )

        card.add("Theme", self._theme)
        card.add("On window close", self._background)
        card.add("Wording", self._tone)
        card.add_full(self._startup)
        return card.finish()

    def _build_notifications(self) -> Card:
        """Master toggle, quiet hours, and one switch per alert kind."""
        card = Card(
            "Notifications",
            "Each alert appears at most once a day, and never during quiet hours.",
        )
        self._notify = QCheckBox("Show desktop notifications")
        self._notify.toggled.connect(self._sync_notification_state)
        card.add_full(self._notify)

        self._quiet_start = _time_field()
        self._quiet_end = _time_field()
        card.add("Quiet hours from", self._quiet_start)
        card.add("Quiet hours until", self._quiet_end)

        self._alerts: dict[str, QCheckBox] = {}
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
            box = QCheckBox(label)
            self._alerts[field] = box
            alerts.addWidget(box, index // 2, index % 2)
        alerts_host = QWidget()
        alerts_host.setLayout(alerts)
        card.add_full(alerts_host)

        # The commonest complaint about notifications is that nothing happens, and until
        # this button the app had no way to say which of half a dozen reasons applied.
        row = QHBoxLayout()
        row.setSpacing(8)
        test = QPushButton("Send a test notification")
        test.clicked.connect(self.test_notification_requested)
        row.addWidget(test)
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
        self._sandwich = QComboBox()
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

        self._buffer = QSpinBox()
        self._buffer.setRange(0, 60)
        self._buffer.setSuffix(" min")
        self._buffer.setFixedWidth(NUMBER_WIDTH)
        self._buffer.setToolTip(
            "Reaching your vehicle, parking, the walk at either end. A fact about your "
            "building rather than the road, so only you can supply it."
        )
        card.add("Add for parking etc.", self._buffer)

        self._api_key = QLineEdit()
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setPlaceholderText("Paste your TomTom key")
        self._api_key.setMinimumWidth(320)
        card.add("TomTom API key", self._api_key)

        row = QHBoxLayout()
        row.setSpacing(8)
        check = QPushButton("Check key")
        check.clicked.connect(lambda: self.key_check_requested.emit(self._api_key.text()))
        row.addWidget(check)
        guide = QPushButton("How do I get one?")
        guide.clicked.connect(self.key_guide_requested)
        row.addWidget(guide)
        row.addStretch(1)
        card.body.addLayout(row)

        self._key_state = QLabel()
        self._key_state.setObjectName("CardCaption")
        self._key_state.setWordWrap(True)
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
            "Pulls past months so trends and month-over-month comparisons have something "
            "to work with. The portal only serves the current year, so history stops at "
            "that January.",
        )
        row = QHBoxLayout()
        row.setSpacing(8)
        sync = QPushButton("Sync history")
        sync.clicked.connect(self.sync_history_requested)
        cancel = QPushButton("Stop")
        cancel.clicked.connect(self.cancel_history_requested)
        row.addWidget(sync)
        row.addWidget(cancel)
        row.addStretch(1)
        card.body.addLayout(row)
        return card.finish()

    def _build_updates(self) -> Card:
        card = Card(
            "Updates",
            "Beta sees early builds from the development branch. Switching back to stable "
            "does not undo an update — it stops offering new prereleases, and the next "
            "stable release picks you up.",
        )
        self._channel = _choice([("Stable", "stable"), ("Beta", "beta")])
        self._check_startup = QCheckBox("Check when CerePulse starts")
        self._auto_download = QCheckBox("Download updates in the background")
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
        clear = QPushButton("Clear cached data")
        clear.clicked.connect(self.clear_cache_requested)
        row.addWidget(clear)
        row.addStretch(1)
        card.body.addLayout(row)
        return card.finish()

    def _build_save_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("Sidebar")  # reuse the elevated surface
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 12, 24, 12)

        # The outcome lives beside the button that caused it. It used to go to the banner
        # at the top of the scrolling page — and the save bar is pinned *outside* the
        # scroll precisely so it is always reachable, which meant the button and its answer
        # were never on screen at the same time. Saving from the bottom of the page looked
        # like nothing happening.
        self._save_status = QLabel()
        self._save_status.setObjectName("CardCaption")
        self._save_status.setWordWrap(True)
        layout.addWidget(self._save_status, 1)

        revert = QPushButton("Revert")
        revert.clicked.connect(self._revert)
        layout.addWidget(revert)

        save = QPushButton("Save changes")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        layout.addWidget(save)
        return bar

    def _revert(self) -> None:
        self._load(self._config)
        self.show_save_result("Reverted to the last saved settings.")

    def show_save_result(self, text: str, *, failed: bool = False) -> None:
        """Report a save outcome where the Save button is, styled by how it went."""
        self._save_status.setText(text)
        # One hex rather than a palette lookup: this view is built before a Palette reaches
        # it, and the colour reads as "wrong" on both themes, which is all it must do.
        self._save_status.setStyleSheet("color: #e5484d;" if failed else "")

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


def _hours() -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0.0, 24.0)
    spin.setSingleStep(0.25)
    spin.setDecimals(2)
    spin.setSuffix(" h")
    spin.setFixedWidth(NUMBER_WIDTH)
    return spin


def _minutes() -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(1, 240)
    spin.setSuffix(" min")
    spin.setFixedWidth(NUMBER_WIDTH)
    return spin


def _choice(options: list[tuple[str, str]]) -> QComboBox:
    combo = QComboBox()
    for label, value in options:
        combo.addItem(label, value)
    combo.setFixedWidth(CHOICE_WIDTH)
    return combo


def _time_field() -> QTimeEdit:
    field = QTimeEdit()
    field.setDisplayFormat("HH:mm")
    field.setFixedWidth(TIME_WIDTH)
    return field


def _to_qtime(text: str) -> QTime:
    parsed = QTime.fromString(text, "HH:mm")
    return parsed if parsed.isValid() else QTime(0, 0)
