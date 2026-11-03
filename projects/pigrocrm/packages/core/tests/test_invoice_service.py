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
    DEFAULT_LEGAL_REFERENCE,
    LEGAL_REFERENCE_EXTRA_UE,
    FiscalProfileUpsert,
)
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.naming import REFERENCE_PROFORMA_RE
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
COLLABORATOR = Actor(id=None, type="user", role="collaborator")
READONLY = Actor(id=None, type="user", role="readonly")


@pytest.fixture
def storage(tmp_path) -> LocalFileStorage:  # type: ignore[no-untyped-def]
    return LocalFileStorage(tmp_path / "documents")


@pytest.fixture
def service(db_session: Session, storage: LocalFileStorage) -> InvoiceService:
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(regime_code="RF19"), ADMIN)
    return InvoiceService(db_session, storage)


@pytest.fixture
def customer_id(db_session: Session) -> UUID:
    customer = Customer(
        legal_name="Acme S.r.l.",
        vat_number="12345678901",
        sdi_code="ABCDEFG",
        address="Corso Italia 5",
        cap="00100",
        city="Roma",
        province="RM",
        country="IT",
    )
    db_session.add(customer)
    db_session.flush()
    return customer.id


def _line(description: str = "Consulenza", price: str = "1000.00", **kw: object) -> InvoiceLineIn:
    payload: dict[str, object] = {
        "description": description,
        "unit_price": Decimal(price),
    }
    payload.update(kw)
    return InvoiceLineIn(**payload)  # type: ignore[arg-type]


# --- creation -----------------------------------------------------------------------


def test_a_new_invoice_is_a_draft_with_no_number(
    service: InvoiceService, customer_id: UUID
) -> None:
    invoice = service.create(InvoiceCreate(customer_id=customer_id), ADMIN)
    assert invoice.type == "invoice"
    assert invoice.status == "draft"
    assert invoice.year is None
    assert invoice.number is None
    assert invoice.reference is None
    assert invoice.sdi_document_type == "TD01"
    assert invoice.currency == "EUR"
    assert invoice.total == Decimal("0.00")
    assert invoice.payment_status == "unpaid"


def test_a_new_proforma_gets_a_reference_no_fiscal_regex_can_accept(
    service: InvoiceService, customer_id: UUID
) -> None:
    proforma = service.create(
        InvoiceCreate(customer_id=customer_id, type="proforma", lines=[_line()]), ADMIN
    )
    assert proforma.status == "draft"
    assert proforma.number is None
    assert REFERENCE_PROFORMA_RE.fullmatch(proforma.reference or "")


def test_two_proformas_get_different_references(service: InvoiceService, customer_id: UUID) -> None:
    first = service.create(InvoiceCreate(customer_id=customer_id, type="proforma"), ADMIN)
    second = service.create(InvoiceCreate(customer_id=customer_id, type="proforma"), ADMIN)
    assert first.reference != second.reference


def test_creation_computes_and_stores_the_totals(
    service: InvoiceService, customer_id: UUID
) -> None:
    """Totals are computed by the service and stored, never recomputed by a client:
    a total computed in the browser is the structural defect this slice removes."""
    invoice = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            lines=[
                _line("Consulenza", "33.333333", quantity=Decimal("3.000000")),
                _line("Sconto", "-10.00"),
            ],
        ),
        ADMIN,
    )
    assert invoice.taxable_amount == Decimal("90.00")
    assert invoice.vat_amount == Decimal("0.00")
    assert invoice.total == Decimal("90.00")
    assert invoice.stamp_duty == Decimal("2.00")


def test_the_stamp_duty_is_stored_but_stays_out_of_the_total(
    service: InvoiceService, customer_id: UUID
) -> None:
    invoice = service.create(
        InvoiceCreate(customer_id=customer_id, lines=[_line("Consulenza", "1000.00")]), ADMIN
    )
    assert invoice.stamp_duty == Decimal("2.00")
    assert invoice.total == Decimal("1000.00")


def test_the_regime_decides_the_line_vat_nature(service: InvoiceService, customer_id: UUID) -> None:
    invoice = service.create(InvoiceCreate(customer_id=customer_id, lines=[_line()]), ADMIN)
    (riga,) = service.lines(invoice.id, ADMIN)
    assert riga.line_number == 1
    assert riga.vat_rate == Decimal("0.00")
    assert riga.vat_nature == "N2.2"
    assert riga.legal_reference == DEFAULT_LEGAL_REFERENCE


