"""The XML as a stored artefact: one `documents` row, one hash, byte-identical forever.

Two `documents` rows per issued invoice, not one (spec 8.4): a `document_versions`
chain is a linear history of one logical file with one `hash_sha256` used for
deduplication and integrity, so putting the PDF and the XML in the same chain would
make "version 3" ambiguous and the two hashes incomparable.
"""

import hashlib
from decimal import Decimal
from uuid import UUID

import pytest
from fpr12 import assert_valid
from lxml import etree
from sqlalchemy import text
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.emitter.schemas import AziendaUpsert
from pigrocrm.core.emitter.service import AziendaService
from pigrocrm.core.errors import Conflict, NotFound
from pigrocrm.core.fiscal.schemas import (
    DEFAULT_RIFERIMENTO_NORMATIVO,
    RIFERIMENTO_NORMATIVO_EXTRA_UE,
    RIFERIMENTO_NORMATIVO_UE,
    FiscalProfileUpsert,
)
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceIssue, InvoiceLineIn
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="system", role="admin")


@pytest.fixture
def storage(tmp_path) -> LocalFileStorage:  # type: ignore[no-untyped-def]
    return LocalFileStorage(tmp_path / "documents")


@pytest.fixture
def service(db_session: Session, storage: LocalFileStorage) -> InvoiceService:
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)
    AziendaService(db_session).upsert_default(
        AziendaUpsert(
            ragione_sociale="Studio Rossi",
            partita_iva="01234567890",
            codice_fiscale="HMCRFT00A01H501K",
            indirizzo="Via Vittorio Veneto 12",
            cap="20124",
            comune="Milano",
            provincia="MI",
            nazione="IT",
            email="mario@example.com",
        ),
        ADMIN,
    )
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


def _issue(service: InvoiceService, customer_id: UUID) -> UUID:
    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            causale="Consulenza",
            righe=[InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("1000.00"))],
        ),
        ADMIN,
    )
    return service.issue(draft.id, InvoiceIssue(), ADMIN).id


def test_the_first_export_writes_a_document_a_version_and_the_hash(
    service: InvoiceService, customer_id: UUID
) -> None:
    invoice_id = _issue(service, customer_id)
    artifact = service.export_xml(invoice_id, ADMIN)
    invoice = service.get(invoice_id, ADMIN)

    assert artifact.kind == "xml"
    assert artifact.version_numero == 1
    assert artifact.content_type == "application/xml"
    assert invoice.xml_document_id == artifact.document_id
    assert invoice.xml_hash_sha256 == artifact.hash_sha256


def test_the_stored_bytes_validate_against_the_official_schema(
    service: InvoiceService, customer_id: UUID
) -> None:
    invoice_id = _issue(service, customer_id)
    service.export_xml(invoice_id, ADMIN)
    data, content_type, filename = service.download(invoice_id, "xml", ADMIN)
    assert_valid(data)
    assert content_type == "application/xml"
    assert filename.startswith("IT")
    assert filename.endswith(".xml")


def test_the_download_name_follows_the_sdi_convention_using_the_frozen_emitter_id(
    service: InvoiceService, customer_id: UUID
) -> None:
    """The name comes from the snapshot, not from the live profile: the file goes to an
    intermediary that often validates the name before the content, and it must not
    change because the issuer edited their profile afterwards."""
    invoice_id = _issue(service, customer_id)
    service.export_xml(invoice_id, ADMIN)
    _, _, before = service.download(invoice_id, "xml", ADMIN)
    AziendaService(service.session).upsert_default(
        AziendaUpsert(
            ragione_sociale="Altro Nome",
            partita_iva="01234567890",
            codice_fiscale="RSSMRA80A01H501U",
            indirizzo="Via Nuova 1",
            cap="20125",
            comune="Milano",
            provincia="MI",
            nazione="IT",
        ),
        ADMIN,
    )
    _, _, after = service.download(invoice_id, "xml", ADMIN)
    assert before == after
    assert before.startswith("ITHMCRFT00A01H501K_")


