"""Shapes only -- no session, no service. What is pinned here is the set of things
that reach Postgres if a schema forgets them: a width, a scale, a NUL byte, a bound.
"""

from datetime import date
from decimal import Decimal
from typing import get_args
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pigrocrm.core.documents.schemas import ALLOWED_CONTENT_TYPES, DocumentType
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.invoices.schemas import (
    DESCRIPTION_MAX_LENGTH,
    MAX_LINES,
    MAX_NUMBER,
    SNAPSHOT_VERSION,
    InvoiceCreate,
    InvoiceImport,
    InvoiceLineImport,
    InvoiceLineIn,
    InvoiceListQuery,
    InvoiceSnapshot,
    InvoiceUpdate,
    PartySnapshot,
    PdfSource,
    RegisterGapsDeclare,
)
from pigrocrm.core.schema_registry import CREATE_MODELS, ENTITY_TYPES, native_fields


def test_invoice_is_a_declared_entity_type_in_all_three_python_places() -> None:
    assert "invoice" in get_args(EntityType)
    assert "invoice" in ENTITY_TYPES
    assert CREATE_MODELS["invoice"] is InvoiceCreate


def test_native_fields_for_an_invoice_covers_the_derived_fiscal_columns() -> None:
    """`native_fields` derives from the Create schema, and an invoice's most
    collision-prone names -- `total`, `taxable_amount`, `number` -- are derived columns
    that no Create schema declares. A custom field labelled "Totale" slugifies to
    `total`; A13 is still open, so nothing consults this list yet, but the list it
    will consult has to be right."""
    names = native_fields("invoice")
    assert {"customer_id", "type", "memo", "lines"} <= set(names)
    assert {
        "year",
        "number",
        "taxable_amount",
        "vat_amount",
        "stamp_duty",
        "total",
        "status",
    } <= set(names)
    assert "custom_fields" not in names


def test_native_fields_for_the_older_entities_is_unchanged() -> None:
    """EXTRA_NATIVE_FIELDS is empty for them, so the derivation is still the whole
    answer, less `note`, the text of a first note and no column (REB-879)."""
    assert native_fields("customer") == [
        name
        for name in CREATE_MODELS["customer"].model_fields
        if name not in {"custom_fields", "note"}
    ]


def test_documents_learns_the_three_invoice_artefact_types() -> None:
    assert {"invoice", "invoice_xml", "proforma"} <= set(get_args(DocumentType))
    assert ALLOWED_CONTENT_TYPES["application/xml"] == ".xml"


def test_a_line_rejects_a_nul_byte_in_its_description() -> None:
    with pytest.raises(ValidationError):
        InvoiceLineIn(description="Consulenza\x00", unit_price=Decimal("100.00"))


def test_a_line_description_is_bounded_to_the_column_width() -> None:
    with pytest.raises(ValidationError):
        InvoiceLineIn(description="x" * (DESCRIPTION_MAX_LENGTH + 1), unit_price=Decimal("100.00"))


def test_a_unit_price_keeps_six_decimals_and_refuses_a_seventh() -> None:
    """Numeric(12, 6): six decimals is what makes 33,3333 EUR/h expressible, and a
    seventh would be silently rounded by Postgres while the response still reported
    the original."""
    assert InvoiceLineIn(
        description="Consulenza", unit_price=Decimal("33.333333")
    ).unit_price == Decimal("33.333333")
    with pytest.raises(ValidationError):
        InvoiceLineIn(description="Consulenza", unit_price=Decimal("33.3333331"))


def test_a_unit_price_beyond_twelve_digits_is_refused_before_postgres_sees_it() -> None:
    with pytest.raises(ValidationError):
        InvoiceLineIn(description="Consulenza", unit_price=Decimal("1234567.000000"))


def test_to_line_never_carries_its_own_vat_nature() -> None:
    """`vat_nature` and `legal_reference` are the regime's answer, not the caller's:
    accepting them would make the table constraint
    `(aliquota_iva = 0) = (natura IS NOT NULL)` reachable from a request body."""
    assert "vat_nature" not in InvoiceLineIn.model_fields
    assert "legal_reference" not in InvoiceLineIn.model_fields
    with pytest.raises(ValidationError):
        InvoiceLineIn(description="x", unit_price=Decimal("1.00"), vat_nature="N2.2")


