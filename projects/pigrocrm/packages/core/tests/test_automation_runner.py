"""**Criteria 8 and 9.** The two rules, and the four reasons one may decline.

Everything here runs inside the caller's transaction: the runner never commits, so every
test flushes and reads back rather than committing. That is not a testing convenience, it
is the property under test -- §9.3's answer to "what happens if an automation fails
halfway" is that there is no halfway.

`§9.5`'s fourth surface is the one that usually goes missing, so it gets the most tests:
without an activity for the *non*-execution, "it did not fire" and "it was not supposed to
fire" are the same empty screen. The defect this file is built to catch is a **fifth**,
silent path -- a condition that falls through, or an exception swallowed, and the run ends
having neither moved anything nor said why. Three things close it: the scenario table
below is parametrised over `get_args(AutomationSkipReason)`, so a declared reason nobody
can reach fails; `AutomationOutcome` refuses to exist with neither a move nor a reason; and
`test_a_triggered_run_always_ends_in_a_move_or_a_named_reason` drives every scenario
through the public method and counts the activities.
"""

import inspect
from collections.abc import Callable
from typing import get_args

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.activities.repository import ActivityRepository
from pigrocrm.core.actor import Actor
from pigrocrm.core.automations.config_service import AutomationConfigService
from pigrocrm.core.automations.runner import AutomationOutcome, AutomationRunner
from pigrocrm.core.automations.schemas import (
    KIND_NOT_EXECUTED,
    KIND_STAGE_MOVED,
    AutomationConfigUpdate,
    AutomationSkipReason,
)
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.documents.models import Document
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.pipeline.schemas import PipelineStageRead
from pigrocrm.core.pipeline.service import PipelineService

ADMIN = Actor(id=uuid7(), type="user", role="admin")
SEED = Actor(id=None, type="system", role="admin")


@pytest.fixture
def stages(db_session: Session) -> dict[str, PipelineStageRead]:
    PipelineService(db_session).seed_defaults(SEED)
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
        probability=10,
        custom_fields={},
    )
    db_session.add(row)
    db_session.flush()
    return row


def _offer(db_session: Session, deal: Deal, status: str) -> Document:
    document = Document(
        deal_id=deal.id,
        type="offer",
        title="Offerta impianti",
        status=status,
        current_version=1,
        custom_fields={},
    )
    db_session.add(document)
    db_session.flush()
    return document


def _kinds(db_session: Session, kind: str) -> list[Activity]:
    return ActivityRepository(db_session).by_kind([kind], limit=20)


# -- A1 --------------------------------------------------------------------------


