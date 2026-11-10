"""The periodic IVA settlement and its due item (REB-1021, suppliers design § 6).

The arithmetic is checked on `compute_figures` with the pack's own law, and the registers, the
carry-over, the due item and the refusals through `IvaSettlementService` on real rows.
"""

import datetime as dt
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.due_items.schemas import SettlementCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import AgentForbidden, Conflict, PermissionDenied, ValidationFailed
from pigrocrm.core.fiscal import iva_settlement
from pigrocrm.core.fiscal.iva_settlement import (
    IvaSettlementService,
    compute_figures,
    payment_date,
    period_bounds,
)
from pigrocrm.core.fiscal.models import FiscalProfile, IvaSettlement
from pigrocrm.core.fiscal.pack import IT_FLAT_RATE_PACK, IT_STANDARD_PACK
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert, IvaPeriod
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.purchases.models import Purchase, PurchaseVatLine
from pigrocrm.core.timetracking.models import CostCategory

ADMIN = Actor(id=uuid4(), type="user", role="admin")
COLLABORATOR = Actor(id=uuid4(), type="user", role="collaborator")
AGENT_ADMIN = Actor(id=uuid4(), type="mcp", role="admin")
AGENT_ADMIN_FULL = Actor(id=uuid4(), type="mcp", role="admin", full_access=True)
D = dt.date
LAW = IT_STANDARD_PACK.law_for(2026).iva  # type: ignore[union-attr]
assert LAW is not None
M = Decimal


