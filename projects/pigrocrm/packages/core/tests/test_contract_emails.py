"""REB-753: saved email templates per contract, and the draft they make from an invoice:
rendered from the invoice's own figures, with its PDF and the register's attached, and
sent by nobody here."""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.contract_emails.schemas import (
    ContractEmailTemplateCreate,
    ContractEmailTemplateUpdate,
)
from pigrocrm.core.contract_emails.service import ContractEmailService
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.emitter.schemas import LegalEntityUpsert
from pigrocrm.core.emitter.service import LegalEntityService
from pigrocrm.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from pigrocrm.core.fiscal.repository import FiscalProfileRepository
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.schemas import InvoiceIssue
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.people.models import Person
from pigrocrm.core.storage.local import LocalFileStorage
from pigrocrm.core.work_units.invoicing import assemble_unbilled_work_units_into_new_invoice
from pigrocrm.core.work_units.models import WorkUnit

ADMIN = Actor(id=None, type="system", role="admin")
READER = Actor(id=None, type="user", role="readonly")
SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]

BODY = (
    "Gentile {{cliente}},\n\nin allegato la fattura {{numero_fattura}} del {{data_fattura}} "
    "di {{importo}} e il registro di {{periodo}} ({{giornate}} giornate).\n\n"
    "{{emittente.ragione_sociale}}"
)


