"""The engine: every reminder, recomputed (spec 2026-10-26 §3, §8, §9; Mastro's `engine.ts`).

`evaluate` runs the detectors that apply and resolves, for every person each detection
names, whether it is theirs and at which severity: their own ladder decides, and a type
they (or the space) switched off is not raised for them. Nothing is read from a table of
reminders because there is none: a cause that has cleared is not detected, and the
reminder is gone with no job to clean up after it.

`record_pass` is the part of a pass that writes, and it is deliberately small. It records
that each reminder reached each person's in-app list (the in-app channel is the source of
truth and every other channel is a copy, spec §6.1), once per rank and, for the types
delivered daily, once a day; it never records one a person has already acknowledged or
snoozed; and it ends the occurrence of every key a type no longer raises
(`ReminderLedger.forget_unraised`). The sends of the other channels are the cards of
those channels and plug in here, after the reads and before the ledger writes, the order
the digest keeps so that a read transaction is not held across a network call.

**The day is the installation's.** `today` is `today_local()`, never the database's clock
or the host's (`db/clock.py`), and a snooze compares dates in that zone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from pigrocrm.core.config import Settings
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.reminders.catalogue import reminder_key, severity_for
from pigrocrm.core.reminders.detect import DetectorContext, Reminder
from pigrocrm.core.reminders.detectors import DETECTORS
from pigrocrm.core.reminders.directory import Directory
from pigrocrm.core.reminders.ledger import DeliveryIdentity, ReminderLedger
from pigrocrm.core.reminders.preferences import ReminderSettings
from pigrocrm.core.reminders.state import states_for

IN_APP = "in_app"


@dataclass(frozen=True)
class Evaluation:
    """What one evaluation found: the reminders (one per person and subject), the types
    that ran, and the keys raised for anybody."""

    reminders: tuple[Reminder, ...]
    ran: frozenset[str]
    raised: frozenset[str]


@dataclass(frozen=True)
class PassResult:
    """What a pass did, in counters: reminders raised, those newly put in a person's list,
    and the occurrences it ended."""

    raised: int
    delivered: int
    ended: int


class ReminderEngine:
    def __init__(
        self, session: Session, settings: Settings, *, owner_email: str | None = None
    ) -> None:
        self.session = session
        self.settings = settings
        self.owner_email = owner_email

    def evaluate(self, *, today: date | None = None, now: datetime | None = None) -> Evaluation:
        """Runs every detector that applies to the space and is switched on for it, and
        resolves each detection per recipient. Reads and writes nothing else."""
        today = today if today is not None else today_local(self.settings)
        now = now if now is not None else datetime.now(UTC)
        preferences = ReminderSettings.load(self.session)
        ctx = DetectorContext(
            session=self.session,
            today=today,
            now=now,
            timezone=self.settings.timezone,
            directory=Directory.load(self.session, self.owner_email),
            settings=preferences,
        )
        reminders: list[Reminder] = []
        ran: set[str] = set()
        raised: set[str] = set()
        for detector in DETECTORS:
            kind = detector.kind
            if not preferences.space_enabled(kind) or not detector.applies(ctx):
                continue
            ran.add(kind.key)
            for detection in detector.detect(ctx):
                key = reminder_key(kind.key, detection.subject_id)
                for user_id in sorted(detection.recipients):
                    effective = preferences.for_user(user_id).type(kind)
                    if not effective.enabled:
                        continue
                    severity = severity_for(kind, detection.measure, effective.ladder)
                    if severity is None:
                        continue
                    raised.add(key)
                    reminders.append(
                        Reminder(
                            key=key,
                            type=kind.key,
                            user_id=user_id,
                            severity=severity,
                            measure=detection.measure,
                            title=detection.title,
                            detail=detection.detail,
                            entity=detection.entity,
                            entity_id=detection.entity_id,
                            azienda_id=detection.azienda_id,
                            customer_id=detection.customer_id,
                            period=today if kind.daily else None,
                        )
                    )
        return Evaluation(tuple(reminders), frozenset(ran), frozenset(raised))

    def plan(
        self, evaluation: Evaluation, *, today: date | None = None
    ) -> list[tuple[Reminder, DeliveryIdentity]]:
        """What this pass would put in people's lists: every reminder the person has not
        dealt with and the ledger does not cover yet. Reads only, which is what makes a
        rehearsal and the real pass one decision (spec §9.4)."""
        today = today if today is not None else today_local(self.settings)
        ledger = ReminderLedger(self.session)
        delivered = ledger.delivered({reminder.key for reminder in evaluation.reminders})
        states = states_for(self.session, {reminder.user_id for reminder in evaluation.reminders})
        due: list[tuple[Reminder, DeliveryIdentity]] = []
        for reminder in evaluation.reminders:
            state = states.get((reminder.user_id, reminder.key))
            if state is not None and state.covers(reminder.severity, today):
                continue
            identity = (reminder.key, reminder.user_id, IN_APP, "immediate", reminder.period)
            if ledger.covered(delivered, identity, reminder.severity):
                continue
            due.append((reminder, identity))
        return due

    def record_pass(
        self, evaluation: Evaluation, *, today: date | None = None, job: str | None = None
    ) -> PassResult:
        """Writes what a pass writes and commits it: the in-app deliveries, then the end
        of every occurrence that cleared, then the job clock when `job` names one. One
        transaction, so a pass that fails halfway leaves the ledger as it found it."""
        ledger = ReminderLedger(self.session)
        due = self.plan(evaluation, today=today)
        for reminder, identity in due:
            ledger.record(identity, reminder.severity)
        ended = ledger.forget_unraised(evaluation.ran, evaluation.raised)
        if job is not None:
            ledger.touch_job(job)
        self.session.commit()
        return PassResult(raised=len(evaluation.reminders), delivered=len(due), ended=ended)
