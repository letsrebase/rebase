"""The forecasts of money in (REB-997, spec 2026-11-09, due items, § 1.2).

Forecasts are stored due items materialised from the active contracts' recurring fees, their
worked days on no invoice line, and the drafts and proformas; each is replaced by the document
that arrives and never added to it.
"""

import datetime as dt
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract, RateCard, RenewalAssumption
from pigrocrm.core.db.clock import add_months, today_local
from pigrocrm.core.due_items.forecast import (
    CONTRACT_OCCURRENCE,
    HORIZON_DAYS,
    SALES_DRAFT,
    ForecastService,
    forecast_key,
    refresh_forecasts,
)
from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.invoices.due_date import due_date_for_customer
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.invoices.repository import InvoiceRepository
from pigrocrm.core.invoices.schemas import (
    InvoiceAnnul,
    InvoiceCreate,
    InvoiceIssue,
    InvoiceLineIn,
)
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.storage.local import LocalFileStorage
from pigrocrm.core.work_units.invoicing import assemble_unbilled_work_units_into_new_invoice
from pigrocrm.core.work_units.models import WorkUnit

ADMIN = Actor(id=None, type="system", role="admin")
TODAY = today_local()


def _install_profiles(session: Session) -> None:
    """The legal entity's identity and a flat-rate fiscal profile, as `test_work_units_invoicing`
    installs them (duplicated: `conftest` is an ambiguous module name across the test roots)."""
    from pigrocrm.core.fiscal.repository import FiscalProfileRepository
    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
    from pigrocrm.core.fiscal.service import FiscalProfileService
    from pigrocrm.core.legal_entities.repository import LegalEntityRepository
    from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
    from pigrocrm.core.legal_entities.service import LegalEntityService

    LegalEntityService(session).upsert_default(
        LegalEntityUpsert(
            legal_name="Studio Rossi",
            vat_number="01234567890",
            tax_code="HMCRFT00A01H501K",
            address="Via Vittorio Veneto 12",
            postal_code="20124",
            city="Milano",
            province="MI",
            country="IT",
            email="mario@example.com",
        ),
        ADMIN,
    )
    legal_entity = LegalEntityRepository(session).default()
    assert legal_entity is not None
    if FiscalProfileRepository(session).get(legal_entity.id) is None:
        FiscalProfileService(session).upsert(FiscalProfileUpsert(regime_code="RF19"), ADMIN)


def _customer(session: Session, **overrides: object) -> Organization:
    payload: dict[str, object] = {
        "legal_name": f"ACME {uuid4()}",
        "vat_number": str(uuid4().int)[:11],
        "sdi_code": "ABCDEFG",
        "address": "Corso Italia 5",
        "postal_code": "00100",
        "city": "Roma",
        "province": "RM",
        "country": "IT",
    }
    payload.update(overrides)
    customer = Organization(**payload)  # type: ignore[arg-type]
    session.add(customer)
    session.flush()
    return customer


def _contract(
    session: Session, customer: Organization | None = None, **overrides: object
) -> Contract:
    payload: dict[str, object] = {
        "customer_id": (customer or _customer(session)).id,
        "title": "Consulenza",
        "start": TODAY - dt.timedelta(days=200),
        "renewal_type": "none",
        "termination_notice_days": 30,
        "billing_cadence": "monthly",
        "expense_policy": {"kind": "not_reimbursable"},
        "status": "active",
    }
    payload.update(overrides)
    contract = Contract(**payload)  # type: ignore[arg-type]
    session.add(contract)
    session.flush()
    return contract


def _card(session: Session, contract: Contract, **overrides: object) -> RateCard:
    payload: dict[str, object] = {
        "contract_id": contract.id,
        "valid_from": TODAY,
        "type": "fixed_recurring",
        "amount": Decimal("1000.00"),
        "unit": "month",
        "delivery_period": "monthly",
    }
    payload.update(overrides)
    card = RateCard(**payload)  # type: ignore[arg-type]
    session.add(card)
    session.flush()
    return card


