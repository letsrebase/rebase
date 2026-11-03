"""The constraints, exercised with raw SQL rather than through the service.

Spec 14.4 is explicit that immutability must be imposed by the database and not by
the service, so every check here bypasses `InvoiceService` entirely: an invariant that
only the service defends is an invariant any other write path -- an importer, a fix-up
script, a psql session -- can walk straight past.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.invoices.models import (
    PROFORMA_SEQUENCE_NAME,
    Invoice,
    InvoiceCounter,
    InvoiceLine,
)


@pytest.fixture
def customer_id(db_session: Session) -> UUID:
    customer = Customer(legal_name="Acme S.r.l.", country="IT")
    db_session.add(customer)
    db_session.flush()
    return customer.id


def _draft(customer_id: UUID, **overrides: object) -> Invoice:
    payload: dict[str, object] = {
        "customer_id": customer_id,
        "type": "invoice",
        "status": "draft",
        "sdi_document_type": "TD01",
        "currency": "EUR",
        "taxable_amount": Decimal("0.00"),
        "vat_amount": Decimal("0.00"),
        "stamp_duty": Decimal("0.00"),
        "total": Decimal("0.00"),
        "payment_status": "unpaid",
        "custom_fields": {},
    }
    payload.update(overrides)
    return Invoice(**payload)


def _add(db_session: Session, invoice: Invoice) -> None:
    db_session.add(invoice)
    db_session.flush()


def _refuses(db_session: Session, invoice: Invoice, constraint: str) -> None:
    db_session.add(invoice)
    with pytest.raises(IntegrityError) as caught:
        db_session.flush()
    assert constraint in str(caught.value)
    db_session.rollback()


# --- (tipo, stato): two state machines in one column -------------------------------


@pytest.mark.parametrize("status", ["draft", "issued", "cancelled"])
def test_to_invoice_may_hold_its_own_three_states(
    db_session: Session, customer_id: UUID, status: str
) -> None:
    extra: dict[str, object] = {}
    if status != "draft":
        extra = {"year": 2026, "number": 1, "issue_date": date(2026, 1, 5)}
    if status == "cancelled":
        extra |= {"cancelled_at": date(2026, 2, 1), "cancellation_reason": "importo errato"}
    _add(db_session, _draft(customer_id, status=status, **extra))


@pytest.mark.parametrize("status", ["confirmed", "consumed"])
def test_to_invoice_may_not_hold_to_proforma_state(
    db_session: Session, customer_id: UUID, status: str
) -> None:
    _refuses(db_session, _draft(customer_id, status=status), "ck_invoices_type_status")


def test_to_proforma_may_not_hold_the_issued_state(db_session: Session, customer_id: UUID) -> None:
    _refuses(
        db_session,
        _draft(customer_id, type="proforma", status="issued"),
        "ck_invoices_type_status",
    )


def test_an_unknown_type_is_refused(db_session: Session, customer_id: UUID) -> None:
    _refuses(db_session, _draft(customer_id, type="note_credit"), "ck_invoices_type_status")


# --- the number ---------------------------------------------------------------------


def test_a_number_without_a_year_is_refused(db_session: Session, customer_id: UUID) -> None:
    _refuses(
        db_session,
        _draft(customer_id, status="issued", number=1, issue_date=date(2026, 1, 5)),
        "ck_invoices_year_number_together",
    )


def test_a_draft_may_not_carry_a_number(db_session: Session, customer_id: UUID) -> None:
    """A draft has no number, so "a failed creation burned a number" is impossible by
    construction rather than by care."""
    _refuses(
        db_session,
        _draft(customer_id, year=2026, number=1),
        "ck_invoices_number_requires_issued_invoice",
    )


def test_a_proforma_may_not_carry_a_number(db_session: Session, customer_id: UUID) -> None:
    _refuses(
        db_session,
        _draft(customer_id, type="proforma", status="confirmed", year=2026, number=1),
        "ck_invoices_number_requires_issued_invoice",
    )


def test_a_number_must_be_positive(db_session: Session, customer_id: UUID) -> None:
    _refuses(
        db_session,
        _draft(customer_id, status="issued", year=2026, number=0, issue_date=date(2026, 1, 5)),
        "ck_invoices_number_positive",
    )


def test_the_same_year_and_number_cannot_exist_twice(
    db_session: Session, customer_id: UUID
) -> None:
    """The partial unique index is a net, not the mechanism: the row lock in
    `InvoiceService.issue` is the primary defence, and this is what makes a failure of
    that defence observable rather than a silent duplicate."""
    _add(
        db_session,
        _draft(customer_id, status="issued", year=2026, number=1, issue_date=date(2026, 1, 5)),
    )
    _refuses(
        db_session,
        _draft(customer_id, status="issued", year=2026, number=1, issue_date=date(2026, 1, 6)),
        "uq_invoices_legal_entity_year_number",
    )


def test_two_drafts_do_not_collide_on_a_null_number(db_session: Session, customer_id: UUID) -> None:
    """`WHERE numero IS NOT NULL`: without the partial predicate every draft would be
    a duplicate of every other."""
    _add(db_session, _draft(customer_id))
    _add(db_session, _draft(customer_id))


def test_the_same_number_may_exist_in_two_different_years(
    db_session: Session, customer_id: UUID
) -> None:
    _add(
        db_session,
        _draft(customer_id, status="issued", year=2026, number=1, issue_date=date(2026, 1, 5)),
    )
    _add(
        db_session,
        _draft(customer_id, status="issued", year=2027, number=1, issue_date=date(2027, 1, 5)),
    )


def test_a_fiscal_reference_belongs_only_to_a_proforma(
    db_session: Session, customer_id: UUID
) -> None:
    _refuses(
        db_session,
        _draft(customer_id, reference="PROV-2026-0001"),
        "ck_invoices_reference_only_on_proforma",
    )


# --- immutability: spec 14.4 -------------------------------------------------------


def test_soft_deleting_an_issued_invoice_fails_even_in_raw_sql(
    db_session: Session, customer_id: UUID
) -> None:
    """The point of the check: `UPDATE invoices SET deleted_at = now()` is refused by
    Postgres, not by Python. A numbered row is a page in a register."""
    invoice = _draft(customer_id, status="issued", year=2026, number=1, issue_date=date(2026, 1, 5))
    _add(db_session, invoice)
    with pytest.raises(IntegrityError) as caught:
        db_session.execute(
            text("UPDATE invoices SET deleted_at = now() WHERE id = :id"), {"id": invoice.id}
        )
    assert "ck_invoices_no_delete_once_consumed" in str(caught.value)
    db_session.rollback()


def test_soft_deleting_a_consumed_proforma_also_fails(
    db_session: Session, customer_id: UUID
) -> None:
    """A consumed proforma is the antecedent of an immutable document, so it is not
    deletable either -- even though it never held a number."""
    proforma = _draft(customer_id, type="proforma", status="consumed")
    _add(db_session, proforma)
    with pytest.raises(IntegrityError):
        db_session.execute(
            text("UPDATE invoices SET deleted_at = now() WHERE id = :id"), {"id": proforma.id}
        )
    db_session.rollback()


def test_soft_deleting_a_draft_is_allowed(db_session: Session, customer_id: UUID) -> None:
    draft = _draft(customer_id)
    _add(db_session, draft)
    db_session.execute(
        text("UPDATE invoices SET deleted_at = now() WHERE id = :id"), {"id": draft.id}
    )
    db_session.flush()


def test_an_annulment_needs_both_a_date_and_a_reason(
    db_session: Session, customer_id: UUID
) -> None:
    _refuses(
        db_session,
        _draft(
            customer_id,
            status="cancelled",
            year=2026,
            number=1,
            issue_date=date(2026, 1, 5),
            cancelled_at=date(2026, 2, 1),
        ),
        "ck_invoices_cancellation_complete",
    )


def test_an_annulment_date_on_a_live_invoice_is_refused(
    db_session: Session, customer_id: UUID
) -> None:
    _refuses(
        db_session,
        _draft(
            customer_id,
            status="issued",
            year=2026,
            number=1,
            issue_date=date(2026, 1, 5),
            cancelled_at=date(2026, 2, 1),
            cancellation_reason="ripensamento",
        ),
        "ck_invoices_cancellation_complete",
    )


def test_a_collection_date_requires_the_collected_state(
    db_session: Session, customer_id: UUID
) -> None:
    _refuses(
        db_session,
        _draft(customer_id, payment_date=date(2026, 3, 1)),
        "ck_invoices_collection_requires_state",
    )


def test_a_snapshot_and_its_version_travel_together(db_session: Session, customer_id: UUID) -> None:
    _refuses(
        db_session,
        _draft(customer_id, snapshot={"versione": 1}),
        "ck_invoices_snapshot_together",
    )


# --- lines --------------------------------------------------------------------------


def _line(invoice_id: UUID, **overrides: object) -> InvoiceLine:
    payload: dict[str, object] = {
        "invoice_id": invoice_id,
        "line_number": 1,
        "description": "Consulenza",
        "quantity": Decimal("1.000000"),
        "unit_price": Decimal("100.000000"),
        "total_price": Decimal("100.00"),
        "vat_rate": Decimal("0.00"),
        "vat_nature": "N2.2",
    }
    payload.update(overrides)
    return InvoiceLine(**payload)


def test_to_zero_rate_line_must_carry_to_vat_nature(db_session: Session, customer_id: UUID) -> None:
    """The two SdI checks the spec names as a pair, as one table constraint: the
    invalid combination is not storable, therefore not exportable. Defending the
    invariant in the generator would leave it reachable from every other write path."""
    invoice = _draft(customer_id)
    _add(db_session, invoice)
    _refuses(
        db_session,
        _line(invoice.id, vat_nature=None),
        "ck_invoice_lines_vat_nature_agrees_with_rate",
    )


def test_to_not_zero_rate_line_must_not_carry_to_vat_nature(
    db_session: Session, customer_id: UUID
) -> None:
    invoice = _draft(customer_id)
    _add(db_session, invoice)
    _refuses(
        db_session,
        _line(invoice.id, vat_rate=Decimal("22.00"), vat_nature="N2.2"),
        "ck_invoice_lines_vat_nature_agrees_with_rate",
    )


def test_to_not_zero_rate_line_with_no_vat_nature_is_accepted(
    db_session: Session, customer_id: UUID
) -> None:
    invoice = _draft(customer_id)
    _add(db_session, invoice)
    _add(db_session, _line(invoice.id, vat_rate=Decimal("22.00"), vat_nature=None))


def test_two_lines_cannot_share_a_number_on_one_invoice(
    db_session: Session, customer_id: UUID
) -> None:
    invoice = _draft(customer_id)
    _add(db_session, invoice)
    _add(db_session, _line(invoice.id))
    _refuses(db_session, _line(invoice.id), "uq_invoice_lines_invoice_number")


def test_a_line_number_starts_at_one(db_session: Session, customer_id: UUID) -> None:
    invoice = _draft(customer_id)
    _add(db_session, invoice)
    _refuses(db_session, _line(invoice.id, line_number=0), "ck_invoice_lines_number_positive")


def test_a_unit_price_keeps_six_decimals_in_the_column(
    db_session: Session, customer_id: UUID
) -> None:
    """`Numeric(12, 6)` is the deliberate extension of the money convention: three
    hours at 33,333333 EUR/h is not expressible at two decimals, and the user would
    otherwise be forced to write a total that is not the product of what they
    declared."""
    invoice = _draft(customer_id)
    _add(db_session, invoice)
    line = _line(invoice.id, unit_price=Decimal("33.333333"))
    _add(db_session, line)
    db_session.expire(line)
    assert line.unit_price == Decimal("33.333333")


# --- the counter --------------------------------------------------------------------


def test_the_counter_is_keyed_by_year(db_session: Session) -> None:
    db_session.add(InvoiceCounter(year=2026, last_number=0))
    db_session.flush()
    db_session.add(InvoiceCounter(year=2026, last_number=5))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_the_counter_cannot_go_negative(db_session: Session) -> None:
    db_session.add(InvoiceCounter(year=2026, last_number=-1))
    with pytest.raises(IntegrityError) as caught:
        db_session.flush()
    assert "ck_invoice_counters_non_negative" in str(caught.value)
    db_session.rollback()


def test_the_proforma_sequence_exists_and_never_repeats(db_session: Session) -> None:
    """A `SEQUENCE` is the right tool *here* and the wrong one for the fiscal number,
    for the same property: `nextval()` does not roll back. On a proforma a gap means
    nothing, and in exchange the counter serialises nobody."""
    first = db_session.execute(text(f"SELECT nextval('{PROFORMA_SEQUENCE_NAME}')")).scalar_one()
    second = db_session.execute(text(f"SELECT nextval('{PROFORMA_SEQUENCE_NAME}')")).scalar_one()
    assert second == first + 1


# --- the accrual period: both ends or neither, and in order (ORB-61) ----------------


def test_an_accrual_period_needs_both_ends(db_session: Session, customer_id: UUID) -> None:
    """`accrual_from` without `accrual_to` is not a period, and the table says so:
    a fix-up script that writes one column is refused rather than leaving a half-period
    the XML writer would have to guess an end for."""
    _refuses(
        db_session,
        _draft(customer_id, accrual_from=date(2026, 8, 1)),
        "ck_invoices_accrual_together",
    )
    _refuses(
        db_session,
        _draft(customer_id, accrual_to=date(2026, 8, 31)),
        "ck_invoices_accrual_together",
    )


def test_an_accrual_period_cannot_end_before_it_starts(
    db_session: Session, customer_id: UUID
) -> None:
    _refuses(
        db_session,
        _draft(customer_id, accrual_from=date(2026, 8, 31), accrual_to=date(2026, 8, 1)),
        "ck_invoices_accrual_ordered",
    )


def test_a_one_day_accrual_period_is_a_period(db_session: Session, customer_id: UUID) -> None:
    _add(
        db_session,
        _draft(customer_id, accrual_from=date(2026, 8, 20), accrual_to=date(2026, 8, 20)),
    )


# --- the purchase order: its date, CIG and CUP hang on its number (REB-693) ---------


@pytest.mark.parametrize(
    "orphan",
    [
        {"purchase_order_date": date(2026, 10, 9)},
        {"purchase_order_tender_code": "Z0A1B2C3D4"},
        {"purchase_order_project_code": "J41E22000110005"},
    ],
)
def test_an_order_s_date_tender_code_or_project_code_needs_its_number(
    db_session: Session, customer_id: UUID, orphan: dict[str, object]
) -> None:
    """`IdDocumento` is the one mandatory child of `DatiOrdineAcquisto`: a CIG with no
    order to belong to is refused by the table, whatever path wrote it."""
    _refuses(db_session, _draft(customer_id, **orphan), "ck_invoices_order_needs_number")


def test_an_order_number_alone_or_with_everything_is_an_order(
    db_session: Session, customer_id: UUID
) -> None:
    _add(db_session, _draft(customer_id, purchase_order_number="8"))
    _add(
        db_session,
        _draft(
            customer_id,
            purchase_order_number="ODA-14",
            purchase_order_date=date(2026, 10, 9),
            purchase_order_tender_code="Z0A1B2C3D4",
            purchase_order_project_code="J41E22000110005",
        ),
    )
