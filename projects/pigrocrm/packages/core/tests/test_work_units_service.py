"""`WorkUnitService`/`ApprovalService`: the actor/motivo wiring spec §5 asks for (a
day's transition log must never be silently attributed to nobody), the friendlier
pre-database validation `WorkUnitCreate`'s own entry-state check gives, and that the
service and a bare SQL write enforce the identical rule -- the other half of the
Done-when's "a direct SQL UPDATE outside the service layer obeys the same rules as
the API".
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.models import Document
from pigrocrm.core.errors import PermissionDenied, ValidationFailed
from pigrocrm.core.work_units.models import Approval
from pigrocrm.core.work_units.schemas import ApprovalCreate, WorkUnitCreate
from pigrocrm.core.work_units.service import (
    ApprovalService,
    WorkUnitService,
    actor_to_transition_json,
)

WRITER = Actor(id=uuid4(), type="user", role="collaboratore")
AGENT = Actor(id=uuid4(), type="mcp", role="collaboratore")
READER = Actor(id=None, type="user", role="readonly")


def _contract(db_session: Session, *, requires_prior_approval: bool = False) -> Contract:
    customer = Customer(ragione_sociale=f"ACME {uuid4()}")
    db_session.add(customer)
    db_session.flush()
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
    db_session.add(contract)
    db_session.flush()
    return contract


def _document(db_session: Session, contract: Contract) -> Document:
    document = Document(contract_id=contract.id, tipo="contratto", titolo="Contratto firmato")
    db_session.add(document)
    db_session.flush()
    return document


def _approval(db_session: Session, contract: Contract) -> Approval:
    return ApprovalService(db_session).create(
        ApprovalCreate(
            contract_id=contract.id,
            canale="email",
            mittente="cliente@example.test",
            ricevuto_il=datetime(2026, 3, 1, 9, 0),
            document_id=_document(db_session, contract).id,
            estratto="Confermiamo le giornate.",
        ),
        WRITER,
    )


# ---- actor_to_transition_json -------------------------------------------------------


def test_a_user_actor_becomes_a_human_kind() -> None:
    actor = Actor(id=uuid4(), type="user", role="admin")
    assert actor_to_transition_json(actor) == f'{{"kind": "human", "id": "{actor.id}"}}'


def test_an_mcp_actor_becomes_an_agent_kind() -> None:
    actor = Actor(id=uuid4(), type="mcp", role="admin")
    assert actor_to_transition_json(actor) == f'{{"kind": "agent", "id": "{actor.id}"}}'


def test_a_system_actor_carries_no_id() -> None:
    assert actor_to_transition_json(Actor.system()) == '{"kind": "system"}'


def test_a_rebase_actor_is_its_own_kind_and_never_an_agent() -> None:
    """rebase writes in a freelancer's space through the engagements door (spec
    2026-09-25 § 2.5): not the freelancer's own agent, so the log must not say one did."""
    assert actor_to_transition_json(Actor.rebase()) == '{"kind": "rebase"}'


# ---- WorkUnitService.create ----------------------------------------------------------


def test_create_refuses_a_non_entry_stato_before_touching_the_database(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    with pytest.raises(ValidationFailed):
        WorkUnitService(db_session).create(
            WorkUnitCreate(
                contract_id=contract.id,
                data=date(2026, 3, 1),
                quantita=Decimal("1.00"),
                descrizione="Giornata",
                stato="fatturato",
            ),
            WRITER,
        )


def test_create_a_legal_entry_state_succeeds(db_session: Session) -> None:
    contract = _contract(db_session)
    work_unit = WorkUnitService(db_session).create(
        WorkUnitCreate(
            contract_id=contract.id,
            data=date(2026, 3, 1),
            quantita=Decimal("1.00"),
            descrizione="Giornata",
            stato="proposto",
        ),
        WRITER,
    )
    assert work_unit.stato == "proposto"


def test_create_reports_the_redirect_not_the_naive_request(db_session: Session) -> None:
    """The read the caller gets back must show what actually happened -- not what
    they asked for -- or the redirect would be invisible to anything but a direct
    SQL read."""
    contract = _contract(db_session, requires_prior_approval=True)
    work_unit = WorkUnitService(db_session).create(
        WorkUnitCreate(
            contract_id=contract.id,
            data=date(2026, 3, 1),
            quantita=Decimal("1.00"),
            descrizione="Giornata",
            stato="lavorato",
        ),
        WRITER,
    )
    assert work_unit.stato == "lavorato_senza_approvazione"


def test_a_reader_cannot_create_a_work_unit(db_session: Session) -> None:
    contract = _contract(db_session)
    with pytest.raises(PermissionDenied):
        WorkUnitService(db_session).create(
            WorkUnitCreate(
                contract_id=contract.id,
                data=date(2026, 3, 1),
                quantita=Decimal("1.00"),
                descrizione="Giornata",
                stato="proposto",
            ),
            READER,
        )


# ---- WorkUnitService.transition / link_approval / transitions_for --------------------


def test_transition_moves_a_day_and_logs_the_supplied_actor_and_motivo(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    service = WorkUnitService(db_session)
    work_unit = service.create(
        WorkUnitCreate(
            contract_id=contract.id,
            data=date(2026, 3, 1),
            quantita=Decimal("1.00"),
            descrizione="Giornata",
            stato="proposto",
        ),
        WRITER,
    )
    updated = service.transition(
        work_unit.id, "approvato", AGENT, "il cliente ha confermato via email"
    )
    assert updated.stato == "approvato"

    transitions = service.transitions_for(work_unit.id)
    assert [t.stato_nuovo for t in transitions] == ["proposto", "approvato"]
    assert transitions[-1].attore == {"kind": "agent", "id": str(AGENT.id)}
    assert transitions[-1].motivo == "il cliente ha confermato via email"


def test_transition_to_an_illegal_target_is_refused(db_session: Session) -> None:
    contract = _contract(db_session)
    service = WorkUnitService(db_session)
    work_unit = service.create(
        WorkUnitCreate(
            contract_id=contract.id,
            data=date(2026, 3, 1),
            quantita=Decimal("1.00"),
            descrizione="Giornata",
            stato="proposto",
        ),
        WRITER,
    )
    with pytest.raises(IntegrityError):  # raised by the trigger
        service.transition(work_unit.id, "pagato", WRITER, "tentativo illegale")


def test_link_approval_recovers_the_flagged_state(db_session: Session) -> None:
    contract = _contract(db_session, requires_prior_approval=True)
    service = WorkUnitService(db_session)
    work_unit = service.create(
        WorkUnitCreate(
            contract_id=contract.id,
            data=date(2026, 3, 1),
            quantita=Decimal("1.00"),
            descrizione="Giornata",
            stato="lavorato",
        ),
        WRITER,
    )
    assert work_unit.stato == "lavorato_senza_approvazione"

    approval = _approval(db_session, contract)
    recovered = service.link_approval(work_unit.id, approval.id, WRITER, "approvazione ricevuta")
    assert recovered.stato == "lavorato"
    assert recovered.approval_id == approval.id


def test_transitions_for_an_unknown_work_unit_is_empty_not_an_error(
    db_session: Session,
) -> None:
    assert WorkUnitService(db_session).transitions_for(uuid4()) == []


# ---- ApprovalService -------------------------------------------------------------------


def test_approval_create_and_get_round_trip(db_session: Session) -> None:
    contract = _contract(db_session)
    approval = _approval(db_session, contract)
    fetched = ApprovalService(db_session).get(approval.id)
    assert fetched.id == approval.id
    assert fetched.canale == "email"
    assert fetched.origine == {"kind": "manuale"}
