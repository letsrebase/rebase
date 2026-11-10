"""«Fattura il canone» (REB-1032): one occurrence of a contract's recurring fee becomes a draft
invoice whose line names the rate card and the period, and issuing that draft replaces the
occurrence's forecast.

The forecasts themselves are `test_due_item_forecasts.py`'s; what is asked here is the action
that writes the invoice they wait for, and the read the contract's page draws its rows from.
"""

import datetime as dt
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.fee_billing import FeeBillingService
from pigrocrm.core.contracts.fee_schemas import FeeBillRequest
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.db.clock import add_months, today_local
from pigrocrm.core.due_items.forecast import (
    CONTRACT_OCCURRENCE,
    HORIZON_DAYS,
    SALES_DRAFT,
    ForecastService,
    forecast_key,
)
from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.errors import (
    AgentForbidden,
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.invoices.schemas import InvoiceIssue
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="user", role="admin")  # type: ignore[arg-type]
COLLABORATOR = Actor(id=None, type="user", role="collaborator")  # type: ignore[arg-type]
AGENT = Actor(id=uuid4(), type="mcp", role="admin")
FULL_ACCESS_AGENT = Actor(id=uuid4(), type="mcp", role="admin", full_access=True)
TODAY = today_local()


def _install_profiles(session: Session) -> None:
    """The legal entity's identity and a flat-rate fiscal profile, as the forecast tests install
    them (duplicated: `conftest` is an ambiguous module name across the test roots)."""
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


def _customer(session: Session) -> Organization:
    customer = Organization(
        legal_name=f"ACME {uuid4()}",
        vat_number=str(uuid4().int)[:11],
        sdi_code="ABCDEFG",
        address="Corso Italia 5",
        postal_code="00100",
        city="Roma",
        province="RM",
        country="IT",
    )
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


def _service(session: Session, storage: LocalFileStorage) -> FeeBillingService:
    return FeeBillingService(session, storage)


def _bill(
    session: Session,
    storage: LocalFileStorage,
    contract: Contract,
    card: RateCard,
    day: dt.date,
    actor: Actor = ADMIN,
):  # type: ignore[no-untyped-def]
    return _service(session, storage).bill(
        contract.id, FeeBillRequest(rate_card_id=card.id, period_start=day), actor
    )


def _live(session: Session) -> list[DueItem]:
    return list(
        session.scalars(
            select(DueItem).where(DueItem.state == "forecast").order_by(DueItem.due_date)
        )
    )


def _occurrences(card: RateCard) -> list[dt.date]:
    horizon = TODAY + dt.timedelta(days=HORIZON_DAYS)
    dates, index = [], 0
    while (day := add_months(card.valid_from, index)) <= horizon:
        dates.append(day)
        index += 1
    return dates


# --- billing --------------------------------------------------------------------------------


def test_billing_an_occurrence_makes_a_draft_whose_line_names_the_card_and_the_period(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_profiles(db_session)
    customer = _customer(db_session)
    contract = _contract(db_session, customer, title="Consulenza strategica")
    card = _card(db_session, contract, amount=Decimal("1200.00"))
    day = _occurrences(card)[1]

    billed = _bill(db_session, local_storage, contract, card, day)

    assert billed.created is True
    invoice = billed.invoice
    assert (invoice.type, invoice.status, invoice.number) == ("invoice", "draft", None)
    assert invoice.customer_id == customer.id
    assert invoice.legal_entity_id == contract.legal_entity_id
    assert invoice.taxable_amount == Decimal("1200.00")
    (line,) = db_session.scalars(
        select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id)
    ).all()
    assert (line.rate_card_id, line.period_start) == (card.id, day)
    assert (line.quantity, line.unit_price) == (Decimal("1.000000"), Decimal("1200.000000"))
    following = add_months(card.valid_from, 2) - dt.timedelta(days=1)
    assert line.description == (
        f"Canone Consulenza strategica dal {day.strftime('%d/%m/%Y')} "
        f"al {following.strftime('%d/%m/%Y')}"
    )
    (entry,) = db_session.scalars(
        select(Activity).where(Activity.entity_type == "contract", Activity.kind == "fee_billed")
    ).all()
    assert entry.entity_id == contract.id
    assert entry.payload == {
        "rate_card_id": str(card.id),
        "period_start": day.isoformat(),
        "invoice_id": str(invoice.id),
    }


