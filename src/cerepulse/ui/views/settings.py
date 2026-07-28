"""Settings — account, shift policy, sync, appearance, and cache management.

Changes are collected and applied on Save rather than written per keystroke, so a half-typed
value never becomes the live policy.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from cerepulse.core.config import AppConfig
from cerepulse.ui.widgets import Banner, SectionTitle


class SettingsView(QWidget):
    """Edits the configuration and hands a new :class:`AppConfig` back."""

    config_saved = Signal(object)  # AppConfig
    sign_out_requested = Signal(bool)  # forget stored credentials
    clear_cache_requested = Signal()

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(16)

        self.banner = Banner()
        layout.addWidget(self.banner)

        layout.addWidget(SectionTitle("Account"))
        layout.addLayout(self._build_account())

        layout.addWidget(SectionTitle("Shift policy"))
        policy_note = QLabel(
            "Company policy, not something the portal reports. Defaults assume an eight-hour "
            "day with a one-hour break inside a nine-hour span."
        )
        policy_note.setObjectName("CardCaption")
        policy_note.setWordWrap(True)
        layout.addWidget(policy_note)
        layout.addLayout(self._build_shift())

        layout.addWidget(SectionTitle("Sync"))
        layout.addLayout(self._build_sync())

        layout.addWidget(SectionTitle("Appearance"))
        layout.addLayout(self._build_appearance())

        layout.addWidget(SectionTitle("Cache"))
        layout.addLayout(self._build_cache())

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        save = QPushButton("Save changes")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        buttons.addWidget(save)
        layout.addLayout(buttons)

        layout.addStretch(1)
        self._load(config)

    # --- sections -------------------------------------------------------------------

    def _build_account(self) -> QFormLayout:
        form = QFormLayout()
        self._username = QLabel()
        self._remember = QCheckBox("Remember me on this device")
        self._remember.setToolTip("Password lives in the Windows Credential Manager only.")

        row = QHBoxLayout()
        sign_out = QPushButton("Sign out")
        sign_out.clicked.connect(lambda: self.sign_out_requested.emit(False))
        forget = QPushButton("Sign out and forget password")
        forget.clicked.connect(lambda: self.sign_out_requested.emit(True))
        row.addWidget(sign_out)
        row.addWidget(forget)
        row.addStretch(1)

        form.addRow("Signed in as", self._username)
        form.addRow("", self._remember)
        form.addRow("", _wrap(row))
        return form

    def _build_shift(self) -> QFormLayout:
        form = QFormLayout()
        self._work = _hours_spin()
        self._break = _hours_spin()
        self._span = _hours_spin()
        form.addRow("Work target (hours)", self._work)
        form.addRow("Break allowance (hours)", self._break)
        form.addRow("Shift span (hours)", self._span)
        return form

    def _build_sync(self) -> QFormLayout:
        form = QFormLayout()
        self._interval = QSpinBox()
        self._interval.setRange(1, 240)
        self._interval.setSuffix(" min")
        self._ttl = QSpinBox()
        self._ttl.setRange(1, 240)
        self._ttl.setSuffix(" min")
        self._history = QSpinBox()
        self._history.setRange(1, 36)
        self._history.setSuffix(" months")
        form.addRow("Background refresh every", self._interval)
        form.addRow("Treat cache as fresh for", self._ttl)
        form.addRow("Keep history for", self._history)
        return form

    def _build_appearance(self) -> QFormLayout:
        form = QFormLayout()
        self._theme = QComboBox()
        self._theme.addItem("Dark", "dark")
        self._theme.addItem("Light", "light")
        self._theme.addItem("Follow Windows", "system")
        self._background = QComboBox()
        self._background.addItem("Keep running in the tray", "tray")
        self._background.addItem("Close fully when the window closes", "foreground")
        self._startup = QCheckBox("Start CerePulse when I sign in to Windows")

        form.addRow("Theme", self._theme)
        form.addRow("When the window closes", self._background)
        form.addRow("", self._startup)
        return form

    def _build_cache(self) -> QHBoxLayout:
        row = QHBoxLayout()
        clear = QPushButton("Clear cached data")
        clear.setToolTip("Removes local attendance and leave data. It re-syncs on next launch.")
        clear.clicked.connect(self.clear_cache_requested)
        row.addWidget(clear)
        row.addStretch(1)
        return row

    # --- state ----------------------------------------------------------------------

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
            ),
        )
        self._config = updated
        self.config_saved.emit(updated)

    def set_config(self, config: AppConfig) -> None:
        self._config = config
        self._load(config)


def _hours_spin() -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0.0, 24.0)
    spin.setSingleStep(0.25)
    spin.setDecimals(2)
    return spin


def _wrap(layout: QHBoxLayout) -> QWidget:
    host = QWidget()
    host.setLayout(layout)
    return host
