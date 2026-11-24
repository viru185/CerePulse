"""Comp-off rebuilt credit by credit, against the balance the portal actually enforces.

The shape of the year below is a real one, with the reasons invented: the leave register
summed it to 8 while the portal's own lookup said 5 could be spent, and listed exactly the
five October credits. Every figure the screens show comes from here, so this file pins the
whole case rather than one rule at a time.
"""

from __future__ import annotations

from datetime import date

import pytest

from cerepulse.intelligence.comp_off import (
    CreditState,
    comp_off_ledger,
    expiry_of,
    is_comp_off_spend,
)
from cerepulse.models.application import Application, ApplicationKind, RequestStatus
from cerepulse.models.leave import CompOffLink, CompOffSnapshot, LeaveTransaction

TODAY = date(2026, 11, 24)
APPROVED, PENDING = RequestStatus.APPROVED, RequestStatus.IN_PROCESS


def earned(app_id: str, when: date, days: float = 1.0, status: RequestStatus = APPROVED):
    return Application(app_id, ApplicationKind.COMP_OFF, when, when, days, "Weekend work", status)


def spent(app_id: str, when: date, days: float = 1.0, status: RequestStatus = APPROVED):
    return Application(
        app_id, ApplicationKind.LEAVE, when, when, days, "Family visit", status, leave_type="CO-"
    )


YEAR = [
    earned("5484", date(2026, 2, 17)),
    earned("5544", date(2026, 2, 23), 0.5),
    spent("5742", date(2026, 3, 16)),
    earned("6136", date(2026, 4, 9), 0.5),
    earned("6672", date(2026, 5, 31), 0.5),
    earned("7210", date(2026, 7, 18)),
    earned("7428", date(2026, 8, 4), 0.5),
    spent("7622", date(2026, 8, 26)),
    spent("7732", date(2026, 8, 27), 0.5),
    earned("8115", date(2026, 9, 19)),
    earned("8250", date(2026, 9, 21), status=PENDING),
    earned("8116", date(2026, 9, 26)),
    earned("8220", date(2026, 10, 2)),
    earned("8221", date(2026, 10, 3)),
    earned("8489", date(2026, 10, 7)),
    earned("8409", date(2026, 10, 10)),
    earned("8490", date(2026, 10, 24)),
    spent("8728", date(2026, 11, 12), status=PENDING),
    spent("8730", date(2026, 11, 13), status=PENDING),
    earned("8936", date(2026, 11, 19), status=PENDING),
]

#: What opening each CO- application showed. The March one predates the financial year and
#: carries an empty field.
LINKS = {
    "5742": [CompOffLink("5742", None, 0.0)],
    "7622": [CompOffLink("7622", date(2026, 7, 18), 1.0)],
    "7732": [CompOffLink("7732", date(2026, 5, 31), 0.5, "Second")],
    "8728": [CompOffLink("8728", date(2026, 9, 19), 1.0)],
    "8730": [CompOffLink("8730", date(2026, 9, 26), 1.0)],
}

OCTOBER = [date(2026, 10, day) for day in (2, 3, 7, 10, 24)]


def states(ledger) -> dict[date, CreditState]:
    return {credit.earned_on: credit.state for credit in ledger.credits}


def test_the_usable_balance_is_the_portals_five_not_the_registers_eight() -> None:
    ledger = comp_off_ledger(today=TODAY, applications=YEAR, links=LINKS)

    assert ledger.usable == 5
    assert [credit.earned_on for credit in ledger.available] == OCTOBER


