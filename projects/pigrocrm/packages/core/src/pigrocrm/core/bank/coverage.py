"""Which months of an account a statement covers, and which are missing (spec 2026-11-09, § 4).

A month is `covered` when the union of the account's `imported` statements' periods holds the
whole of it, `missing` when it is not and its period (the month; for a quarterly account, the
calendar quarter it falls in) ended more than the grace ago, else `pending`. A month before the
account existed in the CRM (or before its opening balance) is `pending` too: nobody is asked
for a statement of a time the account was not here. The window is the last twelve calendar
months, the current one included.
"""

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.bank.constants import STATEMENT_GRACE_DAYS
from pigrocrm.core.bank.models import BankAccount, BankStatement
from pigrocrm.core.db.clock import add_months, local_date, month_bounds, today_local

CoverageState = Literal["covered", "missing", "pending"]
WINDOW_MONTHS = 12


@dataclass(frozen=True)
class CoverageMonth:
    month: str
    period_from: dt.date
    period_to: dt.date
    state: CoverageState


@dataclass(frozen=True)
class AccountCoverage:
    grace_days: int
    months: list[CoverageMonth]


@dataclass(frozen=True)
class MissingPeriod:
    """A run of consecutive missing months of one account (or quarters, for a quarterly one)."""

    account_id: UUID
    account_name: str
    period_from: dt.date
    period_to: dt.date


def _window(today: dt.date) -> list[tuple[dt.date, dt.date]]:
    first_of_month = today.replace(day=1)
    return [
        month_bounds(day.year, day.month)
        for day in (add_months(first_of_month, -back) for back in range(WINDOW_MONTHS - 1, -1, -1))
    ]


def _quarter_end(day: dt.date) -> dt.date:
    last_month = (day.month - 1) // 3 * 3 + 3
    return month_bounds(day.year, last_month)[1]


def _covers(periods: Iterable[tuple[dt.date, dt.date]], start: dt.date, end: dt.date) -> bool:
    """True when the union of the closed `periods` holds every day of `start`..`end`. Two
    periods that touch (one ends the day before the next starts) join."""
    reached = start
    for period_from, period_to in sorted(periods):
        if period_from > reached:
            return False
        if period_to >= reached:
            reached = period_to + dt.timedelta(days=1)
        if reached > end:
            return True
    return reached > end


def _first_asked_month(account: BankAccount) -> dt.date:
    """The first day of the first month a statement is asked for: the one the account was
    created in, or the one its opening balance is dated in when that is later."""
    first = local_date(account.created_at).replace(day=1)
    if account.opening_balance_date is not None:
        first = max(first, account.opening_balance_date.replace(day=1))
    return first


def _months(
    account: BankAccount, periods: list[tuple[dt.date, dt.date]], today: dt.date
) -> list[CoverageMonth]:
    asked_from = _first_asked_month(account)
    months: list[CoverageMonth] = []
    for start, end in _window(today):
        state: CoverageState
        if _covers(periods, start, end):
            state = "covered"
        elif start < asked_from:
            state = "pending"
        else:
            due = _quarter_end(start) if account.cadence == "quarterly" else end
            late = (today - due).days > STATEMENT_GRACE_DAYS
            state = "missing" if late else "pending"
        months.append(CoverageMonth(f"{start:%Y-%m}", start, end, state))
    return months


def _periods_by_account(
    session: Session, account_ids: list[UUID]
) -> dict[UUID, list[tuple[dt.date, dt.date]]]:
    found: dict[UUID, list[tuple[dt.date, dt.date]]] = defaultdict(list)
    for row in session.execute(
        select(
            BankStatement.bank_account_id, BankStatement.period_from, BankStatement.period_to
        ).where(
            BankStatement.bank_account_id.in_(account_ids),
            BankStatement.state == "imported",
        )
    ):
        found[row.bank_account_id].append((row.period_from, row.period_to))
    return found


def statement_gaps(
    session: Session, account: BankAccount, start: dt.date, end: dt.date, today: dt.date
) -> list[MissingPeriod]:
    """The months of `start`..`end` that no imported statement of the account covers, one entry
    per run of consecutive months, for an export's readiness (accountant export design § 2). A
    month before the account was asked for, and one (a quarter, for a quarterly account) that
    has not ended by `today`, is not a gap: nobody has a statement of it to give. The grace of
    `missing` is not applied, since a period is exported after the fact."""
    periods = _periods_by_account(session, [account.id]).get(account.id, [])
    asked_from = _first_asked_month(account)
    runs: list[MissingPeriod] = []
    run: list[tuple[dt.date, dt.date]] = []
    month = start.replace(day=1)
    while month <= end:
        first, last = month_bounds(month.year, month.month)
        due = _quarter_end(first) if account.cadence == "quarterly" else last
        gap = first >= asked_from and due < today and not _covers(periods, first, last)
        if gap:
            run.append((first, last))
        if run and (not gap or month.replace(day=1) == end.replace(day=1)):
            runs.append(MissingPeriod(account.id, account.name, run[0][0], run[-1][1]))
            run = []
        month = add_months(month, 1)
    return runs


def account_coverage(
    session: Session, account: BankAccount, today: dt.date | None = None
) -> AccountCoverage:
    day = today if today is not None else today_local()
    periods = _periods_by_account(session, [account.id]).get(account.id, [])
    return AccountCoverage(STATEMENT_GRACE_DAYS, _months(account, periods, day))


def missing_periods(session: Session, today: dt.date | None = None) -> list[MissingPeriod]:
    """The missing statements of every active account, one per run of consecutive missing
    months, oldest first within an account."""
    day = today if today is not None else today_local()
    accounts = list(
        session.execute(
            select(BankAccount)
            .where(BankAccount.archived_at.is_(None))
            .order_by(BankAccount.name, BankAccount.id)
        ).scalars()
    )
    if not accounts:
        return []
    periods = _periods_by_account(session, [account.id for account in accounts])
    found: list[MissingPeriod] = []
    for account in accounts:
        run: list[CoverageMonth] = []
        for month in [*_months(account, periods.get(account.id, []), day), None]:
            if month is not None and month.state == "missing":
                run.append(month)
                continue
            if run:
                found.append(
                    MissingPeriod(account.id, account.name, run[0].period_from, run[-1].period_to)
                )
                run = []
    return found
