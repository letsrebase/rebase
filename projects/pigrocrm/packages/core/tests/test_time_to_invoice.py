"""**Criterion 5.** Hours become invoice lines, once; and the derived value stops
counting the moment the invoice exists.

The bridge owns no invoicing logic: it builds an `InvoiceCreate` and calls
`InvoiceService`, which stays the sole owner of numbering, fiscal validation, rounding
and freezing (slice 3 §3, §6, §9). This task adds no participant to slice 3's locked
transaction.

Which reading of "already invoiced" each figure uses is the thing this file is most
careful about, because task 4B-3 deliberately left two that differ. The **link-based**
reading is `time_entries.invoice_line_id IS NOT NULL` -- one indexed column, no join --
and it is what the `invoiced` list filter applies. The **invoice-state** reading goes
through `billed_entry_ids`, joins to `invoices` and asks whether the line belongs to an
*issued* one. `bind_time_to_invoice` writes the link, which is what makes the two
readings differ at all; the double-invoicing guard and every P&L figure use the
invoice-state one, because an hour on a *draft* is not revenue yet.
"""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.schemas import BindTimeRequest
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.config import Settings
from pigrocrm.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from pigrocrm.core.invoices.schemas import InvoiceIssue
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.money import line_value, sum_hours, sum_money
from pigrocrm.core.storage.local import LocalFileStorage
from pigrocrm.core.timetracking.billing import TimeBilling
from pigrocrm.core.timetracking.schemas import TimeEntryCreate, TimeEntryRead
from pigrocrm.core.timetracking.service import TimeEntryService

ADMIN = Actor(id=None, type="system", role="admin")
WRITER = Actor(id=None, type="user", role="collaborator")
READER = Actor(id=None, type="user", role="readonly")


def _log(
    session: Session,
    deal_id: UUID,
    user_id: UUID,
    *,
    day: date,
    hours: str,
    rate: str | None,
    billable: bool = True,
) -> TimeEntryRead:
    return TimeEntryService(session).create(
        TimeEntryCreate(
            deal_id=deal_id,
            user_id=user_id,
            date=day,
            hours=Decimal(hours),
            description=f"Attivita' {day.isoformat()}",
            billable=billable,
            applied_rate=Decimal(rate) if rate is not None else None,
        ),
        WRITER,
    )


def _lines(session: Session, invoice_id: UUID) -> list[tuple[UUID, Decimal, Decimal, str]]:
    rows = session.execute(
        text(
            "SELECT id, quantity, unit_price, description FROM invoice_lines "
            "WHERE invoice_id = :inv ORDER BY line_number"
        ),
        {"inv": invoice_id},
    ).all()
    return [(row[0], Decimal(row[1]), Decimal(row[2]), row[3]) for row in rows]


