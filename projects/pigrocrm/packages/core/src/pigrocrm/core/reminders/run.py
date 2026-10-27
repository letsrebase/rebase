"""One space's reminder pass: the lock, the decision, and what is left behind (spec
2026-10-26 §9; the shape of `digest/run.py`).

`ReminderRun.run_for_space` is the one place that answers «what does this pass do for this
space», so the cron, a rehearsal (`--dry-run`) and a re-evaluation of another day
(`--data`) are three calls to one decision and never three copies of it.

**One pass per space at a time** (§9.2). The pass takes a Postgres advisory lock on the
space's database, `pg_try_advisory_lock`, **before it reads any delivery state**, and holds
it until the ledger writes are committed. Taking it only around the sends would let two
passes read the same undelivered list and send from it one after the other. The lock is
held on a connection of its own, in autocommit, and released explicitly: a session-level
lock belongs to the connection that took it, and the working session commits in the middle
of a pass, which hands its connection back to the pool. A pass that does not get the lock
answers `in_corso`, which is the system working as designed and not a failure.

**Reads first, then the network, then writes.** The detectors and the channels' plans read;
the read transaction is then ended, and only after that do the other channels send (push,
email), so no transaction is held across a network call. The ledger writes, what the
channels accepted and the job clock (`reminder_job_run`, which `job_late` and Impostazioni →
Esecuzioni read) follow in one transaction (`ReminderEngine.record_pass`), so a pass that
fails halfway writes nothing and the next one sends again, which is the contract of §8.3:
a duplicate message after a crash between the send and the write, never a missing one. A
real run on a space that has nothing to raise still touches the clock: a pass that ran and
found nothing is a pass that ran.

**Nothing is recorded that did not happen.** A rehearsal writes nothing at all, not even
the clock, and reports what *would* be delivered and sent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.db.scope import bind_scope
from pigrocrm.core.mail import EmailSender
from pigrocrm.core.reminders.channels import dispatcher_for
from pigrocrm.core.reminders.engine import ReminderEngine
from pigrocrm.core.reminders.jobs import REMINDER_PASS_JOB
from pigrocrm.core.reminders.push import PushSender

# `pg_try_advisory_lock(int, int)`: a namespace of this project's own, as
# `gmail/repository.py` and `customers/repository.py` do, and one key for this pass.
REMINDERS_LOCK_NAMESPACE = 0x7093
_PASS_LOCK_KEY = 1

Esito = Literal["eseguito", "in_corso"]


@dataclass(frozen=True)
class ReminderOutcome:
    """What happened to one space in one pass, in the terms the command prints. Counters
    only: never a title, a customer's name or an address, since the line goes to a file on
    the host."""

    slug: str
    esito: Esito
    raised: int = 0
    delivered: int = 0
    ended: int = 0
    # Messages the other channels sent (on a rehearsal, would have sent).
    pushes: int = 0
    emails: int = 0


class ReminderRun:
    def __init__(
        self,
        settings: Settings,
        *,
        sender: EmailSender | None = None,
        pusher: PushSender | None = None,
    ) -> None:
        """`sender` and `pusher` are the transports of the email and push channels; `None`
        is a channel with no transport, which sends nothing and records nothing."""
        self.settings = settings
        self.dispatcher = dispatcher_for(sender=sender, pusher=pusher)

    def run_for_space(
        self,
        slug: str,
        engine: Engine,
        *,
        today: date | None = None,
        now: datetime | None = None,
        dry_run: bool = False,
        public_url: str = "",
    ) -> ReminderOutcome:
        """The whole decision for one space. The space's owner, who hears of a reminder
        nobody else can be told, is its first active admin, the same person the list
        in the app falls back to (`directory.py`).

        `public_url` is the base of every link in a mail or a notification: the space's own
        address.

        Raises whatever the database raises: the caller turns it into one `saltato` line,
        the way the digest's does, so one space's failure is not the pass's.
        """
        with engine.connect() as lock:
            lock = lock.execution_options(isolation_level="AUTOCOMMIT")
            held: bool = lock.execute(
                text("SELECT pg_try_advisory_lock(:namespace, :key)"),
                {"namespace": REMINDERS_LOCK_NAMESPACE, "key": _PASS_LOCK_KEY},
            ).scalar_one()
            if not held:
                return ReminderOutcome(slug, "in_corso")
            try:
                return self._pass(slug, engine, today, now, dry_run, public_url)
            finally:
                lock.execute(
                    text("SELECT pg_advisory_unlock(:namespace, :key)"),
                    {"namespace": REMINDERS_LOCK_NAMESPACE, "key": _PASS_LOCK_KEY},
                )

    def _pass(
        self,
        slug: str,
        engine: Engine,
        today: date | None,
        now: datetime | None,
        dry_run: bool,
        public_url: str,
    ) -> ReminderOutcome:
        today = today if today is not None else today_local(self.settings)
        now = now if now is not None else datetime.now(UTC)
        factory: sessionmaker[Session] = sessionmaker(engine, expire_on_commit=False)
        with factory() as session:
            # The pass sees the whole space: each reminder is then kept from the people
            # whose scope does not cover it, by the recipient rules (`directory.py`), and
            # not by what the cron's own session could read (REB-634).
            bind_scope(session, Actor.system())
            reminders = ReminderEngine(session, self.settings)
            evaluation = reminders.evaluate(today=today, now=now)
            open_reminders = reminders.open_reminders(evaluation, today=today)
            due = reminders.plan(evaluation, today=today, open_reminders=open_reminders)
            prepared = self.dispatcher.prepare(
                session,
                self.settings,
                open_reminders,
                newly_listed={identity for _, identity in due},
                today=today,
                now=now,
                public_url=public_url,
            )
            if dry_run:
                session.rollback()
                planned = {plan.channel: plan.planned for plan in prepared}
                return ReminderOutcome(
                    slug,
                    "eseguito",
                    raised=len(evaluation.reminders),
                    delivered=len(due),
                    pushes=planned.get("push", 0),
                    emails=planned.get("email", 0),
                )
            # The read transaction ends before the first network call.
            session.rollback()
            sent = self.dispatcher.send(prepared)
            result = reminders.record_pass(
                evaluation, today=today, job=REMINDER_PASS_JOB, due=due, sent=sent, now=now
            )
            return ReminderOutcome(
                slug,
                "eseguito",
                raised=result.raised,
                delivered=result.delivered,
                ended=result.ended,
                pushes=result.sent.get("push", 0),
                emails=result.sent.get("email", 0),
            )