def test_the_bytes_live_under_the_fiscal_prefix(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    invoice_id = _issue(service, customer_id)
    artifact = service.export_xml(invoice_id, ADMIN)
    key = db_session.execute(
        text(
            "SELECT storage_key FROM document_versions WHERE document_id = :id AND numero = :numero"
        ),
        {"id": artifact.document_id, "numero": artifact.version_numero},
    ).scalar_one()
    invoice = service.get(invoice_id, ADMIN)
    assert key == f"fatture/{invoice.anno}/{invoice.numero}/v1.xml"


def test_the_xml_gets_its_own_documents_row_typed_fattura_xml(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    invoice_id = _issue(service, customer_id)
    artifact = service.export_xml(invoice_id, ADMIN)
    tipo, stato = db_session.execute(
        text("SELECT tipo, stato FROM documents WHERE id = :id"), {"id": artifact.document_id}
    ).one()
    assert tipo == "fattura_xml"
    # `documents.stato` stays NULL for all three invoice artefact types: the
    # authoritative state is the invoice's. Duplicating a state machine in two tables
    # produces two truths.
    assert stato is None


def test_re_exporting_returns_the_same_artifact_without_writing_a_second_version(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    invoice_id = _issue(service, customer_id)
    first = service.export_xml(invoice_id, ADMIN)
    second = service.export_xml(invoice_id, ADMIN)
    assert second == first
    versions = db_session.execute(
        text("SELECT count(*) FROM document_versions WHERE document_id = :id"),
        {"id": first.document_id},
    ).scalar_one()
    assert versions == 1


def test_a_regenerated_export_is_byte_identical_to_the_original(
    service: InvoiceService, customer_id: UUID
) -> None:
    """Criterion 6 for the XML half: with the emitter and fiscal profiles changed in
    the meantime, the bytes still match, because the exporter reads the snapshot and
    nothing else."""
    invoice_id = _issue(service, customer_id)
    service.export_xml(invoice_id, ADMIN)
    original, _, _ = service.download(invoice_id, "xml", ADMIN)

    FiscalProfileService(service.session).upsert(
        FiscalProfileUpsert(
            codice_regime="RF01",
            aliquota_iva_default=Decimal("22.00"),
            natura_default=None,
            riferimento_normativo=None,
            giorni_scadenza=60,
        ),
        ADMIN,
    )
    AziendaService(service.session).upsert_default(
        AziendaUpsert(
            ragione_sociale="Altro Nome",
            partita_iva="01234567890",
            codice_fiscale="HMCRFT00A01H501K",
            indirizzo="Via Nuova 1",
            cap="20125",
            comune="Torino",
            provincia="TO",
            nazione="IT",
        ),
        ADMIN,
    )

    service.export_xml(invoice_id, ADMIN)
    again, _, _ = service.download(invoice_id, "xml", ADMIN)
    assert again == original
    assert hashlib.sha256(again).hexdigest() == service.get(invoice_id, ADMIN).xml_hash_sha256


def test_a_lost_file_is_repaired_as_a_new_identical_version(
    service: InvoiceService, storage: LocalFileStorage, db_session: Session, customer_id: UUID
) -> None:
    """Spec 4: a new version whose content is byte-for-byte the previous one is a
    repair, not a modification. The stored bytes are deleted behind the service's back
    and the export puts them back."""
    invoice_id = _issue(service, customer_id)
    artifact = service.export_xml(invoice_id, ADMIN)
    key = db_session.execute(
        text("SELECT storage_key FROM document_versions WHERE document_id = :id"),
        {"id": artifact.document_id},
    ).scalar_one()
    storage.delete(key)

    repaired = service.export_xml(invoice_id, ADMIN)
    assert repaired.version_numero == 2
    assert repaired.hash_sha256 == artifact.hash_sha256
    data, _, _ = service.download(invoice_id, "xml", ADMIN)
    assert hashlib.sha256(data).hexdigest() == artifact.hash_sha256


def test_a_divergent_export_is_an_error_to_report_not_a_version_to_save(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    """Spec 4, said exactly: "a divergence is an error to report, not a version to
    save". The snapshot is tampered with directly, which is the only way to make the
    generator produce different bytes for the same invoice -- and precisely the kind of
    out-of-band edit this check exists to catch."""
    invoice_id = _issue(service, customer_id)
    service.export_xml(invoice_id, ADMIN)
    db_session.execute(
        text(
            "UPDATE invoices SET snapshot = jsonb_set(snapshot, "
            "'{cliente,ragione_sociale}', '\"Altro Cliente\"') WHERE id = :id"
        ),
        {"id": invoice_id},
    )
    db_session.expire_all()
    with pytest.raises(Conflict) as caught:
        service.export_xml(invoice_id, ADMIN)
    assert "xml_hash_sha256" in str(caught.value.details)


def test_a_proforma_produces_no_xml(service: InvoiceService, customer_id: UUID) -> None:
    """Mechanism 2 of the four: the exporter refuses on the basis of the row's own
    **state**, never on a flag passed by the caller."""
    proforma = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            tipo="proforma",
            righe=[InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("100.00"))],
        ),
        ADMIN,
    )
    with pytest.raises(Conflict) as caught:
        service.export_xml(proforma.id, ADMIN)
    assert caught.value.details["entity"] == "invoice"


def test_a_draft_produces_no_xml(service: InvoiceService, customer_id: UUID) -> None:
    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            righe=[InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("100.00"))],
        ),
        ADMIN,
    )
    with pytest.raises(Conflict):
        service.export_xml(draft.id, ADMIN)


