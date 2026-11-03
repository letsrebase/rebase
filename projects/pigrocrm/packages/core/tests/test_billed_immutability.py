"""§4.3, at full strength. The rule attaches to the **state of the invoice**, not to the
mere presence of the link, because a draft is still freely editable: while the invoice is
a draft the hours stay modifiable, and slice 3's wholesale line replacement unbinds and
rebinds them without orphans. The moment `issue()` commits, the bound hours are frozen --
without `issue()` having had to know they exist.

Part of criterion 5.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, ImmutableField
from pigrocrm.core.timetracking.models import TimeEntry
from pigrocrm.core.timetracking.schemas import TimeEntryCreate, TimeEntryUpdate
from pigrocrm.core.timetracking.service import TimeEntryService, billed_entry_ids

WRITER = Actor(id=None, type="user", role="collaborator")


def _bind(session: Session, entry_id: UUID, line_id: UUID) -> TimeEntry:
    """Raw SQL, because nothing writes this column until 4B's `bind_time_to_invoice`.
    The ORM copy is expired afterwards so the helper's caller reads the bound state."""
    session.execute(
        text("UPDATE time_entries SET invoice_line_id = :line WHERE id = :id"),
        {"line": line_id, "id": entry_id},
    )
    session.flush()
    entry = session.get(TimeEntry, entry_id)
    assert entry is not None
    session.refresh(entry)
    return entry


def _log(service: TimeEntryService, deal_id: UUID, user_id: UUID) -> UUID:
    entry = service.create(
        TimeEntryCreate(
            deal_id=deal_id,
            user_id=user_id,
            data=date(2026, 3, 4),
            hours=Decimal("2.00"),
            description="x",
        ),
        WRITER,
    )
    return entry.id


def test_an_hour_on_a_draft_invoice_is_still_editable(
    db_session: Session,
    seeded_deal_id: UUID,
    seeded_user_id: UUID,
    draft_invoice_line_id: UUID,
) -> None:
    service = TimeEntryService(db_session)
    entry_id = _log(service, seeded_deal_id, seeded_user_id)
    entry = _bind(db_session, entry_id, draft_invoice_line_id)

    assert billed_entry_ids(db_session, [entry]) == set()
    assert service.update(
        entry_id, TimeEntryUpdate(hours=Decimal("3.00")), WRITER
    ).hours == Decimal("3.00")


def test_deleting_an_hour_bound_to_a_draft_is_refused_in_words_not_in_a_500(
    db_session: Session,
    seeded_deal_id: UUID,
    seeded_user_id: UUID,
    draft_invoice_line_id: UUID,
) -> None:
    """Editable is not deletable, and the two guards ask different questions.

    `billed_entry_ids` answers "bound to a line of an *issued* invoice", which is the
    right question for freezing a rate and the wrong one for deletion: the database's
    own `ck_time_entries_billed_not_deleted` is `deleted_at IS NULL OR invoice_line_id
    IS NULL`, and does not care what state the invoice is in.

    While `soft_delete` guarded on the narrower one, an entry bound to a *draft* line --
    which is exactly what `bind_time_to_invoice` produces, in the ordinary window between
    choosing the hours and issuing -- passed the check and hit the CHECK at commit. The
    caller got a 500 carrying a `CheckViolation` instead of the sentence the service had
    already written for them. Found by review and reproduced against real Postgres; this
    is the test that was missing, because the neighbouring one exercises the constraint
    through raw SQL and never through the service.
    """
    service = TimeEntryService(db_session)
    entry_id = _log(service, seeded_deal_id, seeded_user_id)
    entry = _bind(db_session, entry_id, draft_invoice_line_id)
    # The precondition that makes this test about the gap rather than about the freeze.
    assert billed_entry_ids(db_session, [entry]) == set()

    with pytest.raises(Conflict) as caught:
        service.soft_delete(entry_id, WRITER)
    assert "scollegala" in caught.value.message

    # Refused, not merely complained about: the row is still there and still undeleted.
    db_session.rollback()
    survivor = db_session.get(TimeEntry, entry_id)
    assert survivor is not None and survivor.deleted_at is None