def test_a1_moves_the_deal_to_won(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    document = _offer(db_session, deal, "accepted")
    AutomationRunner(db_session).on_offer_state_changed(document, "sent", ADMIN)
    db_session.flush()
    assert deal.pipeline_stage_id == stages["vinto"].id
    assert deal.probability == 100


def test_a1_writes_exactly_one_activity_attributed_to_the_system(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    """§16 criterion 8. One entry, `actor_type='system'`, and `triggered_by` naming the
    human -- the timeline says "the system did it" *and* "because you accepted that
    offer". `Actor.system()` carries `id=None`, which is why the trigger's actor lives in
    the payload."""
    document = _offer(db_session, deal, "accepted")
    AutomationRunner(db_session).on_offer_state_changed(document, "sent", ADMIN)
    db_session.flush()

    moved = _kinds(db_session, KIND_STAGE_MOVED)
    assert len(moved) == 1
    entry = moved[0]
    assert entry.entity_type == "deal"
    assert entry.entity_id == deal.id
    assert entry.actor_type == "system"
    assert entry.actor_id is None
    assert entry.payload["rule"] == "A1"
    assert entry.payload["document_id"] == str(document.id)
    assert entry.payload["triggered_by"] == str(ADMIN.id)
    assert entry.payload["da"] == "Lead"
    assert entry.payload["a"] == "Vinto"


def test_a1_does_not_write_a_second_stage_changed_entry(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    """§9.3: one entry for the movement, not two. Calling `move_stage` from the runner
    would produce a parallel `stage_changed` and the timeline would show one movement
    twice."""
    document = _offer(db_session, deal, "accepted")
    AutomationRunner(db_session).on_offer_state_changed(document, "sent", ADMIN)
    db_session.flush()
    assert _kinds(db_session, "stage_changed") == []


def test_to_1_ignores_to_transition_that_is_not_sent_to_accepted(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    """`rejected` moves nothing: an offer refused is almost always followed by a revision,
    and marking the deal lost would force reopening it to tell the truth (§9.2)."""
    document = _offer(db_session, deal, "rejected")
    AutomationRunner(db_session).on_offer_state_changed(document, "sent", ADMIN)
    db_session.flush()
    assert deal.pipeline_stage_id == stages["lead"].id
    assert _kinds(db_session, KIND_STAGE_MOVED) == []
    # Nor is it a *skipped* automation: nothing was supposed to happen.
    assert _kinds(db_session, KIND_NOT_EXECUTED) == []


def test_a1_on_an_offer_without_a_deal_does_nothing(db_session: Session) -> None:
    customer = Customer(legal_name="Cliente Srl", country="IT", custom_fields={})
    db_session.add(customer)
    db_session.flush()
    document = Document(
        customer_id=customer.id,
        type="offer",
        title="Offerta",
        status="accepted",
        current_version=1,
        custom_fields={},
    )
    db_session.add(document)
    db_session.flush()

    AutomationRunner(db_session).on_offer_state_changed(document, "sent", ADMIN)
    db_session.flush()
    assert _kinds(db_session, KIND_STAGE_MOVED) == []
    assert _kinds(db_session, KIND_NOT_EXECUTED) == []


def test_a1_on_an_offer_whose_deal_was_soft_deleted_records_nothing(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    """The other "there was nothing to move" case, and the reason it is not one of the
    four: the four reasons describe an automation declining, and here no rule was ever
    selected. A soft-deleted deal is the same class of non-event as an offer attached to a
    customer -- recording `target_stage_missing` would be a lie about *which* thing is
    missing, and it is the target stage's name."""
    from datetime import UTC, datetime

    document = _offer(db_session, deal, "accepted")
    deal.deleted_at = datetime.now(UTC)
    db_session.flush()

    AutomationRunner(db_session).on_offer_state_changed(document, "sent", ADMIN)
    db_session.flush()
    assert _kinds(db_session, KIND_STAGE_MOVED) == []
    assert _kinds(db_session, KIND_NOT_EXECUTED) == []


def test_a1_resolves_by_code_and_not_by_name(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    """The stage renamed, the automation unaffected. This is what `code` is for
    (slice 1 §5.4, residuo R11)."""
    won = db_session.get(PipelineStage, stages["vinto"].id)
    assert won is not None
    won.nome = "Chiuso positivo"
    db_session.flush()

    document = _offer(db_session, deal, "accepted")
    AutomationRunner(db_session).on_offer_state_changed(document, "sent", ADMIN)
    db_session.flush()
    assert deal.pipeline_stage_id == won.id


def test_a1_falls_back_to_the_single_won_stage_when_the_code_is_gone(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    won = db_session.get(PipelineStage, stages["vinto"].id)
    assert won is not None
    won.code = None
    db_session.flush()

    document = _offer(db_session, deal, "accepted")
    AutomationRunner(db_session).on_offer_state_changed(document, "sent", ADMIN)
    db_session.flush()
    assert deal.pipeline_stage_id == won.id


def test_a1_refuses_to_guess_between_two_won_stages(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    """Residuo **R14**: nothing forbids two `type='won'` stages, and no migration in this
    slice adds a constraint -- it would refuse data an installation may have created for a
    reason. So the automation does not guess; it declines and says why."""
    won = db_session.get(PipelineStage, stages["vinto"].id)
    assert won is not None
    won.code = None
    db_session.add(PipelineStage(nome="Vinto bis", position=6, default_probability=100, type="won"))
    db_session.flush()

    document = _offer(db_session, deal, "accepted")
    AutomationRunner(db_session).on_offer_state_changed(document, "sent", ADMIN)
    db_session.flush()

    assert deal.pipeline_stage_id == stages["lead"].id
    skipped = _kinds(db_session, KIND_NOT_EXECUTED)
    assert len(skipped) == 1
    assert skipped[0].payload["reason"] == "target_stage_ambiguous"
    assert skipped[0].payload["rule"] == "A1"


def test_a1_declines_when_there_is_no_won_stage_at_all(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    """§16 criterion 7's second half: accepting the offer **succeeds**, and the
    non-execution is recorded."""
    won = db_session.get(PipelineStage, stages["vinto"].id)
    assert won is not None
    db_session.delete(won)
    db_session.flush()

    document = _offer(db_session, deal, "accepted")
    AutomationRunner(db_session).on_offer_state_changed(document, "sent", ADMIN)
    db_session.flush()

    skipped = _kinds(db_session, KIND_NOT_EXECUTED)
    assert skipped[0].payload["reason"] == "target_stage_missing"


def test_a1_on_a_deal_already_won_is_a_recorded_no_op(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    """§16 criterion 8: idempotence is *inherited*, not added (§9.4). The effect is a
    state, not an increment, so a second accepted offer produces one move and one recorded
    no-op -- and no execution-log table is needed to know that."""
    deal.pipeline_stage_id = stages["vinto"].id
    db_session.flush()

    document = _offer(db_session, deal, "accepted")
    AutomationRunner(db_session).on_offer_state_changed(document, "sent", ADMIN)
    db_session.flush()

    assert _kinds(db_session, KIND_STAGE_MOVED) == []
    skipped = _kinds(db_session, KIND_NOT_EXECUTED)
    assert skipped[0].payload["reason"] == "already_in_state"


def test_a1_declines_when_the_rule_is_switched_off(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    """§16 criterion 9. Disabled means disabled, and it says so rather than staying
    silent -- otherwise "off" and "broken" look identical."""
    AutomationConfigService(db_session).update_automation_config(
        AutomationConfigUpdate(a1_wins_accepted_offer_deal=False), ADMIN
    )

    document = _offer(db_session, deal, "accepted")
    AutomationRunner(db_session).on_offer_state_changed(document, "sent", ADMIN)
    db_session.flush()

    assert deal.pipeline_stage_id == stages["lead"].id
    skipped = _kinds(db_session, KIND_NOT_EXECUTED)
    assert skipped[0].payload["reason"] == "rule_disabled"


def test_switching_a1_off_leaves_a2_alone(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    """The two switches are two switches. Reading the wrong column is a defect that looks
    exactly like a working automation until somebody disables the other rule."""
    AutomationConfigService(db_session).update_automation_config(
        AutomationConfigUpdate(a1_wins_accepted_offer_deal=False), ADMIN
    )
    document = _offer(db_session, deal, "sent")
    AutomationRunner(db_session).on_offer_state_changed(document, "draft", ADMIN)
    db_session.flush()
    assert deal.pipeline_stage_id == stages["offer"].id


# -- A2 --------------------------------------------------------------------------


def test_a2_advances_the_deal_to_the_offer_stage(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    document = _offer(db_session, deal, "sent")
    AutomationRunner(db_session).on_offer_state_changed(document, "draft", ADMIN)
    db_session.flush()
    assert deal.pipeline_stage_id == stages["offer"].id


def test_a2_never_moves_a_deal_backwards(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    """§9.2: a deal already in Negoziazione must not retreat because a second offer was
    sent. An automation that moves things backwards gets switched off on day one."""
    deal.pipeline_stage_id = stages["negoziazione"].id
    db_session.flush()

    document = _offer(db_session, deal, "sent")
    AutomationRunner(db_session).on_offer_state_changed(document, "draft", ADMIN)
    db_session.flush()

    assert deal.pipeline_stage_id == stages["negoziazione"].id
    skipped = _kinds(db_session, KIND_NOT_EXECUTED)
    assert skipped[0].payload["reason"] == "already_in_state"


def test_to_2_has_no_type_fallback(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    """§9.2, the asymmetry that is easy to miss: "Offerta" is `open` like every other open
    stage, so there is nothing for a `type` fallback to select. Two rules with the same
    shape and two different resolutions -- confusing them would move a deal into some
    arbitrary open stage."""
    offer_stage = db_session.get(PipelineStage, stages["offer"].id)
    assert offer_stage is not None
    offer_stage.code = None
    db_session.flush()

    document = _offer(db_session, deal, "sent")
    AutomationRunner(db_session).on_offer_state_changed(document, "draft", ADMIN)
    db_session.flush()

    assert deal.pipeline_stage_id == stages["lead"].id
    skipped = _kinds(db_session, KIND_NOT_EXECUTED)
    assert skipped[0].payload["reason"] == "target_stage_missing"


def test_a2_is_not_triggered_by_a_reopened_offer(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    """`inviata -> bozza` is a legal transition (`OFFER_TRANSITIONS`), and it is not a
    send."""
    document = _offer(db_session, deal, "draft")
    AutomationRunner(db_session).on_offer_state_changed(document, "sent", ADMIN)
    db_session.flush()
    assert deal.pipeline_stage_id == stages["lead"].id
    assert _kinds(db_session, KIND_STAGE_MOVED) == []


def test_a2_respects_its_own_switch(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    AutomationConfigService(db_session).update_automation_config(
        AutomationConfigUpdate(a2_advances_sent_offer_deal=False), ADMIN
    )
    document = _offer(db_session, deal, "sent")
    AutomationRunner(db_session).on_offer_state_changed(document, "draft", ADMIN)
    db_session.flush()

    assert deal.pipeline_stage_id == stages["lead"].id
    assert _kinds(db_session, KIND_NOT_EXECUTED)[0].payload["reason"] == "rule_disabled"


def test_switching_a2_off_leaves_a1_alone(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    AutomationConfigService(db_session).update_automation_config(
        AutomationConfigUpdate(a2_advances_sent_offer_deal=False), ADMIN
    )
    document = _offer(db_session, deal, "accepted")
    AutomationRunner(db_session).on_offer_state_changed(document, "sent", ADMIN)
    db_session.flush()
    assert deal.pipeline_stage_id == stages["vinto"].id


# -- the four declared reasons, enumerated ---------------------------------------
#
# One scenario per value of `AutomationSkipReason`, and the tests below are parametrised
# over `get_args(...)` rather than over this table's keys. That direction matters: a fifth
# reason added to the Literal without a scenario fails immediately, and a reason nobody can
# actually reach fails too. A table parametrised over itself would prove neither.

Scenario = Callable[[Session, Deal, dict[str, PipelineStageRead]], tuple[Document, str]]


def _scenario_stage_absent(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> tuple[Document, str]:
    won = db_session.get(PipelineStage, stages["vinto"].id)
    assert won is not None
    db_session.delete(won)
    db_session.flush()
    return _offer(db_session, deal, "accepted"), "sent"


def _scenario_stage_ambiguous(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> tuple[Document, str]:
    won = db_session.get(PipelineStage, stages["vinto"].id)
    assert won is not None
    won.code = None
    db_session.add(PipelineStage(nome="Vinto bis", position=6, default_probability=100, type="won"))
    db_session.flush()
    return _offer(db_session, deal, "accepted"), "sent"


def _scenario_already_in_state(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> tuple[Document, str]:
    deal.pipeline_stage_id = stages["vinto"].id
    db_session.flush()
    return _offer(db_session, deal, "accepted"), "sent"


def _scenario_rule_disabled(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> tuple[Document, str]:
    AutomationConfigService(db_session).update_automation_config(
        AutomationConfigUpdate(a1_wins_accepted_offer_deal=False), ADMIN
    )
    return _offer(db_session, deal, "accepted"), "sent"


SCENARIOS: dict[str, Scenario] = {
    "target_stage_missing": _scenario_stage_absent,
    "target_stage_ambiguous": _scenario_stage_ambiguous,
    "already_in_state": _scenario_already_in_state,
    "rule_disabled": _scenario_rule_disabled,
}


@pytest.mark.parametrize("reason", sorted(get_args(AutomationSkipReason)))
def test_each_declared_reason_is_reachable_and_named_in_the_activity(
    reason: str, db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    """The four values are a design, not a vocabulary. If one of them cannot be produced by
    any input, it is decoration; if a fifth appears in the Literal without a scenario here,
    this test says so before anybody has to notice it in production."""
    assert reason in SCENARIOS, f"no scenario reaches {reason!r}"
    document, previous = SCENARIOS[reason](db_session, deal, stages)
    AutomationRunner(db_session).on_offer_state_changed(document, previous, ADMIN)
    db_session.flush()

    skipped = _kinds(db_session, KIND_NOT_EXECUTED)
    assert len(skipped) == 1
    assert skipped[0].payload["reason"] == reason
    assert skipped[0].payload["rule"] == "A1"
    assert skipped[0].entity_id == deal.id
    assert skipped[0].actor_type == "system"
    assert skipped[0].payload["triggered_by"] == str(ADMIN.id)
    assert _kinds(db_session, KIND_STAGE_MOVED) == []


@pytest.mark.parametrize("reason", sorted(get_args(AutomationSkipReason)))
def test_a_triggered_run_always_ends_in_a_move_or_a_named_reason(
    reason: str, db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    """The fifth-path guard, stated as a count. A trigger fired; exactly one activity of
    the two automation kinds must exist afterwards, and if it is a non-execution its
    `reason` must be one of the four. A condition that falls through silently produces
    zero, and that is the defect."""
    document, previous = SCENARIOS[reason](db_session, deal, stages)
    AutomationRunner(db_session).on_offer_state_changed(document, previous, ADMIN)
    db_session.flush()

    entries = _kinds(db_session, KIND_STAGE_MOVED) + _kinds(db_session, KIND_NOT_EXECUTED)
    assert len(entries) == 1
    entry = entries[0]
    if entry.kind == KIND_NOT_EXECUTED:
        assert entry.payload["reason"] in get_args(AutomationSkipReason)


def test_an_outcome_cannot_decline_without_saying_why() -> None:
    """The fifth path made unrepresentable rather than merely tested. `moved=False` with
    no reason is the silent non-execution §9.5 exists to prevent, so the dataclass refuses
    to hold it -- and a `ValueError` raised inside the runner is an *undeclared*
    exception, which propagates and rolls the trigger back. A silent skip therefore cannot
    reach a user as a successfully accepted offer."""
    with pytest.raises(ValueError, match="reason"):
        AutomationOutcome(rule="A1", moved=False, reason=None)
    with pytest.raises(ValueError, match="reason"):
        AutomationOutcome(rule="A1", moved=True, reason="already_in_state")
    # The two legal shapes, for contrast.
    assert AutomationOutcome(rule="A1", moved=True, reason=None).moved
    assert AutomationOutcome(rule="A2", moved=False, reason="already_in_state").reason


# -- the exception policy --------------------------------------------------------


def test_the_runner_never_commits(
    db_session: Session, deal: Deal, stages: dict[str, PipelineStageRead]
) -> None:
    """The property everything else rests on. If the runner committed, the offer's own
    state change would be persisted before the trigger finished -- and §9.3's "there is no
    halfway" would be false."""
    document = _offer(db_session, deal, "accepted")
    AutomationRunner(db_session).on_offer_state_changed(document, "sent", ADMIN)
    db_session.rollback()
    assert db_session.get(Deal, deal.id) is None


def test_an_undeclared_exception_propagates(
    db_session: Session,
    deal: Deal,
    stages: dict[str, PipelineStageRead],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The distinction that keeps this code from lying. The runner absorbs four *declared*
    domain conditions. Anything else -- a database that cannot write -- propagates and
    rolls the trigger back, because that is not "an automation that did not fire"."""

    def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(DealService, "set_stage_in_transaction", explode)
    document = _offer(db_session, deal, "accepted")

    with pytest.raises(RuntimeError, match="disk on fire"):
        AutomationRunner(db_session).on_offer_state_changed(document, "sent", ADMIN)


def test_no_exception_handler_swallows_anything_in_the_runner() -> None:
    """The fifth silent path in its other spelling. The four reasons are produced by
    explicit `return`s, never by an `except`: a bare or broad handler here would turn a
    real failure into a silent non-execution -- which is precisely the difference between
    "it did not fire" and "the database is unwritable" that §9.3 refuses to blur."""
    import ast
    from pathlib import Path

    import pigrocrm.core.automations.runner as runner_module

    source = Path(runner_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    handlers = [node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)]
    assert handlers == [], "the runner must not catch anything: every non-domain error propagates"


def test_the_runner_exposes_exactly_one_public_method() -> None:
    public = {
        name
        for name, member in inspect.getmembers(AutomationRunner, predicate=inspect.isfunction)
        if not name.startswith("_") and member.__qualname__.startswith("AutomationRunner.")
    }
    assert public == {"on_offer_state_changed"}


def test_the_runner_never_authorises() -> None:
    """No `actor.require_*` anywhere in the file. The trigger already authorised, and a
    runner that could elevate would make accepting an offer a way to write to a deal the
    actor cannot otherwise touch."""
    import ast
    from pathlib import Path

    import pigrocrm.core.automations.runner as runner_module

    tree = ast.parse(Path(runner_module.__file__).read_text(encoding="utf-8"))
    calls = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr.startswith("require_")
    ]
    assert calls == [], calls
