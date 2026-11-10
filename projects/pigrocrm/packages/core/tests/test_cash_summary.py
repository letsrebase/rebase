"""The phone Home's money figures (REB-1025, spec 2026-11-09, due items and the cash schedule,
§ 4; brief 2026-11-10): today's balance, what is owed each way with its late part, the next
seven days and the worst alert.

The receivable is the figure the receivables dashboard prints, to the cent.
"""

import datetime as dt
import itertools
from decimal import Decimal

import pytest
from fakes.cash_fixtures import ADMIN, AGENT, COLLABORATOR, READONLY, CashWorld
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.due_items.schedule import CashScheduleService
from pigrocrm.core.due_items.schedule_schemas import CashScheduleQuery, CashSummary
from pigrocrm.core.due_items.schemas import SettlementCreate
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.repository import InvoiceRepository
from pigrocrm.core.modules.registry import Profile

_NUMBERS = itertools.count(1)


@pytest.fixture
def world(db_session: Session) -> CashWorld:
    return CashWorld(db_session)


def summary(
    world: CashWorld,
    actor: Actor = ADMIN,
    *,
    profile: Profile | None = "company",
    modules: set[str] | None = None,
    entity: object = None,
) -> CashSummary:
    return CashScheduleService(world.session).get_summary(
        entity,  # type: ignore[arg-type]
        actor,
        modules=modules,
        profile=profile,
    )


def _invoice(world: CashWorld, amount: str, due: dt.date | None) -> Invoice:
    invoice = Invoice(
        customer_id=world.customer.id,
        type="invoice",
        status="issued",
        year=2026,
        number=next(_NUMBERS),
        payment_status="unpaid",
        taxable_amount=Decimal(amount),
        vat_amount=Decimal("0.00"),
        stamp_duty=Decimal("0.00"),
        total=Decimal(amount),
        issue_date=world.today - dt.timedelta(days=1),
        due_date=due,
        sdi_document_type="TD01",
        currency="EUR",
        custom_fields={},
    )
    world.session.add(invoice)
    world.session.flush()
    return invoice


def test_the_receivable_is_the_receivables_dashboards_figure_to_the_cent(
    world: CashWorld,
) -> None:
    for amount, offset in [("100.00", -40), ("200.00", 0), ("300.50", 45), ("600.00", None)]:
        due = None if offset is None else world.today + dt.timedelta(days=offset)
        _invoice(world, amount, due)
    paid = _invoice(world, "7000.00", world.today - dt.timedelta(days=10))
    paid.payment_status = "paid"
    paid.payment_date = world.today
    world.session.flush()
    part = _invoice(world, "1000.00", world.today - dt.timedelta(days=2))
    item = world.service.repo.by_source("sales_invoice", part.id)
    assert item is not None
    world.service.settle(
        item.id, SettlementCreate(amount=Decimal("400.00"), settled_on=world.today), ADMIN
    )
    invoices = InvoiceRepository(world.session)

    page = summary(world)

    assert page.receivable.total == invoices.sum_unpaid() == Decimal("1800.50")
    assert page.receivable.late_total == invoices.sum_expired() == Decimal("700.00")
    assert page.receivable.late_count == invoices.count_overdue_not_paid() == 2
    # The same two figures read for one legal entity.
    scoped = summary(world, entity=world.entity)
    assert scoped.receivable == page.receivable
    assert scoped.receivable.total == invoices.sum_unpaid(world.entity)


def test_the_payable_counts_the_expected_items_going_out(world: CashWorld) -> None:
    world.item("100.00", world.today + dt.timedelta(days=20), direction="out")
    world.item("40.00", world.today - dt.timedelta(days=6), direction="out")
    world.item("900.00", world.today + dt.timedelta(days=20), direction="out", state="forecast")

    page = summary(world)

    assert page.payable is not None
    assert page.payable.model_dump() == {
        "total": Decimal("140.00"),
        "late_total": Decimal("40.00"),
        "late_count": 1,
    }
    assert page.show_outgoing is True


def test_the_next_seven_days_run_from_today_through_the_sixth_day_after(
    world: CashWorld,
) -> None:
    world.item("10.00", world.today)
    world.item("5.00", world.today + dt.timedelta(days=6), state="forecast")
    world.item("999.00", world.today + dt.timedelta(days=7))  # the seventh day after: not in
    world.item("999.00", world.today - dt.timedelta(days=1))  # late, not in
    world.item("30.00", world.today + dt.timedelta(days=2), direction="out")
    world.item("2.00", world.today + dt.timedelta(days=3), direction="out", state="forecast")
    world.item("88.00", world.today + dt.timedelta(days=3), currency="USD")

    page = summary(world)

    assert page.next_7_days.in_total == Decimal("15.00")
    assert page.next_7_days.in_forecast == Decimal("5.00")
    assert page.next_7_days.out_total == Decimal("32.00")
    assert page.next_7_days.out_forecast == Decimal("2.00")