def _day_card(session: Session, contract: Contract) -> RateCard:
    return _card(
        session,
        contract,
        type="daily",
        unit="day",
        amount=Decimal("500.00"),
        valid_from=TODAY - dt.timedelta(days=200),
        delivery_period=None,
    )


def _worked_day(session: Session, contract: Contract, day: dt.date) -> WorkUnit:
    work_unit = WorkUnit(
        contract_id=contract.id,
        date=day,
        quantity=Decimal("1.00"),
        description="Giornata di consulenza",
        status="worked",
    )
    session.add(work_unit)
    session.flush()
    return work_unit


def _live(session: Session, **where: object) -> list[DueItem]:
    stmt = select(DueItem).where(DueItem.state == "forecast")
    for column, value in where.items():
        stmt = stmt.where(getattr(DueItem, column) == value)
    return list(session.scalars(stmt.order_by(DueItem.due_date, DueItem.id)))


def _occurrences(card: RateCard) -> list[dt.date]:
    horizon = TODAY + dt.timedelta(days=HORIZON_DAYS)
    dates, index = [], 0
    while (day := add_months(card.valid_from, index)) <= horizon:
        dates.append(day)
        index += 1
    return dates


def _terms(session: Session, customer: Organization, day: dt.date) -> dt.date:
    from pigrocrm.core.fiscal.service import FiscalProfileService

    return due_date_for_customer(
        InvoiceRepository(session),
        day,
        customer.id,
        FiscalProfileService(session).snapshot().due_days,
    )


# --- the recurring fee --------------------------------------------------------------------


def test_an_active_contracts_recurring_fee_becomes_one_forecast_for_each_occurrence(
    db_session: Session,
) -> None:
    _install_profiles(db_session)
    customer = _customer(db_session)
    contract = _contract(db_session, customer)
    card = _card(db_session, contract)

    report = ForecastService(db_session).refresh_contract(contract.id)

    live = _live(db_session, source_id=contract.id)
    occurrences = _occurrences(card)
    assert report.created == len(live) == len(occurrences)
    assert {item.forecast_key for item in live} == {
        forecast_key(CONTRACT_OCCURRENCE, card.id, day) for day in occurrences
    }
    first = min(live, key=lambda item: item.due_date or dt.date.max)
    assert (first.direction, first.state, first.source_kind, first.source_rule) == (
        "in",
        "forecast",
        CONTRACT_OCCURRENCE,
        "contract_recurring_fee",
    )
    assert (first.amount, first.organization_id, first.currency) == (
        Decimal("1000.00"),
        customer.id,
        "EUR",
    )
    # Dated as the invoice will be: the customer's terms, else the profile's days, from the
    # occurrence; never the contract's own `payment_days`.
    assert first.due_date == _terms(db_session, customer, TODAY)


def test_the_date_follows_the_customers_terms_and_not_the_contracts_payment_days(
    db_session: Session,
) -> None:
    _install_profiles(db_session)
    customer = _customer(db_session, payment_days=60, payment_end_of_month=True)
    contract = _contract(db_session, customer, payment_days=7, payment_end_of_month=False)
    _card(db_session, contract, delivery_period="one_off")

    ForecastService(db_session).refresh_contract(contract.id)

    (item,) = _live(db_session, source_id=contract.id)
    assert item.due_date == due_date_for_customer(
        InvoiceRepository(db_session), TODAY, customer.id, 30
    )
    assert item.due_date != TODAY + dt.timedelta(days=7)


def test_only_an_active_contract_is_forecast_and_a_contract_that_stops_loses_its_forecasts(
    db_session: Session,
) -> None:
    _install_profiles(db_session)
    drafted = _contract(db_session, status="draft")
    _card(db_session, drafted)
    active = _contract(db_session)
    _card(db_session, active)
    service = ForecastService(db_session)

    service.refresh_contract(drafted.id)
    service.refresh_contract(active.id)
    assert _live(db_session, source_id=drafted.id) == []
    live = _live(db_session, source_id=active.id)
    assert live

    for status in ("terminated", "expired"):
        active.status = status
        db_session.flush()
        report = service.refresh_contract(active.id)
        assert _live(db_session, source_id=active.id) == []
        assert report.dropped == len(live)
        active.status = "active"
        assert service.refresh_contract(active.id).created == len(live)


