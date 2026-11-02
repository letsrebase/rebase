"""**Criterion 7.** The comparison the decomposition names as this slice's title, and the
one that tells a consultant whether the work was worth the price.

The value comparison is against the **pro-rata** budget, not the full one: at 40% of the
hours, being at 40% of the estimated value is on track, while comparing with 100% would
mark every job in progress as underperforming -- and a report that flags everything flags
nothing.
"""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from periodo_fiscale import OGGI, PRIMO_DEL_MESE
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.schemas import BudgetQuery, BudgetVsActualRow
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.errors import ValidationFailed

READER = Actor(id=None, type="user", role="readonly")
# Derived, never a literal month: `budgeted_deal` builds its revenue by *issuing* a real
# invoice, and `issue()` refuses a `data_emissione` outside the current fiscal year, so a
# literal `2026-03` was a date on which every test in this file would begin failing at
# once. See `periodo_fiscale.py` -- "this year, March" would not have been a fix either.
PERIODO = BudgetQuery(da=PRIMO_DEL_MESE, a=OGGI)

BudgetedDeal = Callable[..., UUID]


def _row(db_session: Session, deal_id: UUID, query: BudgetQuery = PERIODO) -> BudgetVsActualRow:
    return next(
        row
        for row in AnalyticsService(db_session).budget_vs_actual(query, READER).items
        if row.deal_id == deal_id
    )


def test_the_spec_worked_example(db_session: Session, budgeted_deal: BudgetedDeal) -> None:
    """A deal of 100 hours and 10,000 EUR, 40 hours logged, 4,000 EUR invoiced:
    `avanzamento = "40.00"`, `budget_pro_rata = "4000.00"`, `scostamento_valore = "0.00"`.
    """
    deal_id = budgeted_deal(
        ore_preventivate="100.00",
        valore_preventivato="10000.00",
        ore_registrate="40.00",
        ricavi="4000.00",
    )
    row = _row(db_session, deal_id)
    assert row.ore_consuntivate == Decimal("40.00")
    assert row.ricavi == Decimal("4000.00")
    assert row.avanzamento_ore == Decimal("40.00")
    assert row.budget_pro_rata == Decimal("4000.00")
    assert row.scostamento_valore == Decimal("0.00")
    assert row.scostamento_ore == Decimal("-60.00")
    assert row.non_preventivato is False
    assert row.pro_rata_non_calcolabile is False


def test_over_delivery_is_measured_against_the_pro_rata_not_the_full_budget(
    db_session: Session, budgeted_deal: BudgetedDeal
) -> None:
    """With 4,500 EUR invoiced the variance is `"500.00"`, **not** `"-5500.00"`. The second
    number is what comparing against the full budget produces, and it would mark a job
    that is ahead as 55% behind."""
    deal_id = budgeted_deal(
        ore_preventivate="100.00",
        valore_preventivato="10000.00",
        ore_registrate="40.00",
        ricavi="4500.00",
    )
    row = _row(db_session, deal_id)
    assert row.scostamento_valore == Decimal("500.00")
    assert row.scostamento_valore != Decimal("-5500.00")


def test_an_absent_estimate_is_not_an_estimate_of_zero(
    db_session: Session, budgeted_deal: BudgetedDeal
) -> None:
    """`ore_preventivate IS NULL` means nobody estimated. The row says «non preventivato»,
    is excluded from the budget aggregates, and is never counted as a 100% overrun. No
    division by a null or zero estimate is ever executed."""
    deal_id = budgeted_deal(
        ore_preventivate=None,
        valore_preventivato=None,
        ore_registrate="40.00",
        ricavi="4000.00",
    )
    page = AnalyticsService(db_session).budget_vs_actual(PERIODO, READER)
    row = next(r for r in page.items if r.deal_id == deal_id)
    assert row.non_preventivato is True
    assert row.avanzamento_ore is None
    assert row.budget_pro_rata is None
    assert row.scostamento_valore is None
    assert row.scostamento_ore is None
    assert row.tariffa_media_preventivata is None
    # The actuals are still reported in full: unestimated is not unmeasured.
    assert row.ore_consuntivate == Decimal("40.00")
    assert row.ricavi == Decimal("4000.00")
    assert row.tariffa_media_consuntivata == Decimal("100.00")
    # Excluded from the aggregates, and counted separately so the reader knows. This is
    # the only deal in the window, so the aggregates are zero exactly because it was
    # left out -- not merely "some number that happens to differ".
    assert page.deal_non_preventivati == 1
    assert page.deal_preventivati == 0
    assert page.totale_preventivato == Decimal("0.00")
    assert page.totale_ricavi == Decimal("0.00")