def _non_resident_customer(db_session: Session) -> UUID:
    """The shape of ORB-32: a British company, no SDI code, no PEC, a postcode that is
    not five digits and no province."""
    customer = Customer(
        legal_name="Example Ltd",
        vat_number="GB123456789",
        address="1 Old Street",
        cap="EC1V 9HL",
        city="London",
        province="",
        country="GB",
    )
    db_session.add(customer)
    db_session.flush()
    return customer.id


def test_to_not_resident_customer_gets_vat_nature_n_2_1_and_the_7_ter_reference(
    service: InvoiceService, db_session: Session
) -> None:
    """ORB-32. The regime reads the customer's country: a service to a business outside
    Italy is outside the scope of Italian VAT (art. 7-ter DPR 633/1972), so the line
    carries `N2.1` and the 7-ter reference, not the domestic `N2.2` declaration."""
    customer_id = _non_resident_customer(db_session)
    invoice = service.create(
        InvoiceCreate(customer_id=customer_id, type="proforma", lines=[_line()]), ADMIN
    )
    (riga,) = service.lines(invoice.id, ADMIN)
    assert riga.vat_rate == Decimal("0.00")
    assert riga.vat_nature == "N2.1"
    assert riga.legal_reference == LEGAL_REFERENCE_EXTRA_UE
    assert "7-ter" in riga.legal_reference


def test_replacing_the_lines_reads_the_customer_s_country_again(
    service: InvoiceService, db_session: Session
) -> None:
    """The natura is decided when the lines are computed, so a proforma created before
    this fix, or before the customer's country was corrected, is repaired by replacing
    its lines: that is the remedy for the proforma in ORB-32."""
    customer_id = _non_resident_customer(db_session)
    invoice = service.create(InvoiceCreate(customer_id=customer_id, lines=[_line()]), ADMIN)
    customer = db_session.get(Customer, customer_id)
    assert customer is not None
    customer.country = "IT"
    customer.province = "RM"
    customer.cap = "00100"
    db_session.flush()

    service.replace_lines(invoice.id, [_line()], ADMIN)
    (riga,) = service.lines(invoice.id, ADMIN)
    assert riga.vat_nature == "N2.2"
    assert riga.legal_reference == DEFAULT_LEGAL_REFERENCE


def test_to_not_zero_rate_under_the_flat_rate_is_refused_by_field_name(
    service: InvoiceService, customer_id: UUID
) -> None:
    with pytest.raises(ValidationFailed) as caught:
        service.create(
            InvoiceCreate(
                customer_id=customer_id,
                lines=[_line(vat_rate=Decimal("22.00"))],
            ),
            ADMIN,
        )
    assert caught.value.details["field"] == "vat_rate"


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
    other = Customer(legal_name="Altro", country="IT")
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
    # The azienda exists, as it does on every provisioned space; what is missing is
    # its fiscal profile, which is the configuration the message has to name.
    from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
    from pigrocrm.core.legal_entities.service import LegalEntityService

    LegalEntityService(db_session).upsert_default(LegalEntityUpsert(legal_name="Studio"), ADMIN)
    with pytest.raises(NotFound) as caught:
        InvoiceService(db_session, storage).create(InvoiceCreate(customer_id=customer_id), ADMIN)
    assert caught.value.details["entity"] == "fiscal_profile"


def test_a_readonly_actor_cannot_create(service: InvoiceService, customer_id: UUID) -> None:
    with pytest.raises(PermissionDenied):
        service.create(InvoiceCreate(customer_id=customer_id), READONLY)


def test_to_collaborator_may_create_to_draft(service: InvoiceService, customer_id: UUID) -> None:
    """Drafting is ordinary entity writing; only consuming a register number is not
    (spec 11)."""
    assert service.create(InvoiceCreate(customer_id=customer_id), COLLABORATOR).status == "draft"


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
        InvoiceCreate(customer_id=customer_id, lines=[_line("A"), _line("B"), _line("C")]), ADMIN
    )
    service.replace_lines(invoice.id, [_line("Solo questa", "50.00")], ADMIN)
    lines = service.lines(invoice.id, ADMIN)
    assert [r.line_number for r in lines] == [1]
    assert lines[0].description == "Solo questa"


