"""Acknowledge and snooze, per user (spec 2026-10-26 §8.2).

**Acknowledge** marks a reminder seen at the severity it had; **snooze** is the same with
a date. Both are Mastro's `covers` rule: a state covers a severity when it was recorded
at that rank or above, so «seen at warning» never silences «critical», and a snooze stops
covering on the calendar day it names, compared as dates in the installation's zone and
never as instants. Both belong to the person who made them: one user acknowledging a
reminder does not hide it from another, the reverse of Mastro.

What a person may do here is their own bookkeeping and nothing else, so neither operation
asks for a write role: a `readonly` member is reminded too and may say they have seen it.
The severity is the one the caller saw; it can only ever make *their own* state cover
more, which is why the server does not re-detect to check it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.errors import Conflict, ValidationFailed
from pigrocrm.core.reminders.catalogue import (
    REMINDER_TYPES,
    SEVERITY_RANK,
    Severity,
    type_of_key,
)
from pigrocrm.core.reminders.models import KEY_MAX_LENGTH, ReminderAck, ReminderSnooze
from pigrocrm.core.reminders.schemas import ReminderStateRead

ENTITY = "reminder"

SnoozeOption = Literal["tomorrow", "next_monday", "in_a_week", "in_a_month"]
# Mastro's set, and never a free date picker: «next Monday» and «in a month» are computed
# here once, so the web and an agent cannot compute them two ways.
SNOOZE_OPTIONS: tuple[SnoozeOption, ...] = ("tomorrow", "next_monday", "in_a_week", "in_a_month")


def snooze_until(option: SnoozeOption, today: date) -> date:
    """The calendar day a snooze lasts until, from the installation's today.

    `next_monday` is strictly after today, a Monday included: said on a Monday it means
    next week's. `in_a_month` is the same day number of the next month, clamped to that
    month's last day (the 31st of January is the end of February).
    """
    if option == "tomorrow":
        return today + timedelta(days=1)
    if option == "next_monday":
        return today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    if option == "in_a_week":
        return today + timedelta(days=7)
    year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    after = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return date(year, month, min(today.day, (after - timedelta(days=1)).day))


@dataclass(frozen=True)
class KeyState:
    """What one user has done about one key: the rank acknowledged, and the rank and day
    of a snooze. `None` for what they have not done."""

    ack_rank: int | None = None
    snooze_rank: int | None = None
    snooze_until: date | None = None

    def covers(self, severity: Severity, today: date) -> bool:
        """Whether this user has already dealt with a reminder at `severity` on `today`:
        acknowledged at that rank or above, or snoozed at that rank or above for a day
        that has not come yet."""
        rank = SEVERITY_RANK[severity]
        if self.ack_rank is not None and self.ack_rank >= rank:
            return True
        return (
            self.snooze_rank is not None
            and self.snooze_rank >= rank
            and self.snooze_until is not None
            and today < self.snooze_until
        )


def states_for(session: Session, user_ids: Iterable[UUID]) -> dict[tuple[UUID, str], KeyState]:
    """Every acknowledgement and snooze of these users, `(user, key)` to its state, in two
    queries: the pass reads them once per space and never one per reminder."""
    users = list(user_ids)
    if not users:
        return {}
    acks = {
        (row.user_id, row.key): row.severity_rank
        for row in session.scalars(select(ReminderAck).where(ReminderAck.user_id.in_(users)))
    }
    snoozes = {
        (row.user_id, row.key): (row.severity_rank, row.until)
        for row in session.scalars(select(ReminderSnooze).where(ReminderSnooze.user_id.in_(users)))
    }
    states: dict[tuple[UUID, str], KeyState] = {}
    for identity in acks.keys() | snoozes.keys():
        snooze = snoozes.get(identity)
        states[identity] = KeyState(
            ack_rank=acks.get(identity),
            snooze_rank=snooze[0] if snooze else None,
            snooze_until=snooze[1] if snooze else None,
        )
    return states


def check_key(key: str) -> str:
    """A key a person may acknowledge or snooze: `type:subject`, the type one the
    catalogue knows. The subject is not looked up: the reminder may have cleared since
    the person saw it, and then the end of the next pass drops the row."""
    kind, _, subject = key.partition(":")
    if kind not in REMINDER_TYPES or not subject or len(key) > KEY_MAX_LENGTH or " " in key:
        raise ValidationFailed(
            ENTITY,
            "key",
            "chiave di promemoria non valida",
            expected="tipo:identificativo, di un tipo che esiste",
        )
    return key


class ReminderStateService:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _user(actor: Actor) -> UUID:
        if actor.id is None:
            # A job has no inbox: acknowledge and snooze are what a person does.
            raise Conflict(ENTITY, "solo una persona può chiudere un promemoria")
        return actor.id

    def acknowledge(self, actor: Actor, key: str, severity: Severity) -> ReminderStateRead:
        user_id = self._user(actor)
        key = check_key(key)
        rank = SEVERITY_RANK[severity]
        statement = insert(ReminderAck).values(
            key=key, type=type_of_key(key), user_id=user_id, severity_rank=rank
        )
        self.session.execute(
            statement.on_conflict_do_update(
                constraint="uq_reminder_ack_key_user",
                # Never lowered: a stale tab acknowledging at warning must not uncover
                # the critical this person already acknowledged.
                set_={
                    "severity_rank": func.greatest(
                        ReminderAck.severity_rank, statement.excluded.severity_rank
                    ),
                    "updated_at": func.now(),
                },
            )
        )
        self.session.commit()
        return self._read(user_id, key)

    def snooze(
        self, actor: Actor, key: str, severity: Severity, option: SnoozeOption
    ) -> ReminderStateRead:
        user_id = self._user(actor)
        key = check_key(key)
        until = snooze_until(option, today_local())
        rank = SEVERITY_RANK[severity]
        statement = insert(ReminderSnooze).values(
            key=key, type=type_of_key(key), user_id=user_id, severity_rank=rank, until=until
        )
        self.session.execute(
            statement.on_conflict_do_update(
                constraint="uq_reminder_snooze_key_user",
                set_={
                    "severity_rank": func.greatest(
                        ReminderSnooze.severity_rank, statement.excluded.severity_rank
                    ),
                    "until": statement.excluded["until"],
                    "updated_at": func.now(),
                },
            )
        )
        self.session.commit()
        return self._read(user_id, key)

    def _read(self, user_id: UUID, key: str) -> ReminderStateRead:
        self.session.expire_all()
        state = states_for(self.session, [user_id]).get((user_id, key), KeyState())
        return ReminderStateRead(
            key=key,
            acknowledged_severity=_severity(state.ack_rank),
            snoozed_severity=_severity(state.snooze_rank),
            snoozed_until=state.snooze_until,
        )

    def mine(self, actor: Actor) -> list[ReminderStateRead]:
        """Everything this person has acknowledged or snoozed, for a client to join with
        the reminders it lists."""
        user_id = self._user(actor)
        return [
            ReminderStateRead(
                key=key,
                acknowledged_severity=_severity(state.ack_rank),
                snoozed_severity=_severity(state.snooze_rank),
                snoozed_until=state.snooze_until,
            )
            for (_, key), state in sorted(states_for(self.session, [user_id]).items())
        ]


def _severity(rank: int | None) -> Severity | None:
    if rank is None:
        return None
    return next(severity for severity, value in SEVERITY_RANK.items() if value == rank)
