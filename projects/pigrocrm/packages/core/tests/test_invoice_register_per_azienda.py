"""The register, the import and the regime per azienda (REB-619, spec 2026-10-03
§1.3, §1.4, §1.5, §8 «Register»).

A space with two aziende: each keeps a yearly counter of its own, an import lands on
the azienda whose P.IVA issued the file, and a foreign azienda issues a PDF with no
bollo and no FatturaPA. The second azienda is written by row here, since no route or
tool creates one before milestone 5 (§9), and a draft is moved onto it by row for the
same reason: the customer chain that assigns it is milestone 3.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from test_invoice_import_review import CONSULENZA, _fixture

from pigrocrm.core.actor import Actor
from pigrocrm.core.clock import oggi_in_italia
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import session_factory
from pigrocrm.core.documents.schemas import DocumentCreate
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.emitter.models import LegalEntity
from pigrocrm.core.emitter.repository import LegalEntityRepository
from pigrocrm.core.emitter.schemas import LegalEntityUpsert
from pigrocrm.core.emitter.service import LegalEntityService
from pigrocrm.core.errors import Conflict, ValidationFailed
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices import pdf as invoice_pdf
from pigrocrm.core.invoices.models import Invoice, InvoiceCounter
from pigrocrm.core.invoices.repository import InvoiceRepository
from pigrocrm.core.invoices.schemas import (
    InvoiceCreate,
    InvoiceImport,
    InvoiceIssue,
    InvoiceLineImport,
    InvoiceLineIn,
    RegisterGapIn,
    RegisterGapsDeclare,
)
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="system", role="admin")
# `oggi_in_italia()`, not `date.today()`: `issue` dates the invoice in Rome, and a CI
# host in UTC is a year behind it for an hour on New Year's Eve (see `clock.py`).
ANNO = oggi_in_italia().year
# The FPR12 fixture's own issuer (`fixtures/fatturapa/fpr12-consulenza-marzo.xml`).
FIXTURE_PIVA = "01234567890"
FIXTURE_CF = "BNCCHR85M41H501Z"


@pytest.fixture
def service(db_session: Session, local_storage: LocalFileStorage) -> InvoiceService:
    """The default azienda with an identity the FatturaPA checks accept, and its
    profile; the fixture's P.IVA is deliberately *not* this one, so a test that wants
    the file to land on the second azienda gives that one the fixture's ids."""
    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(
            nome="humancraft",
            ragione_sociale="Studio Rossi",
            partita_iva="09876543210",
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
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)
    return InvoiceService(db_session, local_storage)


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


def _default_id(session: Session) -> UUID:
    azienda = LegalEntityRepository(session).default()
    assert azienda is not None
    return azienda.id


def _second_azienda(session: Session, **overrides: object) -> LegalEntity:
    """An Italian SRL beside the default, with a fiscal profile of its own."""
    values: dict[str, object] = {
        "nome": "rebase",
        "ragione_sociale": "Rebase S.r.l.",
        "partita_iva": FIXTURE_PIVA,
        "codice_fiscale": FIXTURE_CF,
        "indirizzo": "Via Po 1",
        "cap": "10100",
        "comune": "Torino",
        "provincia": "TO",
        "nazione": "IT",
        "email": "fatture@rebase.it",
    }
    values.update(overrides)
    row = LegalEntity(**values)
    session.add(row)
    session.flush()
    FiscalProfileService(session).upsert(
        FiscalProfileUpsert(codice_regime="RF19"), ADMIN, azienda_id=row.id
    )
    return row


def _foreign_azienda(session: Session) -> LegalEntity:
    """A company established abroad, on the `non-it` pack: no regime code, 20% as
    the rate it enters, nothing of the forfettario's arithmetic."""
    row = LegalEntity(
        nome="rebase ltd",
        ragione_sociale="Rebase Ltd",
        partita_iva="GB123456789",
        nazione="GB",
        indirizzo="1 Fleet Street",
        cap="EC4Y 1AA",
        comune="London",
    )
    session.add(row)
    session.flush()
    FiscalProfileService(session).upsert(
        FiscalProfileUpsert(
            pack_id="non-it",
            aliquota_iva_default=Decimal("20.00"),
            natura_default=None,
            riferimento_normativo=None,
        ),
        ADMIN,
        azienda_id=row.id,
    )
    return row