def test_the_draft_stands_for_its_occurrence_in_the_forecast_and_nothing_else_moves(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session)
    card = _card(db_session, contract)
    ForecastService(db_session).refresh_contract(contract.id)
    days = _occurrences(card)
    assert forecast_key(CONTRACT_OCCURRENCE, card.id, days[0]) in {
        item.forecast_key for item in _live(db_session)
    }

    billed = _bill(db_session, local_storage, contract, card, days[0])

    live = _live(db_session)
    # The contract's forecast of the period is replaced by the draft's own, never added to it.
    assert forecast_key(CONTRACT_OCCURRENCE, card.id, days[0]) not in {
        item.forecast_key for item in live
    }
    (own,) = [item for item in live if item.source_kind == SALES_DRAFT]
    assert (own.source_id, own.amount) == (billed.invoice.id, Decimal("1000.00"))
    assert {item.forecast_key for item in live if item.source_kind != SALES_DRAFT} == {
        forecast_key(CONTRACT_OCCURRENCE, card.id, day) for day in days[1:]
    }


def test_a_second_call_for_the_occurrence_answers_the_draft_and_makes_nothing(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session)
    card = _card(db_session, contract)
    day = _occurrences(card)[0]
    first = _bill(db_session, local_storage, contract, card, day)

    second = _bill(db_session, local_storage, contract, card, day)

    assert (second.created, second.invoice.id) == (False, first.invoice.id)
    assert db_session.scalar(select(func.count()).select_from(Invoice)) == 1
    assert db_session.scalar(select(func.count()).select_from(InvoiceLine)) == 1
    assert (
        db_session.scalar(
            select(func.count()).select_from(Activity).where(Activity.kind == "fee_billed")
        )
        == 1
    )


