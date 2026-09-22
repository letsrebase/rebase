"""Drafts and proformas: everything that happens before a number exists.

A draft has no number at all, which is why "a failed creation burned a number" is not
a scenario in this file -- it is impossible by construction. The number appears only in
`issue`, which the next task covers.
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.errors import (
    Conflict,
    ImmutableField,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from pigrocrm.core.fiscal.schemas import (
    DEFAULT_RIFERIMENTO_NORMATIVO,
    RIFERIMENTO_NORMATIVO_EXTRA_UE,
    FiscalProfileUpsert,
)
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.naming import RIFERIMENTO_PROFORMA_RE
from pigrocrm.core.invoices.schemas import (
    InvoiceCreate,
    InvoiceLineIn,
    InvoiceListQuery,
    InvoiceUpdate,
    PaymentState,
)
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="system", role="admin")
COLLABORATORE = Actor(id=None, type="user", role="collaboratore")
READONLY = Actor(id=None, type="user", role="readonly")


@pytest.fixture
def storage(tmp_path) -> LocalFileStorage:  # type: ignore[no-untyped-def]
    return LocalFileStorage(tmp_path / "documents")


@pytest.fixture
def service(db_session: Session, storage: LocalFileStorage) -> InvoiceService:
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)
    return InvoiceService(db_session, storage)


@pytest.fixture
def customer_id(db_session: Session) -> UUID:
    customer = Customer(
        ragione_sociale="Acme S.r.l.",
        partita_iva="12345678901",
        codice_sdi="ABCDEFG",
        indirizzo="Corso Italia 5",
        cap="00100",
        comune="Roma",
        provincia="RM",
        nazione="IT",
    )
    db_session.add(customer)
    db_session.flush()
    return customer.id


def _line(descrizione: str = "Consulenza", prezzo: str = "1000.00", **kw: object) -> InvoiceLineIn:
    payload: dict[str, object] = {
        "descrizione": descrizione,
        "prezzo_unitario": Decimal(prezzo),
    }
    payload.update(kw)
    return InvoiceLineIn(**payload)  # type: ignore[arg-type]


# --- creation -----------------------------------------------------------------------


def test_a_new_invoice_is_a_draft_with_no_number(
    service: InvoiceService, customer_id: UUID
) -> None:
    invoice = service.create(InvoiceCreate(customer_id=customer_id), ADMIN)
    assert invoice.tipo == "fattura"
    assert invoice.stato == "bozza"
    assert invoice.anno is None
    assert invoice.numero is None
    assert invoice.riferimento is None
    assert invoice.tipo_documento == "TD01"
    assert invoice.divisa == "EUR"
    assert invoice.totale == Decimal("0.00")
    assert invoice.stato_pagamento == "da_incassare"


def test_a_new_proforma_gets_a_reference_no_fiscal_regex_can_accept(
    service: InvoiceService, customer_id: UUID
) -> None:
    proforma = service.create(
        InvoiceCreate(customer_id=customer_id, tipo="proforma", righe=[_line()]), ADMIN
    )
    assert proforma.stato == "bozza"
    assert proforma.numero is None
    assert RIFERIMENTO_PROFORMA_RE.fullmatch(proforma.riferimento or "")


def test_two_proformas_get_different_references(service: InvoiceService, customer_id: UUID) -> None:
    first = service.create(InvoiceCreate(customer_id=customer_id, tipo="proforma"), ADMIN)
    second = service.create(InvoiceCreate(customer_id=customer_id, tipo="proforma"), ADMIN)
    assert first.riferimento != second.riferimento


def test_creation_computes_and_stores_the_totals(
    service: InvoiceService, customer_id: UUID
) -> None:
    """Totals are computed by the service and stored, never recomputed by a client:
    a total computed in the browser is the structural defect this slice removes."""
    invoice = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            righe=[
                _line("Consulenza", "33.333333", quantita=Decimal("3.000000")),
                _line("Sconto", "-10.00"),
            ],
        ),
        ADMIN,
    )
    assert invoice.imponibile == Decimal("90.00")
    assert invoice.imposta == Decimal("0.00")
    assert invoice.totale == Decimal("90.00")
    assert invoice.bollo == Decimal("2.00")


def test_the_stamp_duty_is_stored_but_stays_out_of_the_total(
    service: InvoiceService, customer_id: UUID
) -> None:
    invoice = service.create(
        InvoiceCreate(customer_id=customer_id, righe=[_line("Consulenza", "1000.00")]), ADMIN
    )
    assert invoice.bollo == Decimal("2.00")
    assert invoice.totale == Decimal("1000.00")


def test_the_regime_decides_the_line_natura(service: InvoiceService, customer_id: UUID) -> None:
    invoice = service.create(InvoiceCreate(customer_id=customer_id, righe=[_line()]), ADMIN)
    (riga,) = service.lines(invoice.id, ADMIN)
    assert riga.numero_linea == 1
    assert riga.aliquota_iva == Decimal("0.00")
    assert riga.natura == "N2.2"
    assert riga.riferimento_normativo == DEFAULT_RIFERIMENTO_NORMATIVO


def _non_resident_customer(db_session: Session) -> UUID:
    """The shape of ORB-32: a British company, no SDI code, no PEC, a postcode that is
    not five digits and no province."""
    customer = Customer(
        ragione_sociale="Example Ltd",
        partita_iva="GB123456789",
        indirizzo="1 Old Street",
        cap="EC1V 9HL",
        comune="London",
        provincia="",
        nazione="GB",
    )
    db_session.add(customer)
    db_session.flush()
    return customer.id


def test_a_non_resident_customer_gets_natura_n2_1_and_the_7_ter_reference(
    service: InvoiceService, db_session: Session
) -> None:
    """ORB-32. The regime reads the customer's country: a service to a business outside
    Italy is outside the scope of Italian VAT (art. 7-ter DPR 633/1972), so the line
    carries `N2.1` and the 7-ter reference, not the domestic `N2.2` declaration."""
    customer_id = _non_resident_customer(db_session)
    invoice = service.create(
        InvoiceCreate(customer_id=customer_id, tipo="proforma", righe=[_line()]), ADMIN
    )
    (riga,) = service.lines(invoice.id, ADMIN)
    assert riga.aliquota_iva == Decimal("0.00")
    assert riga.natura == "N2.1"
    assert riga.riferimento_normativo == RIFERIMENTO_NORMATIVO_EXTRA_UE
    assert "7-ter" in riga.riferimento_normativo


def test_replacing_the_lines_reads_the_customer_s_country_again(
    service: InvoiceService, db_session: Session
) -> None:
    """The natura is decided when the lines are computed, so a proforma created before
    this fix, or before the customer's country was corrected, is repaired by replacing
    its lines: that is the remedy for the proforma in ORB-32."""
    customer_id = _non_resident_customer(db_session)
    invoice = service.create(InvoiceCreate(customer_id=customer_id, righe=[_line()]), ADMIN)
    customer = db_session.get(Customer, customer_id)
    assert customer is not None
    customer.nazione = "IT"
    customer.provincia = "RM"
    customer.cap = "00100"
    db_session.flush()

    service.replace_lines(invoice.id, [_line()], ADMIN)
    (riga,) = service.lines(invoice.id, ADMIN)
    assert riga.natura == "N2.2"
    assert riga.riferimento_normativo == DEFAULT_RIFERIMENTO_NORMATIVO


def test_a_non_zero_rate_under_the_forfettario_is_refused_by_field_name(
    service: InvoiceService, customer_id: UUID
) -> None:
    with pytest.raises(ValidationFailed) as caught:
        service.create(
            InvoiceCreate(
                customer_id=customer_id,
                righe=[_line(aliquota_iva=Decimal("22.00"))],
            ),
            ADMIN,
        )
    assert caught.value.details["field"] == "aliquota_iva"


def test_an_unknown_customer_is_not_found_rather_than_a_foreign_key_violation(
    service: InvoiceService,
) -> None:
    with pytest.raises(NotFound):
        service.create(InvoiceCreate(customer_id=uuid4()), ADMIN)


def test_an_unknown_deal_is_not_found_even_though_the_column_is_nullable(
    service: InvoiceService, customer_id: UUID
) -> None:
    """A nullable FK is skipped only when the caller supplies nothing, never when the
    caller supplies a value -- the defect `deals.owner_id` shipped with."""
    with pytest.raises(NotFound):
        service.create(InvoiceCreate(customer_id=customer_id, deal_id=uuid4()), ADMIN)


def test_a_deal_belonging_to_another_customer_is_refused(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    # `deals.pipeline_stage_id` is NOT NULL: `DealService.create` fills it in from
    # `PipelineService.default_stage()` for every caller, so a raw `Deal(...)` built
    # by hand (bypassing the service, as this test deliberately does) needs a real
    # stage id or the insert fails on the column, not on anything this test means to
    # exercise.
    stage = PipelineService(db_session).seed_defaults(ADMIN)[0]
    other = Customer(ragione_sociale="Altro", nazione="IT")
    db_session.add(other)
    db_session.flush()
    deal = Deal(nome="Progetto", customer_id=other.id, pipeline_stage_id=stage.id)
    db_session.add(deal)
    db_session.flush()
    with pytest.raises(ValidationFailed) as caught:
        service.create(InvoiceCreate(customer_id=customer_id, deal_id=deal.id), ADMIN)
    assert caught.value.details["field"] == "deal_id"


def test_creation_without_a_fiscal_profile_says_which_configuration_is_missing(
    db_session: Session, storage: LocalFileStorage, customer_id: UUID
) -> None:
    with pytest.raises(NotFound) as caught:
        InvoiceService(db_session, storage).create(InvoiceCreate(customer_id=customer_id), ADMIN)
    assert caught.value.details["entity"] == "fiscal_profile"


def test_a_readonly_actor_cannot_create(service: InvoiceService, customer_id: UUID) -> None:
    with pytest.raises(PermissionDenied):
        service.create(InvoiceCreate(customer_id=customer_id), READONLY)


def test_a_collaboratore_may_create_a_draft(service: InvoiceService, customer_id: UUID) -> None:
    """Drafting is ordinary entity writing; only consuming a register number is not
    (spec 11)."""
    assert service.create(InvoiceCreate(customer_id=customer_id), COLLABORATORE).stato == "bozza"


def test_creation_records_an_activity(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    invoice = service.create(InvoiceCreate(customer_id=customer_id), ADMIN)
    entries = ActivityService(db_session).timeline("invoice", invoice.id)
    assert [entry.kind for entry in entries] == ["created"]


# --- the line editor ----------------------------------------------------------------


def test_replacing_the_lines_renumbers_them_from_one_contiguously(
    service: InvoiceService, customer_id: UUID
) -> None:
    """Contiguity is a service invariant -- no single-row CHECK can see the other rows
    -- and this is where it is maintained: the whole list is renumbered from 1."""
    invoice = service.create(
        InvoiceCreate(customer_id=customer_id, righe=[_line("A"), _line("B"), _line("C")]), ADMIN
    )
    service.replace_lines(invoice.id, [_line("Solo questa", "50.00")], ADMIN)
    righe = service.lines(invoice.id, ADMIN)
    assert [r.numero_linea for r in righe] == [1]
    assert righe[0].descrizione == "Solo questa"


def test_replacing_the_lines_recomputes_the_totals(
    service: InvoiceService, customer_id: UUID
) -> None:
    invoice = service.create(
        InvoiceCreate(customer_id=customer_id, righe=[_line("A", "1000.00")]), ADMIN
    )
    updated = service.replace_lines(invoice.id, [_line("A", "10.00")], ADMIN)
    assert updated.totale == Decimal("10.00")
    # Below the threshold now, so the duty disappears with the amount.
    assert updated.bollo == Decimal("0.00")


def test_replacing_the_lines_with_an_empty_list_is_allowed_on_a_draft(
    service: InvoiceService, customer_id: UUID
) -> None:
    """A draft with no lines is a legitimate intermediate state; only *emission*
    requires at least one line."""
    invoice = service.create(InvoiceCreate(customer_id=customer_id, righe=[_line()]), ADMIN)
    assert service.replace_lines(invoice.id, [], ADMIN).totale == Decimal("0.00")
    assert service.lines(invoice.id, ADMIN) == []


def test_bulk_replacement_is_how_an_optional_numeric_field_gets_cleared(
    service: InvoiceService, customer_id: UUID
) -> None:
    """Written when A14 made bulk replacement the *only* way to clear
    `sconto_importo`; kept now that task 4B-1 has closed A14, because lines still have
    no `Update` schema and replacement is still how a line editor saves."""
    invoice = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            righe=[_line("A", "100.00", sconto_importo=Decimal("10.00"), unita_misura="ore")],
        ),
        ADMIN,
    )
    service.replace_lines(invoice.id, [_line("A", "100.00")], ADMIN)
    (riga,) = service.lines(invoice.id, ADMIN)
    assert riga.sconto_importo is None
    assert riga.unita_misura is None
    assert riga.prezzo_totale == Decimal("100.00")


def test_a_percentage_and_a_fixed_discount_compose_in_a_defined_order(
    service: InvoiceService, customer_id: UUID
) -> None:
    invoice = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            righe=[
                _line(
                    "A",
                    "100.00",
                    quantita=Decimal("2.000000"),
                    sconto_percentuale=Decimal("10.00"),
                    sconto_importo=Decimal("5.00"),
                )
            ],
        ),
        ADMIN,
    )
    assert invoice.totale == Decimal("175.00")


def test_more_lines_than_the_bound_are_refused_by_the_schema(
    service: InvoiceService, customer_id: UUID
) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        InvoiceCreate(customer_id=customer_id, righe=[_line()] * 201)


def test_replacing_lines_records_an_activity(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    invoice = service.create(InvoiceCreate(customer_id=customer_id), ADMIN)
    service.replace_lines(invoice.id, [_line(), _line("B")], ADMIN)
    kinds = [e.kind for e in ActivityService(db_session).timeline("invoice", invoice.id)]
    assert "lines_replaced" in kinds


# --- editing what may be edited -----------------------------------------------------


def test_the_causale_and_the_notes_are_editable_on_a_draft(
    service: InvoiceService, customer_id: UUID
) -> None:
    invoice = service.create(InvoiceCreate(customer_id=customer_id, causale="Bozza"), ADMIN)
    updated = service.update(
        invoice.id, InvoiceUpdate(causale="Consulenza agosto", note_interne="da rileggere"), ADMIN
    )
    assert updated.causale == "Consulenza agosto"
    assert updated.note_interne == "da rileggere"


def test_an_empty_string_clears_a_text_column(service: InvoiceService, customer_id: UUID) -> None:
    """The only clear-it spelling that exists, and it works because both editable
    native columns are text-shaped."""
    invoice = service.create(InvoiceCreate(customer_id=customer_id, causale="Bozza"), ADMIN)
    assert service.update(invoice.id, InvoiceUpdate(causale=""), ADMIN).causale == ""


def test_an_omitted_key_clears_nothing(service: InvoiceService, customer_id: UUID) -> None:
    invoice = service.create(
        InvoiceCreate(customer_id=customer_id, causale="Bozza", note_interne="nota"), ADMIN
    )
    assert service.update(invoice.id, InvoiceUpdate(causale="Altro"), ADMIN).note_interne == "nota"


# --- proforma confirmation ----------------------------------------------------------


def test_confirming_a_proforma_moves_it_out_of_draft(
    service: InvoiceService, customer_id: UUID
) -> None:
    proforma = service.create(
        InvoiceCreate(customer_id=customer_id, tipo="proforma", righe=[_line()]), ADMIN
    )
    assert service.confirm_proforma(proforma.id, ADMIN).stato == "confermata"


def test_confirming_a_proforma_with_no_lines_is_refused(
    service: InvoiceService, customer_id: UUID
) -> None:
    proforma = service.create(InvoiceCreate(customer_id=customer_id, tipo="proforma"), ADMIN)
    with pytest.raises(ValidationFailed) as caught:
        service.confirm_proforma(proforma.id, ADMIN)
    assert caught.value.details["field"] == "righe"


def test_a_fattura_cannot_be_confirmed(service: InvoiceService, customer_id: UUID) -> None:
    invoice = service.create(InvoiceCreate(customer_id=customer_id, righe=[_line()]), ADMIN)
    with pytest.raises(Conflict):
        service.confirm_proforma(invoice.id, ADMIN)


def test_a_confirmed_proforma_can_still_be_edited(
    service: InvoiceService, customer_id: UUID
) -> None:
    """A proforma is entirely mutable until it is consumed -- that is what it is for
    (spec 5): agree the amount before consuming a number."""
    proforma = service.create(
        InvoiceCreate(customer_id=customer_id, tipo="proforma", righe=[_line()]), ADMIN
    )
    service.confirm_proforma(proforma.id, ADMIN)
    assert service.replace_lines(proforma.id, [_line("B", "20.00")], ADMIN).totale == Decimal(
        "20.00"
    )


# --- deletion -----------------------------------------------------------------------


def test_a_draft_can_be_soft_deleted_and_disappears_from_the_list(
    service: InvoiceService, customer_id: UUID
) -> None:
    invoice = service.create(InvoiceCreate(customer_id=customer_id), ADMIN)
    service.soft_delete(invoice.id, ADMIN)
    assert invoice.id not in [item.id for item in service.list(InvoiceListQuery(), ADMIN).items]
    with pytest.raises(NotFound):
        service.get(invoice.id, ADMIN)


def test_soft_deleting_a_draft_also_removes_its_lines_from_the_reader(
    service: InvoiceService, customer_id: UUID
) -> None:
    invoice = service.create(InvoiceCreate(customer_id=customer_id, righe=[_line()]), ADMIN)
    service.soft_delete(invoice.id, ADMIN)
    with pytest.raises(NotFound):
        service.lines(invoice.id, ADMIN)


# --- payment ------------------------------------------------------------------------


def test_the_payment_state_cannot_be_set_on_a_draft(
    service: InvoiceService, customer_id: UUID
) -> None:
    """There is nothing to collect on a document that was never issued."""
    invoice = service.create(InvoiceCreate(customer_id=customer_id, righe=[_line()]), ADMIN)
    with pytest.raises(Conflict):
        service.set_payment_state(
            invoice.id,
            PaymentState(stato_pagamento="incassato", data_incasso=date(2026, 9, 1)),
            ADMIN,
        )


def test_marking_collected_without_a_date_is_refused(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    """ "Collected with no date" and "a date but not collected" are both nonsense, so
    a single method takes both and checks their agreement."""
    invoice = _issued_row(db_session, customer_id)
    with pytest.raises(ValidationFailed) as caught:
        service.set_payment_state(invoice.id, PaymentState(stato_pagamento="incassato"), ADMIN)
    assert caught.value.details["field"] == "data_incasso"


def test_going_back_to_uncollected_clears_the_date(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    invoice = _issued_row(db_session, customer_id)
    service.set_payment_state(
        invoice.id, PaymentState(stato_pagamento="incassato", data_incasso=date(2026, 9, 1)), ADMIN
    )
    back = service.set_payment_state(
        invoice.id, PaymentState(stato_pagamento="da_incassare"), ADMIN
    )
    assert back.stato_pagamento == "da_incassare"
    assert back.data_incasso is None


def test_a_collaboratore_may_record_a_payment(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    """Spec 11: `set_payment_state` is the one invoice write a collaborator -- and an
    agent -- may perform, because collecting is a subsequent fact, not part of the
    document."""
    invoice = _issued_row(db_session, customer_id)
    assert (
        service.set_payment_state(
            invoice.id,
            PaymentState(stato_pagamento="incassato", data_incasso=date(2026, 9, 1)),
            COLLABORATORE,
        ).stato_pagamento
        == "incassato"
    )


def _issued_row(db_session: Session, customer_id: UUID, **overrides: object) -> Invoice:
    """An already-issued row inserted directly, so this file's payment tests do not
    depend on `issue` (next task) being written yet. `overrides` are applied before the
    flush, since `(anno, numero)` is unique and a second row needs its own number."""
    fields: dict[str, object] = dict(
        customer_id=customer_id,
        tipo="fattura",
        stato="emessa",
        anno=2026,
        numero=1,
        data_emissione=date(2026, 8, 20),
        tipo_documento="TD01",
        divisa="EUR",
        imponibile=Decimal("100.00"),
        imposta=Decimal("0.00"),
        bollo=Decimal("2.00"),
        totale=Decimal("100.00"),
        stato_pagamento="da_incassare",
        snapshot={"versione": 1},
        snapshot_versione=1,
        custom_fields={},
    )
    fields.update(overrides)
    invoice = Invoice(**fields)  # type: ignore[arg-type]
    db_session.add(invoice)
    db_session.flush()
    return invoice


# --- editing what may not be edited -------------------------------------------------


def test_the_lines_of_an_issued_invoice_cannot_be_replaced(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    invoice = _issued_row(db_session, customer_id)
    with pytest.raises(ImmutableField) as caught:
        service.replace_lines(invoice.id, [_line()], ADMIN)
    assert caught.value.details["field"] == "righe"


def test_the_causale_of_an_issued_invoice_cannot_be_changed(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    invoice = _issued_row(db_session, customer_id)
    with pytest.raises(ImmutableField) as caught:
        service.update(invoice.id, InvoiceUpdate(causale="ripensamento"), ADMIN)
    assert caught.value.details["field"] == "causale"


def test_the_internal_notes_of_an_issued_invoice_can_still_be_changed(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    """They appear on no artefact, so they are not part of the document (spec 4)."""
    invoice = _issued_row(db_session, customer_id)
    assert (
        service.update(invoice.id, InvoiceUpdate(note_interne="sollecitato"), ADMIN).note_interne
        == "sollecitato"
    )


def test_an_issued_invoice_cannot_be_soft_deleted(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    invoice = _issued_row(db_session, customer_id)
    with pytest.raises(Conflict):
        service.soft_delete(invoice.id, ADMIN)


# --- listing ------------------------------------------------------------------------


def test_the_list_finds_the_fattura_a_consumed_proforma_was_issued_as(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    """The fattura points back at its proforma and the proforma points at nothing, so
    the consumed proforma's page finds its fattura by asking the list for the row whose
    `origine_proforma_id` is its own id (ORB-134). Another fattura, or another
    proforma's fattura, does not answer."""
    proforma = service.create(InvoiceCreate(customer_id=customer_id, tipo="proforma"), ADMIN)
    other = service.create(InvoiceCreate(customer_id=customer_id, tipo="proforma"), ADMIN)
    from_proforma = _issued_row(db_session, customer_id, origine_proforma_id=proforma.id)
    _issued_row(db_session, customer_id, numero=2, origine_proforma_id=other.id)
    standalone = _issued_row(db_session, customer_id, numero=3)

    found = service.list(InvoiceListQuery(origine_proforma_id=proforma.id), ADMIN).items
    assert [i.id for i in found] == [from_proforma.id]
    assert standalone.id not in {i.id for i in found}
    assert service.list(InvoiceListQuery(origine_proforma_id=uuid4()), ADMIN).items == []


