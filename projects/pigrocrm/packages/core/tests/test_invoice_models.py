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
from pigrocrm.core.invoices.models import Invoice, InvoiceCounter, InvoiceLine


@pytest.fixture
def customer_id(db_session: Session) -> UUID:
    customer = Customer(ragione_sociale="Acme S.r.l.", nazione="IT")
    db_session.add(customer)
    db_session.flush()
    return customer.id


def _draft(customer_id: UUID, **overrides: object) -> Invoice:
    payload: dict[str, object] = {
        "customer_id": customer_id,
        "tipo": "fattura",
        "stato": "bozza",
        "tipo_documento": "TD01",
        "divisa": "EUR",
        "imponibile": Decimal("0.00"),
        "imposta": Decimal("0.00"),
        "bollo": Decimal("0.00"),
        "totale": Decimal("0.00"),
        "stato_pagamento": "da_incassare",
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


@pytest.mark.parametrize("stato", ["bozza", "emessa", "annullata"])
def test_a_fattura_may_hold_its_own_three_states(
    db_session: Session, customer_id: UUID, stato: str
) -> None:
    extra: dict[str, object] = {}
    if stato != "bozza":
        extra = {"anno": 2026, "numero": 1, "data_emissione": date(2026, 1, 5)}
    if stato == "annullata":
        extra |= {"annullata_il": date(2026, 2, 1), "motivo_annullamento": "importo errato"}
    _add(db_session, _draft(customer_id, stato=stato, **extra))


@pytest.mark.parametrize("stato", ["confermata", "consumata"])
def test_a_fattura_may_not_hold_a_proforma_state(
    db_session: Session, customer_id: UUID, stato: str
) -> None:
    _refuses(db_session, _draft(customer_id, stato=stato), "ck_invoices_tipo_stato")


def test_a_proforma_may_not_hold_the_emessa_state(db_session: Session, customer_id: UUID) -> None:
    _refuses(
        db_session,
        _draft(customer_id, tipo="proforma", stato="emessa"),
        "ck_invoices_tipo_stato",
    )


def test_an_unknown_tipo_is_refused(db_session: Session, customer_id: UUID) -> None:
    _refuses(db_session, _draft(customer_id, tipo="nota_credito"), "ck_invoices_tipo_stato")


# --- the number ---------------------------------------------------------------------


def test_a_number_without_a_year_is_refused(db_session: Session, customer_id: UUID) -> None:
    _refuses(
        db_session,
        _draft(customer_id, stato="emessa", numero=1, data_emissione=date(2026, 1, 5)),
        "ck_invoices_anno_numero_together",
    )


def test_a_draft_may_not_carry_a_number(db_session: Session, customer_id: UUID) -> None:
    """A draft has no number, so "a failed creation burned a number" is impossible by
    construction rather than by care."""
    _refuses(
        db_session,
        _draft(customer_id, anno=2026, numero=1),
        "ck_invoices_numero_requires_issued_fattura",
    )


def test_a_proforma_may_not_carry_a_number(db_session: Session, customer_id: UUID) -> None:
    _refuses(
        db_session,
        _draft(customer_id, tipo="proforma", stato="confermata", anno=2026, numero=1),
        "ck_invoices_numero_requires_issued_fattura",
    )


def test_a_number_must_be_positive(db_session: Session, customer_id: UUID) -> None:
    _refuses(
        db_session,
        _draft(customer_id, stato="emessa", anno=2026, numero=0, data_emissione=date(2026, 1, 5)),
        "ck_invoices_numero_positive",
    )


def test_the_same_year_and_number_cannot_exist_twice(
    db_session: Session, customer_id: UUID
) -> None:
    """The partial unique index is a net, not the mechanism: the row lock in
    `InvoiceService.issue` is the primary defence, and this is what makes a failure of
    that defence observable rather than a silent duplicate."""
    _add(
        db_session,
        _draft(customer_id, stato="emessa", anno=2026, numero=1, data_emissione=date(2026, 1, 5)),
    )
    _refuses(
        db_session,
        _draft(customer_id, stato="emessa", anno=2026, numero=1, data_emissione=date(2026, 1, 6)),
        "uq_invoices_azienda_anno_numero",
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
        _draft(customer_id, stato="emessa", anno=2026, numero=1, data_emissione=date(2026, 1, 5)),
    )
    _add(
        db_session,
        _draft(customer_id, stato="emessa", anno=2027, numero=1, data_emissione=date(2027, 1, 5)),
    )


def test_a_fiscal_reference_belongs_only_to_a_proforma(
    db_session: Session, customer_id: UUID
) -> None:
    _refuses(
        db_session,
        _draft(customer_id, riferimento="PROV-2026-0001"),
        "ck_invoices_riferimento_only_on_proforma",
    )


# --- immutability: spec 14.4 -------------------------------------------------------


def test_soft_deleting_an_issued_invoice_fails_even_in_raw_sql(
    db_session: Session, customer_id: UUID
) -> None:
    """The point of the check: `UPDATE invoices SET deleted_at = now()` is refused by
    Postgres, not by Python. A numbered row is a page in a register."""
    invoice = _draft(
        customer_id, stato="emessa", anno=2026, numero=1, data_emissione=date(2026, 1, 5)
    )
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
    proforma = _draft(customer_id, tipo="proforma", stato="consumata")
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
            stato="annullata",
            anno=2026,
            numero=1,
            data_emissione=date(2026, 1, 5),
            annullata_il=date(2026, 2, 1),
        ),
        "ck_invoices_annullamento_complete",
    )