def test_thirty_seven_entries_on_three_rates_and_two_months_become_six_lines(
    db_session: Session,
    billable_deal_id: UUID,
    seeded_user_id: UUID,
    local_storage: LocalFileStorage,
) -> None:
    """One line per `(applied_rate, month)`, with `quantity = Sum(hours)` and
    `unit_price = rate`. Not one line per entry: an invoice with forty lines is
    unreadable for the client, and the detail already has its place -- the timesheet
    attached to it."""
    entries = []
    for month, day_base in ((3, 1), (4, 1)):
        for index, rate in enumerate(("80.000000", "100.000000", "120.000000")):
            for offset in range(6 if (month, index) != (4, 2) else 7):
                entries.append(
                    _log(
                        db_session,
                        billable_deal_id,
                        seeded_user_id,
                        day=date(2026, month, day_base + offset),
                        hours="1.50",
                        rate=rate,
                    )
                )
    assert len(entries) == 37

    invoice = TimeBilling(db_session, local_storage).bind_to_invoice(
        billable_deal_id, BindTimeRequest(entry_ids=[e.id for e in entries]), ADMIN
    )

    lines = _lines(db_session, invoice.id)
    assert len(lines) == 6
    # The six groups spelled out, in the order they must come back: month first, rate
    # within it. Six entries of 1.50 h is 9.00 h; the April 120 group has the extra
    # seventh entry and so 10.50 h. A grouping keyed on anything but
    # `(tariffa, mese)` -- on the entry, on the day, on the rate alone -- produces a
    # different list here, so this one assertion pins the whole rule.
    assert [(quantity, price) for _, quantity, price, _ in lines] == [
        (Decimal("9.00"), Decimal("80")),
        (Decimal("9.00"), Decimal("100")),
        (Decimal("9.00"), Decimal("120")),
        (Decimal("9.00"), Decimal("80")),
        (Decimal("9.00"), Decimal("100")),
        (Decimal("10.50"), Decimal("120")),
    ]
    # `Sum(quantity)` is **exactly** `Sum(hours)` of the bound entries.
    assert sum_hours([quantity for _, quantity, _, _ in lines]) == sum_hours(
        [e.hours for e in entries]
    )

    # Every entry is bound, and bound to the line of *its own* group. The mapping from
    # group to line is positional, so an off-by-one in it would leave every entry bound
    # to some line and every count above still correct -- this join is what catches it.
    bound = db_session.execute(
        text(
            "SELECT t.id, t.applied_rate, t.date, l.unit_price, l.description "
            "FROM time_entries t JOIN invoice_lines l ON l.id = t.invoice_line_id "
            "WHERE t.deal_id = :deal"
        ),
        {"deal": billable_deal_id},
    ).all()
    assert len(bound) == 37
    for _, rate, entry_date, price, description in bound:
        assert Decimal(rate) == Decimal(price)
        assert ("marzo" if entry_date.month == 3 else "aprile") in description

    # The description names the month and the hours, which is what makes a six-line
    # invoice legible next to a timesheet the client can check it against.
    assert any("marzo 2026" in description for *_, description in lines)
    assert any("aprile 2026" in description for *_, description in lines)
    assert all("hours" in description for *_, description in lines)

    # One activity for the whole binding, not thirty-seven: it is a single commercial
    # act over a selection, unlike `recalculate_rates`, which changes each row's meaning
    # individually and therefore records per row.
    activities = db_session.execute(
        text(
            "SELECT count(*) FROM activities WHERE entity_type = 'deal' "
            "AND entity_id = :deal AND kind = 'time_bound_to_invoice'"
        ),
        {"deal": billable_deal_id},
    ).scalar_one()
    assert activities == 1


def test_one_line_per_rate_when_the_monthly_grouping_is_turned_off(
    db_session: Session,
    billable_deal_id: UUID,
    seeded_user_id: UUID,
    local_storage: LocalFileStorage,
) -> None:
    """`group_by_by_month=False` collapses the same hours to one line per rate.

    The monthly cut is what a client usually expects, but it is a presentation choice
    and not a rule, so it is a field on the request rather than a constant -- and the
    same selection has to produce a *different* line count when it is turned off, which
    is the only way to tell an implemented option from an ignored one.
    """
    entries = [
        _log(
            db_session,
            billable_deal_id,
            seeded_user_id,
            day=date(2026, month, 5),
            hours="2.00",
            rate=rate,
        )
        for month in (3, 4)
        for rate in ("80.000000", "100.000000")
    ]
    service = TimeBilling(db_session, local_storage)

    monthly = service.bind_to_invoice(
        billable_deal_id, BindTimeRequest(entry_ids=[e.id for e in entries]), ADMIN
    )
    assert len(_lines(db_session, monthly.id)) == 4

    collapsed = service.bind_to_invoice(
        billable_deal_id,
        BindTimeRequest(entry_ids=[e.id for e in entries], group_by_by_month=False),
        ADMIN,
    )
    lines = _lines(db_session, collapsed.id)
    assert [(quantity, price) for _, quantity, price, _ in lines] == [
        (Decimal("4.00"), Decimal("80")),
        (Decimal("4.00"), Decimal("100")),
    ]
    # No month to name, so the label falls back to a neutral one rather than inventing
    # a period the line does not cover.
    assert all("marzo" not in description for *_, description in lines)


