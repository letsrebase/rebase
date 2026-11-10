"""The cash schedule, and the phone Home's figures read from the same place (spec 2026-11-09,
due items and the cash schedule, § 4; brief 2026-11-10).

«Cassa» answers when money moves: today's balance per account, then week by week what comes in
and what goes out (forecast apart from expected) and the balance projected at each week's end,
with what is late shown apart and never in that balance. Every figure is computed here, on the
server, from the open due items and the bank balances; nothing is left for a browser to add.

**Who sees what.** Any member reads the schedule of the ordinary kinds. The kinds of
`RESTRICTED_SOURCE_KINDS` are left out of every total and every week for whoever may not read
them (an administrator, and for a token `full_access`: `read_fiscal_due_items`, as
`DueItemService.list`), and so are the balances, the account cards and every alert about them
for whoever may not read the bank accounts (`read_bank_accounts`, the same rule). The balance
part exists only with the module `cash` on; the schedule and the payables do not need it.

**Profiles.** A solo space has no outgoing column and no balance by week: the weeks carry money
in, its own taxes are a table apart that no balance subtracts, and the late payable is absent.
A space with no profile chosen yet reads as the company one.

**What a week is.** Monday to Sunday. The current week is whole: today and the days after it
count in it, the days of it before today are late, as every item due before today. An undated
item is in no week and not late.
"""

import datetime as dt
from collections.abc import Set as AbstractSet
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.bank.balances import AccountBalance, account_balances
from pigrocrm.core.bank.coverage import missing_periods
from pigrocrm.core.bank.models import BankAccount
from pigrocrm.core.db import today_local
from pigrocrm.core.due_items.payables import build_payables, empty_payables
from pigrocrm.core.due_items.schedule_queries import DayRow, Owed, ScheduleQueries
from pigrocrm.core.due_items.schedule_schemas import (
    DEFAULT_HORIZON_WEEKS,
    HORIZON_OPTIONS,
    SCHEDULE_CURRENCY,
    CashAccountCard,
    CashAlert,
    CashSchedule,
    CashScheduleQuery,
    CashSummary,
    CashWeek,
    LateFigure,
    Next7,
    OwedFigure,
    PayablesDashboard,
    SoloTaxRow,
    TotalBalance,
)
from pigrocrm.core.errors import AgentForbidden, PermissionDenied, ValidationFailed
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.modules.registry import Profile
from pigrocrm.core.money import ZERO_MONEY

DAYS_IN_WEEK = 7
NEXT_DAYS = 7


def may_read_fiscal(actor: Actor) -> bool:
    """Whether the caller reads the tax, IVA, withholding, payroll and engagement items: the
    rule `DueItemService.list` applies."""
    try:
        actor.require_admin("read_fiscal_due_items")
    except (PermissionDenied, AgentForbidden):
        return False
    return True


def may_read_balances(actor: Actor) -> bool:
    """Whether the caller reads the bank accounts, and so the balances: an administrator and,
    for a token, `full_access`."""
    try:
        actor.require_admin("read_bank_accounts")
    except (PermissionDenied, AgentForbidden):
        return False
    return True


def _late_figure(owed: Owed, today: dt.date) -> LateFigure:
    return LateFigure(
        count=owed.late_count,
        total=owed.late_total,
        oldest_days=None if owed.oldest_due is None else (today - owed.oldest_due).days,
    )


class _Bucket:
    """One week's figures while the day rows are poured in."""

    def __init__(self) -> None:
        self.in_expected = ZERO_MONEY
        self.in_forecast = ZERO_MONEY
        self.out_expected = ZERO_MONEY
        self.out_forecast = ZERO_MONEY
        self.item_count = 0
        self.has_forecast = False
        self.has_estimated_date = False

    def add(self, row: DayRow) -> None:
        match (row.direction, row.state):
            case ("in", "expected"):
                self.in_expected += row.amount
            case ("in", _):
                self.in_forecast += row.amount
            case (_, "expected"):
                self.out_expected += row.amount
            case _:
                self.out_forecast += row.amount
        self.item_count += row.number
        self.has_forecast = self.has_forecast or row.state == "forecast"
        self.has_estimated_date = self.has_estimated_date or row.estimated


class _Balances:
    """The accounts the caller may read, with what the schedule derives from them."""

    def __init__(
        self, cards: list[CashAccountCard], total: TotalBalance | None, ids: set[UUID]
    ) -> None:
        self.cards = cards
        self.total = total
        self.ids = ids


