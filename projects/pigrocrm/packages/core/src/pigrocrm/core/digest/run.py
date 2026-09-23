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
read transaction too (`_senza_scrivere`): the cron holds one session per space for as
long as that space takes, and a connection left idle-in-transaction is how a vacuum stops
working.

**Nothing is recorded that did not happen.** A week is written down only once mails have
actually left: a provider that refused every address (`invio_rifiutato`) and an
installation with no Resend key at all (`invio_non_configurato`) both answer `saltato` and
leave no row, no timeline entry and no PostHog event, so the next run sends the week
instead of finding it already handled. `--dry-run` is the third answer that writes
nothing, and the only one that reports `inviato`: it says what *would* go out.

**What is written, and when.** The mails go out first, then one `digests` row per ISO week
(the column is unique, which is what makes a cron that fires twice send once), one
timeline entry whose payload is three counts, one commit, and only then the PostHog
events. Only the addresses the sender actually accepted reach any of the three.

**The concurrency window this leaves open.** Sending before writing means two crons awake
at the same instant both build, both send, and then the loser's `INSERT` fails on
`uq digests.settimana` -- so a space is mailed twice and one run answers `saltato` with
`IntegrityError`. That is the brief's order and the right way round: the alternative
writes the row first and loses the week silently when the send then fails, and a duplicate
mail is a nuisance where a missing one is the whole feature not happening.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor, Role
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.digest.models import Digest
from pigrocrm.core.digest.schemas import WeeklyDigest
from pigrocrm.core.digest.service import DigestService, iso_week
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.mail import EmailSender, digest_mail
from pigrocrm.core.telemetry import Tracker
from pigrocrm.core.timetracking.models import TimeEntry

# What the timeline entry is filed under. `digest` is not a `fields.EntityType` and is not
# meant to become one: nobody adds custom fields to a mail that was sent. `activities`
# accepts any string, and `attivita`/`google_account` are the precedent.
ENTITA = "digest"
KIND = "digest.inviato"

Esito = Literal["inviato", "vuoto", "gia_inviato", "nessun_destinatario", "saltato"]

# The four `saltato` motives that are not an exception's name. Short machine words, like
# the `esito` literals themselves, and deliberately not a sentence containing the address:
# `motivo` is printed in a cron's log.
TITOLARE_MANCANTE = "titolare_mancante"
TITOLARE_DISATTIVATO = "titolare_disattivato"
INVIO_RIFIUTATO = "invio_rifiutato"
# No Resend key: there is no transport at all, so nothing was sent. The twin of
# `INVIO_RIFIUTATO` -- a week nobody received -- and it leaves the same nothing behind, so
# that the run after the key is configured sends the week instead of finding it recorded.
INVIO_NON_CONFIGURATO = "invio_non_configurato"


@dataclass(frozen=True)
class DigestOutcome:
    """What happened to one space in one week, in the terms §3.4's command prints.

    `destinatari` counts the people the report actually reached -- for a rehearsal, the
    people it would have reached. Not the people who were eligible: a provider that
    refuses two addresses out of three has not sent three reports.

    `motivo` is filled only for `saltato`, and never with an exception's *message*: a
    provider's error text routinely quotes the address it failed on, and this value is
    written to a log and handed back to a command.
    """

    slug: str
    esito: Esito
    settimana: str
    destinatari: int = 0
    motivo: str = ""


@dataclass(frozen=True)
class _DaRiferire:
    """What `_invia` leaves for `send_for_space` to report to PostHog once the commit has
    made it true. Internal: the caller of `send_for_space` never sees it."""

    destinatari: tuple[tuple[UUID, str], ...]
    sezioni: int