def test_an_annulled_invoice_still_exports_its_xml(
    service: InvoiceService, customer_id: UUID
) -> None:
    """An annulled invoice consumed a number and remains a document of record; being
    unable to produce its file would make the register unauditable."""
    from pigrocrm.core.invoices.schemas import InvoiceAnnul

    invoice_id = _issue(service, customer_id)
    service.export_xml(invoice_id, ADMIN)
    service.annul(invoice_id, InvoiceAnnul(motivo="importo errato"), ADMIN)
    assert service.export_xml(invoice_id, ADMIN).kind == "xml"


def test_downloading_an_xml_that_was_never_exported_is_not_found(
    service: InvoiceService, customer_id: UUID
) -> None:
    invoice_id = _issue(service, customer_id)
    with pytest.raises(NotFound):
        service.download(invoice_id, "xml", ADMIN)


def test_the_hostile_customer_name_survives_the_whole_round_trip(
    service: InvoiceService, db_session: Session
) -> None:
    """Criterion 2, end to end through storage rather than in memory."""
    hostile = 'Rossi & C. <IdCodice>999</IdCodice> "#$@\\ ]]>'
    customer = Customer(
        ragione_sociale=hostile,
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
    invoice_id = _issue(service, customer.id)
    service.export_xml(invoice_id, ADMIN)
    data, _, _ = service.download(invoice_id, "xml", ADMIN)
    assert_valid(data)
    root = etree.fromstring(data)
    # Unqualified, not `{fpr12-namespace}Tag`: the schema's `elementFormDefault` is
    # "unqualified", so only the root `FatturaElettronica` element carries the `p:`
    # namespace prefix -- every descendant, including `Denominazione` and `IdCodice`,
    # is emitted with no namespace at all (confirmed in Task 6 against the real,
    # vendored XSD; a namespace-qualified query here finds nothing, not "not yet").
    assert hostile in [element.text for element in root.iter("Denominazione")]
    assert len(list(root.iter("IdCodice"))) == 3


def _non_resident_customer(db_session: Session, nazione: str = "GB") -> UUID:
    customer = Customer(
        ragione_sociale="Example Ltd",
        partita_iva="GB123456789",
        indirizzo="1 Old Street",
        cap="EC1V 9HL",
        comune="London",
        provincia="",
        nazione=nazione,
    )
    db_session.add(customer)
    db_session.flush()
    return customer.id


@pytest.mark.parametrize(
    ("nazione", "riferimento"),
    [("GB", RIFERIMENTO_NORMATIVO_EXTRA_UE), ("FR", RIFERIMENTO_NORMATIVO_UE)],
)
def test_the_xml_for_a_non_resident_customer_carries_n2_1_and_the_7_ter_reference(
    service: InvoiceService, db_session: Session, nazione: str, riferimento: str
) -> None:
    """ORB-32, on the surface the SdI reads. Every line and the summary group say
    `N2.1`, the summary's `RiferimentoNormativo` carries the art. 21 c. 6-bis
    annotation for where the customer is, and the file validates against FPR12 1.2.3
    in both cases."""
    invoice_id = _issue(service, _non_resident_customer(db_session, nazione))
    service.export_xml(invoice_id, ADMIN)
    data, _, _ = service.download(invoice_id, "xml", ADMIN)
    assert_valid(data)

    root = etree.fromstring(data)
    body = root.find("FatturaElettronicaBody")
    assert body is not None
    assert [n.text for n in body.iter("Natura")] == ["N2.1", "N2.1"]
    assert body.findtext("DatiBeniServizi/DatiRiepilogo/RiferimentoNormativo") == riferimento


def test_a_foreign_customer_with_a_real_postcode_is_issued_and_exported(
    service: InvoiceService, db_session: Session
) -> None:
    """ORB-38, end to end. `issue` consumed a register number for a GB customer with
    `EC1V 9HL` and `export_xml` then refused on the five-digit `CAP` pattern, forever.
    Now the same customer issues, the file validates against FPR12 1.2.3, `CAP` carries
    the `00000` the specifications prescribe for a foreign address, `Provincia` is
    omitted, and the postcode survives at the end of `Indirizzo`."""
    invoice_id = _issue(service, _non_resident_customer(db_session))
    assert service.get(invoice_id, ADMIN).numero is not None
    service.export_xml(invoice_id, ADMIN)
    data, _, _ = service.download(invoice_id, "xml", ADMIN)
    assert_valid(data)

    sede = etree.fromstring(data).find(".//CessionarioCommittente/Sede")
    assert sede is not None
    assert sede.findtext("CAP") == "00000"
    assert sede.find("Provincia") is None
    assert sede.findtext("Indirizzo") == "1 Old Street, EC1V 9HL"
    assert sede.findtext("Nazione") == "GB"


def test_the_xml_for_an_italian_customer_keeps_n2_2_and_the_domestic_declaration(
    service: InvoiceService, customer_id: UUID
) -> None:
    invoice_id = _issue(service, customer_id)
    service.export_xml(invoice_id, ADMIN)
    data, _, _ = service.download(invoice_id, "xml", ADMIN)
    root = etree.fromstring(data)
    body = root.find("FatturaElettronicaBody")
    assert body is not None
    assert [n.text for n in body.iter("Natura")] == ["N2.2", "N2.2"]
    assert body.findtext("DatiBeniServizi/DatiRiepilogo/RiferimentoNormativo") == (
        DEFAULT_RIFERIMENTO_NORMATIVO
    )


# --- the accrual period reaches the stored file (ORB-61) ------------------------------


def test_the_stored_xml_carries_the_frozen_accrual_period_on_every_line(
    service: InvoiceService, customer_id: UUID
) -> None:
    """End to end: the period set on the draft is the one the exported, schema-valid
    file says on each `DettaglioLinee`. The export reads the row `issue` froze, and the
    two columns it reads are the ones `update` refuses to change after emission, so what
    the file says is what the document said when it took its number."""
    from datetime import date

    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            causale="Consulenza agosto",
            competenza_da=date(2026, 8, 1),
            competenza_a=date(2026, 8, 31),
            righe=[
                InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("1000.00")),
                InvoiceLineIn(descrizione="Sviluppo", prezzo_unitario=Decimal("500.00")),
            ],
        ),
        ADMIN,
    )
    invoice_id = service.issue(draft.id, InvoiceIssue(), ADMIN).id
    artifact = service.export_xml(invoice_id, ADMIN)
    data, _, _ = service.download(invoice_id, "xml", ADMIN)
    assert hashlib.sha256(data).hexdigest() == artifact.hash_sha256
    assert_valid(data)
    linee = list(etree.fromstring(data).iter("DettaglioLinee"))
    assert len(linee) == 2
    assert [linea.findtext("DataInizioPeriodo") for linea in linee] == ["2026-08-01"] * 2
    assert [linea.findtext("DataFinePeriodo") for linea in linee] == ["2026-08-31"] * 2
