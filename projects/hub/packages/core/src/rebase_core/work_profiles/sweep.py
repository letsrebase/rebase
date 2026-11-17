"""The retention of spec 2026-11-17 § 8: a profile is kept at most 12 months from its submission,
and a device authorization a day after it expired or was spent."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Result, delete, or_
from sqlalchemy.orm import Session

from rebase_core.audit import utcnow
from rebase_core.models import CliAuthorization, WorkProfile

AUTHORIZATION_GRACE = timedelta(days=1)


@dataclass(frozen=True)
class SweepCounts:
    profiles: int
    authorizations: int


def _count(result: Result[Any]) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


def sweep(session: Session, now: Callable[[], datetime] = utcnow) -> SweepCounts:
    moment = now()
    profiles = session.execute(delete(WorkProfile).where(WorkProfile.expires_at <= moment))
    cutoff = moment - AUTHORIZATION_GRACE
    authorizations = session.execute(
        delete(CliAuthorization).where(
            or_(CliAuthorization.expires_at <= cutoff, CliAuthorization.spent_at <= cutoff)
        )
    )
    session.commit()
    return SweepCounts(_count(profiles), _count(authorizations))
