"""REB-1076: tax credits as negative due items that carry their origin (taxes design § 2).

A withholding suffered on sales (what customers kept on the invoices collected in a tax year,
credited against that year's income tax, for a person on the ordinary regime) and an IVA credit
carried forward from a settled period are each a due item of source kind `tax_credit`, negative,
restricted like the taxes, with a `TaxCredit` row that says where it came from. What a payment or
an F24 group touched is left as it is.
"""

import datetime as dt
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fakes.cash_fixtures import ADMIN, AGENT, COLLABORATOR
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_iva_settlement import _entity as iva_entity
from test_iva_settlement import _purchase, _sale

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.due_items.models import RESTRICTED_SOURCE_KINDS, DueItem
from pigrocrm.core.due_items.schemas import DueItemCreate, DueItemListQuery
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import AgentForbidden, PermissionDenied
from pigrocrm.core.fiscal import iva_settlement, tax_items
from pigrocrm.core.fiscal.advances import Missing, balance_date
from pigrocrm.core.fiscal.declared import DeclaredTaxFigureService
from pigrocrm.core.fiscal.f24 import F24GroupService
from pigrocrm.core.fiscal.f24_models import TaxCredit
from pigrocrm.core.fiscal.f24_schemas import F24GroupCreate
from pigrocrm.core.fiscal.iva_settlement import IvaSettlementService
from pigrocrm.core.fiscal.models import FiscalProfile, IvaSettlement
from pigrocrm.core.fiscal.pack import IT_STANDARD_PACK
from pigrocrm.core.fiscal.schemas import DeclaredTaxFigureSet, IvaPeriod
from pigrocrm.core.fiscal.tax_items import TaxDueItemService
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.organizations.models import Organization

D = Decimal
TODAY = dt.date(2026, 11, 15)


@pytest.fixture(autouse=True)
def _clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """The day the tax writer believes it is, and the IVA periods all ended."""
    monkeypatch.setattr(tax_items, "today_local", lambda: TODAY)
    monkeypatch.setattr(iva_settlement, "today_local", lambda: dt.date(2030, 1, 1))

    def no_estimate(
        self: AnalyticsService, year: int, legal_entity: LegalEntity, profile: object
    ) -> dict[str, Decimal | None]:
        return {}

    monkeypatch.setattr(AnalyticsService, "estimated_taxes", no_estimate)


def _person(session: Session, pack_id: str = "it-standard") -> UUID:
    entity = LegalEntity(name=f"credito {uuid4().hex[:6]}", legal_name="Credito", country="IT")
    session.add(entity)
    session.flush()
    session.add(
        FiscalProfile(
            legal_entity_id=entity.id,
            pack_id=pack_id,
            regime_code="RF19" if pack_id == "it-flat-rate" else "RF01",
            default_vat_rate=D("0.00") if pack_id == "it-flat-rate" else D("22.00"),
            profitability_coefficient=D("78.00") if pack_id == "it-flat-rate" else None,
            substitute_tax_rate=D("5.00") if pack_id == "it-flat-rate" else None,
        )
    )
    session.flush()
    return entity.id


def _collected(
    session: Session,
    entity: UUID,
    paid_on: dt.date,
    *,
    withholding: str = "200.00",
    taxable: str = "1000.00",
) -> Invoice:
    """An issued invoice with a withholding, collected in full on `paid_on`: the customer pays the
    gross less the withholding."""
    customer = Organization(legal_name="Studio", country="IT", roles=["customer"])
    session.add(customer)
    session.flush()
    gross = D(taxable) * D("1.22")
    invoice = Invoice(
        customer_id=customer.id,
        legal_entity_id=entity,
        type="invoice",
        status="issued",
        year=paid_on.year,
        number=uuid4().int % 10**6,
        issue_date=paid_on - dt.timedelta(days=20),
        due_date=paid_on,
        taxable_amount=D(taxable),
        vat_amount=gross - D(taxable),
        total=gross,
        withholding_amount=D(withholding),
        net_receivable=gross - D(withholding),
        payment_status="unpaid",
    )
    session.add(invoice)
    session.flush()
    DueItemService(session).record_invoice_payment(
        invoice, paid=True, paid_on=paid_on, actor=Actor.system()
    )
    return invoice


def _credits(session: Session, entity: UUID, tax: str = "irpef") -> list[DueItem]:
    session.expire_all()
    return list(
        session.scalars(
            select(DueItem)
            .where(
                DueItem.legal_entity_id == entity,
                DueItem.source_kind == "tax_credit",
                DueItem.tax == tax,
                DueItem.state != "dropped",
            )
            .order_by(DueItem.due_date)
        )
    )


def _refresh(session: Session, entity: UUID):  # noqa: ANN202
    return TaxDueItemService(session).refresh_tax_due_items(entity, today=TODAY)


