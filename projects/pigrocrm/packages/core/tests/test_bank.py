"""Bank accounts, statements, movements and counterparty IBANs (REB-998, spec 2026-11-09, due
items and the cash schedule, § 2): the IBAN check, the dedup key, the import seam."""

import datetime as dt
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fakes.customer_fixtures import make_customer
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.bank import readers
from pigrocrm.core.bank.dedup import keys_for_file
from pigrocrm.core.bank.iban import is_valid_iban, normalise_iban
from pigrocrm.core.bank.models import BankAccount, BankMovement, StatementMovement
from pigrocrm.core.bank.readers import ParsedMovement, ParsedStatement
from pigrocrm.core.bank.service import BankStatementService, OrganizationIbanService
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.legal_entities.repository import LegalEntityRepository

D = dt.date
OURS = "IT60X0542811101000000123456"
THEIRS = "DE89370400440532013000"


def _account(session: Session, iban: str = OURS, **overrides: object) -> BankAccount:
    entity = LegalEntityRepository(session).default()
    assert entity is not None
    values: dict[str, object] = {
        "legal_entity_id": entity.id,
        "name": "Main",
        "bank": "Banco BPM",
        "iban": iban,
    }
    values.update(overrides)
    account = BankAccount(**values)
    session.add(account)
    session.flush()
    return account


def _movement(day: int, amount: str, description: str, reference: str | None = None):
    return ParsedMovement(
        booking_date=D(2026, 10, day),
        amount=Decimal(amount),
        currency="EUR",
        description=description,
        bank_reference=reference,
    )


def _file(*movements: ParsedMovement, **overrides: object) -> ParsedStatement:
    values: dict[str, object] = {
        "format": "test",
        "movements": list(movements),
        "period_from": D(2026, 10, 1),
        "period_to": D(2026, 10, 31),
    }
    values.update(overrides)
    return ParsedStatement(**values)  # type: ignore[arg-type]


def test_iban_check_accepts_real_ibans_and_refuses_a_typo() -> None:
    assert is_valid_iban(OURS)
    assert is_valid_iban("de89 3704 0044 0532 0130 00")
    assert normalise_iban(" de89 3704 ") == "DE893704"
    assert not is_valid_iban("IT60X0542811101000000123457")
    assert not is_valid_iban("not an iban")


def test_the_key_uses_the_reference_alone_when_there_is_one() -> None:
    a = list(keys_for_file([(D(2026, 10, 1), Decimal("5"), "x", "REF1")]))
    b = list(keys_for_file([(D(2026, 11, 9), Decimal("9"), "other", " REF1 ")]))
    assert a == b


def test_identical_same_day_payments_get_distinct_keys_but_the_same_file_twice_agrees() -> None:
    rows = [(D(2026, 10, 1), Decimal("-10.00"), "Coffee  Bar", None)] * 2
    first = list(keys_for_file(rows))
    assert first[0] != first[1]
    assert list(keys_for_file(rows)) == first
    # A re-wrapped, re-cased description is the same movement.
    again = list(keys_for_file([(D(2026, 10, 1), Decimal("-10.00"), "coffee bar", None)]))
    assert again == [first[0]]


def test_an_import_stores_movements_and_links_them(db_session: Session) -> None:
    account = _account(db_session)
    result = BankStatementService(db_session).import_statement(
        account.id,
        _file(_movement(1, "-10.00", "a"), _movement(2, "100.00", "b", "R2")),
    )
    assert (result.added, result.duplicates) == (2, 0)
    assert result.statement.state == "imported"
    assert db_session.scalar(select(func.count()).select_from(StatementMovement)) == 2


def test_two_identical_same_day_payments_are_both_kept(db_session: Session) -> None:
    account = _account(db_session)
    result = BankStatementService(db_session).import_statement(
        account.id, _file(_movement(1, "-10.00", "bar"), _movement(1, "-10.00", "bar"))
    )
    assert result.added == 2


def test_the_same_movement_in_two_statements_is_kept_once(db_session: Session) -> None:
    account = _account(db_session)
    service = BankStatementService(db_session)
    first = service.import_statement(
        account.id, _file(_movement(1, "-10.00", "a"), _movement(2, "5.00", "b", "R"))
    )
    second = service.import_statement(
        account.id,
        _file(
            _movement(2, "5.00", "b", "R"),
            _movement(1, "-10.00", "a"),
            _movement(3, "7.00", "c"),
            period_from=D(2026, 10, 2),
            period_to=D(2026, 11, 2),
        ),
    )
    assert (second.added, second.duplicates) == (1, 2)
    assert db_session.scalar(select(func.count()).select_from(BankMovement)) == 3
    # Both statements carry the shared movements.
    assert db_session.scalar(select(func.count()).select_from(StatementMovement)) == 5
    assert first.statement.id != second.statement.id


