"""CerePulse's UI kit: the one set of widgets every screen is built from.

Each class wraps a Qt widget once — its look, as a ``variant``, ``role`` or ``tone``
property the one stylesheet paints (:func:`cerepulse.ui.theme._kit_rules`), and its
behaviour: cursor, focus, the wheel, accessible names, theme changes. So every button,
field, card, row and label looks and behaves the same wherever it is. A screen that needs
something different gets another variant, never its own stylesheet.

``tests/ui/test_kit_usage.py`` fails if UI code outside the kit builds a raw
``QPushButton``, ``QComboBox``… or calls ``setStyleSheet``, so the look cannot drift again.
Screens still being moved onto the kit are listed there, and the list only shrinks.

The pattern is the one ReportFlow uses, which is where the user asked for it from.
"""

from cerepulse.ui.kit.buttons import (
    Button,
    IconButton,
    NavButton,
    SegmentButton,
    StepButton,
    Tabs,
    repolish,
    set_variant,
)
from cerepulse.ui.kit.icons import IconLabel, icon, toned
from cerepulse.ui.kit.inputs import (
    CheckBox,
    ComboBox,
    DateEdit,
    DoubleSpinBox,
    LineEdit,
    SpinBox,
    TimeEdit,
)
from cerepulse.ui.kit.legend import LegendItem, Swatch
from cerepulse.ui.kit.meter import Meter
from cerepulse.ui.kit.surfaces import Divider, ListRow, SectionHeader, StatCard, Surface
from cerepulse.ui.kit.text import Pill, Text
from cerepulse.ui.kit.views import Box, ScrollArea, TableWidget

__all__ = [
    "Box",
    "Button",
    "CheckBox",
    "ComboBox",
    "DateEdit",
    "Divider",
    "DoubleSpinBox",
    "IconButton",
    "IconLabel",
    "LegendItem",
    "LineEdit",
    "ListRow",
    "Meter",
    "NavButton",
    "Pill",
    "ScrollArea",
    "SectionHeader",
    "SegmentButton",
    "SpinBox",
    "StatCard",
    "StepButton",
    "Surface",
    "Swatch",
    "TableWidget",
    "Tabs",
    "Text",
    "TimeEdit",
    "icon",
    "repolish",
    "set_variant",
    "toned",
]