def _balance_date_2026() -> dt.date:
    due = balance_date(IT_STANDARD_PACK, IT_STANDARD_PACK.law_for(2026), "irpef", 2026, 12, 31)
    assert not isinstance(due, Missing)
    return due


# --- a withholding suffered on sales ---------------------------------------------------------


def test_a_withholding_suffered_is_a_negative_item_on_the_balance_date_of_its_tax_year(
    db_session: Session,
) -> None:
    entity = _person(db_session)
    _collected(db_session, entity, dt.date(2026, 3, 10))
    _collected(db_session, entity, dt.date(2026, 6, 1), withholding="100.00")

    report = _refresh(db_session, entity)

    (item,) = _credits(db_session, entity)
    assert (item.direction, item.state, item.counterparty_type) == ("out", "expected", "tax_office")
    assert (item.amount, item.due_date) == (D("-300.00"), _balance_date_2026())
    assert report.written >= 1
    credit = db_session.scalars(select(TaxCredit).where(TaxCredit.id == item.source_id)).one()
    assert (credit.origin, credit.tax, credit.tax_year, credit.amount) == (
        "withholding_suffered",
        "irpef",
        2026,
        D("300.00"),
    )
    # A second pass changes nothing: the same item, the same row.
    _refresh(db_session, entity)
    (again,) = _credits(db_session, entity)
    assert again.id == item.id
    assert db_session.scalars(
        select(TaxCredit).where(TaxCredit.legal_entity_id == entity)
    ).all() == [credit]


def test_a_later_collection_restates_the_credit_while_nothing_touched_it(
    db_session: Session,
) -> None:
    entity = _person(db_session)
    _collected(db_session, entity, dt.date(2026, 3, 10))
    _refresh(db_session, entity)
    (first,) = _credits(db_session, entity)

    _collected(db_session, entity, dt.date(2026, 10, 2), withholding="50.00")
    _refresh(db_session, entity)

    (second,) = _credits(db_session, entity)
    assert (second.id, second.amount) == (first.id, D("-250.00"))


def test_a_collection_counts_in_the_year_it_was_collected_not_the_year_it_was_issued(
    db_session: Session,
) -> None:
    entity = _person(db_session)
    invoice = _collected(db_session, entity, dt.date(2026, 2, 3))
    invoice.issue_date = dt.date(2025, 12, 20)
    db_session.flush()

    _refresh(db_session, entity)

    (item,) = _credits(db_session, entity)
    assert item.amount == D("-200.00") and item.due_date == _balance_date_2026()


def test_a_year_whose_balance_date_has_passed_is_not_invented(db_session: Session) -> None:
    entity = _person(db_session)
    _collected(db_session, entity, dt.date(2025, 4, 1))

    _refresh(db_session, entity)

    assert _credits(db_session, entity) == []


def test_a_declared_figure_nets_the_withholdings_and_the_credit_goes(db_session: Session) -> None:
    entity = _person(db_session)
    _collected(db_session, entity, dt.date(2026, 3, 10))
    _refresh(db_session, entity)
    assert len(_credits(db_session, entity)) == 1

    DeclaredTaxFigureService(db_session).set_declared_tax_figure(
        DeclaredTaxFigureSet(tax="irpef", year=2026, tax_due=D("5000.00")),
        ADMIN,
        legal_entity_id=entity,
    )

    assert _credits(db_session, entity) == []


def test_only_a_pack_with_a_credit_law_has_the_credit(db_session: Session) -> None:
    flat_rate = _person(db_session, "it-flat-rate")
    company = _person(db_session, "it-company")
    for entity in (flat_rate, company):
        _collected(db_session, entity, dt.date(2026, 3, 10))
        _refresh(db_session, entity)
        assert _credits(db_session, entity) == []


def test_a_credit_in_an_f24_is_left_as_it_is_by_the_next_collection(db_session: Session) -> None:
    entity = _person(db_session)
    _collected(db_session, entity, dt.date(2026, 3, 10))
    _refresh(db_session, entity)
    (credit,) = _credits(db_session, entity)
    other = DueItemService(db_session).create(
        DueItemCreate(
            legal_entity_id=entity,
            direction="out",
            counterparty_type="tax_office",
            tax="irpef",
            amount=D("900.00"),
            source_kind="tax_balance",
            source_id=uuid4(),
            own_date=credit.due_date,
        )
    )
    F24GroupService(db_session).group_f24(F24GroupCreate(due_item_ids=[credit.id, other.id]), ADMIN)

    _collected(db_session, entity, dt.date(2026, 10, 2), withholding="50.00")
    _refresh(db_session, entity)

    (kept,) = _credits(db_session, entity)
    assert (kept.id, kept.amount) == (credit.id, D("-200.00"))


