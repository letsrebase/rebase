"""What an action is measured against, and whether a person has done it since (spec
§ 6.2). The send-time check (§ 5.3) asks `done_at` whether a mail would ask for
something already done; the tick's `stamp_outcomes` (`outcome.py`) asks it when."""

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    DateTime,
    Integer,
    SQLColumnExpression,
    Uuid,
    and_,
    column,
    func,
    select,
    values,
)
from sqlalchemy.orm import Session, defer

from rebase_core.campaigns.states import Candidate, card_state
from rebase_core.models import (
    Campaign,
    CampaignRecipient,
    Company,
    Freelancer,
    Login,
    Note,
    User,
)

# What `MemberService.replace_cv` writes on a card's first CV (`members.py:303`).
CV_NOTE_PREFIX = "CV caricato dalla persona"


def _card(session: Session, email: str) -> Freelancer | None:
    """The live card of `email`, without its CV's bytes: `cv_size` says whether one is
    there, and a snapshot or a check runs once per recipient."""
    return session.scalar(
        select(Freelancer)
        .join(User, User.id == Freelancer.user_id)
        .where(func.lower(User.email) == email.lower(), Freelancer.deleted_at.is_(None))
        .options(defer(Freelancer.cv_bytes))
    )


def _complete(card: Freelancer) -> bool:
    return (
        card_state(card.cv_size, card.tariffa_giornaliera, card.posizione, card.remoto)
        == "completo"
    )


def snapshot(session: Session, candidate: Candidate, now: datetime) -> dict[str, Any]:
    card = _card(session, candidate.email)
    richieste: dict[str, str] = {}
    if candidate.company_ids:
        rows = session.execute(
            select(Company.id, Company.updated_at).where(Company.id.in_(candidate.company_ids))
        ).all()
        richieste = {str(company_id): updated.isoformat() for company_id, updated in rows}
    return {
        "t": now.isoformat(),
        "ha_scheda": card is not None,
        "ha_cv": card is not None and card.cv_size is not None,
        "completa": card is not None and _complete(card),
        "richieste": richieste,
    }


# The moment an action must follow, when the caller has no moment of its own for a row:
# the list's snapshot (`prima["t"]`), which `done_at_many` reads itself.
_NO_SINCE: Callable[[CampaignRecipient], datetime | None] = lambda _row: None  # noqa: E731

_Moments = dict[UUID, datetime]


def _cards_by_email(session: Session, emails: set[str]) -> dict[str, Freelancer]:
    """The live card of each address, lowercase, without its CV's bytes: one read for the
    whole set where `_card` reads one."""
    if not emails:
        return {}
    address = func.lower(User.email)
    found: dict[str, Freelancer] = {}
    for card, email in session.execute(
        select(Freelancer, address)
        .join(User, User.id == Freelancer.user_id)
        .where(address.in_(emails), Freelancer.deleted_at.is_(None))
        .options(defer(Freelancer.cv_bytes))
    ):
        found.setdefault(email, card)
    return found


def _first_after(
    session: Session,
    table: type[Login] | type[Note],
    owner: SQLColumnExpression[UUID],
    when: SQLColumnExpression[datetime],
    asked: dict[UUID, tuple[UUID, datetime]],
    *where: ColumnElement[bool],
) -> dict[UUID, datetime]:
    """For each key of `asked`, whose value is an owner and a moment, the earliest `when`
    after that moment among the `table` rows of that owner that `where` keeps; a key with
    none is left out. One statement however many are asked: the moments differ by row, so
    they are joined in as a `VALUES` list rather than asked one at a time."""
    if not asked:
        return {}
    keys = list(asked)
    wanted = values(
        column("n", Integer),
        column("owner", Uuid),
        column("moment", DateTime(timezone=True)),
        name="wanted",
    ).data([(n, *asked[key]) for n, key in enumerate(keys)])
    rows = session.execute(
        select(wanted.c.n, func.min(when))
        .select_from(wanted)
        .join(table, and_(owner == wanted.c.owner, when > wanted.c.moment, *where))
        .group_by(wanted.c.n)
    ).all()
    return {keys[n]: earliest for n, earliest in rows if earliest is not None}