def test_the_same_key_may_exist_on_two_accounts(db_session: Session) -> None:
    one = _account(db_session)
    two = _account(db_session, iban=THEIRS)
    service = BankStatementService(db_session)
    movements = (_movement(1, "-10.00", "a", "R"),)
    assert service.import_statement(one.id, _file(*movements)).added == 1
    assert service.import_statement(two.id, _file(*movements)).added == 1


def test_a_period_is_never_guessed(db_session: Session) -> None:
    account = _account(db_session)
    service = BankStatementService(db_session)
    bare = _file(_movement(1, "1.00", "a"), period_from=None, period_to=None)
    with pytest.raises(ValidationFailed):
        service.import_statement(account.id, bare)
    ok = service.import_statement(
        account.id, bare, period_from=D(2026, 10, 1), period_to=D(2026, 10, 31)
    )
    assert ok.statement.period_to == D(2026, 10, 31)


def test_an_import_refuses_what_does_not_fit_the_account(db_session: Session) -> None:
    account = _account(db_session)
    service = BankStatementService(db_session)
    with pytest.raises(ValidationFailed):
        service.import_statement(account.id, _file(account_iban=THEIRS))
    with pytest.raises(ValidationFailed):
        service.import_statement(account.id, _file(currency="USD"))
    usd = ParsedMovement(booking_date=D(2026, 10, 1), amount=Decimal("1"), currency="USD")
    with pytest.raises(ValidationFailed):
        service.import_statement(account.id, _file(usd))
    zero = _movement(1, "0.00", "z")
    with pytest.raises(ValidationFailed):
        service.import_statement(account.id, _file(zero))
    bad_iban = ParsedMovement(
        booking_date=D(2026, 10, 1), amount=Decimal("1"), currency="EUR", counterparty_iban="IT00"
    )
    with pytest.raises(ValidationFailed):
        service.import_statement(account.id, _file(bad_iban))
    with pytest.raises(NotFound):
        service.import_statement(uuid4(), _file())
    account.archived_at = dt.datetime.now(dt.UTC)
    with pytest.raises(Conflict):
        service.import_statement(account.id, _file())


def test_a_counterparty_iban_is_stored_normalised(db_session: Session) -> None:
    account = _account(db_session)
    movement = ParsedMovement(
        booking_date=D(2026, 10, 1),
        amount=Decimal("-3"),
        currency="EUR",
        counterparty_iban="de89 3704 0044 0532 0130 00",
    )
    BankStatementService(db_session).import_statement(account.id, _file(movement))
    stored = db_session.scalars(select(BankMovement.counterparty_iban)).one()
    assert stored == THEIRS


def test_the_schema_holds_the_invariants_the_service_relies_on(db_session: Session) -> None:
    account = _account(db_session)
    BankStatementService(db_session).import_statement(account.id, _file(_movement(1, "1", "a")))
    base = {
        "bank_account_id": account.id,
        "legal_entity_id": account.legal_entity_id,
        "booking_date": D(2026, 10, 1),
        "amount": Decimal("1"),
        "currency": "EUR",
    }
    for bad in (
        {"dedup_key": "k1", "state": "ignored"},  # ignored without a reason
        {"dedup_key": "k2", "state": "matched", "ignored_reason": "r"},
        {"dedup_key": "k3", "legal_entity_id": uuid4()},  # not the account's entity
    ):
        with pytest.raises(IntegrityError), db_session.begin_nested():
            db_session.add(BankMovement(**{**base, **bad}))
            db_session.flush()
    with pytest.raises(IntegrityError), db_session.begin_nested():
        _account(db_session)  # the same IBAN twice


def test_registered_readers_are_found_by_format() -> None:
    def reader(content: bytes) -> ParsedStatement:
        return ParsedStatement(format="seam-test")

    readers.register_reader("seam-test", reader)
    try:
        assert readers.reader_for("seam-test") is reader
        assert "seam-test" in readers.registered_formats()
        with pytest.raises(ValueError):
            readers.register_reader("seam-test", reader)
    finally:
        readers._READERS.pop("seam-test")


def test_counterparty_ibans_are_a_list_with_proposals_waiting(db_session: Session) -> None:
    organization = make_customer(db_session)
    service = OrganizationIbanService(db_session)
    proposed = service.add(organization.id, "de89 3704 0044 0532 0130 00", source="movement")
    assert (proposed.iban, proposed.state) == (THEIRS, "proposed")
    assert service.add(organization.id, THEIRS, source="supplier_xml").id == proposed.id
    assert service.accept(proposed.id).state == "accepted"
    other = service.add(organization.id, OURS, source="supplier_xml", holder_name="ACME")
    assert other.state == "accepted"
    assert [row.iban for row in service.list_for(organization.id)] == [THEIRS, OURS]
    with pytest.raises(ValidationFailed):
        service.add(organization.id, "IT60X0542811101000000123457", source="manual")
    with pytest.raises(NotFound):
        service.add(UUID(int=1), OURS, source="manual")
