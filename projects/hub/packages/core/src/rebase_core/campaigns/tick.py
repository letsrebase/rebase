"""One pass of the `campaigns` loop (spec § 5.4): every campaign whose time has come, or
that a previous pass left `in_invio`, sent one recipient at a time with the send-time
checks of § 5.3. A session advisory lock keeps two passes from ever running together.

Controller ruling (R12/R13): the campaigns a first, unlocked `SELECT` finds are only
ids, never held onto as ORM objects across the loop. Each one is re-read and locked
(`FOR UPDATE SKIP LOCKED`) right before it is claimed, so a row someone else is already
holding is skipped this pass rather than raced, and a row this pass finds no longer
due (moved back to `bozza`, already claimed and moving, or something else entirely) is
left alone. Claiming commits immediately, releasing the campaign's own lock for the
rest of the send: the campaign is never locked for the whole run, only for the moment
that flips it to `in_invio`, so a `cancel()` from elsewhere can land between two of its
rows. The final transition back out of `in_invio` re-reads and re-locks the campaign
once more and only moves it to `inviata` if it is still there: a campaign a `cancel()`
already moved to `annullata` stays there.

The session advisory lock (`pg_try_advisory_lock`/`pg_advisory_unlock`) is a per
*connection* lock, but `session` commits many times over a pass (once per claim, once
per row); each commit lets the ORM session's connection go back to the pool, and its
next statement may check out a different one. Taking and releasing the lock through
`session` itself would therefore risk locking on one physical connection and
unlocking on another, leaking the lock onto whichever connection went back to the
pool still holding it. So the lock lives on its own `Connection`, checked out once
from the same engine `session` is bound to and held for the whole pass, independent of
whatever `session` does with its own connections.

Controller ruling R14: nothing else isolates an exception per campaign or per row, so
one bad row or one bad campaign must not be able to wedge every campaign after it,
forever. Two backstops, both narrow on purpose: they catch what preparing a mail can
raise on data this pass didn't choose (a `pigro` button phase 1 refuses, a recipient's
own broken `prima` snapshot, corrupt stored `filtri`), not bugs in the loop itself:
a row whose checks or `render` raise is marked `fallita` with a short, address-free
reason and the row loop moves on; a campaign whose own work raises outside a row (e.g.
`candidates`) rolls that campaign's work back, is logged by id and
exception type only, and is left `in_invio` for a later pass while the loop moves on
to the next due campaign. The advisory lock is released in every case regardless,
since it is taken and released around the whole pass, outside both of these."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import Session

from rebase_core.campaigns.actions import done_at
from rebase_core.campaigns.audience import REASON_DONE, REASON_NOT_LISTED, candidates, exclusions
from rebase_core.campaigns.render import RenderTarget, render
from rebase_core.campaigns.sender import CampaignSender
from rebase_core.config import Settings
from rebase_core.models import Campaign, CampaignRecipient

MAX_ATTEMPTS = 3
# Resend's default limit is two requests a second for the whole team, and the magic
# link and the member mails share it: a campaign takes one, leaving the other.
SEND_INTERVAL_SECONDS = 1.0
TICK_LOCK_KEY = 0x72656261  # "reba"
ROW_PREPARE_ERROR = "errore nel preparare la mail"

_log = logging.getLogger(__name__)


@dataclass
class TickResult:
    campagne: int = 0
    inviate: int = 0
    saltate: int = 0
    fallite: int = 0


def run_tick(
    session: Session,
    sender: CampaignSender,
    settings: Settings,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    pause: Callable[[float], None] = time.sleep,
) -> TickResult:
    result = TickResult()
    with session.get_bind().connect() as lock_conn:  # type: ignore[union-attr]
        got_lock = lock_conn.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": TICK_LOCK_KEY}
        ).scalar()
        if not got_lock:
            return result
        try:
            due_ids = session.scalars(
                select(Campaign.id)
                .where(
                    or_(
                        and_(Campaign.stato == "programmata", Campaign.programmata_per <= clock()),
                        Campaign.stato == "in_invio",
                    )
                )
                .order_by(Campaign.programmata_per, Campaign.id)
            ).all()
            for campaign_id in due_ids:
                campaign = _claim(session, campaign_id, clock())
                if campaign is None:
                    continue
                result.campagne += 1
                try:
                    _send(session, campaign, sender, settings, clock, pause, result)
                except Exception as exc:  # one bad campaign must not wedge the rest (R14)
                    session.rollback()
                    _log.error("campaign %s failed this tick: %s", campaign_id, type(exc).__name__)
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": TICK_LOCK_KEY})
            lock_conn.commit()
    return result


def _claim(session: Session, campaign_id: UUID, now: datetime) -> Campaign | None:
    """Re-reads and locks one campaign the unlocked scan above found due. Returns it,
    moved to `in_invio` and the move already committed (releasing the lock), only if it
    is still claimable under the lock; otherwise releases the lock and returns `None`
    without touching the row: someone else has it (`SKIP LOCKED` found nothing), or it
    moved on since the scan (back to `bozza`, cancelled, already `in_invio`).

    `populate_existing=True`: this session may already hold this campaign in its
    identity map (`expire_on_commit=False`, and a pass touches several rows without
    ever expiring the objects it already has). Without it, a `Campaign` this session
    saw before would keep its old, in-memory `stato` instead of the one this very
    `SELECT ... FOR UPDATE` just locked and read, the one write this function is about
    to gate on."""
    campaign = session.scalars(
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    ).first()
    if campaign is None:
        return None
    if campaign.stato == "in_invio":
        session.commit()
        return campaign
    due = (
        campaign.stato == "programmata"
        and campaign.programmata_per is not None
        and campaign.programmata_per <= now
    )
    if due:
        campaign.stato = "in_invio"
        session.commit()
        return campaign
    session.commit()  # release the lock: nothing to claim
    return None


def _send(
    session: Session,
    campaign: Campaign,
    sender: CampaignSender,
    settings: Settings,
    clock: Callable[[], datetime],
    pause: Callable[[float], None],
    result: TickResult,
) -> None:
    # Read once per pass: rebuilding a state's list is a scan of every card. Who opted
    # out, bounced or was reached by another campaign is read again per row, below.
    members = (
        {c.email for c in candidates(session, campaign)}
        if campaign.fonte in ("stato", "filtri")
        else None
    )
    while True:
        row = session.scalars(
            select(CampaignRecipient)
            .where(
                CampaignRecipient.campaign_id == campaign.id, CampaignRecipient.stato == "in_coda"
            )
            .order_by(CampaignRecipient.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        if row is None:
            break
        try:
            # Right before this mail, not once per pass (spec § 5.3): a pass sends one
            # mail a second, so an opt-out, a «Non scrivere mai» or a bounce recorded
            # while it runs must stop the rows still queued behind it.
            reason = exclusions(
                session,
                [row.email],
                campaign_id=campaign.id,
                now=clock(),
                gap_days=settings.campaign_gap_days,
            ).get(row.email)
            if reason is None and done_at(session, row, campaign.azione) is not None:
                reason = REASON_DONE
            if reason is None and members is not None and row.email not in members:
                reason = REASON_NOT_LISTED
            if reason is not None:
                row.stato, row.motivo = "saltata", reason
                result.saltate += 1
                session.commit()
                continue
            target = RenderTarget(
                email=row.email,
                nome=row.nome,
                codice=row.codice,
                token=row.disiscrizione_token,
                recipient_id=str(row.id),
            )
            rendered = render(campaign, target, settings)
        except Exception:  # one bad row must not stop the rest of the send (R14)
            # Data this pass didn't choose (a `pigro` button phase 1 refuses, a
            # recipient's own broken `prima` snapshot): never the address, never a key.
            row.stato, row.motivo = "fallita", ROW_PREPARE_ERROR
            result.fallite += 1
            session.commit()
            continue
        outcome = sender.send(rendered, idempotency_key=str(row.id))
        if outcome.esito == "fermati":
            # Resend refused the key or the domain: every other row would get the same
            # answer. Stop this campaign's pass with the row still `in_coda` and no
            # attempt counted; the campaign stays `in_invio` and the next tick tries
            # again, so fixing the key resumes the send. The status alone is logged.
            session.commit()  # releases the row's lock; nothing was written
            _log.error("campaign %s stopped this tick: %s", campaign.id, outcome.dettaglio)
            return
        if outcome.esito == "accettata":
            row.stato, row.resend_id, row.inviata_at = "inviata", outcome.resend_id, clock()
            result.inviate += 1
        elif outcome.esito == "riprova":
            row.tentativi += 1
            if row.tentativi >= MAX_ATTEMPTS:
                dettaglio = outcome.dettaglio or "Resend non risponde"
                row.stato, row.motivo = "fallita", dettaglio[:200]
                result.fallite += 1
            else:
                session.commit()
                return  # the next tick tries this row again, same key
        else:
            row.stato, row.motivo = "fallita", (outcome.dettaglio or "rifiutata")[:200]
            result.fallite += 1
        session.commit()
        pause(SEND_INTERVAL_SECONDS)
    _finish(session, campaign.id, clock)


def _finish(session: Session, campaign_id: UUID, clock: Callable[[], datetime]) -> None:
    """Controller ruling R13: re-reads and locks the campaign once more, and moves it
    out of `in_invio` only if it is still there. A `cancel()` that landed between two
    rows already moved it to `annullata`; that stands. `populate_existing=True` for the
    same reason as `_claim`: this session's identity map may still hold the `in_invio`
    this very function set a moment ago, and the whole point here is to see whatever a
    concurrent `cancel()` wrote since."""
    campaign = session.scalars(
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    if campaign.stato == "in_invio":
        campaign.stato, campaign.inviata_at = "inviata", clock()
    session.commit()
