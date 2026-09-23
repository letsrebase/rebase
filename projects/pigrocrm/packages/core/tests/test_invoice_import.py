"""Import dello storico fatture (slice 9 §3)."""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.invoices.models import Invoice, InvoiceRegisterGap

ADMIN = Actor(id=None, type="system", role="admin")


def _fiscal_customer_id(session: Session) -> UUID:
    """A customer with enough identity to appear on an issued document.

    Copied from `conftest.py` rather than imported: `conftest` is an ambiguous
    top-level module name across this repository's three test roots, and only the
    directory pytest resolves first would ever bind `from conftest import ...`
    correctly when the whole suite is collected in one run.
    """
    customer = Customer(
        ragione_sociale=f"Acme {uuid4()}",
        partita_iva="12345678901",
        codice_sdi="ABCDEFG",
        indirizzo="Corso Italia 5",
        cap="00100",
        comune="Roma",
        provincia="RM",
        nazione="IT",
    )
    session.add(customer)
    session.flush()
    return customer.id


def test_an_invoice_records_where_it_was_imported_from(db_session: Session) -> None:
    row = Invoice(
        customer_id=_fiscal_customer_id(db_session),
        tipo="fattura",
        stato="emessa",
        anno=2026,
        numero=7,
        data_emissione=date(2026, 5, 5),
        importata_da="esterno",
        imponibile=Decimal("2700.00"),
        imposta=Decimal("0.00"),
        # The stamp is declared and stored, never added: `totale = imponibile + imposta`
        # (slice 3 `totals.py:sum_totals`), because `DatiBollo/BolloVirtuale` says the
        # issuer settled it themselves.
        bollo=Decimal("2.00"),
        totale=Decimal("2700.00"),
    )
    db_session.add(row)
    db_session.flush()
    assert db_session.get(Invoice, row.id).importata_da == "esterno"