def test_zero_estimated_hours_behave_identically_to_null(
    db_session: Session, budgeted_deal: BudgetedDeal
) -> None:
    """The secondary defence residual A14 requires regardless of Task 4B-1: `0` is treated
    as "not comparable", never as a denominator. Without it, the only reachable way to say
    "no estimate" before A14 was closed -- `0.00` -- read as "zero hours estimated, infinite
    overrun"."""
    zero = budgeted_deal(
        ore_preventivate="0.00",
        valore_preventivato="10000.00",
        ore_registrate="40.00",
        ricavi="4000.00",
        nome="Preventivo a zero",
    )
    null = budgeted_deal(
        ore_preventivate=None,
        valore_preventivato="10000.00",
        ore_registrate="40.00",
        ricavi="4000.00",
        nome="Senza preventivo ore",
    )
    items = {
        r.deal_id: r for r in AnalyticsService(db_session).budget_vs_actual(PERIODO, READER).items
    }
    for field in ("avanzamento_ore", "budget_pro_rata", "scostamento_valore", "scostamento_ore"):
        assert getattr(items[zero], field) == getattr(items[null], field)
        # Equal *and* absent: two rows agreeing on a wrong figure would satisfy the line
        # above on its own.
        assert getattr(items[zero], field) is None
    assert items[zero].pro_rata_non_calcolabile is True
    assert items[zero].tariffa_media_preventivata is None


def test_a_value_estimate_without_an_hours_estimate_shows_only_the_absolute_comparison(
    db_session: Session, budgeted_deal: BudgetedDeal
) -> None:
    """The pro-rata needs **both** estimate columns. With `valore_preventivato` set and
    `ore_preventivate` null there is no progress figure to derive it from, so the row is
    marked `pro_rata_non_calcolabile` rather than inventing a progress from the invoiced
    value -- which would be circular, because the invoiced value is the very quantity being
    judged."""
    deal_id = budgeted_deal(
        ore_preventivate=None,
        valore_preventivato="10000.00",
        ore_registrate="40.00",
        ricavi="4000.00",
    )
    row = _row(db_session, deal_id)
    assert row.pro_rata_non_calcolabile is True
    assert row.non_preventivato is False
    assert row.budget_pro_rata is None
    # The absolute comparison is still there.
    assert row.valore_preventivato == Decimal("10000.00")
    assert row.ricavi == Decimal("4000.00")


def test_an_hours_estimate_without_a_value_estimate_still_measures_the_hours(
    db_session: Session, budgeted_deal: BudgetedDeal
) -> None:
    """The mirror case, and the one that shows `pro_rata_non_calcolabile` names a specific
    situation rather than "anything incomplete": the hours comparison needs only the hours
    estimate, and it is reported."""
    deal_id = budgeted_deal(
        ore_preventivate="100.00",
        valore_preventivato=None,
        ore_registrate="120.00",
        ricavi="4000.00",
    )
    row = _row(db_session, deal_id)
    assert row.avanzamento_ore == Decimal("120.00")
    assert row.scostamento_ore == Decimal("20.00")
    assert row.non_preventivato is False
    # No value estimate, so no pro-rata and no variance against one -- but this is not the
    # `pro_rata_non_calcolabile` case, which is about a value estimate with no progress.
    assert row.budget_pro_rata is None
    assert row.scostamento_valore is None
    assert row.pro_rata_non_calcolabile is False
    assert row.tariffa_media_preventivata is None


