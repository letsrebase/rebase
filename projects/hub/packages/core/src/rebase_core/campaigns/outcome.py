"""What a sent mail led to (spec § 6.2): when the person entered their area and when they
did the campaign's action, each stamped once on the recipient row. The tick calls
`stamp_outcomes` after sending. The campaign page and the MCP tools read the stamped
columns and run no action query of their own.

Only rows sent in the last `STAMP_WINDOW` are read, and only the ones still missing a
stamp, so a pass costs a few queries per open row, not per row ever sent. A stamp is
written once and never moved: a second login does not turn «entrato il 26/09» into the
28th. The entry stamp and the action stamp each get their own savepoint (the same
isolation principle as the send loop's R14): a row whose own data makes `done_at` raise
(a broken `prima["richieste"]` timestamp, say) loses only the action savepoint, not an
`entrato_at` already found and committed a moment earlier in the row's own entry
savepoint. Either failure is logged by id and exception type only and skipped, so it
never wedges every other row's stamp for the rest of the pass -- or every pass after it,
since a wedged row would otherwise stay in the 30-day window forever.

The row scan first narrows to recent campaigns' ids (Greptile P2: `campaign_recipients`
has no index on `stato`/`inviata_at` alone, and a migration is out of scope here). A
mail is never sent before its campaign's `programmata_per`, so restricting to campaigns
whose `programmata_per` falls in the same window cannot drop a row the unrestricted
scan would have stamped."""

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
    campaign_ids = list(
        session.scalars(
            select(Campaign.id).where(
                Campaign.stato.in_(("in_invio", "inviata", "annullata")),
                Campaign.programmata_per >= now - STAMP_WINDOW,
            )
        )
    )
    if not campaign_ids:
        return 0
    pending = session.execute(
        select(CampaignRecipient, Campaign.azione)
        .join(Campaign, Campaign.id == CampaignRecipient.campaign_id)
        .where(
            CampaignRecipient.campaign_id.in_(campaign_ids),
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
        # Each flag is set only once its own `with` block has exited without raising:
        # setting it from inside the block, on the same line as the attribute, counted
        # a row whose savepoint itself then failed to commit or release -- a DB error
        # writing the stamp, not `entered_at`/`done_at`'s own check -- since the
        # attribute assignment had already run (CodeRabbit's adversarial pass, item 6).
        kept_entry = False
        kept_action = False
        if row.entrato_at is None:
            try:
                with session.begin_nested():
                    entered = entered_at(session, row, since=t0)
                    if entered is not None:
                        row.entrato_at = entered
                kept_entry = entered is not None
            except Exception as exc:  # one bad row must not wedge the rest (R14)
                _log.error(
                    "outcome of recipient %s skipped this tick: %s", row.id, type(exc).__name__
                )
        if row.azione_at is None:
            try:
                with session.begin_nested():
                    done = (
                        row.entrato_at
                        if azione == "entrato"
                        else done_at(session, row, azione, since=t0, now=now)
                    )
                    if done is not None:
                        row.azione_at = done
                kept_action = done is not None
            except Exception as exc:  # one bad row must not wedge the rest (R14)
                _log.error(
                    "outcome of recipient %s skipped this tick: %s", row.id, type(exc).__name__
                )
        if kept_entry or kept_action:
            stamped += 1
    session.commit()
    return stamped
