"""The scheduled jobs, what each last did, and how that reads (REB-764).

PigroCRM has no daemon and no queue: four host cron commands walk the root installation and
every space (`pigrocrm gmail-sync`, `digest`, `reminders`, `automations`). Each leaves one
row per space in `reminder_job_run`, the reminder pass's clock since 0054, written by
`JobClock` and read here, by `job_late` and by Impostazioni → Esecuzioni. There is no second
record: the table kept its name when the other three commands started writing it.

**A row says two things, kept apart.** `ran_at` is the last time the job ran to the end, and
it is what lateness is measured from; a job that ran once and fails on every run since goes
late, and does not look alive because it tried. `failed_at` and `failure` say the latest
attempt failed and why (a short word, never a message: a message can quote an address), and
the next success clears them. A job whose every attempt has failed has no time to measure
from: the panel says it failed, and `job_late`, which needs a time, waits for a success. A
command that cannot reach a space's database records nothing there, so a space it cannot
walk reads as late, which is the true thing.

**Nothing is invented.** A job with no row has never run in this space, and that is said
(`never_run`), not shown as a time. A rehearsal (`--dry-run`) records nothing, like every
other write of a pass. The one job that depends on the space's configuration is the mailbox
sync: the command prints nothing for a space with no connected mailbox, so there is nothing
to record and the row reads `not_configured` instead of `never_run`.

Mastro's `/settings` answered the same question with `ok`, `failure`, `stale` and
`never_run` for its backup, mail polling and extraction runner; those are the states here,
under the names `ok`, `failed`, `late` and `never_run`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.gmail.models import GoogleAccount
from pigrocrm.core.reminders.models import ReminderJobRun

GMAIL_SYNC_JOB: Final = "gmail-sync"
DIGEST_JOB: Final = "digest"
REMINDER_PASS_JOB: Final = "reminders"
AUTOMATIONS_JOB: Final = "automations"

FAILURE_MAX_LENGTH: Final = 200

JobState = Literal["ok", "failed", "late", "never_run", "not_configured"]


@dataclass(frozen=True)
class ScheduledJob:
    """One cron command, as the product names it.

    `stale_after_minutes` is the point at which silence is a fault: the period of the line
    in the runbook plus the slack its own runbook gives it, so one slow run is not an alarm
    and one missed run is. `late_label` is the sentence's subject in the `job_late`
    reminder, `None` for a job that reminder does not watch (the sync: a stopped mailbox is
    `connection_failing`'s, which reads the same moment with the mailbox's own name, and
    two reminders about one cause would be noise)."""

    key: str
    label: str
    command: str
    schedule: str
    stale_after_minutes: int
    late_label: str | None = None


# In the order the panel lists them: the three that read and write mail and data, then the
# one that only reads. Each runbook is `docs/superpowers/notes/`.
SCHEDULED_JOBS: Final[tuple[ScheduledJob, ...]] = (
    ScheduledJob(
        GMAIL_SYNC_JOB,
        "Sincronizzazione Gmail",
        "pigrocrm gmail-sync",
        "ogni 15 minuti",
        stale_after_minutes=30,
    ),
    ScheduledJob(
        REMINDER_PASS_JOB,
        "Passaggio dei promemoria",
        "pigrocrm reminders",
        "ogni 15 minuti",
        stale_after_minutes=30,
        late_label="Il passaggio dei promemoria",
    ),
    ScheduledJob(
        AUTOMATIONS_JOB,
        "Automazioni",
        "pigrocrm automations",
        "ogni giorno alle 6",
        # A daily line and two hours of slack for a slow host.
        stale_after_minutes=26 * 60,
        late_label="Il passaggio delle automazioni",
    ),
    ScheduledJob(
        DIGEST_JOB,
        "Riepilogo settimanale",
        "pigrocrm digest",
        "ogni lunedì alle 8",
        # A weekly line and a day of slack: Tuesday morning without a Monday run is late.
        stale_after_minutes=8 * 24 * 60,
        late_label="L'invio del riepilogo settimanale",
    ),
)

JOBS_BY_KEY: Final = {job.key: job for job in SCHEDULED_JOBS}


class ScheduledJobRead(BaseModel):
    key: str = Field(description="Il nome del comando: `gmail-sync`, `reminders`, ...")
    label: str = Field(description="Come si chiama nell'app")
    command: str = Field(description="La riga che il cron lancia")
    schedule: str = Field(description="Ogni quanto deve girare")
    state: JobState = Field(
        description=(
            "`ok`: l'ultimo giro è finito e non è vecchio. `failed`: l'ultimo tentativo è "
            "fallito (`failure` dice perché). `late`: nessun giro finito da troppo tempo. "
            "`never_run`: non ha mai girato in questo spazio. `not_configured`: non c'è "
            "niente da fare qui, per esempio nessuna casella Gmail collegata."
        )
    )
    last_run_at: datetime | None = Field(
        description="Quando l'ultimo giro è finito senza errori, `null` se non è mai successo"
    )
    last_failure_at: datetime | None = Field(
        description="Quando è fallito l'ultimo tentativo, solo se è anche l'ultimo"
    )
    failure: str | None = Field(
        description=(
            "Perché è fallito: una parola breve, per esempio il nome dell'errore, mai un testo "
            "che cita dati"
        )
    )
    stale_after_minutes: int = Field(
        description="Dopo quanti minuti senza un giro finito un'operazione risulta in ritardo"
    )


class ScheduledJobsRead(BaseModel):
    jobs: list[ScheduledJobRead]


def failure_text(detail: str) -> str:
    """What is stored of a failure: bounded, one line."""
    return " ".join(detail.split())[:FAILURE_MAX_LENGTH]


class JobClock:
    """Writes a job's clock. The commands record per space, in a transaction of their own
    (`gmail-sync`, `digest`, `automations`) or in the reminder pass's own one, which is why
    nothing here commits."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def succeeded(self, job: str, at: datetime | None = None) -> None:
        """The job ran to the end: its time moves and a failure it had is over."""
        moment = at or datetime.now(UTC)
        statement = insert(ReminderJobRun).values(job=job, ran_at=moment)
        self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[ReminderJobRun.job],
                set_={
                    "ran_at": statement.excluded.ran_at,
                    "failed_at": None,
                    "failure": None,
                    "updated_at": func.now(),
                },
            )
        )

    def failed(self, job: str, detail: str, at: datetime | None = None) -> None:
        """The job's attempt failed. `ran_at` is not touched: the last success is still
        the last success, and lateness is measured from it."""
        moment = at or datetime.now(UTC)
        statement = insert(ReminderJobRun).values(
            job=job, failed_at=moment, failure=failure_text(detail)
        )
        self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[ReminderJobRun.job],
                set_={
                    "failed_at": statement.excluded.failed_at,
                    "failure": statement.excluded.failure,
                    "updated_at": func.now(),
                },
            )
        )


