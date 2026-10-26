"""`build_dispute_bundle` (REB-759): the ZIP of a contested day.

What these hold the code to: the archive carries the summary and, byte for byte, the original
the approval rests on; a day with no approval still gets a summary that says so; a file the
archive cannot read is said so rather than left out in silence; the summary names the approval,
the day's log, the month's register and the invoice line; every name in the archive is safe
for any filesystem; and the same day gives the same bytes twice.
"""

import hashlib
import io
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from conftest import _invoice_service
from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.models import Document, DocumentVersion
from pigrocrm.core.errors import Conflict, NotFound
from pigrocrm.core.invoices.schemas import InvoiceIssue
from pigrocrm.core.storage.local import LocalFileStorage
from pigrocrm.core.work_units.dispute_bundle import build_dispute_bundle, safe_entry_name
from pigrocrm.core.work_units.invoicing import assemble_unbilled_work_units_into_new_invoice
from pigrocrm.core.work_units.models import WorkUnit
from pigrocrm.core.work_units.schemas import ApprovalRecord
from pigrocrm.core.work_units.service import ApprovalService

WRITER = Actor(id=uuid4(), type="user", role="collaboratore")
ADMIN = Actor(id=None, type="system", role="admin")
READER = Actor(id=None, type="user", role="readonly")
EMAIL = b"From: giulia@cliente.example\nSubject: Ok\n\nConfermo le giornate di marzo.\n"


def _contract(session: Session) -> Contract:
    customer = Customer(
        ragione_sociale=f"Acme S.r.l. {uuid4().hex[:6]}",
        partita_iva="01234567890",
        codice_sdi="ABCDEFG",
        indirizzo="Corso Italia 5",
        cap="00100",
        comune="Roma",
        provincia="RM",
        nazione="IT",
    )
    session.add(customer)
    session.flush()
    contract = Contract(
        customer_id=customer.id,
        titolo="Consulenza Acme",
        inizio=date(2026, 1, 1),
        tipo_rinnovo="nessuno",
        preavviso_disdetta_giorni=30,
        cadenza_fatturazione="mensile",
        politica_spese={"kind": "non_rimborsabile"},
        requires_prior_approval=False,
    )
    session.add(contract)
    session.flush()
    session.add(
        RateCard(
            contract_id=contract.id,
            valido_da=date(2026, 1, 1),
            valido_a=None,
            tipo="giornaliero",
            importo=Decimal("500.00"),
            unita="giorno",
        )
    )
    session.flush()
    return contract


def _day(session: Session, contract: Contract, giorno: date, stato: str = "lavorato") -> WorkUnit:
    unit = WorkUnit(
        contract_id=contract.id,
        data=giorno,
        quantita=Decimal("1.00"),
        descrizione="Giornata di consulenza",
        stato=stato,
    )
    session.add(unit)
    session.flush()
    session.refresh(unit)
    return unit


def _filed_document(
    session: Session, storage: LocalFileStorage, contract: Contract, content: bytes = EMAIL
) -> Document:
    document = Document(
        contract_id=contract.id,
        tipo="documento",
        titolo="Mail di conferma di Giulia",
        versione_corrente=1,
    )
    session.add(document)
    session.flush()
    key = f"test/{document.id}/v1.txt"
    storage.put(key, content, "text/plain")
    session.add(
        DocumentVersion(
            document_id=document.id,
            numero=1,
            storage_key=key,
            content_type="text/plain",
            dimensione=len(content),
            hash_sha256="0" * 64,
        )
    )
    session.flush()
    return document


def _approve(
    session: Session, storage: LocalFileStorage, contract: Contract, units: list[WorkUnit]
):
    document = _filed_document(session, storage, contract)
    ApprovalService(session, storage).record(
        ApprovalRecord(
            contract_id=contract.id,
            canale="email",
            mittente="giulia@cliente.example",
            ricevuto_il=datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
            estratto="Confermo le giornate di marzo.",
            document_id=document.id,
            work_unit_ids=[unit.id for unit in units],
        ),
        WRITER,
    )
    return document


def _open(content: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(content))


