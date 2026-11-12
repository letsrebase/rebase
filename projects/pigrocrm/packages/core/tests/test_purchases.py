"""A purchase is anything the space pays for, with its document -- as distinct from an hour's
cost, which is an internal, notional figure derived from a rate you chose yourself (§4.2).
These are the old cost tests, kept (a cost is an `expense`), then one block per kind."""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from pigrocrm.core.purchases.schemas import (
    PurchaseCreate,
    PurchaseListQuery,
    PurchaseUpdate,
    VatLine,
)
from pigrocrm.core.purchases.service import PurchaseService
from pigrocrm.core.timetracking.categories import CostCategoryService
from pigrocrm.core.timetracking.locks import PeriodLockService
from pigrocrm.core.timetracking.schemas import PeriodLockCreate

WRITER = Actor(id=None, type="user", role="collaborator")
READER = Actor(id=None, type="user", role="readonly")


def _create(service, category_id, **overrides):
    payload = {
        "category_id": category_id,
        "date": date(2026, 3, 5),
        "amount": Decimal("120.00"),
        "description": "Licenza annuale",
    }
    payload.update(overrides)
    return service.create(PurchaseCreate(**payload), WRITER)


def test_an_expense_with_no_deal_is_a_general_expense(
    db_session: Session, seeded_category_id: UUID, seeded_deal_id: UUID
) -> None:
    """§7.4. It enters the period P&L in a row of its own and is never apportioned onto
    any deal: any apportionment key would make a deal's margin move when a *different*
    deal was invoiced."""
    service = PurchaseService(db_session)
    general = _create(service, seeded_category_id, deal_id=None)
    attributed = _create(service, seeded_category_id, deal_id=seeded_deal_id)
    assert general.deal_id is None

    only_general = service.list(PurchaseListQuery(only_general=True), READER)
    assert [c.id for c in only_general.items] == [general.id]
    per_deal = service.list(PurchaseListQuery(deal_id=seeded_deal_id), READER)
    assert [c.id for c in per_deal.items] == [attributed.id]


def test_a_negative_amount_is_a_refund_and_zero_is_refused(
    db_session: Session, seeded_category_id: UUID
) -> None:
    """§4.4, the same choice slice 3 §6.1 rule 6 makes for a discount: represent the
    correction with the instrument that already exists, instead of a `type` column that
    multiplies the cases in every sum."""
    service = PurchaseService(db_session)
    assert _create(service, seeded_category_id, amount=Decimal("-45.50")).amount == Decimal(
        "-45.50"
    )
    with pytest.raises(ValidationFailed) as excinfo:
        _create(service, seeded_category_id, amount=Decimal("0.00"))
    assert excinfo.value.details["field"] == "amount"


def test_an_archived_category_cannot_be_chosen_for_a_new_purchase(
    db_session: Session, seeded_category_id: UUID
) -> None:
    admin = Actor(id=None, type="system", role="admin")
    CostCategoryService(db_session).archive_cost_category(seeded_category_id, admin)
    with pytest.raises(ValidationFailed) as excinfo:
        _create(PurchaseService(db_session), seeded_category_id)
    assert excinfo.value.details["field"] == "category_id"


def test_a_write_into_a_closed_period_is_refused_both_ways(
    db_session: Session, seeded_category_id: UUID, seeded_user_id: UUID
) -> None:
    admin = Actor(id=seeded_user_id, type="user", role="admin")
    service = PurchaseService(db_session)
    purchase = _create(service, seeded_category_id, date=date(2026, 4, 5))
    PeriodLockService(db_session).close_period(PeriodLockCreate(year=2026, month=3), admin)

    with pytest.raises(Conflict):
        _create(service, seeded_category_id, date=date(2026, 3, 5))
    with pytest.raises(Conflict):
        service.update(purchase.id, PurchaseUpdate(date=date(2026, 3, 5)), WRITER)

    PeriodLockService(db_session).close_period(PeriodLockCreate(year=2026, month=4), admin)
    with pytest.raises(Conflict):
        service.soft_delete(purchase.id, WRITER)