def test_the_derived_value_stops_being_consulted_once_the_invoice_exists(
    db_session: Session,
    billable_deal_id: UUID,
    seeded_user_id: UUID,
    local_storage: LocalFileStorage,
) -> None:
    """**The rounding pin.** Per-entry and per-invoice-line rounding disagree by cents,
    and the spec dissolves the disagreement rather than reconciling it: hours x rate is
    an **estimate** that stops being consulted once an invoice exists (§3 decision 2,
    §6.2's exception, §7.1).

    Constructed so the two genuinely differ: 3 entries of 0.10 h at 33.333333 EUR/h.
    Per entry, `Sum(ROUND(0.10 x 33.333333, 2))` = 3 x 3.33 = **9.99**. As one grouped
    line, `ROUND(0.30 x 33.333333, 2)` = **10.00**. Exactly one cent apart, and the one
    that wins is the invoice's: it is the figure on the document the client received.
    """
    entries = [
        _log(
            db_session,
            billable_deal_id,
            seeded_user_id,
            day=date(2026, 3, day),
            hours="0.10",
            rate="33.333333",
        )
        for day in (1, 2, 3)
    ]
    derived = sum_money([line_value(e.hours, e.applied_rate) for e in entries])
    assert derived == Decimal("9.99")

    billing = TimeBilling(db_session, local_storage)
    invoice = billing.bind_to_invoice(
        billable_deal_id, BindTimeRequest(entry_ids=[e.id for e in entries]), ADMIN
    )
    InvoiceService(db_session, local_storage).issue(invoice.id, InvoiceIssue(), ADMIN)

    issued_taxable_amount = Decimal(
        db_session.execute(
            text("SELECT taxable_amount FROM invoices WHERE id = :id"), {"id": invoice.id}
        ).scalar_one()
    )
    # The two figures, and the size and direction of the disagreement, named outright:
    # the invoice is one cent *above* the sum of the per-entry values.
    assert issued_taxable_amount == Decimal("10.00")
    assert issued_taxable_amount - derived == Decimal("0.01")

    pnl = AnalyticsService(db_session).deal_pnl(billable_deal_id, READER)
    # The invoice's figure, not the sum of the derived ones. This is the assertion the
    # whole rounding question reduces to.
    assert pnl.revenue == Decimal("10.00")
    assert pnl.revenue != derived
    # And the accrued value no longer counts those hours at all: they are invoiced, so
    # `accrued_value` is the revenue alone and carries none of the 9.99 with it.
    assert pnl.billable_uninvoiced_hours == Decimal("0.00")
    assert pnl.accrued_value == Decimal("10.00")


def test_the_draft_is_not_revenue_and_the_estimate_is_still_the_one_consulted(
    db_session: Session,
    billable_deal_id: UUID,
    seeded_user_id: UUID,
    local_storage: LocalFileStorage,
) -> None:
    """The other half of the pin, and the reason the two readings of "already invoiced"
    have to differ.

    Between the bind and the emission the hours carry a `invoice_line_id` -- they are
    `invoiced` under the **link-based** reading the list filter applies -- and yet no
    invoice has been issued, so there is no revenue. The P&L uses the **invoice-state**
    reading and therefore still counts them as billable-and-unbilled, at the derived
    9.99. Under the link-based reading their value would exist in neither figure: work
    done, priced, and invisible until somebody pressed "issue".
    """
    entries = [
        _log(
            db_session,
            billable_deal_id,
            seeded_user_id,
            day=date(2026, 3, day),
            hours="0.10",
            rate="33.333333",
        )
        for day in (1, 2, 3)
    ]
    billing = TimeBilling(db_session, local_storage)
    analytics = AnalyticsService(db_session)
    invoice = billing.bind_to_invoice(
        billable_deal_id, BindTimeRequest(entry_ids=[e.id for e in entries]), ADMIN
    )

    linked = db_session.execute(
        text(
            "SELECT count(*) FROM time_entries "
            "WHERE deal_id = :deal AND invoice_line_id IS NOT NULL"
        ),
        {"deal": billable_deal_id},
    ).scalar_one()
    assert linked == 3

    on_draft = analytics.deal_pnl(billable_deal_id, READER)
    assert on_draft.revenue == Decimal("0.00")
    assert on_draft.billable_uninvoiced_hours == Decimal("0.30")
    # The derived value, cents and all -- and it is the *lower* of the two, which is
    # what makes the swap at emission observable rather than a no-op.
    assert on_draft.accrued_value == Decimal("9.99")

    InvoiceService(db_session, local_storage).issue(invoice.id, InvoiceIssue(), ADMIN)
    assert analytics.deal_pnl(billable_deal_id, READER).accrued_value == Decimal("10.00")