def _draft_on(
    service: InvoiceService, session: Session, customer_id: UUID, azienda: LegalEntity | None
) -> UUID:
    """A draft, moved onto `azienda` by row: the service writes every new document on
    the default azienda until milestone 3 derives it from the customer."""
    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            righe=[InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("100.00"))],
        ),
        ADMIN,
    )
    if azienda is not None:
        row = session.get(Invoice, draft.id)
        assert row is not None
        row.azienda_id = azienda.id
        session.flush()
    return draft.id


def _import_payload(customer_id: UUID, *, numero: int, giorno: date) -> InvoiceImport:
    return InvoiceImport(
        customer_id=customer_id,
        anno=giorno.year,
        numero=numero,
        data_emissione=giorno,
        righe=[
            InvoiceLineImport(
                descrizione="Consulenza",
                quantita=Decimal("1"),
                prezzo_unitario=Decimal("100.00"),
                prezzo_totale=Decimal("100.00"),
                aliquota_iva=Decimal("0.00"),
                natura="N2.2",
            )
        ],
        imponibile=Decimal("100.00"),
        imposta=Decimal("0.00"),
        bollo=Decimal("2.00"),
        totale=Decimal("100.00"),
    )


# --- the register ----------------------------------------------------------------


def test_two_aziende_issue_in_the_same_year_and_both_get_number_one(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    second = _second_azienda(db_session)
    first = service.issue(_draft_on(service, db_session, customer_id, None), InvoiceIssue(), ADMIN)
    other = service.issue(
        _draft_on(service, db_session, customer_id, second), InvoiceIssue(), ADMIN
    )
    assert (first.anno, first.numero) == (ANNO, 1)
    assert (other.anno, other.numero) == (ANNO, 1)
    assert first.azienda_id == _default_id(db_session)
    assert other.azienda_id == second.id
    # Two counters, one per register, each at 1.
    default_counter = db_session.get(InvoiceCounter, (_default_id(db_session), ANNO))
    second_counter = db_session.get(InvoiceCounter, (second.id, ANNO))
    assert default_counter is not None and default_counter.ultimo_numero == 1
    assert second_counter is not None and second_counter.ultimo_numero == 1
    # The frozen issuer is the azienda the document belongs to, not the default.
    row = db_session.get(Invoice, other.id)
    assert row is not None and row.snapshot is not None
    assert row.snapshot["emittente"]["ragione_sociale"] == "Rebase S.r.l."
    # And the next one on the default goes to 2, untouched by the other register.
    again = service.issue(_draft_on(service, db_session, customer_id, None), InvoiceIssue(), ADMIN)
    assert again.numero == 2


def test_an_artifact_of_the_second_azienda_lives_under_its_own_prefix(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    second = _second_azienda(db_session)
    issued = service.issue(
        _draft_on(service, db_session, customer_id, second), InvoiceIssue(), ADMIN
    )
    artifact = service.export_xml(issued.id, ADMIN)
    key = db_session.execute(
        text(
            "SELECT storage_key FROM document_versions WHERE document_id = :id AND numero = :numero"
        ),
        {"id": artifact.document_id, "numero": artifact.version_numero},
    ).scalar_one()
    assert key == f"fatture/{second.id}/{issued.anno}/{issued.numero}/v1.xml"


def test_an_import_may_take_a_number_the_other_azienda_already_issued(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    """Spec §8: the default issued 1 natively this year; the second azienda's register
    is empty, so importing its own 1 is history, not a number above a native one."""
    second = _second_azienda(db_session)
    native = service.issue(_draft_on(service, db_session, customer_id, None), InvoiceIssue(), ADMIN)
    assert native.numero == 1
    imported = service.import_issued(
        _import_payload(customer_id, numero=1, giorno=date(ANNO, 1, 10)),
        ADMIN,
        azienda_id=second.id,
    )
    assert (imported.anno, imported.numero, imported.azienda_id) == (ANNO, 1, second.id)
    # The same import on the default's register is refused: 1 is taken there.
    with pytest.raises(Conflict):
        service.import_issued(
            _import_payload(customer_id, numero=1, giorno=date(ANNO, 1, 10)), ADMIN
        )


def test_gaps_are_declared_and_read_per_azienda(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    second = _second_azienda(db_session)
    service.import_issued(
        _import_payload(customer_id, numero=3, giorno=date(ANNO, 2, 1)),
        ADMIN,
        azienda_id=second.id,
    )
    assert service.undeclared_gaps(ANNO, second.id) == [1, 2]
    assert service.undeclared_gaps(ANNO) == []
    declared = service.declare_gaps(
        ANNO,
        RegisterGapsDeclare(buchi=[RegisterGapIn(numero=1, motivo="mai emessa")]),
        ADMIN,
        azienda_id=second.id,
    )
    assert [g.numero for g in declared] == [1]
    assert service.undeclared_gaps(ANNO, second.id) == [2]
    # The default's register knows nothing of it.
    assert service.register_gaps(ANNO, ADMIN) == []
    assert service.register_gaps(ANNO, ADMIN, second.id)[0].numero == 1
    # And the same number can be declared a gap on the default, since it is free there.
    service.declare_gaps(
        ANNO, RegisterGapsDeclare(buchi=[RegisterGapIn(numero=1, motivo="prova")]), ADMIN
    )
    assert [g.numero for g in service.register_gaps(ANNO, ADMIN)] == [1]


def test_the_lock_of_one_azienda_does_not_block_the_other(db_engine: Engine) -> None:
    """Two sessions, two aziende (spec §8): while one holds its counter, the other's
    counter is taken at once, and a third session on the first's waits. Committed
    rows and real transactions, since one connection cannot contend with itself."""
    factory = session_factory(db_engine)
    with factory() as setup:
        # Inactive, so that while they exist no import in this worker's database can
        # match them; `lock_counter` does not care, it is the row that is the lock.
        one = LegalEntity(
            nome="uno", ragione_sociale="Uno S.r.l.", partita_iva="11111111111", attiva=False
        )
        two = LegalEntity(
            nome="due", ragione_sociale="Due S.r.l.", partita_iva="22222222222", attiva=False
        )
        setup.add_all([one, two])
        setup.commit()
        one_id, two_id = one.id, two.id
    sessions = [factory() for _ in range(3)]
    holder, other, waiter = sessions
    try:
        InvoiceRepository(holder).lock_counter(one_id, ANNO)
        other.execute(text("SET LOCAL statement_timeout = '1500ms'"))
        counter = InvoiceRepository(other).lock_counter(two_id, ANNO)
        assert counter.ultimo_numero == 0
        waiter.execute(text("SET LOCAL statement_timeout = '1500ms'"))
        with pytest.raises(OperationalError):
            InvoiceRepository(waiter).lock_counter(one_id, ANNO)
    finally:
        # Every session is closed whatever its rollback does, and the second delete
        # runs whatever the first did: a teardown that stops halfway leaves two
        # aziende and a lock row in a database the next file shares.
        for session in sessions:
            try:
                session.rollback()
            finally:
                session.close()
        with factory() as cleanup:
            try:
                cleanup.execute(
                    text("DELETE FROM invoice_counters WHERE azienda_id IN (:a, :b)"),
                    {"a": one_id, "b": two_id},
                )
            finally:
                cleanup.execute(
                    text("DELETE FROM emitter_profile WHERE id IN (:a, :b)"),
                    {"a": one_id, "b": two_id},
                )
                cleanup.commit()


# --- the import --------------------------------------------------------------------


def _document(session: Session, storage: LocalFileStorage, content: bytes, owner: UUID) -> UUID:
    service = DocumentService(session, storage)
    document = service.create(DocumentCreate(customer_id=owner, titolo="Fattura"), ADMIN)
    service.add_version(document.id, content, "application/xml", ADMIN)
    return document.id


def test_the_review_names_the_azienda_the_file_was_issued_by(
    service: InvoiceService, db_session: Session, local_storage: LocalFileStorage, customer_id: UUID
) -> None:
    second = _second_azienda(db_session)
    document_id = _document(db_session, local_storage, _fixture(CONSULENZA), customer_id)
    [row] = service.review_import([document_id], ADMIN)
    assert row.outcome in {"ready", "needs_customer_confirmation"}
    assert row.azienda_id == second.id


def test_the_confirm_writes_the_invoice_on_the_matched_aziendas_register(
    service: InvoiceService, db_session: Session, local_storage: LocalFileStorage, customer_id: UUID
) -> None:
    second = _second_azienda(db_session)
    document_id = _document(db_session, local_storage, _fixture(CONSULENZA), customer_id)
    [row] = service.confirm_import(document_id, ADMIN, customer_id=customer_id)
    assert row.outcome == "imported", row
    assert row.azienda_id == second.id
    assert row.fattura is not None and row.fattura.azienda_id == second.id
    # On the second's register, and nowhere on the default's.
    assert service.repo.numbers_present(second.id, row.fattura.anno or 0) == {row.fattura.numero}
    assert service.repo.numbers_present(_default_id(db_session), row.fattura.anno or 0) == set()


def test_a_file_no_azienda_issued_is_incoming_skipped_with_no_azienda(
    service: InvoiceService, db_session: Session, local_storage: LocalFileStorage, customer_id: UUID
) -> None:
    _second_azienda(db_session, partita_iva="33333333333", codice_fiscale=None)
    document_id = _document(db_session, local_storage, _fixture(CONSULENZA), customer_id)
    [row] = service.review_import([document_id], ADMIN)
    assert (row.outcome, row.azienda_id) == ("incoming_skipped", None)


def test_a_foreign_azienda_is_never_a_candidate_issuer_of_a_fatturapa_file(
    service: InvoiceService, db_session: Session
) -> None:
    foreign = _foreign_azienda(db_session)
    candidates = LegalEntityRepository(db_session).active_italian()
    assert foreign.id not in {a.id for a in candidates}
    assert _default_id(db_session) in {a.id for a in candidates}


# --- the foreign azienda -----------------------------------------------------------


def test_a_foreign_azienda_issues_a_pdf_with_no_bollo_and_refuses_the_xml(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    foreign = _foreign_azienda(db_session)
    # Born on the default azienda, as every draft is until milestone 3, then moved by
    # row; the lines are replaced once it is the foreign azienda's, since a 20% rate is
    # the foreign regime's answer and the forfettario's refusal.
    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            righe=[InvoiceLineIn(descrizione="Consulting", prezzo_unitario=Decimal("100.00"))],
        ),
        ADMIN,
    )
    row = db_session.get(Invoice, draft.id)
    assert row is not None
    row.azienda_id = foreign.id
    db_session.flush()
    service.replace_lines(
        draft.id,
        [
            InvoiceLineIn(
                descrizione="Consulting",
                prezzo_unitario=Decimal("100.00"),
                aliquota_iva=Decimal("20.00"),
            )
        ],
        ADMIN,
    )
    issued = service.issue(draft.id, InvoiceIssue(), ADMIN)
    assert (issued.numero, issued.azienda_id) == (1, foreign.id)
    assert issued.bollo == Decimal("0.00")
    assert issued.imposta == Decimal("20.00")
    assert issued.totale == Decimal("120.00")
    assert row.snapshot is not None
    assert row.snapshot["fiscale"]["pack_id"] == "non-it"
    assert row.snapshot["fiscale"]["codice_regime"] is None
    assert row.snapshot["emittente"]["ragione_sociale"] == "Rebase Ltd"
    scope = invoice_pdf.build_scope(service._for_export(row), riferimento=None)
    assert scope["fattura"]["dichiarazione_bollo"] == ""
    with pytest.raises(Conflict) as caught:
        service.export_xml(draft.id, ADMIN)
    assert "non emette fatture elettroniche" in caught.value.message
    artifacts = service.produce_artifacts(draft.id, ADMIN)
    assert [a.kind for a in artifacts] == ["pdf"]


def test_a_foreign_profile_has_no_regime_code_and_an_italian_one_requires_it(
    db_session: Session,
) -> None:
    foreign = LegalEntity(nome="ltd", ragione_sociale="Rebase Ltd", nazione="GB")
    db_session.add(foreign)
    db_session.flush()
    profiles = FiscalProfileService(db_session)
    with pytest.raises(ValidationFailed) as italian:
        profiles.upsert(FiscalProfileUpsert(), ADMIN)
    assert italian.value.details["field"] == "codice_regime"
    with pytest.raises(ValidationFailed) as mixed:
        profiles.upsert(
            FiscalProfileUpsert(pack_id="non-it", codice_regime="RF19"),
            ADMIN,
            azienda_id=foreign.id,
        )
    assert mixed.value.details["field"] == "codice_regime"
    saved = profiles.upsert(
        FiscalProfileUpsert(
            pack_id="non-it",
            aliquota_iva_default=Decimal("20.00"),
            natura_default=None,
            riferimento_normativo=None,
        ),
        ADMIN,
        azienda_id=foreign.id,
    )
    assert (saved.pack_id, saved.codice_regime) == ("non-it", None)
    assert profiles.snapshot(foreign.id).pack_id == "non-it"


def test_a_foreign_vat_number_longer_than_eleven_characters_is_kept(
    db_session: Session,
) -> None:
    """REB-615 refused it in words until the column widened; it fits now."""
    foreign = LegalEntity(nome="sarl", ragione_sociale="Rebase SARL", nazione="FR")
    db_session.add(foreign)
    db_session.flush()
    read = LegalEntityService(db_session).update(
        foreign.id,
        LegalEntityUpsert(
            ragione_sociale="Rebase SARL", nazione="FR", partita_iva="FR 12 345678901"
        ),
        ADMIN,
    )
    assert read.partita_iva == "FR12345678901"


def test_a_space_with_one_azienda_sees_nothing_change(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    """Every call without an azienda is the default's: the one-azienda path of before."""
    issued = service.issue(_draft_on(service, db_session, customer_id, None), InvoiceIssue(), ADMIN)
    assert issued.numero == 1 and issued.azienda_id == _default_id(db_session)
    assert service.undeclared_gaps(ANNO) == []
    assert service.register_gaps(ANNO, ADMIN) == []
    assert service.repo.numbers_present(_default_id(db_session), ANNO) == {1}
    assert service.repo.numbers_present(uuid4(), ANNO) == set()


def test_a_foreign_azienda_issues_without_the_sdis_checks_on_the_parties_or_the_text(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    """The SdI's rules (a complete address, a routing code, Latin text) are the XML's;
    a foreign PDF has none of them. The same draft under the Italian default is
    refused, which is what shows the skip is the foreign azienda's and not a loosening."""
    foreign = _foreign_azienda(db_session)
    bare = Customer(ragione_sociale="Overseas Ltd", nazione="GB")
    db_session.add(bare)
    db_session.flush()

    def draft_with_dash() -> UUID:
        return service.create(
            InvoiceCreate(
                customer_id=bare.id,
                causale="Consulting \u2014 Q3",
                righe=[InvoiceLineIn(descrizione="Consulting", prezzo_unitario=Decimal("100.00"))],
            ),
            ADMIN,
        ).id

    with pytest.raises(ValidationFailed):
        service.issue(draft_with_dash(), InvoiceIssue(), ADMIN)

    draft = draft_with_dash()
    row = db_session.get(Invoice, draft)
    assert row is not None
    row.azienda_id = foreign.id
    db_session.flush()
    service.replace_lines(
        draft,
        [
            InvoiceLineIn(
                descrizione="Consulting \u2014 advisory",
                prezzo_unitario=Decimal("100.00"),
                aliquota_iva=Decimal("20.00"),
            )
        ],
        ADMIN,
    )
    issued = service.issue(draft, InvoiceIssue(), ADMIN)
    assert (issued.numero, issued.azienda_id) == (1, foreign.id)


def test_the_frozen_snapshot_and_not_the_live_profile_decides_the_xml(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    """`export_xml` reads the pack off the row's snapshot: a document issued abroad
    stays refused after its azienda's profile turns Italian, and one issued in Italy
    still exports after its azienda's profile turns foreign."""
    foreign = _foreign_azienda(db_session)
    draft = _draft_on(service, db_session, customer_id, foreign)
    service.replace_lines(
        draft,
        [
            InvoiceLineIn(
                descrizione="Consulting",
                prezzo_unitario=Decimal("100.00"),
                aliquota_iva=Decimal("20.00"),
            )
        ],
        ADMIN,
    )
    abroad = service.issue(draft, InvoiceIssue(), ADMIN)
    FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(codice_regime="RF19"), ADMIN, azienda_id=foreign.id
    )
    with pytest.raises(Conflict):
        service.export_xml(abroad.id, ADMIN)

    italian = service.issue(
        _draft_on(service, db_session, customer_id, None), InvoiceIssue(), ADMIN
    )
    FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(pack_id="non-it", aliquota_iva_default=Decimal("20.00")), ADMIN
    )
    assert service.export_xml(italian.id, ADMIN).kind == "xml"


def test_a_register_write_refuses_an_inactive_azienda_and_a_read_still_answers(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    second = _second_azienda(db_session, attiva=False)
    with pytest.raises(Conflict) as imported:
        service.import_issued(
            _import_payload(customer_id, numero=1, giorno=date(ANNO, 1, 10)),
            ADMIN,
            azienda_id=second.id,
        )
    assert "non e' attiva" in imported.value.message
    with pytest.raises(Conflict):
        service.declare_gaps(
            ANNO,
            RegisterGapsDeclare(buchi=[RegisterGapIn(numero=1, motivo="mai emessa")]),
            ADMIN,
            azienda_id=second.id,
        )
    assert service.register_gaps(ANNO, ADMIN, second.id) == []
    # And a draft moved onto it does not consume a number either.
    draft = _draft_on(service, db_session, customer_id, second)
    with pytest.raises(Conflict):
        service.issue(draft, InvoiceIssue(), ADMIN)
