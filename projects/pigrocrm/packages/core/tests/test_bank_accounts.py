"""Bank account balances, statement coverage and missing statements (REB-1025, spec 2026-11-09,
due items and the cash schedule, §§ 4 and 5)."""

import datetime as dt
import itertools
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from pigrocrm.core.bank.balances import account_balances
from pigrocrm.core.bank.constants import STATEMENT_GRACE_DAYS
from pigrocrm.core.bank.coverage import account_coverage, missing_periods
from pigrocrm.core.bank.models import BankAccount, BankMovement, BankStatement
from pigrocrm.core.legal_entities.repository import LegalEntityRepository

D = dt.date
TODAY = D(2026, 11, 10)
LONG_AGO = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
_IBANS = itertools.count(1)


def _account(session: Session, **overrides: object) -> BankAccount:
    entity = LegalEntityRepository(session).default()
    assert entity is not None
    values: dict[str, object] = {
        "legal_entity_id": entity.id,
        "name": "Main",
        "bank": "Banco BPM",
        "iban": f"IT60X05428111{next(_IBANS):013d}",
        "created_at": LONG_AGO,
    }
    values.update(overrides)
    account = BankAccount(**values)
    session.add(account)
    session.flush()
    return account


def _statement(
    session: Session,
    account: BankAccount,
    start: dt.date,
    end: dt.date,
    *,
    closing: str | None = None,
    state: str = "imported",
) -> BankStatement:
    statement = BankStatement(
        bank_account_id=account.id,
        legal_entity_id=account.legal_entity_id,
        period_from=start,
        period_to=end,
        closing_balance=Decimal(closing) if closing is not None else None,
        format="test",
        state=state,
        reverted_at=dt.datetime(2026, 11, 1, tzinfo=dt.UTC) if state == "reverted" else None,
    )
    session.add(statement)
    session.flush()
    return statement


def _movement(session: Session, account: BankAccount, day: dt.date, amount: str) -> None:
    session.add(
        BankMovement(
            bank_account_id=account.id,
            legal_entity_id=account.legal_entity_id,
            booking_date=day,
            amount=Decimal(amount),
            currency="EUR",
            dedup_key=uuid4().hex,
        )
    )
    session.flush()


def test_an_account_with_no_anchor_has_no_balance(db_session: Session) -> None:
    account = _account(db_session)
    _movement(db_session, account, D(2026, 11, 2), "10.00")
    assert account_balances(db_session, today=TODAY)[account.id].amount is None
    balance = account_balances(db_session, today=TODAY)[account.id]
    assert (balance.as_of, balance.stale_days, balance.is_stale) == (None, None, False)


def test_the_opening_balance_anchors_and_movements_after_its_date_add_up(
    db_session: Session,
) -> None:
    account = _account(
        db_session, opening_balance=Decimal("1000.00"), opening_balance_date=D(2026, 10, 31)
    )
    _movement(db_session, account, D(2026, 10, 31), "-999.00")  # in the opening figure
    _movement(db_session, account, D(2026, 11, 3), "-40.50")
    _movement(db_session, account, D(2026, 11, 8), "200.00")
    balance = account_balances(db_session, today=TODAY)[account.id]
    assert balance.amount == Decimal("1159.50")
    assert balance.as_of == D(2026, 11, 8)
    assert (balance.stale_days, balance.is_stale) == (2, False)


def test_the_latest_closing_balance_replaces_the_opening_one(db_session: Session) -> None:
    account = _account(
        db_session, opening_balance=Decimal("1.00"), opening_balance_date=D(2026, 1, 1)
    )
    _statement(db_session, account, D(2026, 9, 1), D(2026, 9, 30), closing="500.00")
    _statement(db_session, account, D(2026, 10, 1), D(2026, 10, 31), closing="800.00")
    _statement(
        db_session, account, D(2026, 11, 1), D(2026, 11, 30), closing="9.00", state="reverted"
    )
    _statement(db_session, account, D(2026, 8, 1), D(2026, 8, 31))  # states no balance
    _movement(db_session, account, D(2026, 9, 15), "300.00")  # before the anchor: counted already
    _movement(db_session, account, D(2026, 11, 4), "-50.00")
    balance = account_balances(db_session, today=TODAY)[account.id]
    assert balance.amount == Decimal("750.00")
    assert balance.as_of == D(2026, 11, 4)


def test_the_anchor_date_is_the_as_of_when_nothing_was_booked_after_it(
    db_session: Session,
) -> None:
    account = _account(db_session)
    _statement(db_session, account, D(2026, 10, 1), D(2026, 10, 31), closing="800.00")
    balance = account_balances(db_session, today=TODAY)[account.id]
    assert (balance.amount, balance.as_of, balance.stale_days) == (
        Decimal("800.00"),
        D(2026, 10, 31),
        10,
    )