def test_the_receipt_is_a_document_reference_not_bytes(
    db_session: Session, seeded_category_id: UUID
) -> None:
    """The previous system kept the attachment as base64 inside the costs JSON
    (`parseBase64Payload`). The document store already exists, with pluggable storage,
    versioning and a hash, and is not reinvented (§10.3). An id that resolves to
    nothing is `NotFound`, not a raw `ForeignKeyViolation`."""
    with pytest.raises(NotFound):
        _create(PurchaseService(db_session), seeded_category_id, document_id=uuid4())


def test_soft_delete_is_reversible_and_a_reader_cannot_write(
    db_session: Session, seeded_category_id: UUID
) -> None:
    service = PurchaseService(db_session)
    purchase = _create(service, seeded_category_id)
    service.soft_delete(purchase.id, WRITER)
    assert service.list(PurchaseListQuery(), READER).items == []
    # `PurchaseRead` carries no `deleted_at` -- no other entity's Read schema does, and
    # exposing it would invite a client to branch on soft-delete state when list
    # queries already filter it out. Reversibility is proven by visibility instead.
    restored = service.restore(purchase.id, WRITER)
    assert restored.id == purchase.id
    assert [c.id for c in service.list(PurchaseListQuery(), READER).items] == [purchase.id]
    with pytest.raises(PermissionDenied):
        service.soft_delete(purchase.id, READER)


def test_the_list_is_ordered_by_date_descending(
    db_session: Session, seeded_category_id: UUID
) -> None:
    service = PurchaseService(db_session)
    for day in (5, 20, 12):
        _create(service, seeded_category_id, date=date(2026, 3, day))
    assert [c.date.day for c in service.list(PurchaseListQuery(), READER).items] == [20, 12, 5]


def test_a_cursor_that_names_no_purchase_is_refused_not_read_as_the_first_page(
    db_session: Session,
) -> None:
    with pytest.raises(ValidationFailed) as refused:
        PurchaseService(db_session).list(PurchaseListQuery(cursor=uuid4()), READER)

    assert refused.value.details["field"] == "cursor"


# -- one block per kind ------------------------------------------------------------------------

ADMIN = Actor(id=None, type="system", role="admin")


def _supplier(session: Session, name: str = "Vendor Srl", **fields: object) -> UUID:
    from pigrocrm.core.organizations.schemas import OrganizationCreate
    from pigrocrm.core.organizations.service import OrganizationService

    return (
        OrganizationService(session)
        .create(
            OrganizationCreate(legal_name=name, roles=["supplier"], **fields),  # type: ignore[arg-type]
            ADMIN,
        )
        .id
    )


def _buyer(session: Session, pack: str) -> UUID:
    """The default legal entity, on `pack`: the buyer a supplier's document names."""
    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
    from pigrocrm.core.fiscal.service import FiscalProfileService
    from pigrocrm.core.legal_entities.service import LegalEntityService

    profile = (
        FiscalProfileUpsert(regime_code="RF19")
        if pack == "it-flat-rate"
        else FiscalProfileUpsert(pack_id=pack)
    )
    FiscalProfileService(session).upsert(profile, ADMIN)
    return LegalEntityService(session).resolve().id


def _line(rate: str = "22.00", taxable: str = "100.00", vat: str = "22.00", **fields: object):
    return {
        "rate": Decimal(rate),
        "taxable_amount": Decimal(taxable),
        "vat_amount": Decimal(vat),
        **fields,
    }


def _invoice(service, category_id, supplier_id, **overrides):
    payload = {
        "kind": "invoice",
        "supplier_id": supplier_id,
        "number": "FA/12",
        "category_id": category_id,
        "date": date(2026, 3, 5),
        "amount": Decimal("122.00"),
        "description": "Consulenza",
        "vat_lines": [_line()],
    }
    payload.update(overrides)
    return service.create(PurchaseCreate(**payload), WRITER)


