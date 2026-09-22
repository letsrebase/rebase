"""Shapes only -- no session, no service. What is pinned here is the set of things
that reach Postgres if a schema forgets them: a width, a scale, a NUL byte, a bound.
"""

from datetime import date
from decimal import Decimal
from typing import get_args
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pigrocrm.core.documents.schemas import ALLOWED_CONTENT_TYPES, DocumentTipo
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.invoices.schemas import (
    ALLOWED_STATI,
    DESCRIZIONE_MAX_LENGTH,
    MAX_LINES,
    MAX_NUMERO,
    SNAPSHOT_VERSIONE,
    InvoiceCreate,
    InvoiceImport,
    InvoiceLineImport,
    InvoiceLineIn,
    InvoiceListQuery,
    InvoiceSnapshot,
    InvoiceUpdate,
    PartySnapshot,
    PdfSorgente,
    RegisterGapsDeclare,
)
from pigrocrm.core.schema_registry import CREATE_MODELS, ENTITY_TYPES, native_fields


def test_invoice_is_a_declared_entity_type_in_all_three_python_places() -> None:
    assert "invoice" in get_args(EntityType)
    assert "invoice" in ENTITY_TYPES
    assert CREATE_MODELS["invoice"] is InvoiceCreate


def test_native_fields_for_an_invoice_covers_the_derived_fiscal_columns() -> None:
    """`native_fields` derives from the Create schema, and an invoice's most
    collision-prone names -- `totale`, `imponibile`, `numero` -- are derived columns
    that no Create schema declares. A custom field labelled "Totale" slugifies to
    `totale`; A13 is still open, so nothing consults this list yet, but the list it
    will consult has to be right."""
    names = native_fields("invoice")
    assert {"customer_id", "tipo", "causale", "righe"} <= set(names)
    assert {"anno", "numero", "imponibile", "imposta", "bollo", "totale", "stato"} <= set(names)
    assert "custom_fields" not in names


def test_native_fields_for_the_older_entities_is_unchanged() -> None:
    """EXTRA_NATIVE_FIELDS is empty for them, so the derivation is still the whole
    answer and no existing behaviour moved."""
    assert native_fields("customer") == [
        name for name in CREATE_MODELS["customer"].model_fields if name != "custom_fields"
    ]


def test_documents_learns_the_three_invoice_artefact_types() -> None:
    assert {"fattura", "fattura_xml", "proforma"} <= set(get_args(DocumentTipo))
    assert ALLOWED_CONTENT_TYPES["application/xml"] == ".xml"


def test_the_state_machines_are_declared_per_tipo() -> None:
    """Two state machines in one column would be ambiguous, so the legal pairs are
    data here and a table constraint in the database."""
    assert {
        "fattura": frozenset({"bozza", "emessa", "annullata"}),
        "proforma": frozenset({"bozza", "confermata", "consumata"}),
    } == ALLOWED_STATI


def test_a_line_rejects_a_nul_byte_in_its_description() -> None:
    with pytest.raises(ValidationError):
        InvoiceLineIn(descrizione="Consulenza\x00", prezzo_unitario=Decimal("100.00"))


def test_a_line_description_is_bounded_to_the_column_width() -> None:
    with pytest.raises(ValidationError):
        InvoiceLineIn(
            descrizione="x" * (DESCRIZIONE_MAX_LENGTH + 1), prezzo_unitario=Decimal("100.00")
        )


def test_a_unit_price_keeps_six_decimals_and_refuses_a_seventh() -> None:
    """Numeric(12, 6): six decimals is what makes 33,3333 EUR/h expressible, and a
    seventh would be silently rounded by Postgres while the response still reported
    the original."""
    assert InvoiceLineIn(
        descrizione="Consulenza", prezzo_unitario=Decimal("33.333333")
    ).prezzo_unitario == Decimal("33.333333")
    with pytest.raises(ValidationError):
        InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("33.3333331"))


def test_a_unit_price_beyond_twelve_digits_is_refused_before_postgres_sees_it() -> None:
    with pytest.raises(ValidationError):
        InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("1234567.000000"))


def test_a_line_never_carries_its_own_natura() -> None:
    """`natura` and `riferimento_normativo` are the regime's answer, not the caller's:
    accepting them would make the table constraint
    `(aliquota_iva = 0) = (natura IS NOT NULL)` reachable from a request body."""
    assert "natura" not in InvoiceLineIn.model_fields
    assert "riferimento_normativo" not in InvoiceLineIn.model_fields
    with pytest.raises(ValidationError):
        InvoiceLineIn(descrizione="x", prezzo_unitario=Decimal("1.00"), natura="N2.2")


