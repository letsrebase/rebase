"""The CAMT.053 reader (REB-999): both message versions, the balances, the references, the
counterparty on the right side, and the import twice adding nothing."""

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.bank import readers
from pigrocrm.core.bank.camt053 import read_camt053
from pigrocrm.core.bank.models import BankAccount, BankMovement
from pigrocrm.core.bank.service import BankStatementService
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.legal_entities.repository import LegalEntityRepository

FIXTURES = Path(__file__).parent / "fixtures" / "camt053"
VERSIONS = ("statement_001_02.xml", "statement_001_08.xml")
D = dt.date


def _read(name: str):
    return read_camt053((FIXTURES / name).read_bytes())


@pytest.mark.parametrize("name", VERSIONS)
def test_the_statement_carries_its_account_period_and_stated_balances(name: str) -> None:
    parsed = _read(name)
    assert parsed.format == "camt053"
    assert parsed.account_iban == "IT60X0542811101000000123456"
    assert parsed.currency == "EUR"
    assert (parsed.period_from, parsed.period_to) == (D(2026, 10, 1), D(2026, 10, 31))
    assert (parsed.opening_balance, parsed.closing_balance) == (
        Decimal("1000.00"),
        Decimal("2170.00"),
    )
    assert parsed.balance_difference() == Decimal("0.00")


@pytest.mark.parametrize("name", VERSIONS)
def test_only_booked_entries_become_movements_with_signed_amounts(name: str) -> None:
    movements = _read(name).movements
    assert [m.amount for m in movements] == [
        Decimal("1220.00"),
        Decimal("-30.00"),
        Decimal("-10.00"),
        Decimal("-10.00"),
    ]
    assert movements[0].booking_date == D(2026, 10, 5)
    assert movements[0].value_date == D(2026, 10, 5)


@pytest.mark.parametrize("name", VERSIONS)
def test_the_counterparty_is_the_other_side_and_the_reference_the_banks(name: str) -> None:
    incoming, outgoing, plain, _ = _read(name).movements
    # Money in: the debtor; the servicer's reference wins over a `NOTPROVIDED` end-to-end id.
    assert (incoming.counterparty_name, incoming.counterparty_iban) == (
        "Acme Srl",
        "DE89370400440532013000",
    )
    assert incoming.bank_reference == "ASR-0001"
    assert incoming.description == "Fattura 12/2026"
    assert incoming.end_to_end_id is None  # `NOTPROVIDED`
    # Money out: the creditor; the end-to-end id is kept apart from the bank's reference.
    assert (outgoing.counterparty_name, outgoing.counterparty_iban) == (
        "Fornitore SpA",
        "FR1420041010050500013M02606",
    )
    assert outgoing.bank_reference is None
    assert outgoing.end_to_end_id == "E2E-77"
    assert (plain.counterparty_name, plain.bank_reference) == (None, None)


def test_both_versions_read_to_the_same_statement() -> None:
    assert _read(VERSIONS[0]) == _read(VERSIONS[1])


@pytest.mark.parametrize("name", VERSIONS)
def test_importing_the_same_file_twice_adds_its_movements_once(
    db_session: Session, name: str
) -> None:
    entity = LegalEntityRepository(db_session).default()
    assert entity is not None
    account = BankAccount(
        legal_entity_id=entity.id, name="Main", bank="B", iban="IT60X0542811101000000123456"
    )
    db_session.add(account)
    db_session.flush()
    service = BankStatementService(db_session)
    first = service.import_statement(account.id, _read(name))
    second = service.import_statement(account.id, _read(name))
    # Two identical same-day payments stay two; the reference-less ones key on their index.
    assert (first.added, first.duplicates) == (4, 0)
    assert (second.added, second.duplicates) == (0, 4)
    assert db_session.scalar(select(func.count()).select_from(BankMovement)) == 4


def test_the_reader_is_registered_for_the_seam() -> None:
    assert readers.reader_for("camt053") is read_camt053


def test_a_file_that_is_not_one_statement_is_refused() -> None:
    with pytest.raises(ValidationFailed):
        read_camt053(b"not xml")
    with pytest.raises(ValidationFailed):
        read_camt053(b'<Document xmlns="urn:x"><BkToCstmrStmt/></Document>')
    two = (FIXTURES / VERSIONS[0]).read_text().replace("</Stmt>", "</Stmt><Stmt><Id>2</Id></Stmt>")
    with pytest.raises(ValidationFailed):
        read_camt053(two.encode())


def test_an_entry_without_its_amount_or_date_is_refused() -> None:
    text = (FIXTURES / VERSIONS[0]).read_text()
    with pytest.raises(ValidationFailed):
        read_camt053(text.replace('<Amt Ccy="EUR">30.00</Amt>', "").encode())
    with pytest.raises(ValidationFailed):
        read_camt053(text.replace("<BookgDt><Dt>2026-10-07</Dt></BookgDt>", "").encode())


def test_an_external_entity_is_not_resolved() -> None:
    evil = (
        b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/hostname">]>'
        b'<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"><BkToCstmrStmt>'
        b"<Stmt><Acct><Id><IBAN>&x;</IBAN></Id></Acct></Stmt></BkToCstmrStmt></Document>"
    )
    assert read_camt053(evil).account_iban is None


def test_two_payments_reusing_one_end_to_end_id_are_kept_apart(db_session: Session) -> None:
    text = (FIXTURES / VERSIONS[0]).read_text()
    reused = text.replace("NOTPROVIDED", "REUSED")
    parsed = read_camt053(reused.encode())
    entity = LegalEntityRepository(db_session).default()
    assert entity is not None
    account = BankAccount(
        legal_entity_id=entity.id, name="Main", bank="B", iban="IT60X0542811101000000123456"
    )
    db_session.add(account)
    db_session.flush()
    result = BankStatementService(db_session).import_statement(account.id, parsed)
    assert (result.added, result.duplicates) == (4, 0)
    stored = db_session.scalars(select(BankMovement.end_to_end_id)).all()
    assert stored.count("REUSED") == 3


def test_a_malformed_date_inside_the_file_is_a_file_refusal_not_a_period_one() -> None:
    content = (FIXTURES / VERSIONS[0]).read_bytes().replace(b"2026-10-31T23", b"2026-13-45T23")
    with pytest.raises(ValidationFailed) as refused:
        read_camt053(content)
    assert refused.value.details["field"] == "file"