def test_the_renewal_assumption_and_the_deals_are_not_forecast(db_session: Session) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session, end=TODAY + dt.timedelta(days=60))
    card = _card(db_session, contract)
    db_session.add(
        RenewalAssumption(
            contract_id=contract.id,
            probability=80,
            expected_volume=Decimal("50000.00"),
            horizon_until=TODAY + dt.timedelta(days=700),
        )
    )
    db_session.flush()

    ForecastService(db_session).refresh_contract(contract.id)

    live = _live(db_session, source_id=contract.id)
    end = TODAY + dt.timedelta(days=60)
    # One forecast for each occurrence up to the contract's own end and not one more, whatever
    # the assumption says of the time after it.
    assert len(live) == len([day for day in _occurrences(card) if day <= end])
    assert {item.amount for item in live} == {Decimal("1000.00")}
    assert {item.source_kind for item in db_session.scalars(select(DueItem))} == {
        CONTRACT_OCCURRENCE
    }


def test_a_change_to_the_card_revises_the_forecast_and_a_repeat_changes_nothing(
    db_session: Session,
) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session)
    card = _card(db_session, contract)
    service = ForecastService(db_session)
    service.refresh_contract(contract.id)
    before = {item.id for item in _live(db_session, source_id=contract.id)}

    assert service.refresh_contract(contract.id).__dict__ == {
        "created": 0,
        "revised": 0,
        "dropped": 0,
        "failed": 0,
    }
    card.amount = Decimal("1200.00")
    db_session.flush()
    report = service.refresh_contract(contract.id)

    live = _live(db_session, source_id=contract.id)
    assert {item.id for item in live} == before
    assert report.revised == len(live)
    assert {item.amount for item in live} == {Decimal("1200.00")}


def test_a_forecast_whose_date_has_passed_is_dropped(db_session: Session) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session)
    _card(db_session, contract)
    service = ForecastService(db_session)
    service.refresh_contract(contract.id)
    live = _live(db_session, source_id=contract.id)
    first = min(live, key=lambda item: item.due_date or dt.date.max)
    assert first.due_date is not None

    service.refresh_contract(contract.id, today=first.due_date + dt.timedelta(days=1))

    db_session.refresh(first)
    assert first.state == "dropped"
    assert first.dropped_reason


def test_only_one_live_forecast_holds_a_key(db_session: Session) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session)
    _card(db_session, contract, delivery_period="one_off")
    ForecastService(db_session).refresh_contract(contract.id)
    (item,) = _live(db_session, source_id=contract.id)

    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(
            DueItem(
                legal_entity_id=item.legal_entity_id,
                direction="in",
                state="forecast",
                counterparty_type="organization",
                organization_id=item.organization_id,
                amount=Decimal("1.00"),
                source_kind=CONTRACT_OCCURRENCE,
                source_id=contract.id,
                source_rule="contract_recurring_fee",
                forecast_key=item.forecast_key,
            )
        )
        db_session.flush()


# --- issuing replaces the forecast ---------------------------------------------------------


def _fee_invoice(
    session: Session, storage: LocalFileStorage, contract: Contract, card: RateCard, day: dt.date
) -> UUID:
    created = InvoiceService(session, storage).create(
        InvoiceCreate(
            customer_id=contract.customer_id,
            lines=[
                InvoiceLineIn(
                    description="Canone",
                    unit_price=Decimal("1000.00"),
                    rate_card_id=card.id,
                    period_start=day,
                )
            ],
        ),
        ADMIN,
        legal_entity_id=contract.legal_entity_id,
    )
    return created.id


