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
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from cerepulse.core.config import AppConfig
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
        # Kept terse: a long checkbox label inflates this card's minimum width and
        # squeezes the neighbouring column.
        for field, label in (
            ("work_target_reached", "Target reached"),
            ("short_hours_warning", "Short of hours"),
            ("swipe_request_needed", "Swipe request needed"),
            ("break_exceeded", "Break ran over"),
            ("leave_expiring", "Leave expiring"),
        ):
            box = QCheckBox(label)
            self._alerts[field] = box
            card.add_full(box)

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
        layout.addStretch(1)

        revert = QPushButton("Revert")
        revert.clicked.connect(lambda: self._load(self._config))
        layout.addWidget(revert)

        save = QPushButton("Save changes")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        layout.addWidget(save)
        return bar

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

        notifications = config.notifications
        self._notify.setChecked(notifications.enabled)
        self._quiet_start.setTime(_to_qtime(notifications.quiet_hours_start))
        self._quiet_end.setTime(_to_qtime(notifications.quiet_hours_end))
        for field, box in self._alerts.items():
            box.setChecked(bool(getattr(notifications, field, True)))
        self._sync_notification_state(notifications.enabled)

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
            notifications=replace(
                config.notifications,
                enabled=self._notify.isChecked(),
                quiet_hours_start=self._quiet_start.time().toString("HH:mm"),
                quiet_hours_end=self._quiet_end.time().toString("HH:mm"),
                **{field: box.isChecked() for field, box in self._alerts.items()},
            ),
        )
        self._config = updated
        self.config_saved.emit(updated)

    def set_config(self, config: AppConfig) -> None:
        self._config = config
        self._load(config)


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