def sezioni_con_righe(digest: WeeklyDigest) -> int:
    """How many of §3.1's seven sections have something in them.

    A *section* count, not a list count: «Da incassare» is two lists and one heading,
    «Da emettere» is three figures on two lines, and «In pipeline» is three lists. It is
    what `digest_inviato` carries as `sezioni`, so PostHog can tell the week that was
    worth reading from the one that said «settimana ferma», and the timeline entry carries
    the same number for the same reason.

    Each predicate below is the one `mail.digest_mail` uses to decide whether to print
    that heading, which is what makes this a count of the sections the recipient saw
    rather than a second opinion about them. «Da emettere» is the one worth spelling out:
    the mail prints a line for `vinti_da_fatturare` and a second for `ore_non_fatturate or
    valore_maturato`, so the section exists when any of the three is set.

    `ore` is the one section tested for presence rather than for content, because
    `DigestService.build` has already collapsed a week of zero hours to `None` -- so
    `is not None` is the whole question, and `ore.ore_totali` would be a second, weaker
    way of asking it.
    """
    return sum(
        [
            bool(digest.scadute or digest.in_scadenza),
            bool(digest.vinti_da_fatturare or digest.ore_non_fatturate or digest.valore_maturato),
            bool(digest.emesse),
            bool(digest.incassate),
            digest.ore is not None,
            bool(digest.pipeline or digest.deal_mossi or digest.offerte_in_attesa),
            bool(digest.segnali),
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
        # `saltato` (`INVIO_NON_CONFIGURATO`) and writes nothing, exactly as it does when
        # the provider refuses every address.
        self.sender = sender
        self.tracker = tracker
        self.public_url = public_url

    def send_for_space(
        self,
        slug: str,
        owner_email: str | None,
        settimana: tuple[date, date],
        *,
        forza: bool = False,
        dry_run: bool = False,
    ) -> DigestOutcome:
        """The whole decision for one space, in the order the module docstring gives.

        The four early answers -- `saltato`, `vuoto`, `gia_inviato`, `nessun_destinatario`
        -- are returned before anything is built and write nothing at all. Only the last
        path opens the snapshot, and everything it does after that point is inside the
        `try` below, because half a sent week is worse than an unsent one.

        `owner_email` is the registry's `Tenant.owner_email` for a space, and `None` for
        the root installation, which has no registry row (REB-263): its titolare is the
        first active admin, so a root with none answers `titolare_mancante` like a space
        whose owner's address matches nobody.
        """
        iso = iso_week(settimana[0])
        utenti = UserRepository(self.session)

        titolare = (
            utenti.get_by_email(owner_email)
            if owner_email is not None
            else utenti.first_active_admin()
        )
        if titolare is None:
            return self._senza_scrivere(
                DigestOutcome(slug, "saltato", iso, motivo=TITOLARE_MANCANTE)
            )
        if not titolare.attivo:
            return self._senza_scrivere(
                DigestOutcome(slug, "saltato", iso, motivo=TITOLARE_DISATTIVATO)
            )
        # `cast` and not a runtime check, exactly as `cli.py`'s `_cron_actor` does it:
        # `Actor` validates `role` against its own literal on construction, so a column
        # holding something else raises there rather than travelling on unnoticed.
        attore = Actor(id=titolare.id, type="system", role=cast(Role, titolare.ruolo))

        if self._spazio_vuoto():
            return self._senza_scrivere(DigestOutcome(slug, "vuoto", iso))

        riga_id = self._riga_della_settimana(iso)
        if riga_id is not None and not forza:
            return self._senza_scrivere(DigestOutcome(slug, "gia_inviato", iso))

        # Plain values, taken now: the `User` objects behind them expire on the rollback
        # the build needs, and reloading them would reopen the very transaction that
        # rollback exists to close.
        destinatari = [
            (utente.id, utente.email)
            for utente in utenti.list_all()
            if utente.attivo and utente.digest_settimanale
        ]
        if not destinatari:
            return self._senza_scrivere(DigestOutcome(slug, "nessun_destinatario", iso))

        try:
            esito, da_riferire = self._invia(
                slug,
                iso,
                attore,
                destinatari,
                riga_id,
                settimana,
                dry_run=dry_run,
            )
        except Exception as exc:  # noqa: BLE001 - one space's failure is not the cron's
            # Whatever went wrong, this week is left exactly as it was found: no row, no
            # timeline entry, and a motive the operator can act on without it carrying an
            # address out of the exception's text.
            self.session.rollback()
            return DigestOutcome(slug, "saltato", iso, motivo=type(exc).__name__)

        # Outside the `try` deliberately, and not only after the commit: by this point the
        # mails have gone and the week is recorded, so nothing PostHog does may turn this
        # answer into `saltato` or roll back a transaction that no longer exists.
        # `da_riferire` is `None` for a rehearsal, which reports nothing to anybody.
        if da_riferire is not None and self.tracker is not None:
            for user_id, _ in da_riferire.destinatari:
                self.tracker.digest_sent(user_id, settimana=iso, sezioni=da_riferire.sezioni)
        return esito

    # -- the path that sends ----------------------------------------------------------

    def _invia(
        self,
        slug: str,
        iso: str,
        attore: Actor,
        destinatari: list[tuple[UUID, str]],
        riga_id: UUID | None,
        settimana: tuple[date, date],
        *,
        dry_run: bool,
    ) -> tuple[DigestOutcome, _DaRiferire | None]:
        """The outcome, and whom PostHog is to be told about -- `None` when it is to be
        told nothing, which is a rehearsal and only a rehearsal.

        Everything here runs inside the caller's `try`, and the caller does the tracking
        afterwards, so the capture cannot be the reason a committed week reports `saltato`.
        """
        if not dry_run and self.sender is None:
            # No Resend key, so nothing can leave and nobody is written to. Recording the
            # week here -- which is what this did until the branch review -- made it
            # `gia_inviato` for ever, wrote a timeline entry saying it had reached N
            # people and told PostHog the same, all about a mail that was never attempted.
            # Nothing is written, so the first run after the key is configured sends it;
            # and it is answered before the report is built, which a mail nobody can send
            # does not need. A rehearsal goes on: `--dry-run` chose not to send, and it
            # wants the report to say what would have gone out.
            return (
                self._senza_scrivere(
                    DigestOutcome(slug, "saltato", iso, motivo=INVIO_NON_CONFIGURATO)
                ),
                None,
            )

        # Nothing has been written yet: this closes the *read* transaction the checks
        # above autobegan, so that the dashboard's snapshot is the first statement of the
        # next one. `DashboardService._open_snapshot` refuses a session in a transaction.
        self.session.rollback()

        digest = DigestService(self.session, self.settings).build(attore, settimana)
        sezioni = sezioni_con_righe(digest)

        if dry_run:
            # A rehearsal: the report is built so the operator can be told what would go
            # out and to how many people, and then nothing at all happens. It needs no
            # sender, which is the whole of its difference from the answer just below:
            # `--dry-run` chose not to send, an installation with no key cannot.
            return (
                self._senza_scrivere(
                    DigestOutcome(slug, "inviato", iso, destinatari=len(destinatari))
                ),
                None,
            )

        sender = self.sender
        assert sender is not None  # answered above, before the report was built

        accettati = self._spedisci(sender, digest, destinatari)
        if not accettati:
            # Every address was refused. `EmailSender.send` never raises, so without this
            # the week would be recorded, tracked and `gia_inviato` forever on the
            # strength of mails that never arrived. Nothing is written, so next Monday --
            # or a rerun ten minutes later -- tries again.
            return (
                self._senza_scrivere(DigestOutcome(slug, "saltato", iso, motivo=INVIO_RIFIUTATO)),
                None,
            )

        indirizzi = [indirizzo for _, indirizzo in accettati]
        riga = self._registra(iso, indirizzi, riga_id)
        # Last touch on the session before the commit, as `ActivityService.record`'s own
        # contract requires: it flushes into this transaction and never commits.
        ActivityService(self.session).record(
            ENTITA,
            riga.id,
            KIND,
            attore,
            {"settimana": iso, "destinatari": len(accettati), "sezioni": sezioni},
        )
        self.session.commit()

        # The week is out and recorded. The caller reports it to PostHog from here.
        return (
            DigestOutcome(slug, "inviato", iso, destinatari=len(accettati)),
            _DaRiferire(tuple(accettati), sezioni),
        )

    def _spedisci(
        self,
        sender: EmailSender,
        digest: WeeklyDigest,
        destinatari: list[tuple[UUID, str]],
    ) -> list[tuple[UUID, str]]:
        """The recipients the provider took, in the order it was given them.

        `EmailSender.send` answers `False` rather than raising -- `ResendSender` returns it
        for any non-2xx and for a connection that never opened -- so a run that ignored the
        boolean would record, count and report addresses that were refused. Only what came
        back `True` goes into `inviato_a`, into `destinatari` and to PostHog.

        The sender is a parameter and not `self.sender`, so this method cannot be reached
        without one: «no Resend key» is `_invia`'s answer, decided once and above, and not
        a branch inside the loop that sends.
        """
        return [
            (user_id, indirizzo)
            for user_id, indirizzo in destinatari
            if sender.send(digest_mail(indirizzo, digest, public_url=self.public_url))
        ]

    # -- the reads, and the row -------------------------------------------------------

    def _senza_scrivere(self, esito: DigestOutcome) -> DigestOutcome:
        """An answer that wrote nothing, with the read transaction released.

        Every check above autobegins one, and the cron keeps this session for as long as
        the space takes. Rolling back here discards nothing -- nothing was written on any
        path that comes through this method -- and leaves the session where the caller
        handed it over.
        """
        self.session.rollback()
        return esito

    def _spazio_vuoto(self) -> bool:
        """§2: silence for a space with nothing in it.

        Four existence probes rather than the built report's own `vuoto`, because this is
        the question asked *before* building: a space that has never had a customer should
        cost one bounded query per table on a Monday morning, not seven sections and a
        snapshot. `LIMIT 1` on the primary key: the answer is whether there is a row, not
        how many.

        `deleted_at IS NULL` on every one of them. All four carry `SoftDeleteMixin` and
        core applies no global filter, so without it a space whose customer and deal were
        deleted last year is not empty and is mailed a report in which every section is
        empty -- which is the one mail §2 exists to prevent. It is also the condition the
        registers themselves read under, so the probe and the report agree about what
        exists.
        """
        return all(
            self.session.execute(
                select(model.id).where(model.deleted_at.is_(None)).limit(1)
            ).first()
            is None
            for model in (Customer, Deal, Invoice, TimeEntry)
        )

    def _riga_della_settimana(self, iso: str) -> UUID | None:
        return self.session.execute(
            select(Digest.id).where(Digest.settimana == iso)
        ).scalar_one_or_none()

    def _registra(self, iso: str, indirizzi: list[str], riga_id: UUID | None) -> Digest:
        """The week's row: created, or -- under `forza` -- moved.

        `digests.settimana` is unique, so a resend cannot be a second row: it updates the
        addresses and the instant, and the row goes on being the answer to «did this week
        go out, and to whom». `datetime.now(UTC)` is the same expression `occurred_at`'s
        column default uses, and it is an instant rather than a calendar day -- the thing
        `db/clock.py` bans is projecting `now()` onto a *date*, which nothing here does.
        """
        if riga_id is None:
            riga = Digest(settimana=iso, inviato_a=indirizzi, occurred_at=datetime.now(UTC))
            self.session.add(riga)
            self.session.flush()
            return riga
        riga = self.session.get_one(Digest, riga_id)
        riga.inviato_a = indirizzi
        riga.occurred_at = datetime.now(UTC)
        self.session.flush()
        return riga
