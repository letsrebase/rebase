"""What a sent mail led to (spec § 6.2): when the person entered their area and when they
did the campaign's action, each stamped once on the recipient row. The tick calls
`stamp_outcomes` after sending. The campaign page and the MCP tools read the stamped
columns and run no action query of their own.

Only rows sent in the last `STAMP_WINDOW` are read, and only the ones still missing a
stamp, so a pass costs a few queries per open row, not per row ever sent. A stamp is
written once and never moved: a second login does not turn «entrato il 26/09» into the
28th. Each row is stamped in its own savepoint (the same isolation principle as the send
loop's R14): a row whose own data makes `done_at` raise (a broken `prima["richieste"]`
timestamp, say) is rolled back to the savepoint, logged by id and exception type only,
and skipped, so it never wedges every other row's stamp for the rest of the pass -- or
every pass after it, since a wedged row would otherwise stay in the 30-day window
forever."""

import logging
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from rebase_core.campaigns.actions import done_at, entered_at
from rebase_core.models import Campaign, CampaignRecipient

STAMP_WINDOW = timedelta(days=30)

_log = logging.getLogger(__name__)


def stamp_outcomes(session: Session, *, now: datetime) -> int:
    """Stamps every open sent row once, commits, and answers how many rows got a new
    stamp. `now` is the moment a card seen complete is stamped with. A row whose own
    stamping raises is rolled back and skipped, and does not count in the return."""
    pending = session.execute(
        select(CampaignRecipient, Campaign.azione)
        .join(Campaign, Campaign.id == CampaignRecipient.campaign_id)
        .where(
            CampaignRecipient.stato == "inviata",
            CampaignRecipient.inviata_at >= now - STAMP_WINDOW,
            or_(CampaignRecipient.entrato_at.is_(None), CampaignRecipient.azione_at.is_(None)),
        )
        .order_by(CampaignRecipient.inviata_at, CampaignRecipient.id)
    ).all()
    stamped = 0
    for row, azione in pending:
        t0 = row.inviata_at
        if t0 is None:  # excluded by the query; here for the type checker
            continue
        try:
            with session.begin_nested():
                changed = False
                if row.entrato_at is None:
                    entered = entered_at(session, row, since=t0)
                    if entered is not None:
                        row.entrato_at, changed = entered, True
                if row.azione_at is None:
                    done = (
                        row.entrato_at
                        if azione == "entrato"
                        else done_at(session, row, azione, since=t0, now=now)
                    )
                    if done is not None:
                        row.azione_at, changed = done, True
                if changed:
                    stamped += 1
        except Exception as exc:  # one bad row must not wedge the rest (R14)
            _log.error("outcome of recipient %s skipped this tick: %s", row.id, type(exc).__name__)
    session.commit()
    return stamped