def test_an_invoice_cannot_be_created_with_more_lines_than_the_bound() -> None:
    line = {"description": "x", "unit_price": "1.00"}
    with pytest.raises(ValidationError):
        InvoiceCreate(customer_id=uuid4(), lines=[line] * (MAX_LINES + 1))


def test_update_exposes_the_editable_columns_and_custom_fields() -> None:
    """Until ORB-61 every native column here was text-shaped and the docstring said A14
    was sidestepped by that. Task 4B-1 retired A14 itself -- `supplied_changes` reads
    `exclude_unset`, so an explicit `null` clears a typed column and an omitted key
    leaves it alone -- which is what lets three `date` columns sit here: the accrual
    period (cleared as a pair, the service checks) and a proforma's own document date
    (never cleared, the service checks), and since REB-326 the due date, which cleared
    hands the decision back to the customer's terms at emission. All of them are editable
    only while the document is a draft, which `InvoiceService.update` enforces with
    `ImmutableField`."""
    assert set(InvoiceUpdate.model_fields) == {
        "memo",
        "note_interne",
        "custom_fields",
        "accrual_from",
        "accrual_to",
        "issue_date",
        "due_date",
        "purchase_order_number",
        "purchase_order_date",
        "purchase_order_tender_code",
        "purchase_order_project_code",
    }


def test_the_accrual_period_is_carried_by_every_shape_that_reaches_a_document() -> None:
    """Settable on creation and on import, read back, and part of what the exporter and
    the PDF read -- the period is on the document (ORB-61), so a shape that dropped it
    would print or export a document that says less than the row."""
    from pigrocrm.core.invoices.schemas import InvoiceForExport, InvoiceImport, InvoiceRead

    for model in (InvoiceCreate, InvoiceUpdate, InvoiceImport, InvoiceRead, InvoiceForExport):
        assert {"accrual_from", "accrual_to"} <= set(model.model_fields), model.__name__
    assert InvoiceCreate(customer_id=uuid4()).accrual_from is None
    assert InvoiceForExport.model_fields["accrual_from"].default is None


def test_the_list_query_limit_is_bounded_in_the_schema_not_only_the_router() -> None:
    assert InvoiceListQuery().limit == 50
    with pytest.raises(ValidationError):
        InvoiceListQuery(limit=201)


def test_a_snapshot_round_trips_through_json_with_its_version() -> None:
    party = PartySnapshot(
        legal_name="Rossi & C.",
        vat_number="12345678901",
        tax_code=None,
        sdi_code="ABCDEFG",
        certified_email=None,
        address="Via Roma 1",
        cap="20100",
        city="Milano",
        province="MI",
        country="IT",
        email=None,
        phone=None,
        website=None,
    )
    snapshot = InvoiceSnapshot(
        version=SNAPSHOT_VERSION,
        legal_entity=party,
        customer=party,
        tax={
            "regime_code": "RF19",
            "default_vat_rate": Decimal("0.00"),
            "default_vat_nature": "N2.2",
            "legal_reference": "art. 1",
            "apply_stamp_duty": True,
            "stamp_duty_threshold": Decimal("77.47"),
            "stamp_duty_amount": Decimal("2.00"),
            "payment_terms": "TP02",
            "payment_method": "MP05",
            "due_days": 30,
            "iban": None,
        },
    )
    payload = snapshot.model_dump(mode="json")
    assert payload["versione"] == 1
    restored = InvoiceSnapshot.model_validate(payload)
    assert restored == snapshot


def test_an_unversioned_snapshot_payload_is_refused() -> None:
    """A JSON blob written today is read by code three years from now; a payload with
    no version is interpreted by guessing. That is what the column costs and what it
    buys."""
    with pytest.raises(ValidationError):
        InvoiceSnapshot.model_validate({"emittente": {}, "customer": {}, "tax": {}})


def test_an_issue_request_may_carry_a_date_and_nothing_else() -> None:
    from pigrocrm.core.invoices.schemas import InvoiceIssue

    assert set(InvoiceIssue.model_fields) == {"issue_date"}
    assert InvoiceIssue(issue_date=date(2026, 8, 20)).issue_date == date(2026, 8, 20)
    assert InvoiceIssue().issue_date is None


