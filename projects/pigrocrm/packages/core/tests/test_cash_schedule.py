"""The cash schedule (REB-1025, spec 2026-11-09, due items and the cash schedule, § 4): the weeks,
what is expected against forecast, what is late, the projected balance, who sees what and the
profiles.

Dates are relative to today (`CashWorld`): the schedule is read from today forward.
"""

import datetime as dt
from decimal import Decimal

import pytest
from fakes.cash_fixtures import ADMIN, AGENT, AGENT_FULL, COLLABORATOR, READONLY, CashWorld
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.bank.coverage import MissingPeriod
from pigrocrm.core.due_items import schedule as schedule_module
from pigrocrm.core.due_items.schedule import CashScheduleService
from pigrocrm.core.due_items.schedule_schemas import (
    HORIZON_OPTIONS,
    CashSchedule,
    CashScheduleQuery,
)
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.modules.registry import Profile

D = dt.date


@pytest.fixture
def world(db_session: Session) -> CashWorld:
    return CashWorld(db_session)


def read(
    world: CashWorld,
    actor: Actor = ADMIN,
    *,
    weeks: int = 13,
    profile: Profile | None = "company",
    modules: set[str] | None = None,
    entity: object = None,
) -> CashSchedule:
    return CashScheduleService(world.session).get_schedule(
        CashScheduleQuery(weeks=weeks, legal_entity_id=entity),  # type: ignore[arg-type]
        actor,
        modules=modules,
        profile=profile,
    )


def test_a_week_is_monday_to_sunday_and_the_current_one_is_whole(world: CashWorld) -> None:
    world.item("100.00", world.today)
    world.item("20.00", world.week(0, 6))  # the Sunday of this week
    world.item("300.00", world.week(1))  # next Monday
    world.item("7000.00", world.week(13))  # the first day past a 13-week horizon
    world.item("5.00", world.today - dt.timedelta(days=1))  # yesterday: late, in no week
    world.item("9.00", None)  # no date: in no week and not late

    page = read(world)

    assert [option for option in page.horizon_options] == list(HORIZON_OPTIONS)
    assert page.horizon_weeks == 13 and len(page.weeks) == 13
    first = page.weeks[0]
    assert (first.start, first.end, first.is_current) == (world.week(0), world.week(0, 6), True)
    assert not any(week.is_current for week in page.weeks[1:])
    assert all(
        later.start == earlier.start + dt.timedelta(days=7)
        for earlier, later in zip(page.weeks, page.weeks[1:], strict=False)
    )
    assert first.in_expected == Decimal("120.00")
    assert page.weeks[1].in_expected == Decimal("300.00")
    assert sum(week.in_expected for week in page.weeks) == Decimal("420.00")
    assert page.late_receivable.model_dump() == {
        "count": 1,
        "total": Decimal("5.00"),
        "oldest_days": 1,
    }
    assert page.has_open_items is True

    wide = read(world, weeks=26)
    assert wide.weeks[13].in_expected == Decimal("7000.00")


def test_a_horizon_that_is_not_offered_is_refused(world: CashWorld) -> None:
    with pytest.raises(ValidationFailed) as refused:
        read(world, weeks=7)
    assert refused.value.details["field"] == "weeks"


def test_expected_and_forecast_are_two_figures_in_each_direction(world: CashWorld) -> None:
    due = world.week(1, 2)
    world.item("100.00", due)
    world.item("40.00", due, state="forecast")
    world.item("60.00", due, direction="out")
    world.item("25.00", due, direction="out", state="forecast")
    world.item("10.00", due, estimated=True)

    week = read(world).weeks[1]

    assert (week.in_expected, week.in_forecast) == (Decimal("110.00"), Decimal("40.00"))
    assert (week.out_expected, week.out_forecast) == (Decimal("60.00"), Decimal("25.00"))
    assert week.net == Decimal("65.00")
    assert (week.item_count, week.has_forecast, week.has_estimated_date) == (5, True, True)
    quiet = read(world).weeks[2]
    assert (quiet.item_count, quiet.has_forecast, quiet.has_estimated_date) == (0, False, False)
    assert quiet.net == Decimal("0.00")


def test_a_part_paid_item_counts_for_what_is_left(world: CashWorld) -> None:
    from pigrocrm.core.due_items.schemas import SettlementCreate

    item = world.item("1000.00", world.week(1))
    world.service.settle(
        item, SettlementCreate(amount=Decimal("400.00"), settled_on=world.today), ADMIN
    )

    assert read(world).weeks[1].in_expected == Decimal("600.00")


