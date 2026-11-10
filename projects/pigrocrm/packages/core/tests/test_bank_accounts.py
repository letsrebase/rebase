"""Bank account balances, statement coverage and missing statements (REB-1025, spec 2026-11-09,
due items and the cash schedule, §§ 4 and 5)."""

import datetime as dt
import itertools
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from openpyxl import Workbook
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.bank.accounts import BankAccountService
from pigrocrm.core.bank.balances import account_balances
from pigrocrm.core.bank.constants import STATEMENT_GRACE_DAYS, UNMATCHED_OLD_DAYS
from pigrocrm.core.bank.coverage import account_coverage, missing_periods
from pigrocrm.core.bank.models import BankAccount, BankMovement, BankStatement, StatementMovement
from pigrocrm.core.bank.schemas import BankAccountCreate, BankAccountUpdate
from pigrocrm.core.bank.statements import (
    STATEMENT_SIZE_MAX,
    BankStatementReadService,
    StatementImportService,
)
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.errors import (
    AgentForbidden,
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.repository import LegalEntityRepository

D = dt.date
ADMIN = Actor(id=None, type="system", role="admin")
COLLABORATOR = Actor(id=None, type="user", role="collaborator")
AGENT = Actor(id=None, type="mcp", role="admin")
FULL_AGENT = Actor(id=None, type="mcp", role="admin", full_access=True)
CAMT = Path(__file__).parent / "fixtures" / "camt053" / "statement_001_02.xml"
CAMT_IBAN = "IT60X0542811101000000123456"
TODAY = D(2026, 11, 10)
LONG_AGO = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
_IBANS = itertools.count(1)


class _Statements:
    """The two statement services behind one name, as the tests below use them together."""

    def __init__(self, session: Session) -> None:
        self._reads = BankStatementReadService(session)
        self._imports = StatementImportService(session)

    def __getattr__(self, name: str) -> Any:
        for service in (self._reads, self._imports):
            if hasattr(service, name):
                return getattr(service, name)
        raise AttributeError(name)


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


# -- the service: accounts -----------------------------------------------------------------


def _create(session: Session, **overrides: object) -> UUID:
    values: dict[str, object] = {
        "name": "Main",
        "bank": "Banco BPM",
        "iban": CAMT_IBAN,
        "opening_balance": "1000.00",
        "opening_balance_date": "2026-09-30",
    }
    values.update(overrides)
    return (
        BankAccountService(session)
        .create_bank_account(BankAccountCreate.model_validate(values), ADMIN)
        .id
    )


def test_an_account_is_created_with_its_iban_normalised_and_its_defaults(
    db_session: Session,
) -> None:
    read = BankAccountService(db_session).create_bank_account(
        BankAccountCreate(
            name="Main",
            bank="Banco BPM",
            iban="it60 x054 2811 1010 0000 0123 456",
            senders=[" A@B.it ", "a@b.it"],
        ),
        ADMIN,
    )
    assert read.iban == CAMT_IBAN
    assert (read.currency, read.arrival, read.cadence) == ("EUR", "upload", "monthly")
    assert read.senders == ["a@b.it"]
    assert (read.balance, read.balance_as_of, read.is_stale, read.unmatched_count) == (
        None,
        None,
        False,
        0,
    )
    assert read.legal_entity_name


def test_the_create_schema_refuses_a_bad_iban_and_half_an_opening_balance() -> None:
    base = {"name": "A", "bank": "B"}
    with pytest.raises(ValidationError):
        BankAccountCreate(**base, iban="IT60X0542811101000000123457")
    with pytest.raises(ValidationError):
        BankAccountCreate(**base, iban=CAMT_IBAN, opening_balance=Decimal("1.00"))
    with pytest.raises(ValidationError):
        BankAccountCreate(**base, iban=CAMT_IBAN, currency="EURO")
    with pytest.raises(ValidationError):
        BankAccountUpdate(opening_balance=Decimal("1.00"))


def test_an_iban_is_unique_and_the_refusal_leaves_the_session_usable(db_session: Session) -> None:
    _create(db_session)
    with pytest.raises(Conflict):
        _create(db_session, name="Again")
    assert len(BankAccountService(db_session).list_bank_accounts(ADMIN)) == 1


def test_an_account_needs_an_administrator_and_an_agent_needs_full_access(
    db_session: Session,
) -> None:
    service = BankAccountService(db_session)
    data = BankAccountCreate(name="A", bank="B", iban=CAMT_IBAN)
    with pytest.raises(PermissionDenied):
        service.create_bank_account(data, COLLABORATOR)
    with pytest.raises(PermissionDenied):
        service.list_bank_accounts(COLLABORATOR)
    with pytest.raises(AgentForbidden):
        service.create_bank_account(data, AGENT)
    with pytest.raises(AgentForbidden):
        service.list_bank_accounts(AGENT)
    assert service.create_bank_account(data, FULL_AGENT).iban == CAMT_IBAN
    assert [a.iban for a in service.list_bank_accounts(FULL_AGENT)] == [CAMT_IBAN]


def test_with_several_companies_the_account_names_whose_it_is(db_session: Session) -> None:
    first = LegalEntityRepository(db_session).default()
    assert first is not None
    other = LegalEntity(legal_name="Second Srl", country="IT")
    db_session.add(other)
    db_session.flush()
    with pytest.raises(ValidationFailed) as refused:
        _create(db_session)
    assert refused.value.details["field"] == "legal_entity_id"
    account = BankAccountService(db_session).create_bank_account(
        BankAccountCreate(name="A", bank="B", iban=CAMT_IBAN, legal_entity_id=other.id), ADMIN
    )
    assert account.legal_entity_id == other.id
    only = BankAccountService(db_session).list_bank_accounts(ADMIN, legal_entity_id=first.id)
    assert only == []


def test_an_update_changes_what_it_names_and_the_iban_freezes_with_the_first_statement(
    db_session: Session,
) -> None:
    account_id = _create(db_session)
    service = BankAccountService(db_session)
    renamed = service.update_bank_account(
        account_id, BankAccountUpdate(name="Operating", cadence="quarterly"), ADMIN
    )
    assert (renamed.name, renamed.cadence, renamed.iban) == ("Operating", "quarterly", CAMT_IBAN)
    moved = service.update_bank_account(
        account_id, BankAccountUpdate(iban="DE89370400440532013000"), ADMIN
    )
    assert moved.iban == "DE89370400440532013000"
    cleared = service.update_bank_account(
        account_id,
        BankAccountUpdate(opening_balance=None, opening_balance_date=None),
        ADMIN,
    )
    assert (cleared.opening_balance, cleared.balance) == (None, None)
    service.update_bank_account(account_id, BankAccountUpdate(iban=CAMT_IBAN), ADMIN)
    _Statements(db_session).import_file(account_id, CAMT.read_bytes(), ADMIN)
    for frozen in (
        BankAccountUpdate(iban="DE89370400440532013000"),
        BankAccountUpdate(currency="USD"),
    ):
        with pytest.raises(Conflict):
            service.update_bank_account(account_id, frozen, ADMIN)
    assert (
        service.update_bank_account(account_id, BankAccountUpdate(name="Still"), ADMIN).name
        == "Still"
    )


def test_an_archived_account_leaves_the_list_and_the_balances_and_keeps_its_rows(
    db_session: Session,
) -> None:
    account_id = _create(db_session)
    _Statements(db_session).import_file(account_id, CAMT.read_bytes(), ADMIN)
    service = BankAccountService(db_session)
    assert [a.id for a in service.list_bank_accounts(ADMIN)] == [account_id]
    archived = service.archive_bank_account(account_id, ADMIN)
    assert archived.archived_at is not None
    assert service.list_bank_accounts(ADMIN) == []
    assert [a.id for a in service.list_bank_accounts(ADMIN, include_archived=True)] == [account_id]
    assert account_balances(db_session) == {}
    assert db_session.scalar(select(func.count()).select_from(BankMovement)) == 4
    assert missing_periods(db_session, TODAY) == []
    with pytest.raises(Conflict):
        service.archive_bank_account(account_id, ADMIN)
    with pytest.raises(NotFound):
        service.archive_bank_account(uuid4(), ADMIN)


def test_the_account_read_carries_the_balance_the_server_computed(db_session: Session) -> None:
    account_id = _create(db_session)
    _Statements(db_session).import_file(account_id, CAMT.read_bytes(), ADMIN)
    read = BankAccountService(db_session).get_bank_account(account_id, ADMIN)
    # The statement states a closing balance of 2170.00 on 31 October.
    assert read.balance == Decimal("2170.00")
    assert read.balance_as_of == D(2026, 10, 31)
    assert read.unmatched_count == 4
    assert (read.stale_days, read.is_stale) == (10, False)


def test_the_coverage_read_lists_twelve_months(db_session: Session) -> None:
    account_id = _create(db_session)
    coverage = BankAccountService(db_session).get_coverage(account_id, ADMIN)
    assert coverage.grace_days == STATEMENT_GRACE_DAYS
    assert len(coverage.months) == 12
    assert coverage.months[-1].month == f"{TODAY:%Y-%m}"


# -- the service: statements and movements -------------------------------------------------


def _xlsx(*rows: list[object]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "movimentiConto"
    sheet.append(
        [
            "Data Contabile",
            "Data Valuta",
            "Importo",
            "Divisa",
            "Causale / Descrizione",
            "Stato ",
            "Canale",
        ]
    )
    for row in rows:
        sheet.append(list(row))
    out = BytesIO()
    workbook.save(out)
    return out.getvalue()


def test_a_camt_file_is_imported_and_read_back_with_its_check(db_session: Session) -> None:
    account_id = _create(db_session)
    result = _Statements(db_session).import_file(account_id, CAMT.read_bytes(), ADMIN)
    assert (result.added, result.duplicates) == (4, 0)
    statement = result.statement
    assert (statement.format, statement.state, statement.movement_count) == (
        "camt053",
        "imported",
        4,
    )
    assert (statement.period_from, statement.period_to) == (D(2026, 10, 1), D(2026, 10, 31))
    # 1000.00 + 1220.00 - 30.00 - 10.00 - 10.00 = 2170.00, the stated closing balance.
    assert statement.check is not None and statement.check.ok
    again = _Statements(db_session).import_file(account_id, CAMT.read_bytes(), ADMIN)
    assert (again.added, again.duplicates) == (0, 4)
    assert again.statement.movement_count == 4


def test_an_xlsx_needs_the_period_a_person_sets_and_is_told_by_its_content(
    db_session: Session,
) -> None:
    account_id = _create(db_session, iban="DE89370400440532013000")
    actions = _Statements(db_session)
    file = _xlsx(["09/11/2026", "09/11/2026", 300.5, "EUR", "BON.DA X", "Cont.", ""])
    with pytest.raises(ValidationFailed) as refused:
        actions.import_file(account_id, file, ADMIN)
    assert refused.value.details["field"] == "period"
    with pytest.raises(ValidationFailed):
        actions.import_file(account_id, file, ADMIN, period_from=D(2026, 11, 1))
    result = actions.import_file(
        account_id, file, ADMIN, period_from=D(2026, 11, 1), period_to=D(2026, 11, 30)
    )
    assert (result.statement.format, result.added) == ("banco_bpm_xlsx", 1)
    assert result.statement.check is None  # it states no balance


def test_a_file_in_no_known_format_or_over_the_cap_is_refused(db_session: Session) -> None:
    account_id = _create(db_session)
    actions = _Statements(db_session)
    with pytest.raises(ValidationFailed, match="formato non riconosciuto"):
        actions.import_file(account_id, b"just some text", ADMIN)
    with pytest.raises(ValidationFailed, match="supera"):
        actions.import_file(account_id, b"<" + b"x" * STATEMENT_SIZE_MAX, ADMIN)
    with pytest.raises(PermissionDenied):
        actions.import_file(account_id, CAMT.read_bytes(), COLLABORATOR)
    with pytest.raises(NotFound):
        actions.import_file(uuid4(), CAMT.read_bytes(), ADMIN)
    assert db_session.scalar(select(func.count()).select_from(BankStatement)) == 0


def test_an_archived_account_takes_no_import(db_session: Session) -> None:
    account_id = _create(db_session)
    BankAccountService(db_session).archive_bank_account(account_id, ADMIN)
    with pytest.raises(Conflict):
        _Statements(db_session).import_file(account_id, CAMT.read_bytes(), ADMIN)


def test_the_check_names_a_balance_that_does_not_add_up_and_a_gap_between_statements(
    db_session: Session,
) -> None:
    account = _account(db_session)
    _statement(db_session, account, D(2026, 8, 1), D(2026, 8, 31), closing="500.00")
    sums = _statement(db_session, account, D(2026, 9, 1), D(2026, 9, 30), closing="900.00")
    sums.opening_balance = Decimal("500.00")
    _movement(db_session, account, D(2026, 9, 3), "300.00")
    db_session.add(
        StatementMovement(
            statement_id=sums.id,
            movement_id=db_session.scalar(select(BankMovement.id)),
            legal_entity_id=account.legal_entity_id,
        )
    )
    jump = _statement(db_session, account, D(2026, 10, 1), D(2026, 10, 31), closing="910.00")
    jump.opening_balance = Decimal("910.00")
    db_session.flush()
    page = _Statements(db_session).list_statements(account.id, ADMIN)
    by_period = {s.period_from: s for s in page.items}
    assert by_period[D(2026, 8, 1)].check is None
    # 500.00 + 300.00 = 800.00 against a stated closing of 900.00.
    assert by_period[D(2026, 9, 1)].check is not None
    assert by_period[D(2026, 9, 1)].check.model_dump() == {
        "ok": False,
        "difference": Decimal("100.00"),
        "kind": "balance",
    }
    # The opening of October (910.00) is not September's closing (900.00).
    assert by_period[D(2026, 10, 1)].check is not None
    assert by_period[D(2026, 10, 1)].check.model_dump() == {
        "ok": False,
        "difference": Decimal("10.00"),
        "kind": "gap",
    }


def test_statements_come_newest_first_with_a_cursor(db_session: Session) -> None:
    account = _account(db_session)
    for month in range(1, 6):
        _statement(db_session, account, D(2026, month, 1), D(2026, month, 28))
    actions = _Statements(db_session)
    first = actions.list_statements(account.id, ADMIN, limit=2)
    assert [s.period_from.month for s in first.items] == [5, 4]
    assert first.total_count == 5 and first.next_cursor is not None
    second = actions.list_statements(account.id, ADMIN, limit=2, cursor=first.next_cursor)
    assert [s.period_from.month for s in second.items] == [3, 2]
    last = actions.list_statements(account.id, ADMIN, limit=2, cursor=second.next_cursor)
    assert [s.period_from.month for s in last.items] == [1] and last.next_cursor is None
    with pytest.raises(ValidationFailed):
        actions.list_statements(account.id, ADMIN, limit=0)


def test_movements_come_newest_first_flagged_when_they_wait_too_long(db_session: Session) -> None:
    account = _account(db_session)
    today = today_local()
    old = today - dt.timedelta(days=UNMATCHED_OLD_DAYS)
    recent = today - dt.timedelta(days=UNMATCHED_OLD_DAYS - 1)
    _movement(db_session, account, old, "-5.00")
    _movement(db_session, account, recent, "7.00")
    _movement(db_session, account, today - dt.timedelta(days=30), "9.00")
    db_session.execute(
        BankMovement.__table__.update()
        .where(BankMovement.booking_date == today - dt.timedelta(days=30))
        .values(state="ignored", ignored_reason="personal")
    )
    page = _Statements(db_session).list_movements(account.id, ADMIN)
    assert [m.booking_date for m in page.items] == [recent, old, today - dt.timedelta(days=30)]
    assert [(m.unmatched_days, m.is_overdue_unmatched) for m in page.items] == [
        (UNMATCHED_OLD_DAYS - 1, False),
        (UNMATCHED_OLD_DAYS, True),
        (None, False),
    ]
    assert (page.total_count, page.unmatched_count) == (3, 2)
    waiting = _Statements(db_session).list_movements(account.id, ADMIN, state="unmatched")
    assert (len(waiting.items), waiting.total_count, waiting.unmatched_count) == (2, 2, 2)


def test_movements_page_by_cursor_and_filter_by_statement(db_session: Session) -> None:
    account_id = _create(db_session)
    actions = _Statements(db_session)
    first = actions.import_file(account_id, CAMT.read_bytes(), ADMIN).statement
    page = actions.list_movements(account_id, ADMIN, limit=3)
    assert len(page.items) == 3 and page.next_cursor is not None
    rest = actions.list_movements(account_id, ADMIN, limit=3, cursor=page.next_cursor)
    assert len(rest.items) == 1 and rest.next_cursor is None
    seen = {m.id for m in page.items} | {m.id for m in rest.items}
    assert len(seen) == 4
    assert all(m.statement_ids == [first.id] for m in page.items + rest.items)
    other = actions.list_movements(account_id, ADMIN, statement_id=uuid4())
    assert other.items == [] and other.total_count == 0
    assert actions.list_movements(account_id, ADMIN, statement_id=first.id).total_count == 4


def test_a_movement_in_two_statements_is_one_row_with_both_statements(
    db_session: Session,
) -> None:
    account_id = _create(db_session)
    actions = _Statements(db_session)
    first = actions.import_file(account_id, CAMT.read_bytes(), ADMIN).statement
    second = actions.import_file(account_id, CAMT.read_bytes(), ADMIN).statement
    page = actions.list_movements(account_id, ADMIN)
    assert page.total_count == 4
    assert {tuple(sorted(m.statement_ids)) for m in page.items} == {
        tuple(sorted([first.id, second.id]))
    }


# -- the revert ----------------------------------------------------------------------------


def test_reverting_removes_the_statements_movements_and_the_balance_follows(
    db_session: Session,
) -> None:
    account_id = _create(db_session)
    actions = _Statements(db_session)
    statement = actions.import_file(account_id, CAMT.read_bytes(), ADMIN).statement
    reverted = actions.revert_statement(statement.id, ADMIN)
    assert (reverted.state, reverted.movement_count, reverted.check) == ("reverted", 0, None)
    assert db_session.scalar(select(func.count()).select_from(BankMovement)) == 0
    assert db_session.scalar(select(func.count()).select_from(StatementMovement)) == 0
    read = BankAccountService(db_session).get_bank_account(account_id, ADMIN)
    assert (read.balance, read.balance_as_of) == (Decimal("1000.00"), D(2026, 9, 30))
    assert [s.state for s in actions.list_statements(account_id, ADMIN).items] == ["reverted"]
    with pytest.raises(Conflict):
        actions.revert_statement(statement.id, ADMIN)
    assert actions.import_file(account_id, CAMT.read_bytes(), ADMIN).added == 4


def test_reverting_keeps_a_movement_another_statement_carries(db_session: Session) -> None:
    account_id = _create(db_session)
    actions = _Statements(db_session)
    first = actions.import_file(account_id, CAMT.read_bytes(), ADMIN).statement
    second = actions.import_file(account_id, CAMT.read_bytes(), ADMIN).statement
    reverted = actions.revert_statement(first.id, ADMIN)
    assert reverted.state == "reverted"
    assert db_session.scalar(select(func.count()).select_from(BankMovement)) == 4
    page = actions.list_movements(account_id, ADMIN)
    assert all(m.statement_ids == [second.id] for m in page.items)


def test_a_decided_movement_blocks_the_revert_and_nothing_changes(db_session: Session) -> None:
    account_id = _create(db_session)
    actions = _Statements(db_session)
    statement = actions.import_file(account_id, CAMT.read_bytes(), ADMIN).statement
    db_session.execute(
        BankMovement.__table__.update()
        .where(BankMovement.amount == Decimal("1220.00"))
        .values(state="matched")
    )
    with pytest.raises(Conflict):
        actions.revert_statement(statement.id, ADMIN)
    db_session.rollback()
    assert db_session.get(BankStatement, statement.id).state == "imported"  # type: ignore[union-attr]
    with pytest.raises(PermissionDenied):
        actions.revert_statement(statement.id, COLLABORATOR)
    with pytest.raises(NotFound):
        actions.revert_statement(uuid4(), ADMIN)