def test_issuing_the_invoice_freezes_the_bound_hour(
    db_session: Session,
    seeded_deal_id: UUID,
    seeded_user_id: UUID,
    issued_invoice_line_id: UUID,
) -> None:
    service = TimeEntryService(db_session)
    entry_id = _log(service, seeded_deal_id, seeded_user_id)
    _bind(db_session, entry_id, issued_invoice_line_id)

    for field, value in [
        ("hours", Decimal("3.00")),
        ("data", date(2026, 3, 5)),
        ("applied_rate", Decimal("99.000000")),
        ("description", "Altro"),
    ]:
        with pytest.raises(ImmutableField) as excinfo:
            service.update(entry_id, TimeEntryUpdate(**{field: value}), WRITER)
        assert excinfo.value.details["field"] == field

    # `internal_notes` stays mutable: it appears on no artefact.
    assert (
        service.update(entry_id, TimeEntryUpdate(internal_notes="ok"), WRITER).internal_notes
        == "ok"
    )

    with pytest.raises(Conflict):
        service.soft_delete(entry_id, WRITER)


# The other half of §4.3 -- `ck_time_entries_billed_not_deleted`, which forbids deleting
# *any* entry bound to a line, a draft's included -- is deliberately wider than the rule
# narrowed here, because a constraint that distinguished invoice state would have to read
# another table, i.e. be a trigger, and this project keeps that kind of invisible logic
# out of the database. It is proven in `test_timetracking_models.py`, against a draft
# line, and is not repeated here.


def test_deleting_an_invoice_line_unbinds_rather_than_orphans(
    db_session: Session, seeded_entry_id: UUID, draft_invoice_line_id: UUID
) -> None:
    """`ON DELETE SET NULL`, which is what lets slice 3 replace a draft's lines wholesale
    without slice 4 participating in that transaction."""
    _bind(db_session, seeded_entry_id, draft_invoice_line_id)
    db_session.execute(
        text("DELETE FROM invoice_lines WHERE id = :line"), {"line": draft_invoice_line_id}
    )
    db_session.flush()
    remaining = db_session.execute(
        text("SELECT invoice_line_id FROM time_entries WHERE id = :id"), {"id": seeded_entry_id}
    ).scalar_one()
    assert remaining is None


def test_a_line_that_does_not_exist_cannot_be_bound_at_all(
    db_session: Session, seeded_entry_id: UUID
) -> None:
    """The foreign key itself. Until migration 0012 the column was a bare `Uuid`, so any
    128 bits at all looked like a billed hour -- which is precisely what four of slice
    4A's own tests relied on."""
    with pytest.raises(IntegrityError):
        db_session.execute(
            text("UPDATE time_entries SET invoice_line_id = :line WHERE id = :id"),
            {"line": uuid4(), "id": seeded_entry_id},
        )
        db_session.flush()
    db_session.rollback()


def test_an_annulled_invoice_does_not_freeze_its_hours(
    db_session: Session, seeded_entry_id: UUID, annulled_invoice_line_id: UUID
) -> None:
    """Only `issued` freezes. An annulled invoice keeps its number but not its revenue
    (§7.1) -- it is the struck-through page of a paper register -- so the hours behind it
    are CRM data again and can be re-invoiced."""
    entry = _bind(db_session, seeded_entry_id, annulled_invoice_line_id)
    assert billed_entry_ids(db_session, [entry]) == set()


def test_a_proforma_never_freezes_anything(
    db_session: Session,
    seeded_entry_id: UUID,
    proforma_invoice_line_id: UUID,
) -> None:
    """A proforma never touches the register (slice 3 §5): `confirmed` is not
    `issued`, and the join filters on `type` as well as `status` so a proforma's own
    states can never be mistaken for an emission."""
    entry = _bind(db_session, seeded_entry_id, proforma_invoice_line_id)
    assert billed_entry_ids(db_session, [entry]) == set()


def test_one_query_however_many_entries(
    db_session: Session,
    seeded_deal_id: UUID,
    seeded_user_id: UUID,
    issued_invoice_line_id: UUID,
) -> None:
    """`deal_summary` calls this with every entry of a deal, so a per-row lookup would
    make the P&L quadratic in a deal's hours."""
    service = TimeEntryService(db_session)
    bound = [_log(service, seeded_deal_id, seeded_user_id) for _ in range(3)]
    free = _log(service, seeded_deal_id, seeded_user_id)
    entries = []
    for entry_id in bound:
        entries.append(_bind(db_session, entry_id, issued_invoice_line_id))
    unbound = db_session.get(TimeEntry, free)
    assert unbound is not None
    entries.append(unbound)

    statements: list[str] = []

    def _record(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
        statements.append(statement)

    event.listen(db_session.bind, "before_cursor_execute", _record)
    try:
        assert billed_entry_ids(db_session, entries) == set(bound)
    finally:
        event.remove(db_session.bind, "before_cursor_execute", _record)
    assert len([s for s in statements if "invoice_lines" in s]) == 1
