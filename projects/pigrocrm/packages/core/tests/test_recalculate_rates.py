"""A real error exists: somebody typed 80 instead of 180 and notices two weeks later.
Denying it would produce hand corrections row by row, which is worse. So there is one
way to touch the past -- and it is explicit, admin-only, audited per entry, absent from
MCP, and it never reaches an hour that has been invoiced. Criterion 3."""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.errors import Conflict, PermissionDenied
from pigrocrm.core.timetracking.locks import PeriodLockService
from pigrocrm.core.timetracking.schemas import (
    PeriodLockCreate,
    RecalculateRatesRequest,
    TimeEntryCreate,
)
from pigrocrm.core.timetracking.service import TimeEntryService

WRITER = Actor(id=None, type="user", role="collaborator")
READER = Actor(id=None, type="user", role="readonly")


def _log(service, deal_id, user_id, day, hours="8.00"):
    return service.create(
        TimeEntryCreate(
            deal_id=deal_id,
            user_id=user_id,
            data=date(2026, 3, day),
            hours=Decimal(hours),
            description=f"Giorno {day}",
        ),
        WRITER,
    )


def test_it_rewrites_the_interval_and_records_one_activity_per_entry(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    admin = Actor(id=seeded_user_id, type="user", role="admin")
    deal = db_session.get(Deal, seeded_deal_id)
    deal.hourly_rate = Decimal("80.000000")
    db_session.flush()
    service = TimeEntryService(db_session)
    inside = [_log(service, seeded_deal_id, seeded_user_id, day) for day in (2, 3, 4)]
    outside = _log(service, seeded_deal_id, seeded_user_id, 25)

    deal.hourly_rate = Decimal("180.000000")
    db_session.flush()
    touched = service.recalculate_rates(
        seeded_deal_id, RecalculateRatesRequest(da=date(2026, 3, 1), a=date(2026, 3, 10)), admin
    )
    assert touched == 3

    for entry in inside:
        refreshed = service.get(entry.id, READER)
        assert refreshed.applied_rate == Decimal("180.000000")
        assert refreshed.rate_source == "deal"
        assert refreshed.line_value == Decimal("1440.00")
        kinds = [e.kind for e in ActivityService(db_session).timeline("time_entry", entry.id)]
        assert "rates_recalculated" in kinds
        payload = next(
            e.payload
            for e in ActivityService(db_session).timeline("time_entry", entry.id)
            if e.kind == "rates_recalculated"
        )
        assert payload["rate_before"] == "80.000000"
        assert payload["rate_after"] == "180.000000"

    # Outside the interval, untouched.
    assert service.get(outside.id, READER).applied_rate == Decimal("80.000000")


def test_an_entry_with_a_rate_written_on_it_keeps_that_rate(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    """A `manual` rate is a decision (an explicit zero is "free work on purpose"): the
    recalculation re-resolves the entries whose rate came from a level, not that one."""
    admin = Actor(id=seeded_user_id, type="user", role="admin")
    deal = db_session.get(Deal, seeded_deal_id)
    deal.hourly_rate = Decimal("80.000000")
    db_session.flush()
    service = TimeEntryService(db_session)
    free = service.create(
        TimeEntryCreate(
            deal_id=seeded_deal_id,
            user_id=seeded_user_id,
            data=date(2026, 3, 2),
            hours=Decimal("2.00"),
            description="Omaggio",
            applied_rate=Decimal("0.000000"),
        ),
        WRITER,
    )
    resolved = _log(service, seeded_deal_id, seeded_user_id, 3)

    deal.hourly_rate = Decimal("180.000000")
    db_session.flush()
    touched = service.recalculate_rates(
        seeded_deal_id, RecalculateRatesRequest(da=date(2026, 3, 1), a=date(2026, 3, 10)), admin
    )

    assert touched == 1
    kept = service.get(free.id, READER)
    assert (kept.applied_rate, kept.rate_source) == (Decimal("0.000000"), "manual")
    assert service.get(resolved.id, READER).applied_rate == Decimal("180.000000")


def test_a_billed_entry_in_the_interval_refuses_the_whole_call_and_changes_nothing(
    db_session: Session,
    seeded_deal_id: UUID,
    seeded_user_id: UUID,
    issued_invoice_line_id: UUID,
) -> None:
    """All-or-nothing, and verified by re-reading the others after the refusal -- a
    partial rewrite would leave the interval in a state nobody chose."""
    admin = Actor(id=seeded_user_id, type="user", role="admin")
    deal = db_session.get(Deal, seeded_deal_id)
    deal.hourly_rate = Decimal("80.000000")
    db_session.flush()
    service = TimeEntryService(db_session)
    entries = [_log(service, seeded_deal_id, seeded_user_id, day) for day in (2, 3, 4)]
    # A line of a genuinely issued invoice: those figures have been handed to a client,
    # which is the whole reason this call refuses. A draft's would not refuse, and since
    # 4B-3 made the column a foreign key it could not even be a stand-in UUID.
    line_id = issued_invoice_line_id
    db_session.execute(
        text("UPDATE time_entries SET invoice_line_id = :line WHERE id = :id"),
        {"line": line_id, "id": entries[1].id},
    )
    db_session.flush()

    deal.hourly_rate = Decimal("180.000000")
    db_session.flush()
    with pytest.raises(Conflict) as excinfo:
        service.recalculate_rates(
            seeded_deal_id,
            RecalculateRatesRequest(da=date(2026, 3, 1), a=date(2026, 3, 10)),
            admin,
        )
    assert excinfo.value.details["entries_invoiced"] == 1
    assert excinfo.value.details["before_line"] == str(line_id)

    db_session.rollback()
    for entry in entries:
        assert service.get(entry.id, READER).applied_rate == Decimal("80.000000")


def test_a_collaborator_cannot_call_it(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    """Rewriting what already-done work was worth is closer to configuration than to
    writing an entity -- slice 1 §6.3's reading, which slice 3 §11 applied to
    issuing."""
    with pytest.raises(PermissionDenied):
        TimeEntryService(db_session).recalculate_rates(
            seeded_deal_id,
            RecalculateRatesRequest(da=date(2026, 3, 1), a=date(2026, 3, 10)),
            WRITER,
        )


def test_it_will_not_rewrite_inside_a_closed_period(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    """The lock and the recalculation are the two ways the past can move, and they must
    not have a gap between them: a closed month is closed to this too."""
    admin = Actor(id=seeded_user_id, type="user", role="admin")
    service = TimeEntryService(db_session)
    _log(service, seeded_deal_id, seeded_user_id, 2)
    PeriodLockService(db_session).close_period(PeriodLockCreate(year=2026, month=3), admin)
    with pytest.raises(Conflict):
        service.recalculate_rates(
            seeded_deal_id,
            RecalculateRatesRequest(da=date(2026, 3, 1), a=date(2026, 3, 10)),
            admin,
        )


def test_an_inverted_interval_is_a_validation_failure(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    from pigrocrm.core.errors import ValidationFailed

    admin = Actor(id=seeded_user_id, type="user", role="admin")
    with pytest.raises(ValidationFailed) as excinfo:
        TimeEntryService(db_session).recalculate_rates(
            seeded_deal_id,
            RecalculateRatesRequest(da=date(2026, 3, 10), a=date(2026, 3, 1)),
            admin,
        )
    assert excinfo.value.details["field"] == "a"
