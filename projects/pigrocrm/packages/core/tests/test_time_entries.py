from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from orologio import OGGI_IN_ITALIA, congela
from sqlalchemy import text
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.errors import (
    Conflict,
    ImmutableField,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from pigrocrm.core.timetracking import service as timetracking_service_module
from pigrocrm.core.timetracking.locks import PeriodLockService
from pigrocrm.core.timetracking.schemas import (
    PeriodLockCreate,
    TimeEntryCreate,
    TimeEntryListQuery,
    TimeEntryUpdate,
)
from pigrocrm.core.timetracking.service import TimeEntryService

WRITER = Actor(id=None, type="user", role="collaboratore")
READER = Actor(id=None, type="user", role="readonly")


def _create(service, deal_id, user_id, **overrides):
    payload = {
        "deal_id": deal_id,
        "user_id": user_id,
        "data": date(2026, 3, 10),
        "ore": Decimal("3.00"),
        "descrizione": "Sviluppo",
    }
    payload.update(overrides)
    return service.create(TimeEntryCreate(**payload), WRITER)


def test_a_created_entry_carries_its_frozen_rate_and_its_row_value(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    db_session.get(Deal, seeded_deal_id).tariffa_oraria = Decimal("80.000000")
    db_session.flush()
    entry = _create(TimeEntryService(db_session), seeded_deal_id, seeded_user_id)
    assert entry.tariffa_applicata == Decimal("80.000000")
    assert entry.tariffa_origine == "deal"
    # Returned already computed: the browser does no economic arithmetic (§6).
    assert entry.valore_riga == Decimal("240.00")
    assert entry.costo_riga is None
    assert entry.costo_origine == "assente"


def test_a_multi_line_description_keeps_its_newlines_unescaped(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    """Two of the previous system's defects at once. `normalizeSingleLine` kept only the first line
    (`String(value).split(/\\r?\\n/)[0]`), silently dropping the rest; and it applied
    `escapeTypstText` at *write* time, so the stored value was already escaped for one
    target and reached the XLSX escaped and the PDF double-escaped. Here the value is
    stored raw and `escape_for` prepares it at render, once, for the context it lands
    in."""
    hostile = 'Call con @mario su [fase 1] & #2 — "urgente"\nseconda riga\\backslash'
    entry = _create(
        TimeEntryService(db_session), seeded_deal_id, seeded_user_id, descrizione=hostile
    )
    assert entry.descrizione == hostile
    stored = db_session.execute(
        text("SELECT descrizione FROM time_entries WHERE id = :id"), {"id": entry.id}
    ).scalar_one()
    assert stored == hostile


def test_a_future_date_is_refused_but_back_dating_is_not(
    db_session: Session,
    seeded_deal_id: UUID,
    seeded_user_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§6.3. Back-dating with **no year floor**, unlike slice 3's `data_emissione`: here
    you declare when work was done, and a consultant logs Monday on Friday and closes
    December at the end of January. A future hour is not data, it is a forecast, and
    this slice makes no forecasts.

    "Tomorrow" is taken from the product's own clock, frozen, and not from
    `date.today()`. The service compares against `today_local()`, so on a host running
    in UTC -- every CI runner, and `Dockerfile.api` pins no `TZ` -- the two disagree for
    the first hours of each Italian day and `date.today() + 1` is merely *today* in
    Italy, which is legal and is not refused. The test then failed for a reason that has
    nothing to do with the rule it is checking, at 01:02 Rome time on 2026-09-08.
    """
    congela(monkeypatch, timetracking_service_module)
    service = TimeEntryService(db_session)
    _create(service, seeded_deal_id, seeded_user_id, data=date(2019, 7, 1))
    with pytest.raises(ValidationFailed) as excinfo:
        _create(service, seeded_deal_id, seeded_user_id, data=OGGI_IN_ITALIA + timedelta(days=1))
    assert excinfo.value.details["field"] == "data"


def test_a_write_into_a_closed_period_is_refused(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    """The lock, reached through the service rather than only through
    `assert_writable` -- criterion 2's second half."""
    admin = Actor(id=seeded_user_id, type="user", role="admin")
    PeriodLockService(db_session).close_period(PeriodLockCreate(anno=2026, mese=3), admin)
    service = TimeEntryService(db_session)
    with pytest.raises(Conflict) as excinfo:
        _create(service, seeded_deal_id, seeded_user_id, data=date(2026, 3, 10))
    assert excinfo.value.details["mese"] == 3
    assert "marzo 2026" in excinfo.value.message

    # An entry already in an open month cannot be moved into the closed one, nor
    # deleted out of it.
    entry = _create(service, seeded_deal_id, seeded_user_id, data=date(2026, 4, 10))
    with pytest.raises(Conflict):
        service.update(entry.id, TimeEntryUpdate(data=date(2026, 3, 20)), WRITER)
    PeriodLockService(db_session).close_period(PeriodLockCreate(anno=2026, mese=4), admin)
    with pytest.raises(Conflict):
        service.soft_delete(entry.id, WRITER)


def test_a_deactivated_user_cannot_receive_new_hours_but_keeps_the_old_ones(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    """Residual R3, answered concretely (criterion 8). `get_active` on write, plain
    `get` on read: the money really was spent, so the hours stay in the P&L, in the
    aggregates and in the PDF with their owner's name."""
    service = TimeEntryService(db_session)
    existing = _create(service, seeded_deal_id, seeded_user_id, ore=Decimal("12.00"))
    db_session.get(User, seeded_user_id).attivo = False
    db_session.flush()

    with pytest.raises(ValidationFailed) as excinfo:
        _create(service, seeded_deal_id, seeded_user_id)
    assert excinfo.value.details["field"] == "user_id"

    other = User(email=f"altro-{uuid4()}@example.test", password_hash="x", nome="Altro")
    db_session.add(other)
    db_session.flush()
    fresh = service.create(
        TimeEntryCreate(
            deal_id=seeded_deal_id,
            user_id=other.id,
            data=date(2026, 3, 11),
            ore=Decimal("1.00"),
            descrizione="x",
        ),
        WRITER,
    )
    with pytest.raises(ValidationFailed):
        service.update(fresh.id, TimeEntryUpdate(user_id=seeded_user_id), WRITER)

    # And the read path still resolves it.
    assert service.get(existing.id, READER).ore == Decimal("12.00")
    assert service.deal_summary(seeded_deal_id, READER).ore_totali == Decimal("13.00")


def test_logging_on_a_closed_deal_is_allowed_and_leaves_a_trace(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID, seeded_won_stage_id: UUID
) -> None:
    """§4.3's last paragraph. On a won deal the work *begins* at that moment, and on a
    lost one the pre-sales hours are a real cost. Refusing would force reopening the
    deal to tell the truth -- corrupting the pipeline to save the actuals. The service
    warns and records; it does not refuse."""
    db_session.get(Deal, seeded_deal_id).pipeline_stage_id = seeded_won_stage_id
    db_session.flush()
    entry = _create(TimeEntryService(db_session), seeded_deal_id, seeded_user_id)
    kinds = [e.kind for e in ActivityService(db_session).timeline("time_entry", entry.id)]
    assert "time_logged_on_closed_deal" in kinds


def test_an_entry_on_an_issued_invoice_freezes_the_named_fields(
    db_session: Session,
    seeded_deal_id: UUID,
    seeded_user_id: UUID,
    issued_invoice_line_id: UUID,
) -> None:
    """§4.3's table. `note_interne` and `custom_fields` stay mutable because they appear
    on no artefact; `descrizione` is frozen and that is not obvious -- it is the column
    the client reads in the timesheet attached to the invoice, so changing it after
    issue would make the delivered document and the database say two different things.

    A real issued line, not the random UUID this test used to bind: since 4B-3 the
    column is a foreign key and the rule reads the invoice's `stato`, so a stand-in
    would neither insert nor freeze. Which state freezes is `test_billed_immutability`;
    what it freezes is here."""
    service = TimeEntryService(db_session)
    entry = _create(service, seeded_deal_id, seeded_user_id)
    db_session.execute(
        text("UPDATE time_entries SET invoice_line_id = :line WHERE id = :id"),
        {"line": issued_invoice_line_id, "id": entry.id},
    )
    db_session.flush()

    for field, value in [
        ("ore", Decimal("4.00")),
        ("data", date(2026, 3, 12)),
        ("tariffa_applicata", Decimal("90.000000")),
        ("costo_applicato", Decimal("10.000000")),
        ("descrizione", "Altro"),
        ("deal_id", seeded_deal_id),
        ("fatturabile", False),
    ]:
        with pytest.raises(ImmutableField) as excinfo:
            service.update(entry.id, TimeEntryUpdate(**{field: value}), WRITER)
        assert excinfo.value.details["field"] == field

    updated = service.update(entry.id, TimeEntryUpdate(note_interne="da ricontrollare"), WRITER)
    assert updated.note_interne == "da ricontrollare"

    with pytest.raises(Conflict):
        service.soft_delete(entry.id, WRITER)


def test_the_summary_states_the_three_facts_a_report_needs(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    service = TimeEntryService(db_session)
    _create(
        service,
        seeded_deal_id,
        seeded_user_id,
        ore=Decimal("2.00"),
        tariffa_applicata=Decimal("100.000000"),
        costo_applicato=Decimal("30.000000"),
    )
    _create(service, seeded_deal_id, seeded_user_id, ore=Decimal("1.50"))  # no rate at all
    _create(
        service,
        seeded_deal_id,
        seeded_user_id,
        ore=Decimal("1.00"),
        fatturabile=False,
        tariffa_applicata=Decimal("100.000000"),
        costo_applicato=Decimal("30.000000"),
    )

    summary = service.deal_summary(seeded_deal_id, READER)
    assert summary.ore_totali == Decimal("4.50")
    assert summary.ore_fatturabili_non_fatturate == Decimal("3.50")
    # Only the priced, billable, unbilled hours contribute the accrued value; the
    # unpriced 1.50 h is counted separately and never valued at zero.
    assert summary.valore_ore_non_fatturate == Decimal("200.00")
    assert summary.ore_senza_tariffa == 1
    # Labour cost includes the NON-billable hour: an internal meeting costs exactly
    # what it would cost if it were billed, and excluding it would make the deal that
    # demanded more of them look more profitable (§7.1).
    assert summary.costo_lavoro == Decimal("90.00")
    assert summary.stato == "in corso"


def test_the_state_is_derived_never_stored(
    db_session: Session,
    seeded_deal_id: UUID,
    seeded_user_id: UUID,
    seeded_won_stage_id: UUID,
    issued_invoice_line_id: UUID,
) -> None:
    """§7.3's three states, and they come from the data -- a stage type plus whether
    billable unbilled hours exist -- never from a column."""
    service = TimeEntryService(db_session)
    _create(service, seeded_deal_id, seeded_user_id, tariffa_applicata=Decimal("100.000000"))
    assert service.deal_summary(seeded_deal_id, READER).stato == "in corso"

    db_session.get(Deal, seeded_deal_id).pipeline_stage_id = seeded_won_stage_id
    db_session.flush()
    assert service.deal_summary(seeded_deal_id, READER).stato == "da fatturare"

    # "chiuso" is "no billable hour left unbilled", and since 4B-3 only an *issued*
    # invoice bills one: on a draft the deal would still read "da fatturare", which is
    # the honest answer while the document can still change.
    db_session.execute(
        text("UPDATE time_entries SET invoice_line_id = :line WHERE deal_id = :deal"),
        {"line": issued_invoice_line_id, "deal": seeded_deal_id},
    )
    db_session.flush()
    assert service.deal_summary(seeded_deal_id, READER).stato == "chiuso"


def test_soft_delete_is_reversible_and_a_reader_cannot_write(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    service = TimeEntryService(db_session)
    entry = _create(service, seeded_deal_id, seeded_user_id)
    service.soft_delete(entry.id, WRITER)
    assert service.list(TimeEntryListQuery(deal_id=seeded_deal_id), READER).items == []
    restored = service.restore(entry.id, WRITER)
    # `TimeEntryRead` carries no `deleted_at` -- no other entity's Read schema does,
    # and exposing it would invite a client to branch on soft-delete state when list
    # queries already filter it out. Reversibility is proven by visibility instead.
    assert restored.id == entry.id
    visible = service.list(TimeEntryListQuery(deal_id=seeded_deal_id), READER).items
    assert [e.id for e in visible] == [entry.id]
    with pytest.raises(PermissionDenied):
        service.soft_delete(entry.id, READER)


def test_a_dangling_foreign_key_is_not_found_not_an_integrity_error(
    db_session: Session, seeded_user_id: UUID
) -> None:
    with pytest.raises(NotFound):
        TimeEntryService(db_session).create(
            TimeEntryCreate(
                deal_id=uuid4(),
                user_id=seeded_user_id,
                data=date(2026, 3, 1),
                ore=Decimal("1.00"),
                descrizione="x",
            ),
            WRITER,
        )


def test_the_list_is_ordered_by_date_descending(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    """Residual R9 is open in general; a list of hours with no ordering is unusable, so
    these two tables are born ordered."""
    service = TimeEntryService(db_session)
    for day in (5, 20, 12):
        _create(service, seeded_deal_id, seeded_user_id, data=date(2026, 3, day))
    page = service.list(TimeEntryListQuery(deal_id=seeded_deal_id), READER)
    assert [e.data.day for e in page.items] == [20, 12, 5]


def test_the_fatturato_filter_answers_how_much_is_left_to_invoice(
    db_session: Session,
    seeded_deal_id: UUID,
    seeded_user_id: UUID,
    issued_invoice_line_id: UUID,
) -> None:
    """The filter is on the *link*, not on the invoice's state -- one indexed column and
    no join, because this is the list query the weekly "how much do I have to invoice?"
    runs. The entry here is on an issued invoice, so both readings agree and the test
    keeps saying what it always said."""
    service = TimeEntryService(db_session)
    billed = _create(service, seeded_deal_id, seeded_user_id)
    _create(service, seeded_deal_id, seeded_user_id, data=date(2026, 3, 11))
    db_session.execute(
        text("UPDATE time_entries SET invoice_line_id = :line WHERE id = :id"),
        {"line": issued_invoice_line_id, "id": billed.id},
    )
    db_session.flush()
    unbilled = service.list(TimeEntryListQuery(deal_id=seeded_deal_id, fatturato=False), READER)
    assert [e.id for e in unbilled.items] == [
        e.id for e in unbilled.items if e.invoice_line_id is None
    ]
    assert len(unbilled.items) == 1


def test_the_future_limit_is_italys_own_day_not_the_processs(
    db_session: Session,
    seeded_deal_id: UUID,
    seeded_user_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§6.3's limit is a day in the issuer's calendar, so it must be read off Italy's
    clock and not the host's -- see `db/clock.py` and `orologio.py`.

    Frozen at 00:30 on 1 January in Rome, which is still 23:30 on 31 December for a
    process running in UTC (the API image does). Against the host's day, an hour logged
    for the day it is *actually* being logged on is a day in the future and gets refused
    as `data futura`: a legal entry rejected, at the one hour of the day when a
    freelancer closing the year is most likely to be writing it, with a message that
    reads as a product defect. The refusal of tomorrow is asserted in the same test so
    the fix cannot be "stop checking".
    """
    congela(monkeypatch, timetracking_service_module)
    service = TimeEntryService(db_session)

    entry = _create(service, seeded_deal_id, seeded_user_id, data=OGGI_IN_ITALIA)
    assert entry.data == OGGI_IN_ITALIA

    with pytest.raises(ValidationFailed) as excinfo:
        _create(service, seeded_deal_id, seeded_user_id, data=OGGI_IN_ITALIA + timedelta(days=1))
    assert excinfo.value.details["field"] == "data"
