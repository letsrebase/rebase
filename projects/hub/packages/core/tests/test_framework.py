"""The framework agreement's twelve months, and the letter numbers (REB-387)."""

import threading
from collections.abc import Iterator
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from rebase_core.db import session_factory
from rebase_core.framework import (
    document_read,
    is_active,
    last_notice_day,
    next_letter_number,
    next_renewal,
    signed_on,
)
from rebase_core.models import ContractDocument


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    hub_session.execute(text("DELETE FROM contract_letter_counters"))
    hub_session.commit()


@pytest.mark.parametrize(
    ("signed", "today", "renewal"),
    [
        (date(2026, 10, 1), date(2026, 12, 1), date(2027, 10, 1)),
        (date(2026, 10, 1), date(2027, 9, 30), date(2027, 10, 1)),
        # On the anniversary itself the new twelve months have started.
        (date(2026, 10, 1), date(2027, 10, 1), date(2028, 10, 1)),
        (date(2026, 10, 1), date(2026, 10, 1), date(2027, 10, 1)),
        # 29 February has an anniversary in common years too: the day before March.
        (date(2028, 2, 29), date(2029, 1, 10), date(2029, 2, 28)),
    ],
)
def test_the_next_renewal_is_the_first_anniversary_after_today(
    signed: date, today: date, renewal: date
) -> None:
    assert next_renewal(signed, today) == renewal


def test_the_last_day_for_a_notice_is_thirty_days_before_the_renewal() -> None:
    assert last_notice_day(date(2027, 10, 1)) == date(2027, 9, 1)


def _quadro(**columns: object) -> ContractDocument:
    return ContractDocument(kind="quadro", **columns)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("document", "active"),
    [
        (_quadro(stato="firmato", notice_at=None), True),
        (_quadro(stato="firmato", notice_at=datetime(2027, 1, 1, tzinfo=UTC)), False),
        (_quadro(stato="disdetto", notice_at=datetime(2027, 1, 1, tzinfo=UTC)), False),
        (_quadro(stato="generato", notice_at=None), False),
        (_quadro(stato="inviato", notice_at=None), False),
        (ContractDocument(kind="lettera", stato="firmato", notice_at=None), False),
    ],
)
def test_a_framework_is_active_while_signed_and_without_notice(
    document: ContractDocument, active: bool
) -> None:
    assert is_active(document) is active


def test_a_signature_is_dated_in_rome() -> None:
    """Review Focus 3: 23:30 UTC on 30 September is 1 October where rebase signs."""
    late = _quadro(signed_at=datetime(2026, 9, 30, 23, 30, tzinfo=UTC))
    assert signed_on(late) == date(2026, 10, 1)
    assert signed_on(_quadro(signed_at=None)) is None


def test_the_read_model_computes_the_dates_only_for_an_active_framework() -> None:
    common = {
        "id": UUID("01a00000-0000-7000-8000-00000000000a"),
        "freelancer_id": UUID("01a00000-0000-7000-8000-00000000000b"),
        "created_by": UUID("01a00000-0000-7000-8000-00000000000c"),
        "created_at": datetime(2026, 9, 23, tzinfo=UTC),
        "match_id": None,
        "numero": None,
        "testo_bozza": False,
        "sent_at": None,
        "signed_pdf": None,
    }
    signed = _quadro(
        **common,
        text_version="0.0",
        stato="firmato",
        signed_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
        notice_at=None,
    )
    read = document_read(signed, date(2026, 12, 1), current_version="0.1")
    assert (read.attivo, read.rinnovo, read.ultimo_giorno_disdetta) == (
        True,
        date(2027, 10, 1),
        date(2027, 9, 1),
    )
    assert read.nuova_versione is True and read.ha_pdf_firmato is False
    generated = _quadro(
        **common, text_version="0.1", stato="generato", signed_at=None, notice_at=None
    )
    read = document_read(generated, date(2026, 12, 1), current_version="0.1")
    assert (read.attivo, read.rinnovo, read.ultimo_giorno_disdetta, read.nuova_versione) == (
        False,
        None,
        None,
        False,
    )


def test_letters_are_numbered_per_year_and_a_rollback_leaves_no_gap(clean: Session) -> None:
    assert next_letter_number(clean, 2030) == "2030-001"
    assert next_letter_number(clean, 2030) == "2030-002"
    clean.commit()
    assert next_letter_number(clean, 2030) == "2030-003"
    clean.rollback()
    assert next_letter_number(clean, 2030) == "2030-003"
    assert next_letter_number(clean, 2031) == "2031-001"
    clean.commit()


def test_two_letters_taken_at_once_get_two_numbers(hub_engine: Engine, clean: Session) -> None:
    """Review Focus 1: the second transaction waits on the first one's row and then takes
    the next number, instead of reading the same one or failing on the key."""
    factory = session_factory(hub_engine)
    first, second = factory(), factory()
    taken: list[str] = []

    def take() -> None:
        taken.append(next_letter_number(second, 2032))
        second.commit()

    try:
        assert next_letter_number(first, 2032) == "2032-001"
        worker = threading.Thread(target=take)
        worker.start()
        worker.join(timeout=0.5)
        assert worker.is_alive(), "the second number was taken while the first was still open"
        first.commit()
        worker.join(timeout=5)
        assert taken == ["2032-002"]
    finally:
        first.close()
        second.close()