def test_a_forecast_whose_date_passed_is_money_to_come_no_more(world: CashWorld) -> None:
    world.item("500.00", world.today - dt.timedelta(days=3), state="forecast")

    page = read(world)

    assert page.late_receivable.count == 0
    assert page.has_open_items is False
    assert all(week.in_forecast == Decimal("0.00") for week in page.weeks)


def test_what_is_late_is_apart_from_the_weeks_and_names_its_age(world: CashWorld) -> None:
    world.item("100.00", world.today - dt.timedelta(days=40))
    world.item("50.00", world.today - dt.timedelta(days=10))
    world.item("30.00", world.today - dt.timedelta(days=2), direction="out")
    world.item("70.00", world.today)  # due today is due today, not late

    page = read(world)

    assert page.late_receivable.model_dump() == {
        "count": 2,
        "total": Decimal("150.00"),
        "oldest_days": 40,
    }
    assert page.late_payable is not None
    assert page.late_payable.model_dump() == {
        "count": 1,
        "total": Decimal("30.00"),
        "oldest_days": 2,
    }
    assert sum(week.in_expected for week in page.weeks) == Decimal("70.00")
    empty = read(CashWorld(world.session), entity=world.second_entity())
    assert empty.late_receivable.oldest_days is None and empty.has_open_items is False


def test_an_item_in_another_currency_is_in_no_figure_but_it_is_open(world: CashWorld) -> None:
    world.item("900.00", world.week(1), currency="USD")

    page = read(world)

    assert sum(week.in_expected for week in page.weeks) == Decimal("0.00")
    assert page.has_open_items is True


def test_the_legal_entity_filter_scopes_every_figure(world: CashWorld) -> None:
    other = world.second_entity()
    world.item("100.00", world.week(1))
    world.item("7.00", world.week(1), entity=other)
    world.item("3.00", world.today - dt.timedelta(days=5), entity=other)

    everything = read(world)
    only_other = read(world, entity=other)
    only_studio = read(world, entity=world.entity)

    assert everything.weeks[1].in_expected == Decimal("107.00")
    assert everything.late_receivable.total == Decimal("3.00")
    assert only_other.weeks[1].in_expected == Decimal("7.00")
    assert only_other.legal_entity_id == other
    assert only_studio.weeks[1].in_expected == Decimal("100.00")
    assert only_studio.late_receivable.count == 0


# --- who sees what -----------------------------------------------------------------------------


def _restricted_world(world: CashWorld) -> None:
    world.item("100.00", world.week(1))
    world.item("40.00", world.week(1), direction="out", source_kind="iva_settlement", tax="vat")
    world.item("900.00", world.week(1), direction="out", source_kind="payroll")
    world.item(
        "11.00",
        world.today - dt.timedelta(days=3),
        direction="out",
        source_kind="withholding",
        tax="withholding",
    )


def test_the_restricted_kinds_are_left_out_of_every_figure_for_who_may_not_read_them(
    world: CashWorld,
) -> None:
    from pigrocrm.core.due_items.models import RESTRICTED_SOURCE_KINDS

    assert {"iva_settlement", "payroll", "withholding"} <= RESTRICTED_SOURCE_KINDS
    _restricted_world(world)

    admin = read(world, ADMIN)
    assert admin.weeks[1].out_expected == Decimal("940.00")
    assert admin.late_payable is not None and admin.late_payable.total == Decimal("11.00")

    for actor in (COLLABORATOR, READONLY, AGENT):
        page = read(world, actor)
        assert page.weeks[1].out_expected == Decimal("0.00"), actor
        assert page.weeks[1].item_count == 1, actor
        assert page.late_payable is not None and page.late_payable.total == Decimal("0.00"), actor
    assert read(world, AGENT_FULL).weeks[1].out_expected == Decimal("940.00")


def test_the_schedule_is_a_plain_read_for_every_credential(world: CashWorld) -> None:
    from pigrocrm.core.agent_actions import AGENT_ALLOWED_ACTIONS

    assert "read_cash_schedule" in AGENT_ALLOWED_ACTIONS
    for actor in (ADMIN, COLLABORATOR, READONLY, AGENT, AGENT_FULL):
        assert len(read(world, actor).weeks) == 13, actor


# --- the solo profile --------------------------------------------------------------------------