def test_a_credit_is_read_and_listed_by_an_administrator_only(db_session: Session) -> None:
    entity = _person(db_session)
    _collected(db_session, entity, dt.date(2026, 3, 10))
    _refresh(db_session, entity)
    (item,) = _credits(db_session, entity)
    service = DueItemService(db_session)

    assert "tax_credit" in RESTRICTED_SOURCE_KINDS
    assert [
        i.id for i in service.list(DueItemListQuery(source_kind="tax_credit"), ADMIN).items
    ] == [item.id]
    # The ordinary listing leaves it out of a reader that may not see the fiscal kinds.
    assert item.id not in [i.id for i in service.list(DueItemListQuery(), COLLABORATOR).items]
    with pytest.raises(PermissionDenied):
        service.list(DueItemListQuery(source_kind="tax_credit"), COLLABORATOR)
    with pytest.raises(AgentForbidden):
        service.get(item.id, AGENT)


# --- an IVA credit carried forward -----------------------------------------------------------


def _iva_credit(session: Session) -> tuple[UUID, IvaSettlementService]:
    entity = iva_entity(session)
    _sale(session, entity, dt.date(2026, 2, 1), "100.00")
    _purchase(session, entity, dt.date(2026, 2, 5), "400.00")
    return entity, IvaSettlementService(session)


def test_an_iva_credit_carried_forward_is_a_negative_item_that_names_its_period(
    db_session: Session,
) -> None:
    entity, service = _iva_credit(db_session)

    read = service.settle_iva_period(IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity)

    assert read is not None and read.outcome == "credit"
    (item,) = _credits(db_session, entity, "iva")
    assert (item.amount, item.due_date, item.state) == (
        D("-300.00"),
        dt.date(2026, 5, 18),
        "expected",
    )
    credit = db_session.scalars(select(TaxCredit).where(TaxCredit.id == item.source_id)).one()
    assert (credit.origin, credit.tax_year, credit.amount) == (
        "iva_carry_forward",
        2026,
        D("300.00"),
    )
    assert credit.iva_settlement_id == read.id
    # Settling it again changes nothing.
    service.settle_iva_period(IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity)
    assert [i.id for i in _credits(db_session, entity, "iva")] == [item.id]


def test_the_next_period_takes_the_credit_in_and_only_the_latest_credit_is_outstanding(
    db_session: Session,
) -> None:
    entity, service = _iva_credit(db_session)
    service.settle_iva_period(IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity)
    (first,) = _credits(db_session, entity, "iva")
    # The second quarter buys more than it sells: the credit grows, and the first is carried in.
    _sale(db_session, entity, dt.date(2026, 5, 2), "100.00")
    _purchase(db_session, entity, dt.date(2026, 5, 3), "250.00")

    service.settle_iva_period(IvaPeriod(year=2026, number=2), ADMIN, legal_entity_id=entity)

    (latest,) = _credits(db_session, entity, "iva")
    assert latest.id != first.id and latest.amount == D("-450.00")
    db_session.expire_all()
    assert db_session.get(DueItem, first.id).state == "dropped"  # type: ignore[union-attr]
    rows = db_session.scalars(select(IvaSettlement).where(IvaSettlement.legal_entity_id == entity))
    assert {row.period_number: row.outcome for row in rows} == {1: "credit", 2: "credit"}


def test_a_period_that_uses_the_credit_leaves_no_credit_item(db_session: Session) -> None:
    entity, service = _iva_credit(db_session)
    service.settle_iva_period(IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity)
    _sale(db_session, entity, dt.date(2026, 5, 2), "1000.00")

    read = service.settle_iva_period(IvaPeriod(year=2026, number=2), ADMIN, legal_entity_id=entity)

    assert read is not None and read.outcome == "due"
    assert _credits(db_session, entity, "iva") == []


def test_an_iva_credit_in_an_f24_survives_the_period_that_takes_it_in(db_session: Session) -> None:
    entity, service = _iva_credit(db_session)
    service.settle_iva_period(IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity)
    (credit,) = _credits(db_session, entity, "iva")
    owed = DueItemService(db_session).create(
        DueItemCreate(
            legal_entity_id=entity,
            direction="out",
            counterparty_type="tax_office",
            tax="irpef",
            amount=D("900.00"),
            source_kind="tax_balance",
            source_id=uuid4(),
            own_date=credit.due_date,
        )
    )
    F24GroupService(db_session).group_f24(F24GroupCreate(due_item_ids=[credit.id, owed.id]), ADMIN)
    _sale(db_session, entity, dt.date(2026, 5, 2), "1000.00")

    service.settle_iva_period(IvaPeriod(year=2026, number=2), ADMIN, legal_entity_id=entity)

    db_session.expire_all()
    kept = db_session.get(DueItem, credit.id)
    assert kept is not None and (kept.state, kept.amount) == ("expected", D("-300.00"))