def test_a_register_gap_is_unique_per_year_and_number(db_session: Session) -> None:
    db_session.add(InvoiceRegisterGap(anno=2026, numero=4, motivo="annullata altrove"))
    db_session.flush()
    db_session.add(InvoiceRegisterGap(anno=2026, numero=4, motivo="di nuovo"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def _issued(
    session: Session, *, anno: int, numero: int, giorno: date, importata: bool = True
) -> Invoice:
    row = Invoice(
        customer_id=_fiscal_customer_id(session),
        tipo="fattura",
        stato="emessa",
        anno=anno,
        numero=numero,
        data_emissione=giorno,
        importata_da="esterno" if importata else None,
        imponibile=Decimal("100.00"),
        imposta=Decimal("0.00"),
        bollo=Decimal("0.00"),
        totale=Decimal("100.00"),
    )
    session.add(row)
    session.flush()
    return row


def test_neighbour_dates_look_both_ways(db_session: Session) -> None:
    from pigrocrm.core.invoices.repository import InvoiceRepository

    _issued(db_session, anno=2026, numero=7, giorno=date(2026, 5, 5))
    _issued(db_session, anno=2026, numero=11, giorno=date(2026, 7, 13))
    repo = InvoiceRepository(db_session)
    assert repo.neighbour_dates(2026, 9) == (date(2026, 5, 5), date(2026, 7, 13))
    assert repo.neighbour_dates(2026, 2) == (None, date(2026, 5, 5))
    assert repo.neighbour_dates(2026, 12) == (date(2026, 7, 13), None)
    assert repo.numbers_present(2026) == {7, 11}
    assert repo.first_native_number(2026) is None
    _issued(db_session, anno=2026, numero=18, giorno=date(2026, 9, 10), importata=False)
    assert repo.first_native_number(2026) == 18


def test_gaps_round_trip(db_session: Session) -> None:
    from pigrocrm.core.invoices.repository import InvoiceRepository

    repo = InvoiceRepository(db_session)
    repo.add_gap(InvoiceRegisterGap(anno=2026, numero=6, motivo="test"))
    repo.add_gap(InvoiceRegisterGap(anno=2026, numero=1, motivo="test"))
    assert repo.declared_gaps(2026) == {1, 6}
    assert [g.numero for g in repo.gaps(2026)] == [1, 6]
    assert repo.declared_gaps(2025) == set()


def _svc(session: Session, tmp_path, *, settings=None, drive_reader_factory=None):  # noqa: ANN001
    """`InvoiceService` with the two profiles it reads already in place.

    Copied from `conftest._invoice_service` rather than imported, for the same
    reason `_fiscal_customer_id` above is copied and not imported: `conftest` is an
    ambiguous top-level module name across this repository's three test roots.
    """
    from pigrocrm.core.emitter.repository import EmitterProfileRepository
    from pigrocrm.core.emitter.schemas import EmitterProfileUpsert
    from pigrocrm.core.emitter.service import EmitterProfileService
    from pigrocrm.core.fiscal.repository import FiscalProfileRepository
    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
    from pigrocrm.core.fiscal.service import FiscalProfileService
    from pigrocrm.core.invoices.service import InvoiceService
    from pigrocrm.core.storage.local import LocalFileStorage

    if FiscalProfileRepository(session).get() is None:
        FiscalProfileService(session).upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)
    if EmitterProfileRepository(session).get() is None:
        EmitterProfileService(session).upsert(
            EmitterProfileUpsert(
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
    return InvoiceService(
        session,
        LocalFileStorage(tmp_path),
        settings,
        drive_reader_factory=drive_reader_factory,
    )


def _payload(
    customer_id: UUID, *, numero: int, giorno: date, **overrides: object
) -> "InvoiceImport":  # noqa: F821
    from pigrocrm.core.invoices.schemas import InvoiceImport

    base: dict[str, object] = {
        "anno": giorno.year,
        "numero": numero,
        "data_emissione": giorno,
        "data_scadenza": date(giorno.year, giorno.month, 28),
        "customer_id": customer_id,
        "causale": "900142/0426/Consulenza AI CTO progetto Aurora",
        "righe": [
            {
                "descrizione": "900142/0426/Consulenza AI CTO progetto Aurora",
                "quantita": Decimal("9"),
                "prezzo_unitario": Decimal("300"),
                "prezzo_totale": Decimal("2700.00"),
                "aliquota_iva": Decimal("0"),
                "natura": "N2.2",
            }
        ],
        "imponibile": Decimal("2700.00"),
        "imposta": Decimal("0.00"),
        # Declared, and deliberately *not* inside `totale`: see `_check_declared_totals`.
        "bollo": Decimal("2.00"),
        "totale": Decimal("2700.00"),
        "stato_pagamento": "incassato",
        "data_incasso": date(giorno.year, giorno.month, 20),
        "trasmessa_esternamente_il": giorno,
    }
    base.update(overrides)
    return InvoiceImport(**base)


def test_an_imported_invoice_is_issued_numbered_and_moves_the_counter(
    db_session: Session, tmp_path
) -> None:  # noqa: ANN001
    from pigrocrm.core.invoices.models import InvoiceCounter

    service = _svc(db_session, tmp_path)
    customer_id = _fiscal_customer_id(db_session)
    read = service.import_issued(_payload(customer_id, numero=7, giorno=date(2026, 5, 5)), ADMIN)

    assert (read.anno, read.numero, read.stato, read.tipo) == (2026, 7, "emessa", "fattura")
    assert read.importata_da == "esterno"
    assert read.totale == Decimal("2700.00") and read.bollo == Decimal("2.00")
    assert read.stato_pagamento == "incassato" and read.data_incasso == date(2026, 5, 20)
    assert read.xml_hash_sha256 is None and read.pdf_document_id is None
    assert db_session.get(InvoiceCounter, 2026).ultimo_numero == 7
    lines = service.repo.lines(read.id)
    assert [(riga.numero_linea, riga.prezzo_totale, riga.natura) for riga in lines] == [
        (1, Decimal("2700.00"), "N2.2")
    ]
    row = db_session.get(Invoice, read.id)
    assert row.snapshot is not None and row.snapshot["versione"] == 1


def test_import_issued_accepts_xml_document_id_and_hash_gated_by_the_caller(
    db_session: Session, tmp_path
) -> None:  # noqa: ANN001
    """REB-366 §5 item 3: `import_issued` never derives these itself -- they default
    to `None`, exactly the state a hand-declared import leaves them in -- and only a
    caller that already has the original bytes in hand (`InvoiceService.confirm_
    import`) ever passes real values. Proven here at `import_issued` itself, in
    isolation from `confirm_import`'s own classification. `document_id` names a real
    `documents` row -- `invoices.xml_document_id` carries a foreign key -- but is
    otherwise unrelated to it: this test proves the plumbing, not the archiving."""
    service = _svc(db_session, tmp_path)
    customer_id = _fiscal_customer_id(db_session)
    document_id = _pdf_document(db_session, tmp_path, customer_id)
    read = service.import_issued(
        _payload(customer_id, numero=7, giorno=date(2026, 5, 5)),
        ADMIN,
        xml_document_id=document_id,
        xml_hash_sha256="a" * 64,
    )
    assert read.xml_document_id == document_id
    assert read.xml_hash_sha256 == "a" * 64
    # Still `"esterno"`: widening `importata_da` is a separate, later follow-up
    # (design §5 item 4/§7 item 6), not this capability's.
    assert read.importata_da == "esterno"


def test_the_counter_never_moves_backwards(db_session: Session, tmp_path) -> None:  # noqa: ANN001
    from pigrocrm.core.invoices.models import InvoiceCounter

    service = _svc(db_session, tmp_path)
    cid = _fiscal_customer_id(db_session)
    service.import_issued(_payload(cid, numero=11, giorno=date(2026, 7, 13)), ADMIN)
    service.import_issued(_payload(cid, numero=9, giorno=date(2026, 6, 5)), ADMIN)
    assert db_session.get(InvoiceCounter, 2026).ultimo_numero == 11


def test_a_duplicate_number_is_a_conflict(db_session: Session, tmp_path) -> None:  # noqa: ANN001
    from pigrocrm.core.errors import Conflict

    service = _svc(db_session, tmp_path)
    cid = _fiscal_customer_id(db_session)
    service.import_issued(_payload(cid, numero=7, giorno=date(2026, 5, 5)), ADMIN)
    with pytest.raises(Conflict):
        service.import_issued(_payload(cid, numero=7, giorno=date(2026, 5, 5)), ADMIN)


def test_the_register_stays_chronological_against_both_neighbours(
    db_session: Session, tmp_path
) -> None:  # noqa: ANN001
    from pigrocrm.core.errors import ValidationFailed

    service = _svc(db_session, tmp_path)
    cid = _fiscal_customer_id(db_session)
    service.import_issued(_payload(cid, numero=7, giorno=date(2026, 5, 5)), ADMIN)
    service.import_issued(_payload(cid, numero=11, giorno=date(2026, 7, 13)), ADMIN)
    with pytest.raises(ValidationFailed) as before:
        service.import_issued(_payload(cid, numero=9, giorno=date(2026, 5, 4)), ADMIN)
    assert before.value.details["field"] == "data_emissione"
    with pytest.raises(ValidationFailed):
        service.import_issued(_payload(cid, numero=9, giorno=date(2026, 7, 14)), ADMIN)
    service.import_issued(_payload(cid, numero=9, giorno=date(2026, 6, 5)), ADMIN)


def test_declared_totals_must_add_up_to_the_cent(db_session: Session, tmp_path) -> None:  # noqa: ANN001
    from pigrocrm.core.errors import ValidationFailed

    service = _svc(db_session, tmp_path)
    cid = _fiscal_customer_id(db_session)
    with pytest.raises(ValidationFailed) as caught:
        service.import_issued(
            _payload(cid, numero=7, giorno=date(2026, 5, 5), totale=Decimal("2699.99")), ADMIN
        )
    assert "2700.00" in caught.value.message and "2699.99" in caught.value.message
    with pytest.raises(ValidationFailed):
        service.import_issued(
            _payload(
                cid,
                numero=7,
                giorno=date(2026, 5, 5),
                imponibile=Decimal("3400.00"),
                totale=Decimal("3400.00"),
            ),
            ADMIN,
        )


def test_the_stamp_duty_is_declared_alongside_and_never_added_to_the_total(
    db_session: Session, tmp_path
) -> None:  # noqa: ANN001
    """`imponibile + imposta == totale`, with `bollo` beside it -- the identity slice 3
    stores (`sum_totals`) and the one the previous system's register shows («Totale» always equals
    «Imp. Reddito»). A caller who adds the stamp into the total is refused, and a
    negative stamp is refused too, even though nothing sums it.
    """
    from pigrocrm.core.errors import ValidationFailed

    service = _svc(db_session, tmp_path)
    cid = _fiscal_customer_id(db_session)
    with pytest.raises(ValidationFailed) as summed:
        service.import_issued(
            _payload(cid, numero=7, giorno=date(2026, 5, 5), totale=Decimal("3422.00")), ADMIN
        )
    assert summed.value.details["field"] == "totale"
    with pytest.raises(ValidationFailed) as negative:
        service.import_issued(
            _payload(cid, numero=7, giorno=date(2026, 5, 5), bollo=Decimal("-2.00")), ADMIN
        )
    assert negative.value.details["field"] == "bollo"

    read = service.import_issued(_payload(cid, numero=7, giorno=date(2026, 5, 5)), ADMIN)
    assert (read.imponibile, read.imposta, read.bollo, read.totale) == (
        Decimal("2700.00"),
        Decimal("0.00"),
        Decimal("2.00"),
        Decimal("2700.00"),
    )


def test_a_future_date_and_a_collaborator_are_refused(db_session: Session, tmp_path) -> None:  # noqa: ANN001
    from datetime import timedelta

    from pigrocrm.core.clock import oggi_in_italia
    from pigrocrm.core.errors import PermissionDenied, ValidationFailed

    service = _svc(db_session, tmp_path)
    cid = _fiscal_customer_id(db_session)
    domani = oggi_in_italia() + timedelta(days=1)
    with pytest.raises(ValidationFailed):
        service.import_issued(_payload(cid, numero=7, giorno=domani), ADMIN)
    with pytest.raises(PermissionDenied):
        service.import_issued(
            _payload(cid, numero=7, giorno=date(2026, 5, 5)),
            Actor(id=None, type="user", role="collaboratore"),
        )


def test_an_external_transmission_date_is_checked_like_a_native_one(
    db_session: Session, tmp_path
) -> None:  # noqa: ANN001
    """`trasmessa_esternamente_il` is immutable once written and it is what `annul`
    reads to decide whether a correction is still possible, so an import cannot be the
    one door through which a future -- or pre-emission -- delivery date walks in. Same
    helper, same two refusals, as `mark_transmitted_externally`.
    """
    from datetime import timedelta

    from pigrocrm.core.clock import oggi_in_italia
    from pigrocrm.core.errors import ValidationFailed

    service = _svc(db_session, tmp_path)
    cid = _fiscal_customer_id(db_session)
    domani = oggi_in_italia() + timedelta(days=1)
    with pytest.raises(ValidationFailed) as futura:
        service.import_issued(
            _payload(cid, numero=7, giorno=date(2026, 5, 5), trasmessa_esternamente_il=domani),
            ADMIN,
        )
    assert futura.value.details["field"] == "trasmessa_esternamente_il"
    with pytest.raises(ValidationFailed) as prima:
        service.import_issued(
            _payload(
                cid,
                numero=7,
                giorno=date(2026, 5, 5),
                trasmessa_esternamente_il=date(2026, 5, 4),
            ),
            ADMIN,
        )
    assert prima.value.details["field"] == "trasmessa_esternamente_il"
    # And nothing was written by either refusal.
    assert db_session.execute(select(Invoice).where(Invoice.numero == 7)).first() is None


def test_the_import_writes_one_activity(db_session: Session, tmp_path) -> None:  # noqa: ANN001
    from pigrocrm.core.activities.models import Activity

    service = _svc(db_session, tmp_path)
    cid = _fiscal_customer_id(db_session)
    read = service.import_issued(_payload(cid, numero=7, giorno=date(2026, 5, 5)), ADMIN)
    kinds = (
        db_session.execute(
            select(Activity.kind).where(
                Activity.entity_type == "invoice", Activity.entity_id == read.id
            )
        )
        .scalars()
        .all()
    )
    assert kinds == ["imported"]


def test_a_collection_date_without_a_collected_state_is_refused(
    db_session: Session, tmp_path
) -> None:  # noqa: ANN001
    from pigrocrm.core.errors import ValidationFailed

    service = _svc(db_session, tmp_path)
    cid = _fiscal_customer_id(db_session)
    with pytest.raises(ValidationFailed) as caught:
        service.import_issued(
            _payload(cid, numero=7, giorno=date(2026, 5, 5), stato_pagamento="da_incassare"),
            ADMIN,
        )
    assert caught.value.details["field"] == "data_incasso"


def test_anno_must_match_the_issue_dates_year(db_session: Session, tmp_path) -> None:  # noqa: ANN001
    from pigrocrm.core.errors import ValidationFailed

    service = _svc(db_session, tmp_path)
    cid = _fiscal_customer_id(db_session)
    with pytest.raises(ValidationFailed) as caught:
        service.import_issued(_payload(cid, numero=7, giorno=date(2026, 5, 5), anno=2025), ADMIN)
    assert caught.value.details["field"] == "anno"


def test_a_missing_emitter_profile_is_refused_before_any_row_is_flushed(
    db_session: Session, tmp_path
) -> None:  # noqa: ANN001
    """The state a fresh installation is in before §2.3 is done -- and the state the
    live one was in when this was found. `_build_snapshot` reads `emitter_profile` and
    raises `NotFound`; that used to happen *after* `repo.add`/`add_line` had flushed the
    invoice and its lines, and nothing rolled back, because only `IntegrityError` was
    caught. The caller was told the import failed and the register carried it anyway.
    """
    from pigrocrm.core.emitter.repository import EmitterProfileRepository
    from pigrocrm.core.errors import NotFound
    from pigrocrm.core.fiscal.repository import FiscalProfileRepository
    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
    from pigrocrm.core.fiscal.service import FiscalProfileService
    from pigrocrm.core.invoices.models import InvoiceCounter
    from pigrocrm.core.invoices.service import InvoiceService
    from pigrocrm.core.storage.local import LocalFileStorage

    if FiscalProfileRepository(db_session).get() is None:
        FiscalProfileService(db_session).upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)
    assert EmitterProfileRepository(db_session).get() is None, "this test needs no emitter profile"
    service = InvoiceService(db_session, LocalFileStorage(tmp_path))
    cid = _fiscal_customer_id(db_session)

    with pytest.raises(NotFound) as caught:
        service.import_issued(_payload(cid, numero=7, giorno=date(2026, 5, 5)), ADMIN)
    assert caught.value.details["entity"] == "emitter_profile"
    # No invoice, no lines, and not even the year's counter row: the refusal happened
    # above `lock_counter`, which is what inserts it.
    assert db_session.execute(select(Invoice)).first() is None
    assert db_session.get(InvoiceCounter, 2026) is None
    # And the session is still usable -- a query, not an exception, is what comes back.
    assert service.undeclared_gaps(2026) == []


def test_a_number_beyond_the_register_is_refused_by_the_schema(
    db_session: Session, tmp_path
) -> None:  # noqa: ANN001
    """`MAX_NUMERO` is a bound on `InvoiceImport` itself, so a document id slipped from
    the previous system (`900142`) never reaches the service: no lock, no counter row, no
    two-hundred-thousand-element `undeclared_gaps`. The counter is the witness -- it
    exists only if `lock_counter` ran.
    """
    from pydantic import ValidationError

    from pigrocrm.core.invoices.models import InvoiceCounter
    from pigrocrm.core.invoices.schemas import MAX_NUMERO

    service = _svc(db_session, tmp_path)
    cid = _fiscal_customer_id(db_session)
    with pytest.raises(ValidationError):
        service.import_issued(_payload(cid, numero=900142, giorno=date(2026, 5, 5)), ADMIN)
    assert db_session.get(InvoiceCounter, 2026) is None
    service.import_issued(_payload(cid, numero=MAX_NUMERO, giorno=date(2026, 5, 5)), ADMIN)
    assert db_session.get(InvoiceCounter, 2026).ultimo_numero == MAX_NUMERO


def test_a_declared_gap_refuses_the_import_of_that_number(db_session: Session, tmp_path) -> None:  # noqa: ANN001
    from pigrocrm.core.errors import Conflict

    service = _svc(db_session, tmp_path)
    cid = _fiscal_customer_id(db_session)
    service.repo.add_gap(InvoiceRegisterGap(anno=2026, numero=8, motivo="annullata altrove"))
    with pytest.raises(Conflict):
        service.import_issued(_payload(cid, numero=8, giorno=date(2026, 5, 5)), ADMIN)


def test_import_is_refused_above_the_first_native_number(db_session: Session, tmp_path) -> None:  # noqa: ANN001
    from pigrocrm.core.errors import Conflict

    service = _svc(db_session, tmp_path)
    cid = _fiscal_customer_id(db_session)
    _issued(db_session, anno=2026, numero=5, giorno=date(2026, 4, 1), importata=False)
    with pytest.raises(Conflict):
        service.import_issued(_payload(cid, numero=6, giorno=date(2026, 4, 2)), ADMIN)
    service.import_issued(_payload(cid, numero=3, giorno=date(2026, 3, 1)), ADMIN)


def test_gaps_are_declared_with_a_reason_and_listed(db_session: Session, tmp_path) -> None:  # noqa: ANN001
    from pigrocrm.core.invoices.schemas import RegisterGapsDeclare

    service = _svc(db_session, tmp_path)
    out = service.declare_gaps(
        2026,
        RegisterGapsDeclare(
            buchi=[
                {"numero": 1, "motivo": "annullata nel gestionale precedente"},
                {"numero": 4, "motivo": "test di emissione"},
            ]
        ),  # type: ignore[list-item]
        ADMIN,
    )
    assert [(g.numero, g.motivo) for g in out] == [
        (1, "annullata nel gestionale precedente"),
        (4, "test di emissione"),
    ]
    assert [g.numero for g in service.register_gaps(2026, ADMIN)] == [1, 4]


def test_declaring_a_number_that_is_an_invoice_is_a_conflict(db_session: Session, tmp_path) -> None:  # noqa: ANN001
    from pigrocrm.core.errors import Conflict
    from pigrocrm.core.invoices.schemas import RegisterGapsDeclare

    service = _svc(db_session, tmp_path)
    cid = _fiscal_customer_id(db_session)
    service.import_issued(_payload(cid, numero=7, giorno=date(2026, 5, 5)), ADMIN)
    with pytest.raises(Conflict):
        service.declare_gaps(2026, RegisterGapsDeclare(buchi=[{"numero": 7, "motivo": "x"}]), ADMIN)  # type: ignore[list-item]


def test_undeclared_gaps_are_named_and_block_native_issuing(db_session: Session, tmp_path) -> None:  # noqa: ANN001
    from pigrocrm.core.clock import oggi_in_italia
    from pigrocrm.core.errors import Conflict
    from pigrocrm.core.invoices.schemas import (
        InvoiceCreate,
        InvoiceIssue,
        InvoiceLineIn,
        RegisterGapsDeclare,
    )

    anno = oggi_in_italia().year
    service = _svc(db_session, tmp_path)
    cid = _fiscal_customer_id(db_session)
    service.import_issued(_payload(cid, numero=2, giorno=date(anno, 2, 4)), ADMIN)
    service.import_issued(_payload(cid, numero=5, giorno=date(anno, 4, 7)), ADMIN)
    # From 1, not from the lowest number imported: the 1 is a hole exactly as much as
    # the 3 and the 4 are, and in the previous system's real register it is *the* hole.
    assert service.undeclared_gaps(anno) == [1, 3, 4]

    draft = service.create(
        InvoiceCreate(
            customer_id=cid, righe=[InvoiceLineIn(descrizione="x", prezzo_unitario=Decimal("100"))]
        ),
        ADMIN,
    )
    with pytest.raises(Conflict) as caught:
        service.issue(draft.id, InvoiceIssue(), ADMIN)
    assert "3" in str(caught.value) and "4" in str(caught.value)

    service.declare_gaps(
        anno,
        RegisterGapsDeclare(
            buchi=[
                {"numero": 1, "motivo": "mai emessa"},
                {"numero": 3, "motivo": "a"},
                {"numero": 4, "motivo": "b"},
            ]
        ),  # type: ignore[list-item]
        ADMIN,
    )
    assert service.undeclared_gaps(anno) == []
    issued = service.issue(draft.id, InvoiceIssue(), ADMIN)
    assert issued.numero == 6


def test_the_hole_at_one_is_not_silent(db_session: Session, tmp_path) -> None:  # noqa: ANN001
    """A register that starts at 2 is a register missing its 1. Bounding the scan below
    by `min(present)` made that the one hole nobody was told about -- silently, which is
    precisely what §3.2 rule 4 exists to prevent -- and it is the shape of the real
    import: the previous system's 2026 register has no invoice 1, and it has to be *declared*.
    """
    from pigrocrm.core.invoices.schemas import RegisterGapsDeclare

    service = _svc(db_session, tmp_path)
    cid = _fiscal_customer_id(db_session)
    service.import_issued(_payload(cid, numero=2, giorno=date(2026, 2, 4)), ADMIN)
    assert service.undeclared_gaps(2026) == [1]
    service.declare_gaps(
        2026,
        RegisterGapsDeclare(buchi=[{"numero": 1, "motivo": "mai emessa"}]),  # type: ignore[list-item]
        ADMIN,
    )
    assert service.undeclared_gaps(2026) == []


def test_a_long_gap_list_is_reported_whole_and_rendered_short(
    db_session: Session, tmp_path
) -> None:  # noqa: ANN001
    """The refusal is read by a person: the message names the first twenty numbers and
    says how many there are, while `details["numeri"]` keeps every one of them for a
    caller that wants to act on the list.
    """
    from pigrocrm.core.clock import oggi_in_italia
    from pigrocrm.core.errors import Conflict
    from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceIssue, InvoiceLineIn
    from pigrocrm.core.invoices.service import GAPS_SHOWN_IN_MESSAGE

    anno = oggi_in_italia().year
    service = _svc(db_session, tmp_path)
    cid = _fiscal_customer_id(db_session)
    service.import_issued(_payload(cid, numero=30, giorno=date(anno, 2, 4)), ADMIN)
    buchi = list(range(1, 30))
    assert service.undeclared_gaps(anno) == buchi

    draft = service.create(
        InvoiceCreate(
            customer_id=cid, righe=[InvoiceLineIn(descrizione="x", prezzo_unitario=Decimal("100"))]
        ),
        ADMIN,
    )
    with pytest.raises(Conflict) as caught:
        service.issue(draft.id, InvoiceIssue(), ADMIN)
    assert caught.value.details["numeri"] == buchi
    assert f"({len(buchi)} in tutto)" in caught.value.message
    assert str(GAPS_SHOWN_IN_MESSAGE) in caught.value.message
    # The twenty-first number is not spelled out.
    assert f", {buchi[GAPS_SHOWN_IN_MESSAGE]}," not in caught.value.message


def test_a_partly_invalid_batch_of_gaps_writes_none_of_it(db_session: Session, tmp_path) -> None:  # noqa: ANN001
    """The whole batch is validated before the first `add_gap`. Validating inside the
    writing loop left the elements before the bad one flushed into the caller's
    transaction: rows the caller was told had not been created.
    """
    from pigrocrm.core.errors import Conflict
    from pigrocrm.core.invoices.schemas import RegisterGapsDeclare

    service = _svc(db_session, tmp_path)
    cid = _fiscal_customer_id(db_session)
    service.import_issued(_payload(cid, numero=7, giorno=date(2026, 5, 5)), ADMIN)
    with pytest.raises(Conflict):
        service.declare_gaps(
            2026,
            RegisterGapsDeclare(
                buchi=[
                    {"numero": 1, "motivo": "mai emessa"},
                    {"numero": 4, "motivo": "annullata"},
                    {"numero": 7, "motivo": "questa e' una fattura"},
                ]
            ),  # type: ignore[list-item]
            ADMIN,
        )
    assert db_session.execute(select(InvoiceRegisterGap)).first() is None
    assert service.register_gaps(2026, ADMIN) == []


def test_a_same_batch_duplicate_number_is_a_conflict_and_leaves_the_session_usable(
    db_session: Session, tmp_path
) -> None:  # noqa: ANN001
    from pigrocrm.core.errors import Conflict
    from pigrocrm.core.invoices.schemas import RegisterGapsDeclare

    service = _svc(db_session, tmp_path)
    with pytest.raises(Conflict):
        service.declare_gaps(
            2026,
            RegisterGapsDeclare(buchi=[{"numero": 3, "motivo": "a"}, {"numero": 3, "motivo": "b"}]),  # type: ignore[list-item]
            ADMIN,
        )
    # The session must still answer a query after the Conflict: a poisoned
    # transaction (an unrolled-back IntegrityError) would raise on this next
    # statement instead of returning a result. This particular Conflict never
    # touches Postgres -- it is refused in Python, from the batch-local `seen` set,
    # before the duplicate's own `add_gap` flush -- so it does not poison the
    # session the way an actual `IntegrityError` would; the point of this test is
    # only that the call below does not raise.
    service.register_gaps(2026, ADMIN)


def test_anno_out_of_range_is_refused_before_any_write(db_session: Session, tmp_path) -> None:  # noqa: ANN001
    from pigrocrm.core.errors import ValidationFailed
    from pigrocrm.core.invoices.schemas import RegisterGapsDeclare

    service = _svc(db_session, tmp_path)
    with pytest.raises(ValidationFailed) as caught:
        service.declare_gaps(
            10**12,
            RegisterGapsDeclare(buchi=[{"numero": 1, "motivo": "x"}]),  # type: ignore[list-item]
            ADMIN,
        )
    assert caught.value.details["field"] == "anno"
    assert db_session.execute(select(InvoiceRegisterGap)).first() is None


def _pdf_document(session: Session, tmp_path, customer_id: UUID, *, storage=None) -> UUID:  # noqa: ANN001
    """A document of type `fattura` with one PDF version already uploaded.

    `storage` defaults to a fresh `LocalFileStorage(tmp_path)`; a caller that wants
    the adopted PDF's bytes to actually download through the `InvoiceService` under
    test must pass that service's own `storage`, since `download` reads through the
    invoice service's storage, not a new one pointed at the same directory.
    """
    from pigrocrm.core.config import get_settings
    from pigrocrm.core.documents.schemas import DocumentCreate
    from pigrocrm.core.documents.service import DocumentService
    from pigrocrm.core.storage.local import LocalFileStorage

    docs = DocumentService(session, storage or LocalFileStorage(tmp_path), get_settings())
    doc = docs.create(
        DocumentCreate(
            customer_id=customer_id, tipo="fattura", titolo="Fattura 7/2026 (importata)"
        ),
        ADMIN,
    )
    docs.add_version(doc.id, b"%PDF-1.4 fake", "application/pdf", ADMIN)
    return doc.id


def test_the_original_pdf_is_adopted_not_rendered(db_session: Session, tmp_path) -> None:  # noqa: ANN001
    from pigrocrm.core.errors import Conflict
    from pigrocrm.core.invoices.schemas import PdfSorgente

    service = _svc(db_session, tmp_path)
    cid = _fiscal_customer_id(db_session)
    doc_id = _pdf_document(db_session, tmp_path, cid, storage=service.storage)
    read = service.import_issued(
        _payload(
            cid, numero=7, giorno=date(2026, 5, 5), pdf_sorgente=PdfSorgente(document_id=doc_id)
        ),
        ADMIN,
    )
    assert read.pdf_document_id == doc_id
    data, content_type, _ = service.download(read.id, "pdf", ADMIN)
    assert (data, content_type) == (b"%PDF-1.4 fake", "application/pdf")
    with pytest.raises(Conflict):
        service.export_xml(read.id, ADMIN)
    with pytest.raises(Conflict):
        service.produce_artifacts(read.id, ADMIN)


def test_a_pdf_of_another_customer_or_without_bytes_is_refused(
    db_session: Session, tmp_path
) -> None:  # noqa: ANN001
    from pigrocrm.core.config import get_settings
    from pigrocrm.core.documents.schemas import DocumentCreate
    from pigrocrm.core.documents.service import DocumentService
    from pigrocrm.core.errors import ValidationFailed
    from pigrocrm.core.invoices.schemas import PdfSorgente
    from pigrocrm.core.storage.local import LocalFileStorage

    service = _svc(db_session, tmp_path)
    cid, other = _fiscal_customer_id(db_session), _fiscal_customer_id(db_session)
    foreign = _pdf_document(db_session, tmp_path, other, storage=service.storage)
    # Neither refusal below leaves a row behind, so both may use the same numero.
    with pytest.raises(ValidationFailed):
        service.import_issued(
            _payload(
                cid,
                numero=7,
                giorno=date(2026, 5, 5),
                pdf_sorgente=PdfSorgente(document_id=foreign),
            ),
            ADMIN,
        )
    assert db_session.execute(select(Invoice).where(Invoice.numero == 7)).first() is None
    empty = DocumentService(db_session, LocalFileStorage(tmp_path), get_settings()).create(
        DocumentCreate(customer_id=cid, tipo="fattura", titolo="vuoto"), ADMIN
    )
    # numero=7 again, and that is the point: the refused call above validated the PDF
    # before writing anything, so the register does not carry a 7 and this call is not
    # working around a leaked row.
    with pytest.raises(ValidationFailed):
        service.import_issued(
            _payload(
                cid,
                numero=7,
                giorno=date(2026, 5, 5),
                pdf_sorgente=PdfSorgente(document_id=empty.id),
            ),
            ADMIN,
        )


# --- the original PDF taken from Drive (slice 9C §3.5) -------------------------------

ROOT_FOLDER = "1RadiceFattureAAA"
SUB_FOLDER = "1SottocartellaACME"
DRIVE_PDF = "1FatturaOriginale1"
DRIVE_TXT = "1AppuntiTestoZZZZZ"
OUTSIDE_FOLDER = "1CartellaPersonale"
OUTSIDE_PDF = "1FatturaAltroLavor"
EMPTY_PDF = "1FatturaVuotaZeroB"

ORIGINAL_PDF = b"%PDF-1.4 la fattura che il cliente ha ricevuto nel 2026"


def _drive() -> "FakeDrive":  # noqa: F821
    """One configured root holding the invoice's original PDF, and a second top-level
    folder that is *not* configured -- so "inside the roots" is not accidentally true
    of everything on the drive."""
    from fakes.fake_drive import FakeDrive

    drive = FakeDrive()
    drive.add_folder("Fatture", parent=drive.root_id, file_id=ROOT_FOLDER)
    drive.add_folder("ACME", parent=ROOT_FOLDER, file_id=SUB_FOLDER)
    drive.add_file(
        "Fattura 7-2026.pdf",
        parent=SUB_FOLDER,
        mime="application/pdf",
        content=ORIGINAL_PDF,
        file_id=DRIVE_PDF,
    )
    drive.add_file(
        "Appunti.txt",
        parent=SUB_FOLDER,
        mime="text/plain",
        content=b"non e' una fattura",
        file_id=DRIVE_TXT,
    )
    # Zero bytes, and Drive is perfectly happy to serve it: a sync that died half way,
    # a placeholder somebody made and never filled.
    drive.add_file(
        "Fattura 8-2026.pdf",
        parent=SUB_FOLDER,
        mime="application/pdf",
        content=b"",
        file_id=EMPTY_PDF,
    )
    drive.add_folder("Altro lavoro", parent=drive.root_id, file_id=OUTSIDE_FOLDER)
    drive.add_file(
        "fattura-di-un-altro.pdf",
        parent=OUTSIDE_FOLDER,
        mime="application/pdf",
        content=b"%PDF-1.4 non e' roba di questo CRM",
        file_id=OUTSIDE_PDF,
    )
    return drive


def _reader_factory(drive: "FakeDrive", *, roots: tuple[str, ...] = (ROOT_FOLDER,)):  # noqa: F821, ANN202
    """The `drive_reader_factory` seam `InvoiceService.__init__` takes for the tests
    that are about the *import*, not about the credential: a reader pointed straight at
    an in-memory Drive, with no account row and no token exchange in the way.

    The wiring that does go through `drive_reader_for` -- the account row, the sealed
    refresh token, the roots read from that row -- is exercised by
    `test_the_original_pdf_can_come_straight_from_drive` below.
    """
    from pigrocrm.core.drive.reader import DriveReader
    from pigrocrm.core.drive.transport import DriveTransport

    class _Tokens:
        def access_token(self) -> str:
            return "at-1"

        def forget(self) -> None:
            pass

    def build(actor: Actor) -> DriveReader:
        return DriveReader(
            DriveTransport(tokens=_Tokens(), http=drive, sleep=lambda _: None), roots=roots
        )

    return build


def _drive_account(session: Session, *, roots: tuple[str, ...] = (ROOT_FOLDER,)) -> Actor:
    """A titolare with Drive connected and the roots configured, and the `Actor` that
    is them. The refresh token is really sealed with the key `gmail_settings`
    publishes, so `drive_reader_for` runs its real `unseal`."""
    from fakes.gmail_fixtures import TOKEN_KEY

    from pigrocrm.core.auth.models import User
    from pigrocrm.core.drive.models import GoogleDriveAccount
    from pigrocrm.core.drive.schemas import DRIVE_SCOPE_FILE, DRIVE_SCOPE_READONLY
    from pigrocrm.core.gmail.crypto import seal

    user = User(
        email=f"titolare-{uuid4().hex[:8]}@example.it",
        nome="Titolare",
        password_hash="x",
        ruolo="admin",
        attivo=True,
    )
    session.add(user)
    session.flush()
    ciphertext, nonce = seal("1//0gDriveRefreshToken", TOKEN_KEY)
    session.add(
        GoogleDriveAccount(
            user_id=user.id,
            google_sub=f"sub-{user.id}",
            email_address="io@example.it",
            refresh_token_ciphertext=ciphertext,
            refresh_token_nonce=nonce,
            scopes_granted=[DRIVE_SCOPE_READONLY, DRIVE_SCOPE_FILE],
            status="active",
            root_folder_ids=list(roots),
        )
    )
    session.flush()
    return Actor(id=user.id, type="user", role="admin")


def _imported_documents(session: Session):  # noqa: ANN202
    from pigrocrm.core.documents.models import Document

    return list(session.execute(select(Document).where(Document.tipo == "fattura")).scalars())


def test_the_original_pdf_can_come_straight_from_drive(
    db_session: Session, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    """§3.5 through the real credential path: the account row, the sealed refresh
    token and the roots that row configures. Only the HTTP boundary is faked, which is
    the seam slice 5 exists to have.

    What is asserted is the whole point of the feature: the PDF the customer actually
    received is what the CRM hands back, byte for byte, from a `documents` row of type
    `fattura` whose title names the invoice -- not a PDF rendered today with today's
    layout, which would not be that document.
    """
    from fakes.gmail_fixtures import gmail_settings

    from pigrocrm.core.activities.models import Activity
    from pigrocrm.core.drive import reader as reader_module
    from pigrocrm.core.drive.transport import DriveTransport
    from pigrocrm.core.invoices.schemas import PdfSorgente

    drive = _drive()
    providers: list[object] = []

    class _Tokens:
        def access_token(self) -> str:
            return "at-1"

        def forget(self) -> None:
            pass

    real_user_transport_for = reader_module.user_transport_for

    def capture(account, settings, **_):  # noqa: ANN001, ANN202
        """The composition seam pointed at the in-memory Drive, with the token provider
        `drive_reader_for` built *captured* on the way past and replaced by a stub --
        the same monkeypatch `test_drive_reader.py::_inject` uses, and for the same
        reason: the composition can then be asserted without Google's token endpoint
        being called at all (this suite opens no socket, see the root `conftest.py`).

        `user_transport_for` is the name replaced, because that is the one helper both
        users of a user-credentialled Drive compose through; the real one still runs, so
        the row's refresh token is really unsealed on the way past.
        """
        providers.append(real_user_transport_for(account, settings)._tokens)
        return DriveTransport(tokens=_Tokens(), http=drive, sleep=lambda _: None)

    monkeypatch.setattr(reader_module, "user_transport_for", capture)
    actor = _drive_account(db_session)
    service = _svc(db_session, tmp_path, settings=gmail_settings())
    cid = _fiscal_customer_id(db_session)

    read = service.import_issued(
        _payload(
            cid,
            numero=7,
            giorno=date(2026, 5, 5),
            pdf_sorgente=PdfSorgente(drive_file_id=DRIVE_PDF),
        ),
        actor,
    )

    assert read.pdf_document_id is not None
    # The credential really was this actor's, with the refresh token really unsealed.
    assert len(providers) == 1
    assert providers[0].refresh_token == "1//0gDriveRefreshToken"  # type: ignore[attr-defined]
    data, content_type, filename = service.download(read.id, "pdf", actor)
    assert (data, content_type) == (ORIGINAL_PDF, "application/pdf")
    assert filename == "fattura-2026-7.pdf"
    documents = _imported_documents(db_session)
    assert len(documents) == 1
    document = documents[0]
    assert document.id == read.pdf_document_id
    assert document.customer_id == cid
    assert document.titolo == "Fattura 2026/7 (originale)"
    assert document.versione_corrente == 1
    # Provenance is recorded, so in three years the answer to "where did this PDF come
    # from?" is the Drive id it was fetched from and not a shrug.
    payloads = [
        payload
        for payload in db_session.execute(
            select(Activity.payload).where(
                Activity.entity_type == "document",
                Activity.entity_id == document.id,
                Activity.kind == "document.importato",
            )
        ).scalars()
    ]
    assert payloads and payloads[0]["origine"]["drive_file_id"] == DRIVE_PDF


def test_a_drive_file_outside_the_configured_roots_is_a_conflict(
    db_session: Session, tmp_path
) -> None:  # noqa: ANN001
    """The refusal `DriveReader` reports as `NotFound("drive_file")` -- deliberately
    the same sentence for "does not exist", "is in a corner of the titolare's Drive
    nobody configured" and "belongs to a stranger" -- reaches the caller of an import
    as a `Conflict`: nothing about the *invoice* is missing, and the identifier in the
    refusal is a Drive id, not an id of this CRM.
    """
    from pigrocrm.core.errors import Conflict
    from pigrocrm.core.invoices.schemas import PdfSorgente

    drive = _drive()
    service = _svc(db_session, tmp_path, drive_reader_factory=_reader_factory(drive))
    cid = _fiscal_customer_id(db_session)

    with pytest.raises(Conflict) as caught:
        service.import_issued(
            _payload(
                cid,
                numero=7,
                giorno=date(2026, 5, 5),
                pdf_sorgente=PdfSorgente(drive_file_id=OUTSIDE_PDF),
            ),
            ADMIN,
        )

    assert caught.value.details["drive_file_id"] == OUTSIDE_PDF
    assert db_session.execute(select(Invoice).where(Invoice.numero == 7)).first() is None
    assert _imported_documents(db_session) == []


def test_a_drive_file_that_is_not_a_pdf_is_refused(db_session: Session, tmp_path) -> None:  # noqa: ANN001
    """Inside the roots, readable, and still not the original document: a text file is
    not the PDF the customer holds. Refused on the field the caller typed, so the
    message points at `pdf_sorgente.drive_file_id` rather than at "a document"."""
    from pigrocrm.core.errors import ValidationFailed
    from pigrocrm.core.invoices.schemas import PdfSorgente

    drive = _drive()
    service = _svc(db_session, tmp_path, drive_reader_factory=_reader_factory(drive))
    cid = _fiscal_customer_id(db_session)

    with pytest.raises(ValidationFailed) as caught:
        service.import_issued(
            _payload(
                cid,
                numero=7,
                giorno=date(2026, 5, 5),
                pdf_sorgente=PdfSorgente(drive_file_id=DRIVE_TXT),
            ),
            ADMIN,
        )

    assert caught.value.details["field"] == "pdf_sorgente.drive_file_id"
    assert db_session.execute(select(Invoice).where(Invoice.numero == 7)).first() is None
    assert _imported_documents(db_session) == []
    # And it was refused *without downloading it*. The mime is a fact one `files.get`
    # already knows, so paying for the bytes to learn it spends the titolare's bandwidth
    # and Drive quota to reach a decidable "no" -- for a 20 MB spreadsheet somebody named
    # by mistake, in full. `alt=media` is how the reader asks for content; the assertion
    # is that no request ever carried it.
    assert not [request for request in drive.requests if request.params.get("alt") == ["media"]]


def test_a_register_refusal_after_the_drive_read_files_no_document(
    db_session: Session, tmp_path
) -> None:  # noqa: ANN001
    """The Drive read happens among the *pure* checks, above the counter lock, so a
    refusal that comes from the register itself -- here a number the register already
    carries -- must leave no imported document behind. Bytes fetched and thrown away
    cost one HTTP call; a `documents` row committed for an invoice that was refused
    would be an orphan PDF filed against a customer forever.
    """
    from pigrocrm.core.errors import Conflict
    from pigrocrm.core.invoices.schemas import PdfSorgente

    drive = _drive()
    service = _svc(db_session, tmp_path, drive_reader_factory=_reader_factory(drive))
    cid = _fiscal_customer_id(db_session)
    service.import_issued(_payload(cid, numero=7, giorno=date(2026, 5, 5)), ADMIN)

    with pytest.raises(Conflict):
        service.import_issued(
            _payload(
                cid,
                numero=7,
                giorno=date(2026, 5, 5),
                pdf_sorgente=PdfSorgente(drive_file_id=DRIVE_PDF),
            ),
            ADMIN,
        )

    assert _imported_documents(db_session) == []


def test_a_failed_commit_takes_the_imported_pdf_with_it(
    db_session: Session, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    """The import is one transaction, and this is the test that proves the `documents`
    row is inside it. `import_bytes(commit=False)` is what makes that true: with the
    ordinary committing `create`, the document would survive the rollback of the
    invoice it was fetched for.
    """
    from pigrocrm.core.invoices.schemas import PdfSorgente

    drive = _drive()
    service = _svc(db_session, tmp_path, drive_reader_factory=_reader_factory(drive))
    cid = _fiscal_customer_id(db_session)

    def boom() -> None:
        raise RuntimeError("la connessione e' caduta durante il commit")

    monkeypatch.setattr(db_session, "commit", boom)
    with pytest.raises(RuntimeError):
        service.import_issued(
            _payload(
                cid,
                numero=7,
                giorno=date(2026, 5, 5),
                pdf_sorgente=PdfSorgente(drive_file_id=DRIVE_PDF),
            ),
            ADMIN,
        )
    monkeypatch.undo()

    assert db_session.execute(select(Invoice).where(Invoice.numero == 7)).first() is None
    assert _imported_documents(db_session) == []


def test_an_empty_pdf_on_drive_is_refused_among_the_pure_checks(
    db_session: Session, tmp_path
) -> None:  # noqa: ANN001
    """A zero-byte file inside the configured roots: readable, `application/pdf`, and
    not a document. `DocumentService._check_upload` would refuse it too -- but only
    from *inside* `import_bytes`, which runs after the `Invoice` has been flushed, and
    the only rollback in `import_issued` wraps the final `commit`. So the refusal is
    hoisted here, among the pure checks, where it belongs: it is a fact about the file
    the caller named.

    Three things must be absent afterwards, not one: the fattura, the imported
    document, and the counter row -- `lock_counter` creates it, so a refusal that
    escaped the transaction would leave a register year in existence because somebody
    typed a bad Drive id.
    """
    from pigrocrm.core.documents.models import Document
    from pigrocrm.core.errors import ValidationFailed
    from pigrocrm.core.invoices.models import InvoiceCounter
    from pigrocrm.core.invoices.schemas import PdfSorgente

    drive = _drive()
    service = _svc(db_session, tmp_path, drive_reader_factory=_reader_factory(drive))
    cid = _fiscal_customer_id(db_session)

    with pytest.raises(ValidationFailed) as caught:
        service.import_issued(
            _payload(
                cid,
                numero=7,
                giorno=date(2026, 5, 5),
                pdf_sorgente=PdfSorgente(drive_file_id=EMPTY_PDF),
            ),
            ADMIN,
        )

    assert caught.value.details["field"] == "pdf_sorgente.drive_file_id"
    assert db_session.execute(select(Invoice).where(Invoice.numero == 7)).first() is None
    assert db_session.execute(select(Document)).first() is None
    assert db_session.get(InvoiceCounter, 2026) is None


def test_a_refusal_from_inside_import_bytes_still_leaves_nothing_flushed(
    db_session: Session, tmp_path
) -> None:  # noqa: ANN001
    """The guard on the window that cannot be closed by hoisting checks.

    `import_bytes` runs *after* the `Invoice` and its lines have been flushed, and it
    can refuse for reasons that are not facts about the Drive file at all: here a
    required custom field defined on `document`, which every `documents` row must carry
    and this one has no way to supply. Without a rollback at that call site the refused
    fattura would sit flushed in the caller's session, and the next query in the same
    transaction would find a fattura the caller had just been told did not exist --
    the precise failure `import_issued`'s own docstring exists to describe.

    The assertion after the refusal is therefore made *through the same session*: it is
    the session's view, not the database's, that was corrupted before the fix.
    """
    from pigrocrm.core.documents.models import Document
    from pigrocrm.core.errors import ValidationFailed
    from pigrocrm.core.fields.schemas import FieldDefinitionCreate
    from pigrocrm.core.fields.service import FieldDefinitionService
    from pigrocrm.core.invoices.models import InvoiceCounter
    from pigrocrm.core.invoices.schemas import PdfSorgente

    drive = _drive()
    service = _svc(db_session, tmp_path, drive_reader_factory=_reader_factory(drive))
    cid = _fiscal_customer_id(db_session)
    FieldDefinitionService(db_session).create(
        FieldDefinitionCreate(
            entity_type="document",
            key="pratica",
            label="Pratica",
            field_type="text",
            required=True,
        ),
        ADMIN,
    )

    with pytest.raises(ValidationFailed):
        service.import_issued(
            _payload(
                cid,
                numero=7,
                giorno=date(2026, 5, 5),
                pdf_sorgente=PdfSorgente(drive_file_id=DRIVE_PDF),
            ),
            ADMIN,
        )

    assert db_session.execute(select(Invoice).where(Invoice.numero == 7)).first() is None
    assert db_session.execute(select(Document)).first() is None
    assert db_session.get(InvoiceCounter, 2026) is None
    # And the session is still usable: a later read does not raise on a pending failure.
    assert service.repo.numbers_present(2026) == set()