def test_the_average_rate_is_the_row_that_matters_most(
    db_session: Session, budgeted_deal: BudgetedDeal
) -> None:
    """Not in the decomposition, and the one that serves best: how much I realised per
    hour worked, against how much I expected to. Two figures comparable even between deals
    of different sizes, and the only form in which "is this client worth it?" has a
    numeric answer."""
    deal_id = budgeted_deal(
        ore_preventivate="100.00",
        valore_preventivato="10000.00",
        ore_registrate="40.00",
        ricavi="4000.00",
    )
    row = _row(db_session, deal_id)
    assert row.tariffa_media_preventivata == Decimal("100.00")
    assert row.tariffa_media_consuntivata == Decimal("100.00")

    # And the pair says something only when the two differ: 80 hours for the same money
    # is the same deal realised at half the rate it was sold at.
    slow = budgeted_deal(
        ore_preventivate="100.00",
        valore_preventivato="10000.00",
        ore_registrate="80.00",
        ricavi="4000.00",
        nome="Progetto lento",
    )
    slow_row = _row(db_session, slow)
    assert slow_row.tariffa_media_preventivata == Decimal("100.00")
    assert slow_row.tariffa_media_consuntivata == Decimal("50.00")


def test_a_deal_with_no_hours_has_no_realised_rate_rather_than_zero(
    db_session: Session, budgeted_deal: BudgetedDeal
) -> None:
    """Invoiced with nothing logged against it: dividing by zero hours has no answer, and
    `0.00` would read as "realised nothing per hour" -- the opposite of the truth."""
    deal_id = budgeted_deal(
        ore_preventivate="100.00", valore_preventivato="10000.00", ricavi="4000.00"
    )
    row = _row(db_session, deal_id)
    assert row.ore_consuntivate == Decimal("0.00")
    assert row.tariffa_media_consuntivata is None
    assert row.avanzamento_ore == Decimal("0.00")
    assert row.budget_pro_rata == Decimal("0.00")
    assert row.scostamento_valore == Decimal("4000.00")


def test_the_list_is_paginated_from_the_first_commit(
    db_session: Session, budgeted_deal: BudgetedDeal
) -> None:
    """Residual B3: the margins view is by its nature a list of *closed* deals, so
    unbounded growth stops being invisible here. Paginated with a mandatory period
    filter."""
    for index in range(5):
        budgeted_deal(
            ore_preventivate="10.00",
            valore_preventivato="1000.00",
            ore_registrate="1.00",
            ricavi="100.00",
            nome=f"Deal {index}",
        )
    service = AnalyticsService(db_session)
    page = service.budget_vs_actual(BudgetQuery(da=PERIODO.da, a=PERIODO.a, limit=2), READER)
    assert len(page.items) == 2
    assert page.next_cursor is not None

    # Following the cursor walks the list once: no row repeats, none is skipped, and the
    # last page stops offering one. A cursor that reset to the start would satisfy the two
    # assertions above forever.
    seen = [row.deal_id for row in page.items]
    while page.next_cursor is not None:
        page = service.budget_vs_actual(
            BudgetQuery(da=PERIODO.da, a=PERIODO.a, limit=2, cursor=page.next_cursor), READER
        )
        seen.extend(row.deal_id for row in page.items)
    assert len(seen) == 5
    assert len(set(seen)) == 5


def test_a_cursor_that_names_no_deal_is_refused_rather_than_read_as_the_first_page(
    db_session: Session, budgeted_deal: BudgetedDeal
) -> None:
    """An unresolved cursor used to return page one again, so a client following
    `next_cursor` through a list that changed under it looped for ever. It is a 422 that
    names the field instead."""
    budgeted_deal(
        ore_preventivate="10.00",
        valore_preventivato="1000.00",
        ore_registrate="1.00",
        ricavi="100.00",
    )
    with pytest.raises(ValidationFailed) as excinfo:
        AnalyticsService(db_session).budget_vs_actual(
            BudgetQuery(da=PERIODO.da, a=PERIODO.a, cursor=uuid4()), READER
        )
    assert excinfo.value.details["field"] == "cursor"


def test_an_inverted_window_is_refused(db_session: Session) -> None:
    """The same refusal as `period_pnl`, for the same reason: a window that runs backwards
    silently matches nothing, and an empty report is the one failure mode that looks like
    an answer."""
    with pytest.raises(ValidationFailed) as excinfo:
        AnalyticsService(db_session).budget_vs_actual(
            BudgetQuery(da=date(2026, 3, 31), a=date(2026, 3, 1)), READER
        )
    assert excinfo.value.details["field"] == "a"