def test_an_expense_is_what_a_cost_was(db_session: Session, seeded_category_id: UUID) -> None:
    """No kind named means an expense: total only, no recap, and it costs its total."""
    service = PurchaseService(db_session)
    expense = _create(service, seeded_category_id, merchant_name="Trenitalia")
    assert (expense.kind, expense.status, expense.currency) == ("expense", "registered", "EUR")
    assert expense.merchant_name == "Trenitalia"
    assert expense.cost_amount == expense.amount == Decimal("120.00")
    assert expense.vat_lines == [] and expense.iva_period is None
    assert expense.buyer_pack_id is None


def test_a_receipt_has_the_total_only_and_names_a_shop(
    db_session: Session, seeded_category_id: UUID
) -> None:
    service = PurchaseService(db_session)
    receipt = _create(service, seeded_category_id, kind="receipt", merchant_name="Cartoleria")
    assert receipt.kind == "receipt" and receipt.supplier_id is None
    assert receipt.cost_amount == receipt.amount
    # The VAT of a retail receipt is not deductible without an invoice, so a receipt carries no
    # recap, no VAT regime and no IVA period.
    for refused in (
        {"vat_lines": [_line()]},
        {"reverse_charge": True},
        {"iva_period": date(2026, 3, 31)},
    ):
        with pytest.raises(ValidationFailed):
            _create(service, seeded_category_id, kind="receipt", **refused)


def test_an_invoice_names_its_supplier_and_splits_its_vat(
    db_session: Session, seeded_category_id: UUID
) -> None:
    service = PurchaseService(db_session)
    supplier = _supplier(db_session)
    with pytest.raises(ValidationFailed) as no_supplier:
        _invoice(service, seeded_category_id, None)
    assert no_supplier.value.details["field"] == "supplier_id"
    with pytest.raises(ValidationFailed) as no_lines:
        _invoice(service, seeded_category_id, supplier, vat_lines=[])
    assert no_lines.value.details["field"] == "vat_lines"
    # A customer that is not a supplier cannot be the supplier of a purchase.
    from pigrocrm.core.organizations.schemas import OrganizationCreate
    from pigrocrm.core.organizations.service import OrganizationService

    customer = OrganizationService(db_session).create(OrganizationCreate(legal_name="Cli"), ADMIN)
    with pytest.raises(ValidationFailed) as not_a_supplier:
        _invoice(service, seeded_category_id, customer.id)
    assert not_a_supplier.value.details["field"] == "supplier_id"

    invoice = _invoice(service, seeded_category_id, supplier)
    assert invoice.kind == "invoice" and invoice.number == "FA/12"
    assert [(line.rate, line.taxable_amount, line.vat_amount) for line in invoice.vat_lines] == [
        (Decimal("22.00"), Decimal("100.00"), Decimal("22.00"))
    ]
    # Registered in the period it was received in unless a person chose another.
    assert invoice.iva_period == date(2026, 3, 5)
    assert service.get(invoice.id, READER).vat_lines == invoice.vat_lines


def test_split_payment_and_reverse_charge_exclude_each_other(
    db_session: Session, seeded_category_id: UUID
) -> None:
    """REB-1099: art. 17-ter c. 1 DPR 633/1972 and Circolare 14/E of 2015 § 4."""
    service = PurchaseService(db_session)
    supplier = _supplier(db_session)
    with pytest.raises(ValidationFailed) as both:
        _invoice(service, seeded_category_id, supplier, split_payment=True, reverse_charge=True)
    assert both.value.details["field"] == "split_payment"
    # Under reverse charge the VAT is the buyer's and is not in what is paid.
    for one in (
        {"split_payment": True},
        {"reverse_charge": True, "amount": Decimal("100.00")},
    ):
        assert _invoice(service, seeded_category_id, supplier, **one).kind == "invoice"


