"""§4's figures, each from its one stated source.

The three §3 exceptions are here and the tests say so out loud, because an exception that is
not written down is a rule that does not hold. Everything else in this file is a `COUNT` or
a `SUM` over a single table.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from frozen_clock import TODAY_IN_ITALY, TODAY_OF_PROCESS, freeze
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.deals.models import Deal, DealValueLine
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.documents.models import Document
from pigrocrm.core.documents.repository import DocumentRepository
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.pipeline.service import PipelineService

SEED = Actor(id=None, type="system", role="admin")


@pytest.fixture
def stages(db_session: Session) -> dict[str, Any]:
    PipelineService(db_session).seed_defaults(SEED)
    return {s.code: s for s in PipelineService(db_session).list() if s.code is not None}


@pytest.fixture
def customer(db_session: Session) -> Organization:
    row = Organization(legal_name="Cliente Srl", country="IT", custom_fields={})
    db_session.add(row)
    db_session.flush()
    return row


def _deal(
    db_session: Session,
    customer: Organization,
    stage_id: object,
    *,
    value: str | None = "1000.00",
    probability: int = 50,
    closed_at: date | None = None,
    expected_close_date: date | None = None,
) -> Deal:
    row = Deal(
        name="Impianto",
        customer_id=customer.id,
        pipeline_stage_id=stage_id,
        expected_value=Decimal(value) if value is not None else None,
        probability=probability,
        closed_at=closed_at,
        expected_close_date=expected_close_date,
        custom_fields={},
    )
    db_session.add(row)
    db_session.flush()
    return row


def _offer(
    db_session: Session,
    customer: Organization,
    title: str,
    *,
    status: str = "sent",
    status_since: date | None = date(2026, 3, 1),
) -> Document:
    row = Document(
        customer_id=customer.id,
        type="offer",
        title=title,
        status=status,
        status_since=status_since,
        current_version=1,
        custom_fields={},
    )
    db_session.add(row)
    db_session.flush()
    return row


# -- pipeline_summary ------------------------------------------------------------


def test_pipeline_summary_groups_open_deals_by_stage(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    _deal(db_session, customer, stages["lead"].id, value="1000.00")
    _deal(db_session, customer, stages["lead"].id, value="2000.00")
    _deal(db_session, customer, stages["offer"].id, value="500.00")

    rows = {row.stage_code: row for row in DealRepository(db_session).pipeline_summary()}
    assert rows["lead"].number == 2
    assert rows["lead"].total_value == Decimal("3000.00")
    assert rows["offer"].number == 1
    assert rows["offer"].total_value == Decimal("500.00")


def test_pipeline_summary_includes_the_closed_stages_last(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    """Every configured stage, in `position` order, closed ones included.

    It used to be `type='open'` only, on the reasoning that a won deal is history and
    not pipeline. The card that reads this then stopped at the last open stage and never
    said where the work ended up, which is the one thing a pipeline exists to answer --
    so since 2026-09-09 the closed stages are returned too, each carrying its
    `stage_type` so the renderer can group them by that rather than by a `name` the user
    is free to rename. What they count is what sits in the stage *today*: there is no
    period filter in this query and never was, and the period question belongs to
    `closed_in_period` on the same card.
    """
    _deal(db_session, customer, stages["won"].id)
    _deal(db_session, customer, stages["lost"].id)

    rows = DealRepository(db_session).pipeline_summary()

    per_code = {row.stage_code: row for row in rows}
    assert per_code["won"].number == 1
    assert per_code["lost"].number == 1
    assert per_code["won"].stage_type == "won"
    assert per_code["lost"].stage_type == "lost"
    # In `position` order, which is what puts the two closed stages at the end: the
    # order is the contract, because the card renders the rows as they arrive.
    assert [row.stage_code for row in rows] == sorted(
        per_code, key=lambda code: stages[code].position
    )


def test_pipeline_summary_excludes_soft_deleted_deals(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    row = _deal(db_session, customer, stages["lead"].id)
    row.deleted_at = datetime.now(UTC)
    db_session.flush()
    summary = {r.stage_code: r for r in DealRepository(db_session).pipeline_summary()}
    assert summary["lead"].number == 0
    assert summary["lead"].total_value == Decimal("0.00")
    # And it is not counted as a deal *without* a value either: it is not a deal here at
    # all. A soft-deleted row leaking into `without_value` would inflate the one column
    # whose whole job is to say "these figures are missing something".
    assert summary["lead"].without_value == 0


def test_a_deal_without_a_value_is_counted_separately_and_never_summed_as_zero(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    """§4's own note. Counting a missing value as zero understates the pipeline and there
    is no way for the reader to tell -- so it is a column of its own."""
    _deal(db_session, customer, stages["lead"].id, value="1000.00")
    _deal(db_session, customer, stages["lead"].id, value=None)

    row = next(r for r in DealRepository(db_session).pipeline_summary() if r.stage_code == "lead")
    assert row.number == 2
    assert row.without_value == 1
    assert row.total_value == Decimal("1000.00")


def test_a_stage_with_no_deals_still_appears_with_zeroes(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    """A missing stage and an empty stage render identically in a bar chart, and the
    reader cannot tell which they are looking at.

    Every column is asserted, not just the stage's presence: under a LEFT JOIN the empty
    stage still produces one row whose `deals` columns are all `NULL`, so a `without_value`
    written as `COUNT(CASE WHEN valore_previsto IS NULL THEN 1 END)` counts that phantom
    row and reports one value-less deal in a stage that has no deals at all.
    """
    rows = {r.stage_code: r for r in DealRepository(db_session).pipeline_summary()}
    assert {"lead", "contacted", "offer", "negotiation"} <= set(rows)
    empty = rows["negotiation"]
    assert empty.number == 0
    assert empty.without_value == 0
    assert empty.total_value == Decimal("0.00")
    assert empty.weighted_value == Decimal("0.00")


def test_stages_come_back_in_pipeline_order(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    """A pipeline chart whose bars reorder between loads is unreadable.

    The extra stage is created *last* and positioned *first*, so insertion order and
    `position` order disagree: an implementation that orders by `id` (or by nothing,
    which in Postgres means whatever the group-by hash produces) puts it at the end and
    fails both assertions.
    """
    db_session.add(
        PipelineStage(name="Ricontatto", position=-1, default_probability=5, type="open")
    )
    db_session.flush()

    rows = DealRepository(db_session).pipeline_summary()
    assert [row.position for row in rows] == sorted(row.position for row in rows)
    assert rows[0].stage_name == "Ricontatto"


# -- the first §3 exception: the weighted value ----------------------------------


def test_the_weighted_value_is_the_product_of_two_columns_of_deals(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    """**§3 exception 1.** `Σ ROUND(valore_previsto × probabilita / 100, 2)`. Allowed
    because it combines only columns of `deals` and is not money received; labelled
    *stima* everywhere it appears."""
    _deal(db_session, customer, stages["lead"].id, value="1000.00", probability=50)
    _deal(db_session, customer, stages["lead"].id, value="333.33", probability=33)

    row = next(r for r in DealRepository(db_session).pipeline_summary() if r.stage_code == "lead")
    # 500.00 + ROUND(109.99890, 2) = 500.00 + 110.00
    assert row.weighted_value == Decimal("610.00")
    # And the unweighted total is untouched by the estimate: the two columns are two
    # figures, and the weighted one is never the one added to anything.
    assert row.total_value == Decimal("1333.33")


def test_the_weighted_value_rounds_per_row_then_sums(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    """`Σ ROUND(row, 2)`, never `ROUND(Σ exact, 2)`, and HALF_UP not HALF_EVEN -- the
    project-wide rule from slice 4. Three rows at 0.005 differ between the two by a cent,
    which is exactly how a reconciliation stops reconciling."""
    for _ in range(3):
        _deal(db_session, customer, stages["lead"].id, value="0.01", probability=50)
    row = next(r for r in DealRepository(db_session).pipeline_summary() if r.stage_code == "lead")
    # ROUND(0.005, 2) = 0.01 half-up, three times. ROUND(Σ 0.015, 2) would be 0.02, and
    # half-even on either would give 0.00 and 0.02.
    assert row.weighted_value == Decimal("0.03")


def test_a_deal_without_a_value_contributes_nothing_to_the_weighted_value(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    _deal(db_session, customer, stages["lead"].id, value=None, probability=90)
    row = next(r for r in DealRepository(db_session).pipeline_summary() if r.stage_code == "lead")
    assert row.weighted_value == Decimal("0.00")
    assert row.number == 1


# -- closed_in_period and the second §3 exception --------------------------------


def test_closed_in_period_counts_won_and_lost_by_closed_at(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    _deal(db_session, customer, stages["won"].id, closed_at=date(2026, 3, 10), value="1000.00")
    _deal(db_session, customer, stages["won"].id, closed_at=date(2026, 3, 20), value="2000.00")
    _deal(db_session, customer, stages["lost"].id, closed_at=date(2026, 3, 15))
    _deal(db_session, customer, stages["won"].id, closed_at=date(2026, 4, 1))

    result = DealRepository(db_session).closed_in_period(date(2026, 3, 1), date(2026, 3, 31))
    assert result.won == 2
    assert result.lost == 1
    # The lost deal's own 1000.00 is not in here: `won_value` is the value of what was
    # won, not of everything that closed.
    assert result.won_value == Decimal("3000.00")


def test_closed_in_period_ignores_a_soft_deleted_deal(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    row = _deal(db_session, customer, stages["won"].id, closed_at=date(2026, 3, 10))
    row.deleted_at = datetime.now(UTC)
    db_session.flush()
    result = DealRepository(db_session).closed_in_period(date(2026, 3, 1), date(2026, 3, 31))
    assert result.won == 0
    assert result.won_value == Decimal("0.00")


def test_the_period_bounds_are_inclusive(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    _deal(db_session, customer, stages["won"].id, closed_at=date(2026, 3, 1))
    _deal(db_session, customer, stages["won"].id, closed_at=date(2026, 3, 31))
    # One day outside each end, to prove the bounds are bounds and not decoration.
    _deal(db_session, customer, stages["won"].id, closed_at=date(2026, 2, 28))
    _deal(db_session, customer, stages["won"].id, closed_at=date(2026, 4, 1))
    result = DealRepository(db_session).closed_in_period(date(2026, 3, 1), date(2026, 3, 31))
    assert result.won == 2


def test_the_conversion_rate_is_a_ratio_of_two_counts(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    """**§3 exception 2.** `vinti / (vinti + persi)`, two decimals."""
    for _ in range(3):
        _deal(db_session, customer, stages["won"].id, closed_at=date(2026, 3, 10))
    _deal(db_session, customer, stages["lost"].id, closed_at=date(2026, 3, 10))

    result = DealRepository(db_session).closed_in_period(date(2026, 3, 1), date(2026, 3, 31))
    assert result.conversion_rate == Decimal("75.00")


def test_the_conversion_rate_rounds_half_up(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    """1 of 32 is 3.125 per cent exactly: 3.13 half-up, 3.12 half-even.

    `Decimal.quantize` defaults to the context's `ROUND_HALF_EVEN`, so a rate quantized
    without naming a mode passes every other test in this file and fails this one. That is
    the whole reason `money.round_money` exists and is used here rather than a local
    `quantize`.
    """
    _deal(db_session, customer, stages["won"].id, closed_at=date(2026, 3, 10))
    for _ in range(31):
        _deal(db_session, customer, stages["lost"].id, closed_at=date(2026, 3, 10))

    result = DealRepository(db_session).closed_in_period(date(2026, 3, 1), date(2026, 3, 31))
    assert result.won == 1 and result.lost == 31
    assert result.conversion_rate == Decimal("3.13")


def test_the_conversion_rate_is_null_when_nothing_closed(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    """`null`, not `0`. Zero per cent means "I lost everything"; no closed deals means
    something else. Same rule as slice 4 §7.1's margin percentage, and the *same* rule so
    there is one."""
    result = DealRepository(db_session).closed_in_period(date(2026, 3, 1), date(2026, 3, 31))
    assert result.won == 0 and result.lost == 0
    assert result.conversion_rate is None


def test_the_conversion_rate_is_zero_when_everything_was_lost(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    """The other half of the same distinction, which a `null`-only test would not catch."""
    _deal(db_session, customer, stages["lost"].id, closed_at=date(2026, 3, 10))
    result = DealRepository(db_session).closed_in_period(date(2026, 3, 1), date(2026, 3, 31))
    assert result.conversion_rate == Decimal("0.00")


def test_deals_closed_before_the_column_existed_are_reported_not_counted(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    """§4.1: `closed_at` is not backfilled, so historical closures are unattributable. The
    dashboard says how many rather than counting them as zero or putting them in the wrong
    month -- a guessed conversion rate is plausible and wrong, which is the worst
    combination."""
    _deal(db_session, customer, stages["won"].id, closed_at=None)
    _deal(db_session, customer, stages["lost"].id, closed_at=None)
    _deal(db_session, customer, stages["won"].id, closed_at=date(2026, 3, 10))

    repo = DealRepository(db_session)
    assert repo.closed_in_period(date(2026, 3, 1), date(2026, 3, 31)).won == 1
    assert repo.unattributable_closures() == 2


def test_an_open_deal_with_no_closed_at_is_not_an_unattributable_closure(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    """Nearly every deal in the table is open and has no `closed_at`; counting those would
    make the warning meaningless the day it shipped."""
    _deal(db_session, customer, stages["lead"].id, closed_at=None)
    assert DealRepository(db_session).unattributable_closures() == 0


def test_expected_closures_counts_open_deals_in_the_window(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    _deal(db_session, customer, stages["lead"].id, expected_close_date=date(2026, 3, 15))
    _deal(db_session, customer, stages["lead"].id, expected_close_date=date(2026, 6, 1))
    # A won deal with a future expected date is not an expected closure.
    _deal(
        db_session,
        customer,
        stages["won"].id,
        expected_close_date=date(2026, 3, 20),
        closed_at=date(2026, 2, 1),
    )
    # Nor is a deal with no expected date at all, which is most of them.
    _deal(db_session, customer, stages["lead"].id, expected_close_date=None)

    assert DealRepository(db_session).expected_closures(date(2026, 3, 1), date(2026, 3, 31)) == 1


# -- the documents side ----------------------------------------------------------


def test_pending_offers_carries_the_age_in_days(
    db_session: Session, customer: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The age is the emitter's day minus `status_since`, not the database server's.

    The clock is frozen at an instant where Italy and a UTC process disagree about the
    day, so an implementation computing the age from `CURRENT_DATE` -- or from
    `date.today()` on a UTC host -- reports one day less and fails here. Freezing is the
    only way this assertion says anything: against the real clock every candidate
    implementation agrees.
    """
    freeze(monkeypatch)
    _offer(db_session, customer, "Offerta ferma", status_since=date(2025, 12, 25))

    rows = DocumentRepository(db_session).pending_offers()
    assert len(rows) == 1
    assert rows[0].title == "Offerta ferma"
    assert rows[0].status_since == date(2025, 12, 25)
    assert rows[0].days == (TODAY_IN_ITALY - date(2025, 12, 25)).days
    assert rows[0].days != (TODAY_OF_PROCESS - date(2025, 12, 25)).days


