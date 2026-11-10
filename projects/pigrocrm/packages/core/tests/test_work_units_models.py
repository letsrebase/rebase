"""REB-359's own "what the implementing issues must test" for the day lifecycle
(design spec §14): every legal edge accepted, every illegal edge rejected, the
automatic redirect and recovery, the append-only log surviving a run of transitions
without losing an intermediate state, approval immutability, and the partial index.

Writes go straight through the ORM/raw SQL, deliberately bypassing
`WorkUnitService`, to prove the Done-when's own claim: "a direct SQL UPDATE outside
the service layer obeys the same rules as the API" -- the trigger is what enforces
this, not a service-level check anything could route around.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fakes.customer_fixtures import make_customer
from sqlalchemy import RowMapping, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.documents.models import Document
from pigrocrm.core.work_units.models import (
    WORK_UNIT_ENTRY_STATUSES,
    WORK_UNIT_STATUSES,
    WORK_UNIT_TRANSITIONS,
    Approval,
    WorkUnit,
)


def _contract(db_session: Session, *, requires_prior_approval: bool = False) -> Contract:
    contract = Contract(
        customer_id=make_customer(db_session).id,
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
    approval = Approval(
        contract_id=contract.id,
        channel="email",
        sender="cliente@example.test",
        received_at=datetime(2026, 3, 1, 9, 0),
        document_id=_document(db_session, contract).id,
        excerpt="Confermiamo le giornate di marzo.",
        origin={"kind": "manual"},
    )
    db_session.add(approval)
    db_session.flush()
    return approval


def _work_unit(db_session: Session, contract: Contract, **overrides: object) -> WorkUnit:
    payload: dict[str, object] = {
        "contract_id": contract.id,
        "date": date(2026, 3, 5),
        "quantity": Decimal("1.00"),
        "description": "Giornata di consulenza",
        "status": "worked",
    }
    payload.update(overrides)
    work_unit = WorkUnit(**payload)  # type: ignore[arg-type]
    db_session.add(work_unit)
    db_session.flush()
    # `work_unit_enforce_state_machine` can rewrite `status` in flight (the redirect,
    # spec §5); refresh so the in-memory object reflects what was actually written,
    # not merely what was requested.
    db_session.refresh(work_unit)
    return work_unit


def _transitions(db_session: Session, work_unit_id: UUID) -> list[RowMapping]:
    return list(
        db_session.execute(
            text(
                "SELECT id, work_unit_id, previous_status, new_status, actor, reason, seq "
                "FROM work_unit_transitions WHERE work_unit_id = :wid ORDER BY seq"
            ),
            {"wid": work_unit_id},
        ).mappings()
    )


# ---- entry states -------------------------------------------------------------------


@pytest.mark.parametrize("status", sorted(WORK_UNIT_ENTRY_STATUSES))
def test_every_legal_entry_state_is_accepted_on_insert(db_session: Session, status: str) -> None:
    contract = _contract(db_session)
    work_unit = _work_unit(db_session, contract, status=status)
    assert work_unit.status == status


@pytest.mark.parametrize(
    "status",
    sorted(set(WORK_UNIT_TRANSITIONS) - WORK_UNIT_ENTRY_STATUSES - {"worked_without_approval"}),
)
def test_every_non_entry_state_is_refused_on_insert(db_session: Session, status: str) -> None:
    contract = _contract(db_session)
    with pytest.raises(IntegrityError):
        _work_unit(db_session, contract, status=status)


def test_worked_without_approval_cannot_be_named_directly_on_insert(
    db_session: Session,
) -> None:
    """The flagged state is reachable only through the redirect -- a caller can never
    manufacture it by naming it outright."""
    contract = _contract(db_session, requires_prior_approval=True)
    with pytest.raises(IntegrityError):
        _work_unit(db_session, contract, status="worked_without_approval")


# ---- the state graph, edge by edge ---------------------------------------------------

# A legal chain of stato-hops from an entry state that reaches each state at least
# once -- used to *set up* a test at the right origin before exercising the one edge
# under test. `worked_without_approval` is reached by inserting straight into
# `'worked'` on a contract that requires prior approval, with no approval linked
# (the redirect); its own edge back to `'worked'` is the recovery, which needs an
# `approval_id` write rather than a plain `status` assignment, so it is exercised only
# by the dedicated recovery tests above, never through this generic path.
_PATH_TO: dict[str, tuple[str, ...]] = {
    "proposed": ("proposed",),
    "approved": ("proposed", "approved"),
    "worked": ("proposed", "worked"),
    "worked_without_approval": ("worked",),
    "invoiced": ("proposed", "worked", "invoiced"),
    "paid": ("proposed", "worked", "invoiced", "paid"),
    "disputed": ("proposed", "worked", "disputed"),
    "revoked": ("proposed", "revoked"),
    "rejected": ("proposed", "rejected"),
    "non_billable": ("proposed", "worked", "non_billable"),
}


def _drive_to(
    db_session: Session, contract: Contract, target_status: str, *, day: date
) -> WorkUnit:
    path = _PATH_TO[target_status]
    work_unit = _work_unit(db_session, contract, status=path[0], date=day)
    for step in path[1:]:
        work_unit.status = step
        db_session.flush()
    db_session.refresh(work_unit)
    assert work_unit.status == target_status, (
        f"expected {target_status}, landed on {work_unit.status}"
    )
    return work_unit


def test_the_transition_graph_names_every_state_as_an_origin() -> None:
    """Independent of the parametrized sweeps below, which derive their cases from
    `WORK_UNIT_TRANSITIONS` itself and so cannot catch a state missing as a *key*
    entirely (a terminal state still needs an empty `frozenset()` entry, not
    absence) -- this is what actually caught `disputed` being dropped from the
    dict during review, silently making every disputed day permanently stuck."""
    assert set(WORK_UNIT_TRANSITIONS) == set(WORK_UNIT_STATUSES)


# The one edge excluded everywhere below: reached only by linking an approval, not by
# assigning `status` directly (`test_linking_an_approval_recovers_...` above covers it).
_RECOVERY_EDGE = ("worked_without_approval", "worked")

LEGAL_EDGES = sorted(
    (origin, target)
    for origin, targets in WORK_UNIT_TRANSITIONS.items()
    for target in targets
    if (origin, target) != _RECOVERY_EDGE
)


@pytest.mark.parametrize("origin,target", LEGAL_EDGES)
def test_every_legal_edge_is_accepted(db_session: Session, origin: str, target: str) -> None:
    contract = _contract(db_session, requires_prior_approval=(origin == "worked_without_approval"))
    work_unit = _drive_to(db_session, contract, origin, day=date(2026, 3, 10))
    work_unit.status = target
    db_session.flush()
    db_session.refresh(work_unit)
    assert work_unit.status == target


ILLEGAL_EDGES = sorted(
    (origin, target)
    for origin in WORK_UNIT_TRANSITIONS
    for target in WORK_UNIT_TRANSITIONS
    if origin != target
    and target not in WORK_UNIT_TRANSITIONS[origin]
    and (origin, target) != _RECOVERY_EDGE
)


@pytest.mark.parametrize("origin,target", ILLEGAL_EDGES)
def test_every_illegal_edge_is_refused(db_session: Session, origin: str, target: str) -> None:
    contract = _contract(db_session, requires_prior_approval=(origin == "worked_without_approval"))
    work_unit = _drive_to(db_session, contract, origin, day=date(2026, 3, 11))
    with pytest.raises(IntegrityError):
        work_unit.status = target
        db_session.flush()


# ---- the automatic redirect and recovery (0013) --------------------------------------


def test_worked_with_no_approval_on_to_requiring_contract_is_redirected(
    db_session: Session,
) -> None:
    contract = _contract(db_session, requires_prior_approval=True)
    work_unit = _work_unit(db_session, contract, status="worked", approval_id=None)
    assert work_unit.status == "worked_without_approval"


def test_worked_with_an_approval_on_to_requiring_contract_is_not_redirected(
    db_session: Session,
) -> None:
    contract = _contract(db_session, requires_prior_approval=True)
    approval = _approval(db_session, contract)
    work_unit = _work_unit(db_session, contract, status="worked", approval_id=approval.id)
    assert work_unit.status == "worked"


def test_worked_on_to_not_requiring_contract_is_never_redirected(db_session: Session) -> None:
    contract = _contract(db_session, requires_prior_approval=False)
    work_unit = _work_unit(db_session, contract, status="worked", approval_id=None)
    assert work_unit.status == "worked"


def test_linking_an_approval_recovers_the_flagged_state_automatically(
    db_session: Session,
) -> None:
    """The recovery half: the caller only sets `approval_id`, never `status` -- the
    trigger itself flips it back to 'lavorato' (spec §5)."""
    contract = _contract(db_session, requires_prior_approval=True)
    work_unit = _work_unit(db_session, contract, status="worked", approval_id=None)
    assert work_unit.status == "worked_without_approval"

    approval = _approval(db_session, contract)
    work_unit.approval_id = approval.id
    db_session.flush()
    db_session.refresh(work_unit)

    assert work_unit.status == "worked"


def test_the_redirect_also_fires_on_update_not_only_insert(db_session: Session) -> None:
    contract = _contract(db_session, requires_prior_approval=True)
    work_unit = _work_unit(db_session, contract, status="proposed")
    work_unit.status = "worked"
    db_session.flush()
    db_session.refresh(work_unit)
    assert work_unit.status == "worked_without_approval"


# ---- the partial unique index --------------------------------------------------------


def test_two_live_work_units_on_the_same_contract_and_date_are_refused(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    _work_unit(db_session, contract, date=date(2026, 5, 1), status="proposed")
    with pytest.raises(IntegrityError):
        _work_unit(db_session, contract, date=date(2026, 5, 1), status="proposed")


def test_two_work_units_on_different_dates_are_both_accepted(db_session: Session) -> None:
    contract = _contract(db_session)
    first = _work_unit(db_session, contract, date=date(2026, 5, 1), status="proposed")
    second = _work_unit(db_session, contract, date=date(2026, 5, 2), status="proposed")
    assert first.id != second.id


def test_revoking_a_day_frees_its_date_for_a_fresh_proposal(db_session: Session) -> None:
    contract = _contract(db_session)
    first = _work_unit(db_session, contract, date=date(2026, 5, 1), status="proposed")
    first.status = "revoked"
    db_session.flush()
    second = _work_unit(db_session, contract, date=date(2026, 5, 1), status="proposed")
    assert second.id != first.id


def test_to_non_billable_day_still_occupies_its_date(db_session: Session) -> None:
    """`non_billable` is excluded from nothing: it is still real, recorded work."""
    contract = _contract(db_session)
    work_unit = _work_unit(db_session, contract, date=date(2026, 5, 1), status="worked")
    work_unit.status = "non_billable"
    db_session.flush()
    with pytest.raises(IntegrityError):
        _work_unit(db_session, contract, date=date(2026, 5, 1), status="proposed")


# ---- the append-only transition log ---------------------------------------------------


def test_insert_writes_exactly_one_transition_with_a_null_predecessor(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    work_unit = _work_unit(db_session, contract, status="proposed")
    rows = _transitions(db_session, work_unit.id)
    assert len(rows) == 1
    assert rows[0]["previous_status"] is None
    assert rows[0]["new_status"] == "proposed"
    # `seq` is a real Postgres identity column, global to the table and -- like any
    # sequence -- exempt from transactional rollback, so its absolute value drifts
    # across this file's other tests; only that it exists and orders correctly
    # (proven below, across several transitions of one day) is this row's own to show.
    assert isinstance(rows[0]["seq"], int)


def test_a_run_of_transitions_logs_every_intermediate_state_in_order(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    work_unit = _work_unit(db_session, contract, status="proposed")
    for status in ("approved", "worked", "invoiced", "paid"):
        work_unit.status = status
        db_session.flush()

    rows = _transitions(db_session, work_unit.id)
    assert [r["new_status"] for r in rows] == [
        "proposed",
        "approved",
        "worked",
        "invoiced",
        "paid",
    ]
    assert [r["previous_status"] for r in rows] == [
        None,
        "proposed",
        "approved",
        "worked",
        "invoiced",
    ]
    assert [r["seq"] for r in rows] == sorted(r["seq"] for r in rows)


def test_editing_to_field_other_than_status_writes_no_transition(db_session: Session) -> None:
    contract = _contract(db_session)
    work_unit = _work_unit(db_session, contract, status="proposed")
    work_unit.note = "aggiornamento"
    db_session.flush()
    rows = _transitions(db_session, work_unit.id)
    assert len(rows) == 1  # only the INSERT


def test_actor_and_reason_reach_the_log_through_session_local_settings(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    work_unit = _work_unit(db_session, contract, status="proposed")
    db_session.execute(
        text("SELECT set_config('pigrocrm.actor', :a, true)"),
        {"a": '{"kind": "human", "id": "' + str(uuid4()) + '"}'},
    )
    db_session.execute(
        text("SELECT set_config('pigrocrm.reason', :m, true)"),
        {"m": "il cliente ha confermato via email"},
    )
    work_unit.status = "approved"
    db_session.flush()

    rows = _transitions(db_session, work_unit.id)
    latest = rows[-1]
    assert latest["actor"]["kind"] == "human"
    assert latest["reason"] == "il cliente ha confermato via email"


def test_unset_actor_and_reason_fall_back_rather_than_failing(db_session: Session) -> None:
    """The log must never go silently incomplete (spec §5, `0012...sql:97-105`)."""
    contract = _contract(db_session)
    work_unit = _work_unit(db_session, contract, status="proposed")
    rows = _transitions(db_session, work_unit.id)
    assert rows[0]["actor"] == {"kind": "system"}
    assert rows[0]["reason"] == "nessun motivo fornito"


# ---- immutability ---------------------------------------------------------------------


def test_an_approval_cannot_be_updated(db_session: Session) -> None:
    contract = _contract(db_session)
    approval = _approval(db_session, contract)
    approval.excerpt = "versione corretta"
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_a_transition_row_cannot_be_updated(db_session: Session) -> None:
    contract = _contract(db_session)
    work_unit = _work_unit(db_session, contract, status="proposed")
    with pytest.raises(IntegrityError):
        db_session.execute(
            text("UPDATE work_unit_transitions SET reason = 'rewritten' WHERE work_unit_id = :wid"),
            {"wid": work_unit.id},
        )


def test_a_transition_row_cannot_be_deleted(db_session: Session) -> None:
    contract = _contract(db_session)
    work_unit = _work_unit(db_session, contract, status="proposed")
    with pytest.raises(IntegrityError):
        db_session.execute(
            text("DELETE FROM work_unit_transitions WHERE work_unit_id = :wid"),
            {"wid": work_unit.id},
        )


def test_a_correction_to_an_approval_is_a_new_row_not_an_edit(db_session: Session) -> None:
    contract = _contract(db_session)
    original = _approval(db_session, contract)
    corrected = Approval(
        contract_id=contract.id,
        channel=original.channel,
        sender=original.sender,
        received_at=original.received_at,
        document_id=original.document_id,
        excerpt="testo corretto",
        origin={"kind": "manual"},
    )
    db_session.add(corrected)
    db_session.flush()
    assert corrected.id != original.id
    assert original.excerpt != corrected.excerpt
