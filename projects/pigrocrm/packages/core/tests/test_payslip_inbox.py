"""REB-1045: a payslip read from the inbox, accepted by a person, and a payroll month closed (spec
2026-11-09, staff, payroll and expenses, § 2 and § 4; the inbox design's frame A4).

What the card promises: a payslip PDF dropped in the inbox, with a proposal, makes the person's
month «Ricevuto»; accepting it replaces the estimate and its due items with the payslip's own
figures, the same ones `record_staff_payslip` writes; rejecting it keeps the document; a closed
month refuses a recording, a correction and an acceptance; and every pay read and decision is an
administrator's. The model is a fake transport, never a key; Postgres is real.
"""

from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_inbox_classifier import SETTINGS, FakeModel
from test_staff_payroll import (
    LAST_MONTH,
    NEXT_MONTH,
    THIS_MONTH,
    _payroll_items,
    _payslip,
    _world,
)
from test_text import minimal_pdf

from pigrocrm.core.actor import Actor
from pigrocrm.core.ai.gateway import ModelGateway
from pigrocrm.core.ai.reader import PAYSLIP_ADDENDUM
from pigrocrm.core.ai.schemas import AiModelUpdate
from pigrocrm.core.ai.service import AiModelService
from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.errors import (
    AgentForbidden,
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from pigrocrm.core.inbox.classifier import InboxClassifier
from pigrocrm.core.inbox.models import InboxItem
from pigrocrm.core.inbox.schemas import InboxKindSet, InboxListQuery
from pigrocrm.core.inbox.service import InboxService
from pigrocrm.core.inbox.uploads import InboxUploads
from pigrocrm.core.proposals.models import Proposal
from pigrocrm.core.proposals.schemas import (
    ProposalAccept,
    ProposalCreate,
    ProposalListQuery,
    ProposalReject,
)
from pigrocrm.core.proposals.service import ProposalService
from pigrocrm.core.staff.estimate import add_months
from pigrocrm.core.staff.models import StaffPayslip
from pigrocrm.core.staff.payroll import PayrollService
from pigrocrm.core.staff.schemas import PayrollMonthClose, PayslipCorrection, StaffMemberUpdate
from pigrocrm.core.staff.service import StaffService
from pigrocrm.core.storage.local import LocalFileStorage

PAYSLIP_LINES = [
    "Cedolino di paga",
    "Dipendente: Bianchi Giulia",
    "Totale competenze 2.142,86",
    "Netto in busta 1.607,15",
]
COLLABORATOR = Actor(id=uuid4(), type="user", role="collaborator")
READONLY = Actor(id=uuid4(), type="user", role="readonly")
AGENT_ADMIN = Actor(id=uuid4(), type="mcp", role="admin")
AGENT_ADMIN_FULL = Actor(id=uuid4(), type="mcp", role="admin", full_access=True)


def _answer(period: Any = NEXT_MONTH, **fields: Any) -> dict[str, Any]:
    figures: dict[str, Any] = {
        "employee_name": "Bianchi Giulia",
        "period": period.isoformat(),
        "gross": 2142.86,
        "net": 1607.15,
        "withholding": 321.43,
    }
    figures.update(fields)
    return {
        "kind": "payslip",
        "confidence": 0.92,
        "confidence_reason": "cedolino leggibile",
        "excerpt": "Netto in busta 1.607,15",
        "reason": None,
        "fields": {k: v for k, v in figures.items() if v is not None},
    }


@pytest.fixture
def storage(tmp_path: Path) -> LocalFileStorage:
    return LocalFileStorage(tmp_path / "store")


@pytest.fixture
def model() -> FakeModel:
    return FakeModel()


@pytest.fixture
def admin(db_session: Session) -> Actor:
    """An administrator with a row of their own: the classifier asks the uploader's role."""
    from pigrocrm.core.auth.models import User

    user = User(email=f"{uuid4()}@example.test", password_hash="x", name="Admin", role="admin")
    db_session.add(user)
    db_session.flush()
    return Actor(id=user.id, type="user", role="admin")


@pytest.fixture
def classifier(db_session: Session, storage: LocalFileStorage, model: FakeModel) -> InboxClassifier:
    return InboxClassifier(db_session, storage, ModelGateway(db_session, SETTINGS, model))


@pytest.fixture
def inbox(db_session: Session, storage: LocalFileStorage) -> InboxUploads:
    return InboxUploads(db_session, storage)


def _switch_on(session: Session, model: FakeModel, admin: Actor, *, payslips: bool = True) -> None:
    AiModelService(session, SETTINGS, model).update(
        AiModelUpdate.model_validate(
            {
                "provider": "anthropic",
                "model": "claude-test",
                "api_key": "placeholder-key-0123456789",
                "daily_cap_eur": "5.00",
                "input_eur_per_mtok": "3",
                "output_eur_per_mtok": "15",
                "enabled": True,
                "read_payslips": payslips,
                "privacy_acknowledged": True,
            }
        ),
        admin,
    )


def _read_payslip(
    session: Session,
    inbox: InboxUploads,
    classifier: InboxClassifier,
    model: FakeModel,
    admin: Actor,
    answer: dict[str, Any],
    *,
    uploader: Actor | None = None,
) -> Any:
    """A payslip PDF uploaded and read by the model: the item as the classifier left it."""
    model.say(answer)
    who = uploader or admin
    # A line of its own, or the second payslip of a test is a duplicate of the first.
    pdf = minimal_pdf([*PAYSLIP_LINES, f"Matricola {uuid4().hex[:8]}"])
    uploaded = inbox.upload([("cedolino.pdf", pdf)], who)
    return classifier.classify_uploaded(uploaded, who, model_budget=5).items[0]


def _month_row(session: Session, entity: UUID, month: Any, actor: Actor) -> Any:
    month_read = PayrollService(session).get_payroll_month(entity, month, actor)
    return month_read.members[0]


# ---- the card's acceptance ---------------------------------------------------------------


def test_a_payslip_in_the_inbox_makes_the_persons_month_received_until_a_person_decides(
    db_session: Session,
    inbox: InboxUploads,
    classifier: InboxClassifier,
    model: FakeModel,
    admin: Actor,
) -> None:
    entity, member = _world(db_session)
    StaffService(db_session).record_staff_payslip(member, _payslip(), admin)
    _switch_on(db_session, model, admin)

    item = _read_payslip(db_session, inbox, classifier, model, admin, _answer())

    assert (item.state, item.kind, item.legal_entity_id) == ("proposed", "payslip", entity)
    proposal = db_session.get(Proposal, item.proposal_id)
    assert proposal is not None
    assert (proposal.target_type, proposal.status) == ("payslip", "pending")
    assert proposal.proposed_fields["staff_member_id"] == str(member)
    assert proposal.proposed_fields["period"] == NEXT_MONTH.isoformat()
    assert Decimal(proposal.proposed_fields["net"]) == Decimal("1607.15")
    # A proposal and nothing else: no payslip row for that month, the estimate stands.
    assert (
        db_session.scalars(select(StaffPayslip).where(StaffPayslip.period == NEXT_MONTH)).first()
        is None
    )
    row = _month_row(db_session, entity, NEXT_MONTH, admin)
    assert row.source == "estimate" and row.payslip is None
    assert row.received is not None
    assert row.received.proposal_id == proposal.id
    assert row.received.inbox_item_id == item.id
    assert (row.received.gross, row.received.net, row.received.withholding) == (
        Decimal("2142.86"),
        Decimal("1607.15"),
        Decimal("321.43"),
    )
    # The other month, already accepted, has nothing received.
    assert _month_row(db_session, entity, LAST_MONTH, admin).received is None


def test_accepting_it_replaces_the_estimate_and_its_due_items_with_the_payslips_figures(
    db_session: Session,
    inbox: InboxUploads,
    classifier: InboxClassifier,
    model: FakeModel,
    admin: Actor,
    storage: LocalFileStorage,
) -> None:
    entity, member = _world(db_session)
    StaffService(db_session).record_staff_payslip(member, _payslip(), admin)
    estimate = _month_row(db_session, entity, NEXT_MONTH, admin)
    assert estimate.source == "estimate"
    rule = f":{NEXT_MONTH:%Y-%m}:"
    before = [
        i
        for i in _payroll_items(db_session, state="forecast")
        if i.source_rule
        and rule in i.source_rule
        and i.source_rule.split(":")[0] in ("payroll_net_pay", "payroll_withholding")
    ]
    assert len(before) == 2
    _switch_on(db_session, model, admin)
    item = _read_payslip(
        db_session,
        inbox,
        classifier,
        model,
        admin,
        _answer(gross=2500.00, net=1800.00, withholding=400.00),
    )

    accepted = ProposalService(db_session).accept(item.proposal_id, ProposalAccept(), admin)

    assert accepted.status == "accepted" and accepted.result_id is not None
    payslip = db_session.get(StaffPayslip, accepted.result_id)
    assert payslip is not None
    assert (payslip.staff_member_id, payslip.period) == (member, NEXT_MONTH)
    assert (payslip.gross, payslip.net, payslip.withholding) == (
        Decimal("2500.00"),
        Decimal("1800.00"),
        Decimal("400.00"),
    )
    assert payslip.inbox_item_id == item.id
    # The month is the payslip's now, and nobody waits any more.
    row = _month_row(db_session, entity, NEXT_MONTH, admin)
    assert row.source == "payslip" and row.received is None
    assert row.payslip is not None and row.payslip.inbox_item_id == item.id
    assert row.cost is not None and row.cost.gross == Decimal("2500.00")
    # The estimate's two due items are dropped, and the payslip's own are expected.
    for dropped in before:
        db_session.refresh(dropped)
        assert dropped.state == "dropped"
    expected = {
        (i.tax or "net_pay"): i.amount for i in _payroll_items(db_session, source_id=payslip.id)
    }
    assert expected == {"net_pay": Decimal("1800.00"), "payroll_withholding": Decimal("400.00")}
    assert {i.state for i in _payroll_items(db_session, source_id=payslip.id)} == {"expected"}
    # The item is filed and keeps its file: it is the payslip's PDF.
    stored = db_session.get(InboxItem, item.id)
    assert stored is not None
    assert (stored.state, stored.kind) == ("filed", "payslip")
    assert stored.storage_key is not None and storage.get(stored.storage_key)


def test_an_accepted_payslip_is_the_row_a_typed_one_is(db_session: Session, admin: Actor) -> None:
    """The proposal and `record_staff_payslip` write the same row and the same due items."""
    entity, member = _world(db_session)
    typed = StaffService(db_session).record_staff_payslip(member, _payslip(LAST_MONTH), admin)
    assert typed.inbox_item_id is None
    kinds = {(i.state, i.tax or "net_pay") for i in _payroll_items(db_session, source_id=typed.id)}
    assert kinds == {("expected", "net_pay"), ("expected", "payroll_withholding")}


def test_rejecting_it_keeps_the_document_and_the_estimate(
    db_session: Session,
    inbox: InboxUploads,
    classifier: InboxClassifier,
    model: FakeModel,
    admin: Actor,
    storage: LocalFileStorage,
) -> None:
    entity, member = _world(db_session)
    StaffService(db_session).record_staff_payslip(member, _payslip(), admin)
    _switch_on(db_session, model, admin)
    item = _read_payslip(db_session, inbox, classifier, model, admin, _answer())

    rejected = ProposalService(db_session).reject(
        item.proposal_id, ProposalReject(reason="le cifre non tornano"), admin
    )

    assert rejected.status == "rejected"
    stored = db_session.get(InboxItem, item.id)
    assert stored is not None
    # Still a payslip, still hidden from whoever may not read pay, with its bytes.
    assert (stored.state, stored.kind) == ("new", "payslip")
    assert stored.storage_key is not None and storage.get(stored.storage_key)
    assert (
        db_session.scalars(select(StaffPayslip).where(StaffPayslip.period == NEXT_MONTH)).first()
        is None
    )
    row = _month_row(db_session, entity, NEXT_MONTH, admin)
    assert row.source == "estimate" and row.received is None
    assert InboxService(db_session).list(InboxListQuery(), COLLABORATOR).items == []
    assert [i.id for i in InboxService(db_session).list(InboxListQuery(), admin).items] == [item.id]
    # A decided proposal is not decided twice.
    with pytest.raises(Conflict):
        ProposalService(db_session).accept(item.proposal_id, ProposalAccept(), admin)


def test_the_agent_proposes_a_payslip_by_the_persons_id_and_a_person_accepts_it(
    db_session: Session, inbox: InboxUploads, admin: Actor
) -> None:
    entity, member = _world(db_session)
    uploaded = inbox.upload([("cedolino.pdf", minimal_pdf(PAYSLIP_LINES))], admin)
    item_id = uploaded.items[0].id
    proposal = ProposalService(db_session).create(
        ProposalCreate(
            inbox_item_id=item_id,
            target_type="payslip",
            proposed_fields={
                "staff_member_id": str(member),
                "period": (NEXT_MONTH.replace(day=15)).isoformat(),
                "gross": "2142.86",
                "net": "1607.15",
                "withholding": "321.43",
            },
            excerpt="Netto in busta 1.607,15",
            confidence=Decimal("0.8"),
        ),
        AGENT_ADMIN_FULL,
    )
    # Stored as parsed: the month as its first day, the amounts as decimal strings.
    assert proposal.proposed_fields["period"] == NEXT_MONTH.isoformat()
    item = db_session.get(InboxItem, item_id)
    assert item is not None and (item.state, item.kind, item.legal_entity_id) == (
        "proposed",
        "payslip",
        entity,
    )
    # The agent cannot decide it, whatever it carries.
    with pytest.raises(AgentForbidden):
        ProposalService(db_session).accept(proposal.id, ProposalAccept(), AGENT_ADMIN_FULL)
    ProposalService(db_session).accept(proposal.id, ProposalAccept(), admin)
    assert _month_row(db_session, entity, NEXT_MONTH, admin).source == "payslip"


# ---- closing a month ------------------------------------------------------------------------


def test_a_closed_month_refuses_recording_correcting_and_accepting_a_payslip(
    db_session: Session,
    inbox: InboxUploads,
    classifier: InboxClassifier,
    model: FakeModel,
    admin: Actor,
) -> None:
    entity, member = _world(db_session)
    service = StaffService(db_session)
    recorded = service.record_staff_payslip(member, _payslip(LAST_MONTH), admin)
    waiting = add_months(LAST_MONTH, -1)
    _switch_on(db_session, model, admin)
    item = _read_payslip(db_session, inbox, classifier, model, admin, _answer(waiting))
    assert item.proposal_id is not None

    closed = service.close_payroll_month(entity, PayrollMonthClose(month=LAST_MONTH), admin)
    assert closed.month == LAST_MONTH and closed.closed_by is not None

    with pytest.raises(Conflict) as typed:
        service.record_staff_payslip(member, _payslip(LAST_MONTH, gross=Decimal("3000.00")), admin)
    assert "chiuso" in str(typed.value)
    with pytest.raises(Conflict):
        service.correct_staff_payslip(
            member,
            recorded.id,
            PayslipCorrection(
                gross=Decimal("2200.00"), net=Decimal("1600.00"), withholding=Decimal("300.00")
            ),
            admin,
        )
    # Not even a correction to what it already is: the month is closed.
    with pytest.raises(Conflict):
        service.correct_staff_payslip(
            member,
            recorded.id,
            PayslipCorrection(
                gross=recorded.gross, net=recorded.net, withholding=recorded.withholding
            ),
            admin,
        )
    # A waiting proposal for another, open month is still acceptable.
    ProposalService(db_session).accept(item.proposal_id, ProposalAccept(), admin)
    # And one for the closed month is refused whole, still pending.
    closed_item = _read_payslip(
        db_session, inbox, classifier, model, admin, _answer(LAST_MONTH, gross=2100.00)
    )
    with pytest.raises(Conflict) as accepted:
        ProposalService(db_session).accept(closed_item.proposal_id, ProposalAccept(), admin)
    assert "chiuso" in str(accepted.value)
    still = db_session.get(Proposal, closed_item.proposal_id)
    assert still is not None and still.status == "pending"
    # A month closes once.
    with pytest.raises(Conflict):
        service.close_payroll_month(entity, PayrollMonthClose(month=LAST_MONTH), admin)


def test_a_closed_month_reads_as_it_was_closed_whatever_the_inputs_become(
    db_session: Session, admin: Actor
) -> None:
    entity, member = _world(db_session)
    service = StaffService(db_session)
    service.record_staff_payslip(member, _payslip(add_months(LAST_MONTH, -1)), admin)
    before = _month_row(db_session, entity, LAST_MONTH, admin)
    assert before.source == "estimate" and before.cost is not None
    # What the month has forecast for a date still to come (its F24 lines, from the ratios of the
    # earlier payslip): it exists only while that date is ahead, so the check follows the day.
    closed_rule = f":{LAST_MONTH:%Y-%m}:"
    forecast_of_it = {
        i.id: i.amount
        for i in _payroll_items(db_session, state="forecast")
        if i.source_rule and closed_rule in i.source_rule
    }
    open_rule = f":{NEXT_MONTH:%Y-%m}:"
    forecast_of_next = {
        i.id
        for i in _payroll_items(db_session, state="forecast")
        if i.source_rule and open_rule in i.source_rule
    }
    assert forecast_of_next

    service.close_payroll_month(entity, PayrollMonthClose(month=LAST_MONTH), admin)
    service.update_staff_member(
        member, StaffMemberUpdate(gross_annual_pay=Decimal("60000.00")), admin
    )

    after = PayrollService(db_session).get_payroll_month(entity, LAST_MONTH, admin)
    assert after.closed is not None and after.closed.closed_by is not None
    assert after.members[0].cost is not None
    assert after.members[0].cost.total == before.cost.total
    # The open month moved with the inputs.
    moved = _month_row(db_session, entity, THIS_MONTH, admin).cost
    assert moved is not None and moved.total > before.cost.total
    # What the closed month had forecast is not rewritten, and the open month's is.
    for item in _payroll_items(db_session, state="forecast"):
        if item.id in forecast_of_it:
            assert item.amount == forecast_of_it[item.id]
    for item_id in forecast_of_it:
        kept = db_session.get(DueItem, item_id)
        assert kept is not None and kept.state == "forecast"
    assert not forecast_of_next & {i.id for i in _payroll_items(db_session, state="forecast")}


def test_only_a_month_that_has_ended_closes(db_session: Session, admin: Actor) -> None:
    entity, _ = _world(db_session)
    for month in (THIS_MONTH, NEXT_MONTH):
        with pytest.raises(ValidationFailed):
            StaffService(db_session).close_payroll_month(
                entity, PayrollMonthClose(month=month), admin
            )


def test_closing_a_month_is_an_administrators_and_a_full_access_tokens(
    db_session: Session, admin: Actor
) -> None:
    entity, _ = _world(db_session)
    service = StaffService(db_session)
    data = PayrollMonthClose(month=LAST_MONTH)
    for refused in (COLLABORATOR, READONLY):
        with pytest.raises(PermissionDenied):
            service.close_payroll_month(entity, data, refused)
    with pytest.raises(AgentForbidden):
        service.close_payroll_month(entity, data, AGENT_ADMIN)
    # A token acts as the user who owns it: a real one, since the closing names who closed.
    owner = Actor(id=admin.id, type="mcp", role="admin", full_access=True)
    closed = service.close_payroll_month(entity, data, owner)
    assert closed.month == LAST_MONTH


# ---- who sees pay ---------------------------------------------------------------------------


def test_nobody_but_whoever_reads_pay_sees_a_payslip_item_or_its_proposal(
    db_session: Session,
    inbox: InboxUploads,
    classifier: InboxClassifier,
    model: FakeModel,
    admin: Actor,
) -> None:
    entity, member = _world(db_session)
    _switch_on(db_session, model, admin)
    item = _read_payslip(db_session, inbox, classifier, model, admin, _answer())
    proposals = ProposalService(db_session)
    service = InboxService(db_session)

    for outsider in (COLLABORATOR, READONLY, AGENT_ADMIN):
        assert service.list(InboxListQuery(), outsider).items == []
        with pytest.raises(NotFound):
            service.get(item.id, outsider)
        assert proposals.list(ProposalListQuery(), outsider).items == []
        with pytest.raises(NotFound):
            proposals.get(item.proposal_id, outsider)
    # The administrator and a `full_access` token read both.
    for reader in (admin, AGENT_ADMIN_FULL):
        assert [i.id for i in service.list(InboxListQuery(), reader).items] == [item.id]
        assert proposals.get(item.proposal_id, reader).target_type == "payslip"
    # A member who may not accept finds it is not there; an agent is refused as for every proposal.
    with pytest.raises(NotFound):
        proposals.accept(item.proposal_id, ProposalAccept(), COLLABORATOR)
    with pytest.raises(NotFound):
        proposals.reject(item.proposal_id, ProposalReject(), COLLABORATOR)
    with pytest.raises(AgentForbidden):
        proposals.accept(item.proposal_id, ProposalAccept(), AGENT_ADMIN_FULL)
    # The month read names the waiting payslip only to an administrator.
    assert _month_row(db_session, entity, NEXT_MONTH, admin).received is not None
    with pytest.raises(PermissionDenied):
        PayrollService(db_session).get_payroll_month(entity, NEXT_MONTH, COLLABORATOR)


def test_saying_an_item_is_a_payslip_or_proposing_one_needs_whoever_reads_pay(
    db_session: Session, inbox: InboxUploads, admin: Actor
) -> None:
    entity, member = _world(db_session)
    uploader = Actor(id=admin.id, type="user", role="collaborator")
    uploaded = inbox.upload([("cedolino.pdf", minimal_pdf(PAYSLIP_LINES))], uploader)
    item_id = uploaded.items[0].id
    for kind in ("payslip", "tax_document"):
        with pytest.raises(PermissionDenied):
            InboxService(db_session).set_kind(item_id, InboxKindSet(kind=kind), uploader)
        with pytest.raises(AgentForbidden):
            InboxService(db_session).set_kind(item_id, InboxKindSet(kind=kind), AGENT_ADMIN)
    fields = {
        "staff_member_id": str(member),
        "period": NEXT_MONTH.isoformat(),
        "gross": "2142.86",
        "net": "1607.15",
        "withholding": "321.43",
    }
    data = ProposalCreate(
        inbox_item_id=item_id,
        target_type="payslip",
        proposed_fields=fields,
        excerpt="Netto",
        confidence=Decimal("0.5"),
    )
    with pytest.raises(PermissionDenied):
        ProposalService(db_session).create(data, uploader)
    with pytest.raises(AgentForbidden):
        ProposalService(db_session).create(data, AGENT_ADMIN)
    # A person with a staff member of another legal entity is no match for this item.
    other_entity, other_member = _world(db_session)
    other = ProposalCreate(
        inbox_item_id=item_id,
        target_type="payslip",
        proposed_fields={**fields, "staff_member_id": str(other_member)},
        excerpt="Netto",
        confidence=Decimal("0.5"),
    )
    ProposalService(db_session).create(other, admin)
    assert db_session.get(InboxItem, item_id).legal_entity_id == other_entity  # type: ignore[union-attr]


def test_a_proposal_is_refused_for_a_person_that_is_not_there_or_not_in_service(
    db_session: Session, inbox: InboxUploads, admin: Actor
) -> None:
    entity, member = _world(db_session)
    uploaded = inbox.upload([("cedolino.pdf", minimal_pdf(PAYSLIP_LINES))], admin)
    base = {
        "inbox_item_id": uploaded.items[0].id,
        "target_type": "payslip",
        "excerpt": "Netto",
        "confidence": Decimal("0.5"),
    }
    figures = {"period": NEXT_MONTH.isoformat(), "gross": "100.00", "net": "70.00"}
    with pytest.raises(NotFound):
        ProposalService(db_session).create(
            ProposalCreate(
                **base,  # type: ignore[arg-type]
                proposed_fields={
                    **figures,
                    "withholding": "10.00",
                    "staff_member_id": str(uuid4()),
                },
            ),
            admin,
        )
    with pytest.raises(ValidationFailed):
        ProposalService(db_session).create(
            ProposalCreate(
                **base,  # type: ignore[arg-type]
                # The net and the withholding cannot be more than the gross.
                proposed_fields={**figures, "withholding": "50.00", "staff_member_id": str(member)},
            ),
            admin,
        )
    # Accepting for a month the person was not in service in is refused, the proposal pending.
    created = ProposalService(db_session).create(
        ProposalCreate(
            **base,  # type: ignore[arg-type]
            proposed_fields={
                **figures,
                "period": "2020-01-01",
                "withholding": "10.00",
                "staff_member_id": str(member),
            },
        ),
        admin,
    )
    with pytest.raises(ValidationFailed):
        ProposalService(db_session).accept(created.id, ProposalAccept(), admin)
    pending = db_session.get(Proposal, created.id)
    assert pending is not None and pending.status == "pending"


# ---- how the model reads it -----------------------------------------------------------------


def test_with_the_payslip_switch_off_the_model_is_not_asked_for_figures_and_the_payslip_is_other(
    db_session: Session,
    inbox: InboxUploads,
    classifier: InboxClassifier,
    model: FakeModel,
    admin: Actor,
) -> None:
    _world(db_session)
    _switch_on(db_session, model, admin, payslips=False)
    model.say(
        {
            "kind": "other",
            "confidence": 0.9,
            "excerpt": None,
            "reason": "Un cedolino",
            "fields": {},
        }
    )
    uploaded = inbox.upload([("cedolino.pdf", minimal_pdf(PAYSLIP_LINES))], admin)
    item = classifier.classify_uploaded(uploaded, admin, model_budget=5).items[0]

    assert (item.state, item.kind) == ("new", "other")
    assert PAYSLIP_ADDENDUM not in model.requests[0].system
    assert db_session.scalars(select(Proposal)).first() is None


def test_the_model_is_asked_for_a_payslip_only_when_the_switch_is_on_and_the_reader_reads_pay(
    db_session: Session,
    inbox: InboxUploads,
    storage: LocalFileStorage,
    model: FakeModel,
    admin: Actor,
) -> None:
    _world(db_session)
    _switch_on(db_session, model, admin)
    # A collaborator's upload is read as before: the figures are never asked for on their behalf.
    classifier = InboxClassifier(db_session, storage, ModelGateway(db_session, SETTINGS, model))
    collaborator = Actor(id=admin.id, type="user", role="collaborator")
    model.say(
        {"kind": "other", "confidence": 0.9, "excerpt": None, "reason": "Un cedolino", "fields": {}}
    )
    uploaded = inbox.upload([("cedolino.pdf", minimal_pdf(PAYSLIP_LINES))], collaborator)
    item = classifier.classify_uploaded(uploaded, collaborator, model_budget=5).items[0]
    assert item.kind == "other" and PAYSLIP_ADDENDUM not in model.requests[0].system
    # An administrator's is asked for them, with the addendum in the prompt.
    model.say(_answer())
    uploaded = inbox.upload([("cedolino2.pdf", minimal_pdf(PAYSLIP_LINES + ["bis"]))], admin)
    classifier.classify_uploaded(uploaded, admin, model_budget=5)
    assert PAYSLIP_ADDENDUM in model.requests[1].system


def test_a_payslip_that_names_nobody_the_company_pays_waits_as_a_payslip_with_no_proposal(
    db_session: Session,
    inbox: InboxUploads,
    classifier: InboxClassifier,
    model: FakeModel,
    admin: Actor,
) -> None:
    _world(db_session)
    _switch_on(db_session, model, admin)

    stranger = _read_payslip(
        db_session, inbox, classifier, model, admin, _answer(employee_name="Mario Rossi")
    )
    assert (stranger.state, stranger.kind, stranger.proposal_id) == ("new", "payslip", None)
    assert stranger.reason is not None and "persona" in stranger.reason

    # A figure the document did not state is no proposal either: a person types it.
    model.say(_answer(withholding=None))
    uploaded = inbox.upload([("altro.pdf", minimal_pdf(PAYSLIP_LINES + ["secondo"]))], admin)
    partial = classifier.classify_uploaded(uploaded, admin, model_budget=5).items[0]
    assert (partial.kind, partial.proposal_id) == ("payslip", None)
    assert partial.reason is not None and "withholding" in partial.reason
    assert db_session.scalars(select(Proposal)).first() is None


def test_the_payslip_switch_needs_the_notice_read_and_goes_off_with_the_provider(
    db_session: Session, model: FakeModel, admin: Actor
) -> None:
    service = AiModelService(db_session, SETTINGS, model)
    _switch_on(db_session, model, admin, payslips=False)
    assert service.get(admin).read_payslips is False
    with pytest.raises(ValidationFailed):
        service.update(AiModelUpdate(read_payslips=True), admin)
    on = service.update(AiModelUpdate(read_payslips=True, privacy_acknowledged=True), admin)
    assert on.read_payslips is True and "cedolin" in on.payslip_notice
    # A different provider is a different consent: the model and the payslip switch go off.
    changed = service.update(AiModelUpdate(provider="openai"), admin)
    assert (changed.enabled, changed.read_payslips) == (False, False)


def test_a_collaborators_upload_is_not_read_for_its_figures_when_an_admin_runs_the_reading(
    db_session: Session,
    inbox: InboxUploads,
    classifier: InboxClassifier,
    model: FakeModel,
    admin: Actor,
) -> None:
    from pigrocrm.core.auth.models import User

    _world(db_session)
    _switch_on(db_session, model, admin)
    user = User(
        email=f"{uuid4()}@example.test", password_hash="x", name="Collab", role="collaborator"
    )
    db_session.add(user)
    db_session.flush()
    collaborator = Actor(id=user.id, type="user", role="collaborator")
    uploaded = inbox.upload([("cedolino.pdf", minimal_pdf(PAYSLIP_LINES))], collaborator)
    model.say(
        {"kind": "other", "confidence": 0.9, "excerpt": None, "reason": "Un cedolino", "fields": {}}
    )

    item = classifier.classify_uploaded(uploaded, admin, model_budget=5).items[0]

    assert item.kind == "other"
    assert PAYSLIP_ADDENDUM not in model.requests[0].system
