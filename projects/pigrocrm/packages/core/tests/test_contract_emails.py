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
    AttachmentKind,
    ContractEmailTemplateCreate,
    ContractEmailTemplateUpdate,
)
from pigrocrm.core.contract_emails.service import ContractEmailService
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.errors import (
    AgentForbidden,
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from pigrocrm.core.fiscal.repository import FiscalProfileRepository
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.schemas import InvoiceIssue
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
from pigrocrm.core.legal_entities.service import LegalEntityService
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
            legal_name="Studio Rossi",
            vat_number="01234567890",
            tax_code="HMCRFT00A01H501K",
            address="Via Vittorio Veneto 12",
            cap="20124",
            city="Milano",
            province="MI",
            country="IT",
            email="mario@example.com",
        ),
        ADMIN,
    )
    from pigrocrm.core.legal_entities.repository import LegalEntityRepository

    legal_entity = LegalEntityRepository(db_session).default()
    assert legal_entity is not None
    if FiscalProfileRepository(db_session).get(legal_entity.id) is None:
        FiscalProfileService(db_session).upsert(FiscalProfileUpsert(regime_code="RF19"), ADMIN)
    customer = Customer(
        legal_name=f"Acme {uuid4()} S.r.l.",
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
    db_session.add(
        Person(customer_id=customer.id, name="Mario", last_name="Verdi", email="mario@acme.example")
    )
    contract = Contract(
        customer_id=customer.id,
        title="Consulenza",
        start=date(2026, 1, 1),
        renewal_type="none",
        termination_notice_days=30,
        billing_cadence="monthly",
        expense_policy={"kind": "not_reimbursable"},
    )
    db_session.add(contract)
    db_session.flush()
    db_session.add(
        RateCard(
            contract_id=contract.id,
            valid_from=date(2026, 1, 1),
            type="daily",
            amount=Decimal("500.00"),
            unit="day",
        )
    )
    db_session.flush()
    return contract


def _template(
    db_session: Session, contract: Contract, attachments: list[AttachmentKind] | None = None
) -> UUID:
    return (
        ContractEmailService(db_session)
        .create(
            contract.id,
            ContractEmailTemplateCreate(
                name="Fattura mensile",
                subject="Fattura {{numero_fattura}} - {{contratto}}",
                body=BODY,
                attachments=attachments
                if attachments is not None
                else ["invoice_pdf", "register_pdf"],
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
                date=day,
                quantity=Decimal("1.00"),
                description="Giornata",
                status="worked",
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

    assert [t.name for t in service.list_for_contract(contract.id, ADMIN)] == ["Fattura mensile"]
    archived = service.update(
        contract.id, template_id, ContractEmailTemplateUpdate(active=False), ADMIN
    )
    assert archived.active is False


def test_an_unknown_variable_is_refused_when_the_template_is_saved(db_session: Session) -> None:
    contract = _setup(db_session)
    with pytest.raises(ValidationFailed):
        ContractEmailService(db_session).create(
            contract.id,
            ContractEmailTemplateCreate(
                name="Rotto", subject="Ciao", body="Ciao {{nome_inventato}}"
            ),
            ADMIN,
        )


def test_two_templates_of_a_contract_cannot_share_a_name_in_any_case(db_session: Session) -> None:
    contract = _setup(db_session)
    _template(db_session, contract)
    with pytest.raises(Conflict):
        ContractEmailService(db_session).create(
            contract.id,
            ContractEmailTemplateCreate(name="FATTURA MENSILE", subject="x", body="y"),
            ADMIN,
        )


def test_a_reader_cannot_save_a_template(db_session: Session) -> None:
    contract = _setup(db_session)
    with pytest.raises(PermissionDenied):
        ContractEmailService(db_session).create(
            contract.id, ContractEmailTemplateCreate(name="x", subject="x", body="y"), READER
        )


def test_the_preview_renders_the_invoices_own_figures_and_suggests_the_customers_people(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    contract = _setup(db_session)
    template_id = _template(db_session, contract)
    invoice_id = _issued_invoice(db_session, local_storage, contract)

    preview = ContractEmailService(db_session).preview(contract.id, template_id, invoice_id, ADMIN)

    invoice = InvoiceService(db_session, local_storage).get(invoice_id, ADMIN)
    number = f"{invoice.year}/{invoice.number}"
    assert preview.subject == f"Fattura {number} - Consulenza"
    assert f"la fattura {number} del " in preview.body
    assert "07/10/2026 - 08/10/2026 (2.00 giornate)" in preview.body
    assert preview.body.rstrip().endswith("Studio Rossi")
    assert "\\/" not in preview.body  # the plain context, not the markdown one
    assert preview.to_suggested == ["mario@acme.example"]
    assert [date_to.type for date_to in preview.attachments] == ["invoice_pdf", "register_pdf"]
    assert (preview.date_from, preview.date_to) == (date(2026, 10, 7), date(2026, 10, 8))


def test_a_draft_invoice_is_not_sent(db_session: Session, local_storage: LocalFileStorage) -> None:
    contract = _setup(db_session)
    template_id = _template(db_session, contract)
    db_session.add(
        WorkUnit(
            contract_id=contract.id,
            date=date(2026, 10, 7),
            quantity=Decimal("1.00"),
            description="Giornata",
            status="worked",
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
            lines=[
                InvoiceLineIn(description="Altro", quantity=Decimal("1"), unit_price=Decimal("10"))
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

    assert draft.send_state == "draft"
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
            other.id, template_id, ContractEmailTemplateUpdate(name="x"), ADMIN
        )


def test_an_agent_cannot_write_a_template_or_prepare_a_draft(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    contract = _setup(db_session)
    template_id = _template(db_session, contract)
    agent = Actor(id=None, type="mcp", role="admin")
    service = ContractEmailService(db_session)

    with pytest.raises(AgentForbidden):
        service.create(
            contract.id, ContractEmailTemplateCreate(name="x", subject="x", body="y"), agent
        )
    with pytest.raises(AgentForbidden):
        service.update(contract.id, template_id, ContractEmailTemplateUpdate(name="z"), agent)
    with pytest.raises(AgentForbidden):
        service.prepare(
            contract.id,
            template_id,
            uuid4(),
            ["a@example.test"],
            agent,
            settings=SETTINGS,
            storage=local_storage,
        )
    # The reads stay open to it.
    assert len(service.list_for_contract(contract.id, agent)) == 1