def test_a_solo_space_has_money_in_and_its_own_taxes_and_no_outgoing_column(
    world: CashWorld,
) -> None:
    world.item("100.00", world.week(1))
    world.item("60.00", world.week(1), direction="out")
    tax = world.item(
        "200.00", world.week(2), direction="out", source_kind="iva_settlement", tax="vat"
    )
    forecast_tax = world.item(
        "50.00",
        world.week(3),
        direction="out",
        state="forecast",
        source_kind="tax_instalment",
        tax="irpef",
    )
    world.item("999.00", world.week(1), direction="out", source_kind="payroll")
    world.account("1000.00")

    page = read(world, profile="solo")

    assert page.profile == "solo" and page.show_outgoing is False
    assert page.late_payable is None
    assert page.weeks[1].in_expected == Decimal("100.00")
    for week in page.weeks:
        assert (week.out_expected, week.out_forecast, week.net, week.balance_end) == (
            None,
            None,
            None,
            None,
        )
        assert week.below_zero is False
    assert page.show_balance is False
    # «Saldo oggi» is still there, and the taxes are a table apart, never taken from it.
    assert page.total_balance is not None and page.total_balance.amount == Decimal("1000.00")
    assert page.taxes is not None
    assert [(t.due_item_id, t.state, t.amount, t.tax) for t in page.taxes] == [
        (tax, "expected", Decimal("200.00"), "vat"),
        (forecast_tax, "forecast", Decimal("50.00"), "irpef"),
    ]
    assert page.has_open_items is True
    # The tax is a restricted kind: whoever may not read it has no table at all.
    assert read(world, COLLABORATOR, profile="solo").taxes is None
    assert read(world, ADMIN, profile="company").taxes is None


def test_a_space_with_no_profile_reads_as_a_company(world: CashWorld) -> None:
    world.item("60.00", world.week(1), direction="out")
    page = read(world, profile=None)
    assert page.show_outgoing is True and page.weeks[1].out_expected == Decimal("60.00")


# --- balances ----------------------------------------------------------------------------------


def test_the_projected_balance_is_one_figure_with_the_forecasts_in_and_the_late_out(
    world: CashWorld,
) -> None:
    world.account("1000.00", name="Conto operativo")
    world.account("250.50", name="Cassa")
    world.item("300.00", world.week(0, 6 if world.today.weekday() < 6 else 0))
    world.item("100.00", world.week(1), state="forecast")
    world.item("2000.00", world.week(2), direction="out")
    world.item("80.00", world.today - dt.timedelta(days=9), direction="out")  # late: not in it

    page = read(world)

    assert page.show_balance is True and page.cash_enabled is True
    assert page.total_balance is not None
    assert page.total_balance.amount == Decimal("1250.50")
    assert (page.total_balance.currency, page.total_balance.excluded_currencies) == ("EUR", [])
    assert page.total_balance.as_of == world.today
    assert [week.balance_end for week in page.weeks[:4]] == [
        Decimal("1550.50"),
        Decimal("1650.50"),
        Decimal("-349.50"),
        Decimal("-349.50"),
    ]
    assert [week.below_zero for week in page.weeks[:4]] == [False, False, True, True]
    assert page.late_payable is not None and page.late_payable.total == Decimal("80.00")
    below = page.alerts[0]
    assert (below.kind, below.week_start, below.week_end) == (
        "below_zero",
        world.week(2),
        world.week(2, 6),
    )
    assert [a.kind for a in page.alerts].count("below_zero") == 1


def test_the_account_cards_say_where_each_balance_stands(world: CashWorld) -> None:
    world.account("100.00", name="B conto", as_of=world.today - dt.timedelta(days=3))
    world.account("5.00", name="A conto")
    world.account(None, name="C conto")

    page = read(world)

    assert page.accounts is not None
    assert [card.name for card in page.accounts] == ["A conto", "B conto", "C conto"]
    cards = {card.name: card for card in page.accounts}
    assert cards["B conto"].balance == Decimal("100.00")
    assert (cards["B conto"].balance_as_of, cards["B conto"].stale_days) == (
        world.today - dt.timedelta(days=3),
        3,
    )
    assert cards["B conto"].legal_entity_name and cards["B conto"].currency == "EUR"
    # An account with no figure is a card with none: never 0,00.
    assert (cards["C conto"].balance, cards["C conto"].balance_as_of) == (None, None)
    assert page.total_balance is not None and page.total_balance.amount == Decimal("105.00")
    assert page.total_balance.as_of == world.today - dt.timedelta(days=3)


def test_an_account_in_another_currency_is_listed_and_not_added(world: CashWorld) -> None:
    world.account("100.00")
    world.account("1350.00", name="Dollari", currency="USD")

    page = read(world)

    assert page.total_balance is not None
    assert page.total_balance.amount == Decimal("100.00")
    assert page.total_balance.excluded_currencies == ["USD"]
    assert page.accounts is not None and {card.currency for card in page.accounts} == {"EUR", "USD"}


