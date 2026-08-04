"""Call discipline: the button always answers, and mashing it costs one call.

The quota was never the constraint — one call per working day is about 22 a month against an
allowance of 20,000. What these tests protect is the other thing: that pressing Refresh three
times because the first press looked like it did nothing does not buy three identical roads,
and that the button never responds by refusing.

The trap worth naming, because it is the one this was written against: the worker pool is
deliberately single-slot, so it does not *drop* concurrent work — it **serialises** it. Ten
clicks become ten sequential calls, each one answering a question the previous one already
answered. Nothing about the pool prevents that; only the guards here do.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from cerepulse.commute.models import Place, TravelEstimate
from cerepulse.commute.tomtom import TravelMode
from cerepulse.core.config import AppConfig, CommuteConfig
from cerepulse.core.errors import CommuteError
from cerepulse.models.values import Duration
from cerepulse.services.commute import LEAD_MINUTES, CommuteService

LEAVING = datetime(2026, 8, 4, 18, 45)
NOW = datetime(2026, 8, 4, 18, 20)


class FakeTomTom:
    """Counts calls, because the count is the thing under test."""

    def __init__(self, minutes: int = 43, fails: bool = False) -> None:
        self.calls = 0
        self.modes: list[TravelMode] = []
        self.departures: list[datetime | None] = []
        self._minutes = minutes
        self._fails = fails

    def route(
        self,
        origin: Place,
        destination: Place,
        *,
        mode: TravelMode = TravelMode.CAR,
        depart_at: datetime | None = None,
    ) -> TravelEstimate:
        self.calls += 1
        self.modes.append(mode)
        self.departures.append(depart_at)
        if self._fails:
            raise CommuteError("TomTom is unreachable")
        return TravelEstimate(
            duration=Duration(self._minutes),
            distance_m=27400,
            delay=Duration(0),
            fetched_at=datetime.now(),
        )


def configured(**overrides: object) -> AppConfig:
    commute = CommuteConfig(
        enabled=True,
        origin="GIFT City",
        origin_lat=23.1601,
        origin_lon=72.6845,
        destination="Satellite, Ahmedabad",
        destination_lat=23.0225,
        destination_lon=72.5714,
        **overrides,  # type: ignore[arg-type]
    )
    return replace(AppConfig(), commute=commute)


def service(
    client: FakeTomTom | None = None, **overrides: object
) -> tuple[CommuteService, FakeTomTom]:
    fake = client or FakeTomTom()
    return (
        CommuteService(config=configured(**overrides), api_key="k", client=fake),  # type: ignore[arg-type]
        fake,
    )


# --- the button always answers ----------------------------------------------------------


def test_ten_clicks_in_one_second_cost_one_call() -> None:
    svc, fake = service()
    views = [svc.estimate(LEAVING, force=True, now=NOW) for _ in range(10)]

    assert fake.calls == 1
    # And every one of them answered. A guard that works by returning nothing is a button
    # that looks broken, which is what makes people click it again.
    assert all(view.is_ready for view in views)


def test_genuinely_concurrent_callers_share_one_journey() -> None:
    """The freshness floor handles the serialised case; this covers real parallelism.

    Five threads arrive while a call is in flight. Four block, and when they wake they take
    the result the first one left rather than starting an identical journey of their own.
    """
    import threading

    released = threading.Event()

    class Slow(FakeTomTom):
        def route(self, *args: object, **kwargs: object) -> TravelEstimate:  # type: ignore[override]
            released.wait(timeout=5)
            return super().route(*args, **kwargs)  # type: ignore[arg-type]

    fake = Slow()
    svc, _ = service(client=fake)
    results: list[bool] = []

    def ask() -> None:
        results.append(svc.estimate(LEAVING, force=True, now=NOW).is_ready)

    threads = [threading.Thread(target=ask) for _ in range(5)]
    for thread in threads:
        thread.start()
    released.set()
    for thread in threads:
        thread.join(timeout=5)

    assert fake.calls == 1
    assert results == [True] * 5


def test_a_click_after_the_freshness_floor_does_buy_a_new_road() -> None:
    """The floor is a debounce, not a cache with a long life."""
    svc, fake = service()
    svc.estimate(LEAVING, force=True, now=NOW)
    svc.estimate(LEAVING, force=True, now=NOW + timedelta(minutes=5))

    assert fake.calls == 2


def test_rendering_without_forcing_never_spends_a_second_call() -> None:
    """A screen repainting is not a question being asked again."""
    svc, fake = service()
    svc.estimate(LEAVING, now=NOW)
    for _ in range(5):
        svc.estimate(LEAVING, now=NOW + timedelta(minutes=30))

    assert fake.calls == 1


def test_a_different_departure_is_a_different_question() -> None:
    svc, fake = service()
    svc.estimate(LEAVING, now=NOW)
    svc.estimate(LEAVING + timedelta(hours=1), now=NOW)

    assert fake.calls == 2


def test_a_departure_drifting_inside_its_bucket_does_not_re_ask() -> None:
    """The predicted exit moves by a minute as punches land. That is not new information."""
    svc, fake = service()
    svc.estimate(datetime(2026, 8, 4, 18, 46), now=NOW)
    svc.estimate(datetime(2026, 8, 4, 18, 51), now=NOW)
    svc.estimate(datetime(2026, 8, 4, 18, 59), now=NOW)

    assert fake.calls == 1


# --- the budget -------------------------------------------------------------------------


def test_the_budget_is_a_ceiling_the_app_cannot_exceed() -> None:
    svc, fake = service(max_calls_per_day=3)
    for hour in range(10):
        svc.estimate(LEAVING + timedelta(hours=hour), force=True, now=NOW)

    assert fake.calls == 3


def test_reaching_the_ceiling_says_so_rather_than_failing_quietly() -> None:
    svc, _ = service(max_calls_per_day=1)
    svc.estimate(LEAVING, now=NOW)
    view = svc.estimate(LEAVING + timedelta(hours=3), now=NOW)

    assert "daily limit" in view.message


def test_at_the_ceiling_the_last_good_estimate_is_still_shown() -> None:
    """Losing the number as well as the ability to refresh it is two punishments for one
    problem."""
    svc, _ = service(max_calls_per_day=1)
    svc.estimate(LEAVING, now=NOW)
    view = svc.estimate(LEAVING + timedelta(hours=3), now=NOW)

    assert view.is_ready


# --- degrading ---------------------------------------------------------------------------


def test_a_provider_outage_costs_this_card_and_nothing_else() -> None:
    svc, _ = service(client=FakeTomTom(fails=True))
    view = svc.estimate(LEAVING, now=NOW)

    assert not view.is_ready
    assert "unreachable" in view.message.lower()


def test_an_outage_keeps_showing_the_last_estimate_with_its_real_age() -> None:
    fake = FakeTomTom()
    svc, _ = service(client=fake)
    svc.estimate(LEAVING, now=NOW)

    fake._fails = True
    view = svc.estimate(LEAVING, force=True, now=NOW + timedelta(minutes=10))

    assert view.is_ready
    assert "last one" in view.message


# --- not configured ----------------------------------------------------------------------


def test_without_a_key_the_card_says_what_to_do() -> None:
    svc = CommuteService(config=configured(), api_key="")
    view = svc.estimate(LEAVING, now=NOW)

    assert not view.is_ready
    assert view.needs_setup
    assert "TomTom API key" in view.message


def test_without_a_destination_the_card_says_what_to_do() -> None:
    config = replace(AppConfig(), commute=CommuteConfig(enabled=True))
    svc = CommuteService(config=config, api_key="k")
    view = svc.estimate(LEAVING, now=NOW)

    assert view.needs_setup
    assert "home address" in view.message


def test_an_unset_address_is_not_routed_to_null_island() -> None:
    """(0, 0) is in the Atlantic, and routing to it answers a question nobody asked."""
    svc, fake = service()
    svc._config = replace(
        svc._config,
        commute=replace(svc._config.commute, destination_lat=0.0, destination_lon=0.0),
    )
    svc.estimate(LEAVING, force=True, now=NOW)

    assert fake.calls == 0


# --- the automatic trigger ----------------------------------------------------------------


def test_nothing_is_asked_until_the_exit_is_close() -> None:
    """Before the lead window the answer describes a departure nobody is acting on yet."""
    svc, _ = service()
    assert not svc.should_ask(LEAVING, now=LEAVING - timedelta(minutes=LEAD_MINUTES + 10))
    assert svc.should_ask(LEAVING, now=LEAVING - timedelta(minutes=LEAD_MINUTES - 1))


def test_nothing_is_asked_about_a_departure_already_past() -> None:
    svc, _ = service()
    assert not svc.should_ask(LEAVING, now=LEAVING + timedelta(minutes=5))


def test_the_automatic_call_happens_once_per_departure() -> None:
    svc, _ = service()
    close = LEAVING - timedelta(minutes=10)
    assert svc.should_ask(LEAVING, now=close)

    svc.estimate(LEAVING, now=close)
    assert not svc.should_ask(LEAVING, now=close + timedelta(minutes=1))


def test_a_big_move_in_the_predicted_exit_earns_another_look() -> None:
    """Half an hour of unexpected overtime is a different journey."""
    svc, _ = service()
    close = LEAVING - timedelta(minutes=10)
    svc.estimate(LEAVING, now=close)

    later = LEAVING + timedelta(minutes=45)
    assert svc.should_ask(later, now=later - timedelta(minutes=10))


def test_an_unconfigured_app_never_asks_on_its_own() -> None:
    svc = CommuteService(config=configured(), api_key="")
    assert not svc.should_ask(LEAVING, now=LEAVING - timedelta(minutes=5))


# --- settings changes ---------------------------------------------------------------------


def test_changing_the_destination_discards_the_held_estimate() -> None:
    """Holding it would answer the previous question with a fresh-looking timestamp."""
    svc, fake = service()
    svc.estimate(LEAVING, now=NOW)

    svc.use_config(
        replace(
            svc._config,
            commute=replace(svc._config.commute, destination_lat=22.0, destination_lon=71.0),
        )
    )
    svc.estimate(LEAVING, now=NOW)

    assert fake.calls == 2


def test_changing_the_travel_mode_discards_it_too() -> None:
    svc, fake = service()
    svc.estimate(LEAVING, now=NOW)
    svc.use_config(replace(svc._config, commute=replace(svc._config.commute, mode="bicycle")))
    svc.estimate(LEAVING, now=NOW)

    assert fake.calls == 2


def test_the_configured_mode_reaches_the_provider() -> None:
    svc, fake = service(mode="motorcycle")
    svc.estimate(LEAVING, now=NOW)

    assert fake.modes == [TravelMode.MOTORCYCLE]


def test_the_buffer_is_added_to_the_arrival() -> None:
    svc, _ = service(buffer_minutes=7)
    view = svc.estimate(LEAVING, now=NOW)

    assert view.arrival is not None
    assert view.arrival.total == Duration(50)


@pytest.mark.parametrize("buffer", [-5, 0])
def test_a_nonsense_buffer_never_shortens_the_journey(buffer: int) -> None:
    svc, _ = service(buffer_minutes=buffer)
    view = svc.estimate(LEAVING, now=NOW)

    assert view.arrival is not None
    assert view.arrival.total == Duration(43)