class CashScheduleService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ---- the schedule ---------------------------------------------------------------------

    def get_schedule(
        self,
        query: CashScheduleQuery,
        actor: Actor,
        *,
        modules: AbstractSet[str] | None = None,
        profile: Profile | None = None,
    ) -> CashSchedule:
        """The schedule over `query.weeks` weeks, from the Monday of the current one. `modules`
        is what the space has on (`None`: everything) and `profile` its profile: a surface
        hands them over, since core reads no settings."""
        actor.require_agent_allowed("read_cash_schedule")
        if query.weeks not in HORIZON_OPTIONS:
            raise ValidationFailed(
                "cash_schedule",
                "weeks",
                "orizzonte non previsto",
                expected=", ".join(str(option) for option in HORIZON_OPTIONS) + " settimane",
            )
        today = today_local()
        queries = self._queries(query.legal_entity_id, actor, today)
        show_outgoing = profile != "solo"
        cash_enabled = modules is None or "cash" in modules
        balances = (
            self._balances(query.legal_entity_id, today)
            if cash_enabled and may_read_balances(actor)
            else None
        )
        total = None if balances is None else balances.total
        show_balance = total is not None and show_outgoing

        monday = today - dt.timedelta(days=today.weekday())
        weeks = self._weeks(
            queries,
            monday,
            query.weeks,
            show_outgoing=show_outgoing,
            opening=total.amount if show_balance and total is not None else None,
        )
        taxes = None
        if not show_outgoing and may_read_fiscal(actor):
            taxes = [
                SoloTaxRow(
                    due_item_id=row.due_item_id,
                    tax=row.tax,
                    description=row.description,
                    due_date=row.due_date,
                    state="forecast" if row.state == "forecast" else "expected",
                    amount=row.amount,
                )
                for row in queries.tax_rows()
            ]
        return CashSchedule(
            calculated_at=datetime.now(UTC),
            today=today,
            legal_entity_id=query.legal_entity_id,
            horizon_weeks=query.weeks,
            horizon_options=list(HORIZON_OPTIONS),
            profile=profile,
            cash_enabled=cash_enabled,
            show_outgoing=show_outgoing,
            show_balance=show_balance,
            accounts=None if balances is None else balances.cards,
            total_balance=total,
            late_receivable=_late_figure(queries.owed("in"), today),
            late_payable=_late_figure(queries.owed("out"), today) if show_outgoing else None,
            weeks=weeks,
            taxes=taxes,
            alerts=self._alerts(balances, weeks, today) if balances is not None else [],
            has_open_items=queries.has_open_items(outgoing=show_outgoing),
        )

    # ---- the payables mirror ----------------------------------------------------------------

    def get_payables(
        self, legal_entity_id: UUID | None, actor: Actor, *, profile: Profile | None = None
    ) -> PayablesDashboard:
        """What we owe, in the receivables' six bands. A solo space shows no outgoing money:
        it answers the same shape at zero, so a client that asks is not met by a 404."""
        actor.require_agent_allowed("read_cash_schedule")
        today = today_local()
        calculated_at = datetime.now(UTC)
        if profile == "solo":
            return empty_payables(
                today=today, legal_entity_id=legal_entity_id, calculated_at=calculated_at
            )
        return build_payables(
            self._queries(legal_entity_id, actor, today),
            legal_entity_id=legal_entity_id,
            calculated_at=calculated_at,
        )

    # ---- the phone's figures ---------------------------------------------------------------

    def get_summary(
        self,
        legal_entity_id: UUID | None,
        actor: Actor,
        *,
        modules: AbstractSet[str] | None = None,
        profile: Profile | None = None,
    ) -> CashSummary:
        """The Home's money figures: today's balance, what is owed to us and by us with the
        late part of each, the next seven days and the worst alert. The balance and the alert
        are the schedule's, over its default horizon."""
        schedule = self.get_schedule(
            CashScheduleQuery(weeks=DEFAULT_HORIZON_WEEKS, legal_entity_id=legal_entity_id),
            actor,
            modules=modules,
            profile=profile,
        )
        today = schedule.today
        queries = self._queries(legal_entity_id, actor, today)
        show_outgoing = schedule.show_outgoing
        receivable = queries.owed("in")
        payable = queries.owed("out") if show_outgoing else None
        rows = queries.day_rows(
            today,
            today + dt.timedelta(days=NEXT_DAYS - 1),
            ("in", "out") if show_outgoing else ("in",),
        )
        in_total = sum((row.amount for row in rows if row.direction == "in"), ZERO_MONEY)
        out_total = sum((row.amount for row in rows if row.direction == "out"), ZERO_MONEY)
        return CashSummary(
            calculated_at=schedule.calculated_at,
            today=today,
            cash_enabled=schedule.cash_enabled,
            show_outgoing=show_outgoing,
            balance_today=None if schedule.total_balance is None else schedule.total_balance.amount,
            balance_as_of=None if schedule.total_balance is None else schedule.total_balance.as_of,
            receivable=OwedFigure(
                total=receivable.total,
                late_total=receivable.late_total,
                late_count=receivable.late_count,
            ),
            payable=None
            if payable is None
            else OwedFigure(
                total=payable.total, late_total=payable.late_total, late_count=payable.late_count
            ),
            next_7_days=Next7(in_total=in_total, out_total=out_total if show_outgoing else None),
            alert=schedule.alerts[0] if schedule.alerts else None,
        )

    # ---- internals --------------------------------------------------------------------------

    def _queries(
        self, legal_entity_id: UUID | None, actor: Actor, today: dt.date
    ) -> ScheduleQueries:
        return ScheduleQueries(
            self.session,
            today=today,
            legal_entity_id=legal_entity_id,
            restricted=may_read_fiscal(actor),
            currency=SCHEDULE_CURRENCY,
        )

    def _weeks(
        self,
        queries: ScheduleQueries,
        monday: dt.date,
        horizon: int,
        *,
        show_outgoing: bool,
        opening: Decimal | None,
    ) -> list[CashWeek]:
        today = queries.today
        last = monday + dt.timedelta(days=horizon * DAYS_IN_WEEK - 1)
        buckets = [_Bucket() for _ in range(horizon)]
        for row in queries.day_rows(today, last, ("in", "out") if show_outgoing else ("in",)):
            buckets[(row.due_date - monday).days // DAYS_IN_WEEK].add(row)
        weeks: list[CashWeek] = []
        balance = opening
        for index, bucket in enumerate(buckets):
            start = monday + dt.timedelta(days=index * DAYS_IN_WEEK)
            net = (
                bucket.in_expected + bucket.in_forecast - bucket.out_expected - bucket.out_forecast
            )
            balance_end = None
            if balance is not None:
                balance += net
                balance_end = balance
            weeks.append(
                CashWeek(
                    start=start,
                    end=start + dt.timedelta(days=DAYS_IN_WEEK - 1),
                    is_current=index == 0,
                    in_expected=bucket.in_expected,
                    in_forecast=bucket.in_forecast,
                    out_expected=bucket.out_expected if show_outgoing else None,
                    out_forecast=bucket.out_forecast if show_outgoing else None,
                    net=net if show_outgoing else None,
                    balance_end=balance_end,
                    below_zero=balance_end is not None and balance_end < 0,
                    item_count=bucket.item_count,
                    has_forecast=bucket.has_forecast,
                    has_estimated_date=bucket.has_estimated_date,
                )
            )
        return weeks

    def _balances(self, legal_entity_id: UUID | None, today: dt.date) -> _Balances:
        stmt = (
            select(BankAccount, LegalEntity.name)
            .join(LegalEntity, LegalEntity.id == BankAccount.legal_entity_id)
            .where(BankAccount.archived_at.is_(None))
            .order_by(BankAccount.name, BankAccount.id)
        )
        if legal_entity_id is not None:
            stmt = stmt.where(BankAccount.legal_entity_id == legal_entity_id)
        accounts = [(row[0], row[1]) for row in self.session.execute(stmt).all()]
        found = account_balances(
            self.session, account_ids=[account.id for account, _ in accounts], today=today
        )
        cards: list[CashAccountCard] = []
        counted: list[AccountBalance] = []
        excluded: set[str] = set()
        without_balance = 0
        for account, entity_name in accounts:
            balance = found[account.id]
            cards.append(
                CashAccountCard(
                    id=account.id,
                    legal_entity_id=account.legal_entity_id,
                    legal_entity_name=entity_name,
                    name=account.name,
                    bank=account.bank,
                    currency=account.currency,
                    balance=balance.amount,
                    balance_as_of=balance.as_of,
                    stale_days=balance.stale_days,
                    is_stale=balance.is_stale,
                )
            )
            if balance.amount is None:
                if account.currency == SCHEDULE_CURRENCY:
                    without_balance += 1
                continue
            if account.currency == SCHEDULE_CURRENCY:
                counted.append(balance)
            else:
                excluded.add(account.currency)
        total = None
        if counted:
            amount = sum((b.amount for b in counted if b.amount is not None), ZERO_MONEY)
            dates = [b.as_of for b in counted if b.as_of is not None]
            total = TotalBalance(
                amount=amount,
                as_of=min(dates) if dates else None,
                excluded_currencies=sorted(excluded),
                accounts_without_balance=without_balance,
            )
        return _Balances(cards, total, {account.id for account, _ in accounts})

    def _alerts(
        self, balances: _Balances, weeks: list[CashWeek], today: dt.date
    ) -> list[CashAlert]:
        """Worst first: the first week the projected balance is below zero, then a statement
        that is missing, then a balance gone stale whose statement is not already missing."""
        alerts: list[CashAlert] = []
        below = next((week for week in weeks if week.below_zero), None)
        if below is not None:
            alerts.append(CashAlert(kind="below_zero", week_start=below.start, week_end=below.end))
        missing = [p for p in missing_periods(self.session, today) if p.account_id in balances.ids]
        for period in missing:
            alerts.append(
                CashAlert(
                    kind="missing_statement",
                    account_id=period.account_id,
                    account_name=period.account_name,
                    period_from=period.period_from,
                    period_to=period.period_to,
                    days=(today - period.period_to).days,
                )
            )
        reported = {period.account_id for period in missing}
        for card in balances.cards:
            if card.is_stale and card.id not in reported:
                alerts.append(
                    CashAlert(
                        kind="stale_balance",
                        account_id=card.id,
                        account_name=card.name,
                        days=card.stale_days,
                    )
                )
        return alerts