def test_with_no_euro_balance_there_is_no_total_and_no_projection(world: CashWorld) -> None:
    world.account("1350.00", name="Dollari", currency="USD")
    world.item("10.00", world.week(1))

    page = read(world)

    assert page.accounts is not None and len(page.accounts) == 1
    assert page.total_balance is None and page.show_balance is False
    assert all(week.balance_end is None for week in page.weeks)


def test_a_space_with_the_module_on_and_no_account_has_an_empty_list(world: CashWorld) -> None:
    page = read(world, modules={"cash"})
    assert page.cash_enabled is True and page.accounts == []
    assert page.total_balance is None and page.show_balance is False
    assert page.alerts == []


def test_who_may_not_read_the_accounts_gets_no_balance_at_all(world: CashWorld) -> None:
    world.account("10.00", as_of=world.today - dt.timedelta(days=400))
    world.item("5000.00", world.week(1), direction="out")

    for actor in (COLLABORATOR, READONLY, AGENT):
        page = read(world, actor)
        assert page.cash_enabled is True
        assert page.accounts is None and page.total_balance is None, actor
        assert page.show_balance is False and page.alerts == [], actor
        assert all(week.balance_end is None and not week.below_zero for week in page.weeks), actor
        assert page.weeks[1].out_expected == Decimal("5000.00")
    full = read(world, AGENT_FULL)
    assert full.accounts is not None and full.total_balance is not None


def test_with_the_module_off_the_schedule_answers_without_balances(world: CashWorld) -> None:
    world.account("10.00", as_of=world.today - dt.timedelta(days=400))
    world.item("5000.00", world.week(1), direction="out")

    page = read(world, modules={"deals", "purchases"})

    assert page.cash_enabled is False
    assert page.accounts is None and page.total_balance is None and page.show_balance is False
    assert page.alerts == []
    assert page.weeks[1].out_expected == Decimal("5000.00")
    assert page.late_receivable.count == 0


def test_the_legal_entity_filter_scopes_the_accounts_too(world: CashWorld) -> None:
    other = world.second_entity()
    world.account("100.00", name="Studio")
    world.account("7.00", name="Altra", entity=other)

    assert read(world).total_balance.amount == Decimal("107.00")  # type: ignore[union-attr]
    only = read(world, entity=other)
    assert only.total_balance is not None and only.total_balance.amount == Decimal("7.00")
    assert only.accounts is not None
    assert [(card.name, card.legal_entity_id) for card in only.accounts] == [("Altra", other)]


def test_an_archived_account_is_not_on_the_schedule(world: CashWorld) -> None:
    gone = world.account("100.00", name="Chiuso")
    gone.archived_at = dt.datetime.now(dt.UTC)
    world.account("5.00")
    world.session.flush()

    page = read(world)

    assert page.accounts is not None and [card.name for card in page.accounts] == [
        "Conto operativo"
    ]
    assert page.total_balance is not None and page.total_balance.amount == Decimal("5.00")


# --- alerts ------------------------------------------------------------------------------------


def test_the_alerts_come_worst_first_and_a_missing_statement_hides_the_stale_balance(
    world: CashWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    quiet = world.account("100.00", name="A quiet", as_of=world.today - dt.timedelta(days=60))
    behind = world.account("100.00", name="B behind", as_of=world.today - dt.timedelta(days=80))
    world.account("100.00", name="C fresh")
    world.item("5000.00", world.week(1), direction="out")
    period = MissingPeriod(
        behind.id,
        "B behind",
        world.today - dt.timedelta(days=75),
        world.today - dt.timedelta(days=45),
    )
    monkeypatch.setattr(schedule_module, "missing_periods", lambda session, today: [period])

    page = read(world)

    assert [(a.kind, a.account_name) for a in page.alerts] == [
        ("below_zero", None),
        ("missing_statement", "B behind"),
        ("stale_balance", "A quiet"),
    ]
    missing = page.alerts[1]
    assert (missing.account_id, missing.period_from, missing.period_to, missing.days) == (
        behind.id,
        period.period_from,
        period.period_to,
        45,
    )
    stale = page.alerts[2]
    assert (stale.account_id, stale.days) == (quiet.id, 60)


def test_a_statement_missing_in_the_data_is_an_alert(world: CashWorld) -> None:
    account = world.account("100.00", as_of=world.today - dt.timedelta(days=100))

    page = read(world)

    kinds = [a.kind for a in page.alerts]
    assert "missing_statement" in kinds
    assert "stale_balance" not in kinds  # the account is already reported missing
    assert {a.account_id for a in page.alerts} == {account.id}
    assert all(a.account_name == account.name for a in page.alerts)
