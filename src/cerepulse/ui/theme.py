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


#: Smallest contrast ratio any text is allowed against the surface behind it. WCAG AA for
#: body text. ``text_faint`` used to sit at 2.49:1 — barely half of this — and it is the
#: colour of every caption, legend and explanatory note in the app, so the parts that
#: explain the numbers were the hardest parts to read.
MIN_CONTRAST = 4.5

DARK = Palette(
    name="dark",
    surface="#000000",
    elevated="#0E0E10",
    border="#1F1F23",
    text="#F4F4F5",
    text_muted="#A1A1AA",
    text_faint="#8A8A94",
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
    text_faint="#6E6E78",
    # Darkened so they hold contrast against a white card.
    work="#0891B2",
    rest="#B45309",
    good="#059669",
    bad="#DC2626",
    adjust="#7C3AED",
    overlay="#F4F4F5",
)

#: Native on Windows 11.
#:
MONO_FAMILY = '"Cascadia Mono", "Consolas", monospace'

#: The chain Qt actually walks, in order, for a code point the previous family lacks.
FONT_FALLBACKS = ("Segoe UI Variable Text", "Segoe UI", "Segoe UI Symbol", "Arial")


def apply_font(application: object) -> None:
    """Give the whole application a font with a real fallback chain.

    ``QFont.setFamilies`` is the only thing in Qt that behaves the way a CSS font stack
    reads: each family is tried in turn for every character. Setting this on the
    ``QApplication`` means a glyph missing from the primary family is drawn from the next
    one instead of coming out as a blank box.
    """
    from PySide6.QtGui import QFont

    font = QFont()
    font.setFamilies(list(FONT_FALLBACKS))
    application.setFont(font)  # type: ignore[attr-defined]


