"""Text: a role from the type ramp and a tone from the palette, and nothing hand-sized.

Sizes and colours were set per label — ``font-size: 26px`` here, ``setStyleSheet("color:
…")`` there — so the same kind of figure came out three sizes on three screens, and every
coloured label had to be found and repainted when the theme changed. A kit label names a
``role`` and a ``tone``; the one sheet says what those look like.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from cerepulse.ui.kit.buttons import repolish
from cerepulse.ui.theme import ROLES, TONES


def font_for(role: str) -> QFont:
    """The font a role is drawn in: the application's family at the ramp's size and weight."""
    size, weight = ROLES[role]
    font = QFont(QApplication.font())
    font.setPointSizeF(size)
    font.setWeight(QFont.Weight(weight))
    return font


def measure(*samples: str, role: str = "body") -> int:
    """The width the widest sample needs in a role's font, in logical pixels.

    A column sized for its content has to be measured in the font the sheet *will* apply.
    A label's own ``fontMetrics()`` at construction is the font before the sheet has polished
    it — smaller and lighter than the role — and the dates in the comp-off list came out
    clipped by a few pixels at every scale.
    """
    metrics = QFontMetrics(font_for(role))
    return max(metrics.horizontalAdvance(sample) for sample in samples) + 2


def _check(role: str | None, tone: str | None) -> None:
    if role is not None and role not in ROLES:
        raise ValueError(f"unknown text role {role!r}")
    if tone is not None and tone not in TONES:
        raise ValueError(f"unknown tone {tone!r}")


class Text(QLabel):
    """A label in a role (``caption`` … ``display``) and, optionally, a tone.

    Plain text only, so remarks from the portal can never be read as markup. ``wrap`` for
    anything longer than a few words.
    """

    def __init__(
        self,
        text: str = "",
        parent: QWidget | None = None,
        *,
        role: str = "body",
        tone: str | None = None,
        wrap: bool = False,
    ) -> None:
        super().__init__(text, parent)
        _check(role, tone)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setProperty("role", role)
        if tone is not None:
            self.setProperty("tone", tone)
        self.setWordWrap(wrap)

    def set_tone(self, tone: str | None) -> None:
        _check(None, tone)
        self.setProperty("tone", tone or "")
        repolish(self)

    def set_role(self, role: str) -> None:
        _check(role, None)
        self.setProperty("role", role)
        repolish(self)


class Pill(QLabel):
    """A state as a word on a tinted pill — always a word, never colour alone."""

    def __init__(self, text: str = "", parent: QWidget | None = None, *, tone: str = "muted"):
        super().__init__(text, parent)
        _check(None, tone)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setProperty("variant", "pill")
        self.setProperty("tone", tone)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_state(self, text: str, tone: str) -> None:
        _check(None, tone)
        self.setText(text)
        self.setVisible(bool(text))
        if self.property("tone") != tone:
            self.setProperty("tone", tone)
            repolish(self)