def test_a_draft_that_bills_the_occurrence_by_a_line_written_by_hand_is_answered_too(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """The span an occurrence bills is the forecasts' own: a period that starts inside it, however
    it was written, already bills it."""
    from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceLineIn

    _install_profiles(db_session)
    contract = _contract(db_session)
    card = _card(db_session, contract)
    day = _occurrences(card)[1]
    by_hand = InvoiceService(db_session, local_storage).create(
        InvoiceCreate(
            customer_id=contract.customer_id,
            lines=[
                InvoiceLineIn(
                    description="Canone",
                    unit_price=Decimal("1000.00"),
                    rate_card_id=card.id,
                    period_start=day + dt.timedelta(days=3),
                )
            ],
        ),
        ADMIN,
        legal_entity_id=contract.legal_entity_id,
    )

    again = _bill(db_session, local_storage, contract, card, day)

    assert (again.created, again.invoice.id) == (False, by_hand.id)


def test_issuing_the_draft_removes_that_occurrences_forecast(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """The card's own check: bill an occurrence, issue the draft, and no forecast is left for
    that occurrence, neither the contract's nor the draft's. The others stay, and the daily job
    does not bring the billed one back."""
    _install_profiles(db_session)
    contract = _contract(db_session)
    card = _card(db_session, contract)
    service = ForecastService(db_session)
    service.refresh_contract(contract.id)
    days = _occurrences(card)
    key = forecast_key(CONTRACT_OCCURRENCE, card.id, days[0])
    assert key in {item.forecast_key for item in _live(db_session)}
    billed = _bill(db_session, local_storage, contract, card, days[0])

    InvoiceService(db_session, local_storage).issue(billed.invoice.id, InvoiceIssue(), ADMIN)

    live = _live(db_session)
    assert key not in {item.forecast_key for item in live}
    assert [item for item in live if item.source_kind == SALES_DRAFT] == []
    (item,) = db_session.scalars(
        select(DueItem).where(
            DueItem.source_kind == "sales_invoice", DueItem.source_id == billed.invoice.id
        )
    ).all()
    assert (item.state, item.amount) == ("expected", Decimal("1000.00"))
    still = {forecast_key(CONTRACT_OCCURRENCE, card.id, day) for day in days[1:]}
    assert {item.forecast_key for item in live} == still
    service.refresh_all(today=TODAY)
    assert {item.forecast_key for item in _live(db_session)} == still


def test_an_issued_invoice_refuses_a_second_billing_and_a_deleted_draft_frees_the_occurrence(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session)
    card = _card(db_session, contract)
    day = _occurrences(card)[0]
    invoices = InvoiceService(db_session, local_storage)

    first = _bill(db_session, local_storage, contract, card, day)
    invoices.soft_delete(first.invoice.id, ADMIN)
    second = _bill(db_session, local_storage, contract, card, day)
    assert (second.created, second.invoice.id != first.invoice.id) == (True, True)

    issued = invoices.issue(second.invoice.id, InvoiceIssue(), ADMIN)
    with pytest.raises(Conflict) as refused:
        _bill(db_session, local_storage, contract, card, day)

    assert refused.value.details["invoice_id"] == str(issued.id)
    assert refused.value.details["number"] == issued.number
    # An annulled invoice frees its occurrence again.
    from pigrocrm.core.invoices.schemas import InvoiceAnnul

    invoices.annul(issued.id, InvoiceAnnul(reason="errore"), ADMIN)
    third = _bill(db_session, local_storage, contract, card, day)
    assert third.created is True


def test_a_one_off_fee_is_billed_once_and_a_quarterly_one_covers_its_quarter(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session)
    one_off = _card(db_session, contract, delivery_period="one_off", amount=Decimal("500.00"))
    quarterly = _card(
        db_session,
        contract,
        delivery_period="quarterly",
        valid_from=TODAY + dt.timedelta(days=1),
        amount=Decimal("3000.00"),
    )

    single = _bill(db_session, local_storage, contract, one_off, one_off.valid_from)
    quarter = _bill(db_session, local_storage, contract, quarterly, quarterly.valid_from)

    lines = {
        line.rate_card_id: line
        for line in db_session.scalars(select(InvoiceLine).order_by(InvoiceLine.created_at))
    }
    assert lines[one_off.id].description == (
        f"Canone Consulenza del {one_off.valid_from.strftime('%d/%m/%Y')}"
    )
    end = add_months(quarterly.valid_from, 3) - dt.timedelta(days=1)
    assert lines[quarterly.id].description.endswith(f"al {end.strftime('%d/%m/%Y')}")
    assert single.invoice.id != quarter.invoice.id
    assert _bill(db_session, local_storage, contract, one_off, one_off.valid_from).created is False


def test_the_period_a_fee_covers_stops_where_the_contract_ends(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    ends = TODAY + dt.timedelta(days=10)
    _install_profiles(db_session)
    contract = _contract(db_session, end=ends)
    card = _card(db_session, contract)

    _bill(db_session, local_storage, contract, card, card.valid_from)

    (line,) = db_session.scalars(select(InvoiceLine)).all()
    assert line.description.endswith(f"al {ends.strftime('%d/%m/%Y')}")


# --- what is refused ------------------------------------------------------------------------


def test_only_an_administrator_bills_and_an_agent_needs_full_access(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session)
    card = _card(db_session, contract)
    day = card.valid_from

    with pytest.raises(PermissionDenied):
        _bill(db_session, local_storage, contract, card, day, COLLABORATOR)
    with pytest.raises(AgentForbidden):
        _bill(db_session, local_storage, contract, card, day, AGENT)
    assert db_session.scalar(select(func.count()).select_from(Invoice)) == 0

    billed = _bill(db_session, local_storage, contract, card, day, FULL_ACCESS_AGENT)
    assert billed.invoice.status == "draft"


def test_a_date_that_is_not_an_occurrence_is_refused_with_the_dates_that_are(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session)
    card = _card(db_session, contract)
    days = _occurrences(card)

    with pytest.raises(ValidationFailed) as refused:
        _bill(db_session, local_storage, contract, card, days[1] + dt.timedelta(days=1))

    assert refused.value.details["field"] == "period_start"
    assert days[1].isoformat() in refused.value.details["expected"]
    with pytest.raises(ValidationFailed):
        _bill(db_session, local_storage, contract, card, card.valid_from - dt.timedelta(days=30))
    assert db_session.scalar(select(func.count()).select_from(Invoice)) == 0


def test_the_card_must_be_a_recurring_fee_of_this_contract(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session)
    other = _card(db_session, _contract(db_session))
    day_card = _card(
        db_session,
        contract,
        type="daily",
        unit="day",
        amount=Decimal("500.00"),
        delivery_period=None,
    )

    with pytest.raises(NotFound):
        _bill(db_session, local_storage, contract, other, other.valid_from)
    with pytest.raises(ValidationFailed) as refused:
        _bill(db_session, local_storage, contract, day_card, TODAY)
    assert refused.value.details["field"] == "rate_card_id"
    with pytest.raises(NotFound):
        _service(db_session, local_storage).bill(
            uuid4(), FeeBillRequest(rate_card_id=other.id, period_start=TODAY), ADMIN
        )


@pytest.mark.parametrize(
    ("overrides", "field"),
    [({"status": "draft"}, "status"), ({"currency": "USD"}, "currency")],
)
def test_a_contract_that_cannot_be_billed_says_why(
    db_session: Session,
    local_storage: LocalFileStorage,
    overrides: dict[str, object],
    field: str,
) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session, **overrides)
    card = _card(db_session, contract)

    with pytest.raises(ValidationFailed) as refused:
        _bill(db_session, local_storage, contract, card, card.valid_from)

    assert refused.value.details["field"] == field
    listed = _service(db_session, local_storage).occurrences(contract.id)
    assert listed.billable is False and listed.block_reason is not None


def test_an_expired_or_terminated_contract_still_bills_a_fee_it_earned(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session, status="terminated")
    card = _card(db_session, contract, valid_from=TODAY - dt.timedelta(days=40))

    billed = _bill(db_session, local_storage, contract, card, card.valid_from)

    assert billed.created is True


# --- the read behind the contract's page ----------------------------------------------------


def test_the_occurrences_say_which_are_to_bill_which_are_on_a_draft_and_which_are_issued(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session)
    card = _card(db_session, contract, valid_from=add_months(TODAY, -2))
    first, second, third = (add_months(card.valid_from, n) for n in range(3))
    invoices = InvoiceService(db_session, local_storage)
    issued = invoices.issue(
        _bill(db_session, local_storage, contract, card, first).invoice.id, InvoiceIssue(), ADMIN
    )
    drafted = _bill(db_session, local_storage, contract, card, second).invoice

    listed = _service(db_session, local_storage).occurrences(contract.id)

    assert listed.billable is True and listed.block_reason is None
    rows = {row.period_start: row for row in listed.occurrences}
    assert [(rows[d].state, rows[d].amount) for d in (first, second, third)] == [
        ("issued", Decimal("1000.00")),
        ("drafted", Decimal("1000.00")),
        ("to_bill", Decimal("1000.00")),
    ]
    assert rows[first].invoice is not None
    assert (rows[first].invoice.id, rows[first].invoice.number) == (issued.id, issued.number)
    assert rows[second].invoice is not None and rows[second].invoice.id == drafted.id
    assert rows[third].invoice is None
    assert rows[first].period_end == add_months(card.valid_from, 1) - dt.timedelta(days=1)
    assert [row.period_start for row in listed.occurrences] == sorted(rows)
    # By default the last three months and the forecasts' own horizon ahead.
    assert listed.date_from == TODAY - dt.timedelta(days=90)
    assert listed.date_to == TODAY + dt.timedelta(days=HORIZON_DAYS)


def test_the_window_of_the_occurrences_is_the_callers_and_is_bounded(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session)
    card = _card(db_session, contract, valid_from=add_months(TODAY, -6))
    service = _service(db_session, local_storage)

    narrow = service.occurrences(
        contract.id, date_from=card.valid_from, date_to=card.valid_from + dt.timedelta(days=40)
    )

    assert [row.period_start for row in narrow.occurrences] == [
        card.valid_from,
        add_months(card.valid_from, 1),
    ]
    with pytest.raises(ValidationFailed):
        service.occurrences(contract.id, date_from=TODAY, date_to=TODAY - dt.timedelta(days=1))
    with pytest.raises(ValidationFailed):
        service.occurrences(contract.id, date_from=TODAY, date_to=TODAY + dt.timedelta(days=801))
    with pytest.raises(NotFound):
        service.occurrences(UUID(int=0))


def test_a_contract_with_no_recurring_fee_lists_nothing(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session)

    listed = _service(db_session, local_storage).occurrences(contract.id)

    assert listed.occurrences == []


def test_a_date_at_the_end_of_the_calendar_is_refused_instead_of_walking_to_it(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session)
    card = _card(db_session, contract)
    service = _service(db_session, local_storage)

    with pytest.raises(ValidationFailed) as refused:
        _bill(db_session, local_storage, contract, card, dt.date(9999, 12, 31))
    assert refused.value.details["field"] == "period_start"
    for window in (
        {"date_from": dt.date(9999, 1, 1), "date_to": dt.date(9999, 12, 31)},
        {"date_to": dt.date.min},
        {"date_from": dt.date(9999, 12, 31)},
    ):
        with pytest.raises(ValidationFailed):
            service.occurrences(contract.id, **window)  # type: ignore[arg-type]


def test_a_lone_bound_of_the_window_gets_the_other_from_the_default_span(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session)
    _card(db_session, contract, valid_from=add_months(TODAY, -1))
    service = _service(db_session, local_storage)
    later = TODAY + dt.timedelta(days=400)

    after = service.occurrences(contract.id, date_from=later)
    before = service.occurrences(contract.id, date_to=TODAY - dt.timedelta(days=400))

    assert (after.date_to - after.date_from).days == 270
    assert (before.date_to - before.date_from).days == 270
    assert [row.period_start for row in after.occurrences if row.period_start < later] == []