class Space:
    """The only gaps the UI is allowed to use.

    Every screen previously picked its own margins and spacings, so a card on Today sat at
    a different distance from its heading than the same card on Insights. A fixed ladder
    means a layout either matches the rest of the app or is obviously wrong.
    """

    TIGHT = 4
    SNUG = 8
    ROW = 12
    GAP = 16
    SECTION = 24


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG relative-luminance contrast between two hex colours.

    Here rather than in a test so the palette can be checked, and so anyone adding a colour
    has something to check it with.
    """
    return _ratio(_luminance(foreground), _luminance(background))


def _luminance(hex_colour: str) -> float:
    value = hex_colour.lstrip("#")
    channels = [int(value[index : index + 2], 16) / 255 for index in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _ratio(first: float, second: float) -> float:
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


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


def _arrow_glyphs(palette: Palette) -> dict[str, str]:
    """Write themed arrow SVGs for the combo and spin-box subcontrols; return url() paths.

    Once the box is styled, Qt's own arrow primitives draw as small near-black triangles
    whatever the palette — invisible on the dark theme. QSS can only replace them with an
    ``image:``, and an image needs a real file: these are two hundred bytes each, written
    beside the cache (never beside the executable, which is read-only when installed) and
    named by colour so switching themes writes a second pair rather than repainting the
    first. Failure degrades to the default arrows, not to a broken sheet.
    """
    from cerepulse.core import paths

    colour = palette.text_muted
    shapes = {"up": "M2,8 L6,3.5 L10,8", "down": "M2,4 L6,8.5 L10,4"}
    urls: dict[str, str] = {}
    try:
        directory = paths.data_root() / "cache" / "glyphs"
        directory.mkdir(parents=True, exist_ok=True)
        for name, line in shapes.items():
            svg = (
                '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12">'
                f'<path d="{line}" fill="none" stroke="{colour}" stroke-width="1.6" '
                'stroke-linecap="round" stroke-linejoin="round"/></svg>'
            )
            file = directory / f"arrow-{name}-{colour.lstrip('#')}.svg"
            if not file.exists():
                file.write_text(svg, encoding="utf-8")
            urls[name] = file.as_posix()
    except OSError:
        return {}
    return urls


def _arrow_rules(palette: Palette) -> str:
    glyphs = _arrow_glyphs(palette)
    if not glyphs:
        return ""
    return f"""
    QComboBox::down-arrow {{ image: url({glyphs["down"]}); width: 12px; height: 12px; }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow,
    QTimeEdit::up-arrow, QDateEdit::up-arrow {{
        image: url({glyphs["up"]}); width: 10px; height: 10px;
    }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow,
    QTimeEdit::down-arrow, QDateEdit::down-arrow {{
        image: url({glyphs["down"]}); width: 10px; height: 10px;
    }}
    """


def stylesheet(palette: Palette) -> str:
    """Build the application stylesheet for a palette."""
    return f"""
    {_arrow_rules(palette)}
    /* No font-family here on purpose. A style-sheet font *overrides* the one set through
       QApplication::setFont, and a QSS font-family carries no fallback chain — so naming
       it on the QWidget rule (which matches everything) silently discarded the fallback
       list `apply_font` builds, including the family that actually holds the ◀ ▶ glyphs.
       `QFont.setFamilies` owns the font; this sheet only sizes it. */
    QWidget {{
        background-color: {palette.surface};
        color: {palette.text};
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
    /* Opaque, so rows scrolling underneath do not show through a sticky header. */
    QHeaderView::section {{
        background-color: {palette.overlay};
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
    /* The ◀ ▶ steppers. The default 7px/14px padding plus a 1px border is 30px of chrome,
       and these were set to a fixed 30px wide — leaving exactly zero pixels for the glyph,
       which is why they drew as empty rounded squares. They are square by design, so they
       get padding that fits inside one. */
    QPushButton#StepButton {{
        padding: 4px 0px;
        font-size: 14px;
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

    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTimeEdit, QDateEdit {{
        background-color: {palette.surface};
        border: 1px solid {palette.border};
        border-radius: 8px;
        padding: 7px 10px;
        selection-background-color: {palette.work};
        selection-color: {palette.surface};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
    QTimeEdit:focus, QDateEdit:focus {{
        border-color: {palette.work};
    }}

    /* Styling the box switches Qt to stylesheet rendering for the WHOLE control, and the
       subcontrols it stops drawing natively have to be declared or they misbehave two
       different ways: the combo's drop-down kept its native raised button, painted square
       over the themed rounded corner; and the spin boxes' up/down buttons were left with
       no reserved area at all, so the editable text owned the pixels under the arrows and
       clicking them planted a text cursor instead of stepping. */
    QComboBox {{ padding-right: 30px; }}
    QComboBox::drop-down {{
        subcontrol-origin: border;
        subcontrol-position: center right;
        width: 28px;
        border: none;
        background: transparent;
    }}
    QSpinBox, QDoubleSpinBox, QTimeEdit, QDateEdit {{ padding-right: 26px; }}
    QSpinBox::up-button, QDoubleSpinBox::up-button,
    QTimeEdit::up-button, QDateEdit::up-button {{
        subcontrol-origin: border;
        subcontrol-position: top right;
        width: 22px;
        border: none;
        border-left: 1px solid {palette.border};
        /* One less than the box's own radius, so the fill stays inside the border curve. */
        border-top-right-radius: 7px;
        background: {palette.overlay};
    }}
    QSpinBox::down-button, QDoubleSpinBox::down-button,
    QTimeEdit::down-button, QDateEdit::down-button {{
        subcontrol-origin: border;
        subcontrol-position: bottom right;
        width: 22px;
        border: none;
        border-left: 1px solid {palette.border};
        border-bottom-right-radius: 7px;
        background: {palette.overlay};
    }}
    QSpinBox::up-button:hover, QSpinBox::down-button:hover,
    QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover,
    QTimeEdit::up-button:hover, QTimeEdit::down-button:hover,
    QDateEdit::up-button:hover, QDateEdit::down-button:hover {{
        background: {palette.border};
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
