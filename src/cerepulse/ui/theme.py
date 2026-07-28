"""Visual identity and stylesheets.

The palette carries over from ninetofive so CerePulse reads as the same product line: a
near-black surface with a small set of semantic accents. Each accent means one thing
everywhere it appears — aqua is work, amber is break, green is target met, red is short,
violet is an adjustment — so the segmented bar, the cards and the insight strip all share
one visual grammar.

Durations are rendered with tabular figures. Without them a live countdown jitters as the
digits change width, which reads as the number being unstable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Accent(Enum):
    """Semantic colours. The meaning is fixed; only the shade changes between themes."""

    WORK = "work"
    BREAK = "break"
    GOOD = "good"
    BAD = "bad"
    ADJUST = "adjust"
    MUTED = "muted"


@dataclass(frozen=True, slots=True)
class Palette:
    """Every colour the UI is allowed to use."""

    name: str
    surface: str
    elevated: str
    border: str
    text: str
    text_muted: str
    text_faint: str
    work: str
    rest: str
    good: str
    bad: str
    adjust: str
    overlay: str

    def accent(self, accent: Accent) -> str:
        return {
            Accent.WORK: self.work,
            Accent.BREAK: self.rest,
            Accent.GOOD: self.good,
            Accent.BAD: self.bad,
            Accent.ADJUST: self.adjust,
            Accent.MUTED: self.text_muted,
        }[accent]


DARK = Palette(
    name="dark",
    surface="#000000",
    elevated="#0E0E10",
    border="#1F1F23",
    text="#F4F4F5",
    text_muted="#A1A1AA",
    text_faint="#52525B",
    work="#22D3EE",
    rest="#F59E0B",
    good="#34D399",
    bad="#F87171",
    adjust="#A78BFA",
    overlay="#18181B",
)

LIGHT = Palette(
    name="light",
    surface="#FAFAFA",
    elevated="#FFFFFF",
    border="#E4E4E7",
    text="#18181B",
    text_muted="#52525B",
    text_faint="#A1A1AA",
    # Darkened so they hold contrast against a white card.
    work="#0891B2",
    rest="#B45309",
    good="#059669",
    bad="#DC2626",
    adjust="#7C3AED",
    overlay="#F4F4F5",
)

#: Native on Windows 11, with a tabular-figures variant for anything numeric.
FONT_FAMILY = '"Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif'
MONO_FAMILY = '"Cascadia Mono", "Consolas", monospace'


def palette_for(theme: str) -> Palette:
    """Resolve a configured theme name. ``system`` follows the OS setting."""
    if theme == "light":
        return LIGHT
    if theme == "dark":
        return DARK
    return DARK if _system_prefers_dark() else LIGHT


def _system_prefers_dark() -> bool:
    """Read the Windows apps-use-light-theme preference, defaulting to dark."""
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        with key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return not bool(value)
    except OSError:
        return True


def stylesheet(palette: Palette) -> str:
    """Build the application stylesheet for a palette."""
    return f"""
    QWidget {{
        background-color: {palette.surface};
        color: {palette.text};
        font-family: {FONT_FAMILY};
        font-size: 13px;
    }}

    /* Labels inherit the QWidget background rule, which paints an opaque box over
       whatever card they sit on. They must be transparent to read as text. */
    QLabel, QCheckBox {{
        background: transparent;
    }}

    /* --- shell ------------------------------------------------------------- */

    #Sidebar {{
        background-color: {palette.elevated};
        border-right: 1px solid {palette.border};
    }}
    #SidebarButton {{
        background: transparent;
        border: none;
        border-radius: 8px;
        padding: 10px 14px;
        text-align: left;
        color: {palette.text_muted};
        font-size: 13px;
    }}
    #SidebarButton:hover {{
        background-color: {palette.overlay};
        color: {palette.text};
    }}
    #SidebarButton:checked {{
        background-color: {palette.overlay};
        color: {palette.text};
        font-weight: 600;
    }}
    #SidebarBrand {{
        color: {palette.text};
        font-size: 17px;
        font-weight: 700;
        padding: 4px 14px 2px 14px;
    }}
    #SidebarTagline {{
        color: {palette.text_faint};
        font-size: 11px;
        padding: 0 14px 12px 14px;
    }}

    /* --- cards ------------------------------------------------------------- */

    #Card {{
        background-color: {palette.elevated};
        border: 1px solid {palette.border};
        border-radius: 12px;
    }}
    #CardTitle {{
        color: {palette.text_muted};
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 0.6px;
    }}
    #CardValue {{
        color: {palette.text};
        font-size: 26px;
        font-weight: 700;
        font-variant-numeric: tabular-nums;
    }}
    #CardCaption {{
        color: {palette.text_faint};
        font-size: 11px;
    }}

    /* --- hero -------------------------------------------------------------- */

    #HeroLabel {{
        color: {palette.text_muted};
        font-size: 13px;
    }}
    #HeroValue {{
        color: {palette.text};
        font-size: 44px;
        font-weight: 700;
        font-variant-numeric: tabular-nums;
    }}
    #HeroCaption {{
        color: {palette.text_faint};
        font-size: 12px;
    }}

    /* --- banners ----------------------------------------------------------- */

    #Banner {{
        border-radius: 8px;
        padding: 8px 12px;
        font-size: 12px;
    }}
    #BannerInfo    {{ background-color: {palette.overlay}; color: {palette.text_muted}; }}
    #BannerWarning {{ background-color: {palette.overlay}; color: {palette.rest}; }}
    #BannerError   {{ background-color: {palette.overlay}; color: {palette.bad}; }}

    /* --- tables ------------------------------------------------------------ */

    QTableView {{
        background-color: {palette.elevated};
        alternate-background-color: {palette.overlay};
        border: 1px solid {palette.border};
        border-radius: 10px;
        gridline-color: {palette.border};
        selection-background-color: {palette.overlay};
        selection-color: {palette.text};
        font-variant-numeric: tabular-nums;
    }}
    QHeaderView::section {{
        background-color: {palette.elevated};
        color: {palette.text_muted};
        border: none;
        border-bottom: 1px solid {palette.border};
        padding: 8px;
        font-size: 11px;
        font-weight: 600;
    }}
    QTableView::item {{ padding: 6px; }}

    /* --- controls ---------------------------------------------------------- */

    QPushButton {{
        background-color: {palette.overlay};
        color: {palette.text};
        border: 1px solid {palette.border};
        border-radius: 8px;
        padding: 7px 14px;
    }}
    QPushButton:hover  {{ border-color: {palette.work}; }}
    QPushButton:disabled {{ color: {palette.text_faint}; border-color: {palette.border}; }}
    QPushButton#Primary {{
        background-color: {palette.work};
        color: {palette.surface};
        border: none;
        font-weight: 600;
    }}
    QPushButton#Primary:disabled {{ background-color: {palette.text_faint}; }}

    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background-color: {palette.surface};
        border: 1px solid {palette.border};
        border-radius: 8px;
        padding: 7px 10px;
        selection-background-color: {palette.work};
        selection-color: {palette.surface};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border-color: {palette.work};
    }}
    QComboBox QAbstractItemView {{
        background-color: {palette.elevated};
        border: 1px solid {palette.border};
        selection-background-color: {palette.overlay};
    }}

    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px;
        border: 1px solid {palette.border};
        border-radius: 4px;
        background-color: {palette.surface};
    }}
    QCheckBox::indicator:checked {{
        background-color: {palette.work};
        border-color: {palette.work};
    }}

    QScrollArea {{ border: none; }}
    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {palette.border}; border-radius: 5px; min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {palette.text_faint}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

    QToolTip {{
        background-color: {palette.elevated};
        color: {palette.text};
        border: 1px solid {palette.border};
        padding: 6px;
    }}
    """