def _line(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "description": "900142/0426/Consulenza AI CTO progetto Aurora",
        "quantity": Decimal("9"),
        "unit_price": Decimal("300"),
        "total_price": Decimal("2700.00"),
        "vat_rate": Decimal("0"),
        "vat_nature": "N2.2",
    }
    base.update(overrides)
    return base


def _import(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "year": 2026,
        "number": 7,
        "issue_date": date(2026, 5, 5),
        "customer_id": uuid4(),
        "lines": [_line()],
        "taxable_amount": Decimal("2700.00"),
        "vat_amount": Decimal("0.00"),
        "stamp_duty": Decimal("2.00"),
        "total": Decimal("3422.00"),
    }
    base.update(overrides)
    return base


def test_an_import_defaults_to_external_and_unpaid() -> None:
    data = InvoiceImport(**_import())
    assert data.imported_from == "external"
    assert data.payment_status == "unpaid"
    assert data.pdf_source is None


def test_an_import_refuses_unknown_fields_and_a_zero_number() -> None:
    with pytest.raises(ValidationError):
        InvoiceImport(**_import(number=0))
    with pytest.raises(ValidationError):
        InvoiceImport(**_import(xml_hash_sha256="abc"))
    with pytest.raises(ValidationError):
        InvoiceImport(**_import(lines=[]))


def test_an_imported_number_stops_at_the_register_ceiling() -> None:
    """`MAX_NUMBER` is enforced here, on the *declared* number, and not only on
    `InvoiceForExport`.

    Everywhere else the number is produced by the counter, so it cannot exceed the
    ceiling by accident; an import is the one place a caller names it. The previous system prints
    its own document ids as `900142`, one column away from the register number on the same
    screenshot: a slipped value would raise `last_number` to it -- irreversibly, since
    the counter never moves backwards -- make every later SdI file name ambiguous (it
    embeds `anno * 10000 + numero`), and turn `undeclared_gaps` into a
    two-hundred-thousand-element list. The refusal belongs at the schema, before the
    service takes the year's counter lock, which is why it is pinned here.
    """
    assert MAX_NUMBER == 9999
    InvoiceImport(**_import(number=MAX_NUMBER))
    with pytest.raises(ValidationError):
        InvoiceImport(**_import(number=MAX_NUMBER + 1))
    with pytest.raises(ValidationError):
        InvoiceImport(**_import(number=900142))


def test_a_declared_gap_stops_at_the_same_ceiling() -> None:
    """A gap is a number *of this register*: same range as an invoice's, so a batch
    cannot declare holes at numbers the register could never have carried."""
    RegisterGapsDeclare(gaps=[{"number": MAX_NUMBER, "reason": "mai emessa"}])  # type: ignore[list-item]
    with pytest.raises(ValidationError):
        RegisterGapsDeclare(gaps=[{"number": MAX_NUMBER + 1, "reason": "x"}])  # type: ignore[list-item]
    with pytest.raises(ValidationError):
        RegisterGapsDeclare(gaps=[{"number": 0, "reason": "x"}])  # type: ignore[list-item]


def test_an_import_has_no_reference_field() -> None:
    """`invoices.reference` is constrained to `NULL` on every `tipo = 'fattura'`
    row (`ck_invoices_reference_only_on_proforma`), and an import always produces
    a `invoice`: the field is absent from the schema, not merely optional, so a
    caller supplying it is refused here rather than at the database's own CHECK."""
    with pytest.raises(ValidationError):
        InvoiceImport(**_import(reference="x"))


def test_an_imported_line_carries_its_own_vat_nature_and_total() -> None:
    line = InvoiceLineImport(**_line())
    assert line.vat_nature == "N2.2"
    assert line.total_price == Decimal("2700.00")


def test_gaps_need_a_reason_each() -> None:
    with pytest.raises(ValidationError):
        RegisterGapsDeclare(gaps=[{"number": 4}])  # type: ignore[list-item]
    with pytest.raises(ValidationError):
        RegisterGapsDeclare(gaps=[])


# --- PdfSorgente (slice 9C task 4) ---------------------------------------------------