def test_issuing_an_invoice_for_a_contract_period_replaces_its_forecast_instead_of_adding_to_it(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session)
    card = _card(db_session, contract)
    service = ForecastService(db_session)
    service.refresh_contract(contract.id)
    occurrences = _occurrences(card)
    key = forecast_key(CONTRACT_OCCURRENCE, card.id, occurrences[0])
    assert any(item.forecast_key == key for item in _live(db_session))

    invoice_id = _fee_invoice(db_session, local_storage, contract, card, occurrences[0])

    # A draft already stands for its period: the contract's forecast is dropped and the draft's
    # own is the only one for that money.
    live = _live(db_session)
    assert key not in {item.forecast_key for item in live}
    (draft,) = [item for item in live if item.source_kind == SALES_DRAFT]
    assert (draft.source_id, draft.amount, draft.source_rule) == (
        invoice_id,
        Decimal("1000.00"),
        "draft_invoice",
    )

    InvoiceService(db_session, local_storage).issue(invoice_id, InvoiceIssue(), ADMIN)

    assert _live(db_session, source_kind=SALES_DRAFT) == []
    assert key not in {item.forecast_key for item in _live(db_session)}
    (issued,) = db_session.scalars(
        select(DueItem).where(
            DueItem.source_kind == "sales_invoice", DueItem.source_id == invoice_id
        )
    ).all()
    assert (issued.state, issued.amount) == ("expected", Decimal("1000.00"))
    # What is still forecast is the other occurrences, and the job does not bring this one back.
    still = {item.forecast_key for item in _live(db_session)}
    assert still == {forecast_key(CONTRACT_OCCURRENCE, card.id, day) for day in occurrences[1:]}
    service.refresh_all(today=TODAY)
    assert {item.forecast_key for item in _live(db_session)} == still
    total_for_period = db_session.scalars(
        select(DueItem).where(
            DueItem.organization_id == contract.customer_id,
            DueItem.state.in_(("forecast", "expected")),
            DueItem.due_date == issued.due_date,
        )
    ).all()
    assert len(total_for_period) == 1


def test_deleting_the_draft_or_annulling_the_invoice_brings_the_forecast_back(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session)
    card = _card(db_session, contract)
    day = _occurrences(card)[0]
    key = forecast_key(CONTRACT_OCCURRENCE, card.id, day)
    service = InvoiceService(db_session, local_storage)

    draft_id = _fee_invoice(db_session, local_storage, contract, card, day)
    assert key not in {item.forecast_key for item in _live(db_session)}
    service.soft_delete(draft_id, ADMIN)
    assert key in {item.forecast_key for item in _live(db_session)}
    assert _live(db_session, source_kind=SALES_DRAFT) == []

    issued_id = _fee_invoice(db_session, local_storage, contract, card, day)
    service.issue(issued_id, InvoiceIssue(), ADMIN)
    assert key not in {item.forecast_key for item in _live(db_session)}
    service.annul(issued_id, InvoiceAnnul(reason="errore"), ADMIN)
    assert key in {item.forecast_key for item in _live(db_session)}


