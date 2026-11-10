"""The two date columns §4.1 adds, and the asymmetry that decides their backfill.

`Date`, not `timestamptz`, against slice 1 §5's general convention and for slice 3 §6.2's
precise reason: a date that decides which period a figure falls in is not an instant. All
of this slice's period filters land on `Date` columns -- `invoices.issue_date`,
`costs.date`, `time_entries.date`, `closed_at` -- and no mixed comparison exists anywhere.

Two of the tests below freeze `frozen_clock.FROZEN_INSTANT` rather than comparing against
`today_local()`. Comparing against `today_local()` is what the brief asked for and it
proves less than it looks: `date.today()` returns the same answer for twenty-three hours
of every day, so such an assertion passes against the exact defect commit 875a1f9 had to
fix three times. The frozen instant is 00:30 on 1 January in Rome, 23:30 on 31 December in
UTC, where the two clocks disagree about the day, the month and the year at once.
"""

from datetime import date
from pathlib import Path

import pytest
from frozen_clock import TODAY_IN_ITALY, TODAY_OF_PROCESS, freeze
from sqlalchemy import Date, DateTime
from sqlalchemy.orm import Session

import pigrocrm.core.deals.service as deals_service_module
import pigrocrm.core.documents.service as documents_service_module
from pigrocrm.core.actor import Actor
from pigrocrm.core.db import today_local
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.documents.models import Document
from pigrocrm.core.documents.schemas import DocumentCreate, DocumentRead
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.pipeline.schemas import PipelineStageRead
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm.core.storage import LocalFileStorage

ADMIN = Actor(id=None, type="system", role="admin")


@pytest.fixture
def stages(db_session: Session) -> dict[str, PipelineStageRead]:
    PipelineService(db_session).seed_defaults(ADMIN)
    return {
        stage.code: stage for stage in PipelineService(db_session).list() if stage.code is not None
    }


@pytest.fixture
def document_service(db_session: Session, tmp_path: Path) -> DocumentService:
    """Duplicated from `test_documents_service.py` rather than promoted to `conftest.py`.

    The brief suggested promoting it, but the fixture there is named `service`, and a
    fixture called `service` in the shared `conftest.py` would be visible to every test
    root -- silently shadowed in some modules, silently shadowing in others, for two lines
    of construction. A generic name is the wrong thing to make global.
    """
    return DocumentService(db_session, LocalFileStorage(tmp_path))


def _deal(db_session: Session, stage_id: object) -> Deal:
    customer = Organization(legal_name="Cliente Srl", country="IT", custom_fields={})
    db_session.add(customer)
    db_session.flush()
    deal = Deal(
        name="Impianto",
        customer_id=customer.id,
        pipeline_stage_id=stage_id,
        probability=50,
        custom_fields={},
    )
    db_session.add(deal)
    db_session.flush()
    return deal


def _offer(service: DocumentService, db_session: Session) -> DocumentRead:
    customer = Organization(legal_name="Cliente Offerta Srl", country="IT", custom_fields={})
    db_session.add(customer)
    db_session.flush()
    return service.create(
        DocumentCreate(customer_id=customer.id, type="offer", title="Offerta 2026-01"), ADMIN
    )


def test_a_new_deal_has_no_closure_date(
    db_session: Session, stages: dict[str, PipelineStageRead]
) -> None:
    deal = _deal(db_session, stages["lead"].id)
    assert deal.closed_at is None


def test_moving_to_a_won_stage_stamps_the_local_day(
    db_session: Session, stages: dict[str, PipelineStageRead]
) -> None:
    deal = _deal(db_session, stages["lead"].id)
    DealService(db_session).move_stage(deal.id, stages["won"].id, ADMIN)
    db_session.refresh(deal)
    assert deal.closed_at == today_local()


def test_moving_to_a_lost_stage_also_stamps_it(
    db_session: Session, stages: dict[str, PipelineStageRead]
) -> None:
    """`type != 'open'`, not `type == 'won'`: a lost deal is closed too, and the
    conversion rate needs both halves of the denominator."""
    deal = _deal(db_session, stages["lead"].id)
    DealService(db_session).move_stage(deal.id, stages["lost"].id, ADMIN)
    db_session.refresh(deal)
    assert deal.closed_at == today_local()


