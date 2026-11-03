"""The mutation half of a stage change, without the transaction or the timeline.

Four things it deliberately does **not** do, each of which is a test below: authorise,
record, commit, or reach the database on its own. It is called by `AutomationRunner`
inside `set_offer_state`'s transaction, where the authorisation has already happened
(`actor.require_write`) and the commit belongs to the trigger.

`_settle_probability` is reused rather than reimplemented, which is the only reason that
function is at module level: the invariant "won at 60% is unreachable" has to hold on this
path too, and an invariant reachable through two paths must live in one function.
"""

import inspect

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.activities.repository import ActivityRepository
from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import today_local
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.pipeline.schemas import PipelineStageRead
from pigrocrm.core.pipeline.service import PipelineService

ADMIN = Actor(id=None, type="system", role="admin")


@pytest.fixture
def stages(db_session: Session) -> dict[str, PipelineStageRead]:
    PipelineService(db_session).seed_defaults(ADMIN)
    return {s.code: s for s in PipelineService(db_session).list() if s.code is not None}


@pytest.fixture
def deal(db_session: Session, stages: dict[str, PipelineStageRead]) -> Deal:
    customer = Customer(legal_name="Cliente Srl", country="IT", custom_fields={})
    db_session.add(customer)
    db_session.flush()
    row = Deal(
        nome="Impianto",
        customer_id=customer.id,
        pipeline_stage_id=stages["lead"].id,
        probability=50,
        custom_fields={},
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_it_moves_the_deal_and_settles_the_probability(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    DealService(db_session).set_stage_in_transaction(deal, stages["won"])
    assert deal.pipeline_stage_id == stages["won"].id
    # "Won at 60%" stays unreachable through this path too.
    assert deal.probability == 100


def test_it_settles_the_closure_date(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    DealService(db_session).set_stage_in_transaction(deal, stages["won"])
    assert deal.closed_at == today_local()


def test_it_records_nothing(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    """The runner writes its own single entry (§9.5). Two entries for one movement is the
    defect §9.3 names explicitly."""
    DealService(db_session).set_stage_in_transaction(deal, stages["won"])
    db_session.flush()
    assert ActivityRepository(db_session).by_kind(["stage_changed"], limit=10) == []


def test_it_does_not_commit(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    """The property the whole design rests on: rolling back must undo it.

    `db_session` joins the outer transaction with `create_savepoint`, so a `commit()`
    inside the method would release the savepoint that holds the fixture's own insert and
    open a fresh one -- and the `rollback()` below would then find the deal still there,
    already moved. The two outcomes are therefore distinguishable, which is the only
    reason this assertion is worth writing: without a commit the deal is gone, with one it
    is present and `vinto`.
    """
    DealService(db_session).set_stage_in_transaction(deal, stages["won"])
    db_session.rollback()
    reloaded = db_session.get(Deal, deal.id)
    # The row itself vanished with the rollback of the fixture's own insert, which is
    # itself proof that nothing was committed.
    assert reloaded is None or reloaded.pipeline_stage_id == stages["lead"].id


def test_it_does_not_check_authorisation() -> None:
    """Not an oversight -- the point. The authorisation is the trigger's
    (`set_offer_state` calls `actor.require_write`), and a `readonly` actor never reaches
    the runner. Re-authorising here would be harmless; *elevating* here would make
    accepting an offer a way to write to a deal the actor could not otherwise touch, and
    the architecture test in `test_in_transaction_callers.py` is what keeps this method
    out of a router."""
    # No actor parameter exists to pass; the signature is the assertion.
    signature = inspect.signature(DealService.set_stage_in_transaction)
    assert "actor" not in signature.parameters
    assert list(signature.parameters) == ["self", "deal", "stage"]


def test_moving_back_to_an_open_stage_clears_the_closure_date(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    service = DealService(db_session)
    service.set_stage_in_transaction(deal, stages["won"])
    service.set_stage_in_transaction(deal, stages["negotiation"])
    assert deal.closed_at is None


def test_a_correction_between_two_terminal_stages_keeps_the_original_closure_date(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    """The case that catches reading the previous stage *after* the assignment.

    `_settle_closure_date` decides on `previous.type`, so if `pipeline_stage_id` is
    written before the previous stage is looked up, `previous` is the target and every
    move looks like a correction between two terminal stages -- which silently stops
    stamping `closed_at` at all. Here the previous stage really is terminal, so the stamp
    must survive `vinto -> perso` untouched: it is a correction of *which* outcome, not a
    second closure.
    """
    service = DealService(db_session)
    service.set_stage_in_transaction(deal, stages["won"])
    stamped = deal.closed_at
    assert stamped is not None
    service.set_stage_in_transaction(deal, stages["lost"])
    assert deal.closed_at == stamped
    assert deal.probability == 0