def _setup(db_session: Session) -> Contract:
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
    from pigrocrm.core.emitter.repository import LegalEntityRepository

    azienda = LegalEntityRepository(db_session).default()
    assert azienda is not None
    if FiscalProfileRepository(db_session).get(azienda.id) is None:
        FiscalProfileService(db_session).upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)
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
    db_session.add(
        Person(customer_id=customer.id, nome="Mario", cognome="Verdi", email="mario@acme.example")
    )
    contract = Contract(
        customer_id=customer.id,
        titolo="Consulenza",
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


def _template(db_session: Session, contract: Contract, allegati: list[str] | None = None) -> UUID:
    return (
        ContractEmailService(db_session)
        .create(
            contract.id,
            ContractEmailTemplateCreate(
                nome="Fattura mensile",
                oggetto="Fattura {{numero_fattura}} - {{contratto}}",
                corpo=BODY,
                allegati=allegati if allegati is not None else ["fattura_pdf", "registro_pdf"],  # type: ignore[arg-type]
            ),
            ADMIN,
        )
        .id
    )


def _issued_invoice(db_session: Session, storage: LocalFileStorage, contract: Contract) -> UUID:
    for day in (date(2026, 10, 7), date(2026, 10, 8)):
        db_session.add(
            WorkUnit(
                contract_id=contract.id,
                data=day,
                quantita=Decimal("1.00"),
                descrizione="Giornata",
                stato="lavorato",
            )
        )
    db_session.flush()
    invoice = assemble_unbilled_work_units_into_new_invoice(db_session, storage, contract.id, ADMIN)
    service = InvoiceService(db_session, storage)
    issued = service.issue(invoice.id, InvoiceIssue(), ADMIN)
    service.produce_artifacts(issued.id, ADMIN)
    return issued.id


def test_a_template_is_saved_listed_and_archived(db_session: Session) -> None:
    contract = _setup(db_session)
    service = ContractEmailService(db_session)
    template_id = _template(db_session, contract)

    assert [t.nome for t in service.list_for_contract(contract.id, ADMIN)] == ["Fattura mensile"]
    archived = service.update(
        contract.id, template_id, ContractEmailTemplateUpdate(attivo=False), ADMIN
    )
    assert archived.attivo is False


def test_an_unknown_variable_is_refused_when_the_template_is_saved(db_session: Session) -> None:
    contract = _setup(db_session)
    with pytest.raises(ValidationFailed):
        ContractEmailService(db_session).create(
            contract.id,
            ContractEmailTemplateCreate(
                nome="Rotto", oggetto="Ciao", corpo="Ciao {{nome_inventato}}"
            ),
            ADMIN,
        )


def test_two_templates_of_a_contract_cannot_share_a_name_in_any_case(db_session: Session) -> None:
    contract = _setup(db_session)
    _template(db_session, contract)
    with pytest.raises(Conflict):
        ContractEmailService(db_session).create(
            contract.id,
            ContractEmailTemplateCreate(nome="FATTURA MENSILE", oggetto="x", corpo="y"),
            ADMIN,
        )


def test_a_reader_cannot_save_a_template(db_session: Session) -> None:
    contract = _setup(db_session)
    with pytest.raises(PermissionDenied):
        ContractEmailService(db_session).create(
            contract.id, ContractEmailTemplateCreate(nome="x", oggetto="x", corpo="y"), READER
        )


def test_the_preview_renders_the_invoices_own_figures_and_suggests_the_customers_people(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    contract = _setup(db_session)
    template_id = _template(db_session, contract)
    invoice_id = _issued_invoice(db_session, local_storage, contract)

    preview = ContractEmailService(db_session).preview(contract.id, template_id, invoice_id, ADMIN)

    invoice = InvoiceService(db_session, local_storage).get(invoice_id, ADMIN)
    numero = f"{invoice.anno}/{invoice.numero}"
    assert preview.subject == f"Fattura {numero} - Consulenza"
    assert f"la fattura {numero} del " in preview.body
    assert "07/10/2026 - 08/10/2026 (2.00 giornate)" in preview.body
    assert preview.body.rstrip().endswith("Studio Rossi")
    assert "\\/" not in preview.body  # the plain context, not the markdown one
    assert preview.to_suggeriti == ["mario@acme.example"]
    assert [a.tipo for a in preview.allegati] == ["fattura_pdf", "registro_pdf"]
    assert (preview.da, preview.a) == (date(2026, 10, 7), date(2026, 10, 8))


def test_a_draft_invoice_is_not_sent(db_session: Session, local_storage: LocalFileStorage) -> None:
    contract = _setup(db_session)
    template_id = _template(db_session, contract)
    db_session.add(
        WorkUnit(
            contract_id=contract.id,
            data=date(2026, 10, 7),
            quantita=Decimal("1.00"),
            descrizione="Giornata",
            stato="lavorato",
        )
    )
    db_session.flush()
    draft = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )
    with pytest.raises(ValidationFailed):
        ContractEmailService(db_session).preview(contract.id, template_id, draft.id, ADMIN)


def test_an_invoice_with_no_days_of_this_contract_is_refused(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceLineIn

    contract = _setup(db_session)
    template_id = _template(db_session, contract)
    service = InvoiceService(db_session, local_storage)
    other = service.create(
        InvoiceCreate(
            customer_id=contract.customer_id,
            righe=[
                InvoiceLineIn(
                    descrizione="Altro", quantita=Decimal("1"), prezzo_unitario=Decimal("10")
                )
            ],
        ),
        ADMIN,
    )
    service.issue(other.id, InvoiceIssue(), ADMIN)
    with pytest.raises(ValidationFailed):
        ContractEmailService(db_session).preview(contract.id, template_id, other.id, ADMIN)


def test_prepare_writes_a_draft_with_the_invoice_and_the_register_attached_and_sends_nothing(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    contract = _setup(db_session)
    template_id = _template(db_session, contract)
    invoice_id = _issued_invoice(db_session, local_storage, contract)

    draft = ContractEmailService(db_session).prepare(
        contract.id,
        template_id,
        invoice_id,
        ["mario@acme.example"],
        ADMIN,
        settings=SETTINGS,
        storage=local_storage,
    )

    assert draft.send_state == "bozza"
    assert draft.to_addresses == ["mario@acme.example"]
    assert draft.entity_id == contract.customer_id
    assert len(draft.attachment_version_ids) == 2
    assert "Gentile Acme" in draft.body_markdown


def test_a_template_of_another_contract_is_not_found(db_session: Session) -> None:
    contract = _setup(db_session)
    other = _setup(db_session)
    template_id = _template(db_session, contract)
    with pytest.raises(NotFound):
        ContractEmailService(db_session).update(
            other.id, template_id, ContractEmailTemplateUpdate(nome="x"), ADMIN
        )