def test_unpriced_entries_are_refused_with_a_count_and_a_list(
    db_session: Session,
    billable_deal_id: UUID,
    seeded_user_id: UUID,
    local_storage: LocalFileStorage,
) -> None:
    """An invoice line with no unit price is not issuable, and inventing one here would
    decide on the user's behalf what their work is worth. The refusal counts them and
    lists them, so the response is an instruction -- "give these two entries a rate" --
    and not an obstacle."""
    priced = _log(
        db_session,
        billable_deal_id,
        seeded_user_id,
        day=date(2026, 3, 1),
        hours="1.00",
        rate="80.000000",
    )
    unpriced = [
        _log(
            db_session,
            billable_deal_id,
            seeded_user_id,
            day=date(2026, 3, day),
            hours="1.00",
            rate=None,
        )
        for day in (2, 3)
    ]
    with pytest.raises(ValidationFailed) as excinfo:
        TimeBilling(db_session, local_storage).bind_to_invoice(
            billable_deal_id,
            BindTimeRequest(entry_ids=[priced.id, *[e.id for e in unpriced]]),
            ADMIN,
        )
    details = excinfo.value.details
    assert details["field"] == "entry_ids"
    # The count is in the reason and the ids are in `expected`: both of them, and only
    # them -- naming the priced entry too would send the user to fix a row that is fine.
    assert "2 voci" in details["reason"]
    assert all(str(e.id) in details["expected"] for e in unpriced)
    assert str(priced.id) not in details["expected"]
    # Nothing was created: the refusal happens before any draft exists, so a second
    # attempt after fixing the rates does not leave an abandoned invoice behind.
    assert (
        db_session.execute(
            text("SELECT count(*) FROM invoices WHERE deal_id = :deal"),
            {"deal": billable_deal_id},
        ).scalar_one()
        == 0
    )


def test_non_billable_entries_are_refused_by_definition(
    db_session: Session,
    billable_deal_id: UUID,
    seeded_user_id: UUID,
    local_storage: LocalFileStorage,
) -> None:
    """`billable = False` is a property of the work itself -- an internal meeting, a
    rewrite nobody agreed to pay for -- so it never belongs on a draft, whatever rate it
    happens to carry."""
    internal = _log(
        db_session,
        billable_deal_id,
        seeded_user_id,
        day=date(2026, 3, 1),
        hours="1.00",
        rate="80.000000",
        billable=False,
    )
    with pytest.raises(ValidationFailed) as excinfo:
        TimeBilling(db_session, local_storage).bind_to_invoice(
            billable_deal_id, BindTimeRequest(entry_ids=[internal.id]), ADMIN
        )
    assert excinfo.value.details["field"] == "entry_ids"
    assert str(internal.id) in excinfo.value.details["expected"]


def test_an_hour_already_on_an_issued_invoice_cannot_be_invoiced_twice(
    db_session: Session,
    billable_deal_id: UUID,
    seeded_user_id: UUID,
    local_storage: LocalFileStorage,
) -> None:
    """The mechanism that stops the same work being billed twice, and it is at the
    service level because that is where the information is: the column says which line,
    and only a join to `invoices` says whether that line was ever issued."""
    entry = _log(
        db_session,
        billable_deal_id,
        seeded_user_id,
        day=date(2026, 3, 1),
        hours="1.00",
        rate="80.000000",
    )
    billing = TimeBilling(db_session, local_storage)
    invoice = billing.bind_to_invoice(
        billable_deal_id, BindTimeRequest(entry_ids=[entry.id]), ADMIN
    )
    issued = InvoiceService(db_session, local_storage).issue(invoice.id, InvoiceIssue(), ADMIN)

    with pytest.raises(Conflict) as excinfo:
        billing.bind_to_invoice(billable_deal_id, BindTimeRequest(entry_ids=[entry.id]), ADMIN)
    assert "invoice" in excinfo.value.message
    # The refusal names the document, because "already invoiced" is only actionable if
    # the user can go and look at the invoice in question.
    assert excinfo.value.details["number"] == issued.number
    assert excinfo.value.details["year"] == issued.year
    assert excinfo.value.details["entries"] == 1