def test_the_bundle_holds_the_summary_and_the_original_email_byte_for_byte(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    contract = _contract(db_session)
    proposed = _day(db_session, contract, date(2026, 3, 2), "proposto")
    _approve(db_session, local_storage, contract, [proposed])

    filename, content = build_dispute_bundle(db_session, local_storage, proposed.id, READER)

    assert filename == f"fascicolo-giornata-2026-03-02-{str(proposed.id)[:8]}.zip"
    archive = _open(content)
    names = archive.namelist()
    assert names[0] == "summary.txt"
    (source,) = [n for n in names if n.startswith("source/")]
    assert archive.read(source) == EMAIL
    summary = archive.read("summary.txt").decode()
    assert "FASCICOLO DELLA GIORNATA 2026-03-02" in summary
    assert "Contratto: Consulenza Acme" in summary
    assert "Mittente: giulia@cliente.example" in summary
    assert 'Estratto: "Confermo le giornate di marzo."' in summary
    assert "Ricevuta il: 2026-03-01T09:00:00" in summary
    assert f"{source}: «Mail di conferma di Giulia»" in summary
    assert f"SHA-256 {hashlib.sha256(EMAIL).hexdigest()}" in summary


def test_the_summary_carries_the_log_the_months_register_and_marks_the_day(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    contract = _contract(db_session)
    contested = _day(db_session, contract, date(2026, 3, 2))
    _day(db_session, contract, date(2026, 3, 3))
    _day(db_session, contract, date(2026, 4, 1))  # another month
    gone = _day(db_session, contract, date(2026, 3, 4), "proposto")
    gone.stato = "revocato"
    db_session.flush()
    contested.stato = "contestato"
    db_session.flush()

    _, content = build_dispute_bundle(db_session, local_storage, contested.id, READER)
    summary = _open(content).read("summary.txt").decode()

    assert "Registro del contratto, da 2026-03-01 a 2026-03-31" in summary
    assert "2026-03-02  1.00  contestato  (senza approvazione) <- questa giornata" in summary
    assert "2026-03-03  1.00  lavorato" in summary
    proposed = _day(db_session, contract, date(2026, 3, 9), "proposto")
    assert proposed.stato == "proposto"
    _, content = build_dispute_bundle(db_session, local_storage, contested.id, READER)
    summary = _open(content).read("summary.txt").decode()
    # Listed, but a proposal is not in the total.
    assert "2026-03-09  1.00  proposto" in summary
    assert "2026-04-01" not in summary and "2026-03-04" not in summary
    assert "Totale giornate del mese: 2.00 (" in summary
    assert "-> lavorato" in summary and "lavorato -> contestato" in summary


def test_a_day_with_no_approval_still_gets_a_summary_that_says_so(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    contract = _contract(db_session)
    unit = _day(db_session, contract, date(2026, 3, 2))

    _, content = build_dispute_bundle(db_session, local_storage, unit.id, READER)

    archive = _open(content)
    assert archive.namelist() == ["summary.txt"]
    summary = archive.read("summary.txt").decode()
    assert "Nessuna approvazione in archivio." in summary
    assert "la giornata non ha un'approvazione" in summary
    assert "Nessuna riga di fattura" in summary


def test_an_original_the_archive_cannot_read_is_said_so_not_left_out(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    contract = _contract(db_session)
    proposed = _day(db_session, contract, date(2026, 3, 2), "proposto")
    document = _approve(db_session, local_storage, contract, [proposed])
    db_session.query(DocumentVersion).filter_by(document_id=document.id).update({"numero": 2})
    document.versione_corrente = 1
    db_session.flush()

    _, content = build_dispute_bundle(db_session, local_storage, proposed.id, READER)

    archive = _open(content)
    assert archive.namelist() == ["summary.txt"]
    assert "non è leggibile dall'archivio" in archive.read("summary.txt").decode()


def test_a_storage_that_refuses_the_file_is_said_so_in_the_summary(
    db_session: Session, local_storage: LocalFileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract = _contract(db_session)
    proposed = _day(db_session, contract, date(2026, 3, 2), "proposto")
    _approve(db_session, local_storage, contract, [proposed])

    def refuse(*_: object, **__: object) -> bytes:
        raise Conflict("document", "l'archivio non risponde")

    monkeypatch.setattr(local_storage, "get", refuse)

    _, content = build_dispute_bundle(db_session, local_storage, proposed.id, READER)

    archive = _open(content)
    assert archive.namelist() == ["summary.txt"]
    assert "non è leggibile dall'archivio" in archive.read("summary.txt").decode()


def test_the_invoice_line_the_day_landed_on_is_in_the_summary(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _invoice_service(db_session, local_storage)
    contract = _contract(db_session)
    unit = _day(db_session, contract, date(2026, 3, 5))
    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )
    service.issue(invoice.id, InvoiceIssue(data_emissione=date(2026, 3, 31)), ADMIN)

    _, content = build_dispute_bundle(db_session, local_storage, unit.id, READER)
    summary = _open(content).read("summary.txt").decode()

    assert "Fattura 2026/1 (emessa, emessa il 2026-03-31)" in summary
    assert "Riga 1: Attività" in summary
    assert "Importo della riga: 500.00 EUR" in summary


def test_the_same_day_gives_the_same_bytes_twice(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    contract = _contract(db_session)
    proposed = _day(db_session, contract, date(2026, 3, 2), "proposto")
    _approve(db_session, local_storage, contract, [proposed])

    first = build_dispute_bundle(db_session, local_storage, proposed.id, READER)
    second = build_dispute_bundle(db_session, local_storage, proposed.id, READER)

    assert first == second


def test_an_unknown_day_is_not_found(db_session: Session, local_storage: LocalFileStorage) -> None:
    with pytest.raises(NotFound):
        build_dispute_bundle(db_session, local_storage, uuid4(), READER)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("mail.eml", "mail.eml"),
        ("../../etc/passwd", "etc-passwd"),
        ("/abs/path/x.pdf", "abs-path-x.pdf"),
        ("C:\\Windows\\evil.exe", "Windows-evil.exe"),
        ("a\x00b\x1f.txt", "ab.txt"),
        ("..", "fallback"),
        ("//", "fallback"),
        ("", "fallback"),
    ],
)
def test_an_archive_name_is_one_flat_component(name: str, expected: str) -> None:
    assert safe_entry_name(name, "fallback") == expected


def test_a_very_long_name_is_cut() -> None:
    assert len(safe_entry_name("x" * 500 + ".pdf", "fallback")) == 200