def test_an_invoices_total_must_match_its_recap(
    db_session: Session, seeded_category_id: UUID
) -> None:
    service = PurchaseService(db_session)
    supplier = _supplier(db_session)
    with pytest.raises(ValidationFailed) as off:
        _invoice(service, seeded_category_id, supplier, amount=Decimal("130.00"))
    assert off.value.details["field"] == "amount"
    # Bollo is part of the document's total; a cent of rounding is not a mismatch.
    ok = _invoice(
        service,
        seeded_category_id,
        supplier,
        amount=Decimal("124.01"),
        stamp_duty=Decimal("2.00"),
    )
    assert ok.stamp_duty == Decimal("2.00")
    # A zero-rate line carries its nature and no tax, and only a zero-rate line does.
    exempt = _line("0.00", "50.00", "0.00", nature="N2.2")
    assert (
        _invoice(service, seeded_category_id, supplier, amount=Decimal("50.00"), vat_lines=[exempt])
        .vat_lines[0]
        .nature
        == "N2.2"
    )
    for broken in (_line("0.00", "50.00", "0.00"), _line("22.00", nature="N2.2")):
        with pytest.raises(ValidationFailed):
            _invoice(service, seeded_category_id, supplier, vat_lines=[broken])


def test_a_credit_note_is_negative_and_corrects_an_invoice_of_the_same_supplier(
    db_session: Session, seeded_category_id: UUID
) -> None:
    service = PurchaseService(db_session)
    supplier = _supplier(db_session)
    other = _supplier(db_session, "Other Srl")
    invoice = _invoice(service, seeded_category_id, supplier)
    note = _invoice(
        service,
        seeded_category_id,
        supplier,
        kind="credit_note",
        amount=Decimal("-61.00"),
        vat_lines=[_line(taxable="-50.00", vat="-11.00")],
        corrects_purchase_id=invoice.id,
    )
    assert note.corrects_purchase_id == invoice.id and note.amount == Decimal("-61.00")
    with pytest.raises(ValidationFailed):
        _invoice(service, seeded_category_id, supplier, kind="credit_note")  # positive
    with pytest.raises(ValidationFailed):
        _invoice(service, seeded_category_id, supplier, amount=Decimal("-122.00"))  # an invoice
    with pytest.raises(ValidationFailed) as foreign:
        _invoice(
            service,
            seeded_category_id,
            other,
            kind="credit_note",
            amount=Decimal("-61.00"),
            vat_lines=[_line(taxable="-50.00", vat="-11.00")],
            corrects_purchase_id=invoice.id,
        )
    assert foreign.value.details["field"] == "corrects_purchase_id"
    # Only a credit note corrects.
    with pytest.raises(ValidationFailed):
        _create(service, seeded_category_id, corrects_purchase_id=invoice.id)


def test_what_a_purchase_costs_follows_the_buyers_pack(
    db_session: Session, seeded_category_id: UUID
) -> None:
    """§ 2, «What a purchase costs»: an ordinary buyer's cost is the taxable amount plus the VAT
    it cannot deduct; a flat-rate buyer's is the total (plus the VAT it integrates on a reverse
    charge); a shared purchase's is the total. Frozen with the pack and regime."""
    service = PurchaseService(db_session)
    supplier = _supplier(db_session)
    # A shared invoice: the space has no profile yet, the buyer is unknown, the cost the total.
    shared = _invoice(service, seeded_category_id, supplier)
    assert shared.cost_amount == Decimal("122.00") and shared.buyer_pack_id is None

    buyer = _buyer(db_session, "it-standard")
    ordinary = _invoice(
        service,
        seeded_category_id,
        supplier,
        legal_entity_id=buyer,
        amount=Decimal("244.00"),
        vat_lines=[_line(), _line(taxable="100.00", vat="22.00", deductible_share=Decimal("40"))],
    )
    # 244 less the VAT that is deducted: 22 + 40% of 22.
    assert ordinary.cost_amount == Decimal("244.00") - Decimal("22.00") - Decimal("8.80")
    assert (ordinary.buyer_pack_id, ordinary.buyer_regime_code) == ("it-standard", "RF01")
    # A purchase that names no buyer in a one-entity space is that entity's.
    implicit = _invoice(service, seeded_category_id, supplier)
    assert implicit.legal_entity_id == buyer and implicit.cost_amount == Decimal("100.00")
    # A receipt and an expense carry no VAT: the total, whatever the pack.
    assert _create(service, seeded_category_id, legal_entity_id=buyer).cost_amount == Decimal(
        "120.00"
    )

    # Reverse charge on an ordinary buyer: the VAT is owed, not paid; what cannot be deducted
    # of it is cost.
    reverse = _invoice(
        service,
        seeded_category_id,
        supplier,
        amount=Decimal("100.00"),
        reverse_charge=True,
        vat_lines=[_line(deductible_share=Decimal("40"))],
    )
    assert reverse.cost_amount == Decimal("100.00") + Decimal("22.00") - Decimal("8.80")


