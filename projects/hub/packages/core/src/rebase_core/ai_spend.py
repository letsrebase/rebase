"""The task's daily euro cap as a ledger of settled calls and holds (HCORE-01, REB-889).

A call used to read what the task spent since midnight, call the model, and only then write
its usage row: any number of concurrent calls could read the same sum and all pass the cap.
Now a call **places a hold** before the network: under one advisory lock per task it sums
what is spent and held, refuses when the estimate does not fit, and otherwise inserts an
`ai_usage` row whose `cost_eur` is the estimate and whose `reserved_until` marks it as a
hold. After the answer the same row is settled to the real numbers (`AiConfigService.settle`,
which clears `reserved_until`); a call that fails releases it (the row is deleted); a hold
nobody settles (a crashed process) stops counting once `reserved_until` is past.

A hold is not a call. Everything that lists, counts or averages `ai_usage` rows keeps to
`settled()`; only the cap's sum, `spent_today`, counts `live()` holds too. A hold carries no
tokens and no `freelancer_id`, so a reader that asks about a person never meets one.

This module imports nothing of the rest of the AI code, so `ai_config.py` can build on it.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, delete, func, or_, select
from sqlalchemy.orm import Session

from rebase_core.models import AiUsage

ROME = ZoneInfo("Europe/Rome")

# How long an unsettled hold counts. The longest call is the anonymous CV's, 150 seconds with
# no retry (`llm.py`), and the others end near 81: five minutes is past every one of them and
# short enough that a crash does not hold the cap for long. A call that outlives it still
# settles (its row is found again, or written anew): only the hold stops counting.
HOLD_TTL = timedelta(minutes=5)

# A Message Batch is answered within 24 hours and its hold stays until its first answer is
# recorded (`AiBatch.record`); this is only the backstop, an hour past the provider's expiry.
BATCH_HOLD_TTL = timedelta(hours=25)


def rome_midnight(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(ROME).date(), time.min, tzinfo=ROME)


def settled() -> ColumnElement[bool]:
    """A row that is a call the provider answered, not a hold."""
    return AiUsage.reserved_until.is_(None)


def live(now: datetime) -> ColumnElement[bool]:
    """A row that counts against the cap: a settled call, or a hold still inside its TTL."""
    return or_(AiUsage.reserved_until.is_(None), AiUsage.reserved_until > now)


def spent_since_midnight(session: Session, task: str, now: datetime) -> Decimal:
    """What the task spent since midnight in Rome, every purpose together: the settled calls
    and the live holds. An unpriced call adds nothing (it has no cost)."""
    total = session.scalar(
        select(func.coalesce(func.sum(AiUsage.cost_eur), 0)).where(
            AiUsage.task == task, AiUsage.at >= rome_midnight(now), live(now)
        )
    )
    return Decimal(total or 0)


def place_hold(
    session: Session,
    *,
    task: str,
    purpose: str,
    provider: str,
    model: str,
    estimate: Decimal,
    cap: Decimal | None,
    now: datetime,
    ttl: timedelta = HOLD_TTL,
    batch: bool = False,
) -> UUID | None:
    """Reserves `estimate` against the task's cap and returns the hold's id, committed, or
    `None` when what is spent and held plus `estimate` would pass `cap`. The check and the
    insert are one transaction under `pg_advisory_xact_lock` on the task, so two callers
    cannot both see room for the one place left; the lock is released by the commit (or the
    rollback of a refusal). Holds past their TTL are deleted here, under the lock, so they do
    not pile up. `cap` `None` places the hold all the same: a task with no cap still shows
    what is in flight."""
    session.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"ai_cap:{task}"))))
    session.execute(
        delete(AiUsage).where(AiUsage.reserved_until.is_not(None), AiUsage.reserved_until <= now)
    )
    if cap is not None and spent_since_midnight(session, task, now) + estimate > cap:
        session.rollback()
        return None
    hold = AiUsage(
        at=now,
        task=task,
        purpose=purpose,
        provider=provider,
        model=model,
        cost_eur=estimate,
        priced=True,
        batch=batch,
        reserved_until=now + ttl,
    )
    session.add(hold)
    session.flush()
    hold_id = hold.id
    session.commit()
    return hold_id


def release_hold(session: Session, hold_id: UUID, *, commit: bool = True) -> None:
    """Gives a hold back (the call failed, or the batch's answers are in): the row is
    deleted, and committed unless `commit` is false, for a caller that releases it in the
    transaction that writes the real rows. A hold that is already gone, or already settled,
    is left."""
    session.execute(
        delete(AiUsage).where(AiUsage.id == hold_id, AiUsage.reserved_until.is_not(None))
    )
    if commit:
        session.commit()
