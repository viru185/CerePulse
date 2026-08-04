"""The arrival card, and the Settings controls behind it.

The rule these protect: **Refresh is never disabled.** A control that greys itself out to
stop you spending an API call is a control that looks broken, and looking broken is exactly
what makes somebody click it repeatedly — the behaviour the guards behind it exist to
absorb. It always answers; the service decides whether answering costs a call.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QApplication

from cerepulse.commute.models import TravelEstimate
from cerepulse.core.config import AppConfig
from cerepulse.intelligence.commute import arrival_at
from cerepulse.models.values import Duration
from cerepulse.services.commute import CommuteView
from cerepulse.ui.theme import DARK
from cerepulse.ui.views.settings import SettingsView
from cerepulse.ui.widgets import CommuteCard

LEAVING = datetime(2026, 8, 4, 18, 51)


def ready(minutes: int = 43, delay: int = 0, message: str = "") -> CommuteView:
    estimate = TravelEstimate(
        duration=Duration(minutes),
        distance_m=27400,
        delay=Duration(delay),
        fetched_at=datetime(2026, 8, 4, 18, 45),
    )
    return CommuteView(arrival=arrival_at(LEAVING, estimate), message=message)


def test_the_arrival_time_leads(qapp: QApplication) -> None:
    card = CommuteCard(DARK)
    card.show_view(ready(), now=datetime(2026, 8, 4, 18, 46))

    assert card._value.text() == "7:34 PM"


def test_the_caption_names_the_departure_and_the_age(qapp: QApplication) -> None:
    card = CommuteCard(DARK)
    card.show_view(ready(), now=datetime(2026, 8, 4, 18, 50))

    caption = card._caption.text()
    assert "leaving 6:51 PM" in caption
    assert "5 min ago" in caption


def test_refresh_is_never_disabled(qapp: QApplication) -> None:
    """Whatever state the card is in. See the module docstring."""
    card = CommuteCard(DARK)
    for view in (ready(), CommuteView(message="Could not reach TomTom")):
        card.show_view(view)
        assert card.refresh.isEnabled()


def test_an_unconfigured_card_offers_the_way_to_fix_it(qapp: QApplication) -> None:
    """Hiding the feature until it is set up would make it undiscoverable; pretending it
    works would be worse."""
    card = CommuteCard(DARK)
    card.show_view(CommuteView(message="Add a TomTom API key in Settings.", needs_setup=True))

    assert card.setup.isVisibleTo(card)
    assert "TomTom API key" in card._caption.text()
    assert card._value.text() == "—"


def test_a_configured_card_does_not_nag_about_setup(qapp: QApplication) -> None:
    card = CommuteCard(DARK)
    card.show_view(ready())

    assert not card.setup.isVisibleTo(card)


def test_a_warning_rides_alongside_the_number_rather_than_replacing_it(
    qapp: QApplication,
) -> None:
    """Losing the estimate as well as the ability to refresh it is two punishments for one
    problem."""
    card = CommuteCard(DARK)
    card.show_view(ready(message="Could not reach TomTom just now — this is the last one."))

    assert card._value.text() == "7:34 PM"
    assert "last one" in card._caption.text()


# --- settings ----------------------------------------------------------------------------


def test_the_key_field_never_shows_the_key_back(qapp: QApplication) -> None:
    """Reading it back would put it on screen for anything that can screenshot the window,
    and tempt somebody into copying it out of the app that is supposed to hold it safely."""
    from PySide6.QtWidgets import QLineEdit

    view = SettingsView(AppConfig())
    view.show_stored_key(True)

    assert view._api_key.text() == ""
    assert view._api_key.echoMode() == QLineEdit.EchoMode.Password
    assert "saved" in view._api_key.placeholderText()


def test_clearing_the_address_clears_the_point_with_it(qapp: QApplication) -> None:
    """Stale coordinates behind an empty field would keep routing to the old house."""
    from dataclasses import replace

    config = replace(
        AppConfig(),
        commute=replace(
            AppConfig().commute,
            destination="Somewhere",
            destination_lat=23.0,
            destination_lon=72.0,
        ),
    )
    view = SettingsView(config)
    saved: list[AppConfig] = []
    view.config_saved.connect(saved.append)

    view._home.setText("")
    view._save()

    assert saved[0].commute.destination == ""
    assert saved[0].commute.destination_lat == 0.0


def test_the_resolved_address_is_shown_back(qapp: QApplication) -> None:
    """An address that quietly geocoded to the next city gives a plausible travel time, and
    seeing the match is the only way anybody catches it."""
    view = SettingsView(AppConfig())
    view.show_geocode_result("Found: Satellite, Ahmedabad, Gujarat. Saved.")

    assert "Satellite" in view._home_found.text()


def test_an_unresolved_address_says_so(qapp: QApplication) -> None:
    view = SettingsView(AppConfig())
    assert "Not found yet" in view._home_found.text()


def test_the_travel_mode_round_trips(qapp: QApplication) -> None:
    view = SettingsView(AppConfig())
    saved: list[AppConfig] = []
    view.config_saved.connect(saved.append)

    view._mode.setCurrentIndex(view._mode.findData("bicycle"))
    view._save()

    assert saved[0].commute.mode == "bicycle"