def _entered_many(
    session: Session, recipients: Sequence[CampaignRecipient], moments: _Moments
) -> dict[UUID, datetime | None]:
    by_address = {r.email for r in recipients if r.user_id is None}
    users: dict[str, UUID] = {}
    if by_address:
        address = func.lower(User.email)
        users = {
            email: user_id
            for email, user_id in session.execute(
                select(address, User.id).where(address.in_(by_address))
            )
        }
    owners = {r.id: r.user_id or users.get(r.email) for r in recipients}
    first = _first_after(
        session,
        Login,
        Login.user_id,
        Login.logged_at,
        {row: (owner, moments[row]) for row, owner in owners.items() if owner is not None},
    )
    return {row: first.get(row) for row in owners}


def entered_at(
    session: Session, recipient: CampaignRecipient, *, since: datetime
) -> datetime | None:
    """Action a: the recipient's first login after `since`. A lead has no user until it
    makes a card, and a person with no user has not entered."""
    return _entered_many(session, [recipient], {recipient.id: since})[recipient.id]


def _earlier_clicks(
    session: Session,
    recipients: Sequence[CampaignRecipient],
    since: Callable[[CampaignRecipient], datetime | None],
) -> dict[UUID, datetime | None]:
    """`earlier_click` for a set of recipients: the chain of each campaign is read once,
    then the clicks of every address on it at once."""
    chains: dict[UUID, list[UUID]] = {}
    for campaign_id in {r.campaign_id for r in recipients}:
        chain: list[UUID] = []
        parent = session.scalar(select(Campaign.segue_id).where(Campaign.id == campaign_id))
        while parent is not None and parent not in chain and len(chain) < _MAX_CHAIN:
            chain.append(parent)
            parent = session.scalar(select(Campaign.segue_id).where(Campaign.id == parent))
        chains[campaign_id] = chain
    every_chain = {campaign for chain in chains.values() for campaign in chain}
    clicks: dict[tuple[UUID, str], list[datetime]] = {}
    if every_chain:
        for campaign_id, email, clicked in session.execute(
            select(
                CampaignRecipient.campaign_id,
                CampaignRecipient.email,
                CampaignRecipient.primo_clic_at,
            ).where(
                CampaignRecipient.campaign_id.in_(every_chain),
                CampaignRecipient.email.in_({r.email for r in recipients}),
                CampaignRecipient.primo_clic_at.is_not(None),
            )
        ):
            if clicked is not None:
                clicks.setdefault((campaign_id, email), []).append(clicked)
    answer: dict[UUID, datetime | None] = {}
    for r in recipients:
        after = since(r)
        found = [
            clicked
            for campaign_id in chains[r.campaign_id]
            for clicked in clicks.get((campaign_id, r.email), [])
            if after is None or clicked > after
        ]
        answer[r.id] = min(found) if found else None
    return answer


def done_at(
    session: Session,
    recipient: CampaignRecipient,
    azione: str,
    *,
    since: datetime | None = None,
    now: datetime | None = None,
) -> datetime | None:
    """When the recipient did `azione`, or `None`. `since` is what the action must
    follow: the list's snapshot (`prima["t"]`) when omitted, which is what the
    send-time check wants, and the mail's own `inviata_at` when the tick stamps the
    outcome. Completing a card has no moment of its own (spec § 6.2), so `c` answers
    `now`, the tick that saw it, when given, and the card's `updated_at` otherwise."""
    return done_at_many(session, [recipient], azione, since=lambda _row: since, now=now)[
        recipient.id
    ]


