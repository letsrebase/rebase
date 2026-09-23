"""REB-369's own "Done when": reviewing an import against a contract with recorded,
unbilled `work_units` proposes a day-to-line mapping -- exercised end to end through
`InvoiceService.review_import`, the FPR12 fixture REB-363/364/365 already use
(`fpr12-consulenza-marzo.xml`: `data_emissione=2026-03-15`, one line,
`quantita=5.000000` giorni, `prezzo_totale=1000.00`, cliente PIVA `09876543210`), and
a real `Contract`/`RateCard`/`WorkUnit` set -- the same fixture-reuse and
read-only/idempotence discipline `test_invoice_import_review.py` already holds
`review_content` to, extended onto the day-mapping proposal this issue adds.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.schemas import DocumentCreate
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.emitter.models import EmitterProfile
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm.core.work_units.models import WorkUnit

FIXTURES = Path(__file__).parent / "fixtures" / "fatturapa"
CONSULENZA = "fpr12-consulenza-marzo.xml"
ADMIN = Actor(id=None, type="user", role="admin")

# The fixture's own `CedentePrestatore` -- matching this on an `EmitterProfile` is
# what makes the fixture "outgoing" for the account holder.
FORNITORE_PIVA = "01234567890"
FORNITORE_CF = "BNCCHR85M41H501Z"
# The fixture's own `CessionarioCommittente` -- matching this on a `Customer` is
# what resolves `matched_customer_id`, the day-mapping proposal's own prerequisite.
CLIENTE_PIVA = "09876543210"
# The fixture's own single line: 5 giorni at EUR 200.00/giorno, EUR 1000.00 total.
DATA_EMISSIONE = date(2026, 3, 15)


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _emitter(session: Session) -> EmitterProfile:
    profile = EmitterProfile(
        ragione_sociale="Chiara Bianchi",
        partita_iva=FORNITORE_PIVA,
        codice_fiscale=FORNITORE_CF,
        nazione="IT",
    )
    session.add(profile)
    session.flush()
    return profile


def _customer(session: Session, **overrides: object) -> Customer:
    base: dict[str, object] = {
        "ragione_sociale": "Esempio Servizi S.r.l.",
        "partita_iva": CLIENTE_PIVA,
        "indirizzo": "Piazza dei Modelli 4",
        "cap": "00187",
        "comune": "Roma",
        "provincia": "RM",
        "nazione": "IT",
    }
    base.update(overrides)
    customer = Customer(**base)
    session.add(customer)
    session.flush()
    return customer


def _document(session: Session, storage: LocalFileStorage, customer: Customer) -> UUID:
    service = DocumentService(session, storage)
    document = service.create(
        DocumentCreate(customer_id=customer.id, titolo="Fattura ricevuta"), ADMIN
    )
    service.add_version(document.id, _fixture(CONSULENZA), "application/xml", ADMIN)
    return document.id


def _contract(session: Session, customer: Customer, **overrides: object) -> Contract:
    payload: dict[str, object] = {
        "customer_id": customer.id,
        "titolo": "Consulenza",
        "inizio": date(2026, 1, 1),
        "tipo_rinnovo": "nessuno",
        "preavviso_disdetta_giorni": 30,
        "cadenza_fatturazione": "mensile",
        "politica_spese": {"kind": "non_rimborsabile"},
    }
    payload.update(overrides)
    contract = Contract(**payload)  # type: ignore[arg-type]
    session.add(contract)
    session.flush()
    return contract


def _rate_card(session: Session, contract: Contract, **overrides: object) -> RateCard:
    payload: dict[str, object] = {
        "contract_id": contract.id,
        "valido_da": date(2026, 1, 1),
        "valido_a": None,
        "tipo": "giornaliero",
        "importo": Decimal("200.00"),
        "unita": "giorno",
        "frazioni_ammesse": [Decimal("1")],
    }
    payload.update(overrides)
    rate_card = RateCard(**payload)  # type: ignore[arg-type]
    session.add(rate_card)
    session.flush()
    return rate_card


def _work_unit(session: Session, contract: Contract, **overrides: object) -> WorkUnit:
    payload: dict[str, object] = {
        "contract_id": contract.id,
        "data": date(2026, 3, 1),
        "quantita": Decimal("1.00"),
        "descrizione": "Giornata di consulenza",
        "stato": "lavorato",
    }
    payload.update(overrides)
    work_unit = WorkUnit(**payload)  # type: ignore[arg-type]
    session.add(work_unit)
    session.flush()
    return work_unit


def _five_unbilled_days(session: Session, contract: Contract) -> list[WorkUnit]:
    """5 giorni, one per day, 2026-03-01 through 2026-03-05 -- exactly what the
    fixture's own single line (`quantita=5.000000`) bills."""
    return [_work_unit(session, contract, data=date(2026, 3, day)) for day in range(1, 6)]