def test_replacing_the_lines_recomputes_the_totals(
    service: InvoiceService, customer_id: UUID
) -> None:
    invoice = service.create(
        InvoiceCreate(customer_id=customer_id, lines=[_line("A", "1000.00")]), ADMIN
    )
    updated = service.replace_lines(invoice.id, [_line("A", "10.00")], ADMIN)
    assert updated.total == Decimal("10.00")
    # Below the threshold now, so the duty disappears with the amount.
    assert updated.stamp_duty == Decimal("0.00")


def test_replacing_the_lines_with_an_empty_list_is_allowed_on_a_draft(
    service: InvoiceService, customer_id: UUID
) -> None:
    """A draft with no lines is a legitimate intermediate state; only *emission*
    requires at least one line."""
    invoice = service.create(InvoiceCreate(customer_id=customer_id, lines=[_line()]), ADMIN)
    assert service.replace_lines(invoice.id, [], ADMIN).total == Decimal("0.00")
    assert service.lines(invoice.id, ADMIN) == []


def test_bulk_replacement_is_how_an_optional_numeric_field_gets_cleared(
    service: InvoiceService, customer_id: UUID
) -> None:
    """Written when A14 made bulk replacement the *only* way to clear
    `discount_amount`; kept now that task 4B-1 has closed A14, because lines still have
    no `Update` schema and replacement is still how a line editor saves."""
    invoice = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            lines=[_line("A", "100.00", discount_amount=Decimal("10.00"), unit_of_measure="hours")],
        ),
        ADMIN,
    )
    service.replace_lines(invoice.id, [_line("A", "100.00")], ADMIN)
    (riga,) = service.lines(invoice.id, ADMIN)
    assert riga.discount_amount is None
    assert riga.unit_of_measure is None
    assert riga.total_price == Decimal("100.00")


def test_a_percentage_and_a_fixed_discount_compose_in_a_defined_order(
    service: InvoiceService, customer_id: UUID
) -> None:
    invoice = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            lines=[
                _line(
                    "A",
                    "100.00",
                    quantity=Decimal("2.000000"),
                    discount_percentage=Decimal("10.00"),
                    discount_amount=Decimal("5.00"),
                )
            ],
        ),
        ADMIN,
    )
    assert invoice.total == Decimal("175.00")


def test_more_lines_than_the_bound_are_refused_by_the_schema(
    service: InvoiceService, customer_id: UUID
) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        InvoiceCreate(customer_id=customer_id, lines=[_line()] * 201)