def test_pending_offers_ignores_offers_in_any_other_state(
    db_session: Session, customer: Organization
) -> None:
    for status in ("draft", "accepted", "rejected"):
        _offer(db_session, customer, f"Offerta {status}", status=status)
    assert DocumentRepository(db_session).pending_offers() == []


def test_pending_offers_ignores_a_soft_deleted_offer(
    db_session: Session, customer: Organization
) -> None:
    row = _offer(db_session, customer, "Offerta cestinata")
    row.deleted_at = datetime.now(UTC)
    db_session.flush()
    repo = DocumentRepository(db_session)
    assert repo.pending_offers() == []
    assert repo.count_pending_offers() == 0


def test_pending_offers_are_oldest_first_with_unknown_ages_last(
    db_session: Session, customer: Organization
) -> None:
    """Oldest first because the list exists to be acted on from the top, and an offer with
    no known start date is not the oldest one.

    Postgres already puts NULLs last on an ASC sort, so the explicit `nulls_last()` in the
    repository changes nothing today -- this test does not pretend otherwise. What it does
    catch is the direction: reversed, the newest offer leads the list *and* the row with no
    date leads it, because the NULL default flips with the direction.
    """
    _offer(db_session, customer, "Recente", status_since=date(2026, 3, 5))
    _offer(db_session, customer, "Ignota", status_since=None)
    _offer(db_session, customer, "Antica", status_since=date(2026, 3, 1))

    assert [r.title for r in DocumentRepository(db_session).pending_offers()] == [
        "Antica",
        "Recente",
        "Ignota",
    ]