def test_a_line_naming_another_customers_card_is_refused(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_profiles(db_session)
    card = _card(db_session, _contract(db_session))
    stranger = _customer(db_session)

    with pytest.raises(ValidationFailed):
        InvoiceService(db_session, local_storage).create(
            InvoiceCreate(
                customer_id=stranger.id,
                lines=[
                    InvoiceLineIn(
                        description="Canone",
                        unit_price=Decimal("1000.00"),
                        rate_card_id=card.id,
                        period_start=TODAY,
                    )
                ],
            ),
            ADMIN,
        )


def test_a_period_needs_its_card_and_a_card_its_period() -> None:
    with pytest.raises(ValueError, match="tutti e due"):
        InvoiceLineIn(description="x", unit_price=Decimal("1.00"), rate_card_id=uuid4())
    with pytest.raises(ValueError, match="tutti e due"):
        InvoiceLineIn(description="x", unit_price=Decimal("1.00"), period_start=TODAY)


# --- worked days ---------------------------------------------------------------------------


def test_worked_days_on_no_line_are_forecast_and_replaced_by_the_draft_they_are_bound_to(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_profiles(db_session)
    customer = _customer(db_session)
    contract = _contract(db_session, customer)
    _day_card(db_session, contract)
    last_month = TODAY.replace(day=1) - dt.timedelta(days=1)
    _worked_day(db_session, contract, last_month.replace(day=3))
    _worked_day(db_session, contract, last_month.replace(day=4))
    service = ForecastService(db_session)

    service.refresh_contract(contract.id)

    (forecast,) = _live(db_session, source_id=contract.id)
    assert (forecast.source_rule, forecast.amount, forecast.direction) == (
        "contract_worked_days",
        Decimal("1000.00"),
        "in",
    )
    # The period has ended, so it is billed today at the earliest, plus the customer's terms.
    assert forecast.due_date == _terms(db_session, customer, TODAY)

    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )

    live = _live(db_session)
    assert [item.source_kind for item in live] == [SALES_DRAFT]
    assert (live[0].source_id, live[0].amount) == (invoice.id, Decimal("1000.00"))

    InvoiceService(db_session, local_storage).soft_delete(invoice.id, ADMIN)
    (back,) = _live(db_session)
    assert (back.source_rule, back.amount) == ("contract_worked_days", Decimal("1000.00"))

    again = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )
    InvoiceService(db_session, local_storage).issue(again.id, InvoiceIssue(), ADMIN)
    assert _live(db_session) == []
    (issued,) = db_session.scalars(
        select(DueItem).where(DueItem.source_kind == "sales_invoice")
    ).all()
    assert issued.amount == Decimal("1000.00")


def test_a_contract_billed_on_completion_with_no_end_has_an_undated_forecast(
    db_session: Session,
) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session, billing_cadence="on_completion")
    _day_card(db_session, contract)
    _worked_day(db_session, contract, TODAY - dt.timedelta(days=3))

    ForecastService(db_session).refresh_contract(contract.id)

    (forecast,) = _live(db_session, source_id=contract.id)
    assert (forecast.own_date, forecast.due_date) == (None, None)


def test_a_day_with_no_rate_card_in_force_is_not_forecast(db_session: Session) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session)
    _worked_day(db_session, contract, TODAY - dt.timedelta(days=3))

    ForecastService(db_session).refresh_contract(contract.id)

    assert _live(db_session, source_id=contract.id) == []


# --- drafts and proformas ------------------------------------------------------------------


def _draft(
    session: Session, storage: LocalFileStorage, customer: Organization, kind: str = "invoice"
) -> UUID:
    return (
        InvoiceService(session, storage)
        .create(
            InvoiceCreate(
                customer_id=customer.id,
                type=kind,  # type: ignore[arg-type]
                lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("300.00"))],
            ),
            ADMIN,
        )
        .id
    )