def test_the_balance_is_the_total_of_the_euro_accounts_and_its_date(world: CashWorld) -> None:
    world.account("1000.00", as_of=world.today - dt.timedelta(days=2))
    world.account("500.25", name="Cassa")
    world.account("77.00", name="Dollari", currency="USD")

    page = summary(world)

    assert page.cash_enabled is True
    assert page.balance_today == Decimal("1500.25")
    assert page.balance_as_of == world.today - dt.timedelta(days=2)


def test_the_alert_is_the_worst_of_the_schedules(world: CashWorld) -> None:
    world.account("100.00")
    world.item("5000.00", world.week(1), direction="out")

    page = summary(world)

    assert page.alert is not None
    assert (page.alert.kind, page.alert.week_start) == ("below_zero", world.week(1))
    calm = summary(CashWorld(world.session), entity=world.second_entity())
    assert calm.alert is None


def test_a_solo_space_has_no_payable_and_no_outgoing(world: CashWorld) -> None:
    world.item("100.00", world.today + dt.timedelta(days=1))
    world.item("60.00", world.today + dt.timedelta(days=1), direction="out")
    world.account("40.00")

    page = summary(world, profile="solo")

    assert page.show_outgoing is False and page.payable is None
    assert page.next_7_days.in_total == Decimal("100.00")
    assert page.next_7_days.out_total is None and page.next_7_days.out_forecast is None
    assert page.next_7_days.in_forecast == Decimal("0.00")
    assert page.receivable.total == Decimal("100.00")
    assert page.balance_today == Decimal("40.00")
    # No projection for a solo space, so no below-zero alert either.
    assert page.alert is None


def test_who_may_not_read_the_accounts_has_no_balance_and_no_alert(world: CashWorld) -> None:
    world.account("100.00", as_of=world.today - dt.timedelta(days=400))
    world.item("5000.00", world.today + dt.timedelta(days=30), direction="out")
    world.item("300.00", world.today - dt.timedelta(days=3))
    world.item(
        "40.00",
        world.today + dt.timedelta(days=1),
        direction="out",
        source_kind="iva_settlement",
        tax="vat",
    )

    for actor in (COLLABORATOR, READONLY, AGENT):
        page = summary(world, actor)
        assert (page.balance_today, page.balance_as_of, page.alert) == (None, None, None), actor
        assert page.receivable.total == Decimal("300.00")
        assert page.payable is not None and page.payable.total == Decimal("5000.00")
        # The tax is not in their next seven days.
        assert page.next_7_days.out_total == Decimal("0.00")
    page = summary(world, ADMIN)
    assert page.next_7_days.out_total == Decimal("40.00")
    assert page.balance_today == Decimal("100.00")


def test_with_the_module_off_there_is_no_balance_but_the_owed_figures_stay(
    world: CashWorld,
) -> None:
    world.account("100.00")
    world.item("300.00", world.today - dt.timedelta(days=3))

    page = summary(world, modules={"deals"})

    assert page.cash_enabled is False
    assert (page.balance_today, page.balance_as_of, page.alert) == (None, None, None)
    assert page.receivable.total == Decimal("300.00")
    assert page.receivable.late_count == 1


def test_the_server_says_when_the_balance_is_below_zero(world: CashWorld) -> None:
    world.account("-50.00", name="Scoperto")
    world.account("20.00", name="Altro")

    page = summary(world)
    schedule = CashScheduleService(world.session).get_schedule(
        CashScheduleQuery(), ADMIN, modules=None, profile="company"
    )

    assert page.balance_today == Decimal("-30.00") and page.balance_below_zero is True
    assert schedule.total_balance is not None and schedule.total_balance.below_zero is True
    assert schedule.accounts is not None
    assert {card.name: card.below_zero for card in schedule.accounts} == {
        "Scoperto": True,
        "Altro": False,
    }
    # Zero is not below zero, and no balance says nothing.
    assert (
        summary(CashWorld(world.session), entity=world.second_entity()).balance_below_zero is False
    )
    assert summary(world, COLLABORATOR).balance_below_zero is False