def test_the_list_can_leave_out_the_proformas_that_became_a_fattura(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    """Under «Tutte» the web showed 2026/18 and the proforma it came from as two rows
    with the same customer, period and total (ORB-169). `escludi_consumate` drops the
    consumed ones and nothing else; asked by `stato`, they still answer."""
    draft = service.create(InvoiceCreate(customer_id=customer_id, tipo="proforma"), ADMIN)
    consumed = service.create(InvoiceCreate(customer_id=customer_id, tipo="proforma"), ADMIN)
    db_session.get(Invoice, consumed.id).stato = "consumata"  # type: ignore[union-attr]
    fattura = _issued_row(db_session, customer_id, origine_proforma_id=consumed.id)
    db_session.flush()

    everything = {i.id for i in service.list(InvoiceListQuery(), ADMIN).items}
    assert {draft.id, consumed.id, fattura.id} <= everything
    trimmed = {i.id for i in service.list(InvoiceListQuery(escludi_consumate=True), ADMIN).items}
    assert trimmed == everything - {consumed.id}
    by_state = service.list(InvoiceListQuery(stato="consumata"), ADMIN).items
    assert [i.id for i in by_state] == [consumed.id]


def test_the_list_filters_by_tipo_stato_year_and_payment(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    draft = service.create(InvoiceCreate(customer_id=customer_id), ADMIN)
    proforma = service.create(InvoiceCreate(customer_id=customer_id, tipo="proforma"), ADMIN)
    issued = _issued_row(db_session, customer_id)

    assert {i.id for i in service.list(InvoiceListQuery(tipo="proforma"), ADMIN).items} == {
        proforma.id
    }
    assert {i.id for i in service.list(InvoiceListQuery(stato="bozza"), ADMIN).items} == {
        draft.id,
        proforma.id,
    }
    assert {i.id for i in service.list(InvoiceListQuery(anno=2026), ADMIN).items} == {issued.id}
    assert {
        i.id for i in service.list(InvoiceListQuery(stato_pagamento="incassato"), ADMIN).items
    } == set()


def test_the_list_under_da_incassare_answers_only_the_receivables(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    """REB-325: every row is born `da_incassare`, so an equality on the column listed
    drafts and proformas as unpaid. The filter now means what the dashboard's «Da
    incassare» card means (`_receivable_filter`): an issued fattura nobody has paid."""
    service.create(InvoiceCreate(customer_id=customer_id), ADMIN)
    service.create(InvoiceCreate(customer_id=customer_id, tipo="proforma"), ADMIN)
    owed = _issued_row(db_session, customer_id)
    _issued_row(
        db_session,
        customer_id,
        numero=2,
        stato_pagamento="incassato",
        data_incasso=date(2026, 8, 25),
    )
    _issued_row(
        db_session,
        customer_id,
        numero=3,
        stato="annullata",
        annullata_il=date(2026, 8, 21),
        motivo_annullamento="storno",
    )

    receivable = service.list(InvoiceListQuery(stato_pagamento="da_incassare"), ADMIN).items
    assert [i.id for i in receivable] == [owed.id]


def test_the_list_paginates_on_the_uuid_v7_cursor(
    service: InvoiceService, customer_id: UUID
) -> None:
    for _ in range(3):
        service.create(InvoiceCreate(customer_id=customer_id), ADMIN)
    first = service.list(InvoiceListQuery(limit=2), ADMIN)
    assert len(first.items) == 2
    assert first.next_cursor is not None
    second = service.list(InvoiceListQuery(limit=2, cursor=first.next_cursor), ADMIN)
    assert len(second.items) == 1
    assert second.next_cursor is None


def test_list_is_the_last_method_of_the_service_class() -> None:
    """`def list` rebinds `list` in the class namespace, so a later method annotated
    `-> list[...]` fails at import on Python 3.13. `lines` returns
    `list[InvoiceLineRead]`, so it has to sit above `list`, and this pins the order
    rather than trusting a comment."""
    names = [
        name
        for name, value in vars(InvoiceService).items()
        if callable(value) and not name.startswith("__")
    ]
    assert names[-1] == "list"
    assert names.index("lines") < names.index("list")


# --- the accrual period (ORB-61) ----------------------------------------------------


def test_a_draft_may_carry_an_accrual_period(service: InvoiceService, customer_id: UUID) -> None:
    """The month the work belongs to used to live only in the free text of the causale
    ("FDE, agosto 2026"); now it is two dates the XML and the P&L can read."""
    invoice = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            competenza_da=date(2026, 8, 1),
            competenza_a=date(2026, 8, 31),
        ),
        ADMIN,
    )
    assert invoice.competenza_da == date(2026, 8, 1)
    assert invoice.competenza_a == date(2026, 8, 31)
    assert service.create(InvoiceCreate(customer_id=customer_id), ADMIN).competenza_da is None


def test_half_an_accrual_period_is_refused_by_field_name(
    service: InvoiceService, customer_id: UUID
) -> None:
    with pytest.raises(ValidationFailed) as caught:
        service.create(
            InvoiceCreate(customer_id=customer_id, competenza_da=date(2026, 8, 1)), ADMIN
        )
    assert caught.value.details["field"] == "competenza_a"
    with pytest.raises(ValidationFailed) as caught:
        service.create(
            InvoiceCreate(customer_id=customer_id, competenza_a=date(2026, 8, 31)), ADMIN
        )
    assert caught.value.details["field"] == "competenza_da"


def test_an_inverted_accrual_period_is_refused(service: InvoiceService, customer_id: UUID) -> None:
    with pytest.raises(ValidationFailed) as caught:
        service.create(
            InvoiceCreate(
                customer_id=customer_id,
                competenza_da=date(2026, 8, 31),
                competenza_a=date(2026, 8, 1),
            ),
            ADMIN,
        )
    assert caught.value.details["field"] == "competenza_a"


def test_the_accrual_period_is_editable_on_a_draft_one_end_at_a_time(
    service: InvoiceService, customer_id: UUID
) -> None:
    """The rule is checked on the merged row, not on the request: completing a period
    whose other end is already stored is one field, and so is moving one end."""
    invoice = service.create(InvoiceCreate(customer_id=customer_id), ADMIN)
    with pytest.raises(ValidationFailed) as caught:
        service.update(invoice.id, InvoiceUpdate(competenza_da=date(2026, 8, 1)), ADMIN)
    assert caught.value.details["field"] == "competenza_a"

    updated = service.update(
        invoice.id,
        InvoiceUpdate(competenza_da=date(2026, 8, 1), competenza_a=date(2026, 8, 31)),
        ADMIN,
    )
    assert (updated.competenza_da, updated.competenza_a) == (date(2026, 8, 1), date(2026, 8, 31))
    moved = service.update(invoice.id, InvoiceUpdate(competenza_a=date(2026, 9, 30)), ADMIN)
    assert (moved.competenza_da, moved.competenza_a) == (date(2026, 8, 1), date(2026, 9, 30))
    with pytest.raises(ValidationFailed):
        service.update(invoice.id, InvoiceUpdate(competenza_a=date(2026, 7, 31)), ADMIN)


def test_the_accrual_period_is_cleared_as_a_pair(
    service: InvoiceService, customer_id: UUID
) -> None:
    invoice = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            competenza_da=date(2026, 8, 1),
            competenza_a=date(2026, 8, 31),
        ),
        ADMIN,
    )
    with pytest.raises(ValidationFailed) as caught:
        service.update(invoice.id, InvoiceUpdate(competenza_da=None), ADMIN)
    assert caught.value.details["field"] == "competenza_da"
    cleared = service.update(
        invoice.id, InvoiceUpdate(competenza_da=None, competenza_a=None), ADMIN
    )
    assert cleared.competenza_da is None and cleared.competenza_a is None