@pytest.fixture(autouse=True)
def _years_later(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every period below has ended: the service refuses one that has not."""
    monkeypatch.setattr(iva_settlement, "today_local", lambda: D(2030, 1, 1))


def _figures(
    frequency: str,
    number: int,
    *,
    sales: str = "0",
    reverse: str = "0",
    purchases: str = "0",
    credit: str = "0",
    debit: str = "0",
):  # noqa: ANN202
    return compute_figures(
        LAW,
        frequency=frequency,
        number=number,
        sales_vat=M(sales),
        reverse_charge_vat=M(reverse),
        purchase_vat=M(purchases),
        credit_in=M(credit),
        debit_in=M(debit),
    )


# --- the arithmetic ---------------------------------------------------------------------


def test_a_quarter_with_a_credit_carried_in_and_the_one_percent_is_the_right_figure() -> None:
    figures = _figures("quarterly", 2, sales="1000", purchases="300", credit="100")
    assert (figures.surcharge, figures.result, figures.outcome) == (M("6.00"), M("606.00"), "due")


def test_the_one_percent_is_added_to_a_quarterly_debit_only() -> None:
    assert _figures("monthly", 2, sales="1000").surcharge == M("0.00")
    assert _figures("quarterly", 2, sales="1000").surcharge == M("10.00")
    # No surcharge on a credit, and none on the fourth quarter (A5, to verify).
    assert _figures("quarterly", 2, purchases="1000").surcharge == M("0.00")
    assert _figures("quarterly", 4, sales="1000").surcharge == M("0.00")


def test_a_credit_is_carried_forward_and_a_zero_is_nil() -> None:
    credit = _figures("monthly", 3, sales="100", purchases="350")
    assert (credit.result, credit.outcome) == (M("-250.00"), "credit")
    assert _figures("monthly", 3, sales="100", purchases="100").outcome == "nil"


def test_a_debit_not_exceeding_the_limit_waits_and_one_above_it_is_paid() -> None:
    assert _figures("monthly", 1, sales="100").outcome == "carried"  # exactly the limit waits
    assert _figures("monthly", 1, sales="100.01").outcome == "due"
    assert _figures("monthly", 10, sales="12").outcome == "carried"


def test_november_and_december_are_paid_whatever_they_are() -> None:
    assert _figures("monthly", 11, sales="12").outcome == "due"
    assert _figures("monthly", 12, sales="12").outcome == "due"


def test_the_first_two_quarters_may_wait_and_the_third_and_fourth_may_not() -> None:
    # 50 plus its 1% is still under the limit
    assert _figures("quarterly", 1, sales="50").outcome == "carried"
    assert _figures("quarterly", 2, sales="50").outcome == "carried"
    assert _figures("quarterly", 3, sales="50").outcome == "due"
    assert _figures("quarterly", 4, sales="50").outcome == "due"


def test_the_limit_is_compared_with_the_interest_included() -> None:
    # 99.50 plus 0.995 rounded to 1.00 is 100.50: above the limit
    assert _figures("quarterly", 1, sales="99.50").outcome == "due"


def test_the_debit_that_waited_is_added_to_the_next_period_without_a_second_interest() -> None:
    figures = _figures("quarterly", 2, sales="500", debit="50.50")
    assert (figures.surcharge, figures.result) == (M("5.00"), M("555.50"))


def test_reverse_charge_vat_is_owed_and_its_deductible_share_comes_off() -> None:
    figures = _figures("monthly", 5, reverse="220", purchases="88", sales="0")
    assert figures.result == M("132.00")


# --- the calendar -----------------------------------------------------------------------


def test_each_period_is_paid_on_the_date_of_the_pack_calendar() -> None:
    pack = IT_STANDARD_PACK
    assert payment_date(pack, "quarterly", 2026, 1).nominal_date == D(2026, 5, 16)  # type: ignore[union-attr]
    assert payment_date(pack, "quarterly", 2026, 1).due_date == D(2026, 5, 18)  # type: ignore[union-attr]
    assert payment_date(pack, "quarterly", 2026, 2).nominal_date == D(2026, 8, 20)  # type: ignore[union-attr]
    assert payment_date(pack, "quarterly", 2026, 3).nominal_date == D(2026, 11, 16)  # type: ignore[union-attr]
    assert payment_date(pack, "quarterly", 2026, 4).nominal_date == D(2027, 3, 16)  # type: ignore[union-attr]
    assert payment_date(pack, "monthly", 2026, 7).nominal_date == D(2026, 8, 20)  # type: ignore[union-attr]
    assert payment_date(pack, "monthly", 2026, 11).nominal_date == D(2026, 12, 16)  # type: ignore[union-attr]
    assert payment_date(pack, "monthly", 2026, 12).nominal_date == D(2027, 1, 16)  # type: ignore[union-attr]
    assert payment_date(IT_FLAT_RATE_PACK, "monthly", 2026, 3) is None


def test_period_bounds() -> None:
    assert period_bounds("monthly", 2028, 2).end == D(2028, 2, 29)
    quarter = period_bounds("quarterly", 2026, 3)
    assert (quarter.start, quarter.end) == (D(2026, 7, 1), D(2026, 9, 30))


# --- the service ------------------------------------------------------------------------


def _entity(session: Session, *, pack: str = "it-standard", frequency: str = "quarterly") -> UUID:
    entity = LegalEntity(name=f"iva {uuid4().hex[:6]}", legal_name="Iva Srl", country="IT")
    session.add(entity)
    session.flush()
    session.add(
        FiscalProfile(
            legal_entity_id=entity.id,
            pack_id=pack,
            regime_code="RF01" if pack == "it-standard" else "RF19",
            default_vat_rate=M("22.00") if pack == "it-standard" else M("0.00"),
            iva_frequency=frequency,
        )
    )
    session.flush()
    return entity.id


def _sale(
    session: Session,
    entity: UUID,
    day: dt.date,
    vat: str,
    *,
    status: str = "issued",
    split: bool = False,
) -> None:
    customer = Organization(legal_name="Acme", country="IT", roles=["customer"])
    session.add(customer)
    session.flush()
    session.add(
        Invoice(
            customer_id=customer.id,
            legal_entity_id=entity,
            type="invoice",
            status=status,
            year=day.year,
            number=uuid4().int % 10**6,
            issue_date=day,
            taxable_amount=M(vat) * 5,
            vat_amount=M(vat),
            total=M(vat) * 6,
            split_payment=split,
        )
    )
    session.flush()


def _purchase(
    session: Session,
    entity: UUID,
    period: dt.date,
    vat: str,
    *,
    share: str = "100",
    reverse_charge: bool = False,
    status: str = "registered",
    kind: str = "invoice",
) -> None:
    category = session.scalars(select(CostCategory)).first()
    if category is None:
        category = CostCategory(name="Servizi")
        session.add(category)
        session.flush()
    supplier = Organization(legal_name="Fornitore", country="IT", roles=["supplier"])
    session.add(supplier)
    session.flush()
    sign = -1 if kind == "credit_note" else 1
    purchase = Purchase(
        legal_entity_id=entity,
        category_id=category.id,
        supplier_id=supplier.id,
        kind=kind,
        status=status,
        date=period,
        iva_period=period,
        amount=M(vat) * 6 * sign,
        description="Acquisto",
        reverse_charge=reverse_charge,
        custom_fields={},
    )
    session.add(purchase)
    session.flush()
    session.add(
        PurchaseVatLine(
            purchase_id=purchase.id,
            position=1,
            rate=M("22.00"),
            taxable_amount=M(vat) * 5 * sign,
            vat_amount=M(vat) * sign,
            deductible_share=M(share),
        )
    )
    session.flush()


def _items(session: Session, entity: UUID) -> list[DueItem]:
    return list(
        session.scalars(
            select(DueItem).where(
                DueItem.legal_entity_id == entity, DueItem.source_kind == "iva_settlement"
            )
        )
    )


def test_a_quarter_settles_the_registers_and_writes_one_due_item_to_the_tax_office(
    db_session: Session,
) -> None:
    entity = _entity(db_session)
    _sale(db_session, entity, D(2026, 1, 20), "220.00")
    _sale(db_session, entity, D(2026, 3, 31), "110.00")
    _sale(db_session, entity, D(2026, 2, 2), "999.00", split=True)  # the buyer pays it
    _sale(db_session, entity, D(2026, 2, 3), "888.00", status="cancelled")
    _sale(db_session, entity, D(2026, 4, 1), "777.00")  # the next quarter
    _purchase(db_session, entity, D(2026, 2, 10), "100.00")
    _purchase(db_session, entity, D(2026, 2, 11), "50.00", share="40")  # 20.00 deductible
    _purchase(db_session, entity, D(2026, 2, 12), "70.00", status="cancelled")
    _purchase(db_session, entity, D(2026, 4, 2), "60.00")
    _purchase(db_session, entity, D(2026, 3, 1), "10.00", kind="credit_note")

    read = IvaSettlementService(db_session).settle_iva_period(
        IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity
    )

    assert read is not None
    assert (read.sales_vat, read.purchase_vat, read.reverse_charge_vat) == (
        M("330.00"),
        M("110.00"),
        M("0.00"),
    )
    # 330 - 110 = 220, plus 1% = 2.20
    assert (read.surcharge, read.result, read.outcome) == (M("2.20"), M("222.20"), "due")
    assert read.payment_due_on == D(2026, 5, 18)  # 16 May is a Saturday
    (item,) = _items(db_session, entity)
    assert read.due_item_id == item.id
    assert (item.direction, item.state, item.counterparty_type, item.tax) == (
        "out",
        "expected",
        "tax_office",
        "iva",
    )
    assert (item.amount, item.due_date, item.organization_id) == (
        M("222.20"),
        D(2026, 5, 18),
        None,
    )


def test_a_credit_is_carried_into_the_next_quarter_and_writes_no_item(db_session: Session) -> None:
    entity = _entity(db_session)
    _sale(db_session, entity, D(2026, 2, 1), "100.00")
    _purchase(db_session, entity, D(2026, 2, 5), "400.00")
    _sale(db_session, entity, D(2026, 5, 2), "1000.00")
    _purchase(db_session, entity, D(2026, 5, 3), "300.00")
    service = IvaSettlementService(db_session)

    first = service.settle_iva_period(IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity)
    assert first is not None
    assert (first.result, first.outcome, first.due_item_id) == (M("-300.00"), "credit", None)

    second = service.settle_iva_period(
        IvaPeriod(year=2026, number=2), ADMIN, legal_entity_id=entity
    )
    assert second is not None
    # 1000 - 300 - 300 credit = 400, plus 4.00 interest
    assert (second.credit_in, second.surcharge, second.result) == (
        M("300.00"),
        M("4.00"),
        M("404.00"),
    )
    (item,) = _items(db_session, entity)
    assert (item.amount, item.due_date) == (M("404.00"), D(2026, 8, 20))


def test_settling_a_later_period_settles_the_earlier_ones_of_the_year_first(
    db_session: Session,
) -> None:
    entity = _entity(db_session, frequency="monthly")
    _sale(db_session, entity, D(2026, 1, 15), "10.00")
    _sale(db_session, entity, D(2026, 3, 15), "500.00")

    read = IvaSettlementService(db_session).settle_iva_period(
        IvaPeriod(year=2026, number=3), ADMIN, legal_entity_id=entity
    )

    assert read is not None
    # January's 10 waits, February has nothing of its own and passes it on, March pays 500 + 10
    assert (read.debit_in, read.result) == (M("10.00"), M("510.00"))
    rows = db_session.scalars(
        select(IvaSettlement)
        .where(IvaSettlement.legal_entity_id == entity)
        .order_by(IvaSettlement.period_number)
    ).all()
    assert [(row.period_number, row.outcome) for row in rows] == [
        (1, "carried"),
        (2, "carried"),
        (3, "due"),
    ]
    assert [item.amount for item in _items(db_session, entity)] == [M("510.00")]


def test_reverse_charge_vat_is_owed_and_deducted_in_the_same_period(db_session: Session) -> None:
    entity = _entity(db_session)
    _purchase(db_session, entity, D(2026, 2, 5), "220.00", reverse_charge=True, share="40")

    read = IvaSettlementService(db_session).settle_iva_period(
        IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity
    )

    assert read is not None
    assert (read.reverse_charge_vat, read.purchase_vat) == (M("220.00"), M("88.00"))
    assert read.result == M("133.32")  # 132.00 and its 1%


def test_a_flat_rate_entity_gets_no_settlement_and_no_item(db_session: Session) -> None:
    entity = _entity(db_session, pack="it-flat-rate", frequency="monthly")
    _purchase(db_session, entity, D(2026, 2, 5), "220.00", reverse_charge=True)

    read = IvaSettlementService(db_session).settle_iva_period(
        IvaPeriod(year=2026, number=3), ADMIN, legal_entity_id=entity
    )

    assert read is None
    assert _items(db_session, entity) == []
    assert db_session.scalars(select(IvaSettlement)).all() == []


def test_a_year_the_pack_has_no_law_for_gets_no_settlement(db_session: Session) -> None:
    entity = _entity(db_session)
    assert (
        IvaSettlementService(db_session).settle_iva_period(
            IvaPeriod(year=2025, number=1), ADMIN, legal_entity_id=entity
        )
        is None
    )


def test_settling_again_follows_the_registers_while_nothing_was_paid(db_session: Session) -> None:
    entity = _entity(db_session)
    _sale(db_session, entity, D(2026, 2, 1), "1000.00")
    service = IvaSettlementService(db_session)
    first = service.settle_iva_period(IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity)
    assert first is not None and first.result == M("1010.00")

    _purchase(db_session, entity, D(2026, 2, 20), "400.00")
    again = service.settle_iva_period(IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity)

    assert again is not None and again.result == M("606.00")
    assert again.id == first.id
    (item,) = _items(db_session, entity)
    assert (item.amount, item.state) == (M("606.00"), "expected")


def test_an_item_that_stops_being_due_is_dropped_and_comes_back_when_due_again(
    db_session: Session,
) -> None:
    entity = _entity(db_session)
    _sale(db_session, entity, D(2026, 2, 1), "1000.00")
    service = IvaSettlementService(db_session)
    service.settle_iva_period(IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity)
    _purchase(db_session, entity, D(2026, 2, 20), "2000.00")

    credit = service.settle_iva_period(
        IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity
    )
    assert credit is not None and (credit.outcome, credit.due_item_id) == ("credit", None)
    (item,) = _items(db_session, entity)
    assert item.state == "dropped"

    db_session.query(Purchase).delete()
    due = service.settle_iva_period(IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity)
    assert due is not None and due.due_item_id == item.id
    db_session.refresh(item)
    assert (item.state, item.amount, item.dropped_reason) == ("expected", M("1010.00"), None)


def test_a_payment_freezes_the_item_and_a_changing_recomputation_is_refused(
    db_session: Session,
) -> None:
    entity = _entity(db_session)
    _sale(db_session, entity, D(2026, 2, 1), "1000.00")
    service = IvaSettlementService(db_session)
    first = service.settle_iva_period(IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity)
    assert first is not None and first.due_item_id is not None
    DueItemService(db_session).settle(
        first.due_item_id,
        SettlementCreate(amount=M("100.00"), settled_on=D(2026, 5, 18)),
        ADMIN,
    )

    # Unchanged figures: nothing to refuse.
    same = service.settle_iva_period(IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity)
    assert same is not None and same.result == M("1010.00")

    _purchase(db_session, entity, D(2026, 2, 20), "400.00")
    with pytest.raises(Conflict):
        service.settle_iva_period(IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity)


def test_a_credit_is_carried_over_the_year_end_from_a_recomputed_year(
    db_session: Session,
) -> None:
    entity = _entity(db_session)
    _sale(db_session, entity, D(2026, 2, 1), "10.00")
    service = IvaSettlementService(db_session)
    service.settle_iva_period(IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity)
    # Registered after 2026 was last settled, and only the fourth quarter has it.
    _purchase(db_session, entity, D(2026, 11, 5), "500.00")
    _sale(db_session, entity, D(2027, 2, 1), "800.00")

    read = service.settle_iva_period(IvaPeriod(year=2027, number=1), ADMIN, legal_entity_id=entity)

    assert read is not None
    assert (read.credit_in, read.result) == (M("500.00"), M("303.00"))  # 300 and its 1%
    fourth = db_session.scalars(
        select(IvaSettlement).where(
            IvaSettlement.legal_entity_id == entity,
            IvaSettlement.year == 2026,
            IvaSettlement.period_number == 4,
        )
    ).one()
    assert (fourth.outcome, fourth.result) == ("credit", M("-500.00"))


def test_a_new_frequency_starts_the_year_after_the_last_one_settled(db_session: Session) -> None:
    entity = _entity(db_session)
    service = IvaSettlementService(db_session)
    service.settle_iva_period(IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity)
    db_session.execute(
        update(FiscalProfile)
        .where(FiscalProfile.legal_entity_id == entity)
        .values(iva_frequency="monthly")
    )
    with pytest.raises(ValidationFailed):
        service.settle_iva_period(IvaPeriod(year=2026, number=4), ADMIN, legal_entity_id=entity)

    read = service.settle_iva_period(IvaPeriod(year=2027, number=2), ADMIN, legal_entity_id=entity)
    assert read is not None and (read.frequency, read.period_number) == ("monthly", 2)
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(IvaSettlement)
            .where(IvaSettlement.legal_entity_id == entity, IvaSettlement.year == 2026)
        )
        == 4
    )


def test_a_period_that_has_not_ended_is_refused(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    entity = _entity(db_session)
    monkeypatch.setattr(iva_settlement, "today_local", lambda: D(2026, 3, 31))
    with pytest.raises(ValidationFailed):
        IvaSettlementService(db_session).settle_iva_period(
            IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity
        )
    monkeypatch.setattr(iva_settlement, "today_local", lambda: D(2026, 4, 1))
    assert (
        IvaSettlementService(db_session).settle_iva_period(
            IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity
        )
        is not None
    )


def test_a_quarterly_entity_has_no_fifth_period(db_session: Session) -> None:
    entity = _entity(db_session)
    with pytest.raises(ValidationFailed):
        IvaSettlementService(db_session).settle_iva_period(
            IvaPeriod(year=2026, number=5), ADMIN, legal_entity_id=entity
        )


def test_only_an_administrator_settles_and_an_agent_needs_full_access(db_session: Session) -> None:
    entity = _entity(db_session)
    service = IvaSettlementService(db_session)
    period = IvaPeriod(year=2026, number=1)
    with pytest.raises(PermissionDenied):
        service.settle_iva_period(period, COLLABORATOR, legal_entity_id=entity)
    with pytest.raises(AgentForbidden):
        service.settle_iva_period(period, AGENT_ADMIN, legal_entity_id=entity)
    assert service.settle_iva_period(period, AGENT_ADMIN_FULL, legal_entity_id=entity) is not None


# --- the setting ------------------------------------------------------------------------


def _upsert(**fields: object) -> FiscalProfileUpsert:
    return FiscalProfileUpsert(pack_id="it-standard", **fields)  # type: ignore[arg-type]


def test_a_save_that_leaves_the_frequency_out_keeps_it() -> None:
    assert "iva_frequency" not in _upsert().stored_fields()
    assert _upsert(iva_frequency="quarterly").stored_fields()["iva_frequency"] == "quarterly"


def test_only_a_pack_with_a_periodic_iva_settles_quarterly() -> None:
    FiscalProfileService.check(_upsert(iva_frequency="quarterly").stored_fields())
    flat = FiscalProfileUpsert(pack_id="it-flat-rate", iva_frequency="quarterly")
    with pytest.raises(ValidationFailed):
        FiscalProfileService.check(flat.stored_fields())


# --- the daily pass -----------------------------------------------------------------------


def _rows(session: Session, entity: UUID) -> list[tuple[int, int]]:
    return [
        (row.year, row.period_number)
        for row in session.scalars(
            select(IvaSettlement)
            .where(IvaSettlement.legal_entity_id == entity)
            .order_by(IvaSettlement.period_start)
        )
    ]


def test_the_daily_pass_settles_the_elapsed_periods_and_a_second_pass_changes_nothing(
    db_session: Session,
) -> None:
    from pigrocrm.core.automations.daily import AutomationPass

    entity = _entity(db_session)
    flat = _entity(db_session, pack="it-flat-rate", frequency="monthly")
    _sale(db_session, entity, D(2026, 2, 1), "1000.00")

    report = AutomationPass(db_session).run(today=D(2026, 5, 10))
    assert (report.iva_failed, report.iva_frozen) == (0, ())
    assert report.iva_settled >= 1
    assert _rows(db_session, entity) == [(2026, 1)]
    (item,) = _items(db_session, entity)
    assert (item.amount, item.due_date) == (M("1010.00"), D(2026, 5, 18))
    assert _rows(db_session, flat) == []

    again = AutomationPass(db_session).run(today=D(2026, 5, 10))
    assert (again.iva_failed, again.iva_frozen) == (0, ())
    assert _rows(db_session, entity) == [(2026, 1)]
    assert [(i.id, i.amount) for i in _items(db_session, entity)] == [(item.id, M("1010.00"))]


def test_the_window_keeps_the_previous_year_until_the_annual_return_date(
    db_session: Session,
) -> None:
    from pigrocrm.core.automations.daily import AutomationPass

    entity = _entity(db_session)
    _sale(db_session, entity, D(2026, 11, 20), "300.00")
    # 10 February 2027: no quarter of 2027 has ended, the fourth of 2026 is still in the window.
    AutomationPass(db_session).run(today=D(2027, 2, 10))
    assert _rows(db_session, entity) == [(2026, q) for q in (1, 2, 3, 4)]
    # After 16 March 2027 the first quarter of 2027 settles and the chain still carries 2026.
    AutomationPass(db_session).run(today=D(2027, 4, 20))
    assert _rows(db_session, entity)[-1] == (2027, 1)


def test_a_period_whose_item_was_paid_is_reported_and_the_pass_goes_on(
    db_session: Session,
) -> None:
    from pigrocrm.core.automations.daily import AutomationPass

    entity = _entity(db_session)
    _sale(db_session, entity, D(2026, 2, 1), "1000.00")
    AutomationPass(db_session).run(today=D(2026, 5, 10))
    (item,) = _items(db_session, entity)
    DueItemService(db_session).settle(
        item.id, SettlementCreate(amount=M("100.00"), settled_on=D(2026, 5, 18)), ADMIN
    )
    _purchase(db_session, entity, D(2026, 2, 20), "400.00")

    report = AutomationPass(db_session).run(today=D(2026, 5, 10))

    assert report.iva_frozen == ("1° trimestre 2026",)
    assert report.iva_failed == 0
    db_session.refresh(item)
    assert item.amount == M("1010.00")
    row = db_session.scalars(
        select(IvaSettlement).where(IvaSettlement.legal_entity_id == entity)
    ).one()
    assert row.result == M("1010.00")


def test_an_entity_that_fails_is_counted_by_class_name_and_the_others_settle(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pigrocrm.core.automations.daily import AutomationPass

    broken = _entity(db_session)
    fine = _entity(db_session)
    _sale(db_session, fine, D(2026, 2, 1), "1000.00")
    real = IvaSettlementService.settle_elapsed_periods

    def flaky(self: IvaSettlementService, legal_entity_id: UUID, today: dt.date):  # noqa: ANN202
        if legal_entity_id == broken:
            raise RuntimeError("boom")
        return real(self, legal_entity_id, today)

    monkeypatch.setattr(IvaSettlementService, "settle_elapsed_periods", flaky)
    report = AutomationPass(db_session).run(today=D(2026, 5, 10))

    assert report.iva_failed == 1 and report.iva_failures == ("RuntimeError",)
    assert _rows(db_session, fine) == [(2026, 1)]
    assert _rows(db_session, broken) == []


def test_the_read_lists_a_years_settlements_for_an_administrator_only(
    db_session: Session,
) -> None:
    entity = _entity(db_session)
    _sale(db_session, entity, D(2026, 2, 1), "1000.00")
    service = IvaSettlementService(db_session)
    service.settle_iva_period(IvaPeriod(year=2026, number=2), ADMIN, legal_entity_id=entity)

    rows = service.list_iva_settlements(2026, ADMIN, legal_entity_id=entity)

    assert [row.period_number for row in rows] == [1, 2]
    assert rows[0].due_item_id is not None and rows[1].due_item_id is None
    assert service.list_iva_settlements(2025, ADMIN, legal_entity_id=entity) == []
    with pytest.raises(PermissionDenied):
        service.list_iva_settlements(2026, COLLABORATOR, legal_entity_id=entity)
    with pytest.raises(AgentForbidden):
        service.list_iva_settlements(2026, AGENT_ADMIN, legal_entity_id=entity)