def test_every_credit_says_what_became_of_it() -> None:
    ledger = comp_off_ledger(today=TODAY, applications=YEAR, links=LINKS)

    assert states(ledger) == {
        date(2026, 2, 17): CreditState.USED,
        date(2026, 2, 23): CreditState.LAPSED,
        date(2026, 4, 9): CreditState.LAPSED,
        date(2026, 5, 31): CreditState.USED,
        date(2026, 7, 18): CreditState.USED,
        date(2026, 8, 4): CreditState.LAPSED,
        date(2026, 9, 19): CreditState.BOOKED,
        date(2026, 9, 21): CreditState.PENDING,
        date(2026, 9, 26): CreditState.BOOKED,
        **dict.fromkeys(OCTOBER, CreditState.AVAILABLE),
        date(2026, 11, 19): CreditState.PENDING,
    }
    assert ledger.pending_days == 2
    assert ledger.booked_days == 2
    assert sum(c.remaining for c in ledger.lapsed_since(date(2026, 4, 1))) == 1.0


def test_a_leave_names_the_credit_the_portal_says_it_spent() -> None:
    ledger = comp_off_ledger(today=TODAY, applications=YEAR, links=LINKS)

    (credit,) = ledger.credits_for("7732")
    assert credit.earned_on == date(2026, 5, 31)
    assert credit.spent_by[0].from_portal
    # The empty March link is inferred instead: the oldest whole credit valid on the day.
    (march,) = ledger.credits_for("5742")
    assert march.earned_on == date(2026, 2, 17)
    assert not march.spent_by[0].from_portal
    assert ledger.inferred == ("5742",)


def test_without_links_the_inference_lands_on_the_same_credits() -> None:
    """Oldest valid, same size first: it reproduces every link the portal has recorded."""
    ledger = comp_off_ledger(today=TODAY, applications=YEAR)

    assert ledger.usable == 5
    for app_id, (link,) in LINKS.items():
        if link.credit_date is None:
            continue
        (credit,) = ledger.credits_for(app_id)
        assert credit.earned_on == link.credit_date, app_id


def test_a_half_day_takes_a_half_credit_even_when_a_whole_one_is_older() -> None:
    applications = [
        earned("1", date(2026, 6, 1)),
        earned("2", date(2026, 6, 8), 0.5),
        spent("3", date(2026, 6, 20), 0.5),
    ]
    ledger = comp_off_ledger(today=date(2026, 6, 21), applications=applications)

    assert states(ledger) == {
        date(2026, 6, 1): CreditState.AVAILABLE,
        date(2026, 6, 8): CreditState.USED,
    }


def test_a_pending_credit_cannot_pay_for_anything() -> None:
    applications = [earned("1", date(2026, 6, 1), status=PENDING), spent("2", date(2026, 6, 10))]
    ledger = comp_off_ledger(today=date(2026, 6, 11), applications=applications)

    assert states(ledger) == {date(2026, 6, 1): CreditState.PENDING}
    assert [spend.day for spend in ledger.unmatched] == [date(2026, 6, 10)]


def test_rejected_applications_neither_earn_nor_spend() -> None:
    applications = [
        earned("1", date(2026, 6, 1), status=RequestStatus.REJECTED),
        earned("2", date(2026, 6, 2)),
        spent("3", date(2026, 6, 10), status=RequestStatus.REJECTED),
    ]
    ledger = comp_off_ledger(today=date(2026, 6, 11), applications=applications)

    assert states(ledger) == {date(2026, 6, 2): CreditState.AVAILABLE}
    assert ledger.unmatched == ()


@pytest.mark.parametrize(
    ("earned_on", "expires_on"),
    [
        (date(2026, 10, 2), date(2026, 12, 31)),
        (date(2026, 10, 24), date(2027, 1, 22)),
        # Ninety days would reach May; the financial year ends first.
        (date(2026, 2, 23), date(2026, 3, 31)),
        (date(2027, 3, 31), date(2027, 3, 31)),
        (date(2027, 4, 1), date(2027, 6, 30)),
    ],
)
def test_a_credit_lapses_ninety_days_on_or_at_the_financial_years_end(
    earned_on: date, expires_on: date
) -> None:
    assert expiry_of(earned_on) == expires_on