def test_replacing_lines_records_an_activity(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    invoice = service.create(InvoiceCreate(customer_id=customer_id), ADMIN)
    service.replace_lines(invoice.id, [_line(), _line("B")], ADMIN)
    kinds = [e.kind for e in ActivityService(db_session).timeline("invoice", invoice.id)]
    assert "lines_replaced" in kinds


# --- editing what may be edited -----------------------------------------------------


def test_the_memo_and_the_notes_are_editable_on_to_draft(
    service: InvoiceService, customer_id: UUID
) -> None:
    invoice = service.create(InvoiceCreate(customer_id=customer_id, memo="Bozza"), ADMIN)
    updated = service.update(
        invoice.id, InvoiceUpdate(memo="Consulenza agosto", note_interne="da rileggere"), ADMIN
    )
    assert updated.memo == "Consulenza agosto"
    assert updated.note_interne == "da rileggere"


def test_an_empty_string_clears_a_text_column(service: InvoiceService, customer_id: UUID) -> None:
    """The only clear-it spelling that exists, and it works because both editable
    native columns are text-shaped."""
    invoice = service.create(InvoiceCreate(customer_id=customer_id, memo="Bozza"), ADMIN)
    assert service.update(invoice.id, InvoiceUpdate(memo=""), ADMIN).memo == ""


def test_an_omitted_key_clears_nothing(service: InvoiceService, customer_id: UUID) -> None:
    invoice = service.create(
        InvoiceCreate(customer_id=customer_id, memo="Bozza", note_interne="note"), ADMIN
    )
    assert service.update(invoice.id, InvoiceUpdate(memo="Altro"), ADMIN).note_interne == "note"


# --- proforma confirmation ----------------------------------------------------------


def test_confirming_a_proforma_moves_it_out_of_draft(
    service: InvoiceService, customer_id: UUID
) -> None:
    proforma = service.create(
        InvoiceCreate(customer_id=customer_id, type="proforma", lines=[_line()]), ADMIN
    )
    assert service.confirm_proforma(proforma.id, ADMIN).status == "confirmed"


def test_confirming_a_proforma_with_no_lines_is_refused(
    service: InvoiceService, customer_id: UUID
) -> None:
    proforma = service.create(InvoiceCreate(customer_id=customer_id, type="proforma"), ADMIN)
    with pytest.raises(ValidationFailed) as caught:
        service.confirm_proforma(proforma.id, ADMIN)
    assert caught.value.details["field"] == "lines"


def test_to_invoice_cannot_be_confirmed(service: InvoiceService, customer_id: UUID) -> None:
    invoice = service.create(InvoiceCreate(customer_id=customer_id, lines=[_line()]), ADMIN)
    with pytest.raises(Conflict):
        service.confirm_proforma(invoice.id, ADMIN)


def test_a_confirmed_proforma_can_still_be_edited(
    service: InvoiceService, customer_id: UUID
) -> None:
    """A proforma is entirely mutable until it is consumed -- that is what it is for
    (spec 5): agree the amount before consuming a number."""
    proforma = service.create(
        InvoiceCreate(customer_id=customer_id, type="proforma", lines=[_line()]), ADMIN
    )
    service.confirm_proforma(proforma.id, ADMIN)
    assert service.replace_lines(proforma.id, [_line("B", "20.00")], ADMIN).total == Decimal(
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
    invoice = service.create(InvoiceCreate(customer_id=customer_id, lines=[_line()]), ADMIN)
    service.soft_delete(invoice.id, ADMIN)
    with pytest.raises(NotFound):
        service.lines(invoice.id, ADMIN)


# --- payment ------------------------------------------------------------------------


def test_the_payment_state_cannot_be_set_on_a_draft(
    service: InvoiceService, customer_id: UUID
) -> None:
    """There is nothing to collect on a document that was never issued."""
    invoice = service.create(InvoiceCreate(customer_id=customer_id, lines=[_line()]), ADMIN)
    with pytest.raises(Conflict):
        service.set_payment_state(
            invoice.id,
            PaymentState(payment_status="paid", payment_date=date(2026, 9, 1)),
            ADMIN,
        )


def test_marking_collected_without_a_date_is_refused(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    """ "Collected with no date" and "a date but not collected" are both nonsense, so
    a single method takes both and checks their agreement."""
    invoice = _issued_row(db_session, customer_id)
    with pytest.raises(ValidationFailed) as caught:
        service.set_payment_state(invoice.id, PaymentState(payment_status="paid"), ADMIN)
    assert caught.value.details["field"] == "payment_date"


def test_going_back_to_uncollected_clears_the_date(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    invoice = _issued_row(db_session, customer_id)
    service.set_payment_state(
        invoice.id, PaymentState(payment_status="paid", payment_date=date(2026, 9, 1)), ADMIN
    )
    back = service.set_payment_state(invoice.id, PaymentState(payment_status="unpaid"), ADMIN)
    assert back.payment_status == "unpaid"
    assert back.payment_date is None


def test_to_collaborator_may_record_to_payment(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    """Spec 11: `set_payment_state` is the one invoice write a collaborator -- and an
    agent -- may perform, because collecting is a subsequent fact, not part of the
    document."""
    invoice = _issued_row(db_session, customer_id)
    assert (
        service.set_payment_state(
            invoice.id,
            PaymentState(payment_status="paid", payment_date=date(2026, 9, 1)),
            COLLABORATOR,
        ).payment_status
        == "paid"
    )


def _issued_row(db_session: Session, customer_id: UUID, **overrides: object) -> Invoice:
    """An already-issued row inserted directly, so this file's payment tests do not
    depend on `issue` (next task) being written yet. `overrides` are applied before the
    flush, since `(anno, numero)` is unique and a second row needs its own number."""
    fields: dict[str, object] = dict(
        customer_id=customer_id,
        type="invoice",
        status="issued",
        year=2026,
        number=1,
        issue_date=date(2026, 8, 20),
        sdi_document_type="TD01",
        currency="EUR",
        taxable_amount=Decimal("100.00"),
        vat_amount=Decimal("0.00"),
        stamp_duty=Decimal("2.00"),
        total=Decimal("100.00"),
        payment_status="unpaid",
        snapshot={"versione": 1},
        snapshot_version=1,
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
    assert caught.value.details["field"] == "lines"


def test_the_memo_of_an_issued_invoice_cannot_be_changed(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    invoice = _issued_row(db_session, customer_id)
    with pytest.raises(ImmutableField) as caught:
        service.update(invoice.id, InvoiceUpdate(memo="ripensamento"), ADMIN)
    assert caught.value.details["field"] == "memo"


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


def test_the_list_finds_the_invoice_to_consumed_proforma_was_issued_as(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    """The fattura points back at its proforma and the proforma points at nothing, so
    the consumed proforma's page finds its fattura by asking the list for the row whose
    `origin_proforma_id` is its own id (ORB-134). Another fattura, or another
    proforma's fattura, does not answer."""
    proforma = service.create(InvoiceCreate(customer_id=customer_id, type="proforma"), ADMIN)
    other = service.create(InvoiceCreate(customer_id=customer_id, type="proforma"), ADMIN)
    from_proforma = _issued_row(db_session, customer_id, origin_proforma_id=proforma.id)
    _issued_row(db_session, customer_id, number=2, origin_proforma_id=other.id)
    standalone = _issued_row(db_session, customer_id, number=3)

    found = service.list(InvoiceListQuery(origin_proforma_id=proforma.id), ADMIN).items
    assert [i.id for i in found] == [from_proforma.id]
    assert standalone.id not in {i.id for i in found}
    assert service.list(InvoiceListQuery(origin_proforma_id=uuid4()), ADMIN).items == []


def test_the_list_can_leave_out_the_proformas_that_became_to_invoice(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    """Under «Tutte» the web showed 2026/18 and the proforma it came from as two rows
    with the same customer, period and total (ORB-169). `exclude_consumed` drops the
    consumed ones and nothing else; asked by `status`, they still answer."""
    draft = service.create(InvoiceCreate(customer_id=customer_id, type="proforma"), ADMIN)
    consumed = service.create(InvoiceCreate(customer_id=customer_id, type="proforma"), ADMIN)
    db_session.get(Invoice, consumed.id).status = "consumed"  # type: ignore[union-attr]
    invoice = _issued_row(db_session, customer_id, origin_proforma_id=consumed.id)
    db_session.flush()

    everything = {i.id for i in service.list(InvoiceListQuery(), ADMIN).items}
    assert {draft.id, consumed.id, invoice.id} <= everything
    trimmed = {i.id for i in service.list(InvoiceListQuery(exclude_consumed=True), ADMIN).items}
    assert trimmed == everything - {consumed.id}
    by_state = service.list(InvoiceListQuery(status="consumed"), ADMIN).items
    assert [i.id for i in by_state] == [consumed.id]


def test_the_list_filters_by_type_status_year_and_payment(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    draft = service.create(InvoiceCreate(customer_id=customer_id), ADMIN)
    proforma = service.create(InvoiceCreate(customer_id=customer_id, type="proforma"), ADMIN)
    issued = _issued_row(db_session, customer_id)

    assert {i.id for i in service.list(InvoiceListQuery(type="proforma"), ADMIN).items} == {
        proforma.id
    }
    assert {i.id for i in service.list(InvoiceListQuery(status="draft"), ADMIN).items} == {
        draft.id,
        proforma.id,
    }
    assert {i.id for i in service.list(InvoiceListQuery(year=2026), ADMIN).items} == {issued.id}
    assert {
        i.id for i in service.list(InvoiceListQuery(payment_status="paid"), ADMIN).items
    } == set()


def test_the_list_under_unpaid_answers_only_the_receivables(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    """REB-325: every row is born `unpaid`, so an equality on the column listed
    drafts and proformas as unpaid. The filter now means what the dashboard's «Da
    incassare» card means (`_receivable_filter`): an issued fattura nobody has paid."""
    service.create(InvoiceCreate(customer_id=customer_id), ADMIN)
    service.create(InvoiceCreate(customer_id=customer_id, type="proforma"), ADMIN)
    owed = _issued_row(db_session, customer_id)
    _issued_row(
        db_session,
        customer_id,
        number=2,
        payment_status="paid",
        payment_date=date(2026, 8, 25),
    )
    _issued_row(
        db_session,
        customer_id,
        number=3,
        status="cancelled",
        cancelled_at=date(2026, 8, 21),
        cancellation_reason="storno",
    )

    receivable = service.list(InvoiceListQuery(payment_status="unpaid"), ADMIN).items
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
            accrual_from=date(2026, 8, 1),
            accrual_to=date(2026, 8, 31),
        ),
        ADMIN,
    )
    assert invoice.accrual_from == date(2026, 8, 1)
    assert invoice.accrual_to == date(2026, 8, 31)
    assert service.create(InvoiceCreate(customer_id=customer_id), ADMIN).accrual_from is None


def test_half_an_accrual_period_is_refused_by_field_name(
    service: InvoiceService, customer_id: UUID
) -> None:
    with pytest.raises(ValidationFailed) as caught:
        service.create(InvoiceCreate(customer_id=customer_id, accrual_from=date(2026, 8, 1)), ADMIN)
    assert caught.value.details["field"] == "accrual_to"
    with pytest.raises(ValidationFailed) as caught:
        service.create(InvoiceCreate(customer_id=customer_id, accrual_to=date(2026, 8, 31)), ADMIN)
    assert caught.value.details["field"] == "accrual_from"


def test_an_inverted_accrual_period_is_refused(service: InvoiceService, customer_id: UUID) -> None:
    with pytest.raises(ValidationFailed) as caught:
        service.create(
            InvoiceCreate(
                customer_id=customer_id,
                accrual_from=date(2026, 8, 31),
                accrual_to=date(2026, 8, 1),
            ),
            ADMIN,
        )
    assert caught.value.details["field"] == "accrual_to"


def test_the_accrual_period_is_editable_on_a_draft_one_end_at_a_time(
    service: InvoiceService, customer_id: UUID
) -> None:
    """The rule is checked on the merged row, not on the request: completing a period
    whose other end is already stored is one field, and so is moving one end."""
    invoice = service.create(InvoiceCreate(customer_id=customer_id), ADMIN)
    with pytest.raises(ValidationFailed) as caught:
        service.update(invoice.id, InvoiceUpdate(accrual_from=date(2026, 8, 1)), ADMIN)
    assert caught.value.details["field"] == "accrual_to"

    updated = service.update(
        invoice.id,
        InvoiceUpdate(accrual_from=date(2026, 8, 1), accrual_to=date(2026, 8, 31)),
        ADMIN,
    )
    assert (updated.accrual_from, updated.accrual_to) == (date(2026, 8, 1), date(2026, 8, 31))
    moved = service.update(invoice.id, InvoiceUpdate(accrual_to=date(2026, 9, 30)), ADMIN)
    assert (moved.accrual_from, moved.accrual_to) == (date(2026, 8, 1), date(2026, 9, 30))
    with pytest.raises(ValidationFailed):
        service.update(invoice.id, InvoiceUpdate(accrual_to=date(2026, 7, 31)), ADMIN)


def test_the_accrual_period_is_cleared_as_a_pair(
    service: InvoiceService, customer_id: UUID
) -> None:
    invoice = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            accrual_from=date(2026, 8, 1),
            accrual_to=date(2026, 8, 31),
        ),
        ADMIN,
    )
    with pytest.raises(ValidationFailed) as caught:
        service.update(invoice.id, InvoiceUpdate(accrual_from=None), ADMIN)
    assert caught.value.details["field"] == "accrual_from"
    cleared = service.update(invoice.id, InvoiceUpdate(accrual_from=None, accrual_to=None), ADMIN)
    assert cleared.accrual_from is None and cleared.accrual_to is None


# --- a proforma has a document date of its own (ORB-63) ------------------------------


def test_a_new_proforma_is_dated_today_in_the_issuer_s_calendar(
    service: InvoiceService, customer_id: UUID
) -> None:
    """The PDF used to print the render day, so the same proforma re-rendered tomorrow
    said tomorrow. The date is the sender's and it is stored at creation."""
    from pigrocrm.core.db.clock import today_local

    proforma = service.create(InvoiceCreate(customer_id=customer_id, type="proforma"), ADMIN)
    assert proforma.issue_date == today_local()


def test_a_proforma_may_be_created_with_its_own_date(
    service: InvoiceService, customer_id: UUID
) -> None:
    """Not a register date: no monotonicity, no closed-year rule, and a date in another
    year is fine, because the proforma touches no register (spec 5)."""
    proforma = service.create(
        InvoiceCreate(customer_id=customer_id, type="proforma", issue_date=date(2025, 12, 31)),
        ADMIN,
    )
    assert proforma.issue_date == date(2025, 12, 31)


def test_to_invoice_draft_takes_its_date_at_issue_not_at_creation(
    service: InvoiceService, customer_id: UUID
) -> None:
    with pytest.raises(ValidationFailed) as caught:
        service.create(
            InvoiceCreate(customer_id=customer_id, type="invoice", issue_date=date(2026, 8, 1)),
            ADMIN,
        )
    assert caught.value.details["field"] == "issue_date"
    draft = service.create(InvoiceCreate(customer_id=customer_id), ADMIN)
    assert draft.issue_date is None
    with pytest.raises(ValidationFailed) as caught:
        service.update(draft.id, InvoiceUpdate(issue_date=date(2026, 8, 1)), ADMIN)
    assert caught.value.details["field"] == "issue_date"


def test_a_proforma_date_is_editable_while_a_draft_and_never_cleared(
    service: InvoiceService, customer_id: UUID
) -> None:
    proforma = service.create(InvoiceCreate(customer_id=customer_id, type="proforma"), ADMIN)
    moved = service.update(proforma.id, InvoiceUpdate(issue_date=date(2026, 8, 31)), ADMIN)
    assert moved.issue_date == date(2026, 8, 31)
    # `confirmed` is still a draft for this purpose: the amount is agreed, the document
    # is still the sender's to date.
    service.replace_lines(proforma.id, [_line()], ADMIN)
    service.confirm_proforma(proforma.id, ADMIN)
    moved = service.update(proforma.id, InvoiceUpdate(issue_date=date(2026, 9, 1)), ADMIN)
    assert moved.issue_date == date(2026, 9, 1)
    with pytest.raises(ValidationFailed) as caught:
        service.update(proforma.id, InvoiceUpdate(issue_date=None), ADMIN)
    assert caught.value.details["field"] == "issue_date"


# --- the customer's name on the read shape -------------------------------------------


def test_every_read_carries_the_customer_s_name(service: InvoiceService, customer_id: UUID) -> None:
    """`InvoiceRead.customer_legal_name` is denormalised from `customers` at read
    time, exactly as `DealRead` does it (ORB-98): the list page has to say whose invoice
    a row is without a request per row. Every path that hands back an `InvoiceRead`
    carries it, so a mutation's answer and the next `get` agree on the same shape."""
    created = service.create(InvoiceCreate(customer_id=customer_id), ADMIN)
    assert created.customer_legal_name == "Acme S.r.l."

    assert service.get(created.id, ADMIN).customer_legal_name == "Acme S.r.l."

    updated = service.update(created.id, InvoiceUpdate(memo="Consulenza"), ADMIN)
    assert updated.customer_legal_name == "Acme S.r.l."

    page = service.list(InvoiceListQuery(), ADMIN)
    assert [item.customer_legal_name for item in page.items] == ["Acme S.r.l."]


def test_the_customer_name_costs_one_query_for_the_whole_page(
    service: InvoiceService, db_session: Session
) -> None:
    """Counted, not reasoned about, for the same reason `test_deals.py` counts it: an N+1
    reintroduced by a later refactor is invisible to every other assertion here."""
    for index in range(3):
        customer = Customer(legal_name=f"ACME {index}", country="IT")
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
    assert sorted(item.customer_legal_name or "" for item in page.items) == [
        "ACME 0",
        "ACME 1",
        "ACME 2",
    ]


# --- the purchase order (REB-693) -----------------------------------------------------


def test_a_draft_may_carry_a_purchase_order_stored_clean(
    service: InvoiceService, customer_id: UUID
) -> None:
    invoice = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            purchase_order_number=" ODA-14 ",
            purchase_order_date=date(2026, 10, 9),
            purchase_order_tender_code="z0a1b2c3d4",
            purchase_order_project_code="j41e22000110005",
        ),
        ADMIN,
    )
    assert (
        invoice.purchase_order_number,
        invoice.purchase_order_date,
        invoice.purchase_order_tender_code,
        invoice.purchase_order_project_code,
    ) == ("ODA-14", date(2026, 10, 9), "Z0A1B2C3D4", "J41E22000110005")
    bare = service.create(InvoiceCreate(customer_id=customer_id), ADMIN)
    assert (
        bare.purchase_order_number,
        bare.purchase_order_date,
        bare.purchase_order_tender_code,
        bare.purchase_order_project_code,
    ) == (
        None,
        None,
        None,
        None,
    )


def test_a_padded_cup_is_stored_stripped_and_upper_case(
    service: InvoiceService, customer_id: UUID
) -> None:
    invoice = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            purchase_order_number="ODA-14",
            purchase_order_project_code=" j41e22000110005 ",
        ),
        ADMIN,
    )
    assert invoice.purchase_order_project_code == "J41E22000110005"


def test_to_tender_code_without_an_order_number_is_refused_on_the_number(
    service: InvoiceService, customer_id: UUID
) -> None:
    with pytest.raises(ValidationFailed) as caught:
        service.create(
            InvoiceCreate(customer_id=customer_id, purchase_order_tender_code="Z0A1B2C3D4"), ADMIN
        )
    assert caught.value.details["field"] == "purchase_order_number"


def test_to_malformed_tender_code_is_refused_by_field_name(
    service: InvoiceService, customer_id: UUID
) -> None:
    with pytest.raises(ValidationFailed) as caught:
        service.create(
            InvoiceCreate(
                customer_id=customer_id,
                purchase_order_number="ODA-14",
                purchase_order_tender_code="Z0A1",
            ),
            ADMIN,
        )
    assert caught.value.details["field"] == "purchase_order_tender_code"


def test_the_order_is_edited_on_the_merged_row_and_cleared_as_a_whole(
    service: InvoiceService, customer_id: UUID
) -> None:
    """Like the accrual period: adding a CIG to a document whose number is stored is
    one field, dropping the number while the CIG stays is refused and leaves the row as
    it was, and four nulls clear the order."""
    invoice = service.create(
        InvoiceCreate(customer_id=customer_id, purchase_order_number="ODA-14"), ADMIN
    )
    with_tender_code = service.update(
        invoice.id, InvoiceUpdate(purchase_order_tender_code="z0a1b2c3d4"), ADMIN
    )
    assert (
        with_tender_code.purchase_order_number,
        with_tender_code.purchase_order_tender_code,
    ) == ("ODA-14", "Z0A1B2C3D4")

    with pytest.raises(ValidationFailed) as caught:
        service.update(invoice.id, InvoiceUpdate(purchase_order_number=None), ADMIN)
    assert caught.value.details["field"] == "purchase_order_number"
    assert service.get(invoice.id, ADMIN).purchase_order_number == "ODA-14"

    cleared = service.update(
        invoice.id,
        InvoiceUpdate(
            purchase_order_number=None,
            purchase_order_date=None,
            purchase_order_tender_code=None,
            purchase_order_project_code=None,
        ),
        ADMIN,
    )
    assert (cleared.purchase_order_number, cleared.purchase_order_tender_code) == (None, None)


def test_unpaid_for_customer_lists_the_issued_unpaid_rows_oldest_deadline_first(
    service: InvoiceService, db_session: Session
) -> None:
    from datetime import timedelta

    from fakes.invoice_fixtures import unpaid_invoice

    from pigrocrm.core.db.clock import today_local

    today = today_local()
    customer = Customer(legal_name="Debitore S.r.l.")
    db_session.add(customer)
    db_session.flush()
    later = unpaid_invoice(db_session, due=today + timedelta(days=20), customer=customer)
    earlier = unpaid_invoice(db_session, due=today + timedelta(days=5), customer=customer)
    paid = unpaid_invoice(db_session, due=today + timedelta(days=1), customer=customer)
    paid.payment_status = "paid"
    paid.payment_date = today
    unpaid_invoice(db_session, due=today + timedelta(days=2))  # another customer's
    db_session.flush()

    rows = service.unpaid_for_customer(customer.id, ADMIN, limit=10)

    assert [row.id for row in rows] == [earlier.id, later.id]
    assert [row.id for row in service.unpaid_for_customer(customer.id, ADMIN, limit=1)] == [
        earlier.id
    ]