def job_state(
    row: ReminderJobRun | None, job: ScheduledJob, now: datetime, *, configured: bool = True
) -> JobState:
    """The state of one job from its row. A failure outranks lateness (it says more), and
    lateness outranks `ok`; a job that has nothing to do here is `not_configured` before
    anything else, even if a row from a mailbox since disconnected is still there."""
    if not configured:
        return "not_configured"
    if row is None:
        return "never_run"
    if row.failed_at is not None:
        return "failed"
    if row.ran_at is None:
        return "never_run"
    if (now - row.ran_at).total_seconds() >= job.stale_after_minutes * 60:
        return "late"
    return "ok"


class ScheduledJobService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def describe(self, actor: Actor, *, now: datetime | None = None) -> ScheduledJobsRead:
        """The four jobs of this space and how each last went. An admin's: which jobs a
        space's cron runs, and what failed, is how the space is run."""
        actor.require_admin("read_scheduled_jobs")
        moment = now or datetime.now(UTC)
        rows = {row.job: row for row in self.session.scalars(select(ReminderJobRun))}
        mailbox = (
            self.session.scalar(
                select(GoogleAccount.id).where(GoogleAccount.status != "disconnected").limit(1)
            )
            is not None
        )
        jobs: list[ScheduledJobRead] = []
        for job in SCHEDULED_JOBS:
            row = rows.get(job.key)
            state = job_state(row, job, moment, configured=mailbox or job.key != GMAIL_SYNC_JOB)
            failed = row is not None and row.failed_at is not None
            jobs.append(
                ScheduledJobRead(
                    key=job.key,
                    label=job.label,
                    command=job.command,
                    schedule=job.schedule,
                    state=state,
                    last_run_at=row.ran_at if row is not None else None,
                    last_failure_at=row.failed_at if failed and row is not None else None,
                    failure=row.failure if failed and row is not None else None,
                    stale_after_minutes=job.stale_after_minutes,
                )
            )
        return ScheduledJobsRead(jobs=jobs)