def test_a_flat_rate_buyer_never_recovers_vat(
    db_session: Session, seeded_category_id: UUID
) -> None:
    service = PurchaseService(db_session)
    supplier = _supplier(db_session)
    buyer = _buyer(db_session, "it-flat-rate")
    paid = _invoice(service, seeded_category_id, supplier, legal_entity_id=buyer)
    assert paid.cost_amount == Decimal("122.00")
    assert paid.buyer_pack_id == "it-flat-rate"
    # Reverse charge from abroad: the integrated VAT is owed and not deductible either.
    abroad = _invoice(
        service,
        seeded_category_id,
        supplier,
        legal_entity_id=buyer,
        amount=Decimal("100.00"),
        reverse_charge=True,
        vat_lines=[_line()],
    )
    assert abroad.cost_amount == Decimal("122.00")


def test_withholding_needs_a_withholding_agent_and_adds_up(
    db_session: Session, seeded_category_id: UUID
) -> None:
    service = PurchaseService(db_session)
    supplier = _supplier(db_session, supplier_kind="professional")
    withheld = {
        "withholding_rate": Decimal("20.00"),
        "withholding_base": Decimal("100.00"),
        "withholding_amount": Decimal("20.00"),
    }
    buyer = _buyer(db_session, "it-standard")
    ok = _invoice(service, seeded_category_id, supplier, legal_entity_id=buyer, **withheld)
    assert ok.withholding_amount == Decimal("20.00")
    with pytest.raises(ValidationFailed) as wrong:
        _invoice(
            service,
            seeded_category_id,
            supplier,
            legal_entity_id=buyer,
            **{**withheld, "withholding_amount": Decimal("25.00")},
        )
    assert wrong.value.details["field"] == "withholding_amount"
    with pytest.raises(ValidationFailed):
        _invoice(
            service,
            seeded_category_id,
            supplier,
            legal_entity_id=buyer,
            withholding_rate=Decimal("20.00"),
        )
    with pytest.raises(ValidationFailed):  # an expense is not withheld
        _create(service, seeded_category_id, **withheld)
    # A flat-rate buyer is not a withholding agent (L. 190/2014, art. 1 c. 69).
    flat = _buyer(db_session, "it-flat-rate")
    with pytest.raises(ValidationFailed) as not_an_agent:
        _invoice(service, seeded_category_id, supplier, legal_entity_id=flat, **withheld)
    assert not_an_agent.value.details["field"] == "withholding_rate"


def test_a_foreign_currency_carries_its_euro_rate(
    db_session: Session, seeded_category_id: UUID
) -> None:
    service = PurchaseService(db_session)
    dollars = {
        "currency": "USD",
        "currency_amount": Decimal("110.00"),
        "exchange_rate": Decimal("1.100000"),
        "exchange_rate_date": date(2026, 3, 4),
    }
    purchase = _create(service, seeded_category_id, amount=Decimal("100.00"), **dollars)
    assert purchase.currency == "USD" and purchase.exchange_rate == Decimal("1.100000")
    assert purchase.amount == Decimal("100.00")
    with pytest.raises(ValidationFailed):  # a foreign amount needs all three
        _create(service, seeded_category_id, currency="USD")
    with pytest.raises(ValidationFailed):  # euro has none
        _create(service, seeded_category_id, currency_amount=Decimal("5.00"))


