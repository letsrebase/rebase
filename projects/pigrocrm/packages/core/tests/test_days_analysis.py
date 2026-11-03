"""AnalyticsService.days_analysis -- REB-756.

The days of a period by month and state, the realised day rate against the rate card, and
the trend of days worked without approval. What these hold the code to: a state stacks in
its own month, a day that never happened is not charted, the rate reads an invoice line
once however many days it bills, a day no card covers is counted apart instead of priced at
zero, and the slip into «senza approvazione» is dated by the transition, not by the day.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from conftest import _invoice_service
from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import today_local
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.invoices.models import InvoiceLine
from pigrocrm.core.invoices.schemas import InvoiceIssue
from pigrocrm.core.storage.local import LocalFileStorage
from pigrocrm.core.work_units.invoicing import assemble_unbilled_work_units_into_new_invoice
from pigrocrm.core.work_units.models import WorkUnit

ADMIN = Actor(id=None, type="system", role="admin")
READER = Actor(id=None, type="user", role="readonly")
YEAR = 2026


def _contract(
    session: Session, name: str = "Acme S.r.l.", *, requires_prior_approval: bool = False
) -> Contract:
    customer = Customer(
        legal_name=f"{name} {uuid4().hex[:6]}",
        vat_number="01234567890",
        sdi_code="ABCDEFG",
        address="Corso Italia 5",
        postal_code="00100",
        city="Roma",
        province="RM",
        country="IT",
    )
    session.add(customer)
    session.flush()
    contract = Contract(
        customer_id=customer.id,
        title="Consulenza",
        start=date(2025, 1, 1),
        renewal_type="none",
        termination_notice_days=30,
        billing_cadence="monthly",
        expense_policy={"kind": "not_reimbursable"},
        requires_prior_approval=requires_prior_approval,
    )
    session.add(contract)
    session.flush()
    return contract


def _card(
    session: Session, contract: Contract, amount: str = "500.00", **extra: object
) -> RateCard:
    payload: dict[str, object] = {
        "contract_id": contract.id,
        "valid_from": date(2025, 1, 1),
        "valid_to": None,
        "type": "daily",
        "amount": Decimal(amount),
        "unit": "day",
    }
    payload.update(extra)
    card = RateCard(**payload)  # type: ignore[arg-type]
    session.add(card)
    session.flush()
    return card


def _day(
    session: Session, contract: Contract, day: date, status: str = "worked", q: str = "1.00"
) -> WorkUnit:
    work_unit = WorkUnit(
        contract_id=contract.id,
        date=day,
        quantity=Decimal(q),
        description="Giornata di consulenza",
        status=status,
    )
    session.add(work_unit)
    session.flush()
    session.refresh(work_unit)
    return work_unit


def _stack(month, status: str) -> Decimal:
    return next(s.days for s in month.by_status if s.status == status)


def test_a_state_stacks_in_its_own_month_and_a_day_that_never_happened_is_left_out(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    _day(db_session, contract, date(YEAR, 1, 5), "worked")
    _day(db_session, contract, date(YEAR, 1, 6), "worked", "0.50")
    _day(db_session, contract, date(YEAR, 1, 7), "proposed")
    _day(db_session, contract, date(YEAR, 2, 3), "proposed")
    revoked = _day(db_session, contract, date(YEAR, 2, 4), "proposed")
    revoked.status = "revoked"
    db_session.flush()

    analysis = AnalyticsService(db_session).days_analysis(
        READER, date(YEAR, 1, 1), date(YEAR, 2, 28)
    )

    january, february = analysis.months
    assert (january.month, february.month) == ("2026-01", "2026-02")
    assert _stack(january, "worked") == Decimal("1.50")
    assert _stack(january, "proposed") == Decimal("1.00")
    assert january.total == Decimal("2.50")
    assert _stack(february, "proposed") == Decimal("1.00")
    assert february.total == Decimal("1.00")
    assert analysis.days_in_period == Decimal("3.50")
    # Only the worked day counts as recorded: a proposal is not work done.
    assert january.register == Decimal("1.50")
    assert february.register == Decimal("0.00")
    # The tallest month is the full bar.
    assert january.total_share == 1.0
    assert february.total_share == pytest.approx(0.4)


def test_a_month_is_averaged_against_the_three_before_it_even_when_they_precede_the_window(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    for month, days in ((1, 3), (2, 3), (3, 6)):
        for day in range(1, days + 1):
            _day(db_session, contract, date(YEAR, month, day))
    for day in range(1, 5):
        _day(db_session, contract, date(YEAR, 4, day))

    analysis = AnalyticsService(db_session).days_analysis(
        READER, date(YEAR, 4, 1), date(YEAR, 4, 30)
    )

    (april,) = analysis.months
    assert april.register == Decimal("4.00")
    assert april.media_previous == Decimal("4.00")  # (3 + 3 + 6) / 3
    assert april.complete is True


def test_the_current_month_is_not_complete_and_is_not_the_last_complete_month(
    db_session: Session,
) -> None:
    today = today_local()
    analysis = AnalyticsService(db_session).days_analysis(READER)

    (month,) = analysis.months
    assert month.month == today.strftime("%Y-%m")
    assert month.complete is False
    assert analysis.last_complete_month is None


def test_the_realised_rate_reads_a_line_once_and_sets_it_against_the_card(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _invoice_service(db_session, local_storage)
    contract = _contract(db_session)
    card = _card(db_session, contract, "500.00")
    _day(db_session, contract, date(YEAR, 3, 5))
    _day(db_session, contract, date(YEAR, 3, 12))
    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )
    service.issue(invoice.id, InvoiceIssue(issue_date=date(YEAR, 3, 31)), ADMIN)
    # The card was revised upwards after the invoice went out: the nominal figure follows
    # the card, the realised one is what was billed.
    card.amount = Decimal("600.00")
    db_session.flush()

    analysis = AnalyticsService(db_session).days_analysis(
        READER, date(YEAR, 3, 1), date(YEAR, 3, 31)
    )

    (by_contract,) = analysis.by_contract
    (by_customer,) = analysis.by_customer
    for rate in (by_contract, by_customer):
        assert rate.invoiced_days == Decimal("2.00")
        # One line of 1000.00 bills both days: read once, not twice.
        assert rate.invoiced_amount == Decimal("1000.00")
        assert rate.actual_rate == Decimal("500.00")
        assert rate.nominal_rate == Decimal("600.00")
        assert rate.days_without_list_price == Decimal("0.00")
    assert analysis.invoiced_days == Decimal("2.00")
    assert analysis.actual_rate == Decimal("500.00")


def test_a_period_that_cuts_a_line_takes_only_its_days_share_of_it(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _invoice_service(db_session, local_storage)
    contract = _contract(db_session)
    _card(db_session, contract, "500.00")
    _day(db_session, contract, date(YEAR, 3, 5))
    _day(db_session, contract, date(YEAR, 3, 12))
    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )
    service.issue(invoice.id, InvoiceIssue(issue_date=date(YEAR, 3, 31)), ADMIN)

    analysis = AnalyticsService(db_session).days_analysis(
        READER, date(YEAR, 3, 1), date(YEAR, 3, 10)
    )

    (rate,) = analysis.by_contract
    # One of the line's two days is inside the period: half of the 1000.00 line, not all of it.
    assert rate.invoiced_days == Decimal("1.00")
    assert rate.invoiced_amount == Decimal("500.00")
    assert rate.actual_rate == Decimal("500.00")


def test_an_hourly_card_does_not_price_a_day_so_the_day_counts_as_uncovered(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _invoice_service(db_session, local_storage)
    contract = _contract(db_session)
    card = _card(db_session, contract, "500.00")
    _day(db_session, contract, date(YEAR, 3, 5))
    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )
    service.issue(invoice.id, InvoiceIssue(issue_date=date(YEAR, 3, 31)), ADMIN)
    card.type = "hourly"
    card.unit = "hour"
    db_session.flush()

    analysis = AnalyticsService(db_session).days_analysis(
        READER, date(YEAR, 3, 1), date(YEAR, 3, 31)
    )

    (rate,) = analysis.by_contract
    assert rate.nominal_rate is None
    assert rate.days_without_list_price == Decimal("1.00")


def test_a_day_written_off_as_unbillable_still_counts_as_recorded(db_session: Session) -> None:
    contract = _contract(db_session)
    written_off = _day(db_session, contract, date(YEAR, 3, 5))
    written_off.status = "non_billable"
    db_session.flush()

    analysis = AnalyticsService(db_session).days_analysis(
        READER, date(YEAR, 3, 1), date(YEAR, 3, 31)
    )

    (march,) = analysis.months
    assert _stack(march, "non_billable") == Decimal("1.00")
    assert march.register == Decimal("1.00")


def test_another_legal_entitys_filter_leaves_the_days_and_the_rate_out(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _invoice_service(db_session, local_storage)
    contract = _contract(db_session)
    _card(db_session, contract)
    _day(db_session, contract, date(YEAR, 3, 5))
    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )
    service.issue(invoice.id, InvoiceIssue(issue_date=date(YEAR, 3, 31)), ADMIN)

    elsewhere = AnalyticsService(db_session).days_analysis(
        READER, date(YEAR, 3, 1), date(YEAR, 3, 31), uuid4()
    )
    here = AnalyticsService(db_session).days_analysis(
        READER, date(YEAR, 3, 1), date(YEAR, 3, 31), contract.legal_entity_id
    )

    assert elsewhere.days_in_period == Decimal("0.00")
    assert elsewhere.by_contract == []
    assert here.days_in_period == Decimal("1.00")
    assert len(here.by_contract) == 1


def test_a_billed_day_no_card_covers_is_counted_apart_and_never_priced_at_zero(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _invoice_service(db_session, local_storage)
    contract = _contract(db_session)
    card = _card(db_session, contract, "500.00")
    _day(db_session, contract, date(YEAR, 3, 5))
    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )
    service.issue(invoice.id, InvoiceIssue(issue_date=date(YEAR, 3, 31)), ADMIN)
    card.valid_to = date(YEAR, 2, 28)
    db_session.flush()

    analysis = AnalyticsService(db_session).days_analysis(
        READER, date(YEAR, 3, 1), date(YEAR, 3, 31)
    )

    (rate,) = analysis.by_contract
    assert rate.actual_rate == Decimal("500.00")
    assert rate.nominal_rate is None
    assert rate.days_without_list_price == Decimal("1.00")


def test_a_draft_invoice_and_a_day_outside_the_period_are_not_in_the_rate(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _invoice_service(db_session, local_storage)
    contract = _contract(db_session)
    _card(db_session, contract)
    _day(db_session, contract, date(YEAR, 3, 5))
    assemble_unbilled_work_units_into_new_invoice(db_session, local_storage, contract.id, ADMIN)

    analysis = AnalyticsService(db_session).days_analysis(
        READER, date(YEAR, 3, 1), date(YEAR, 3, 31)
    )

    assert analysis.by_contract == []
    assert analysis.actual_rate is None
    assert analysis.invoiced_days == Decimal("0.00")
    assert db_session.query(InvoiceLine).count() == 1


def test_a_day_that_slipped_without_approval_is_counted_in_the_month_of_the_slip(
    db_session: Session,
) -> None:
    today = today_local()
    contract = _contract(db_session, requires_prior_approval=True)
    # Asked as worked on a contract that wants prior approval, so the database redirects
    # it: the transition is what the trend counts.
    slipped = _day(db_session, contract, date(today.year, today.month, 1), "worked", "0.50")
    assert slipped.status == "worked_without_approval"

    analysis = AnalyticsService(db_session).days_analysis(READER)
    assert analysis.without_approval_in_period == Decimal("0.50")
    assert analysis.months[0].without_approval == Decimal("0.50")
    assert analysis.months[0].share_without_approval == 1.0
    assert analysis.without_approval_always == Decimal("0.50")
    assert _stack(analysis.months[0], "worked_without_approval") == Decimal("0.50")


def test_a_day_that_slips_twice_counts_once(db_session: Session) -> None:
    today = today_local()
    contract = _contract(db_session, requires_prior_approval=True)
    slipped = _day(db_session, contract, date(today.year, today.month, 1), "worked", "0.50")
    slipped.status = "disputed"
    db_session.flush()
    slipped.status = "worked"  # no approval linked: the database flags it again
    db_session.flush()
    db_session.refresh(slipped)
    assert slipped.status == "worked_without_approval"

    analysis = AnalyticsService(db_session).days_analysis(READER)

    assert analysis.without_approval_in_period == Decimal("0.50")
    assert analysis.without_approval_always == Decimal("0.50")


def test_one_bound_without_the_other_and_a_backwards_period_are_refused_by_name(
    db_session: Session,
) -> None:
    service = AnalyticsService(db_session)
    with pytest.raises(ValidationFailed) as only_from:
        service.days_analysis(READER, date_from=date(YEAR, 1, 1))
    assert only_from.value.details["field"] == "to"
    with pytest.raises(ValidationFailed) as backwards:
        service.days_analysis(READER, date(YEAR, 3, 1), date(YEAR, 1, 1))
    assert backwards.value.details["field"] == "from"
    with pytest.raises(ValidationFailed) as too_long:
        service.days_analysis(READER, date(2016, 1, 1), date(2026, 1, 31))
    assert too_long.value.details["field"] == "to"
    with pytest.raises(ValidationFailed) as too_early:
        service.days_analysis(READER, date(1, 1, 1), date(1, 1, 31))
    assert too_early.value.details["field"] == "from"
    # Exactly ten years is the largest period admitted.
    assert len(service.days_analysis(READER, date(2016, 2, 1), date(2026, 1, 31)).months) == 120
