"""Sign-in dialog.

Only shown when it has to be: on first run, or when silent re-authentication from the
credential store fails. Cached data renders before this ever appears.

The password is held in a widget and handed straight to the auth manager. It is never
logged, never written to config, and only reaches the Windows Credential Manager if the
user opts in.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from cerepulse import __about__ as about
from cerepulse.ui import kit
from cerepulse.ui.widgets import add_reveal_toggle


class LoginDialog(QDialog):
    """Collects credentials. Verification is the caller's job, so failures can be shown."""

    submitted = Signal(str, str, bool)  # username, password, remember

    def __init__(
        self,
        *,
        username: str = "",
        remember: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Sign in to {about.NAME}")
        self.setModal(True)
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)

        layout.addWidget(kit.Text(f"Sign in to {about.NAME}", self, role="subtitle"))
        layout.addWidget(
            kit.Text("Use your SpineHR portal credentials.", self, role="caption", tone="muted")
        )

        form = QFormLayout()
        form.setSpacing(10)
        self._username = kit.LineEdit(username, self, placeholder="Employee code")
        self._password = kit.LineEdit(parent=self, placeholder="Password")
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        add_reveal_toggle(self._password)
        form.addRow(kit.Text("Username", self), self._username)
        form.addRow(kit.Text("Password", self), self._password)
        layout.addLayout(form)

        self._remember = kit.CheckBox("Remember me on this device")
        self._remember.setChecked(remember)
        self._remember.setToolTip(
            "Stores the password in the Windows Credential Manager, never in a file."
        )
        layout.addWidget(self._remember)

        self._error = kit.Text(parent=self, tone="bad", wrap=True)
        self._error.setVisible(False)
        layout.addWidget(self._error)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.setSpacing(8)
        self._cancel = kit.Button("Cancel", self, variant="quiet", on_click=self.reject)
        self._submit = kit.Button("Sign in", self, variant="primary", on_click=self._on_submit)
        # Enter signs in from either field; the kit turns auto-default off, so it is said here.
        self._submit.setDefault(True)
        buttons.addWidget(self._cancel)
        buttons.addWidget(self._submit)
        layout.addLayout(buttons)

        self._username.returnPressed.connect(self._on_submit)
        self._password.returnPressed.connect(self._on_submit)
        self._username.textChanged.connect(self._validate)
        self._password.textChanged.connect(self._validate)
        self._validate()

        # Land the cursor where the user still has typing to do.
        (self._password if username else self._username).setFocus(Qt.FocusReason.OtherFocusReason)

    # --- state ----------------------------------------------------------------------

    @property
    def username(self) -> str:
        return self._username.text().strip()

    @property
    def password(self) -> str:
        return self._password.text()

    @property
    def remember(self) -> bool:
        return self._remember.isChecked()

    def _validate(self) -> None:
        self._submit.setEnabled(bool(self.username and self.password))

    def _on_submit(self) -> None:
        if not (self.username and self.password):
            return
        self.set_busy(True)
        self.submitted.emit(self.username, self.password, self.remember)

    # --- feedback -------------------------------------------------------------------

    def set_busy(self, busy: bool) -> None:
        """Disable input while a sign-in attempt is in flight."""
        self._submit.setText("Signing in…" if busy else "Sign in")
        for widget in (self._submit, self._username, self._password, self._remember):
            widget.setEnabled(not busy)
        if not busy:
            self._validate()

    def show_error(self, message: str) -> None:
        self.set_busy(False)
        self._error.setText(message)
        self._error.setVisible(True)
        self._password.selectAll()
        self._password.setFocus(Qt.FocusReason.OtherFocusReason)
