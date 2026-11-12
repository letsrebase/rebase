"""REB-1100: neither a withholding suffered nor an IVA credit is an F24 credit line.

A withholding suffered is deducted inside the income-tax return (RN33 col. 4 into RN34), so the
person's IRPEF estimate is already net of it and no credit item may count it a second time. The
IVA credit of a period is taken in by the next period's settlement (D.P.R. 100/1998 art. 1) and is
no due item either. `tax_credit` items and `TaxCredit` rows that exist stay readable, restricted
like the taxes: the F24 machinery keeps them (`test_f24_groups.py`).
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
from test_ordinary_estimate import _invoice as ordinary_invoice
from test_ordinary_estimate import _part
from test_ordinary_estimate import _person as ordinary_person

from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.due_items.models import RESTRICTED_SOURCE_KINDS, DueItem
from pigrocrm.core.due_items.schemas import DueItemCreate, DueItemListQuery
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import AgentForbidden, PermissionDenied
from pigrocrm.core.fiscal import iva_settlement, tax_items
from pigrocrm.core.fiscal.f24_models import TaxCredit
from pigrocrm.core.fiscal.iva_settlement import IvaSettlementService
from pigrocrm.core.fiscal.models import FiscalProfile, IvaSettlement
from pigrocrm.core.fiscal.schemas import IvaPeriod
from pigrocrm.core.fiscal.tax_items import TaxDueItemService
from pigrocrm.core.legal_entities.models import LegalEntity

D = Decimal
TODAY = dt.date(2026, 11, 15)


@pytest.fixture(autouse=True)
def _clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """The day the tax writer believes it is, and the IVA periods all ended."""
    monkeypatch.setattr(tax_items, "today_local", lambda: TODAY)
    monkeypatch.setattr(iva_settlement, "today_local", lambda: dt.date(2030, 1, 1))


def _credit_items(session: Session, entity: UUID) -> list[DueItem]:
    session.expire_all()
    return list(
        session.scalars(
            select(DueItem).where(
                DueItem.legal_entity_id == entity, DueItem.source_kind == "tax_credit"
            )
        )
    )


def _credit_rows(session: Session, entity: UUID) -> list[TaxCredit]:
    return list(session.scalars(select(TaxCredit).where(TaxCredit.legal_entity_id == entity)))


def _hand_built_credit(session: Session, entity: UUID) -> DueItem:
    """A credit item as REB-1076 wrote them, for the tests that read what is already stored."""
    credit = TaxCredit(
        legal_entity_id=entity,
        origin="withholding_suffered",
        tax="irpef",
        tax_year=2025,
        amount=D("200.00"),
    )
    session.add(credit)
    session.flush()
    created = DueItemService(session).create(
        DueItemCreate(
            legal_entity_id=entity,
            direction="out",
            counterparty_type="tax_office",
            tax="irpef",
            amount=D("-200.00"),
            description="Ritenute subite",
            state="expected",
            source_kind="tax_credit",
            source_id=credit.id,
            own_date=dt.date(2026, 12, 1),
        )
    )
    item = session.get(DueItem, created.id)
    assert item is not None
    return item


# --- a withholding suffered on sales ---------------------------------------------------------


def test_a_withholding_suffered_is_no_credit_item_and_the_irpef_is_net_of_it_once(
    db_session: Session,
) -> None:
    entity = ordinary_person(db_session, isa=False)
    ordinary_invoice(db_session, entity, "65789.47", dt.date(2026, 5, 20), withholding="13157.89")

    TaxDueItemService(db_session).refresh_tax_due_items(entity.id, today=TODAY)
    TaxDueItemService(db_session).refresh_tax_due_items(entity.id, today=TODAY)

    assert _credit_items(db_session, entity.id) == []
    assert _credit_rows(db_session, entity.id) == []
    person = (
        AnalyticsService(db_session)
        .get_fiscal_estimate(2026, ADMIN, legal_entity_id=entity.id)
        .person
    )
    assert person is not None
    assert _part(person.irpef, "withholdings") == D("-13157.89")
    assert person.irpef.amount == D("542.11")  # 13,700.00 gross less the withholding, once
    advances = db_session.scalars(
        select(DueItem)
        .where(
            DueItem.legal_entity_id == entity.id,
            DueItem.source_kind == "tax_advance",
            DueItem.tax == "irpef",
            DueItem.state != "dropped",
        )
        .order_by(DueItem.due_date)
    ).all()
    # The 2027 advances are 40% and 60% of the net IRPEF of 2026 (RN62 reads RN34, the difference
    # after the withholdings): the withholding is not taken off the base a second time.
    assert [item.amount for item in advances] == [D("216.84"), D("325.27")]
    assert sum(item.amount for item in advances) == person.irpef.amount


@pytest.mark.parametrize("pack", ["it-standard", "it-flat-rate", "it-company"])
def test_no_pack_writes_a_credit_item_for_a_collected_withholding(
    db_session: Session, pack: str
) -> None:
    entity = LegalEntity(name=f"credito {uuid4().hex[:6]}", legal_name="Credito", country="IT")
    db_session.add(entity)
    db_session.flush()
    db_session.add(
        FiscalProfile(
            legal_entity_id=entity.id,
            pack_id=pack,
            regime_code="RF19" if pack == "it-flat-rate" else "RF01",
            default_vat_rate=D("0.00") if pack == "it-flat-rate" else D("22.00"),
            profitability_coefficient=D("78.00") if pack == "it-flat-rate" else None,
            substitute_tax_rate=D("5.00") if pack == "it-flat-rate" else None,
        )
    )
    db_session.flush()
    ordinary_invoice(db_session, entity, "1000.00", dt.date(2026, 3, 10), withholding="200.00")

    TaxDueItemService(db_session).refresh_tax_due_items(entity.id, today=TODAY)

    assert _credit_items(db_session, entity.id) == []
    assert _credit_rows(db_session, entity.id) == []


def test_a_credit_item_already_stored_is_read_and_listed_by_an_administrator_only(
    db_session: Session,
) -> None:
    entity = ordinary_person(db_session)
    item = _hand_built_credit(db_session, entity.id)
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


def test_the_daily_refresh_leaves_a_stored_credit_item_as_it_is(db_session: Session) -> None:
    entity = ordinary_person(db_session)
    item = _hand_built_credit(db_session, entity.id)

    TaxDueItemService(db_session).refresh_tax_due_items(entity.id, today=TODAY)

    db_session.expire_all()
    kept = db_session.get(DueItem, item.id)
    assert kept is not None and (kept.state, kept.amount) == ("expected", D("-200.00"))


# --- an IVA credit carried forward -----------------------------------------------------------


def _iva_credit(session: Session) -> tuple[UUID, IvaSettlementService]:
    entity = iva_entity(session)
    _sale(session, entity, dt.date(2026, 2, 1), "100.00")
    _purchase(session, entity, dt.date(2026, 2, 5), "400.00")
    return entity, IvaSettlementService(session)


def test_an_iva_credit_is_no_due_item_and_no_credit_row(db_session: Session) -> None:
    entity, service = _iva_credit(db_session)

    read = service.settle_iva_period(IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity)
    service.settle_iva_period(IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity)

    assert read is not None and (read.outcome, read.result) == ("credit", D("-300.00"))
    assert _credit_items(db_session, entity) == []
    assert _credit_rows(db_session, entity) == []
    assert (
        db_session.scalars(
            select(DueItem).where(
                DueItem.legal_entity_id == entity, DueItem.counterparty_type == "tax_office"
            )
        ).all()
        == []
    )


def test_the_credit_of_a_period_is_netted_into_the_next_settlement(db_session: Session) -> None:
    entity, service = _iva_credit(db_session)
    service.settle_iva_period(IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity)
    _sale(db_session, entity, dt.date(2026, 5, 2), "1000.00")

    read = service.settle_iva_period(IvaPeriod(year=2026, number=2), ADMIN, legal_entity_id=entity)

    assert read is not None
    assert (read.credit_in, read.sales_vat) == (D("300.00"), D("1000.00"))
    # 1,000 owed on the sales less the 300 credit the first quarter left, plus its 1% surcharge:
    # the credit reduced the payment, it was not paid out on an F24 line of its own.
    assert (read.outcome, read.result) == ("due", D("707.00"))
    assert _credit_items(db_session, entity) == []
    (payment,) = db_session.scalars(
        select(DueItem).where(
            DueItem.legal_entity_id == entity, DueItem.source_kind == "iva_settlement"
        )
    ).all()
    assert payment.amount == D("707.00")


def test_a_credit_is_carried_through_the_periods_that_leave_one_again(db_session: Session) -> None:
    entity, service = _iva_credit(db_session)
    service.settle_iva_period(IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity)
    # The second quarter buys more than it sells: the credit grows, the first is carried in.
    _sale(db_session, entity, dt.date(2026, 5, 2), "100.00")
    _purchase(db_session, entity, dt.date(2026, 5, 3), "250.00")

    service.settle_iva_period(IvaPeriod(year=2026, number=2), ADMIN, legal_entity_id=entity)

    rows = db_session.scalars(
        select(IvaSettlement).where(IvaSettlement.legal_entity_id == entity)
    ).all()
    assert {row.period_number: (row.credit_in, row.result) for row in rows} == {
        1: (D("0.00"), D("-300.00")),
        2: (D("300.00"), D("-450.00")),
    }
    assert _credit_items(db_session, entity) == []