def test_a_balance_goes_stale_after_the_cadence_period_and_the_grace(db_session: Session) -> None:
    monthly = _account(
        db_session, opening_balance=Decimal("1.00"), opening_balance_date=D(2026, 10, 3)
    )
    quarterly = _account(
        db_session,
        cadence="quarterly",
        opening_balance=Decimal("1.00"),
        opening_balance_date=D(2026, 10, 3),
    )
    edge = D(2026, 10, 3) + dt.timedelta(days=31 + STATEMENT_GRACE_DAYS)
    on_edge = account_balances(db_session, today=edge)
    past_edge = account_balances(db_session, today=edge + dt.timedelta(days=1))
    assert not on_edge[monthly.id].is_stale and past_edge[monthly.id].is_stale
    assert not past_edge[quarterly.id].is_stale
    assert account_balances(db_session, today=D(2027, 1, 20))[quarterly.id].is_stale


def test_an_archived_account_is_left_out_unless_asked_for(db_session: Session) -> None:
    kept = _account(db_session, opening_balance=Decimal("5.00"), opening_balance_date=TODAY)
    gone = _account(
        db_session,
        opening_balance=Decimal("7.00"),
        opening_balance_date=TODAY,
        archived_at=dt.datetime(2026, 11, 5, tzinfo=dt.UTC),
    )
    assert set(account_balances(db_session, today=TODAY)) == {kept.id}
    named = account_balances(db_session, account_ids=[gone.id], today=TODAY)
    assert named[gone.id].amount == Decimal("7.00")
    assert account_balances(db_session, account_ids=[], today=TODAY) == {}


def _states(session: Session, account: BankAccount) -> dict[str, str]:
    coverage = account_coverage(session, account, TODAY)
    assert coverage.grace_days == STATEMENT_GRACE_DAYS
    return {month.month: month.state for month in coverage.months}


def test_coverage_is_twelve_months_ending_with_the_current_one(db_session: Session) -> None:
    account = _account(db_session)
    coverage = account_coverage(db_session, account, TODAY)
    assert [m.month for m in coverage.months][0] == "2025-12"
    assert [m.month for m in coverage.months][-1] == "2026-11"
    assert (coverage.months[-1].period_from, coverage.months[-1].period_to) == (
        D(2026, 11, 1),
        D(2026, 11, 30),
    )


def test_a_month_is_covered_by_the_union_of_statements_missing_after_the_grace(
    db_session: Session,
) -> None:
    account = _account(db_session)
    _statement(db_session, account, D(2026, 8, 1), D(2026, 8, 15))
    _statement(db_session, account, D(2026, 8, 16), D(2026, 9, 30))
    _statement(db_session, account, D(2026, 10, 1), D(2026, 10, 31), state="reverted")
    states = _states(db_session, account)
    assert states["2026-08"] == "covered" and states["2026-09"] == "covered"
    # October ended on the 31st: on 10 November that is past the 7 days of grace, but its
    # only statement was reverted.
    assert states["2026-10"] == "missing"
    assert states["2026-11"] == "pending"
    assert states["2026-07"] == "missing"


def test_a_month_inside_the_grace_is_pending(db_session: Session) -> None:
    account = _account(db_session)
    coverage = account_coverage(db_session, account, D(2026, 11, 7))
    assert {m.month: m.state for m in coverage.months}["2026-10"] == "pending"


def test_a_quarterly_account_misses_a_month_only_when_its_quarter_is_late(
    db_session: Session,
) -> None:
    account = _account(db_session, cadence="quarterly")
    states = _states(db_session, account)
    # Q3 ended on 30 September: late. Q4 has not ended.
    assert states["2026-09"] == "missing" and states["2026-07"] == "missing"
    assert states["2026-10"] == "pending" and states["2026-11"] == "pending"


def test_months_before_the_account_was_here_are_pending(db_session: Session) -> None:
    account = _account(
        db_session,
        created_at=dt.datetime(2026, 9, 12, tzinfo=dt.UTC),
        opening_balance=Decimal("1.00"),
        opening_balance_date=D(2026, 10, 1),
    )
    _statement(db_session, account, D(2026, 7, 1), D(2026, 7, 31))
    states = _states(db_session, account)
    assert states["2026-07"] == "covered"  # a statement of the past still counts
    assert states["2026-08"] == "pending" and states["2026-09"] == "pending"
    assert states["2026-10"] == "missing"


def test_missing_periods_join_consecutive_months_and_skip_archived_accounts(
    db_session: Session,
) -> None:
    account = _account(db_session, name="Main")
    _account(
        db_session,
        name="Old",
        archived_at=dt.datetime(2026, 11, 5, tzinfo=dt.UTC),
    )
    _statement(db_session, account, D(2025, 12, 1), D(2026, 7, 31))
    _statement(db_session, account, D(2026, 9, 1), D(2026, 9, 30))
    found = missing_periods(db_session, TODAY)
    assert [(p.account_id, p.account_name, p.period_from, p.period_to) for p in found] == [
        (account.id, "Main", D(2026, 8, 1), D(2026, 8, 31)),
        (account.id, "Main", D(2026, 10, 1), D(2026, 10, 31)),
    ]
