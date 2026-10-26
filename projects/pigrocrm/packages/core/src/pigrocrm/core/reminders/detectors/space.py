"""The space-wide group, part one: `month_not_closed`, `proposal_pending`,
`connection_failing` and `job_late` (spec 2026-10-26 §3.5). The two fiscal types of the same
section are `space_fiscal.py`.

**Recipients are admins and nobody else** (§3.5, §4): `Directory.admins()`, and never the
space's owner as a fallback, so a space with no admin who may read the subject raises
nothing. A scoped admin is the one question the card leaves open, and it is decided the way
the rest of the product decides it: a subject of one azienda goes to the admins whose scope
covers it (`Person.sees`, the rule of §4), and a subject of the space as a whole, which
has no azienda, goes to the unscoped admins, which is exactly who may close a period
(`PeriodLockService.close_period` requires an unscoped admin) and who may read a connection
that belongs to nobody's azienda. `admins_seeing` is that decision, written once.

**Nothing here is stored and nothing is invented.** A job can be late only if something
records when it last ran: only the reminder pass does, in `reminder_job_run`. The mailbox
sync leaves `GoogleAccount.last_sync_at`, which `connection_failing` reads. The weekly
digest is not read: it writes a row only
when mail actually left (`digest/run.py`, «nothing is recorded that did not happen»), so the
absence of one is a quiet week or a provider that is not configured as often as it is a late
cron, and an alert that is wrong in a space that has nothing to report would teach admins to
ignore it. The automations are not scheduled at all: they run inside the request that
triggers them (`automations/runner.py`).
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from typing import Final
from uuid import NAMESPACE_DNS, UUID, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import select

from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.drive.models import GoogleDriveAccount
from pigrocrm.core.gmail.models import GoogleAccount
from pigrocrm.core.gmail.schemas import SCOPE_READONLY
from pigrocrm.core.proposals.models import Proposal
from pigrocrm.core.reminders.catalogue import LADDER_MAX, REMINDER_TYPES
from pigrocrm.core.reminders.detect import Detection, Detector, DetectorContext
from pigrocrm.core.reminders.models import ReminderJobRun
from pigrocrm.core.timetracking.locks import period_label
from pigrocrm.core.timetracking.models import PeriodLock

NOT_CLOSED = REMINDER_TYPES["month_not_closed"]
PENDING = REMINDER_TYPES["proposal_pending"]
FAILING = REMINDER_TYPES["connection_failing"]
LATE = REMINDER_TYPES["job_late"]


def _count(number: int, one: str, many: str) -> str:
    """`1 giorno`, `2 giorni`: the number with the noun in the right number, so a detail
    never reads «da 1 giorni»."""
    return f"{number} {one if number == 1 else many}"


def admins_seeing(ctx: DetectorContext, azienda_id: UUID | None) -> frozenset[UUID]:
    """The admins who may be told about a subject of this azienda, or of the space as a
    whole when `azienda_id` is `None` (only an unscoped admin reads that). No fallback to
    the space's owner: an empty set raises nothing."""
    directory = ctx.directory
    return frozenset(
        user_id for user_id in directory.admins() if directory.people[user_id].sees(azienda_id)
    )


# -- month_not_closed ---------------------------------------------------------------------


def period_subject(anno: int, mese: int) -> UUID:
    """The subject id of a month. `period_locks` has no UUID key (`(anno, mese)` is its
    identity), a reminder's key needs one, and the web opens a month by its number: so the
    id carries the month, `anno * 100 + mese` as the integer of the UUID, and
    `subject_period` reads it back."""
    return UUID(int=anno * 100 + mese)


def subject_period(subject_id: UUID) -> tuple[int, int]:
    anno, mese = divmod(subject_id.int, 100)
    return anno, mese


def _closes_periods(ctx: DetectorContext) -> bool:
    """«The space closes periods»: somebody has closed one. Closing is never mandatory
    (`timetracking/locks.py`), so a space that never did gets nothing."""
    return ctx.session.scalar(select(PeriodLock.anno).limit(1)) is not None


def _months_from(first: tuple[int, int]) -> Iterable[tuple[int, int]]:
    anno, mese = first
    while True:
        yield anno, mese
        anno, mese = (anno + 1, 1) if mese == 12 else (anno, mese + 1)


def detect_not_closed(ctx: DetectorContext) -> list[Detection]:
    """Every month from the first one the space closed to the last that ended `bound`
    days ago, which has no lock. A month before the first lock is not counted: the space
    began closing later, and the months it never closed are not a ritual it skipped."""
    closed = {(row.anno, row.mese) for row in ctx.session.execute(select(PeriodLock)).scalars()}
    if not closed:
        return []
    recipients = admins_seeing(ctx, None)
    cutoff = ctx.today - timedelta(days=max(ctx.bound(NOT_CLOSED), 1))
    found: list[Detection] = []
    for anno, mese in _months_from(min(closed)):
        last_day = date(anno, mese, monthrange(anno, mese)[1])
        if last_day > cutoff:
            break
        if (anno, mese) in closed or not recipients:
            continue
        days = (ctx.today - last_day).days
        subject = period_subject(anno, mese)
        found.append(
            Detection(
                type="month_not_closed",
                subject_id=subject,
                measure=float(days),
                recipients=recipients,
                title=f"Mese non chiuso: {period_label(anno, mese)}",
                detail=(
                    f"{period_label(anno, mese).capitalize()} è finito da "
                    f"{_count(days, 'giorno', 'giorni')} e non è "
                    "stato chiuso: finché è aperto, ore e costi del mese possono ancora cambiare"
                ),
                entity="period",
                entity_id=subject,
            )
        )
    return found