def test_an_invoice_cannot_be_created_with_more_lines_than_the_bound() -> None:
    line = {"descrizione": "x", "prezzo_unitario": "1.00"}
    with pytest.raises(ValidationError):
        InvoiceCreate(customer_id=uuid4(), righe=[line] * (MAX_LINES + 1))


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
        "causale",
        "note_interne",
        "custom_fields",
        "competenza_da",
        "competenza_a",
        "data_emissione",
        "data_scadenza",
    }


def test_the_accrual_period_is_carried_by_every_shape_that_reaches_a_document() -> None:
    """Settable on creation and on import, read back, and part of what the exporter and
    the PDF read -- the period is on the document (ORB-61), so a shape that dropped it
    would print or export a document that says less than the row."""
    from pigrocrm.core.invoices.schemas import InvoiceForExport, InvoiceImport, InvoiceRead

    for model in (InvoiceCreate, InvoiceUpdate, InvoiceImport, InvoiceRead, InvoiceForExport):
        assert {"competenza_da", "competenza_a"} <= set(model.model_fields), model.__name__
    assert InvoiceCreate(customer_id=uuid4()).competenza_da is None
    assert InvoiceForExport.model_fields["competenza_da"].default is None


def test_the_list_query_limit_is_bounded_in_the_schema_not_only_the_router() -> None:
    assert InvoiceListQuery().limit == 50
    with pytest.raises(ValidationError):
        InvoiceListQuery(limit=201)


