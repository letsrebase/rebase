"""`ApprovalService.record` (REB-747): a person records a client's approval and the days it
covers, in one transaction.

What is proved here is the part a screen cannot see: that a proposed day becomes approved
and a day worked without approval is recovered, that a day that cannot use an approval
refuses the whole recording and leaves nothing behind, that a synced email is filed as a
document of the contract with its provenance, and that an agent credential is refused.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fakes.gmail_fixtures import MAILBOX, connected_account
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.models import Document, DocumentVersion
from pigrocrm.core.errors import (
    AgentForbidden,
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from pigrocrm.core.gmail.models import GmailMessage, GmailMessageLink
from pigrocrm.core.storage.local import LocalFileStorage
from pigrocrm.core.work_units.models import Approval
from pigrocrm.core.work_units.schemas import ApprovalRecord, WorkUnitCreate
from pigrocrm.core.work_units.service import ApprovalService, WorkUnitService

WRITER = Actor(id=uuid4(), type="user", role="collaboratore")
AGENT = Actor(id=uuid4(), type="mcp", role="admin")
READER = Actor(id=None, type="user", role="readonly")


def _contract(session: Session, *, requires_prior_approval: bool = True) -> Contract:
    customer = Customer(ragione_sociale=f"ACME {uuid4()}")
    session.add(customer)
    session.flush()
    contract = Contract(
        customer_id=customer.id,
        titolo="Consulenza",
        inizio=date(2026, 1, 1),
        tipo_rinnovo="nessuno",
        preavviso_disdetta_giorni=30,
        cadenza_fatturazione="mensile",
        politica_spese={"kind": "non_rimborsabile"},
        requires_prior_approval=requires_prior_approval,
    )
    session.add(contract)
    session.flush()
    return contract


def _day(session: Session, contract: Contract, giorno: date, stato: str) -> UUID:
    return (
        WorkUnitService(session)
        .create(
            WorkUnitCreate(
                contract_id=contract.id,
                data=giorno,
                quantita=Decimal("1.00"),
                descrizione="Giornata",
                stato=stato,  # type: ignore[arg-type]
            ),
            WRITER,
        )
        .id
    )


def _document(session: Session, contract: Contract, *, filed: bool = True) -> Document:
    """A document of the contract; `filed` gives it the one version an archived original has."""
    document = Document(
        contract_id=contract.id,
        tipo="documento",
        titolo="Mail di conferma",
        versione_corrente=1 if filed else 0,
    )
    session.add(document)
    session.flush()
    if filed:
        session.add(
            DocumentVersion(
                document_id=document.id,
                numero=1,
                storage_key=f"test/{document.id}",
                content_type="text/plain",
                dimensione=1,
                hash_sha256="0" * 64,
            )
        )
        session.flush()
    return document


def _record(contract: Contract, days: list[UUID], **extra: object) -> ApprovalRecord:
    return ApprovalRecord(
        contract_id=contract.id,
        canale="email",
        mittente="giulia@cliente.example",
        ricevuto_il=datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
        estratto="Confermo le giornate.",
        work_unit_ids=days,
        **extra,  # type: ignore[arg-type]
    )


def test_a_proposed_day_becomes_approved_and_a_flagged_one_is_recovered(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    proposed = _day(db_session, contract, date(2026, 3, 2), "proposto")
    flagged = _day(db_session, contract, date(2026, 3, 3), "lavorato")
    assert WorkUnitService(db_session).get(flagged).stato == "lavorato_senza_approvazione"
    document = _document(db_session, contract)

    recorded = ApprovalService(db_session).record(
        _record(contract, [proposed, flagged], document_id=document.id), WRITER
    )

    assert {day.id: day.stato for day in recorded.work_units} == {
        proposed: "approvato",
        flagged: "lavorato",
    }
    assert all(day.approval_id == recorded.approval.id for day in recorded.work_units)
    assert recorded.approval.documento_titolo == "Mail di conferma"
    assert recorded.approval.origine == {"kind": "manuale"}
    # The log says who moved them and that an approval was recorded; the sender is on the approval.
    steps = WorkUnitService(db_session).transitions_for(proposed)
    assert steps[-1].stato_nuovo == "approvato"
    assert steps[-1].attore == {"kind": "human", "id": str(WRITER.id)}
    assert steps[-1].motivo == "Approvazione registrata"


def test_one_day_that_cannot_use_the_approval_refuses_all_and_writes_nothing(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    fine = _day(db_session, contract, date(2026, 3, 2), "proposto")
    worked = _day(db_session, contract, date(2026, 3, 3), "approvato")
    document = _document(db_session, contract)

    with pytest.raises(Conflict, match="2026-03-03 \\(approvato\\)"):
        ApprovalService(db_session).record(
            _record(contract, [fine, worked], document_id=document.id), WRITER
        )

    assert WorkUnitService(db_session).get(fine).stato == "proposto"
    assert db_session.scalars(select(Approval)).all() == []


def test_a_day_of_another_contract_is_not_found(db_session: Session) -> None:
    contract = _contract(db_session)
    other = _contract(db_session)
    foreign = _day(db_session, other, date(2026, 3, 2), "proposto")
    document = _document(db_session, contract)

    with pytest.raises(NotFound):
        ApprovalService(db_session).record(
            _record(contract, [foreign], document_id=document.id), WRITER
        )


def test_a_document_with_no_archived_file_cannot_be_the_proof(db_session: Session) -> None:
    contract = _contract(db_session)
    day = _day(db_session, contract, date(2026, 3, 2), "proposto")
    empty = _document(db_session, contract, filed=False)

    with pytest.raises(ValidationFailed, match="non ha ancora un file"):
        ApprovalService(db_session).record(_record(contract, [day], document_id=empty.id), WRITER)

    assert WorkUnitService(db_session).get(day).stato == "proposto"
    assert db_session.scalars(select(Approval)).all() == []


def test_a_document_of_another_customer_is_refused(db_session: Session) -> None:
    contract = _contract(db_session)
    other = _contract(db_session)
    day = _day(db_session, contract, date(2026, 3, 2), "proposto")

    with pytest.raises(ValidationFailed, match="ne' del suo cliente"):
        ApprovalService(db_session).record(
            _record(contract, [day], document_id=_document(db_session, other).id), WRITER
        )


def test_the_proof_is_exactly_one_of_a_document_and_a_message() -> None:
    contract_id = uuid4()
    base = {
        "contract_id": contract_id,
        "canale": "email",
        "mittente": "a@b.example",
        "ricevuto_il": datetime(2026, 3, 1, tzinfo=UTC),
        "estratto": "ok",
        "work_unit_ids": [uuid4()],
    }
    with pytest.raises(ValueError, match="una sola prova"):
        ApprovalRecord(**base)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="una sola prova"):
        ApprovalRecord(**base, document_id=uuid4(), gmail_message_id=uuid4())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="due volte"):
        day = uuid4()
        ApprovalRecord(**{**base, "work_unit_ids": [day, day]}, document_id=uuid4())  # type: ignore[arg-type]


def test_a_synced_email_is_filed_as_a_document_of_the_contract(
    db_session: Session, tmp_path
) -> None:
    contract = _contract(db_session)
    day = _day(db_session, contract, date(2026, 3, 2), "proposto")
    account = connected_account(db_session)
    message = GmailMessage(
        google_account_id=account.id,
        gmail_message_id="g1",
        gmail_thread_id="t1",
        message_id_header="<abc@cliente.example>",
        direction="inbound",
        from_address="giulia@cliente.example",
        to_addresses=[MAILBOX],
        cc_addresses=[],
        subject="Re: giornate di marzo",
        snippet="",
        internal_date=datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
        body_text="Buongiorno, confermo la giornata del 2 marzo.",
        attachments=[],
    )
    db_session.add(message)
    db_session.flush()
    db_session.add(
        GmailMessageLink(
            gmail_message_id=message.id, entity_type="customer", entity_id=contract.customer_id
        )
    )
    db_session.flush()
    storage = LocalFileStorage(str(tmp_path / "documents"))
    # A real user: the filed version records who created it.
    person = Actor(id=account.user_id, type="user", role="collaboratore")

    recorded = ApprovalService(db_session, storage).record(
        _record(contract, [day], gmail_message_id=message.id), person
    )

    approval = db_session.get(Approval, recorded.approval.id)
    assert approval is not None
    assert approval.message_id == "<abc@cliente.example>"
    document = db_session.get(Document, approval.document_id)
    assert document is not None
    assert document.contract_id == contract.id
    assert document.titolo == "Email di giulia@cliente.example: Re: giornate di marzo"
    assert recorded.work_units[0].stato == "approvato"
    # The log keeps the fact, not the sender's address: that is on the approval.
    assert WorkUnitService(db_session).transitions_for(day)[-1].motivo == "Approvazione registrata"


def test_an_email_of_another_customer_cannot_be_the_proof(db_session: Session, tmp_path) -> None:
    contract = _contract(db_session)
    other = _contract(db_session)
    day = _day(db_session, contract, date(2026, 3, 2), "proposto")
    account = connected_account(db_session)
    message = GmailMessage(
        google_account_id=account.id,
        gmail_message_id="g3",
        gmail_thread_id="t3",
        direction="inbound",
        from_address="altro@cliente.example",
        to_addresses=[MAILBOX],
        cc_addresses=[],
        subject="Altro cliente",
        snippet="",
        internal_date=datetime(2026, 3, 1, tzinfo=UTC),
        body_text="Confermo.",
        attachments=[],
    )
    db_session.add(message)
    db_session.flush()
    db_session.add(
        GmailMessageLink(
            gmail_message_id=message.id, entity_type="customer", entity_id=other.customer_id
        )
    )
    db_session.flush()

    with pytest.raises(ValidationFailed, match="tra quelli di questo cliente"):
        ApprovalService(db_session, LocalFileStorage(str(tmp_path / "d"))).record(
            _record(contract, [day], gmail_message_id=message.id), WRITER
        )


def test_an_email_whose_body_was_not_stored_cannot_be_the_proof(
    db_session: Session, tmp_path
) -> None:
    contract = _contract(db_session)
    day = _day(db_session, contract, date(2026, 3, 2), "proposto")
    account = connected_account(db_session)
    message = GmailMessage(
        google_account_id=account.id,
        gmail_message_id="g2",
        gmail_thread_id="t2",
        direction="inbound",
        from_address="giulia@cliente.example",
        to_addresses=[MAILBOX],
        cc_addresses=[],
        subject="Re",
        snippet="",
        internal_date=datetime(2026, 3, 1, tzinfo=UTC),
        body_text="",
        attachments=[],
    )
    db_session.add(message)
    db_session.flush()
    db_session.add(
        GmailMessageLink(
            gmail_message_id=message.id, entity_type="customer", entity_id=contract.customer_id
        )
    )
    db_session.flush()

    with pytest.raises(ValidationFailed, match="non e' archiviato"):
        ApprovalService(db_session, LocalFileStorage(str(tmp_path / "d"))).record(
            _record(contract, [day], gmail_message_id=message.id), WRITER
        )


def test_an_agent_credential_and_a_reader_cannot_record_an_approval(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    day = _day(db_session, contract, date(2026, 3, 2), "proposto")
    document = _document(db_session, contract)
    body = _record(contract, [day], document_id=document.id)

    with pytest.raises(AgentForbidden):
        ApprovalService(db_session).record(body, AGENT)
    with pytest.raises(PermissionDenied):
        ApprovalService(db_session).record(body, READER)