def test_a_day_rate_contracts_recorded_unbilled_days_propose_a_mapping(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _emitter(db_session)
    customer = _customer(db_session)
    contract = _contract(db_session, customer)
    _rate_card(db_session, contract)
    days = _five_unbilled_days(db_session, contract)
    document_id = _document(db_session, local_storage, customer)

    [row] = InvoiceService(db_session, local_storage).review_import([document_id], ADMIN)

    assert row.outcome == "ready"
    assert row.matched_customer_id == customer.id
    assert row.mappature_giorni is not None
    [proposta] = row.mappature_giorni
    assert proposta is not None
    assert proposta.work_unit_ids == [day.id for day in days]
    assert proposta.periodo_da == date(2026, 3, 1)
    assert proposta.periodo_a == date(2026, 3, 5)
    assert proposta.numero_giorni == 5
    assert proposta.importo_proposto == Decimal("1000.00")
    assert proposta.importo_riga == Decimal("1000.00")
    assert proposta.importi_coincidono is True


def test_reviewing_twice_leaves_the_work_units_untouched_and_returns_the_same_mapping(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _emitter(db_session)
    customer = _customer(db_session)
    contract = _contract(db_session, customer)
    _rate_card(db_session, contract)
    days = _five_unbilled_days(db_session, contract)
    document_id = _document(db_session, local_storage, customer)
    service = InvoiceService(db_session, local_storage)

    first = service.review_import([document_id], ADMIN)
    second = service.review_import([document_id], ADMIN)

    assert [row.model_dump(mode="json") for row in first] == [
        row.model_dump(mode="json") for row in second
    ]
    for day in days:
        db_session.refresh(day)
        assert day.invoice_line_id is None
        assert day.stato == "lavorato"


def test_no_day_mapping_when_the_customer_does_not_match(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _emitter(db_session)
    unrelated_customer = _customer(db_session, partita_iva="11111111111")
    document_id = _document(db_session, local_storage, unrelated_customer)

    [row] = InvoiceService(db_session, local_storage).review_import([document_id], ADMIN)

    assert row.outcome == "needs_customer_confirmation"
    assert row.mappature_giorni is None


def test_no_day_mapping_when_the_customer_has_no_contract_at_all(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _emitter(db_session)
    customer = _customer(db_session)
    document_id = _document(db_session, local_storage, customer)

    [row] = InvoiceService(db_session, local_storage).review_import([document_id], ADMIN)

    assert row.outcome == "ready"
    assert row.mappature_giorni == [None]


def test_no_day_mapping_when_the_contracts_rate_card_is_not_day_rate(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _emitter(db_session)
    customer = _customer(db_session)
    contract = _contract(db_session, customer)
    _rate_card(
        db_session,
        contract,
        tipo="orario",
        importo=Decimal("90.00"),
        unita="ora",
    )
    document_id = _document(db_session, local_storage, customer)

    [row] = InvoiceService(db_session, local_storage).review_import([document_id], ADMIN)

    assert row.mappature_giorni == [None]


def test_no_day_mapping_when_the_days_are_already_billed(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _emitter(db_session)
    customer = _customer(db_session)
    contract = _contract(db_session, customer)
    _rate_card(db_session, contract)
    days = _five_unbilled_days(db_session, contract)
    # One of the five is already on a real invoice line -- only 4 giorni are left
    # unbilled, short of the fixture line's own quantita=5, so no complete match
    # exists even though the calendar days themselves are all still eligible dates.
    already_billed = Invoice(customer_id=customer.id, tipo="fattura", stato="bozza")
    db_session.add(already_billed)
    db_session.flush()
    billed_line = InvoiceLine(
        invoice_id=already_billed.id,
        numero_linea=1,
        descrizione="Giornata gia' fatturata",
        quantita=Decimal("1"),
        prezzo_unitario=Decimal("200.00"),
        prezzo_totale=Decimal("200.00"),
        aliquota_iva=Decimal("22.00"),
    )
    db_session.add(billed_line)
    db_session.flush()
    days[0].invoice_line_id = billed_line.id
    db_session.flush()
    document_id = _document(db_session, local_storage, customer)

    [row] = InvoiceService(db_session, local_storage).review_import([document_id], ADMIN)

    assert row.mappature_giorni == [None]


def test_no_day_mapping_when_the_customer_has_two_day_rate_contracts(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """Ambiguous: nothing on the document says which of two concurrent engagements
    its days belong to, mirroring mastro's own `resolveActiveContractId` restraint."""
    _emitter(db_session)
    customer = _customer(db_session)
    first_contract = _contract(db_session, customer, titolo="Consulenza A")
    _rate_card(db_session, first_contract)
    _five_unbilled_days(db_session, first_contract)
    second_contract = _contract(db_session, customer, titolo="Consulenza B")
    _rate_card(db_session, second_contract)
    _five_unbilled_days(db_session, second_contract)
    document_id = _document(db_session, local_storage, customer)

    [row] = InvoiceService(db_session, local_storage).review_import([document_id], ADMIN)

    assert row.mappature_giorni == [None]


def test_a_day_after_the_issue_date_is_excluded_but_an_exact_match_among_the_rest_still_proposes(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _emitter(db_session)
    customer = _customer(db_session)
    contract = _contract(db_session, customer)
    _rate_card(db_session, contract)
    days = _five_unbilled_days(db_session, contract)
    # A sixth day, worked after the invoice's own issue date -- never eligible for
    # a mapping proposed *by* that invoice.
    _work_unit(db_session, contract, data=date(2026, 4, 1))
    document_id = _document(db_session, local_storage, customer)

    [row] = InvoiceService(db_session, local_storage).review_import([document_id], ADMIN)

    assert row.mappature_giorni is not None
    [proposta] = row.mappature_giorni
    assert proposta is not None
    assert proposta.work_unit_ids == [day.id for day in days]
    assert proposta.numero_giorni == 5
