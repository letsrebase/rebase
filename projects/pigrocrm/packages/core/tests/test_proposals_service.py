"""REB-362's own "what the implementing issues must test" for proposal review (design
spec §14, "Proposals"): the CHECK tying `contract_id`'s nullability to `target_type`,
a `'work_unit'` accept creating both the `approval` and the `work_unit` in one
transaction, `proposed_fields`/`accepted_fields` never overwritten, and `excerpt`
verifiable against the source document's own text for `excerpt_type = 'quoted'`.

`proposed_fields` payloads below are built as plain JSON-safe primitives (strings for
UUIDs/dates, decimal strings for money and quantities) -- exactly the shape a real
MCP tool or REST call would submit, never a Python `UUID`/`date`/`Decimal` object --
so these tests exercise the same coercion `ProposalService` applies to caller-supplied
JSON, not a shortcut that skips it.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fakes.customer_fixtures import make_customer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_text import minimal_pdf

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.models import Document
from pigrocrm.core.documents.schemas import DocumentCreate
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.proposals.models import Proposal
from pigrocrm.core.proposals.schemas import (
    ProposalAccept,
    ProposalCreate,
    ProposalListQuery,
    ProposalReject,
)
from pigrocrm.core.proposals.service import ProposalService
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm.core.work_units.models import Approval, WorkUnit

ADMIN = Actor(id=None, type="system", role="admin")


def _contract(
    db_session: Session, *, customer_id: UUID | None = None, **overrides: object
) -> Contract:
    payload: dict[str, object] = {
        "customer_id": customer_id or make_customer(db_session).id,
        "title": "Consulenza",
        "start": date(2026, 1, 1),
        "renewal_type": "none",
        "termination_notice_days": 30,
        "billing_cadence": "monthly",
        "expense_policy": {"kind": "not_reimbursable"},
    }
    payload.update(overrides)
    contract = Contract(**payload)  # type: ignore[arg-type]
    db_session.add(contract)
    db_session.flush()
    return contract


def _document(db_session: Session, **owner: object) -> Document:
    document = Document(type="contract", title="Documento archiviato", **owner)  # type: ignore[arg-type]
    db_session.add(document)
    db_session.flush()
    return document


def _contract_fields(customer_id: UUID, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "contract": {
            "customer_id": str(customer_id),
            "title": "Consulenza annuale",
            "start": "2026-01-01",
            "renewal_type": "none",
            "termination_notice_days": 30,
            "billing_cadence": "monthly",
            "expense_policy": {"kind": "not_reimbursable"},
        },
        "rate_card": {
            "valid_from": "2026-01-01",
            "type": "fixed_recurring",
            "amount": "1000.00",
            "unit": "month",
            "delivery_period": "monthly",
        },
    }
    payload.update(overrides)
    return payload


def _work_unit_fields(contract_id: UUID, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "contract_id": str(contract_id),
        "date": "2026-03-05",
        "quantity": "1.00",
        "description": "Giornata di consulenza",
        "approval": {
            "channel": "email",
            "sender": "cliente@example.test",
            "received_at": "2026-03-01T09:00:00+00:00",
            "message_id": None,
        },
    }
    payload.update(overrides)
    return payload


def _proposal_create(document_id: UUID, **overrides: object) -> ProposalCreate:
    payload: dict[str, object] = {
        "document_id": document_id,
        "target_type": "work_unit",
        "proposed_fields": {},
        "excerpt": "Confermiamo la giornata del 5 marzo.",
        "confidence": Decimal("0.90"),
    }
    payload.update(overrides)
    return ProposalCreate(**payload)  # type: ignore[arg-type]


# ---- create: the one proposal-creation step -----------------------------------------


def test_create_of_to_contract_proposal_produces_to_reviewable_row_with_its_excerpt(
    db_session: Session,
) -> None:
    customer = make_customer(db_session)
    document = _document(db_session, customer_id=customer.id)
    proposal = ProposalService(db_session).create(
        _proposal_create(
            document.id,
            target_type="contract",
            proposed_fields=_contract_fields(customer.id),
            excerpt="Il presente contratto ha durata annuale.",
        ),
        ADMIN,
    )
    assert proposal.status == "pending"
    assert proposal.contract_id is None
    assert proposal.excerpt == "Il presente contratto ha durata annuale."
    assert proposal.proposed_fields["contract"]["title"] == "Consulenza annuale"


def test_create_of_to_work_unit_proposal_produces_to_reviewable_row_with_its_excerpt(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    document = _document(db_session, contract_id=contract.id)
    proposal = ProposalService(db_session).create(
        _proposal_create(
            document.id,
            target_type="work_unit",
            contract_id=contract.id,
            proposed_fields=_work_unit_fields(contract.id),
            excerpt="Confermiamo la giornata del 5 marzo.",
        ),
        ADMIN,
    )
    assert proposal.status == "pending"
    assert proposal.contract_id == contract.id
    assert proposal.accepted_fields is None


def test_create_rejects_an_unknown_document(db_session: Session) -> None:
    with pytest.raises(NotFound):
        ProposalService(db_session).create(
            _proposal_create(uuid4(), target_type="contract", proposed_fields={}), ADMIN
        )


def test_create_of_contract_rejects_to_supplied_contract_id(db_session: Session) -> None:
    """REB-344's own scope-down: this spike does not model a renewal-targeted
    proposal, so a `'contract'` proposal never points at an existing contract."""
    customer = make_customer(db_session)
    document = _document(db_session, customer_id=customer.id)
    contract = _contract(db_session, customer_id=customer.id)
    with pytest.raises(ValidationFailed) as excinfo:
        ProposalService(db_session).create(
            _proposal_create(
                document.id,
                target_type="contract",
                contract_id=contract.id,
                proposed_fields=_contract_fields(customer.id),
            ),
            ADMIN,
        )
    assert excinfo.value.details["field"] == "contract_id"


def test_create_of_work_unit_requires_to_contract_id(db_session: Session) -> None:
    document = _document(db_session, customer_id=make_customer(db_session).id)
    with pytest.raises(ValidationFailed) as excinfo:
        ProposalService(db_session).create(
            _proposal_create(document.id, target_type="work_unit", proposed_fields={}), ADMIN
        )
    assert excinfo.value.details["field"] == "contract_id"


def test_create_of_work_unit_rejects_to_contract_id_mismatched_with_proposed_fields(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    other_contract = _contract(db_session)
    document = _document(db_session, contract_id=contract.id)
    with pytest.raises(ValidationFailed) as excinfo:
        ProposalService(db_session).create(
            _proposal_create(
                document.id,
                target_type="work_unit",
                contract_id=contract.id,
                proposed_fields=_work_unit_fields(other_contract.id),
            ),
            ADMIN,
        )
    assert excinfo.value.details["field"] == "proposed_fields.contract_id"


def test_create_of_work_unit_rejects_an_unknown_contract(db_session: Session) -> None:
    document = _document(db_session, customer_id=make_customer(db_session).id)
    unknown = uuid4()
    with pytest.raises(NotFound):
        ProposalService(db_session).create(
            _proposal_create(
                document.id,
                target_type="work_unit",
                contract_id=unknown,
                proposed_fields=_work_unit_fields(unknown),
            ),
            ADMIN,
        )


def test_create_of_contract_rejects_an_unknown_customer(db_session: Session) -> None:
    document = _document(db_session, customer_id=make_customer(db_session).id)
    with pytest.raises(NotFound):
        ProposalService(db_session).create(
            _proposal_create(
                document.id, target_type="contract", proposed_fields=_contract_fields(uuid4())
            ),
            ADMIN,
        )


def test_create_rejects_to_malformed_proposed_fields_shape_with_to_clean_error(
    db_session: Session,
) -> None:
    """A schema mismatch is a `ValidationFailed`, not a raw pydantic traceback or a
    silent write of garbage into the JSONB column."""
    document = _document(db_session, customer_id=make_customer(db_session).id)
    with pytest.raises(ValidationFailed) as excinfo:
        ProposalService(db_session).create(
            _proposal_create(document.id, target_type="contract", proposed_fields={"contract": {}}),
            ADMIN,
        )
    assert excinfo.value.details["field"] == "proposed_fields"


# ---- get / list -----------------------------------------------------------------------


def test_get_of_an_unknown_proposal_is_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        ProposalService(db_session).get(uuid4(), ADMIN)


def test_list_filters_by_status_oldest_first(db_session: Session) -> None:
    customer = make_customer(db_session)
    document = _document(db_session, customer_id=customer.id)
    service = ProposalService(db_session)
    first = service.create(
        _proposal_create(
            document.id, target_type="contract", proposed_fields=_contract_fields(customer.id)
        ),
        ADMIN,
    )
    second = service.create(
        _proposal_create(
            document.id, target_type="contract", proposed_fields=_contract_fields(customer.id)
        ),
        ADMIN,
    )
    service.reject(second.id, ProposalReject(), ADMIN)

    pending = service.list(ProposalListQuery(status="pending"), ADMIN)
    assert [p.id for p in pending.items] == [first.id]


# ---- reject -----------------------------------------------------------------------


def test_reject_moves_to_proposal_to_rejected_and_records_who_and_when(
    db_session: Session,
) -> None:
    customer = make_customer(db_session)
    document = _document(db_session, customer_id=customer.id)
    service = ProposalService(db_session)
    proposal = service.create(
        _proposal_create(
            document.id, target_type="contract", proposed_fields=_contract_fields(customer.id)
        ),
        ADMIN,
    )
    rejected = service.reject(proposal.id, ProposalReject(), ADMIN)
    assert rejected.status == "rejected"
    assert rejected.decided_by == "system"
    assert rejected.decided_at is not None


def test_reject_of_an_already_decided_proposal_is_a_conflict(db_session: Session) -> None:
    customer = make_customer(db_session)
    document = _document(db_session, customer_id=customer.id)
    service = ProposalService(db_session)
    proposal = service.create(
        _proposal_create(
            document.id, target_type="contract", proposed_fields=_contract_fields(customer.id)
        ),
        ADMIN,
    )
    service.reject(proposal.id, ProposalReject(), ADMIN)
    with pytest.raises(Conflict):
        service.reject(proposal.id, ProposalReject(), ADMIN)


def test_reject_of_an_unknown_proposal_is_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        ProposalService(db_session).reject(uuid4(), ProposalReject(), ADMIN)


# ---- accept: 'contract' -----------------------------------------------------------


def test_accept_contract_creates_to_contract_and_its_first_rate_card(
    db_session: Session,
) -> None:
    customer = make_customer(db_session)
    document = _document(db_session, customer_id=customer.id)
    service = ProposalService(db_session)
    proposal = service.create(
        _proposal_create(
            document.id, target_type="contract", proposed_fields=_contract_fields(customer.id)
        ),
        ADMIN,
    )

    accepted = service.accept(proposal.id, ProposalAccept(), ADMIN)

    assert accepted.status == "accepted"
    assert accepted.result_id is not None
    contract = db_session.get(Contract, accepted.result_id)
    assert contract is not None
    assert contract.title == "Consulenza annuale"
    cards = db_session.query(RateCard).filter_by(contract_id=contract.id).all()
    assert len(cards) == 1
    assert cards[0].type == "fixed_recurring"


def test_accept_contract_repoints_the_originating_documents_ownership(
    db_session: Session,
) -> None:
    customer = make_customer(db_session)
    document = _document(db_session, customer_id=customer.id)
    service = ProposalService(db_session)
    proposal = service.create(
        _proposal_create(
            document.id, target_type="contract", proposed_fields=_contract_fields(customer.id)
        ),
        ADMIN,
    )

    accepted = service.accept(proposal.id, ProposalAccept(), ADMIN)

    db_session.refresh(document)
    assert document.contract_id == accepted.result_id
    assert document.customer_id is None
    assert document.deal_id is None


def test_accept_preserves_proposed_fields_and_stores_the_override_separately(
    db_session: Session,
) -> None:
    """Spec §10: "kept separately... it stays correct forever only if neither side
    is ever overwritten." """
    customer = make_customer(db_session)
    document = _document(db_session, customer_id=customer.id)
    service = ProposalService(db_session)
    original_fields = _contract_fields(customer.id)
    proposal = service.create(
        _proposal_create(document.id, target_type="contract", proposed_fields=original_fields),
        ADMIN,
    )

    edited_contract = {**original_fields["contract"], "title": "Consulenza (corretta)"}
    edited_fields = _contract_fields(customer.id, contract=edited_contract)
    accepted = service.accept(proposal.id, ProposalAccept(accepted_fields=edited_fields), ADMIN)

    assert accepted.proposed_fields["contract"]["title"] == "Consulenza annuale"
    assert accepted.accepted_fields["contract"]["title"] == "Consulenza (corretta)"
    contract = db_session.get(Contract, accepted.result_id)
    assert contract.title == "Consulenza (corretta)"


