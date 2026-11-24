"""The kit's own promises: every variant has a look, every widget follows the theme."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from cerepulse.ui import kit
from cerepulse.ui.kit.icons import PATHS, RATIOS, pixmap
from cerepulse.ui.theme import (
    BUTTON_VARIANTS,
    DARK,
    LIGHT,
    PALETTES,
    ROLES,
    TONES,
    stylesheet,
    tone_colour,
)


@pytest.mark.parametrize("palette", list(PALETTES.values()), ids=list(PALETTES))
def test_every_variant_role_and_tone_has_a_look_in_every_theme(palette) -> None:  # type: ignore[no-untyped-def]
    sheet = stylesheet(palette)
    for variant in BUTTON_VARIANTS:
        assert f'QPushButton[variant="{variant}"]' in sheet, variant
    for role in ROLES:
        assert f'QLabel[role="{role}"]' in sheet, role
    for tone in TONES:
        assert f'QLabel[tone="{tone}"] {{ color: {tone_colour(palette, tone)}; }}' in sheet
        assert f'QLabel[variant="pill"][tone="{tone}"]' in sheet


def test_a_button_carries_its_variant_and_never_presses_on_enter(qapp: QApplication) -> None:
    button = kit.Button("Save", variant="primary")
    assert button.property("variant") == "primary"
    assert not button.autoDefault()
    assert button.cursor().shape() is Qt.CursorShape.PointingHandCursor
    assert button.accessibleName() == "Save"

    with pytest.raises(ValueError, match="unknown button variant"):
        kit.Button("x", variant="glossy")


def test_on_click_is_wired(qapp: QApplication) -> None:
    pressed: list[bool] = []
    button = kit.Button("Go", on_click=lambda: pressed.append(True))
    button.click()
    assert pressed == [True]


def test_text_and_pills_name_a_role_and_a_tone_never_a_colour(qapp: QApplication) -> None:
    label = kit.Text("6:40 PM", role="display", tone="work")
    assert (label.property("role"), label.property("tone")) == ("display", "work")
    assert label.textFormat() is Qt.TextFormat.PlainText, "portal text is never markup"
    assert label.styleSheet() == ""

    pill = kit.Pill("Approved", tone="good")
    pill.set_state("Rejected", "bad")
    assert (pill.text(), pill.property("tone")) == ("Rejected", "bad")

    with pytest.raises(ValueError, match="unknown tone"):
        kit.Text("x", tone="#FF0000")


def test_tabs_are_exclusive_and_say_which_was_picked(qapp: QApplication) -> None:
    tabs = kit.Tabs(["What happened", "Balances", "Holidays"])
    picked: list[int] = []
    tabs.changed.connect(picked.append)
    assert tabs.current() == 0

    tabs.buttons[2].click()
    tabs.set_current(1)

    assert picked == [2, 1]
    assert [button.isChecked() for button in tabs.buttons] == [False, True, False]


@pytest.mark.parametrize("name", sorted(PATHS))
def test_every_icon_draws_something_at_every_ratio(qapp: QApplication, name: str) -> None:
    for ratio in RATIOS:
        drawn = pixmap(name, "#22D3EE", size=20, ratio=ratio)
        assert drawn.devicePixelRatio() == pytest.approx(ratio)
        assert drawn.width() == round(20 * ratio)
        image = drawn.toImage()
        assert any(
            image.pixelColor(x, y).alpha() > 0
            for x in range(image.width())
            for y in range(image.height())
        ), f"{name} drew nothing at {ratio}"


def test_icons_follow_the_theme(qapp: QApplication) -> None:
    label = kit.IconLabel("records", DARK, tone="work")
    before = label.pixmap().toImage()
    label._palette = LIGHT  # what the theme walk does
    after = label.pixmap().toImage()
    assert before != after


def test_a_field_ignores_the_wheel_unless_it_has_focus(qapp: QApplication) -> None:
    """Scrolling Settings used to change whichever field passed under the pointer."""
    box = kit.SpinBox()
    box.setRange(0, 10)
    box.setValue(5)
    event = QWheelEvent(
        QPointF(5, 5),
        QPointF(5, 5),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(box, event)
    assert box.value() == 5
    assert event.type() is QEvent.Type.Wheel


def test_a_combo_finds_its_data_by_value_not_identity(qapp: QApplication) -> None:
    box = kit.ComboBox()
    box.addItem("June", (2026, 6))
    box.addItem("July", (2026, 7))
    wanted = tuple([2026, 7])  # built at runtime: an equal tuple, not the same object

    assert box.select_data(wanted)
    assert box.currentText() == "July"


def test_a_stat_card_takes_a_tone_and_states_its_parts(qapp: QApplication) -> None:
    card = kit.StatCard("Comp-off", value="5", caption="1 lapses Thu 31 Dec", tone="rest")
    card.set_value("4", tone="good")
    assert (card.value.text(), card.value.property("tone")) == ("4", "good")
    assert card.caption.isVisibleTo(card)
    card.set_caption("")
    assert not card.caption.isVisibleTo(card)


def test_a_stat_card_keeps_its_lines_together_at_the_top(qapp: QApplication) -> None:
    """Cards in a row share the tallest one's height. Without a stretch the shorter ones
    spread their lines to fill it, and no two titles sat on the same line."""
    card = kit.StatCard("Worked", value="9h", caption="across 1 day")
    extra = kit.Text("detail")
    card.add_detail(extra)
    layout = card.layout()
    assert layout.itemAt(layout.count() - 1).spacerItem() is not None
    assert layout.indexOf(extra) == layout.count() - 2


def test_a_meter_fills_its_parts_end_to_end_in_tones(qapp: QApplication) -> None:
    meter = kit.Meter(DARK)
    meter.set_parts((0.5, "good", 1.0), (0.2, "work", 0.45))
    assert meter.parts == ((0.5, "good", 1.0), (0.2, "work", 0.45))
    meter.set_fraction(-1.0)
    assert meter.parts == ((0.0, "work", 1.0),)
    with pytest.raises(ValueError, match="tone"):
        meter.set_parts((0.5, "#ff0000", 1.0))
    meter.resize(200, 8)
    assert not meter.grab().isNull()


def test_a_list_row_hides_what_it_was_not_given(qapp: QApplication) -> None:
    row = kit.ListRow(DARK, icon="leave", title="1 day of comp-off taken", state="Pending")
    assert not row.detail.isVisibleTo(row)
    assert not row.kind.isVisibleTo(row)
    assert row.state.isVisibleTo(row)


def test_an_ampersand_is_text_not_a_shortcut(qapp: QApplication) -> None:
    """Qt reads a lone "&" as a mnemonic: "Balances & comp-off" drew as "Balances _comp-off"."""
    tab = kit.SegmentButton("Balances & comp-off")
    assert tab.text() == "Balances && comp-off"
    assert tab.accessibleName() == "Balances & comp-off"
