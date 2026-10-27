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

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from pigrocrm.core.attivita.models import Attivita
from pigrocrm.core.automations.schemas import REGOLA_COPERTA_DA_PROMEMORIA
from pigrocrm.core.config import Settings
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.reminders.catalogue import reminder_key, severity_for
from pigrocrm.core.reminders.detect import DetectorContext, Reminder
from pigrocrm.core.reminders.detectors import DETECTORS
from pigrocrm.core.reminders.directory import Directory
from pigrocrm.core.reminders.dispatch import IN_APP, ChannelResult
from pigrocrm.core.reminders.jobs import JobClock
from pigrocrm.core.reminders.ledger import DeliveryIdentity, ReminderLedger
from pigrocrm.core.reminders.preferences import ReminderSettings
from pigrocrm.core.reminders.push_subscriptions import PushSubscription
from pigrocrm.core.reminders.state import states_for

_ATTIVITA_TYPES = frozenset({"attivita_due", "attivita_overdue"})


@dataclass(frozen=True)
class Evaluation:
    """What one evaluation found: the reminders (one per person and subject), the types
    that ran, and the keys raised for anybody."""

    reminders: tuple[Reminder, ...]
    ran: frozenset[str]
    raised: frozenset[str]
    # The wall-clock moment the evaluation began: what a row written later is told apart by.
    evaluated_at: datetime | None = None


@dataclass(frozen=True)
class PassResult:
    """What a pass did, in counters: reminders raised, those newly put in a person's list,
    the occurrences it ended, and the messages the other channels sent (`sent`, by
    channel)."""

    raised: int
    delivered: int
    ended: int
    sent: Mapping[str, int] = field(default_factory=dict)


class ReminderEngine:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def evaluate(self, *, today: date | None = None, now: datetime | None = None) -> Evaluation:
        """Runs every detector that applies to the space and is switched on for it, and
        resolves each detection per recipient. Reads and writes nothing else."""
        evaluated_at = datetime.now(UTC)
        today = today if today is not None else today_local(self.settings)
        now = now if now is not None else evaluated_at
        preferences = ReminderSettings.load(self.session)
        ctx = DetectorContext(
            session=self.session,
            today=today,
            now=now,
            timezone=self.settings.timezone,
            directory=Directory.load(self.session),
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
        # `raised` keeps what was detected, covered or not: a person's acknowledge or snooze
        # of an attivita must survive the day its reminder is not raised for them, and ends
        # only when the cause is gone (`forget_unraised`).
        reminders = self._without_what_a_reminder_already_says(reminders)
        return Evaluation(tuple(reminders), frozenset(ran), frozenset(raised), evaluated_at)

    def _without_what_a_reminder_already_says(self, reminders: list[Reminder]) -> list[Reminder]:
        """Drops, for one person, the attività reminder of what `fattura_scaduta` opened
        when that person is already told of the same invoice by `invoice_overdue`.

        The two share a cause: the pass opens the attività the day the invoice is late and
        the reminder climbs its own ladder from the same day, so announcing both says it
        twice. Per person and not per space, because `invoice_overdue` can be switched off
        or its first rung raised for one person: for them the attività is the only notice
        and keeps its own reminders. Nothing is stored and nothing is hidden from the
        Attività page, only the second announcement is not raised."""
        told = {(r.user_id, r.entity_id) for r in reminders if r.type == "invoice_overdue"}
        candidates = {r.entity_id for r in reminders if r.type in _ATTIVITA_TYPES}
        if not told or not candidates:
            return reminders
        covered = {
            attivita_id: invoice_id
            for attivita_id, invoice_id in self.session.execute(
                select(Attivita.id, Attivita.invoice_id).where(
                    Attivita.id.in_(candidates),
                    Attivita.regola == REGOLA_COPERTA_DA_PROMEMORIA,
                    Attivita.invoice_id.is_not(None),
                )
            )
        }
        return [
            r
            for r in reminders
            if not (
                r.type in _ATTIVITA_TYPES
                and r.entity_id in covered
                and (r.user_id, covered[r.entity_id]) in told
            )
        ]

    def open_reminders(
        self, evaluation: Evaluation, *, today: date | None = None
    ) -> list[Reminder]:
        """The reminders each person has not dealt with: everything raised minus what they
        acknowledged, or snoozed, at that severity or above. What every channel is
        offered, the in-app list included. Reads only."""
        today = today if today is not None else today_local(self.settings)
        states = states_for(self.session, {reminder.user_id for reminder in evaluation.reminders})
        open_: list[Reminder] = []
        for reminder in evaluation.reminders:
            state = states.get((reminder.user_id, reminder.key))
            if state is not None and state.covers(reminder.severity, today):
                continue
            open_.append(reminder)
        return open_

    def plan(
        self,
        evaluation: Evaluation,
        *,
        today: date | None = None,
        open_reminders: list[Reminder] | None = None,
    ) -> list[tuple[Reminder, DeliveryIdentity]]:
        """What this pass would put in people's lists: every reminder the person has not
        dealt with and the ledger does not cover yet. Reads only, which is what makes a
        rehearsal and the real pass one decision (spec §9.4)."""
        if open_reminders is None:
            open_reminders = self.open_reminders(evaluation, today=today)
        ledger = ReminderLedger(self.session)
        delivered = ledger.delivered({reminder.key for reminder in evaluation.reminders})
        due: list[tuple[Reminder, DeliveryIdentity]] = []
        for reminder in open_reminders:
            identity = (reminder.key, reminder.user_id, IN_APP, "immediate", reminder.period)
            if ledger.covered(delivered, identity, reminder.severity):
                continue
            due.append((reminder, identity))
        return due

    def record_pass(
        self,
        evaluation: Evaluation,
        *,
        today: date | None = None,
        job: str | None = None,
        due: list[tuple[Reminder, DeliveryIdentity]] | None = None,
        sent: ChannelResult | None = None,
        now: datetime | None = None,
    ) -> PassResult:
        """Writes what a pass writes and commits it: the in-app deliveries, what the other
        channels accepted (the ledger rows, the daily summary markers, the push
        subscriptions the service reported gone), then the end of every occurrence that
        cleared, then the job clock when `job` names one. One transaction, so a pass that
        fails halfway leaves the ledger as it found it.

        `due` is the plan the channels were prepared from, when the caller made one: the
        pass writes exactly what it announced, and not what the ledger says a few seconds
        and a network call later. `now` is the instant the pass is for, and what every
        delivery is stamped with: a channel announces what reached the list after a browser
        subscribed by comparing these times, so they are the pass's, not the wall clock's.
        """
        ledger = ReminderLedger(self.session)
        if due is None:
            due = self.plan(evaluation, today=today)
        for reminder, identity in due:
            ledger.record(identity, reminder.severity, at=now)
        if sent is not None:
            for accepted in sent.accepted:
                ledger.record(accepted.identity, accepted.severity, at=now)
            for user_id, day in sent.summaries:
                ledger.mark_summary_sent(user_id, day)
            if sent.gone_endpoints:
                self.session.execute(
                    delete(PushSubscription).where(
                        PushSubscription.endpoint.in_(sent.gone_endpoints)
                    )
                )
        ended = ledger.forget_unraised(
            evaluation.ran, evaluation.raised, before=evaluation.evaluated_at
        )
        if job is not None:
            JobClock(self.session).succeeded(job)
        self.session.commit()
        return PassResult(
            raised=len(evaluation.reminders),
            delivered=len(due),
            ended=ended,
            sent=dict(sent.sent) if sent is not None else {},
        )