def test_to_pdf_source_names_exactly_one_place() -> None:
    """Two places the original PDF can already be -- a `documents` row (9A) or a file
    on Drive (9C) -- and never both: two sources would make "which one is the original"
    a question the service would have to answer by guessing.
    """
    assert PdfSource(document_id=uuid4()).drive_file_id is None
    assert PdfSource(drive_file_id="1PreventivoPdfXXXX").document_id is None
    with pytest.raises(ValidationError):
        PdfSource()
    with pytest.raises(ValidationError):
        PdfSource(document_id=uuid4(), drive_file_id="1PreventivoPdfXXXX")


def test_a_drive_file_id_is_held_to_the_strict_drive_shape() -> None:
    """The same pattern `drive/query.py` holds a caller-supplied id to, because this
    string reaches a Drive URL: ten characters at least (real ids are 28 to 44), and
    nothing outside `[A-Za-z0-9_-]` -- a space, a slash or a trailing newline is the
    shape an injection attempt has, not the shape an id has.
    """
    for bad in ["", "corto", "1Con Spazio Dentro", "1DueBarre/Dentro00", "1TrailingNewline\n"]:
        with pytest.raises(ValidationError):
            PdfSource(drive_file_id=bad)
    # A full-length real-world id passes untouched.
    assert PdfSource(drive_file_id="1a2B3c4D5e6F7g8H9i0J1k2L3m4N5o6P").drive_file_id


def test_an_import_can_declare_a_drive_file_as_its_original_pdf() -> None:
    data = InvoiceImport(**_import(pdf_source={"drive_file_id": "1PreventivoPdfXXXX"}))
    assert data.pdf_source is not None
    assert data.pdf_source.drive_file_id == "1PreventivoPdfXXXX"
    assert data.pdf_source.document_id is None


def test_the_purchase_order_is_carried_by_every_shape_that_reaches_a_document() -> None:
    """Settable on creation and on update, read back, and part of what the exporter
    and the PDF read (REB-693). Not on the import: reading `DatiOrdineAcquisto` back
    from an external XML is out of this card, and an import that half-supported it
    would store an order nobody checked."""
    from pigrocrm.core.invoices.schemas import InvoiceForExport, InvoiceImport, InvoiceRead

    order = {
        "purchase_order_number",
        "purchase_order_date",
        "purchase_order_tender_code",
        "purchase_order_project_code",
    }
    for model in (InvoiceCreate, InvoiceUpdate, InvoiceRead, InvoiceForExport):
        assert order <= set(model.model_fields), model.__name__
    assert not order & set(InvoiceImport.model_fields)
    assert InvoiceCreate(customer_id=uuid4()).purchase_order_number is None
    assert InvoiceForExport.model_fields["purchase_order_tender_code"].default is None


def test_the_purchase_order_widths_mirror_the_columns() -> None:
    with pytest.raises(ValidationError):
        InvoiceCreate(customer_id=uuid4(), purchase_order_number="x" * 21)
    with pytest.raises(ValidationError):
        InvoiceCreate(customer_id=uuid4(), purchase_order_tender_code="x" * 16)
    with pytest.raises(ValidationError):
        InvoiceUpdate(purchase_order_project_code="x" * 16)


def test_a_padded_purchase_order_code_is_not_refused_for_its_raw_width() -> None:
    """The service strips; the schema must strip first, or the 17 characters of
    `" J41E22000110005 "` fail `max_length=15` before the strip gets a say."""
    for model in (InvoiceCreate, InvoiceUpdate):
        kwargs = {} if model is InvoiceUpdate else {"customer_id": uuid4()}
        built = model(
            **kwargs,
            purchase_order_number="  ODA-1234567890123456  ",
            purchase_order_tender_code=" Z0A1B2C3D4  ",
            purchase_order_project_code=" J41E22000110005 ",
        )
        assert built.purchase_order_number == "ODA-1234567890123456"
        assert built.purchase_order_tender_code == "Z0A1B2C3D4"
        assert built.purchase_order_project_code == "J41E22000110005"
        with pytest.raises(ValidationError):
            model(**kwargs, purchase_order_project_code=" " + "x" * 16 + " ")
        with pytest.raises(ValidationError):
            model(**kwargs, purchase_order_number=" " + "x" * 21 + " ")
