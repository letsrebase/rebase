"""The IVA periods and registers of a legal entity's year (REB-1024, suppliers design § 6).

What a register adds up to is the settlement's own line, so most checks read the period's ladder
and a register of the same documents and compare; the rest is the placement of a document (by its
issue date or its `iva_period`), the running figures of a period that has not been settled, and
who may read.
"""

import datetime as dt
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import AgentForbidden, PermissionDenied, ValidationFailed
from pigrocrm.core.fiscal import iva_register, iva_settlement
from pigrocrm.core.fiscal.iva_register import IvaRegisterService, iva_entities
from pigrocrm.core.fiscal.iva_settlement import IvaSettlementService
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.fiscal.schemas import IvaPeriod
from pigrocrm.core.invoices.models import Invoice, InvoiceLine, InvoiceRegisterGap
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.purchases.models import Purchase, PurchaseVatLine
from pigrocrm.core.timetracking.models import CostCategory

ADMIN = Actor(id=uuid4(), type="user", role="admin")
COLLABORATOR = Actor(id=uuid4(), type="user", role="collaborator")
AGENT_ADMIN = Actor(id=uuid4(), type="mcp", role="admin")
D = dt.date
M = Decimal


def _today(monkeypatch: pytest.MonkeyPatch, day: dt.date) -> None:
    monkeypatch.setattr(iva_settlement, "today_local", lambda: day)
    monkeypatch.setattr(iva_register, "today_local", lambda: day)


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
    number: int,
    *,
    groups: tuple[tuple[str, str | None, str], ...] = (("22.00", None, "100.00"),),
    status: str = "issued",
    split: bool = False,
    customer: str = "Acme S.r.l.",
) -> Invoice:
    """An invoice with one line per `(rate, nature, taxable)` group, its totals consistent."""
    organization = Organization(legal_name=customer, country="IT", roles=["customer"])
    session.add(organization)
    session.flush()
    taxable = sum((M(group[2]) for group in groups), M("0.00"))
    vat = sum((M(group[2]) * M(group[0]) / 100 for group in groups), M("0.00")).quantize(M("0.01"))
    invoice = Invoice(
        customer_id=organization.id,
        legal_entity_id=entity,
        type="invoice",
        status=status,
        year=day.year,
        number=number,
        issue_date=day,
        taxable_amount=taxable,
        vat_amount=vat,
        total=taxable + vat,
        split_payment=split,
        cancelled_at=day if status == "cancelled" else None,
        cancellation_reason="errore" if status == "cancelled" else None,
    )
    session.add(invoice)
    session.flush()
    for position, (rate, nature, amount) in enumerate(groups, start=1):
        session.add(
            InvoiceLine(
                invoice_id=invoice.id,
                line_number=position,
                description="Consulenza",
                quantity=M("1"),
                unit_price=M(amount),
                total_price=M(amount),
                vat_rate=M(rate),
                vat_nature=nature,
            )
        )
    session.flush()
    return invoice


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
    number: str = "FT/1",
    supplier: str = "Fornitore S.r.l.",
) -> Purchase:
    category = session.scalars(select(CostCategory)).first()
    if category is None:
        category = CostCategory(name="Servizi")
        session.add(category)
        session.flush()
    organization = Organization(legal_name=supplier, country="IT", roles=["supplier"])
    session.add(organization)
    session.flush()
    sign = -1 if kind == "credit_note" else 1
    purchase = Purchase(
        legal_entity_id=entity,
        category_id=category.id,
        supplier_id=organization.id,
        kind=kind,
        status=status,
        number=number,
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
    return purchase


# --- the periods -----------------------------------------------------------------------------


def test_a_year_lists_its_periods_with_the_stored_ladder_and_the_running_one(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _today(monkeypatch, D(2026, 7, 10))
    entity = _entity(db_session)
    _sale(db_session, entity, D(2026, 1, 20), 1, groups=(("22.00", None, "1000.00"),))
    _purchase(db_session, entity, D(2026, 2, 10), "100.00")
    _sale(db_session, entity, D(2026, 5, 2), 2, groups=(("22.00", None, "500.00"),))
    _sale(db_session, entity, D(2026, 7, 4), 3, groups=(("22.00", None, "100.00"),))
    IvaSettlementService(db_session).settle_iva_period(
        IvaPeriod(year=2026, number=2), ADMIN, legal_entity_id=entity
    )

    periods = IvaRegisterService(db_session).list_periods(2026, ADMIN, legal_entity_id=entity)

    assert [(p.number, p.state, p.settled) for p in periods] == [
        (1, "ended", True),
        (2, "ended", True),
        (3, "open", False),
        (4, "future", False),
    ]
    assert periods[0].label == "1° trimestre 2026"
    first, second, running, future = periods
    assert first.ladder is not None
    assert (first.ladder.sales_vat, first.ladder.purchase_vat) == (M("220.00"), M("100.00"))
    assert (first.ladder.surcharge, first.ladder.result, first.ladder.outcome) == (
        M("1.20"),
        M("121.20"),
        "due",
    )
    assert first.payment_due_on == D(2026, 5, 18)
    assert first.due_item_id is not None and first.payment_state == "expected"
    # The second quarter's debit is not under the carry limit, so it is paid.
    assert second.ladder is not None and second.ladder.sales_vat == M("110.00")
    # The running quarter: its own documents only, no credit and no debit to carry in.
    assert running.ladder is not None
    assert (running.ladder.sales_vat, running.ladder.credit_in) == (M("22.00"), M("0.00"))
    assert running.payment_due_on == D(2026, 11, 16)
    assert running.due_item_id is None and running.payment_state is None
    assert future.ladder is None and future.payment_due_on is None


def test_a_running_period_carries_the_credit_the_previous_one_left(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _today(monkeypatch, D(2026, 5, 10))
    entity = _entity(db_session)
    _sale(db_session, entity, D(2026, 2, 1), 1, groups=(("22.00", None, "500.00"),))
    _purchase(db_session, entity, D(2026, 2, 5), "400.00")
    _sale(db_session, entity, D(2026, 5, 2), 2, groups=(("22.00", None, "2000.00"),))
    IvaSettlementService(db_session).settle_iva_period(
        IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity
    )

    periods = IvaRegisterService(db_session).list_periods(2026, ADMIN, legal_entity_id=entity)

    assert periods[0].ladder is not None
    assert (periods[0].ladder.result, periods[0].ladder.outcome) == (M("-290.00"), "credit")
    assert periods[0].due_item_id is None and periods[0].payment_state is None
    running = periods[1]
    assert running.state == "open" and not running.settled and running.ladder is not None
    # 440 - 290 credit = 150, plus the quarterly 1%
    assert (running.ladder.credit_in, running.ladder.result) == (M("290.00"), M("151.50"))


def test_a_period_after_an_unsettled_one_has_no_ladder_while_an_earlier_period_is_settled(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _today(monkeypatch, D(2026, 11, 10))
    entity = _entity(db_session)
    _sale(db_session, entity, D(2026, 2, 1), 1)
    IvaSettlementService(db_session).settle_iva_period(
        IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity
    )

    periods = IvaRegisterService(db_session).list_periods(2026, ADMIN, legal_entity_id=entity)

    assert [(p.number, p.settled, p.ladder is None) for p in periods] == [
        (1, True, False),
        (2, False, False),  # the period before it is stored: its result is the carry-in
        (3, False, True),  # the period before it is not: the carry-in would be a guess
        (4, False, True),
    ]


def test_the_period_that_may_wait_names_the_packs_limit(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _today(monkeypatch, D(2026, 11, 10))
    entity = _entity(db_session)

    periods = IvaRegisterService(db_session).list_periods(2026, ADMIN, legal_entity_id=entity)

    assert [p.carry_forward_limit for p in periods] == [M("100.00"), M("100.00"), None, None]


def test_a_monthly_entity_lists_twelve_periods(db_session: Session) -> None:
    entity = _entity(db_session, frequency="monthly")

    periods = IvaRegisterService(db_session).list_periods(2026, ADMIN, legal_entity_id=entity)

    assert [p.number for p in periods] == list(range(1, 13))
    assert periods[0].label == "gennaio 2026"


def test_a_flat_rate_entity_has_no_periods_and_no_register(db_session: Session) -> None:
    flat = _entity(db_session, pack="it-flat-rate")
    service = IvaRegisterService(db_session)

    assert service.list_periods(2026, ADMIN, legal_entity_id=flat) == []
    with pytest.raises(ValidationFailed) as refusal:
        service.read_sales_register(2026, 1, ADMIN, legal_entity_id=flat)
    assert refusal.value.details["field"] == "year"
    with pytest.raises(ValidationFailed):
        service.read_purchase_register(2026, 1, ADMIN, legal_entity_id=flat)


def test_a_year_the_pack_has_no_law_for_has_no_periods(db_session: Session) -> None:
    entity = _entity(db_session)

    assert IvaRegisterService(db_session).list_periods(2025, ADMIN, legal_entity_id=entity) == []


def test_only_entities_with_a_periodic_pack_have_registers(db_session: Session) -> None:
    ordinary = _entity(db_session)
    _entity(db_session, pack="it-flat-rate")

    entities = iva_entities(db_session, ADMIN)

    assert [
        (e.legal_entity_id, e.frequency) for e in entities if e.legal_entity_id == ordinary
    ] == [(ordinary, "quarterly")]
    assert all(e.name != "" for e in entities)
    assert len({e.legal_entity_id for e in entities}) == len(entities)


def test_a_period_that_does_not_exist_is_refused(db_session: Session) -> None:
    quarterly = _entity(db_session)

    with pytest.raises(ValidationFailed) as refusal:
        IvaRegisterService(db_session).read_sales_register(
            2026, 5, ADMIN, legal_entity_id=quarterly
        )
    assert refusal.value.details["field"] == "number"


def test_only_an_administrator_with_full_access_reads(db_session: Session) -> None:
    entity = _entity(db_session)
    service = IvaRegisterService(db_session)

    with pytest.raises(PermissionDenied):
        service.list_periods(2026, COLLABORATOR, legal_entity_id=entity)
    with pytest.raises(PermissionDenied):
        service.read_sales_register(2026, 1, COLLABORATOR, legal_entity_id=entity)
    with pytest.raises(PermissionDenied):
        service.read_purchase_register(2026, 1, COLLABORATOR, legal_entity_id=entity)
    with pytest.raises(PermissionDenied):
        iva_entities(db_session, COLLABORATOR)
    with pytest.raises(AgentForbidden):
        service.read_sales_register(2026, 1, AGENT_ADMIN, legal_entity_id=entity)
    with pytest.raises(AgentForbidden):
        service.list_periods(2026, AGENT_ADMIN, legal_entity_id=entity)


# --- the sales register ----------------------------------------------------------------------


def test_the_sales_register_adds_up_to_the_settlements_sales_line(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _today(monkeypatch, D(2026, 7, 10))
    entity = _entity(db_session)
    mixed = _sale(
        db_session,
        entity,
        D(2026, 1, 20),
        1,
        groups=(("22.00", None, "1000.00"), ("0.00", "N2.1", "300.00"), ("10.00", None, "200.00")),
    )
    _sale(db_session, entity, D(2026, 2, 2), 2, groups=(("22.00", None, "500.00"),), split=True)
    _sale(db_session, entity, D(2026, 2, 3), 3, status="cancelled")
    _sale(db_session, entity, D(2026, 3, 31), 4, groups=(("22.00", None, "50.00"),))
    _sale(db_session, entity, D(2026, 4, 1), 5)  # the next quarter
    IvaSettlementService(db_session).settle_iva_period(
        IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity
    )

    register = IvaRegisterService(db_session).read_sales_register(
        2026, 1, ADMIN, legal_entity_id=entity
    )
    period = IvaRegisterService(db_session).list_periods(2026, ADMIN, legal_entity_id=entity)[0]

    assert [(row.number, row.status) for row in register.items] == [
        (1, "issued"),
        (2, "issued"),
        (3, "cancelled"),
        (4, "issued"),
    ]
    assert register.total_count == 4 and register.next_offset is None
    first = register.items[0]
    assert (first.invoice_id, first.customer_name, first.exigibility) == (
        mixed.id,
        "Acme S.r.l.",
        "immediate",
    )
    assert [(g.rate, g.nature, g.taxable_amount, g.vat_amount) for g in first.groups] == [
        (M("0.00"), "N2.1", M("300.00"), M("0.00")),
        (M("10.00"), None, M("200.00"), M("20.00")),
        (M("22.00"), None, M("1000.00"), M("220.00")),
    ]
    assert register.items[1].exigibility == "split_payment"
    # The settlement counts the issued invoices that are not under split payment.
    assert period.ladder is not None
    assert register.totals.vat_due == period.ladder.sales_vat == M("251.00")
    assert register.totals.vat_amount == M("251.00") + M("110.00")
    assert register.totals.taxable_amount == M("1500.00") + M("500.00") + M("50.00")
    assert register.totals.total == register.totals.taxable_amount + register.totals.vat_amount
    assert [
        (s.rate, s.nature, s.exigibility, s.taxable_amount, s.vat_amount) for s in register.summary
    ] == [
        (M("0.00"), "N2.1", "immediate", M("300.00"), M("0.00")),
        (M("10.00"), None, "immediate", M("200.00"), M("20.00")),
        (M("22.00"), None, "immediate", M("1050.00"), M("231.00")),
        (M("22.00"), None, "split_payment", M("500.00"), M("110.00")),
    ]


def test_the_sales_register_pages_by_number_and_its_totals_are_the_periods(
    db_session: Session,
) -> None:
    entity = _entity(db_session)
    for number in range(1, 6):
        _sale(db_session, entity, D(2026, 2, number), number)
    service = IvaRegisterService(db_session)

    first = service.read_sales_register(2026, 1, ADMIN, legal_entity_id=entity, limit=2)
    last = service.read_sales_register(2026, 1, ADMIN, legal_entity_id=entity, limit=2, offset=4)

    assert [row.number for row in first.items] == [1, 2]
    assert (first.total_count, first.next_offset) == (5, 2)
    assert [row.number for row in last.items] == [5]
    assert last.next_offset is None
    assert first.totals == last.totals
    assert first.totals.vat_due == M("110.00")


def test_a_declared_gap_is_a_row_with_its_reason_in_the_period_of_the_invoice_before_it(
    db_session: Session,
) -> None:
    entity = _entity(db_session)
    _sale(db_session, entity, D(2026, 3, 30), 1)
    _sale(db_session, entity, D(2026, 4, 2), 4)
    for number, reason in ((2, "annullata prima dell'invio"), (3, "prova")):
        db_session.add(
            InvoiceRegisterGap(legal_entity_id=entity, year=2026, number=number, reason=reason)
        )
    db_session.flush()
    service = IvaRegisterService(db_session)

    first = service.read_sales_register(2026, 1, ADMIN, legal_entity_id=entity)
    second = service.read_sales_register(2026, 2, ADMIN, legal_entity_id=entity)

    assert [(row.kind, row.number, row.gap_reason) for row in first.items] == [
        ("invoice", 1, None),
        ("gap", 2, "annullata prima dell'invio"),
        ("gap", 3, "prova"),
    ]
    assert [(row.kind, row.number) for row in second.items] == [("invoice", 4)]
    gap = first.items[1]
    assert (gap.invoice_id, gap.groups, gap.total) == (None, [], None)
    # A gap adds nothing.
    assert first.totals.vat_due == M("22.00")


def test_an_empty_period_has_empty_registers_and_zero_totals(db_session: Session) -> None:
    entity = _entity(db_session)
    service = IvaRegisterService(db_session)

    sales = service.read_sales_register(2026, 3, ADMIN, legal_entity_id=entity)
    purchases = service.read_purchase_register(2026, 3, ADMIN, legal_entity_id=entity)

    assert (sales.items, sales.total_count, sales.summary) == ([], 0, [])
    assert (purchases.items, purchases.total_count, purchases.summary) == ([], 0, [])
    assert sales.totals.vat_due == M("0.00") and purchases.totals.deductible_vat == M("0.00")


# --- the purchase register -------------------------------------------------------------------


def test_the_purchase_register_adds_up_to_the_settlements_purchase_lines(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _today(monkeypatch, D(2026, 7, 10))
    entity = _entity(db_session)
    _purchase(db_session, entity, D(2026, 2, 10), "100.00", number="A/1", supplier="Zeta")
    _purchase(db_session, entity, D(2026, 2, 11), "50.00", share="40", number="A/2")
    _purchase(db_session, entity, D(2026, 2, 12), "70.00", status="cancelled")
    _purchase(db_session, entity, D(2026, 4, 2), "60.00")  # the next quarter
    _purchase(db_session, entity, D(2026, 3, 1), "10.00", kind="credit_note", number="NC/1")
    _purchase(db_session, entity, D(2026, 3, 2), "200.00", reverse_charge=True, number="E/1")
    IvaSettlementService(db_session).settle_iva_period(
        IvaPeriod(year=2026, number=1), ADMIN, legal_entity_id=entity
    )

    register = IvaRegisterService(db_session).read_purchase_register(
        2026, 1, ADMIN, legal_entity_id=entity
    )
    period = IvaRegisterService(db_session).list_periods(2026, ADMIN, legal_entity_id=entity)[0]

    assert [row.number for row in register.items] == ["A/1", "A/2", "NC/1", "E/1"]
    assert register.total_count == 4
    first, second, credit, reverse = register.items
    assert (first.supplier_name, first.registered_on, first.kind) == (
        "Zeta",
        D(2026, 2, 10),
        "invoice",
    )
    assert [(g.vat_amount, g.deductible_share, g.deductible_vat) for g in second.groups] == [
        (M("50.00"), M("40.00"), M("20.00"))
    ]
    assert (second.vat_amount, second.deductible_vat) == (M("50.00"), M("20.00"))
    assert (credit.kind, credit.deductible_vat, credit.total) == (
        "credit_note",
        M("-10.00"),
        M("-60.00"),
    )
    assert reverse.reverse_charge is True
    # 100 + 20 - 10 + 200 deductible; 200 owed on the reverse charge.
    assert period.ladder is not None
    assert register.totals.deductible_vat == period.ladder.purchase_vat == M("310.00")
    assert register.totals.reverse_charge_vat == period.ladder.reverse_charge_vat == M("200.00")
    assert register.totals.vat_amount == M("340.00")
    assert register.totals.taxable_amount == M("1700.00")
    assert [(s.deductible_share, s.vat_amount, s.deductible_vat) for s in register.summary] == [
        (M("40.00"), M("50.00"), M("20.00")),
        (M("100.00"), M("290.00"), M("290.00")),
    ]


def test_a_purchase_is_placed_by_its_iva_period_and_not_by_its_date(
    db_session: Session,
) -> None:
    entity = _entity(db_session)
    purchase = _purchase(db_session, entity, D(2026, 3, 30), "100.00")
    purchase.iva_period = D(2026, 4, 3)  # received and registered in the next quarter
    db_session.flush()
    service = IvaRegisterService(db_session)

    assert service.read_purchase_register(2026, 1, ADMIN, legal_entity_id=entity).items == []
    (row,) = service.read_purchase_register(2026, 2, ADMIN, legal_entity_id=entity).items
    assert (row.document_date, row.registered_on) == (D(2026, 3, 30), D(2026, 4, 3))


def test_another_legal_entitys_documents_are_not_in_the_register(db_session: Session) -> None:
    mine = _entity(db_session)
    other = _entity(db_session)
    _sale(db_session, other, D(2026, 2, 1), 1)
    _purchase(db_session, other, D(2026, 2, 1), "10.00")
    service = IvaRegisterService(db_session)

    assert service.read_sales_register(2026, 1, ADMIN, legal_entity_id=mine).items == []
    assert service.read_purchase_register(2026, 1, ADMIN, legal_entity_id=mine).items == []
    assert len(service.read_sales_register(2026, 1, ADMIN, legal_entity_id=other).items) == 1