def test_the_list_is_truncated_but_the_count_is_the_real_total(
    db_session: Session, customer: Organization
) -> None:
    """A dashboard showing twenty of ninety must say ninety: a truncated list presented as
    the whole is the "partial results without saying so" failure §12 names."""
    for day in (1, 2, 3):
        _offer(db_session, customer, f"Offerta {day}", status_since=date(2026, 3, day))

    repo = DocumentRepository(db_session)
    assert len(repo.pending_offers(limit=2)) == 2
    assert repo.count_pending_offers() == 3


def test_an_offer_with_no_status_since_has_to_null_age_rather_than_zero(
    db_session: Session, customer: Organization
) -> None:
    """The backfill covers documents with a `state_changed` in their timeline; one written
    directly by a fixture or an import has none. Zero days would read as "sent today"."""
    _offer(db_session, customer, "Senza data", status_since=None)
    rows = DocumentRepository(db_session).pending_offers()
    assert rows[0].days is None
    assert rows[0].status_since is None


def test_the_signal_counts_accepted_offers_whose_deal_is_not_won(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    """§6.2's first signal, and the permanent cross-check on automation A1: if the
    automation goes quiet, this count speaks. A `COUNT` across a join, which §3 permits --
    a `SUM` across one it does not."""
    open_deal = _deal(db_session, customer, stages["lead"].id)
    won_deal = _deal(db_session, customer, stages["won"].id, closed_at=date(2026, 3, 1))
    lost_deal = _deal(db_session, customer, stages["lost"].id, closed_at=date(2026, 3, 1))
    for deal, title in (
        (open_deal, "Da sistemare"),
        (won_deal, "A posto"),
        # A lost deal with an accepted offer is the same inconsistency as an open one: the
        # offer was accepted and the deal says otherwise.
        (lost_deal, "Anche questa"),
    ):
        db_session.add(
            Document(
                deal_id=deal.id,
                type="offer",
                title=title,
                status="accepted",
                status_since=date(2026, 3, 1),
                current_version=1,
                custom_fields={},
            )
        )
    db_session.flush()

    assert DocumentRepository(db_session).count_accepted_with_unwon_deal() == 2


def test_the_signal_ignores_an_offer_that_was_never_accepted(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    """An offer still out for signature on an open deal is the normal case, not a signal."""
    deal = _deal(db_session, customer, stages["lead"].id)
    db_session.add(
        Document(
            deal_id=deal.id,
            type="offer",
            title="Ancora in attesa",
            status="sent",
            status_since=date(2026, 3, 1),
            current_version=1,
            custom_fields={},
        )
    )
    db_session.flush()
    assert DocumentRepository(db_session).count_accepted_with_unwon_deal() == 0


def test_the_signal_ignores_an_accepted_offer_with_no_deal(
    db_session: Session, customer: Organization
) -> None:
    """An offer attached to a customer has no deal to be won, so it is not an
    inconsistency."""
    _offer(db_session, customer, "Senza deal", status="accepted")
    assert DocumentRepository(db_session).count_accepted_with_unwon_deal() == 0


def test_the_signal_ignores_a_soft_deleted_deal(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    deal = _deal(db_session, customer, stages["lead"].id)
    deal.deleted_at = datetime.now(UTC)
    db_session.add(
        Document(
            deal_id=deal.id,
            type="offer",
            title="Deal archiviato",
            status="accepted",
            status_since=date(2026, 3, 1),
            current_version=1,
            custom_fields={},
        )
    )
    db_session.flush()
    assert DocumentRepository(db_session).count_accepted_with_unwon_deal() == 0


def test_the_signal_ignores_a_soft_deleted_offer(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    deal = _deal(db_session, customer, stages["lead"].id)
    offer = Document(
        deal_id=deal.id,
        type="offer",
        title="Offerta cestinata",
        status="accepted",
        status_since=date(2026, 3, 1),
        current_version=1,
        custom_fields={},
        deleted_at=datetime.now(UTC),
    )
    db_session.add(offer)
    db_session.flush()
    assert DocumentRepository(db_session).count_accepted_with_unwon_deal() == 0


# -- the third §3 exception: the monthly recurring value --------------------------


def _line(
    db_session: Session,
    deal: Deal,
    *,
    period: str,
    price: str,
    unit: str = "flat",
    quantity: str = "1",
    position: int = 0,
) -> None:
    db_session.add(
        DealValueLine(
            deal_id=deal.id,
            position=position,
            unit=unit,
            delivery_period=period,
            unit_price=Decimal(price),
            quantity=Decimal(quantity),
            start=date(2026, 3, 1),
        )
    )
    db_session.flush()


def test_the_monthly_recurring_value_of_the_open_deals_is_a_monthly_figure_per_line(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    """**§3 exception 3.** A monthly fee counts as it is, an annual one a twelfth, a day
    line by its days a month, a one-off nothing; a won deal is not an open one."""
    monthly = _deal(db_session, customer, stages["lead"].id)
    _line(db_session, monthly, period="monthly", price="500.00")
    _line(db_session, monthly, period="one_off", price="20000.00", position=1)
    annual = _deal(db_session, customer, stages["offer"].id)
    _line(db_session, annual, period="annual", price="1200.00")
    days = _deal(db_session, customer, stages["lead"].id)
    _line(db_session, days, period="monthly", unit="day", price="450.00", quantity="2.50")
    _deal(db_session, customer, stages["lead"].id)  # no lines: nothing
    won = _deal(db_session, customer, stages["won"].id, closed_at=date(2026, 3, 10))
    _line(db_session, won, period="monthly", price="9999.00")

    assert DealRepository(db_session).open_monthly_recurring() == Decimal("1725.00")


def test_the_monthly_recurring_value_of_the_won_deals_is_the_periods_by_closed_at(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    inside = _deal(db_session, customer, stages["won"].id, closed_at=date(2026, 3, 10))
    _line(db_session, inside, period="annual", price="6000.00")
    before = _deal(db_session, customer, stages["won"].id, closed_at=date(2026, 2, 28))
    _line(db_session, before, period="monthly", price="100.00")
    lost = _deal(db_session, customer, stages["lost"].id, closed_at=date(2026, 3, 10))
    _line(db_session, lost, period="monthly", price="100.00")
    archived = _deal(db_session, customer, stages["won"].id, closed_at=date(2026, 3, 11))
    _line(db_session, archived, period="monthly", price="100.00")
    archived.deleted_at = datetime.now(UTC)
    db_session.flush()

    result = DealRepository(db_session).closed_in_period(date(2026, 3, 1), date(2026, 3, 31))

    assert result.won_monthly_recurring == Decimal("500.00")
    # A monthly figure sits beside the total and never in it.
    assert result.won_value == Decimal("1000.00")


def test_the_monthly_recurring_value_is_zero_when_nothing_recurs(
    db_session: Session, customer: Organization, stages: dict[str, Any]
) -> None:
    repo = DealRepository(db_session)
    assert repo.open_monthly_recurring() == Decimal("0.00")
    assert repo.closed_in_period(date(2026, 3, 1), date(2026, 3, 31)).won_monthly_recurring == (
        Decimal("0.00")
    )
