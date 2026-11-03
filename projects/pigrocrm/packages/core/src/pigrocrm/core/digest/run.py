"""Whether a space gets its week, who receives it, and what is left behind.

Spec 2026-09-16 §3.3. Everything that *decides* about the weekly report lives here, and
nothing that composes it does: `DigestService` builds the object, `mail.digest_mail`
renders it, `telemetry.Tracker` reports it, and this module is the one place that answers
"send it? to whom? again?" -- so the cron (§3.4), a resend and a rehearsal are three calls
to one decision rather than three copies of it.

**The order of `send_for_space` is load-bearing.** `DigestService.build` opens the
operational dashboard's read-only `REPEATABLE READ` snapshot, and `DashboardService`
*refuses* a session that already has a transaction in progress. Every read this method
makes first -- the owner, the four emptiness probes, the week's row, the recipients --
autobegins one. So the reads happen, then `session.rollback()` closes that transaction,
and only then does the build run with the snapshot as its first statement. Nothing has
been written at that point, so the rollback discards nothing: it is a release of a read
transaction, not an undo. What the reads produced is kept as plain values -- an `Actor`,
a list of `(id, indirizzo)` pairs, a row id -- precisely so that the `User` objects going
stale across that rollback costs nothing. Every answer that writes nothing releases that
read transaction too (`_without_write`): the cron holds one session per space for as
long as that space takes, and a connection left idle-in-transaction is how a vacuum stops
working.

**Nothing is recorded that did not happen.** A week is written down only once mails have
actually left: a provider that refused every address (`send_rejected`) and an
installation with no Resend key at all (`send_not_configured`) both answer `skipped` and
leave no row, no timeline entry and no PostHog event, so the next run sends the week
instead of finding it already handled. `--dry-run` is the third answer that writes
nothing, and the only one that reports `sent`: it says what *would* go out.

**What is written, and when.** The mails go out first, then one `digests` row per ISO week
(the column is unique, which is what makes a cron that fires twice send once), one
timeline entry whose payload is three counts, one commit, and only then the PostHog
events. Only the addresses the sender actually accepted reach any of the three.

**The concurrency window this leaves open.** Sending before writing means two crons awake
at the same instant both build, both send, and then the loser's `INSERT` fails on
`uq digests.settimana` -- so a space is mailed twice and one run answers `skipped` with
`IntegrityError`. That is the brief's order and the right way round: the alternative
writes the row first and loses the week silently when the send then fails, and a duplicate
mail is a nuisance where a missing one is the whole feature not happening.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.auth.scope import actor_for, scope_of
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db.scope import bind_scope
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.digest.models import Digest
from pigrocrm.core.digest.schemas import WeeklyDigest
from pigrocrm.core.digest.service import DigestService, iso_week
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.mail import EmailSender, digest_mail
from pigrocrm.core.tasks.models import Task
from pigrocrm.core.telemetry import Tracker
from pigrocrm.core.timetracking.models import TimeEntry

# What the timeline entry is filed under. `digest` is not a `fields.EntityType` and is not
# meant to become one: nobody adds custom fields to a mail that was sent. `activities`
# accepts any string, and `attivita`/`google_account` are the precedent.
ENTITY = "digest"
KIND = "digest.sent"

Outcome = Literal["sent", "empty", "already_sent", "no_recipient", "skipped"]

# The four `skipped` motives that are not an exception's name. Short machine words, like
# the `outcome` literals themselves, and deliberately not a sentence containing the address:
# `reason` is printed in a cron's log.
OWNER_MISSING = "owner_missing"
OWNER_DISABLED = "owner_disabled"
SEND_REJECTED = "send_rejected"
# No Resend key: there is no transport at all, so nothing was sent. The twin of
# `SEND_REJECTED` -- a week nobody received -- and it leaves the same nothing behind, so
# that the run after the key is configured sends the week instead of finding it recorded.
SEND_NOT_CONFIGURED = "send_not_configured"


@dataclass(frozen=True)
class DigestOutcome:
    """What happened to one space in one week, in the terms §3.4's command prints.

    `recipients` counts the people the report actually reached -- for a rehearsal, the
    people it would have reached. Not the people who were eligible: a provider that
    refuses two addresses out of three has not sent three reports.

    `reason` is filled only for `skipped`, and never with an exception's *message*: a
    provider's error text routinely quotes the address it failed on, and this value is
    written to a log and handed back to a command.
    """

    slug: str
    outcome: Outcome
    week: str
    recipients: int = 0
    reason: str = ""


@dataclass(frozen=True)
class _FromRefer:
    """What `_send` leaves for `send_for_space` to report to PostHog once the commit has
    made it true. Internal: the caller of `send_for_space` never sees it."""

    recipients: tuple[tuple[UUID, str], ...]
    sections: int


def sections_with_lines(digest: WeeklyDigest) -> int:
    """How many of §3.1's seven sections, and REB-726's eighth, have something in them.

    A *section* count, not a list count: «Da incassare» is two lists and one heading,
    «Da emettere» is three figures on two lines, and «In pipeline» is three lists. It is
    what `digest_sent` carries as `sections`, so PostHog can tell the week that was
    worth reading from the one that said «settimana ferma», and the timeline entry carries
    the same number for the same reason.

    Each predicate below is the one `mail.digest_mail` uses to decide whether to print
    that heading, which is what makes this a count of the sections the recipient saw
    rather than a second opinion about them. «Da emettere» is the one worth spelling out:
    the mail prints a line for `won_to_invoice` and a second for `ore_non_fatturate or
    valore_maturato`, so the section exists when any of the three is set.

    «Da fare» (REB-726) is the eighth, and the only one set per recipient: on a report
    straight out of `DigestService.build` it is empty, so the seven above are unchanged.

    `hours` is the one section tested for presence rather than for content, because
    `DigestService.build` has already collapsed a week of zero hours to `None` -- so
    `is not None` is the whole question, and `hours.total_hours` would be a second, weaker
    way of asking it.
    """
    return sum(
        [
            bool(digest.overdue or digest.in_due_date),
            bool(digest.won_to_invoice or digest.hours_uninvoiced or digest.accrued_value),
            bool(digest.issued),
            bool(digest.paid),
            digest.hours is not None,
            bool(digest.pipeline or digest.deal_mossi or digest.offers_pending),
            bool(digest.signals),
            not digest.tasks.empty,
        ]
    )


class DigestRun:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        sender: EmailSender | None,
        tracker: Tracker | None,
        public_url: str,
    ) -> None:
        self.session = session
        self.settings = settings
        # `None` for either is the ordinary state of an installation that has configured
        # neither Resend nor PostHog, and neither is an error -- but they are not the same
        # thing. Without a tracker the week still goes out and is still recorded, and only
        # PostHog hears nothing. Without a sender there is no transport: a real run answers
        # `skipped` (`SEND_NOT_CONFIGURED`) and writes nothing, exactly as it does when
        # the provider refuses every address.
        self.sender = sender
        self.tracker = tracker
        self.public_url = public_url

    def send_for_space(
        self,
        slug: str,
        owner_email: str | None,
        week: tuple[date, date],
        *,
        forza: bool = False,
        dry_run: bool = False,
    ) -> DigestOutcome:
        """The whole decision for one space, in the order the module docstring gives.

        The four early answers -- `skipped`, `empty`, `already_sent`, `no_recipient`
        -- are returned before anything is built and write nothing at all. Only the last
        path opens the snapshot, and everything it does after that point is inside the
        `try` below, because half a sent week is worse than an unsent one.

        `owner_email` is the registry's `Tenant.owner_email` for a space, and `None` for
        the root installation, which has no registry row (REB-263): its owner is the
        first active admin, so a root with none answers `owner_missing` like a space
        whose owner's address matches nobody.
        """
        iso = iso_week(week[0])
        users = UserRepository(self.session)

        owner = (
            users.get_by_email(owner_email)
            if owner_email is not None
            else users.first_active_admin()
        )
        if owner is None:
            return self._without_write(DigestOutcome(slug, "skipped", iso, reason=OWNER_MISSING))
        if not owner.active:
            return self._without_write(DigestOutcome(slug, "skipped", iso, reason=OWNER_DISABLED))
        # `cast` and not a runtime check, exactly as `cli.py`'s `_cron_actor` does it:
        # `Actor` validates `role` against its own literal on construction, so a column
        # holding something else raises there rather than travelling on unnoticed.
        # The cron's own actor sees the whole space, whatever the owner's scope is:
        # the question `_space_empty` asks is the space's, and each recipient's mail is
        # built inside that recipient's scope below. Bound before the first read of a
        # policied table (REB-634): the cron opens this session as the application
        # role, and an unbound probe of `customers` would answer «vuoto» for every
        # space, for ever; bound to a scoped owner's legal entities it would answer «vuoto»
        # for everyone else's (Greptile on PR #513).
        actor = actor_for(owner, "system").model_copy(update={"legal_entities": None})
        bind_scope(self.session, actor)

        if self._space_empty():
            return self._without_write(DigestOutcome(slug, "empty", iso))

        riga_id = self._line_of_week(iso)
        if riga_id is not None and not forza:
            return self._without_write(DigestOutcome(slug, "already_sent", iso))

        # Plain values, taken now: the `User` objects behind them expire on the rollback
        # the build needs, and reloading them would reopen the very transaction that
        # rollback exists to close.
        # Each recipient with their own scope (spec 2026-10-03 §4): the report is
        # rendered once per distinct scope, so B's Monday mail sums B's legal entities alone.
        recipients = [
            (user.id, user.email, scope_of(user))
            for user in users.list_all()
            if user.active and user.weekly_digest
        ]
        if not recipients:
            return self._without_write(DigestOutcome(slug, "no_recipient", iso))

        try:
            outcome, from_refer = self._send(
                slug,
                iso,
                actor,
                recipients,
                riga_id,
                week,
                dry_run=dry_run,
            )
        except Exception as exc:  # noqa: BLE001 - one space's failure is not the cron's
            # Whatever went wrong, this week is left exactly as it was found: no row, no
            # timeline entry, and a motive the operator can act on without it carrying an
            # address out of the exception's text.
            self.session.rollback()
            return DigestOutcome(slug, "skipped", iso, reason=type(exc).__name__)

        # Outside the `try` deliberately, and not only after the commit: by this point the
        # mails have gone and the week is recorded, so nothing PostHog does may turn this
        # answer into `skipped` or roll back a transaction that no longer exists.
        # `from_refer` is `None` for a rehearsal, which reports nothing to anybody.
        if from_refer is not None and self.tracker is not None:
            for user_id, _ in from_refer.recipients:
                self.tracker.digest_sent(user_id, week=iso, sections=from_refer.sections)
        return outcome

    # -- the path that sends ----------------------------------------------------------

    def _send(
        self,
        slug: str,
        iso: str,
        actor: Actor,
        recipients: list[tuple[UUID, str, tuple[UUID, ...] | None]],
        riga_id: UUID | None,
        week: tuple[date, date],
        *,
        dry_run: bool,
    ) -> tuple[DigestOutcome, _FromRefer | None]:
        """The outcome, and whom PostHog is to be told about -- `None` when it is to be
        told nothing, which is a rehearsal and only a rehearsal.

        Everything here runs inside the caller's `try`, and the caller does the tracking
        afterwards, so the capture cannot be the reason a committed week reports `skipped`.
        """
        if not dry_run and self.sender is None:
            # No Resend key, so nothing can leave and nobody is written to. Recording the
            # week here -- which is what this did until the branch review -- made it
            # `already_sent` for ever, wrote a timeline entry saying it had reached N
            # people and told PostHog the same, all about a mail that was never attempted.
            # Nothing is written, so the first run after the key is configured sends it;
            # and it is answered before the report is built, which a mail nobody can send
            # does not need. A rehearsal goes on: `--dry-run` chose not to send, and it
            # wants the report to say what would have gone out.
            return (
                self._without_write(
                    DigestOutcome(slug, "skipped", iso, reason=SEND_NOT_CONFIGURED)
                ),
                None,
            )

        # Nothing has been written yet: this closes the *read* transaction the checks
        # above autobegan, so that the dashboard's snapshot is the first statement of the
        # next one. `DashboardService._open_snapshot` refuses a session in a transaction.
        # One report per distinct scope, each built with that scope bound on the session
        # (spec 2026-10-03 §4): the dashboard call inside `build` opens its own snapshot,
        # and the listener `bind_scope` registers writes the scope at the start of it.
        # The owner's own actor, with the recipients' scope in place of theirs.
        by_scope: dict[tuple[UUID, ...] | None, list[tuple[UUID, str]]] = {}
        for user_id, address, scope in recipients:
            by_scope.setdefault(scope, []).append((user_id, address))
        accepted: list[tuple[UUID, str]] = []
        sections = 0
        for scope, group in by_scope.items():
            bind_scope(self.session, actor.model_copy(update={"legal_entities": scope}))
            self.session.rollback()
            service = DigestService(self.session, self.settings)
            digest = service.build(actor, week)
            for user_id, address in group:
                # «Da fare» is the person's, so it is set on a copy per address; the rest
                # of the report is shared by the scope's group.
                his = digest.model_copy(update={"tasks": service.task_of(user_id)})
                sections = max(sections, sections_with_lines(his))
                # `send` answers `False` for a refusal rather than raising, so only the
                # addresses the provider took are recorded, counted and reported.
                if (
                    not dry_run
                    and self.sender is not None
                    and self.sender.send(digest_mail(address, his, public_url=self.public_url))
                ):
                    accepted.append((user_id, address))
        bind_scope(self.session, actor)

        if dry_run:
            # A rehearsal: the report is built so the operator can be told what would go
            # out and to how many people, and then nothing at all happens. It needs no
            # sender, which is the whole of its difference from the answer just below:
            # `--dry-run` chose not to send, an installation with no key cannot.
            return (
                self._without_write(DigestOutcome(slug, "sent", iso, recipients=len(recipients))),
                None,
            )

        if not accepted:
            # Every address was refused. `EmailSender.send` never raises, so without this
            # the week would be recorded, tracked and `already_sent` forever on the
            # strength of mails that never arrived. Nothing is written, so next Monday --
            # or a rerun ten minutes later -- tries again.
            return (
                self._without_write(DigestOutcome(slug, "skipped", iso, reason=SEND_REJECTED)),
                None,
            )

        indirizzi = [address for _, address in accepted]
        riga = self._record(iso, indirizzi, riga_id)
        # Last touch on the session before the commit, as `ActivityService.record`'s own
        # contract requires: it flushes into this transaction and never commits.
        ActivityService(self.session).record(
            ENTITY,
            riga.id,
            KIND,
            actor,
            {"week": iso, "recipients": len(accepted), "sections": sections},
        )
        self.session.commit()

        # The week is out and recorded. The caller reports it to PostHog from here.
        return (
            DigestOutcome(slug, "sent", iso, recipients=len(accepted)),
            _FromRefer(tuple(accepted), sections),
        )

    # -- the reads, and the row -------------------------------------------------------

    def _without_write(self, outcome: DigestOutcome) -> DigestOutcome:
        """An answer that wrote nothing, with the read transaction released.

        Every check above autobegins one, and the cron keeps this session for as long as
        the space takes. Rolling back here discards nothing -- nothing was written on any
        path that comes through this method -- and leaves the session where the caller
        handed it over.
        """
        self.session.rollback()
        return outcome

    def _space_empty(self) -> bool:
        """§2: silence for a space with nothing in it.

        Four existence probes (and a fifth, for open task) rather than the built
        report's own `empty`, because this is the question asked *before* building: a space
        that has never had a customer should cost one bounded query per table on a Monday
        morning, not seven sections and a snapshot. `LIMIT 1` on the primary key: the
        answer is whether there is a row, not how many.

        `deleted_at IS NULL` on every one of them. All four carry `SoftDeleteMixin` and
        core applies no global filter, so without it a space whose customer and deal were
        deleted last year is not empty and is mailed a report in which every section is
        empty -- which is the one mail §2 exists to prevent. It is also the condition the
        registers themselves read under, so the probe and the report agree about what
        exists.
        """
        empty = all(
            self.session.execute(
                select(model.id).where(model.deleted_at.is_(None)).limit(1)
            ).first()
            is None
            for model in (Customer, Deal, Invoice, TimeEntry)
        )
        if not empty:
            return False
        # A fifth probe, and a different one: an open task needs none of the four
        # records above (a to-do belongs to nobody's customer), so a space that holds
        # only commitments still has a week to report in «Da fare» (REB-726). Only the
        # open ones: a space whose every task is closed has nothing to say.
        return (
            self.session.execute(
                select(Task.id).where(Task.deleted_at.is_(None), Task.status == "open").limit(1)
            ).first()
            is None
        )

    def _line_of_week(self, iso: str) -> UUID | None:
        return self.session.execute(
            select(Digest.id).where(Digest.week == iso)
        ).scalar_one_or_none()

    def _record(self, iso: str, indirizzi: list[str], riga_id: UUID | None) -> Digest:
        """The week's row: created, or -- under `forza` -- moved.

        `digests.week` is unique, so a resend cannot be a second row: it updates the
        addresses and the instant, and the row goes on being the answer to «did this week
        go out, and to whom». `datetime.now(UTC)` is the same expression `occurred_at`'s
        column default uses, and it is an instant rather than a calendar day -- the thing
        `db/clock.py` bans is projecting `now()` onto a *date*, which nothing here does.
        """
        if riga_id is None:
            riga = Digest(week=iso, sent_to=indirizzi, occurred_at=datetime.now(UTC))
            self.session.add(riga)
            self.session.flush()
            return riga
        riga = self.session.get_one(Digest, riga_id)
        riga.sent_to = indirizzi
        riga.occurred_at = datetime.now(UTC)
        self.session.flush()
        return riga
