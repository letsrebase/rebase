"""A source kind of a module that is off is absent from the schedule, the totals and the legend
(brief 2026-11-10, «Modules»): every read of the schedule, the payables and the summary leaves
out the due items of the kinds whose module is not on, and nothing else.

`GET /api/due-items` itself is not filtered: it is a list of records, and these are figures.
"""

import datetime as dt
from decimal import Decimal

import pytest
from fakes.cash_fixtures import ADMIN, CashWorld
from sqlalchemy.orm import Session

from pigrocrm.core.due_items.models import SOURCE_KINDS
from pigrocrm.core.due_items.schedule import CashScheduleService
from pigrocrm.core.due_items.schedule_queries import SOURCE_KIND_MODULES
from pigrocrm.core.due_items.schedule_schemas import CashScheduleQuery
from pigrocrm.core.modules import MODULE_KEYS

# (source kind, direction, state, amount, tax): one item per kind that belongs to a module and
# two of the core's. Each amount is a power of two, so a sum names the items it holds.
ITEMS: list[tuple[str, str, str, str, str | None]] = [
    ("purchase", "out", "expected", "1.00", None),
    ("recurring_expense", "out", "forecast", "2.00", None),
    ("iva_settlement", "out", "expected", "4.00", "vat"),
    ("tax_instalment", "out", "expected", "8.00", "irpef"),
    ("payroll", "out", "expected", "16.00", None),
    ("engagement", "out", "expected", "32.00", None),
    ("contract_occurrence", "in", "forecast", "64.00", None),
    ("sales_invoice", "in", "expected", "128.00", None),
    ("sales_draft", "in", "forecast", "256.00", None),
]
OFF_MODULES = ["purchases", "taxes", "staff", "collaborators", "contracts"]


@pytest.fixture
def world(db_session: Session) -> CashWorld:
    return CashWorld(db_session)


def _populate(world: CashWorld, due: dt.date) -> None:
    for kind, direction, state, amount, tax in ITEMS:
        world.item(amount, due, direction=direction, state=state, source_kind=kind, tax=tax)


def _on(off: str) -> set[str]:
    return set(MODULE_KEYS) - {off}


def _sum(*, direction: str, states: tuple[str, ...], off: str | None) -> Decimal:
    return sum(
        (
            Decimal(amount)
            for kind, item_direction, state, amount, _ in ITEMS
            if item_direction == direction
            and state in states
            and SOURCE_KIND_MODULES.get(kind) != off
        ),
        Decimal("0.00"),
    )


def test_every_source_kind_has_its_module_or_is_the_cores() -> None:
    assert set(SOURCE_KIND_MODULES) <= set(SOURCE_KINDS)
    assert set(SOURCE_KIND_MODULES.values()) <= set(MODULE_KEYS)
    assert {kind for kind in SOURCE_KINDS if kind not in SOURCE_KIND_MODULES} == {
        "sales_invoice",
        "sales_draft",
    }


@pytest.mark.parametrize("off", [*OFF_MODULES, "deals", "time", "cash"])
def test_the_weeks_leave_out_the_kinds_of_a_module_that_is_off(world: CashWorld, off: str) -> None:
    _populate(world, world.week(1))

    page = CashScheduleService(world.session).get_schedule(
        CashScheduleQuery(), ADMIN, modules=_on(off), profile="company"
    )

    week = page.weeks[1]
    assert week.in_expected == _sum(direction="in", states=("expected",), off=off)
    assert week.in_forecast == _sum(direction="in", states=("forecast",), off=off)
    assert week.out_expected == _sum(direction="out", states=("expected",), off=off)
    assert week.out_forecast == _sum(direction="out", states=("forecast",), off=off)
    assert week.item_count == sum(1 for kind, *_ in ITEMS if SOURCE_KIND_MODULES.get(kind) != off)


@pytest.mark.parametrize("off", OFF_MODULES)
def test_the_late_figures_leave_them_out_too(world: CashWorld, off: str) -> None:
    _populate(world, world.today - dt.timedelta(days=5))

    page = CashScheduleService(world.session).get_schedule(
        CashScheduleQuery(), ADMIN, modules=_on(off), profile="company"
    )

    assert page.late_receivable.total == Decimal("128.00")  # the invoice: a forecast is not late
    assert page.late_payable is not None
    assert page.late_payable.total == _sum(direction="out", states=("expected",), off=off)


@pytest.mark.parametrize("off", OFF_MODULES)
def test_the_payables_leave_them_out(world: CashWorld, off: str) -> None:
    _populate(world, world.today - dt.timedelta(days=5))
    _populate(world, world.today + dt.timedelta(days=40))

    page = CashScheduleService(world.session).get_payables(
        None, ADMIN, modules=_on(off), profile="company"
    )

    owed = _sum(direction="out", states=("expected",), off=off)
    assert page.total == owed * 2
    assert page.total_overdue == sum(
        1
        for kind, direction, state, *_ in ITEMS
        if direction == "out" and state == "expected" and SOURCE_KIND_MODULES.get(kind) != off
    )
    assert sum(row.amount for row in page.overdue) == owed


@pytest.mark.parametrize("off", OFF_MODULES)
def test_the_summary_leaves_them_out(world: CashWorld, off: str) -> None:
    _populate(world, world.today + dt.timedelta(days=2))

    page = CashScheduleService(world.session).get_summary(
        None, ADMIN, modules=_on(off), profile="company"
    )

    assert page.next_7_days.in_total == _sum(
        direction="in", states=("expected", "forecast"), off=off
    )
    assert page.next_7_days.in_forecast == _sum(direction="in", states=("forecast",), off=off)
    assert page.next_7_days.out_total == _sum(
        direction="out", states=("expected", "forecast"), off=off
    )
    assert page.next_7_days.out_forecast == _sum(direction="out", states=("forecast",), off=off)
    assert page.payable is not None
    assert page.payable.total == _sum(direction="out", states=("expected",), off=off)


@pytest.mark.parametrize("off", ["purchases", "taxes", "staff"])
def test_a_solo_taxes_table_and_the_open_flag_follow_the_modules(
    world: CashWorld, off: str
) -> None:
    _populate(world, world.week(1))

    page = CashScheduleService(world.session).get_schedule(
        CashScheduleQuery(), ADMIN, modules=_on(off), profile="solo"
    )

    expected_taxes = {
        tax
        for kind, _, _, _, tax in ITEMS
        if tax is not None and SOURCE_KIND_MODULES.get(kind) != off
    }
    assert page.taxes is not None and {row.tax for row in page.taxes} == expected_taxes
    assert page.has_open_items is True


def test_an_item_of_a_module_that_is_off_is_not_an_open_item(world: CashWorld) -> None:
    world.item("16.00", world.week(1), direction="out", source_kind="payroll")
    service = CashScheduleService(world.session)

    on = service.get_schedule(CashScheduleQuery(), ADMIN, modules=set(MODULE_KEYS))
    off = service.get_schedule(CashScheduleQuery(), ADMIN, modules=_on("staff"))

    assert on.has_open_items is True and off.has_open_items is False
    assert off.weeks[1].item_count == 0


def test_with_no_module_set_every_kind_counts(world: CashWorld) -> None:
    _populate(world, world.week(1))

    page = CashScheduleService(world.session).get_schedule(CashScheduleQuery(), ADMIN)

    assert page.weeks[1].item_count == len(ITEMS)