# --- a proforma has a document date of its own (ORB-63) ------------------------------


def test_a_new_proforma_is_dated_today_in_the_issuer_s_calendar(
    service: InvoiceService, customer_id: UUID
) -> None:
    """The PDF used to print the render day, so the same proforma re-rendered tomorrow
    said tomorrow. The date is the sender's and it is stored at creation."""
    from pigrocrm.core.clock import oggi_in_italia

    proforma = service.create(InvoiceCreate(customer_id=customer_id, tipo="proforma"), ADMIN)
    assert proforma.data_emissione == oggi_in_italia()


def test_a_proforma_may_be_created_with_its_own_date(
    service: InvoiceService, customer_id: UUID
) -> None:
    """Not a register date: no monotonicity, no closed-year rule, and a date in another
    year is fine, because the proforma touches no register (spec 5)."""
    proforma = service.create(
        InvoiceCreate(customer_id=customer_id, tipo="proforma", data_emissione=date(2025, 12, 31)),
        ADMIN,
    )
    assert proforma.data_emissione == date(2025, 12, 31)


def test_a_fattura_draft_takes_its_date_at_issue_not_at_creation(
    service: InvoiceService, customer_id: UUID
) -> None:
    with pytest.raises(ValidationFailed) as caught:
        service.create(
            InvoiceCreate(customer_id=customer_id, tipo="fattura", data_emissione=date(2026, 8, 1)),
            ADMIN,
        )
    assert caught.value.details["field"] == "data_emissione"
    draft = service.create(InvoiceCreate(customer_id=customer_id), ADMIN)
    assert draft.data_emissione is None
    with pytest.raises(ValidationFailed) as caught:
        service.update(draft.id, InvoiceUpdate(data_emissione=date(2026, 8, 1)), ADMIN)
    assert caught.value.details["field"] == "data_emissione"