def test_an_annulment_date_on_a_live_invoice_is_refused(
    db_session: Session, customer_id: UUID
) -> None:
    _refuses(
        db_session,
        _draft(
            customer_id,
            stato="emessa",
            anno=2026,
            numero=1,
            data_emissione=date(2026, 1, 5),
            annullata_il=date(2026, 2, 1),
            motivo_annullamento="ripensamento",
        ),
        "ck_invoices_annullamento_complete",
    )


def test_a_collection_date_requires_the_collected_state(
    db_session: Session, customer_id: UUID
) -> None:
    _refuses(
        db_session,
        _draft(customer_id, data_incasso=date(2026, 3, 1)),
        "ck_invoices_incasso_requires_state",
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
        "numero_linea": 1,
        "descrizione": "Consulenza",
        "quantita": Decimal("1.000000"),
        "prezzo_unitario": Decimal("100.000000"),
        "prezzo_totale": Decimal("100.00"),
        "aliquota_iva": Decimal("0.00"),
        "natura": "N2.2",
    }
    payload.update(overrides)
    return InvoiceLine(**payload)


def test_a_zero_rate_line_must_carry_a_natura(db_session: Session, customer_id: UUID) -> None:
    """The two SdI checks the spec names as a pair, as one table constraint: the
    invalid combination is not storable, therefore not exportable. Defending the
    invariant in the generator would leave it reachable from every other write path."""
    invoice = _draft(customer_id)
    _add(db_session, invoice)
    _refuses(
        db_session,
        _line(invoice.id, natura=None),
        "ck_invoice_lines_natura_agrees_with_rate",
    )


def test_a_non_zero_rate_line_must_not_carry_a_natura(
    db_session: Session, customer_id: UUID
) -> None:
    invoice = _draft(customer_id)
    _add(db_session, invoice)
    _refuses(
        db_session,
        _line(invoice.id, aliquota_iva=Decimal("22.00"), natura="N2.2"),
        "ck_invoice_lines_natura_agrees_with_rate",
    )


def test_a_non_zero_rate_line_with_no_natura_is_accepted(
    db_session: Session, customer_id: UUID
) -> None:
    invoice = _draft(customer_id)
    _add(db_session, invoice)
    _add(db_session, _line(invoice.id, aliquota_iva=Decimal("22.00"), natura=None))


def test_two_lines_cannot_share_a_number_on_one_invoice(
    db_session: Session, customer_id: UUID
) -> None:
    invoice = _draft(customer_id)
    _add(db_session, invoice)
    _add(db_session, _line(invoice.id))
    _refuses(db_session, _line(invoice.id), "uq_invoice_lines_invoice_numero")


def test_a_line_number_starts_at_one(db_session: Session, customer_id: UUID) -> None:
    invoice = _draft(customer_id)
    _add(db_session, invoice)
    _refuses(db_session, _line(invoice.id, numero_linea=0), "ck_invoice_lines_numero_positive")


def test_a_unit_price_keeps_six_decimals_in_the_column(
    db_session: Session, customer_id: UUID
) -> None:
    """`Numeric(12, 6)` is the deliberate extension of the money convention: three
    hours at 33,333333 EUR/h is not expressible at two decimals, and the user would
    otherwise be forced to write a total that is not the product of what they
    declared."""
    invoice = _draft(customer_id)
    _add(db_session, invoice)
    line = _line(invoice.id, prezzo_unitario=Decimal("33.333333"))
    _add(db_session, line)
    db_session.expire(line)
    assert line.prezzo_unitario == Decimal("33.333333")


# --- the counter --------------------------------------------------------------------


def test_the_counter_is_keyed_by_year(db_session: Session) -> None:
    db_session.add(InvoiceCounter(anno=2026, ultimo_numero=0))
    db_session.flush()
    db_session.add(InvoiceCounter(anno=2026, ultimo_numero=5))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_the_counter_cannot_go_negative(db_session: Session) -> None:
    db_session.add(InvoiceCounter(anno=2026, ultimo_numero=-1))
    with pytest.raises(IntegrityError) as caught:
        db_session.flush()
    assert "ck_invoice_counters_non_negative" in str(caught.value)
    db_session.rollback()


def test_the_proforma_sequence_exists_and_never_repeats(db_session: Session) -> None:
    """A `SEQUENCE` is the right tool *here* and the wrong one for the fiscal number,
    for the same property: `nextval()` does not roll back. On a proforma a gap means
    nothing, and in exchange the counter serialises nobody."""
    first = db_session.execute(text("SELECT nextval('proforma_riferimento_seq')")).scalar_one()
    second = db_session.execute(text("SELECT nextval('proforma_riferimento_seq')")).scalar_one()
    assert second == first + 1


# --- the accrual period: both ends or neither, and in order (ORB-61) ----------------


def test_an_accrual_period_needs_both_ends(db_session: Session, customer_id: UUID) -> None:
    """`competenza_da` without `competenza_a` is not a period, and the table says so:
    a fix-up script that writes one column is refused rather than leaving a half-period
    the XML writer would have to guess an end for."""
    _refuses(
        db_session,
        _draft(customer_id, competenza_da=date(2026, 8, 1)),
        "ck_invoices_competenza_together",
    )
    _refuses(
        db_session,
        _draft(customer_id, competenza_a=date(2026, 8, 31)),
        "ck_invoices_competenza_together",
    )


def test_an_accrual_period_cannot_end_before_it_starts(
    db_session: Session, customer_id: UUID
) -> None:
    _refuses(
        db_session,
        _draft(customer_id, competenza_da=date(2026, 8, 31), competenza_a=date(2026, 8, 1)),
        "ck_invoices_competenza_ordered",
    )


def test_a_one_day_accrual_period_is_a_period(db_session: Session, customer_id: UUID) -> None:
    _add(
        db_session,
        _draft(customer_id, competenza_da=date(2026, 8, 20), competenza_a=date(2026, 8, 20)),
    )