def test_the_iva_period_defaults_to_the_receipt_and_a_person_may_choose_another(
    db_session: Session, seeded_category_id: UUID
) -> None:
    """The day of receipt is a default (art. 25 c. 1 DPR 633/72 annotates the register after the
    receipt); the term by which it must be annotated is not enforced here (A5)."""
    service = PurchaseService(db_session)
    supplier = _supplier(db_session)
    received = _invoice(service, seeded_category_id, supplier, received_on=date(2026, 4, 2))
    assert received.iva_period == date(2026, 4, 2)
    chosen = _invoice(service, seeded_category_id, supplier, iva_period=date(2026, 12, 1))
    assert chosen.iva_period == date(2026, 12, 1)


def test_an_update_recomputes_the_cost_and_replaces_the_recap(
    db_session: Session, seeded_category_id: UUID
) -> None:
    service = PurchaseService(db_session)
    supplier = _supplier(db_session)
    buyer = _buyer(db_session, "it-standard")
    invoice = _invoice(service, seeded_category_id, supplier, legal_entity_id=buyer)
    assert invoice.cost_amount == Decimal("100.00")
    updated = service.update(
        invoice.id,
        PurchaseUpdate(
            amount=Decimal("122.00"),
            vat_lines=[_line(deductible_share=Decimal("0"))],
        ),
        WRITER,
    )
    assert updated.cost_amount == Decimal("122.00")
    # The pack is frozen: a profile that changes afterwards does not move a registered cost.
    _buyer(db_session, "it-flat-rate")
    again = service.update(invoice.id, PurchaseUpdate(number="FA/13"), WRITER)
    assert again.buyer_pack_id == "it-standard" and again.number == "FA/13"
    # Turning an invoice into an expense needs its recap emptied in the same change.
    with pytest.raises(ValidationFailed):
        service.update(invoice.id, PurchaseUpdate(kind="expense"), WRITER)
    with pytest.raises(ValidationFailed):
        service.update(invoice.id, PurchaseUpdate(amount=Decimal("999.00")), WRITER)


def test_a_cancelled_purchase_stays_on_the_record_and_cannot_be_edited(
    db_session: Session, seeded_category_id: UUID
) -> None:
    service = PurchaseService(db_session)
    purchase = _create(service, seeded_category_id)
    cancelled = service.cancel(purchase.id, WRITER)
    assert cancelled.status == "cancelled"
    assert [p.id for p in service.list(PurchaseListQuery(status="cancelled"), READER).items] == [
        purchase.id
    ]
    assert service.list(PurchaseListQuery(status="registered"), READER).items == []
    with pytest.raises(Conflict):
        service.update(purchase.id, PurchaseUpdate(description="x"), WRITER)
    with pytest.raises(Conflict):
        service.cancel(purchase.id, WRITER)
    with pytest.raises(PermissionDenied):
        service.cancel(_create(service, seeded_category_id).id, READER)


def test_the_list_filters_by_supplier_and_kind(
    db_session: Session, seeded_category_id: UUID
) -> None:
    service = PurchaseService(db_session)
    supplier = _supplier(db_session)
    invoice = _invoice(service, seeded_category_id, supplier)
    _create(service, seeded_category_id)
    by_supplier = service.list(PurchaseListQuery(supplier_id=supplier), READER)
    assert [p.id for p in by_supplier.items] == [invoice.id]
    assert [p.kind for p in service.list(PurchaseListQuery(kind="expense"), READER).items] == [
        "expense"
    ]


