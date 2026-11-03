"""**Criterion 7.** The automation is atomic with its trigger.

With an error injected between the automation and the commit, neither thing happened: the
offer is still `sent` and the deal is still in its old stage, verified by re-reading
both rows on a fresh session. This is the assertion that makes "there is no halfway" a
property rather than a claim, and it needs its own committed rows -- `db_session` holds an
outer transaction open, so a rollback inside it cannot be told apart from the fixture's
own cleanup.

Two mistakes are possible here and they are mirror images. The one slice 5 kept producing
was a `session.rollback()` whose scope was **wider** than its author imagined. The one this
file is built to catch is the inverse: a commit whose scope is **narrower** than its
author imagined -- a `commit()` inside `DealService` or inside the runner that persists the
deal's movement on its own, so that a later failure leaves the deal moved and the offer
still `sent`. Re-reading two rows after a successful call cannot see that; only failing
the second half, and watching a concurrent observer mid-transaction, can. Hence
`test_an_error_between_the_automation_and_the_commit_undoes_both` and
`test_a_concurrent_reader_sees_neither_half_until_the_commit`, which is a real race on two
connections with barriers because the transactional `db_session` cannot host one.
"""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import Engine, delete
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.activities.repository import ActivityRepository
from pigrocrm.core.actor import Actor
from pigrocrm.core.automations.models import AutomationConfig
from pigrocrm.core.automations.schemas import KIND_NOT_EXECUTED, KIND_STAGE_MOVED
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.errors import PermissionDenied
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.pipeline.schemas import PipelineStageRead
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=uuid7(), type="user", role="admin")
READONLY = Actor(id=uuid7(), type="user", role="readonly")
SEED = Actor(id=None, type="system", role="admin")

Committed = tuple[Session, Document, Deal, dict[str, PipelineStageRead]]


@pytest.fixture
def committed(db_engine: Engine) -> Iterator[Committed]:
    """Real committed rows, on their own session, removed afterwards.

    Everything this fixture writes is genuinely committed, so the teardown has to be
    exhaustive rather than tidy: `db_engine` is session-scoped and every other test in the
    suite runs against the same database inside a transaction it rolls back. A leftover
    `activities` row would make `test_automation_runner.py`'s "nothing was recorded"
    assertions false, and leftover pipeline stages would make every test that lists the
    pipeline see six stages it did not create. `pipeline_stages` and `automation_config`
    are emptied wholesale because this file is the only place in the suite that commits
    either.
    """
    factory = session_factory(db_engine)
    session = factory()
    PipelineService(session).seed_defaults(SEED)
    stages = {s.code: s for s in PipelineService(session).list() if s.code is not None}

    customer = Customer(legal_name="ATOMIC Cliente", country="IT", custom_fields={})
    session.add(customer)
    session.flush()
    deal = Deal(
        name="ATOMIC Impianto",
        customer_id=customer.id,
        pipeline_stage_id=stages["lead"].id,
        probability=10,
        custom_fields={},
    )
    session.add(deal)
    session.flush()
    document = Document(
        deal_id=deal.id,
        type="offer",
        title="ATOMIC Offerta",
        status="sent",
        current_version=1,
        custom_fields={},
    )
    session.add(document)
    session.commit()
    touched = {customer.id, deal.id, document.id}
    try:
        yield session, document, deal, stages
    finally:
        session.rollback()
        # `activities.entity_id` is a plain UUID, not a foreign key, so nothing cascades.
        session.execute(delete(Activity).where(Activity.entity_id.in_(touched)))
        session.execute(delete(Document).where(Document.title.like("ATOMIC %")))
        session.execute(delete(Deal).where(Deal.name.like("ATOMIC %")))
        session.execute(delete(Customer).where(Customer.legal_name.like("ATOMIC %")))
        session.execute(delete(PipelineStage))
        session.execute(delete(AutomationConfig))
        session.commit()
        session.close()


def _service(session: Session, tmp_path: Path) -> DocumentService:
    return DocumentService(session, LocalFileStorage(tmp_path))


def test_accepting_an_offer_moves_the_deal_in_the_same_transaction(
    db_engine: Engine, committed: Committed, tmp_path: Path
) -> None:
    session, document, deal, stages = committed
    _service(session, tmp_path).set_offer_state(document.id, "accepted", ADMIN)

    with session_factory(db_engine)() as other:
        reread_document = other.get(Document, document.id)
        reread_deal = other.get(Deal, deal.id)
        assert reread_document is not None and reread_deal is not None
        assert reread_document.status == "accepted"
        assert reread_deal.pipeline_stage_id == stages["won"].id


