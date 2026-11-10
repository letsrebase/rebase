"""The read of a movement: its figures, the statements that carry it and its live match (spec
2026-11-09, § 2 and § 3). One place, so the list, the one-movement read and the answer of a
confirmation all say the same thing."""

import datetime as dt
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.bank.constants import UNMATCHED_OLD_DAYS
from pigrocrm.core.bank.match_reads import live_matches
from pigrocrm.core.bank.match_schemas import MovementMatchRead
from pigrocrm.core.bank.models import BankMovement, StatementMovement
from pigrocrm.core.bank.schemas import MovementRead
from pigrocrm.core.db.clock import today_local


def movement_reads(session: Session, movements: Sequence[BankMovement]) -> list[MovementRead]:
    """Each movement with its statements and its proposal or confirmed match, in the order
    given, in three reads however many movements."""
    if not movements:
        return []
    today = today_local()
    by_movement: dict[UUID, list[UUID]] = {m.id: [] for m in movements}
    for movement_id, statement_id in session.execute(
        select(StatementMovement.movement_id, StatementMovement.statement_id)
        .where(StatementMovement.movement_id.in_(list(by_movement)))
        .order_by(StatementMovement.statement_id)
    ):
        by_movement[movement_id].append(statement_id)
    matches = live_matches(session, list(by_movement))
    return [movement_read(m, by_movement[m.id], today, matches.get(m.id)) for m in movements]


def movement_read(
    movement: BankMovement,
    statement_ids: list[UUID],
    today: dt.date,
    match: MovementMatchRead | None,
) -> MovementRead:
    unmatched_days = (
        max((today - movement.booking_date).days, 0) if movement.state == "unmatched" else None
    )
    return MovementRead.model_validate(
        {
            "id": movement.id,
            "bank_account_id": movement.bank_account_id,
            "booking_date": movement.booking_date,
            "value_date": movement.value_date,
            "amount": movement.amount,
            "currency": movement.currency,
            "counterparty_name": movement.counterparty_name,
            "counterparty_iban": movement.counterparty_iban,
            "description": movement.description,
            "bank_reference": movement.bank_reference,
            "end_to_end_id": movement.end_to_end_id,
            "state": movement.state,
            "ignored_reason": movement.ignored_reason,
            "unmatched_days": unmatched_days,
            "is_overdue_unmatched": unmatched_days is not None
            and unmatched_days >= UNMATCHED_OLD_DAYS,
            "statement_ids": statement_ids,
            "match": match,
        }
    )