def test_accept_of_an_already_decided_proposal_is_a_conflict(db_session: Session) -> None:
    customer = make_customer(db_session)
    document = _document(db_session, customer_id=customer.id)
    service = ProposalService(db_session)
    proposal = service.create(
        _proposal_create(
            document.id, target_type="contract", proposed_fields=_contract_fields(customer.id)
        ),
        ADMIN,
    )
    service.reject(proposal.id, ProposalReject(), ADMIN)
    with pytest.raises(Conflict):
        service.accept(proposal.id, ProposalAccept(), ADMIN)


# ---- accept: 'work_unit' -------------------------------------------------------------


def test_accept_work_unit_creates_an_approval_and_to_work_unit_at_approved_in_one_call(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    document = _document(db_session, contract_id=contract.id)
    service = ProposalService(db_session)
    proposal = service.create(
        _proposal_create(
            document.id,
            target_type="work_unit",
            contract_id=contract.id,
            proposed_fields=_work_unit_fields(contract.id),
        ),
        ADMIN,
    )

    accepted = service.accept(proposal.id, ProposalAccept(), ADMIN)

    assert accepted.status == "accepted"
    work_unit = db_session.get(WorkUnit, accepted.result_id)
    assert work_unit is not None
    # Never left at 'proposto' -- the whole point of spec §10's own rule.
    assert work_unit.status == "approved"
    assert work_unit.approval_id is not None
    approval = db_session.get(Approval, work_unit.approval_id)
    assert approval is not None
    assert approval.document_id == document.id
    assert approval.excerpt == proposal.excerpt
    assert approval.origin == {"kind": "agente", "proposal_id": str(proposal.id)}


def test_accept_work_unit_rejects_an_edited_contract_id_that_diverges_from_the_proposal(
    db_session: Session,
) -> None:
    """The reviewer looked at `proposal.contract_id`; an edited `accepted_fields`
    must not be able to silently redirect the accept onto a different contract."""
    contract = _contract(db_session)
    other_contract = _contract(db_session)
    document = _document(db_session, contract_id=contract.id)
    service = ProposalService(db_session)
    proposal = service.create(
        _proposal_create(
            document.id,
            target_type="work_unit",
            contract_id=contract.id,
            proposed_fields=_work_unit_fields(contract.id),
        ),
        ADMIN,
    )

    diverging = _work_unit_fields(other_contract.id)
    with pytest.raises(ValidationFailed) as excinfo:
        service.accept(proposal.id, ProposalAccept(accepted_fields=diverging), ADMIN)
    assert excinfo.value.details["field"] == "accepted_fields.contract_id"
    assert db_session.query(WorkUnit).count() == 0
    assert db_session.query(Approval).count() == 0
    reloaded = service.get(proposal.id, ADMIN)
    assert reloaded.status == "pending"


def test_accept_work_unit_is_atomic_when_the_work_unit_write_conflicts(
    db_session: Session,
) -> None:
    """The partial unique index (`uq_work_units_contract_data_live`) refuses a
    second live day on the same contract and date. When it does, nothing of this
    accept attempt survives: no orphan `Approval`, and the proposal stays
    `'pending'` -- never a day silently left at `'proposed'`, and never a
    half-applied accept."""
    contract = _contract(db_session)
    document = _document(db_session, contract_id=contract.id)
    service = ProposalService(db_session)
    proposal = service.create(
        _proposal_create(
            document.id,
            target_type="work_unit",
            contract_id=contract.id,
            proposed_fields=_work_unit_fields(contract.id, date="2026-03-05"),
        ),
        ADMIN,
    )
    # An already-live day on the same contract and date, committed independently of
    # the proposal above.
    db_session.add(
        WorkUnit(
            contract_id=contract.id,
            date=date(2026, 3, 5),
            quantity=Decimal("1.00"),
            description="Giornata già registrata",
            status="proposed",
        )
    )
    db_session.commit()
    approvals_before = db_session.query(Approval).filter_by(contract_id=contract.id).count()

    with pytest.raises(Conflict):
        service.accept(proposal.id, ProposalAccept(), ADMIN)

    approvals_after = db_session.query(Approval).filter_by(contract_id=contract.id).count()
    assert approvals_after == approvals_before

    reloaded = service.get(proposal.id, ADMIN)
    assert reloaded.status == "pending"
    assert reloaded.result_id is None


def test_to_refused_work_unit_accept_keeps_what_the_caller_had_pending(db_session: Session) -> None:
    """The refusal rolls back to its own SAVEPOINT: a row the caller flushed before calling
    `accept` is still there afterwards, which a `session.rollback()` would have discarded."""
    contract = _contract(db_session)
    document = _document(db_session, contract_id=contract.id)
    service = ProposalService(db_session)
    proposal = service.create(
        _proposal_create(
            document.id,
            target_type="work_unit",
            contract_id=contract.id,
            proposed_fields=_work_unit_fields(contract.id, date="2026-03-05"),
        ),
        ADMIN,
    )
    db_session.add(
        WorkUnit(
            contract_id=contract.id,
            date=date(2026, 3, 5),
            quantity=Decimal("1.00"),
            description="Giornata già registrata",
            status="proposed",
        )
    )
    db_session.commit()
    pending = make_customer(db_session)

    with pytest.raises(Conflict):
        service.accept(proposal.id, ProposalAccept(), ADMIN)

    assert db_session.get(Customer, pending.id) is not None
    assert service.get(proposal.id, ADMIN).status == "pending"


def test_a_second_decision_on_a_proposal_loaded_before_the_first_is_refused(
    db_session: Session,
) -> None:
    """Another transaction accepted the proposal after this session had loaded it: the
    locked re-read overwrites the loaded copy, so the late accept finds `accepted` and
    creates no second contract, instead of acting on the stale `pending`."""
    from sqlalchemy import update

    customer = make_customer(db_session)
    document = _document(db_session, customer_id=customer.id)
    service = ProposalService(db_session)
    proposal = service.create(
        _proposal_create(
            document.id, target_type="contract", proposed_fields=_contract_fields(customer.id)
        ),
        ADMIN,
    )
    loaded = db_session.get(Proposal, proposal.id)
    assert loaded is not None and loaded.status == "pending"
    db_session.execute(
        update(Proposal)
        .where(Proposal.id == proposal.id)
        .values(status="accepted")
        .execution_options(synchronize_session=False)
    )
    contracts_before = db_session.query(Contract).count()

    with pytest.raises(Conflict):
        service.accept(proposal.id, ProposalAccept(), ADMIN)

    assert db_session.query(Contract).count() == contracts_before


# ---- the database CHECK, bypassing the service entirely -----------------------------


def test_the_database_refuses_to_work_unit_proposal_with_no_contract_id(
    db_session: Session,
) -> None:
    """Done-when: the CHECK tying `contract_id`'s nullability to `target_type` is
    enforced by the database, not only by `ProposalService.create`'s friendly
    pre-check -- a direct ORM insert that skips the service hits it too."""
    document = _document(db_session, customer_id=make_customer(db_session).id)
    with pytest.raises(IntegrityError):
        db_session.add(
            Proposal(
                document_id=document.id,
                contract_id=None,
                target_type="work_unit",
                proposed_fields={},
                excerpt="qualcosa",
                confidence=Decimal("0.50"),
            )
        )
        db_session.flush()


def test_the_database_allows_to_contract_proposal_with_no_contract_id(
    db_session: Session,
) -> None:
    """The CHECK's asymmetry, asserted directly: `'contract'` is the one
    `target_type` allowed to omit `contract_id` -- first intake has no contract row
    to point at yet."""
    document = _document(db_session, customer_id=make_customer(db_session).id)
    proposal = Proposal(
        document_id=document.id,
        contract_id=None,
        target_type="contract",
        proposed_fields={},
        excerpt="qualcosa",
        confidence=Decimal("0.50"),
    )
    db_session.add(proposal)
    db_session.flush()
    assert proposal.contract_id is None


# ---- excerpt verifiability (excerpt_type = 'quoted') -------------------------------


def test_to_quoted_excerpt_is_verifiable_against_the_documents_own_text(
    db_session: Session, tmp_path: object
) -> None:
    """mastro's own reasoning for `quoted` (spec §10): "a span the application
    located in the document's own text (verifiable, searchable)". Proved here
    against the real extraction pipeline (`DocumentService.extract_text`), not a
    string the test only asserts is equal to itself."""
    customer = make_customer(db_session)
    storage = LocalFileStorage(str(tmp_path))  # type: ignore[arg-type]
    doc_service = DocumentService(db_session, storage)
    created = doc_service.create(
        DocumentCreate(customer_id=customer.id, type="contract", title="Contratto firmato"),
        ADMIN,
    )
    excerpt = "Il contratto ha durata di dodici mesi rinnovabili."
    doc_service.add_version(created.id, minimal_pdf([excerpt]), "application/pdf", ADMIN)

    proposal = ProposalService(db_session).create(
        _proposal_create(
            created.id,
            target_type="contract",
            proposed_fields=_contract_fields(customer.id),
            excerpt=excerpt,
            excerpt_type="quoted",
        ),
        ADMIN,
    )

    read = doc_service.extract_text(created.id, None, ADMIN)
    assert proposal.excerpt_type == "quoted"
    assert proposal.excerpt in read.text