# -- proposal_pending ---------------------------------------------------------------------

# One reminder per proposal, oldest first, and no more than this many of them: the queue
# behind the hundredth is younger than it, so it is never the more severe, and a pass that
# listed a thousand would bury the list it exists to keep short.
MAX_PROPOSALS: Final = 100

_WHAT = {"contratto": "La bozza di contratto", "giornata": "La giornata proposta"}


def detect_pending(ctx: DetectorContext) -> list[Detection]:
    """Agent proposals still `in_attesa` that were created `bound` days ago or earlier,
    counted in the installation's days, oldest first."""
    zone = ZoneInfo(ctx.timezone)
    cutoff_day = ctx.today - timedelta(days=max(ctx.bound(PENDING), 1))
    cutoff = datetime.combine(cutoff_day + timedelta(days=1), time.min, tzinfo=zone)
    rows = ctx.session.execute(
        select(Proposal, Contract.titolo, Contract.azienda_id, Contract.customer_id)
        .outerjoin(Contract, Contract.id == Proposal.contract_id)
        .where(Proposal.stato == "in_attesa", Proposal.created_at < cutoff)
        .order_by(Proposal.created_at, Proposal.id)
        .limit(MAX_PROPOSALS)
    )
    found: list[Detection] = []
    for proposal, contract_title, azienda_id, customer_id in rows:
        recipients = admins_seeing(ctx, azienda_id)
        if not recipients:
            continue
        days = (ctx.today - proposal.created_at.astimezone(zone).date()).days
        what = _WHAT.get(proposal.target_type, "La proposta")
        about = f" · {contract_title}" if contract_title else ""
        found.append(
            Detection(
                type="proposal_pending",
                subject_id=proposal.id,
                measure=float(days),
                recipients=recipients,
                title="Proposta in attesa di una decisione",
                detail=(
                    f"{what} aspetta da {_count(days, 'giorno', 'giorni')}: accettala o "
                    f"rifiutala{about}"
                ),
                entity="proposal",
                entity_id=proposal.id,
                azienda_id=azienda_id,
                customer_id=customer_id,
            )
        )
    return found


# -- connection_failing -------------------------------------------------------------------

# A credential Google refused, or that lapsed, is broken whatever the clock says. The
# ladder reads hours, and its critical rung is `LADDER_MAX`, the largest number a ladder
# may hold (the catalogue row says so): no valid rung is above it, so a broken
# credential's measure reaches every rung of every ladder, an edited one included, and
# is critical for anybody who has the type on, while a merely stale sync keeps its honest
# number of hours and stops at serious.
BROKEN: Final = float(LADDER_MAX)
_BROKEN_STATUSES = ("revoked", "expired")


def _connected(ctx: DetectorContext) -> bool:
    """«The space connected it»: a Gmail or Drive row that was not disconnected on purpose."""
    session = ctx.session
    return (
        session.scalar(
            select(GoogleAccount.id).where(GoogleAccount.status != "disconnected").limit(1)
        )
        is not None
        or session.scalar(
            select(GoogleDriveAccount.id)
            .where(GoogleDriveAccount.status != "disconnected")
            .limit(1)
        )
        is not None
    )


def _sync_baseline(account: GoogleAccount) -> datetime:
    """When this mailbox last proved it works: its last sync, or when it was connected if
    it has not synchronised yet (a connection that never ran is as stale as it is old)."""
    moments = [m for m in (account.last_sync_at, account.connected_at) if m is not None]
    return max(moments)


