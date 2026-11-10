"""What each bank account holds today, computed once, on the server (spec 2026-11-09, § 4).

The anchor is the latest `imported` statement that states a closing balance (as of its
`period_to`), else the account's opening balance (as of `opening_balance_date`). The balance is
the anchor plus every movement booked after the anchor date, and `as_of` is the later of the
anchor date and the last movement's booking date. An account with no anchor has no balance: its
movements alone say nothing about what it holds. A movement a reverted statement carried is
gone from the table, so every row is live.

`account_ids=None` reads the active accounts: an archived account is hidden from the schedule's
balances. A caller that names an account reads it whether or not it is archived.
"""

import datetime as dt
from collections.abc import Collection
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, Uuid, case, column, func, select, values
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from pigrocrm.core.bank.constants import (
    MONTHLY_PERIOD_DAYS,
    QUARTERLY_PERIOD_DAYS,
    STATEMENT_GRACE_DAYS,
)
from pigrocrm.core.bank.models import BankAccount, BankMovement, BankStatement
from pigrocrm.core.db.clock import today_local

_ZERO = Decimal("0.00")
_PERIOD_DAYS = {"monthly": MONTHLY_PERIOD_DAYS, "quarterly": QUARTERLY_PERIOD_DAYS}


@dataclass(frozen=True)
class AccountBalance:
    amount: Decimal | None
    as_of: dt.date | None
    stale_days: int | None
    is_stale: bool


NO_BALANCE = AccountBalance(amount=None, as_of=None, stale_days=None, is_stale=False)


def stale_after_days(cadence: str) -> int:
    """How many days without news make a balance stale: the cadence's period and the grace."""
    return _PERIOD_DAYS[cadence] + STATEMENT_GRACE_DAYS


def account_balances(
    session: Session,
    *,
    account_ids: Collection[UUID] | None = None,
    today: dt.date | None = None,
) -> dict[UUID, AccountBalance]:
    """The balance of each account, keyed by id (an account with no anchor is present with
    `amount` and `as_of` `None`). Flushes nothing; reads the caller's session."""
    day = today if today is not None else today_local()
    query = select(
        BankAccount.id,
        BankAccount.cadence,
        BankAccount.opening_balance,
        BankAccount.opening_balance_date,
    )
    if account_ids is None:
        query = query.where(BankAccount.archived_at.is_(None))
    else:
        if not account_ids:
            return {}
        query = query.where(BankAccount.id.in_(list(account_ids)))
    accounts = session.execute(query).all()
    if not accounts:
        return {}
    ids = [row.id for row in accounts]

    # The latest statement that states a closing balance, one per account.
    statements = session.execute(
        select(
            BankStatement.bank_account_id, BankStatement.period_to, BankStatement.closing_balance
        )
        .where(
            BankStatement.bank_account_id.in_(ids),
            BankStatement.state == "imported",
            BankStatement.closing_balance.is_not(None),
        )
        .order_by(
            BankStatement.bank_account_id,
            BankStatement.period_to.desc(),
            BankStatement.created_at.desc(),
            BankStatement.id.desc(),
        )
        .ext(distinct_on(BankStatement.bank_account_id))
    ).all()
    anchors: dict[UUID, tuple[Decimal, dt.date]] = {
        row.bank_account_id: (row.closing_balance, row.period_to) for row in statements
    }
    for row in accounts:
        if row.id not in anchors and row.opening_balance is not None:
            assert row.opening_balance_date is not None  # ck_bank_accounts_opening_pair
            anchors[row.id] = (row.opening_balance, row.opening_balance_date)

    moved: dict[UUID, tuple[Decimal, dt.date | None]] = {}
    if anchors:
        anchor_dates = values(
            column("account_id", Uuid), column("anchor_date", Date), name="anchor_dates"
        ).data([(account_id, anchor[1]) for account_id, anchor in anchors.items()])
        for account_id, total_moved, last_moved in session.execute(
            select(
                BankMovement.bank_account_id,
                func.coalesce(
                    func.sum(
                        case(
                            (
                                BankMovement.booking_date > anchor_dates.c.anchor_date,
                                BankMovement.amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.max(BankMovement.booking_date),
            )
            .join(anchor_dates, anchor_dates.c.account_id == BankMovement.bank_account_id)
            .group_by(BankMovement.bank_account_id)
        ).all():
            moved[account_id] = (Decimal(total_moved).quantize(_ZERO), last_moved)

    result: dict[UUID, AccountBalance] = {}
    for row in accounts:
        anchor = anchors.get(row.id)
        if anchor is None:
            result[row.id] = NO_BALANCE
            continue
        amount, anchor_date = anchor
        total, last_booking = moved.get(row.id, (_ZERO, None))
        as_of = max(anchor_date, last_booking or anchor_date)
        stale_days = (day - as_of).days
        result[row.id] = AccountBalance(
            amount=amount + total,
            as_of=as_of,
            stale_days=stale_days,
            is_stale=stale_days > stale_after_days(row.cadence),
        )
    return result