def test_a_proforma_date_is_editable_while_a_draft_and_never_cleared(
    service: InvoiceService, customer_id: UUID
) -> None:
    proforma = service.create(InvoiceCreate(customer_id=customer_id, tipo="proforma"), ADMIN)
    moved = service.update(proforma.id, InvoiceUpdate(data_emissione=date(2026, 8, 31)), ADMIN)
    assert moved.data_emissione == date(2026, 8, 31)
    # `confermata` is still a draft for this purpose: the amount is agreed, the document
    # is still the sender's to date.
    service.replace_lines(proforma.id, [_line()], ADMIN)
    service.confirm_proforma(proforma.id, ADMIN)
    moved = service.update(proforma.id, InvoiceUpdate(data_emissione=date(2026, 9, 1)), ADMIN)
    assert moved.data_emissione == date(2026, 9, 1)
    with pytest.raises(ValidationFailed) as caught:
        service.update(proforma.id, InvoiceUpdate(data_emissione=None), ADMIN)
    assert caught.value.details["field"] == "data_emissione"


# --- the customer's name on the read shape -------------------------------------------


def test_every_read_carries_the_customer_s_name(service: InvoiceService, customer_id: UUID) -> None:
    """`InvoiceRead.customer_ragione_sociale` is denormalised from `customers` at read
    time, exactly as `DealRead` does it (ORB-98): the list page has to say whose invoice
    a row is without a request per row. Every path that hands back an `InvoiceRead`
    carries it, so a mutation's answer and the next `get` agree on the same shape."""
    created = service.create(InvoiceCreate(customer_id=customer_id), ADMIN)
    assert created.customer_ragione_sociale == "Acme S.r.l."

    assert service.get(created.id, ADMIN).customer_ragione_sociale == "Acme S.r.l."

    updated = service.update(created.id, InvoiceUpdate(causale="Consulenza"), ADMIN)
    assert updated.customer_ragione_sociale == "Acme S.r.l."

    page = service.list(InvoiceListQuery(), ADMIN)
    assert [item.customer_ragione_sociale for item in page.items] == ["Acme S.r.l."]


def test_the_customer_name_costs_one_query_for_the_whole_page(
    service: InvoiceService, db_session: Session
) -> None:
    """Counted, not reasoned about, for the same reason `test_deals.py` counts it: an N+1
    reintroduced by a later refactor is invisible to every other assertion here."""
    for index in range(3):
        customer = Customer(ragione_sociale=f"ACME {index}", nazione="IT")
        db_session.add(customer)
        db_session.flush()
        service.create(InvoiceCreate(customer_id=customer.id), ADMIN)

    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    connection = db_session.connection()
    event.listen(connection, "after_cursor_execute", record)
    try:
        page = service.list(InvoiceListQuery(), ADMIN)
    finally:
        event.remove(connection, "after_cursor_execute", record)

    assert len(page.items) == 3
    # The list itself, plus exactly one lookup for the three customer names.
    assert len(statements) == 2, statements
    assert sorted(item.customer_ragione_sociale or "" for item in page.items) == [
        "ACME 0",
        "ACME 1",
        "ACME 2",
    ]