def _hours(since: datetime, now: datetime) -> int:
    return max(int((now - since).total_seconds() // 3600), 0)


def detect_failing(ctx: DetectorContext) -> list[Detection]:
    """Gmail: a revoked or expired credential, or a sync that last worked `bound` hours
    ago or more. Drive: a revoked or expired credential; Drive is read when somebody asks
    and has no clock of its own, so it cannot be stale."""
    recipients = admins_seeing(ctx, None)
    if not recipients:
        return []
    floor = max(ctx.bound(FAILING), 1)
    found: list[Detection] = []
    for account in ctx.session.scalars(
        select(GoogleAccount)
        .where(GoogleAccount.status != "disconnected")
        .order_by(GoogleAccount.connected_at, GoogleAccount.id)
    ):
        email = account.email_address
        if account.status in _BROKEN_STATUSES:
            measure = BROKEN
            title = "Gmail scollegato: il consenso Google non vale più"
            detail = (
                f"Il consenso Google per {email} è "
                + ("stato revocato" if account.status == "revoked" else "scaduto")
                + ": la sincronizzazione e l'invio sono fermi finché non ricolleghi la casella"
            )
        elif SCOPE_READONLY in account.scopes_granted:
            hours = _hours(_sync_baseline(account), ctx.now)
            if hours < floor:
                continue
            measure = float(hours)
            title = f"Gmail non si sincronizza da {_count(hours, 'ora', 'ore')}"
            detail = (
                f"L'ultima sincronizzazione della casella {email} è di "
                f"{_count(hours, 'ora', 'ore')} fa"
            )
        else:
            # Without `gmail.readonly` the sync is switched off and says so in its own
            # banner; there is nothing here that failed.
            continue
        found.append(
            Detection(
                type="connection_failing",
                subject_id=account.id,
                measure=measure,
                recipients=recipients,
                title=title,
                detail=detail,
                entity="google_connection",
                entity_id=account.id,
            )
        )
    for drive in ctx.session.scalars(
        select(GoogleDriveAccount)
        .where(GoogleDriveAccount.status.in_(_BROKEN_STATUSES))
        .order_by(GoogleDriveAccount.connected_at, GoogleDriveAccount.id)
    ):
        found.append(
            Detection(
                type="connection_failing",
                subject_id=drive.id,
                measure=BROKEN,
                recipients=recipients,
                title="Drive scollegato: il consenso Google non vale più",
                detail=(
                    f"Il consenso Google per {drive.email_address} è "
                    + ("stato revocato" if drive.status == "revoked" else "scaduto")
                    + ": i file di Drive non sono raggiungibili finché non ricolleghi Drive"
                ),
                entity="google_connection",
                entity_id=drive.id,
            )
        )
    return found


# -- job_late -----------------------------------------------------------------------------

# Every job of the crontab runs every quarter of an hour (`docs/superpowers/notes`), so
# the ladder's `serious` rung, 30 minutes, is «later than twice its interval».
JOB_INTERVAL_MINUTES: Final = 15
REMINDER_PASS_JOB: Final = "reminders"

# The jobs this type reads, by the name they have in `reminder_job_run`. Only the reminder
# pass is here, and that is the whole answer to «which jobs may reach `critical`»: the
# ladder's critical rung is the pass's own (spec §3.5: «when the reminder pass itself has
# not run for 3 hours»), so a job added here shares it and one that must stay under it
# needs a type of its own. The mailbox sync is deliberately not here: a sync that stopped
# is `connection_failing`'s, which reads the same `last_sync_at` with the account's name
# and its own ladder, and two reminders about one cause would be noise.
_JOB_LABELS = {REMINDER_PASS_JOB: "Il passaggio dei promemoria"}


def job_subject(job: str) -> UUID:
    """A job has no id of its own (`reminder_job_run` is keyed by name), so its subject is
    one derived from the name."""
    return uuid5(NAMESPACE_DNS, f"{job}.job.pigrocrm")


def _last_runs(ctx: DetectorContext) -> dict[str, datetime]:
    """When each job we can read last ran. The reminder pass writes its own clock at the
    end of a pass (`ReminderLedger.touch_job`), after the detectors have read, so a pass
    sees the previous one. A job nothing records is not in the answer, and is therefore
    never late: no row is invented."""
    return {
        row.job: row.ran_at
        for row in ctx.session.scalars(
            select(ReminderJobRun).where(ReminderJobRun.job.in_(_JOB_LABELS))
        )
    }


def detect_late(ctx: DetectorContext) -> list[Detection]:
    """Jobs whose last run is `bound` minutes old or more."""
    recipients = admins_seeing(ctx, None)
    if not recipients:
        return []
    floor = max(ctx.bound(LATE), 1)
    found: list[Detection] = []
    for job, ran_at in sorted(_last_runs(ctx).items()):
        minutes = max(int((ctx.now - ran_at).total_seconds() // 60), 0)
        if minutes < floor:
            continue
        subject = job_subject(job)
        found.append(
            Detection(
                type="job_late",
                subject_id=subject,
                measure=float(minutes),
                recipients=recipients,
                title=f"{_JOB_LABELS[job]} è in ritardo",
                detail=(
                    f"{_JOB_LABELS[job]} non parte da {_duration(minutes)}: dovrebbe girare "
                    f"ogni {JOB_INTERVAL_MINUTES} minuti"
                ),
                entity="job",
                entity_id=subject,
            )
        )
    return found


def _duration(minutes: int) -> str:
    if minutes < 120:
        return _count(minutes, "minuto", "minuti")
    return _count(minutes // 60, "ora", "ore")


DETECTORS = (
    Detector(NOT_CLOSED, detect_not_closed, _closes_periods),
    Detector(PENDING, detect_pending),
    Detector(FAILING, detect_failing, _connected),
    Detector(LATE, detect_late),
)