def test_a_credit_is_still_usable_on_its_last_day() -> None:
    applications = [earned("1", date(2026, 10, 2))]

    on_the_day = comp_off_ledger(today=date(2026, 12, 31), applications=applications)
    after = comp_off_ledger(today=date(2027, 1, 1), applications=applications)

    assert on_the_day.usable == 1
    assert states(after) == {date(2026, 10, 2): CreditState.LAPSED}


def test_the_register_fills_in_what_no_application_explains() -> None:
    """A credit HR entered by hand, and a register that lists it three times."""
    register = [
        LeaveTransaction("CO- / CO+", 0, 0, 1.0, 1, date(2026, 10, 2), "x |Deploy night")
        for _ in range(3)
    ] + [LeaveTransaction("CO- / CO+", 1, 1.0, 0, 0, date(2026, 10, 20), "Family visit")]
    ledger = comp_off_ledger(today=date(2026, 10, 25), register=register)

    (credit,) = ledger.credits
    assert credit.reason == "Deploy night"
    assert credit.state is CreditState.USED
    assert credit.spent_by[0].day == date(2026, 10, 20)


def test_the_register_cannot_resurrect_a_credit_an_application_explains() -> None:
    register = [LeaveTransaction("CO- / CO+", 0, 0, 1.0, 1, date(2026, 10, 2), "Deploy")]
    applications = [earned("1", date(2026, 10, 2), status=RequestStatus.REJECTED)]

    ledger = comp_off_ledger(today=date(2026, 10, 5), applications=applications, register=register)

    assert ledger.credits == ()


def test_the_portal_reading_is_compared_only_on_its_own_day() -> None:
    snapshot = CompOffSnapshot(TODAY, 5.0, tuple((when, 1.0) for when in OCTOBER))
    ledger = comp_off_ledger(today=TODAY, applications=YEAR, links=LINKS, snapshot=snapshot)
    assert ledger.portal_agrees is True

    stale = comp_off_ledger(
        today=date(2026, 11, 25), applications=YEAR, links=LINKS, snapshot=snapshot
    )
    assert stale.portal_agrees is None

    disagreeing = CompOffSnapshot(TODAY, 4.0)
    assert (
        comp_off_ledger(
            today=TODAY, applications=YEAR, links=LINKS, snapshot=disagreeing
        ).portal_agrees
        is False
    )


def test_expiring_within_counts_only_what_is_usable() -> None:
    ledger = comp_off_ledger(today=TODAY, applications=YEAR, links=LINKS)

    # 2 Oct lapses 31 Dec (37 days), 3 Oct on 1 Jan; 7 Oct on 5 Jan (42 days).
    assert ledger.expiring_within(40) == 2
    assert ledger.next_expiry is not None
    assert ledger.next_expiry.expires_on == date(2026, 12, 31)


def test_a_multi_day_spend_takes_one_credit_per_day() -> None:
    applications = [
        earned("1", date(2026, 6, 1)),
        earned("2", date(2026, 6, 2)),
        Application(
            "3",
            ApplicationKind.LEAVE,
            date(2026, 6, 10),
            date(2026, 6, 11),
            2.0,
            "",
            APPROVED,
            leave_type="CO-",
        ),
    ]
    ledger = comp_off_ledger(today=date(2026, 6, 12), applications=applications)

    assert [c.state for c in ledger.credits] == [CreditState.USED, CreditState.USED]
    assert [c.spent_by[0].day for c in ledger.credits] == [date(2026, 6, 10), date(2026, 6, 11)]


def test_only_leave_typed_co_minus_is_a_spend() -> None:
    assert is_comp_off_spend(spent("1", TODAY))
    assert not is_comp_off_spend(earned("1", TODAY))
    planned = Application("2", ApplicationKind.LEAVE, TODAY, TODAY, 1.0, "", APPROVED, "PL")
    assert not is_comp_off_spend(planned)
