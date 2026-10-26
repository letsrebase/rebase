"""REB-752: the register of a contract's days, as the rows a client reads next to an invoice,
as a CSV and as an archived PDF, and the one property they all share: the total is the
invoice's."""

import csv
import io
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.models import Document
from pigrocrm.core.emitter.schemas import LegalEntityUpsert
from pigrocrm.core.emitter.service import LegalEntityService
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.invoices.schemas import InvoiceIssue
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage.local import LocalFileStorage
from pigrocrm.core.work_units.invoicing import assemble_unbilled_work_units_into_new_invoice
from pigrocrm.core.work_units.models import Approval, WorkUnit
from pigrocrm.core.work_units.register import (
    BILLED_STATI,
    REGISTER_TEMPLATE_NOME,
    ContractRegisterService,
)

ADMIN = Actor(id=None, type="system", role="admin")


def _contract(db_session: Session) -> Contract:
    customer = Customer(
        ragione_sociale=f"Acme {uuid4()} S.r.l.",
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
    contract = Contract(
        customer_id=customer.id,
        titolo="Consulenza CTO",
        inizio=date(2026, 1, 1),
        tipo_rinnovo="nessuno",
        preavviso_disdetta_giorni=30,
        cadenza_fatturazione="mensile",
        politica_spese={"kind": "non_rimborsabile"},
    )
    db_session.add(contract)
    db_session.flush()
    db_session.add(
        RateCard(
            contract_id=contract.id,
            valido_da=date(2026, 1, 1),
            tipo="giornaliero",
            importo=Decimal("500.00"),
            unita="giorno",
        )
    )
    db_session.flush()
    return contract


def _install_profiles(db_session: Session) -> None:
    """The azienda and its regime, which `issue()` reads (duplicated from
    `test_work_units_invoicing.py` rather than imported: `conftest` is an ambiguous module
    name across this repository's test roots)."""
    from pigrocrm.core.emitter.repository import LegalEntityRepository
    from pigrocrm.core.fiscal.repository import FiscalProfileRepository
    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
    from pigrocrm.core.fiscal.service import FiscalProfileService

    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(
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
    azienda = LegalEntityRepository(db_session).default()
    assert azienda is not None
    if FiscalProfileRepository(db_session).get(azienda.id) is None:
        FiscalProfileService(db_session).upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)


def _approval(db_session: Session, contract: Contract, **overrides: object) -> Approval:
    document = Document(contract_id=contract.id, tipo="contratto", titolo="Mail del cliente")
    db_session.add(document)
    db_session.flush()
    payload: dict[str, object] = {
        "contract_id": contract.id,
        "canale": "email",
        "mittente": "mario@cliente.example",
        "ricevuto_il": datetime(2026, 10, 2, 9, 30),
        "message_id": "<abc@cliente.example>",
        "document_id": document.id,
        "estratto": "Confermiamo.",
        "origine": {"kind": "manuale"},
    }
    payload.update(overrides)
    approval = Approval(**payload)  # type: ignore[arg-type]
    db_session.add(approval)
    db_session.flush()
    return approval


# A day is born `proposto`, `approvato` or `lavorato` and walks to the rest: the trigger
# refuses any other start, so a test that wants a billed day takes the graph's own edges.
_WALK: dict[str, tuple[str, ...]] = {
    "fatturato": ("fatturato",),
    "contestato": ("contestato",),
    "pagato": ("fatturato", "pagato"),
}


def _day(db_session: Session, contract: Contract, **overrides: object) -> WorkUnit:
    payload: dict[str, object] = {
        "contract_id": contract.id,
        "data": date(2026, 10, 7),
        "quantita": Decimal("1.00"),
        "descrizione": "Giornata di consulenza",
        "stato": "lavorato",
    }
    payload.update(overrides)
    target = str(payload["stato"])
    walk = _WALK.get(target, ())
    if walk:
        payload["stato"] = "lavorato"
    day = WorkUnit(**payload)  # type: ignore[arg-type]
    db_session.add(day)
    db_session.flush()
    for stato in walk:
        day.stato = stato
        db_session.flush()
    return day


def test_the_register_lists_the_billed_days_with_their_proof_and_leaves_the_rest_out(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    approval = _approval(db_session, contract)
    billed = _day(
        db_session, contract, data=date(2026, 10, 7), approval_id=approval.id, stato="fatturato"
    )
    _day(db_session, contract, data=date(2026, 10, 8), stato="proposto")
    _day(db_session, contract, data=date(2026, 10, 9))  # worked, not billed yet
    _day(  # another month
        db_session, contract, data=date(2026, 11, 3), approval_id=approval.id, stato="fatturato"
    )

    register = ContractRegisterService(db_session).build(
        contract.id, date(2026, 10, 1), date(2026, 10, 31), ADMIN
    )

    assert [(v.work_unit_id, v.data, v.stato) for v in register.voci] == [
        (billed.id, date(2026, 10, 7), "fatturato")
    ]
    [voce] = register.voci
    assert voce.approvazione is not None
    assert voce.approvazione.canale == "email"
    assert register.totale_quantita == Decimal("1.00")
    assert register.senza_approvazione == 0
    assert "fatturato" in BILLED_STATI and "lavorato" not in BILLED_STATI


def test_a_day_with_no_approval_stays_on_the_register_and_is_counted(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    _day(db_session, contract, stato="fatturato", data=date(2026, 10, 7))

    register = ContractRegisterService(db_session).build(
        contract.id, date(2026, 10, 1), date(2026, 10, 31), ADMIN
    )

    assert len(register.voci) == 1
    assert register.voci[0].approvazione is None
    assert register.senza_approvazione == 1
    assert register.totale_quantita == Decimal("1.00")


def test_the_register_total_is_the_invoices_and_names_the_invoice_once_it_is_numbered(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session)
    approval = _approval(db_session, contract)
    _day(db_session, contract, data=date(2026, 10, 7), approval_id=approval.id)
    _day(db_session, contract, data=date(2026, 10, 8), quantita=Decimal("0.50"))
    _day(db_session, contract, data=date(2026, 11, 2))
    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN, date(2026, 10, 1), date(2026, 10, 31)
    )
    service = ContractRegisterService(db_session)

    draft = service.build(contract.id, date(2026, 10, 1), date(2026, 10, 31), ADMIN)

    # «Its total matches the invoice»: 1.50 days on the line, 1.50 on the register, 750.00.
    [line] = InvoiceService(db_session, local_storage).lines(invoice.id, ADMIN)
    assert draft.totale_quantita == line.quantita == Decimal("1.5")
    assert invoice.imponibile == Decimal("750.00")
    # A draft has no number yet; the day is on the register for being billed, not numbered.
    assert [v.numero_fattura for v in draft.voci] == [None, None]

    issued = InvoiceService(db_session, local_storage).issue(invoice.id, InvoiceIssue(), ADMIN)
    numbered = service.build(contract.id, date(2026, 10, 1), date(2026, 10, 31), ADMIN)
    expected = f"{issued.anno}/{issued.numero}"
    assert [v.numero_fattura for v in numbered.voci] == [expected, expected]


def test_a_disputed_and_a_paid_day_are_still_on_it(db_session: Session) -> None:
    contract = _contract(db_session)
    _day(db_session, contract, stato="contestato", data=date(2026, 10, 7))
    _day(db_session, contract, stato="pagato", data=date(2026, 10, 8))

    register = ContractRegisterService(db_session).build(
        contract.id, date(2026, 10, 1), date(2026, 10, 31), ADMIN
    )

    assert [v.stato for v in register.voci] == ["contestato", "pagato"]


def test_a_period_that_ends_before_it_starts_is_refused(db_session: Session) -> None:
    contract = _contract(db_session)
    with pytest.raises(ValidationFailed):
        ContractRegisterService(db_session).build(
            contract.id, date(2026, 10, 31), date(2026, 10, 1), ADMIN
        )


def test_the_register_of_an_unknown_contract_is_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        ContractRegisterService(db_session).build(
            uuid4(), date(2026, 10, 1), date(2026, 10, 31), ADMIN
        )


def test_the_csv_is_rfc_4180_with_a_totals_row_and_the_same_rows(db_session: Session) -> None:
    contract = _contract(db_session)
    approval = _approval(db_session, contract)
    _day(
        db_session,
        contract,
        data=date(2026, 10, 7),
        quantita=Decimal("0.50"),
        descrizione='Revisione, "fase 2"\nsecondo giorno',
        stato="fatturato",
        approval_id=approval.id,
    )
    _day(db_session, contract, data=date(2026, 10, 8), stato="fatturato")
    service = ContractRegisterService(db_session)
    register = service.build(contract.id, date(2026, 10, 1), date(2026, 10, 31), ADMIN)

    raw = service.build_csv(register)

    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    assert text.endswith("\r\n")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["Data", "Quantità", "Descrizione", "Fattura", "Approvazione"]
    assert rows[1] == [
        "2026-10-07",
        "0.50",
        'Revisione, "fase 2"\nsecondo giorno',
        "",
        "email · mario@cliente.example · 2026-10-02 · <abc@cliente.example>",
    ]
    assert rows[2][:2] == ["2026-10-08", "1.00"] and rows[2][4] == ""
    assert rows[3] == ["", "1.50", "Totale", "", ""]
    assert service.csv_filename(register) == "registro-giornate-2026-10-01-2026-10-31.csv"


@pytest.mark.parametrize("leader", ["=", "+", "-", "@"])
def test_a_description_a_spreadsheet_would_run_as_a_formula_is_defused(
    db_session: Session, leader: str
) -> None:
    contract = _contract(db_session)
    _day(db_session, contract, stato="fatturato", descrizione=f"{leader}SUM(A1:A9)")
    service = ContractRegisterService(db_session)
    register = service.build(contract.id, date(2026, 10, 1), date(2026, 10, 31), ADMIN)

    rows = list(csv.reader(io.StringIO(service.build_csv(register).decode("utf-8-sig"))))

    assert rows[1][2] == f"'{leader}SUM(A1:A9)"


def test_the_pdf_is_archived_as_a_document_of_the_customer_and_carries_the_same_figures(
    db_session: Session,
    local_storage: LocalFileStorage,
    extract_pdf_text: Callable[[LocalFileStorage, Session, UUID], str],
) -> None:
    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(
            ragione_sociale="Studio Rossi",
            partita_iva="01234567890",
            indirizzo="Via Vittorio Veneto 12",
            cap="20124",
            comune="Milano",
            provincia="MI",
            nazione="IT",
            email="mario@example.com",
        ),
        ADMIN,
    )
    contract = _contract(db_session)
    approval = _approval(db_session, contract)
    _day(
        db_session,
        contract,
        data=date(2026, 10, 7),
        descrizione="Revisione @architettura [fase 1] & #2",
        stato="fatturato",
        approval_id=approval.id,
    )
    _day(db_session, contract, data=date(2026, 10, 8), stato="fatturato")
    service = ContractRegisterService(db_session, local_storage)

    document = service.render_pdf(contract.id, date(2026, 10, 1), date(2026, 10, 31), ADMIN)

    assert document.customer_id == contract.customer_id
    assert document.tipo == "rapporto_ore"
    assert document.versione_corrente == 1
    text = extract_pdf_text(local_storage, db_session, document.id)
    assert "Registro giornate" in text
    assert "01/10/2026 - 31/10/2026" in text
    assert "Consulenza CTO" in text
    assert "07/10/2026" in text and "08/10/2026" in text
    # The description wraps inside its column, so the hostile characters are checked
    # one by one: each reached the PDF literally, none was eaten by Markdown or Typst.
    for fragment in ("Revisione", "@architettura", "[fase 1]", "& #2"):
        assert fragment in text
    assert "mario@cliente.example" in text and "2026-10-02" in text
    assert "Totale: 2.00" in text and "2 giornate" in text

    # Rendered again, it is a new version of the same template's output, not a second copy
    # of the template: the layout row is created once.
    service.render_pdf(contract.id, date(2026, 10, 1), date(2026, 10, 31), ADMIN)
    from pigrocrm.core.templates.repository import TemplateRepository

    assert TemplateRepository(db_session).get_by_nome(REGISTER_TEMPLATE_NOME) is not None


def test_the_pdf_needs_a_storage(db_session: Session) -> None:
    contract = _contract(db_session)
    with pytest.raises(ValidationFailed):
        ContractRegisterService(db_session).render_pdf(
            contract.id, date(2026, 10, 1), date(2026, 10, 31), ADMIN
        )


def test_a_register_narrowed_to_an_invoice_leaves_out_the_days_billed_on_another(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_profiles(db_session)
    contract = _contract(db_session)
    for day in (date(2026, 10, 7), date(2026, 10, 10), date(2026, 10, 20)):
        _day(db_session, contract, data=day)
    other = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN, date(2026, 10, 10), date(2026, 10, 10)
    )
    mine = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )
    service = ContractRegisterService(db_session)

    whole = service.build(contract.id, date(2026, 10, 7), date(2026, 10, 20), ADMIN)
    narrowed = service.build(
        contract.id, date(2026, 10, 7), date(2026, 10, 20), ADMIN, invoice_id=mine.id
    )

    assert whole.totale_quantita == Decimal("3.00")
    assert [v.data for v in narrowed.voci] == [date(2026, 10, 7), date(2026, 10, 20)]
    assert narrowed.totale_quantita == Decimal("2.00")
    assert other.id != mine.id
