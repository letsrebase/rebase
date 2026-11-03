"""`WorkUnitService`/`ApprovalService`: the actor/motivo wiring spec §5 asks for (a
day's transition log must never be silently attributed to nobody), the friendlier
pre-database validation `WorkUnitCreate`'s own entry-state check gives, and that the
service and a bare SQL write enforce the identical rule -- the other half of the
Done-when's "a direct SQL UPDATE outside the service layer obeys the same rules as
the API".
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
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
from pigrocrm.core.work_units.models import Approval, WorkUnit
from pigrocrm.core.work_units.schemas import ApprovalCreate, WorkUnitCreate, WorkUnitListQuery
from pigrocrm.core.work_units.service import (
    ApprovalService,
    WorkUnitService,
    actor_to_transition_json,
    set_transition_context,
)

WRITER = Actor(id=uuid4(), type="user", role="collaborator")
AGENT = Actor(id=uuid4(), type="mcp", role="collaborator")
# The same credential on an installation that opened `mcp_full_access`: the day ledger's
# writes are closed to an agent otherwise (`FULL_ACCESS_ACTIONS`, REB-745).
TRUSTED_AGENT = Actor(id=uuid4(), type="mcp", role="collaborator", full_access=True)
READER = Actor(id=None, type="user", role="readonly")


def _contract(db_session: Session, *, requires_prior_approval: bool = False) -> Contract:
    customer = Customer(legal_name=f"ACME {uuid4()}")
    db_session.add(customer)
    db_session.flush()
    contract = Contract(
        customer_id=customer.id,
        title="Consulenza",
        start=date(2026, 1, 1),
        renewal_type="none",
        termination_notice_days=30,
        billing_cadence="monthly",
        expense_policy={"kind": "not_reimbursable"},
        requires_prior_approval=requires_prior_approval,
    )
    db_session.add(contract)
    db_session.flush()
    return contract


def _document(db_session: Session, contract: Contract) -> Document:
    document = Document(contract_id=contract.id, type="contract", title="Contratto firmato")
    db_session.add(document)
    db_session.flush()
    return document


def _approval(db_session: Session, contract: Contract) -> Approval:
    return ApprovalService(db_session).create(
        ApprovalCreate(
            contract_id=contract.id,
            channel="email",
            sender="cliente@example.test",
            received_at=datetime(2026, 3, 1, 9, 0),
            document_id=_document(db_session, contract).id,
            excerpt="Confermiamo le giornate.",
        ),
        WRITER,
    )


def _link_approval(db_session: Session, work_unit_id: UUID, approval_id: UUID) -> None:
    """What recording an approval does to a day, minus the proof and the timeline entry:
    link it, and let the trigger recover a day worked without one."""
    set_transition_context(db_session, WRITER, "approvazione ricevuta")
    db_session.execute(
        update(WorkUnit).where(WorkUnit.id == work_unit_id).values(approval_id=approval_id)
    )
    db_session.flush()
    db_session.expire_all()


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


def test_create_refuses_to_not_entry_status_before_touching_the_database(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    with pytest.raises(ValidationFailed):
        WorkUnitService(db_session).create(
            WorkUnitCreate(
                contract_id=contract.id,
                data=date(2026, 3, 1),
                quantity=Decimal("1.00"),
                description="Giornata",
                status="invoiced",
            ),
            WRITER,
        )


def test_create_a_legal_entry_state_succeeds(db_session: Session) -> None:
    contract = _contract(db_session)
    work_unit = WorkUnitService(db_session).create(
        WorkUnitCreate(
            contract_id=contract.id,
            data=date(2026, 3, 1),
            quantity=Decimal("1.00"),
            description="Giornata",
            status="proposed",
        ),
        WRITER,
    )
    assert work_unit.status == "proposed"


def test_create_reports_the_redirect_not_the_naive_request(db_session: Session) -> None:
    """The read the caller gets back must show what actually happened -- not what
    they asked for -- or the redirect would be invisible to anything but a direct
    SQL read."""
    contract = _contract(db_session, requires_prior_approval=True)
    work_unit = WorkUnitService(db_session).create(
        WorkUnitCreate(
            contract_id=contract.id,
            data=date(2026, 3, 1),
            quantity=Decimal("1.00"),
            description="Giornata",
            status="worked",
        ),
        WRITER,
    )
    assert work_unit.status == "worked_without_approval"


def test_a_reader_cannot_create_a_work_unit(db_session: Session) -> None:
    contract = _contract(db_session)
    with pytest.raises(PermissionDenied):
        WorkUnitService(db_session).create(
            WorkUnitCreate(
                contract_id=contract.id,
                data=date(2026, 3, 1),
                quantity=Decimal("1.00"),
                description="Giornata",
                status="proposed",
            ),
            READER,
        )


# ---- WorkUnitService.transition / transitions_for -----------------------------------


def test_transition_moves_to_day_and_logs_the_supplied_actor_and_reason(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    service = WorkUnitService(db_session)
    work_unit = service.create(
        WorkUnitCreate(
            contract_id=contract.id,
            data=date(2026, 3, 1),
            quantity=Decimal("1.00"),
            description="Giornata",
            status="proposed",
        ),
        WRITER,
    )
    updated = service.transition(
        work_unit.id, "approved", TRUSTED_AGENT, "il cliente ha confermato via email"
    )
    assert updated.status == "approved"

    transitions = service.transitions_for(work_unit.id)
    assert [t.new_status for t in transitions] == ["proposed", "approved"]
    assert transitions[-1].actor == {"kind": "agent", "id": str(TRUSTED_AGENT.id)}
    assert transitions[-1].reason == "il cliente ha confermato via email"


def test_transition_to_an_illegal_target_is_refused(db_session: Session) -> None:
    contract = _contract(db_session)
    service = WorkUnitService(db_session)
    work_unit = service.create(
        WorkUnitCreate(
            contract_id=contract.id,
            data=date(2026, 3, 1),
            quantity=Decimal("1.00"),
            description="Giornata",
            status="proposed",
        ),
        WRITER,
    )
    with pytest.raises(Conflict):
        service.transition(work_unit.id, "paid", WRITER, "tentativo illegale")


def test_days_and_approvals_name_the_member_and_the_channel_on_the_contract(
    db_session: Session,
) -> None:
    """REB-781: the day's own ledger holds the actor as an id and `human` or `agent`; the
    contract's timeline holds the member and the channel, and the approval row, which has no
    actor column, is recorded nowhere else."""
    contract = _contract(db_session, requires_prior_approval=True)
    service = WorkUnitService(db_session)
    day = service.create(
        WorkUnitCreate(
            contract_id=contract.id,
            data=date(2026, 3, 1),
            quantity=Decimal("1.00"),
            description="Giornata",
            status="worked",
        ),
        WRITER,
    )
    _approval(db_session, contract)
    service.transition(day.id, "disputed", TRUSTED_AGENT, "contestata dal cliente")

    entries = {e.kind: e for e in ActivityService(db_session).timeline("contract", contract.id)}

    assert (entries["day_created"].actor_id, entries["day_created"].actor_type) == (
        WRITER.id,
        "user",
    )
    assert entries["day_created"].payload == {
        "work_unit": "2026-03-01",
        "status": "worked_without_approval",
    }
    assert entries["approval_added"].actor_type == "user"
    assert entries["day_state_changed"].actor_type == "mcp"
    assert entries["day_state_changed"].payload == {
        "work_unit": "2026-03-01",
        "da": "worked_without_approval",
        "a": "disputed",
    }


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
    assert fetched.channel == "email"
    assert fetched.origin == {"kind": "manual"}


# ---- the day ledger's writes are closed to an agent credential --------------------------


def test_an_agent_credential_cannot_record_or_move_a_day(db_session: Session) -> None:
    """The four writes are REST-reachable now (REB-745), and a personal access token is
    accepted on every REST route: the service is where the ban has to hold."""
    contract = _contract(db_session)
    service = WorkUnitService(db_session)
    body = WorkUnitCreate(
        contract_id=contract.id,
        data=date(2026, 3, 1),
        quantity=Decimal("1.00"),
        description="Giornata",
        status="proposed",
    )
    with pytest.raises(AgentForbidden):
        service.create(body, AGENT)
    day = service.create(body, WRITER)
    with pytest.raises(AgentForbidden):
        service.transition(day.id, "approved", AGENT, "ok")
    approval = _approval(db_session, contract)
    with pytest.raises(AgentForbidden):
        ApprovalService(db_session).create(
            ApprovalCreate(
                contract_id=contract.id,
                channel="email",
                sender="cliente@example.com",
                received_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
                document_id=approval.document_id,
                excerpt="ok",
            ),
            AGENT,
        )


# ---- WorkUnitService.create: the two refusals a screen can hit -------------------------


def _day(contract: Contract, day: date, status: str = "worked") -> WorkUnitCreate:
    return WorkUnitCreate(
        contract_id=contract.id,
        data=day,
        quantity=Decimal("0.50"),
        description="Revisione",
        status=status,  # type: ignore[arg-type]
    )


def test_a_second_live_day_on_the_same_date_is_a_conflict(db_session: Session) -> None:
    contract = _contract(db_session)
    service = WorkUnitService(db_session)
    service.create(_day(contract, date(2026, 3, 2)), WRITER)

    with pytest.raises(Conflict) as refused:
        service.create(_day(contract, date(2026, 3, 2)), WRITER)
    assert refused.value.details["data"] == "2026-03-02"


def test_a_revoked_day_frees_its_date(db_session: Session) -> None:
    contract = _contract(db_session)
    service = WorkUnitService(db_session)
    first = service.create(_day(contract, date(2026, 3, 2)), WRITER)
    service.transition(first.id, "revoked", WRITER, "registrata per errore")

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

    assert detail.status == "worked_without_approval"
    assert detail.contract_title == "Consulenza"
    assert detail.customer_name.startswith("ACME")
    assert detail.requires_prior_approval is True
    assert detail.approval is None
    assert [t.new_status for t in detail.transitions] == ["worked_without_approval"]
    # The edge back to `worked` is the recovery: a person gets it by linking an approval
    # (the trigger would rewrite a plain move straight back), and `invoiced` is what an
    # invoice does, so neither is a plain button.
    assert detail.actions == ["disputed", "revoked", "non_billable"]


def test_detail_names_the_approval_and_its_document(db_session: Session) -> None:
    contract = _contract(db_session, requires_prior_approval=True)
    service = WorkUnitService(db_session)
    day = service.create(_day(contract, date(2026, 3, 2)), WRITER)
    approval = _approval(db_session, contract)
    _link_approval(db_session, day.id, approval.id)

    detail = service.detail(day.id, WRITER)

    assert detail.status == "worked"
    assert detail.approval is not None
    assert detail.approval.id == approval.id
    assert detail.approval.title_document == "Contratto firmato"
    assert [t.new_status for t in detail.transitions] == [
        "worked_without_approval",
        "worked",
    ]


def test_detail_says_when_a_day_falls_outside_the_contracts_term(db_session: Session) -> None:
    contract = _contract(db_session)  # starts 2026-01-01, no end
    service = WorkUnitService(db_session)
    early = service.create(_day(contract, date(2025, 12, 31)), WRITER)
    ok = service.create(_day(contract, date(2026, 3, 2)), WRITER)

    assert service.detail(early.id, WRITER).out_of_term == "before_start"
    assert service.detail(ok.id, WRITER).out_of_term is None


def test_a_terminal_day_offers_no_move(db_session: Session) -> None:
    contract = _contract(db_session)
    service = WorkUnitService(db_session)
    day = service.create(_day(contract, date(2026, 3, 2), "proposed"), WRITER)
    service.transition(day.id, "rejected", WRITER, "il cliente non ha confermato")

    assert service.detail(day.id, WRITER).actions == []


def test_detail_of_an_unknown_day_is_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        WorkUnitService(db_session).detail(uuid4(), WRITER)


def test_list_is_newest_first_filtered_and_says_when_it_cut(db_session: Session) -> None:
    contract = _contract(db_session)
    other = _contract(db_session)
    service = WorkUnitService(db_session)
    for day in (date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)):
        service.create(_day(contract, day), WRITER)
    service.create(_day(other, date(2026, 3, 3)), WRITER)

    everything = service.list(WorkUnitListQuery(contract_id=contract.id), WRITER)
    assert [row.data for row in everything.items] == [
        date(2026, 3, 4),
        date(2026, 3, 3),
        date(2026, 3, 2),
    ]
    assert everything.truncated is False
    assert {row.contract_title for row in everything.items} == {"Consulenza"}

    window = service.list(WorkUnitListQuery(da=date(2026, 3, 3), a=date(2026, 3, 3)), WRITER)
    assert {row.contract_id for row in window.items} == {contract.id, other.id}

    cut = service.list(WorkUnitListQuery(contract_id=contract.id, limit=2), WRITER)
    assert len(cut.items) == 2
    assert cut.truncated is True

    flagged = service.list(WorkUnitListQuery(contract_id=contract.id, status="revoked"), WRITER)
    assert flagged.items == []


def test_list_refuses_an_unbounded_or_inverted_query(db_session: Session) -> None:
    service = WorkUnitService(db_session)
    with pytest.raises(ValidationFailed):
        service.list(WorkUnitListQuery(), WRITER)
    with pytest.raises(ValidationFailed):
        service.list(WorkUnitListQuery(da=date(2026, 3, 5), a=date(2026, 3, 4)), WRITER)


# ---- WorkUnitService.move: the person's door --------------------------------------------


def test_move_refuses_what_the_days_page_would_not_offer(db_session: Session) -> None:
    contract = _contract(db_session, requires_prior_approval=True)
    service = WorkUnitService(db_session)
    flagged = service.create(_day(contract, date(2026, 3, 2)), WRITER)
    proposed = service.create(_day(contract, date(2026, 3, 3), "proposed"), WRITER)

    for work_unit, target, reason in (
        (proposed, "approved", "senza prova"),
        (flagged, "worked", "il trigger lo riscriverebbe"),
        (flagged, "invoiced", "lo fa una fattura"),
    ):
        with pytest.raises(Conflict) as refused:
            service.move(work_unit.id, target, WRITER, reason)
        # The graph allows these edges (`transition` does them); `move` refuses them.
        assert (refused.value.details["da"], refused.value.details["a"]) == (
            work_unit.status,
            target,
        )

    assert service.get(flagged.id).status == "worked_without_approval"
    assert service.get(proposed.id).status == "proposed"


def test_move_makes_the_moves_a_person_may_make_and_not_for_an_agent(db_session: Session) -> None:
    contract = _contract(db_session)
    service = WorkUnitService(db_session)
    day = service.create(_day(contract, date(2026, 3, 2)), WRITER)

    with pytest.raises(AgentForbidden):
        service.move(day.id, "disputed", AGENT, "contesto")
    moved = service.move(day.id, "disputed", WRITER, "il cliente contesta")

    assert moved.status == "disputed"
    with pytest.raises(NotFound):
        service.move(uuid4(), "disputed", WRITER, "x")