def test_the_history_is_written_under_purchase(
    db_session: Session, seeded_category_id: UUID
) -> None:
    from sqlalchemy import select

    from pigrocrm.core.activities.models import Activity

    service = PurchaseService(db_session)
    purchase = _create(service, seeded_category_id)
    service.update(purchase.id, PurchaseUpdate(description="Altro"), WRITER)
    service.cancel(purchase.id, WRITER)
    kinds = list(
        db_session.execute(
            select(Activity.kind)
            .where(Activity.entity_type == "purchase", Activity.entity_id == purchase.id)
            .order_by(Activity.occurred_at, Activity.id)
        ).scalars()
    )
    assert kinds == ["created", "updated", "cancelled"]


def test_an_edit_leaves_an_archived_supplier_alone_but_a_new_one_must_be_a_supplier(
    db_session: Session, seeded_category_id: UUID
) -> None:
    from pigrocrm.core.organizations.service import OrganizationService

    service = PurchaseService(db_session)
    supplier = _supplier(db_session)
    invoice = _invoice(service, seeded_category_id, supplier)
    OrganizationService(db_session).soft_delete(supplier, ADMIN)
    assert service.update(invoice.id, PurchaseUpdate(number="FA/99"), WRITER).number == "FA/99"
    from pigrocrm.core.organizations.schemas import OrganizationCreate

    customer = OrganizationService(db_session).create(OrganizationCreate(legal_name="Cli"), ADMIN)
    with pytest.raises(ValidationFailed):
        service.update(invoice.id, PurchaseUpdate(supplier_id=customer.id), WRITER)


def test_a_cost_turned_into_an_invoice_takes_the_buyers_pack_and_entity(
    db_session: Session, seeded_category_id: UUID
) -> None:
    """A row that was a cost has no pack decision. Editing it into a supplier's document buys it
    as the space's only legal entity, on that entity's pack, as a new invoice would."""
    service = PurchaseService(db_session)
    supplier = _supplier(db_session)
    expense = _create(service, seeded_category_id)
    assert expense.legal_entity_id is None and expense.buyer_pack_id is None
    buyer = _buyer(db_session, "it-standard")
    invoice = service.update(
        expense.id,
        PurchaseUpdate(
            kind="invoice",
            supplier_id=supplier,
            amount=Decimal("122.00"),
            vat_lines=[VatLine(**_line())],
        ),
        WRITER,
    )
    assert invoice.legal_entity_id == buyer and invoice.buyer_pack_id == "it-standard"
    assert invoice.cost_amount == Decimal("100.00")


def test_the_totals_count_a_cancelled_purchase_when_no_status_is_asked_and_never_a_deleted_one(
    db_session: Session, seeded_category_id: UUID, seeded_deal_id: UUID
) -> None:
    from sqlalchemy import select

    from pigrocrm.core.legal_entities.models import LegalEntity

    entity = db_session.execute(
        select(LegalEntity.id).where(LegalEntity.is_default.is_(True))
    ).scalar_one()
    service = PurchaseService(db_session)
    _create(service, seeded_category_id, amount=Decimal("10.00"), legal_entity_id=entity)
    cancelled = _create(service, seeded_category_id, amount=Decimal("20.00"))
    deleted = _create(service, seeded_category_id, amount=Decimal("40.00"))
    _create(service, seeded_category_id, amount=Decimal("80.00"), deal_id=seeded_deal_id)
    service.cancel(cancelled.id, WRITER)
    service.soft_delete(deleted.id, WRITER)

    def totals(**filters):
        page = service.list(PurchaseListQuery(**filters), READER)
        return page.total_count, page.total_amount

    assert totals() == (3, Decimal("110.00"))
    assert totals(status="registered") == (2, Decimal("90.00"))
    assert totals(only_general=True) == (2, Decimal("30.00"))
    assert totals(only_general=True, status="registered") == (1, Decimal("10.00"))
    # The deal's purchase follows the deal's legal entity; the shared ones answer none.
    assert totals(legal_entity_id=entity) == (2, Decimal("90.00"))
