"""The team builder's daily cap (spec § 5, REB-512): how many proposals a day the public
page and the cloud may ask Claude for, together, before the next one answers 503.

Beta has no quota for a person (Ivan: no limits), and a script is not a person: the
concurrency cap (the API's semaphore, `rebase_api.deps.get_proposal_slots`) bounds how
much runs at once, and this bounds what a day can cost. The count is the
`team_proposals` rows of `pubblico` and `cloud` origin created since midnight in Rome,
where the day of the people running rebase begins; a row written with no call (an
empty catalogue, `team_builder.NO_CALL_MODEL`) cost nothing and is not counted, and nor
is an admin's own proposal, which no stranger can repeat.

It is a soft cap: two proposals that read the count at once may both go, and a day can
end a slot or two over it, never more than the concurrency cap allows.
"""

import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rebase_core.audit import utcnow
from rebase_core.config import Settings
from rebase_core.errors import TeamBuilderBusy
from rebase_core.models import TeamProposal
from rebase_core.team_builder import NO_CALL_MODEL

logger = logging.getLogger(__name__)

ROME = ZoneInfo("Europe/Rome")
# The one sentence both caps answer (spec § 3.2): a visitor does the same thing either way.
BUSY_SENTENCE = "Troppe richieste in questo momento: riprova tra un minuto."
# The origins a stranger can repeat: the public page and the cloud (spec § 5).
CAPPED_ORIGINS = ("pubblico", "cloud")


def rome_midnight(now: datetime) -> datetime:
    """The start of `now`'s day in Rome, as an aware datetime."""
    return datetime.combine(now.astimezone(ROME).date(), time.min, tzinfo=ROME)


def proposals_today(session: Session, *, now: datetime) -> int:
    """The proposals that count against the cap since midnight in Rome."""
    count = session.scalar(
        select(func.count())
        .select_from(TeamProposal)
        .where(
            TeamProposal.created_at >= rome_midnight(now),
            TeamProposal.origine.in_(CAPPED_ORIGINS),
            TeamProposal.model != NO_CALL_MODEL,
        )
    )
    return count or 0


def require_daily_room(
    session: Session, settings: Settings, *, now: datetime | None = None
) -> None:
    """`TeamBuilderBusy` once today's proposals reached `team_builder_daily_cap`."""
    cap = settings.team_builder_daily_cap
    if proposals_today(session, now=now or utcnow()) >= cap:
        logger.warning("team builder: the daily cap of %d proposals is reached", cap)
        raise TeamBuilderBusy(BUSY_SENTENCE)