def test_a_draft_a_proforma_and_a_confirmed_proforma_are_each_forecast_once(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_profiles(db_session)
    customer = _customer(db_session)
    service = InvoiceService(db_session, local_storage)

    draft_id = _draft(db_session, local_storage, customer)
    proforma_id = _draft(db_session, local_storage, customer, "proforma")
    service.confirm_proforma(proforma_id, ADMIN)

    live = _live(db_session, source_kind=SALES_DRAFT)
    assert {item.source_id for item in live} == {draft_id, proforma_id}
    assert {item.amount for item in live} == {Decimal("300.00")}
    assert {item.due_date for item in live} == {_terms(db_session, customer, TODAY)}

    service.replace_lines(
        draft_id, [InvoiceLineIn(description="Altro", unit_price=Decimal("450.00"))], ADMIN
    )
    (draft_forecast,) = _live(db_session, source_kind=SALES_DRAFT, source_id=draft_id)
    assert draft_forecast.amount == Decimal("450.00")

    service.soft_delete(draft_id, ADMIN)
    assert [item.source_id for item in _live(db_session, source_kind=SALES_DRAFT)] == [proforma_id]


def test_issuing_a_draft_or_a_proforma_replaces_its_forecast(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_profiles(db_session)
    customer = _customer(db_session)
    service = InvoiceService(db_session, local_storage)
    draft_id = _draft(db_session, local_storage, customer)
    proforma_id = _draft(db_session, local_storage, customer, "proforma")
    service.confirm_proforma(proforma_id, ADMIN)

    issued = service.issue(draft_id, InvoiceIssue(), ADMIN)
    from_proforma = service.issue(proforma_id, InvoiceIssue(), ADMIN)

    assert _live(db_session) == []
    items = db_session.scalars(
        select(DueItem).where(DueItem.state == "expected", DueItem.organization_id == customer.id)
    ).all()
    assert {item.source_id for item in items} == {issued.id, from_proforma.id}
    assert proforma_id != from_proforma.id
    dropped = db_session.scalars(select(DueItem).where(DueItem.source_kind == SALES_DRAFT)).all()
    assert {item.state for item in dropped} == {"dropped"}


# --- the daily job -------------------------------------------------------------------------


def test_the_daily_job_creates_what_is_missing_and_a_second_run_changes_nothing(
    db_session: Session,
) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session)
    card = _card(db_session, contract)
    invoice = Invoice(
        customer_id=contract.customer_id,
        type="invoice",
        status="draft",
        total=Decimal("120.00"),
        currency="EUR",
    )
    db_session.add(invoice)
    db_session.flush()

    first = ForecastService(db_session).refresh_all(today=TODAY, commit=False)
    second = ForecastService(db_session).refresh_all(today=TODAY, commit=False)

    assert first.created == len(_occurrences(card)) + 1
    assert (second.created, second.revised, second.dropped) == (0, 0, 0)


def test_a_rehearsal_leaves_nothing_behind(db_session: Session) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session)
    _card(db_session, contract)

    report = refresh_forecasts(db_session, today=TODAY, dry_run=True)

    assert report.created > 0
    assert _live(db_session) == []


def test_every_invoice_line_of_a_contract_carries_its_card_and_period(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session)
    card = _day_card(db_session, contract)
    day = TODAY - dt.timedelta(days=5)
    _worked_day(db_session, contract, day)

    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )

    (line,) = db_session.scalars(
        select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id)
    ).all()
    assert (line.rate_card_id, line.period_start) == (card.id, day.replace(day=1))


def test_replacing_the_lines_of_a_draft_gives_back_the_period_the_old_lines_billed(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session)
    card = _card(db_session, contract)
    day = _occurrences(card)[0]
    key = forecast_key(CONTRACT_OCCURRENCE, card.id, day)
    draft_id = _fee_invoice(db_session, local_storage, contract, card, day)
    assert key not in {item.forecast_key for item in _live(db_session)}

    InvoiceService(db_session, local_storage).replace_lines(
        draft_id, [InvoiceLineIn(description="Altro", unit_price=Decimal("10.00"))], ADMIN
    )

    live = _live(db_session)
    assert key in {item.forecast_key for item in live}
    (draft,) = [item for item in live if item.source_kind == SALES_DRAFT]
    assert draft.amount == Decimal("10.00")


def test_a_contract_that_cannot_be_read_does_not_stop_the_job_for_the_others(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_profiles(db_session)
    broken = _contract(db_session)
    _card(db_session, broken)
    fine = _contract(db_session)
    card = _card(db_session, fine)
    original = ForecastService._contract_wants

    def wants(self: ForecastService, contract: Contract, today: dt.date):  # type: ignore[no-untyped-def]
        if contract.id == broken.id:
            raise RuntimeError("cannot be priced")
        return original(self, contract, today)

    monkeypatch.setattr(ForecastService, "_contract_wants", wants)

    report = ForecastService(db_session).refresh_all(today=TODAY, commit=False)

    assert report.failed == 1
    assert _live(db_session, source_id=broken.id) == []
    assert len(_live(db_session, source_id=fine.id)) == len(_occurrences(card))
