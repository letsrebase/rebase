"""The reminders a person has, as they read them (spec 2026-10-26 §4, §8, REB-733).

A live evaluation filtered to the person asking, joined with what they did about each
(`state.py`) and when the pass last put it in their list (`ledger.py`). Nothing is read
from a stored reminder, so the list is right the moment a cause is fixed and right before
any pass has run; what the pass adds is `delivered_at` and the other channels.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.errors import Conflict
from pigrocrm.core.reminders.catalogue import REMINDER_TYPES, SEVERITY_RANK
from pigrocrm.core.reminders.engine import IN_APP, ReminderEngine
from pigrocrm.core.reminders.models import ReminderDelivery
from pigrocrm.core.reminders.schemas import ReminderRead
from pigrocrm.core.reminders.state import states_for

ENTITY = "reminder"


class ReminderService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def list(
        self, actor: Actor, *, today: date | None = None, now: datetime | None = None
    ) -> list[ReminderRead]:
        """The person's reminders, the most severe first and, within a severity, the
        furthest gone. Acknowledged and snoozed ones are in the list with their status:
        what to show of each is the client's."""
        if actor.id is None:
            raise Conflict(ENTITY, "i promemoria sono di una persona")
        today = today if today is not None else today_local(self.settings)
        evaluation = ReminderEngine(self.session, self.settings).evaluate(today=today, now=now)
        mine = [reminder for reminder in evaluation.reminders if reminder.user_id == actor.id]
        states = states_for(self.session, [actor.id])
        delivered_at: dict[tuple[str, date | None], datetime] = {
            (row.key, row.period): row.delivered_at
            for row in self.session.scalars(
                select(ReminderDelivery).where(
                    ReminderDelivery.user_id == actor.id,
                    ReminderDelivery.channel == IN_APP,
                    ReminderDelivery.key.in_([reminder.key for reminder in mine]),
                )
            )
        }
        out: list[ReminderRead] = []
        for reminder in mine:
            state = states.get((actor.id, reminder.key))
            status = "open"
            snoozed_until = None
            if state is not None and state.covers(reminder.severity, today):
                acknowledged = (
                    state.ack_rank is not None
                    and state.ack_rank >= SEVERITY_RANK[reminder.severity]
                )
                status = "acknowledged" if acknowledged else "snoozed"
                snoozed_until = None if acknowledged else state.snooze_until
            out.append(
                ReminderRead(
                    key=reminder.key,
                    type=reminder.type,
                    label=REMINDER_TYPES[reminder.type].label,
                    severity=reminder.severity,
                    measure=reminder.measure,
                    title=reminder.title,
                    detail=reminder.detail,
                    entity=reminder.entity,
                    entity_id=reminder.entity_id,
                    customer_id=reminder.customer_id,
                    status=status,  # type: ignore[arg-type]
                    snoozed_until=snoozed_until,
                    delivered_at=delivered_at.get((reminder.key, reminder.period)),
                )
            )
        out.sort(key=lambda item: (-SEVERITY_RANK[item.severity], -item.measure, item.key))
        return out