def test_a_snapshot_round_trips_through_json_with_its_version() -> None:
    party = PartySnapshot(
        ragione_sociale="Rossi & C.",
        partita_iva="12345678901",
        codice_fiscale=None,
        codice_sdi="ABCDEFG",
        pec=None,
        indirizzo="Via Roma 1",
        cap="20100",
        comune="Milano",
        provincia="MI",
        nazione="IT",
        email=None,
        telefono=None,
        sito_web=None,
    )
    snapshot = InvoiceSnapshot(
        versione=SNAPSHOT_VERSIONE,
        emittente=party,
        cliente=party,
        fiscale={
            "codice_regime": "RF19",
            "aliquota_iva_default": Decimal("0.00"),
            "natura_default": "N2.2",
            "riferimento_normativo": "art. 1",
            "applica_bollo": True,
            "soglia_bollo": Decimal("77.47"),
            "importo_bollo": Decimal("2.00"),
            "condizioni_pagamento": "TP02",
            "modalita_pagamento": "MP05",
            "giorni_scadenza": 30,
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
        InvoiceSnapshot.model_validate({"emittente": {}, "cliente": {}, "fiscale": {}})


def test_an_issue_request_may_carry_a_date_and_nothing_else() -> None:
    from pigrocrm.core.invoices.schemas import InvoiceIssue

    assert set(InvoiceIssue.model_fields) == {"data_emissione"}
    assert InvoiceIssue(data_emissione=date(2026, 8, 20)).data_emissione == date(2026, 8, 20)
    assert InvoiceIssue().data_emissione is None


def _line(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "descrizione": "900142/0426/Consulenza AI CTO progetto Aurora",
        "quantita": Decimal("9"),
        "prezzo_unitario": Decimal("300"),
        "prezzo_totale": Decimal("2700.00"),
        "aliquota_iva": Decimal("0"),
        "natura": "N2.2",
    }
    base.update(overrides)
    return base


def _import(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "anno": 2026,
        "numero": 7,
        "data_emissione": date(2026, 5, 5),
        "customer_id": uuid4(),
        "righe": [_line()],
        "imponibile": Decimal("2700.00"),
        "imposta": Decimal("0.00"),
        "bollo": Decimal("2.00"),
        "totale": Decimal("3422.00"),
    }
    base.update(overrides)
    return base


def test_an_import_defaults_to_esterno_and_da_incassare() -> None:
    data = InvoiceImport(**_import())
    assert data.importata_da == "esterno"
    assert data.stato_pagamento == "da_incassare"
    assert data.pdf_sorgente is None


def test_an_import_refuses_unknown_fields_and_a_zero_number() -> None:
    with pytest.raises(ValidationError):
        InvoiceImport(**_import(numero=0))
    with pytest.raises(ValidationError):
        InvoiceImport(**_import(xml_hash_sha256="abc"))
    with pytest.raises(ValidationError):
        InvoiceImport(**_import(righe=[]))


def test_an_imported_number_stops_at_the_register_ceiling() -> None:
    """`MAX_NUMERO` is enforced here, on the *declared* number, and not only on
    `InvoiceForExport`.

    Everywhere else the number is produced by the counter, so it cannot exceed the
    ceiling by accident; an import is the one place a caller names it. The previous system prints
    its own document ids as `900142`, one column away from the register number on the same
    screenshot: a slipped value would raise `ultimo_numero` to it -- irreversibly, since
    the counter never moves backwards -- make every later SdI file name ambiguous (it
    embeds `anno * 10000 + numero`), and turn `undeclared_gaps` into a
    two-hundred-thousand-element list. The refusal belongs at the schema, before the
    service takes the year's counter lock, which is why it is pinned here.
    """
    assert MAX_NUMERO == 9999
    InvoiceImport(**_import(numero=MAX_NUMERO))
    with pytest.raises(ValidationError):
        InvoiceImport(**_import(numero=MAX_NUMERO + 1))
    with pytest.raises(ValidationError):
        InvoiceImport(**_import(numero=900142))


def test_a_declared_gap_stops_at_the_same_ceiling() -> None:
    """A gap is a number *of this register*: same range as an invoice's, so a batch
    cannot declare holes at numbers the register could never have carried."""
    RegisterGapsDeclare(buchi=[{"numero": MAX_NUMERO, "motivo": "mai emessa"}])  # type: ignore[list-item]
    with pytest.raises(ValidationError):
        RegisterGapsDeclare(buchi=[{"numero": MAX_NUMERO + 1, "motivo": "x"}])  # type: ignore[list-item]
    with pytest.raises(ValidationError):
        RegisterGapsDeclare(buchi=[{"numero": 0, "motivo": "x"}])  # type: ignore[list-item]


def test_an_import_has_no_riferimento_field() -> None:
    """`invoices.riferimento` is constrained to `NULL` on every `tipo = 'fattura'`
    row (`ck_invoices_riferimento_only_on_proforma`), and an import always produces
    a `fattura`: the field is absent from the schema, not merely optional, so a
    caller supplying it is refused here rather than at the database's own CHECK."""
    with pytest.raises(ValidationError):
        InvoiceImport(**_import(riferimento="x"))


def test_an_imported_line_carries_its_own_natura_and_total() -> None:
    line = InvoiceLineImport(**_line())
    assert line.natura == "N2.2"
    assert line.prezzo_totale == Decimal("2700.00")


def test_gaps_need_a_reason_each() -> None:
    with pytest.raises(ValidationError):
        RegisterGapsDeclare(buchi=[{"numero": 4}])  # type: ignore[list-item]
    with pytest.raises(ValidationError):
        RegisterGapsDeclare(buchi=[])


# --- PdfSorgente (slice 9C task 4) ---------------------------------------------------


def test_a_pdf_sorgente_names_exactly_one_place() -> None:
    """Two places the original PDF can already be -- a `documents` row (9A) or a file
    on Drive (9C) -- and never both: two sources would make "which one is the original"
    a question the service would have to answer by guessing.
    """
    assert PdfSorgente(document_id=uuid4()).drive_file_id is None
    assert PdfSorgente(drive_file_id="1PreventivoPdfXXXX").document_id is None
    with pytest.raises(ValidationError):
        PdfSorgente()
    with pytest.raises(ValidationError):
        PdfSorgente(document_id=uuid4(), drive_file_id="1PreventivoPdfXXXX")


def test_a_drive_file_id_is_held_to_the_strict_drive_shape() -> None:
    """The same pattern `drive/query.py` holds a caller-supplied id to, because this
    string reaches a Drive URL: ten characters at least (real ids are 28 to 44), and
    nothing outside `[A-Za-z0-9_-]` -- a space, a slash or a trailing newline is the
    shape an injection attempt has, not the shape an id has.
    """
    for bad in ["", "corto", "1Con Spazio Dentro", "1DueBarre/Dentro00", "1TrailingNewline\n"]:
        with pytest.raises(ValidationError):
            PdfSorgente(drive_file_id=bad)
    # A full-length real-world id passes untouched.
    assert PdfSorgente(drive_file_id="1a2B3c4D5e6F7g8H9i0J1k2L3m4N5o6P").drive_file_id


def test_an_import_can_declare_a_drive_file_as_its_original_pdf() -> None:
    data = InvoiceImport(**_import(pdf_sorgente={"drive_file_id": "1PreventivoPdfXXXX"}))
    assert data.pdf_sorgente is not None
    assert data.pdf_sorgente.drive_file_id == "1PreventivoPdfXXXX"
    assert data.pdf_sorgente.document_id is None