def test_the_stamped_day_is_italys_own_and_not_the_processs(
    db_session: Session,
    stages: dict[str, PipelineStageRead],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The assertion the two tests above cannot make.

    `deal.closed_at == today_local()` holds just as well against a `date.today()` that
    happens to agree, which it does for twenty-three hours of every day. Frozen at 00:30
    on 1 January in Rome, the process clock says 31 December of the *previous year*, and
    a deal won that night would be counted in the wrong year's conversion rate --
    permanently, because nothing recomputes it.
    """
    freeze(monkeypatch, deals_service_module)
    deal = _deal(db_session, stages["lead"].id)
    DealService(db_session).move_stage(deal.id, stages["won"].id, ADMIN)
    db_session.refresh(deal)
    assert deal.closed_at == TODAY_IN_ITALY
    assert deal.closed_at != TODAY_OF_PROCESS


def test_reopening_a_deal_clears_the_closure_date(
    db_session: Session, stages: dict[str, PipelineStageRead]
) -> None:
    """A reopened deal is not a deal closed in March. Leaving the stamp would put it in
    both the conversion rate and the open pipeline at the same time."""
    service = DealService(db_session)
    deal = _deal(db_session, stages["lead"].id)
    service.move_stage(deal.id, stages["won"].id, ADMIN)
    service.move_stage(deal.id, stages["negotiation"].id, ADMIN)
    db_session.refresh(deal)
    assert deal.closed_at is None


def test_moving_between_two_open_stages_never_stamps_it(
    db_session: Session, stages: dict[str, PipelineStageRead]
) -> None:
    deal = _deal(db_session, stages["lead"].id)
    DealService(db_session).move_stage(deal.id, stages["offer"].id, ADMIN)
    db_session.refresh(deal)
    assert deal.closed_at is None


def test_moving_from_won_to_lost_keeps_the_original_closure_date(
    db_session: Session, stages: dict[str, PipelineStageRead]
) -> None:
    """A correction of *which* terminal state, not a new closure. Restamping would move
    the deal into the month someone fixed the mistake in."""
    service = DealService(db_session)
    deal = _deal(db_session, stages["lead"].id)
    service.move_stage(deal.id, stages["won"].id, ADMIN)
    db_session.refresh(deal)
    original = deal.closed_at
    deal.closed_at = date(2026, 1, 15)  # simulate a closure recorded earlier
    db_session.flush()

    service.move_stage(deal.id, stages["lost"].id, ADMIN)
    db_session.refresh(deal)
    assert deal.closed_at == date(2026, 1, 15), original


def test_reopening_then_closing_again_stamps_the_new_day(
    db_session: Session, stages: dict[str, PipelineStageRead]
) -> None:
    """The three cases compose. A deal that was won, reopened and won again closed on the
    second day, not the first -- otherwise `closed_at` would be a "first ever closed on"
    column, and the conversion rate for the month the deal actually landed in would miss
    it. Distinguishes clearing from a mere `if target.type == 'open': pass`.
    """
    service = DealService(db_session)
    deal = _deal(db_session, stages["lead"].id)
    service.move_stage(deal.id, stages["won"].id, ADMIN)
    db_session.refresh(deal)
    deal.closed_at = date(2026, 1, 15)
    db_session.flush()

    service.move_stage(deal.id, stages["negotiation"].id, ADMIN)
    service.move_stage(deal.id, stages["won"].id, ADMIN)
    db_session.refresh(deal)
    assert deal.closed_at == today_local()


def test_closed_at_is_exposed_on_the_read_schema(
    db_session: Session, stages: dict[str, PipelineStageRead]
) -> None:
    deal = _deal(db_session, stages["lead"].id)
    read = DealService(db_session).move_stage(deal.id, stages["won"].id, ADMIN)
    assert read.closed_at == today_local()


def test_closed_at_is_on_no_write_schema() -> None:
    """Derived, never claimed. `DealUpdate.closed_at` would let a caller assert a closure
    that never happened, straight into the conversion rate."""
    from pigrocrm.core.deals.schemas import DealCreate, DealUpdate

    assert "closed_at" not in DealCreate.model_fields
    assert "closed_at" not in DealUpdate.model_fields


def test_setting_an_offer_state_stamps_status_since(
    db_session: Session, document_service: DocumentService
) -> None:
    """`documents.status_since` is what makes "questa offerta è ferma da N giorni"
    answerable; §4 shows the age in days on the commercial dashboard."""
    document = _offer(document_service, db_session)
    document_service.set_offer_state(document.id, "sent", ADMIN)
    row = db_session.get(Document, document.id)
    assert row is not None
    assert row.status_since == today_local()


def test_status_since_moves_on_every_state_change(
    db_session: Session, document_service: DocumentService
) -> None:
    document = _offer(document_service, db_session)
    document_service.set_offer_state(document.id, "sent", ADMIN)
    row = db_session.get(Document, document.id)
    assert row is not None
    row.status_since = date(2026, 1, 1)
    db_session.flush()

    document_service.set_offer_state(document.id, "accepted", ADMIN)
    db_session.refresh(row)
    assert row.status_since == today_local()


def test_status_since_is_italys_own_day_too(
    db_session: Session, document_service: DocumentService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same reason as `closed_at`: an offer whose state was set at 00:30 CET on 1 January
    would be reported as having been in that state since the previous year."""
    freeze(monkeypatch, documents_service_module)
    document = _offer(document_service, db_session)
    document_service.set_offer_state(document.id, "sent", ADMIN)
    row = db_session.get(Document, document.id)
    assert row is not None
    assert row.status_since == TODAY_IN_ITALY
    assert row.status_since != TODAY_OF_PROCESS


def test_status_since_is_exposed_on_the_read_schema(
    db_session: Session, document_service: DocumentService
) -> None:
    document = _offer(document_service, db_session)
    read = document_service.set_offer_state(document.id, "sent", ADMIN)
    assert read.status_since == today_local()


def test_both_columns_are_date_and_not_timestamp() -> None:
    """Asserted on the type, because the whole argument of §4.1 rests on it and a
    `DateTime` here would still pass every test above -- `Date` and `DateTime` are
    unrelated classes in SQLAlchemy, so `isinstance` really does separate them, and a
    `DateTime` column would compare equal to a `date` in Python while filtering
    differently in Postgres.
    """
    for column in (Deal.__table__.c.closed_at, Document.__table__.c.status_since):
        assert isinstance(column.type, Date), column
        assert not isinstance(column.type, DateTime), column
        assert column.nullable, column


def test_both_columns_are_indexed_in_the_models() -> None:
    """Both are the filter of a period query on a table that grows without bound.
    Declared in `__table_args__` rather than only in the migration, because
    `Base.metadata.create_all` builds the test schema and an index that exists only in a
    migration is an index no test ever sees.
    """
    assert "ix_deals_closed_at" in {index.name for index in Deal.__table__.indexes}
    assert "ix_documents_status_since" in {index.name for index in Document.__table__.indexes}
