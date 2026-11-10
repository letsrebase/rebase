"""Whether an address is the person's (REB-911, finding HCORE-13): the hub's form takes any
address, so one nobody proved stays out of campaigns until it is.

An address is confirmed by any row in `logins` (a magic link was followed, which only the
address's owner can do) or by `users.email_confirmed_at` (the link of the one confirmation
mail). `schedule` asks for a confirmation for whoever it left out (`request`); the campaigns
tick sends them (`send_pending`), at most one per person for good and within a daily budget.
The link is a 256-bit random value the hub keeps only as a sha256 with a deadline, like the
magic link's: forged, unknown or expired, it confirms nothing, and following it twice is the
same as once."""

import hashlib
import logging
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from time import sleep

from sqlalchemy import ColumnElement, exists, func, select, update
from sqlalchemy.orm import Session

from rebase_core.config import Settings
from rebase_core.mail import EmailSender
from rebase_core.mail_member import confirmation_mail
from rebase_core.models import Login, User
from rebase_core.team_caps import rome_midnight

TOKEN_MAX_LENGTH = 64
SEND_INTERVAL_SECONDS = 1.0
_log = logging.getLogger(__name__)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def unconfirmed() -> ColumnElement[bool]:
    """A `users` row whose address nobody proved: no confirmation and no login ever."""
    return (User.email_confirmed_at.is_(None)) & ~exists().where(Login.user_id == User.id)


def unconfirmed_emails(session: Session, emails: list[str]) -> set[str]:
    """Those of `emails` (lowercase) that belong to a person with an unconfirmed address.
    An address with no `users` row at all (a lead who never made a card) is not here: the
    rule is about cards."""
    if not emails:
        return set()
    address: ColumnElement[str] = func.lower(User.email)
    return set(session.scalars(select(address).where(address.in_(emails), unconfirmed())))


def request(session: Session, emails: list[str], *, now: datetime) -> int:
    """Marks the people behind `emails` as owed a confirmation mail, unless one was asked
    for or sent already. Returns how many were marked. Commits nothing."""
    if not emails:
        return 0
    address: ColumnElement[str] = func.lower(User.email)
    marked = session.execute(
        update(User)
        .where(
            address.in_(emails),
            unconfirmed(),
            User.confirmation_requested_at.is_(None),
            User.confirmation_sent_at.is_(None),
        )
        .values(confirmation_requested_at=now)
        .returning(User.id)
    )
    return len(marked.scalars().all())


def sent_today(session: Session, now: datetime) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.confirmation_sent_at >= rome_midnight(now))
        )
        or 0
    )


def send_pending(
    session: Session,
    mailer: EmailSender,
    settings: Settings,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    pause: Callable[[float], None] = sleep,
) -> int:
    """Sends the confirmation mails that are owed, as many as the day's budget leaves.
    Each person is claimed by a conditional update (`confirmation_sent_at` still empty)
    and committed before the mail leaves, so two passes never send two; a mail the
    provider refuses gives the claim back and is tried again next pass. Returns how many
    went. Whoever opted out, bounced, was discarded or confirmed since the request is not
    written to: `exclusions` is read again, and only «not confirmed» alone lets a mail go."""
    from rebase_core.campaigns.audience import REASON_UNCONFIRMED, exclusions

    room = settings.confirmation_mail_daily_budget - sent_today(session, clock())
    if room <= 0:
        return 0
    owed = session.scalars(
        select(User)
        .where(
            User.confirmation_requested_at.is_not(None),
            User.confirmation_sent_at.is_(None),
            unconfirmed(),
        )
        .order_by(User.confirmation_requested_at, User.id)
        .limit(room)
    ).all()
    sent = 0
    for user in owed:
        email = user.email.lower()
        now = clock()
        reason = exclusions(
            session, [email], campaign_id=None, now=now, gap_days=settings.campaign_gap_days
        ).get(email)
        if reason != REASON_UNCONFIRMED:
            continue
        raw = secrets.token_urlsafe(32)
        claimed = session.execute(
            update(User)
            .where(User.id == user.id, User.confirmation_sent_at.is_(None))
            .values(
                confirmation_sent_at=now,
                confirmation_token_hash=_hash(raw),
                confirmation_expires_at=now + timedelta(days=settings.confirmation_link_days),
            )
            .returning(User.id)
        )
        if len(claimed.scalars().all()) != 1:
            session.rollback()
            continue
        session.commit()
        link = f"{settings.hub_url.rstrip('/')}/confirm?t={raw}"
        try:
            delivered = mailer.send(
                confirmation_mail(user.email, link, settings.confirmation_link_days)
            )
        except Exception as exc:  # the loop must go on: the claim is given back below
            _log.error("confirmation mail not sent: %s", type(exc).__name__)
            delivered = False
        if delivered:
            sent += 1
            pause(SEND_INTERVAL_SECONDS)
            continue
        session.execute(
            update(User)
            .where(User.id == user.id)
            .values(
                confirmation_sent_at=None,
                confirmation_token_hash=None,
                confirmation_expires_at=None,
            )
        )
        session.commit()
    return sent


def confirm(session: Session, raw_token: str, *, now: datetime | None = None) -> bool:
    """Confirms the address behind a link and commits. `False` for a link that is not
    ours, unknown or past its deadline; `True` for a good one and for the same one
    followed again (the answer is the same, the first moment is kept)."""
    if not raw_token or len(raw_token) > TOKEN_MAX_LENGTH:
        return False
    moment = now or datetime.now(UTC)
    user = session.scalar(select(User).where(User.confirmation_token_hash == _hash(raw_token)))
    if user is None:
        return False
    if user.email_confirmed_at is not None:
        return True
    if user.confirmation_expires_at is None or user.confirmation_expires_at <= moment:
        return False
    user.email_confirmed_at = moment
    session.commit()
    return True