def test_an_error_between_the_automation_and_the_commit_undoes_both(
    db_engine: Engine,
    committed: Committed,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The assertion this file exists for. `ActivityService.record` is the last thing
    before the commit, so failing it is precisely "after the automation, before the
    commit"."""
    session, document, deal, stages = committed
    original = ActivityRepository.add

    calls = {"n": 0}

    def fail_on_the_documents_entry(self: ActivityRepository, activity: Activity) -> Activity:
        calls["n"] += 1
        # The runner's own entry is first; the document's `state_changed` is second.
        if calls["n"] == 2:
            raise RuntimeError("injected failure after the automation")
        return original(self, activity)

    monkeypatch.setattr(ActivityRepository, "add", fail_on_the_documents_entry)

    with pytest.raises(RuntimeError, match="injected failure"):
        _service(session, tmp_path).set_offer_state(document.id, "accepted", ADMIN)
    assert calls["n"] == 2, "the failure was not injected after the automation's own entry"
    session.rollback()

    with session_factory(db_engine)() as other:
        reread_document = other.get(Document, document.id)
        reread_deal = other.get(Deal, deal.id)
        assert reread_document is not None and reread_deal is not None
        # Neither happened. There is no halfway.
        assert reread_document.status == "sent"
        assert reread_deal.pipeline_stage_id == stages["lead"].id
        assert reread_deal.closed_at is None
        assert ActivityRepository(other).by_kind([KIND_STAGE_MOVED], limit=5) == []


def test_a_concurrent_reader_sees_neither_half_until_the_commit(
    db_engine: Engine,
    committed: Committed,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The race that catches a commit whose scope is narrower than its author imagined.

    The previous test proves a rollback undoes both halves; it cannot prove that the deal's
    movement was never *visible* on its own. A stray `commit()` inside `DealService` or
    inside the runner would publish the deal's new stage — and, with it, the document's
    half-written state — before the trigger had finished, and every assertion made after a
    successful call would still pass.

    So the writer is stopped where the damage would be visible: the barrier sits inside
    `ActivityRepository.add` for the document's own `state_changed` entry, which is the
    last statement before `set_offer_state` commits. At that instant the automation has
    already run. A reader on a **different connection** — the only place from which a
    committed row and an uncommitted one look different — must still see `sent` and
    `lead`. Two barriers rather than one: the first releases the reader once the writer is
    in position, the second holds the writer until the reader has finished, so neither
    thread can pass by being slower than the other.
    """
    session, document, deal, stages = committed
    document_id, deal_id = document.id, deal.id
    session.commit()

    factory = session_factory(db_engine)
    original = ActivityRepository.add
    writer_in_position = Barrier(2)
    reader_done = Barrier(2)

    def pause_before_the_commit(self: ActivityRepository, activity: Activity) -> Activity:
        if activity.kind == "state_changed" and activity.entity_type == "document":
            writer_in_position.wait(timeout=30)
            reader_done.wait(timeout=30)
        return original(self, activity)

    monkeypatch.setattr(ActivityRepository, "add", pause_before_the_commit)

    def accept() -> None:
        with factory() as writer:
            _service(writer, tmp_path).set_offer_state(document_id, "accepted", ADMIN)

    def observe() -> tuple[str | None, UUID]:
        writer_in_position.wait(timeout=30)
        try:
            with factory() as reader:
                seen_document = reader.get(Document, document_id)
                seen_deal = reader.get(Deal, deal_id)
                assert seen_document is not None and seen_deal is not None
                return seen_document.status, seen_deal.pipeline_stage_id
        finally:
            reader_done.wait(timeout=30)

    with ThreadPoolExecutor(max_workers=2) as pool:
        accepted = pool.submit(accept)
        observed = pool.submit(observe)
        seen_status, seen_stage_id = observed.result(timeout=60)
        accepted.result(timeout=60)

    assert (seen_status, seen_stage_id) == ("sent", stages["lead"].id), (
        "a concurrent reader saw part of the trigger before it committed: something "
        "inside the runner or DealService commits on its own behalf"
    )

    # And once the trigger has committed, both halves are visible together.
    with factory() as after:
        final_document = after.get(Document, document_id)
        final_deal = after.get(Deal, deal_id)
        assert final_document is not None and final_deal is not None
        assert final_document.status == "accepted"
        assert final_deal.pipeline_stage_id == stages["won"].id


def test_with_the_won_stage_deleted_accepting_still_succeeds(
    db_engine: Engine, committed: Committed, tmp_path: Path
) -> None:
    """§16 criterion 7's second half. A missing target stage is a *declared* condition:
    the offer is accepted, and the non-execution is on the record."""
    session, document, deal, stages = committed
    won = session.get(PipelineStage, stages["won"].id)
    assert won is not None
    session.delete(won)
    session.commit()

    _service(session, tmp_path).set_offer_state(document.id, "accepted", ADMIN)

    with session_factory(db_engine)() as other:
        reread = other.get(Document, document.id)
        assert reread is not None and reread.status == "accepted"
        skipped = ActivityRepository(other).by_kind([KIND_NOT_EXECUTED], limit=5)
        assert skipped[0].payload["reason"] == "target_stage_missing"
        assert skipped[0].entity_id == deal.id


def test_a_readonly_actor_never_reaches_the_runner(
    db_engine: Engine, committed: Committed, tmp_path: Path
) -> None:
    """§16 criterion 8's last sentence. The authorisation is the trigger's, and it is
    checked before anything is mutated -- so no row is touched at all."""
    session, document, deal, stages = committed

    with pytest.raises(PermissionDenied):
        _service(session, tmp_path).set_offer_state(document.id, "accepted", READONLY)
    session.rollback()

    with session_factory(db_engine)() as other:
        reread_document = other.get(Document, document.id)
        reread_deal = other.get(Deal, deal.id)
        assert reread_document is not None and reread_deal is not None
        assert reread_document.status == "sent"
        assert reread_deal.pipeline_stage_id == stages["lead"].id
        assert ActivityRepository(other).by_kind([KIND_STAGE_MOVED], limit=5) == []
        assert ActivityRepository(other).by_kind([KIND_NOT_EXECUTED], limit=5) == []
