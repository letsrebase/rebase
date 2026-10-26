"""`WorkUnitService`/`ApprovalService`: the actor/motivo wiring spec §5 asks for (a
day's transition log must never be silently attributed to nobody), the friendlier
pre-database validation `WorkUnitCreate`'s own entry-state check gives, and that the
service and a bare SQL write enforce the identical rule -- the other half of the
Done-when's "a direct SQL UPDATE outside the service layer obeys the same rules as
the API".
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.models import Document
from pigrocrm.core.errors import (
    AgentForbidden,
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from pigrocrm.core.work_units.models import Approval
from pigrocrm.core.work_units.schemas import ApprovalCreate, WorkUnitCreate, WorkUnitListQuery
from pigrocrm.core.work_units.service import (
    ApprovalService,
    WorkUnitService,
    actor_to_transition_json,
)

WRITER = Actor(id=uuid4(), type="user", role="collaboratore")
AGENT = Actor(id=uuid4(), type="mcp", role="collaboratore")
# The same credential on an installation that opened `mcp_full_access`: the day ledger's
# writes are closed to an agent otherwise (`AGENT_FORBIDDEN_ACTIONS`, REB-745).
TRUSTED_AGENT = Actor(id=uuid4(), type="mcp", role="collaboratore", full_access=True)
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
        work_unit.id, "approvato", TRUSTED_AGENT, "il cliente ha confermato via email"
    )
    assert updated.stato == "approvato"

    transitions = service.transitions_for(work_unit.id)
    assert [t.stato_nuovo for t in transitions] == ["proposto", "approvato"]
    assert transitions[-1].attore == {"kind": "agent", "id": str(TRUSTED_AGENT.id)}
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
    with pytest.raises(Conflict):
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


# ---- the day ledger's writes are closed to an agent credential --------------------------


def test_an_agent_credential_cannot_record_or_move_a_day(db_session: Session) -> None:
    """The four writes are REST-reachable now (REB-745), and a personal access token is
    accepted on every REST route: the service is where the ban has to hold."""
    contract = _contract(db_session)
    service = WorkUnitService(db_session)
    body = WorkUnitCreate(
        contract_id=contract.id,
        data=date(2026, 3, 1),
        quantita=Decimal("1.00"),
        descrizione="Giornata",
        stato="proposto",
    )
    with pytest.raises(AgentForbidden):
        service.create(body, AGENT)
    day = service.create(body, WRITER)
    with pytest.raises(AgentForbidden):
        service.transition(day.id, "approvato", AGENT, "ok")
    approval = _approval(db_session, contract)
    with pytest.raises(AgentForbidden):
        service.link_approval(day.id, approval.id, AGENT, "ok")
    with pytest.raises(AgentForbidden):
        ApprovalService(db_session).create(
            ApprovalCreate(
                contract_id=contract.id,
                canale="email",
                mittente="cliente@example.com",
                ricevuto_il=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
                document_id=approval.document_id,
                estratto="ok",
            ),
            AGENT,
        )


# ---- WorkUnitService.create: the two refusals a screen can hit -------------------------


def _day(contract: Contract, giorno: date, stato: str = "lavorato") -> WorkUnitCreate:
    return WorkUnitCreate(
        contract_id=contract.id,
        data=giorno,
        quantita=Decimal("0.50"),
        descrizione="Revisione",
        stato=stato,  # type: ignore[arg-type]
    )


def test_a_second_live_day_on_the_same_date_is_a_conflict(db_session: Session) -> None:
    contract = _contract(db_session)
    service = WorkUnitService(db_session)
    service.create(_day(contract, date(2026, 3, 2)), WRITER)

    with pytest.raises(Conflict, match="gia' una giornata"):
        service.create(_day(contract, date(2026, 3, 2)), WRITER)


def test_a_revoked_day_frees_its_date(db_session: Session) -> None:
    contract = _contract(db_session)
    service = WorkUnitService(db_session)
    first = service.create(_day(contract, date(2026, 3, 2)), WRITER)
    service.transition(first.id, "revocato", WRITER, "registrata per errore")

    second = service.create(_day(contract, date(2026, 3, 2)), WRITER)

    assert second.id != first.id


def test_a_day_on_a_contract_that_does_not_exist_is_not_found(db_session: Session) -> None:
    contract = _contract(db_session)
    body = _day(contract, date(2026, 3, 2)).model_copy(update={"contract_id": uuid4()})
    with pytest.raises(NotFound):
        WorkUnitService(db_session).create(body, WRITER)


# ---- WorkUnitService.detail / list -----------------------------------------------------


def test_detail_carries_the_log_the_contract_and_the_moves_a_person_may_make(
    db_session: Session,
) -> None:
    contract = _contract(db_session, requires_prior_approval=True)
    service = WorkUnitService(db_session)
    day = service.create(_day(contract, date(2026, 3, 2)), WRITER)

    detail = service.detail(day.id, WRITER)

    assert detail.stato == "lavorato_senza_approvazione"
    assert detail.contract_titolo == "Consulenza"
    assert detail.customer_nome.startswith("ACME")
    assert detail.requires_prior_approval is True
    assert detail.approval is None
    assert [t.stato_nuovo for t in detail.transizioni] == ["lavorato_senza_approvazione"]
    # The edge back to `lavorato` is the recovery: a person gets it by linking an approval
    # (the trigger would rewrite a plain move straight back), and `fatturato` is what an
    # invoice does, so neither is a plain button.
    assert detail.azioni == ["contestato", "revocato", "non_fatturabile"]


def test_detail_names_the_approval_and_its_document(db_session: Session) -> None:
    contract = _contract(db_session, requires_prior_approval=True)
    service = WorkUnitService(db_session)
    day = service.create(_day(contract, date(2026, 3, 2)), WRITER)
    approval = _approval(db_session, contract)
    service.link_approval(day.id, approval.id, WRITER, "approvazione ricevuta")

    detail = service.detail(day.id, WRITER)

    assert detail.stato == "lavorato"
    assert detail.approval is not None
    assert detail.approval.id == approval.id
    assert detail.approval.documento_titolo == "Contratto firmato"
    assert [t.stato_nuovo for t in detail.transizioni] == [
        "lavorato_senza_approvazione",
        "lavorato",
    ]


def test_detail_says_when_a_day_falls_outside_the_contracts_term(db_session: Session) -> None:
    contract = _contract(db_session)  # starts 2026-01-01, no end
    service = WorkUnitService(db_session)
    early = service.create(_day(contract, date(2025, 12, 31)), WRITER)
    ok = service.create(_day(contract, date(2026, 3, 2)), WRITER)

    assert service.detail(early.id, WRITER).fuori_termine == "prima_dell_inizio"
    assert service.detail(ok.id, WRITER).fuori_termine is None


def test_a_terminal_day_offers_no_move(db_session: Session) -> None:
    contract = _contract(db_session)
    service = WorkUnitService(db_session)
    day = service.create(_day(contract, date(2026, 3, 2), "proposto"), WRITER)
    service.transition(day.id, "rifiutato", WRITER, "il cliente non ha confermato")

    assert service.detail(day.id, WRITER).azioni == []


def test_detail_of_an_unknown_day_is_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        WorkUnitService(db_session).detail(uuid4(), WRITER)


def test_list_is_newest_first_filtered_and_says_when_it_cut(db_session: Session) -> None:
    contract = _contract(db_session)
    other = _contract(db_session)
    service = WorkUnitService(db_session)
    for giorno in (date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)):
        service.create(_day(contract, giorno), WRITER)
    service.create(_day(other, date(2026, 3, 3)), WRITER)

    everything = service.list(WorkUnitListQuery(contract_id=contract.id), WRITER)
    assert [row.data for row in everything.items] == [
        date(2026, 3, 4),
        date(2026, 3, 3),
        date(2026, 3, 2),
    ]
    assert everything.troncato is False
    assert {row.contract_titolo for row in everything.items} == {"Consulenza"}

    window = service.list(WorkUnitListQuery(da=date(2026, 3, 3), a=date(2026, 3, 3)), WRITER)
    assert {row.contract_id for row in window.items} == {contract.id, other.id}

    cut = service.list(WorkUnitListQuery(contract_id=contract.id, limit=2), WRITER)
    assert len(cut.items) == 2
    assert cut.troncato is True

    flagged = service.list(WorkUnitListQuery(contract_id=contract.id, stato="revocato"), WRITER)
    assert flagged.items == []


def test_list_refuses_an_unbounded_or_inverted_query(db_session: Session) -> None:
    service = WorkUnitService(db_session)
    with pytest.raises(ValidationFailed):
        service.list(WorkUnitListQuery(), WRITER)
    with pytest.raises(ValidationFailed):
        service.list(WorkUnitListQuery(da=date(2026, 3, 5), a=date(2026, 3, 4)), WRITER)