def test_an_hour_on_a_draft_can_be_rebound(
    db_session: Session,
    billable_deal_id: UUID,
    seeded_user_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Binding is written when the draft line is created, not at issue, so while the
    invoice is a draft the hours stay modifiable and re-selectable -- which is what lets
    slice 3's wholesale line replacement work without slice 4 joining its transaction.

    Constructed with no injected storage, deliberately: a caller with no backend of its
    own builds `TimeBilling(session)` with one argument, and a draft produces no artefact, so
    the one-argument form has to keep working here too. That form resolves its storage
    from `get_settings()`, which reads the worktree's `.env`: pinned to the code's own
    defaults (`_env_file=None`) so an installation that has switched its document store
    to Drive does not turn this into a test of its configuration.
    """
    from pigrocrm.core.timetracking import billing as billing_module

    monkeypatch.setattr(
        billing_module,
        "get_settings",
        lambda: Settings(_env_file=None),  # type: ignore[call-arg]
    )
    entry = _log(
        db_session,
        billable_deal_id,
        seeded_user_id,
        day=date(2026, 3, 1),
        hours="1.00",
        rate="80.000000",
    )
    billing = TimeBilling(db_session)
    first = billing.bind_to_invoice(billable_deal_id, BindTimeRequest(entry_ids=[entry.id]), ADMIN)
    first_line = _lines(db_session, first.id)[0][0]
    assert TimeEntryService(db_session).get(entry.id, READER).invoice_line_id == first_line

    second = billing.bind_to_invoice(billable_deal_id, BindTimeRequest(entry_ids=[entry.id]), ADMIN)
    assert second.id != first.id
    # Moved, not duplicated: the entry now points at the second draft's line, and the
    # first draft keeps its line with nothing hanging off it.
    second_line = _lines(db_session, second.id)[0][0]
    assert TimeEntryService(db_session).get(entry.id, READER).invoice_line_id == second_line


def test_a_collaborator_cannot_call_it(
    db_session: Session, billable_deal_id: UUID, local_storage: LocalFileStorage
) -> None:
    """Choosing *which* hours to invoice is a commercial decision, and this is the step
    immediately before the irreversible one -- slice 3 §11 withdrew `issue_invoice` from
    MCP with the same reasoning."""
    with pytest.raises(PermissionDenied):
        TimeBilling(db_session, local_storage).bind_to_invoice(
            billable_deal_id, BindTimeRequest(entry_ids=[uuid4()]), WRITER
        )


def test_an_entry_from_another_deal_is_refused(
    db_session: Session,
    billable_deal_id: UUID,
    seeded_user_id: UUID,
    local_storage: LocalFileStorage,
    budgeted_deal: Callable[..., UUID],
) -> None:
    """A selection is checked against the deal it is being invoiced under, not trusted:
    two deals can belong to two different customers, and an invoice carries one."""
    other = budgeted_deal(
        budgeted_hours=None,
        budgeted_value=None,
        hours_logged="1.00",
        revenue=None,
        name="Progetto estraneo",
    )
    stray = db_session.execute(
        text("SELECT id FROM time_entries WHERE deal_id = :deal LIMIT 1"), {"deal": other}
    ).scalar_one()
    mine = _log(
        db_session,
        billable_deal_id,
        seeded_user_id,
        day=date(2026, 3, 1),
        hours="1.00",
        rate="80.000000",
    )
    with pytest.raises(ValidationFailed) as excinfo:
        TimeBilling(db_session, local_storage).bind_to_invoice(
            billable_deal_id, BindTimeRequest(entry_ids=[mine.id, stray]), ADMIN
        )
    assert excinfo.value.details["field"] == "entry_ids"
    assert str(stray) in excinfo.value.details["expected"]
    assert str(mine.id) not in excinfo.value.details["expected"]


def test_an_unknown_entry_id_is_a_not_found_not_a_silent_skip(
    db_session: Session, billable_deal_id: UUID, local_storage: LocalFileStorage
) -> None:
    """Silently dropping an id nobody could resolve would produce a draft that is
    missing work the user believed they had selected -- and they would find out from the
    client."""
    ghost = uuid4()
    with pytest.raises(NotFound) as excinfo:
        TimeBilling(db_session, local_storage).bind_to_invoice(
            billable_deal_id, BindTimeRequest(entry_ids=[ghost]), ADMIN
        )
    assert excinfo.value.details["identifier"] == str(ghost)


def test_the_same_entry_twice_in_one_selection_is_billed_once(
    db_session: Session,
    billable_deal_id: UUID,
    seeded_user_id: UUID,
    local_storage: LocalFileStorage,
) -> None:
    """A repeated id is the one duplication that would be invisible: both occurrences
    land in the same group, so the line's `quantity` doubles while every count in this
    file still agrees with itself -- one line, one bound entry, one activity. Only the
    quantity gives it away, which is why this is the assertion."""
    entry = _log(
        db_session,
        billable_deal_id,
        seeded_user_id,
        day=date(2026, 3, 1),
        hours="1.00",
        rate="80.000000",
    )
    invoice = TimeBilling(db_session, local_storage).bind_to_invoice(
        billable_deal_id, BindTimeRequest(entry_ids=[entry.id, entry.id]), ADMIN
    )
    lines = _lines(db_session, invoice.id)
    assert [(quantity, price) for _, quantity, price, _ in lines] == [
        (Decimal("1.00"), Decimal("80"))
    ]