def done_at_many(
    session: Session,
    recipients: Sequence[CampaignRecipient],
    azione: str,
    *,
    since: Callable[[CampaignRecipient], datetime | None] = _NO_SINCE,
    now: datetime | None = None,
) -> dict[UUID, datetime | None]:
    """`done_at` for every row of `recipients` (by their `id`), with a statement per kind of
    fact instead of up to three per row: the cards by address, the logins, the notes, the
    requests' `updated_at` and the clicks each in one, whatever the size of the list. The
    answers are `done_at`'s, which is this with one row."""
    moments: _Moments = {}
    for r in recipients:
        own = since(r)
        moments[r.id] = own if own is not None else datetime.fromisoformat((r.prima or {})["t"])
    if azione == "entrato":
        return _entered_many(session, recipients, moments)
    if azione in ("cv", "scheda_completa", "profilo_creato"):
        return _card_action(session, recipients, azione, moments, now)
    if azione == "richiesta_aggiornata":
        return _requests_updated(session, recipients, moments)
    if azione == "clic":
        # «Un link» (REB-530): of a page outside the hub, the first click Resend reports
        # is all the hub sees. This mail's own, or, for a «Riscrivi», the click on a mail
        # of the campaigns it follows.
        clicks = _earlier_clicks(session, [r for r in recipients if r.primo_clic_at is None], since)
        return {r.id: r.primo_clic_at or clicks.get(r.id) for r in recipients}
    return {r.id: None for r in recipients}  # `pigro_cliente`: phase 3 (spec § 6.3)


def _card_action(
    session: Session,
    recipients: Sequence[CampaignRecipient],
    azione: str,
    moments: _Moments,
    now: datetime | None,
) -> dict[UUID, datetime | None]:
    cards = _cards_by_email(session, {r.email.lower() for r in recipients})
    noted: dict[UUID, datetime] = {}
    if azione == "cv":
        pending = {
            r.id: (card.id, moments[r.id])
            for r in recipients
            if (card := cards.get(r.email.lower())) is not None
            and not (r.prima or {}).get("ha_cv")
            and card.cv_size is not None
        }
        noted = _first_after(
            session,
            Note,
            Note.entity_id,
            Note.created_at,
            pending,
            Note.entity_type == "freelancer",
            Note.fonte == "persona",
            Note.eliminata_at.is_(None),
            Note.testo.startswith(CV_NOTE_PREFIX),
        )
    answer: dict[UUID, datetime | None] = {}
    for r in recipients:
        prima = r.prima or {}
        card = cards.get(r.email.lower())
        moment = moments[r.id]
        if card is None:
            answer[r.id] = None
        elif azione == "profilo_creato":
            created = not prima.get("ha_scheda") and card.created_at > moment
            answer[r.id] = card.created_at if created else None
        elif azione == "cv":
            if prima.get("ha_cv") or card.cv_size is None:
                answer[r.id] = None
            else:
                answer[r.id] = noted.get(r.id) or card.updated_at
        elif prima.get("completa") or not _complete(card):
            answer[r.id] = None
        else:
            answer[r.id] = now or card.updated_at
    return answer


def _requests_updated(
    session: Session, recipients: Sequence[CampaignRecipient], moments: _Moments
) -> dict[UUID, datetime | None]:
    company_ids = {
        UUID(company_id)
        for r in recipients
        for company_id in (r.prima or {}).get("richieste") or {}
    }
    updated: dict[UUID, datetime] = {}
    if company_ids:
        updated = {
            company_id: stamp
            for company_id, stamp in session.execute(
                select(Company.id, Company.updated_at).where(Company.id.in_(company_ids))
            )
        }
    answer: dict[UUID, datetime | None] = {}
    for r in recipients:
        moved: list[datetime] = []
        for company_id, was in ((r.prima or {}).get("richieste") or {}).items():
            stamp = updated.get(UUID(company_id))
            if stamp is not None and stamp > max(datetime.fromisoformat(was), moments[r.id]):
                moved.append(stamp)
        answer[r.id] = min(moved) if moved else None
    return answer


# A «Riscrivi» of a «Riscrivi» is possible; nobody writes ten of them.
_MAX_CHAIN = 10


def earlier_click(
    session: Session, recipient: CampaignRecipient, *, since: datetime | None = None
) -> datetime | None:
    """The first click the same address made on a mail of the campaigns `recipient`'s
    campaign follows (`segue_id`, up the chain), or `None`. A follow-up row has no click
    of its own until its mail has left, so without this the send-time check would mail
    somebody who clicked the earlier mail after the follow-up was scheduled (Greptile P1
    on #476). `since` given, as the tick's stamping gives it, only a click after that
    moment counts: a follow-up is credited with what happened after it was sent. `None`,
    the send-time check, counts any click: it means the person has already done it."""
    return _earlier_clicks(session, [recipient], lambda _row: since)[recipient.id]
