"""Who a campaign reaches (spec § 2, § 5.3): the candidates of a state or of filters, one
row per lowercase address, and the reasons someone is left out."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rebase_core.campaigns.schemas import AziendeFiltri, Filtri, TalentiFiltri
from rebase_core.campaigns.states import ENTITY, Candidate, candidates_for_state, display_name
from rebase_core.companies import CompanyService
from rebase_core.errors import ValidationFailed
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
LISTA_LATER = "Le liste fisse arrivano con «Riscrivi a chi non ha fatto niente»."

_FILTRI: TypeAdapter[TalentiFiltri | AziendeFiltri] = TypeAdapter(Filtri)


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
        raise ValidationFailed(ENTITY, "fonte", LISTA_LATER)
    seen: set[str] = set()
    unique: list[Candidate] = []
    for candidate in found:
        if candidate.email not in seen:
            seen.add(candidate.email)
            unique.append(candidate)
    return unique


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
