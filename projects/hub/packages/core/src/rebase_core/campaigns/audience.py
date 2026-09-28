"""Who a campaign reaches (spec § 2, § 5.3): the candidates of a state or of filters, one
row per lowercase address, and the reasons someone is left out."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rebase_core.campaigns.actions import done_at
from rebase_core.campaigns.schemas import AziendeFiltri, Filtri, TalentiFiltri
from rebase_core.campaigns.states import Candidate, candidates_for_state, display_name
from rebase_core.companies import CompanyService
from rebase_core.models import (
    Campaign,
    CampaignOptout,
    CampaignRecipient,
    Company,
    Freelancer,
    User,
)
from rebase_core.schemas import CompanyRead, TalentoRead
from rebase_core.talenti import LIST_LIMIT_MAX, TalentiService

ROME = ZoneInfo("Europe/Rome")
REASON_ADMIN = "amministratore"
REASON_NEVER = "«Non scrivere mai»"
REASON_OPTOUT = "si è disiscritto"
REASON_BOUNCED = "indirizzo rimbalzato"
REASON_RECENT = "ha ricevuto un'altra campagna il {data}"
REASON_DONE = "ha già fatto l'azione"
REASON_NOT_LISTED = "non più in lista"
REASON_CANCELLED = "campagna annullata"
# REB-524, Ivan's decision (DECISIONS.md, 2026-09-28): a card an admin turned down.
REASON_DISCARDED = "scheda scartata"

_FILTRI: TypeAdapter[TalentiFiltri | AziendeFiltri] = TypeAdapter(Filtri)
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudienceRow:
    candidate: Candidate
    escluso: str | None


def candidates(session: Session, campaign: Campaign) -> list[Candidate]:
    if campaign.fonte == "stato":
        found = candidates_for_state(session, campaign.stato_percorso or "")
    elif campaign.fonte == "filtri":
        found = _filtered(session, campaign.filtri or {})
    else:
        found = _not_done(session, campaign)
    seen: set[str] = set()
    unique: list[Candidate] = []
    for candidate in found:
        if candidate.email not in seen:
            seen.add(candidate.email)
            unique.append(candidate)
    return unique


def waiting_rows(session: Session, parent: Campaign) -> list[CampaignRecipient]:
    """The parent's sent rows with no stamped action whose person is still in the hub
    and whose action has not happened live (spec § 4.3), less the rows whose mail
    bounced or drew a complaint (REB-524). Read live, not from
    `azione_at` alone: the tick stamps once a minute and only for 30 days after a mail,
    and whoever acted since must not be written to again (Review Focus 2), nor counted
    as still waiting (`follow_up`'s own check, Greptile P1). Each person keeps the
    snapshot links the earlier row froze: their user, card, lead and open requests. A
    row is also dropped, not shown with a reason, when the person it named is gone from
    the hub -- a soft-deleted card, or a referente whose every open request's company is
    soft-deleted -- the same way `candidates_for_state` never lists them for a `stato`
    or `filtri` campaign; a lead row stays, since a lead who made a card since is caught
    by `done_at`.

    A row whose own snapshot is broken -- `prima` or `prima["richieste"]` of the wrong
    shape (a list, a string), a `richieste` key that is not a UUID, a `richieste` value
    (or `prima["t"]`) that `datetime.fromisoformat` cannot parse, or parses as a naive
    datetime (a date-only string such as `"2026-09-28"` parses cleanly as midnight with
    no `tzinfo`, which then raises `TypeError` where `done_at` compares it in plain
    Python against a timezone-aware database timestamp, Greptile, PR #444, round 4) --
    must not break the whole list. Every one of these is checked up front, before
    `done_at` is ever called for the row, and every check mirrors exactly what `done_at`
    itself would do with the same value (`actions.py`): the wrong shape explicitly,
    since folding it into a broadened `except` would also swallow a real bug elsewhere
    (CodeRabbit Major, PR #444, round 3), and the two `datetime.fromisoformat` calls,
    tzinfo included, by running them here first, inside the one `try` the shape checks
    already need. `done_at` is then called with no `try` around it at all, so anything
    it still raises -- a transient database error, an unrelated bug -- propagates
    rather than silently dropping an eligible person (CodeRabbit's adversarial pass, PR
    #444, round 3). A row that fails a shape or a value check is left out, the safe
    side: it never lets a mail reach someone who may already have acted, and it is
    logged by id."""
    rows = session.scalars(
        select(CampaignRecipient)
        .where(
            CampaignRecipient.campaign_id == parent.id,
            CampaignRecipient.stato == "inviata",
            CampaignRecipient.azione_at.is_(None),
            # A mail that bounced never reached anyone, and a complaint is a «never
            # again» (REB-524): the follow-up's audience excludes both addresses anyway,
            # so a row kept here only made «Riscrivi» draft a list of nobody.
            CampaignRecipient.rimbalzata_at.is_(None),
            CampaignRecipient.reclamo_at.is_(None),
        )
        .order_by(CampaignRecipient.email)
    ).all()
    freelancer_ids = {row.freelancer_id for row in rows if row.freelancer_id is not None}
    alive_freelancers = (
        set(
            session.scalars(
                select(Freelancer.id).where(
                    Freelancer.id.in_(freelancer_ids), Freelancer.deleted_at.is_(None)
                )
            )
        )
        if freelancer_ids
        else set()
    )
    richieste_by_row: dict[UUID, tuple[UUID, ...]] = {}
    for row in rows:
        # The shape is checked explicitly, not folded into the `except` below: `prima`
        # or `richieste` of the wrong shape (a list, a string) must not raise inside a
        # broadened `except`, which would also swallow a real bug elsewhere
        # (CodeRabbit Major on PR #444). `prima` is a dict or `None`; `richieste`, once
        # defaulted, is a dict whose keys are strings -- anything else leaves the row
        # out the same way a key `UUID` cannot parse does.
        prima = row.prima
        if prima is not None and not isinstance(prima, dict):
            _log.error("waiting row %s left out: prima is not a dict", row.id)
            continue
        prima = prima or {}
        richieste = prima.get("richieste", {})
        if not isinstance(richieste, dict) or not all(isinstance(key, str) for key in richieste):
            _log.error("waiting row %s left out: richieste is not a string-keyed dict", row.id)
            continue
        try:
            parsed_keys = tuple(UUID(key) for key in richieste)
            # `done_at` (`actions.py`) parses two snapshot values with
            # `datetime.fromisoformat`, then compares each in plain Python (never SQL)
            # against a timezone-aware database timestamp: every `richieste` value,
            # against `Company.updated_at`, when `parent.azione` is
            # `richiesta_aggiornata`, and `prima["t"]`, against `card.created_at` for
            # `profilo_creato`, only when `since` -- always `row.inviata_at` below -- is
            # `None`. `fromisoformat` accepts a date-only string as naive midnight,
            # which parses cleanly and then raises `TypeError` at the comparison
            # (Greptile, PR #444, round 4) -- so a parsed value only passes here with a
            # `tzinfo`, the same requirement `done_at` needs to not raise. Both are
            # checked here, up front, inside the one `try` the key parsing above
            # already needs, so `done_at` itself can be called below with no `try`
            # around it at all (CodeRabbit's adversarial pass, PR #444, round 3).
            if parent.azione == "richiesta_aggiornata":
                for was in richieste.values():
                    if datetime.fromisoformat(was).tzinfo is None:
                        raise ValueError("richieste value has no timezone")
            if row.inviata_at is None and datetime.fromisoformat(prima["t"]).tzinfo is None:
                raise ValueError("prima['t'] has no timezone")
        except (TypeError, ValueError, KeyError) as exc:
            _log.error("waiting row %s left out: %s", row.id, type(exc).__name__)
            continue
        richieste_by_row[row.id] = parsed_keys
    company_ids = {cid for ids in richieste_by_row.values() for cid in ids}
    alive_companies = (
        set(
            session.scalars(
                select(Company.id).where(Company.id.in_(company_ids), Company.deleted_at.is_(None))
            )
        )
        if company_ids
        else set()
    )
    found: list[CampaignRecipient] = []
    for row in rows:
        if row.id not in richieste_by_row:
            continue  # its own snapshot was already unreadable, and logged above
        if row.freelancer_id is not None and row.freelancer_id not in alive_freelancers:
            continue
        richieste = richieste_by_row[row.id]
        if richieste and not any(cid in alive_companies for cid in richieste):
            continue
        if done_at(session, row, parent.azione, since=row.inviata_at) is not None:
            continue
        found.append(row)
    return found


def _not_done(session: Session, campaign: Campaign) -> list[Candidate]:
    """A `lista` (spec § 4.3): whom the earlier campaign reached and who has not done its
    action, mapped from `waiting_rows`, each row's own snapshot links carried over."""
    parent = session.get(Campaign, campaign.segue_id) if campaign.segue_id else None
    if parent is None:
        return []
    return [
        Candidate(
            email=row.email,
            nome=row.nome,
            tipo=row.tipo,
            user_id=row.user_id,
            freelancer_id=row.freelancer_id,
            signup_id=row.signup_id,
            company_ids=tuple(UUID(key) for key in (row.prima or {}).get("richieste", {})),
            pigro_slugs=tuple(row.pigro_slugs),
        )
        for row in waiting_rows(session, parent)
    ]


def _filtered(session: Session, raw: dict[str, Any]) -> list[Candidate]:
    filtri = _FILTRI.validate_python(raw)
    params = filtri.model_dump(exclude={"lista"}, exclude_none=True)
    if isinstance(filtri, TalentiFiltri):
        return _from_talenti(session, params)
    return _from_aziende(session, params)


def _from_talenti(session: Session, params: dict[str, Any]) -> list[Candidate]:
    service = TalentiService(session)
    rows: list[TalentoRead] = []
    cursor: str | None = None
    while True:
        page = service.list_recent(limit=LIST_LIMIT_MAX, cursor=cursor, **params)
        rows.extend(page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    card_ids = [row.id for row in rows if row.stato != "lead"]
    users: dict[UUID, User] = {}
    if card_ids:
        card_rows = session.execute(
            select(Freelancer.id, User)
            .join(User, User.id == Freelancer.user_id)
            .where(Freelancer.id.in_(card_ids))
        ).all()
        users = {freelancer_id: user for freelancer_id, user in card_rows}
    found: list[Candidate] = []
    for row in rows:
        if row.stato == "lead":
            found.append(
                Candidate(
                    email=row.email.lower(),
                    nome=display_name(row.nome),
                    tipo="lead",
                    signup_id=row.id,
                )
            )
        elif row.id in users:
            user = users[row.id]
            found.append(
                Candidate(
                    email=user.email.lower(),
                    nome=display_name(user.nome),
                    tipo="freelancer",
                    user_id=user.id,
                    freelancer_id=row.id,
                )
            )
    return found


def _from_aziende(session: Session, params: dict[str, Any]) -> list[Candidate]:
    service = CompanyService(session)
    rows: list[CompanyRead] = []
    cursor: str | None = None
    while True:
        page = service.list_recent(limit=LIST_LIMIT_MAX, cursor=cursor, **params)
        rows.extend(page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    owners: dict[UUID, UUID] = {}
    if rows:
        owner_rows = session.execute(
            select(Company.id, Company.user_id).where(Company.id.in_([row.id for row in rows]))
        ).all()
        owners = {company_id: user_id for company_id, user_id in owner_rows}
    by_email: dict[str, Candidate] = {}
    for row in rows:
        email = row.email.lower()
        known = by_email.get(email)
        by_email[email] = Candidate(
            email=email,
            # `CompanyRead.referente` is `f"{user.nome} {user.cognome}"` (`companies.py`'s
            # `_to_read`), always «Nome Cognome»: the first token is the first name.
            nome=display_name(row.referente.split(" ")[0] if row.referente else None),
            tipo="azienda",
            user_id=owners.get(row.id),
            company_ids=(*(known.company_ids if known else ()), row.id),
        )
    return list(by_email.values())


def exclusions(
    session: Session,
    emails: list[str],
    *,
    campaign_id: UUID | None,
    now: datetime,
    gap_days: int,
) -> dict[str, str]:
    if not emails:
        return {}
    admins = set(
        session.scalars(
            select(func.lower(User.email)).where(
                User.role == "admin", func.lower(User.email).in_(emails)
            )
        )
    )
    # A card an admin marked «scartato» (REB-524), deleted or not: a turned-down person
    # stays turned down. By address, so the rule also holds on a company's list for the
    # same person, and before each mail for a card turned down after the list froze.
    discarded = set(
        session.scalars(
            select(func.lower(User.email))
            .join(Freelancer, Freelancer.user_id == User.id)
            .where(Freelancer.stato == "scartato", func.lower(User.email).in_(emails))
        )
    )
    optout_rows = session.execute(
        select(CampaignOptout.email, CampaignOptout.fonte).where(CampaignOptout.email.in_(emails))
    ).all()
    optouts: dict[str, str] = {email: fonte for email, fonte in optout_rows}
    bounced = set(
        session.scalars(
            select(CampaignRecipient.email).where(
                CampaignRecipient.email.in_(emails), CampaignRecipient.rimbalzata_at.is_not(None)
            )
        )
    )
    recent_q = (
        select(CampaignRecipient.email, func.max(CampaignRecipient.inviata_at))
        .where(
            CampaignRecipient.email.in_(emails),
            CampaignRecipient.stato == "inviata",
            CampaignRecipient.inviata_at >= now - timedelta(days=gap_days),
        )
        .group_by(CampaignRecipient.email)
    )
    if campaign_id is not None:
        recent_q = recent_q.where(CampaignRecipient.campaign_id != campaign_id)
    recent: dict[str, datetime] = {
        email: when for email, when in session.execute(recent_q).all() if when is not None
    }
    reasons: dict[str, str] = {}
    for email in emails:
        if email in admins:
            reasons[email] = REASON_ADMIN
        elif email in discarded:
            reasons[email] = REASON_DISCARDED
        elif email in optouts:
            reasons[email] = REASON_NEVER if optouts[email] == "admin" else REASON_OPTOUT
        elif email in bounced:
            reasons[email] = REASON_BOUNCED
        elif email in recent:
            data = recent[email].astimezone(ROME).strftime("%d/%m")
            reasons[email] = REASON_RECENT.format(data=data)
    return reasons


def build_audience(
    session: Session, campaign: Campaign, *, now: datetime, gap_days: int
) -> list[AudienceRow]:
    found = candidates(session, campaign)
    reasons = exclusions(
        session, [c.email for c in found], campaign_id=campaign.id, now=now, gap_days=gap_days
    )
    return [AudienceRow(candidate, reasons.get(candidate.email)) for candidate in found]
