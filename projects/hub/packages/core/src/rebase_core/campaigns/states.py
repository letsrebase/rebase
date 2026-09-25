"""Journey states (spec § 2): where a person stopped between a sign-up and a used card.

Each state is a query over the hub's own tables, evaluated when the list is shown and
again when a mail is about to leave. A card's state reads the same four fields
`card_is_complete` reads (`schemas.py`, the function `_is_complete` itself calls): the
CV, the rate, the position and the work mode. Completeness is never re-derived here.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, defer

from rebase_core.errors import ValidationFailed
from rebase_core.models import Company, Freelancer, Login, Signup, User
from rebase_core.schemas import card_is_complete

ENTITY = "campagna"

JOURNEY_STATES: dict[str, str] = {
    "lead": "Lead senza profilo",
    "scheda_vuota_nuovi": "Scheda vuota, mai entrati",
    "scheda_vuota_entrati": "Scheda vuota, già entrati",
    "manca_cv": "Manca solo il CV",
    "completo": "Profilo completo",
    "pigro_vuoto": "Spazio Pigro vuoto",
    "azienda_aperta": "Azienda con richiesta aperta",
}
PHASE_ONE_STATES = tuple(key for key in JOURNEY_STATES if key != "pigro_vuoto")
OPEN_COMPANY_STATES = ("nuovo", "contattato", "in_corso")
PIGRO_LATER = "Lo spazio Pigro arriva con la fase Pigro delle campagne."

CardState = Literal["completo", "manca_cv", "scheda"]


@dataclass(frozen=True)
class Candidate:
    """One person a list may reach, before any exclusion. `email` is lowercase."""

    email: str
    nome: str | None
    tipo: str
    user_id: UUID | None = None
    freelancer_id: UUID | None = None
    signup_id: UUID | None = None
    company_ids: tuple[UUID, ...] = ()
    pigro_slugs: tuple[str, ...] = ()


def display_name(nome: str | None) -> str | None:
    """The name as a greeting reads it: stripped, and «giulia» or «STEFANIA» put in
    title case, since that is how the form stored what somebody typed. A name already
    mixed-case is theirs and stays."""
    value = (nome or "").strip()
    if not value:
        return None
    if value.islower() or value.isupper():
        return value.title()
    return value


_ANY_CV_SIZE = 0  # a stand-in non-None value: only the other three fields matter below


def card_state(
    cv_size: int | None,
    tariffa: Decimal | None,
    posizione: str | None,
    remoto: str | None,
) -> CardState:
    if card_is_complete(cv_size, tariffa, posizione, remoto):
        return "completo"
    others_complete = card_is_complete(_ANY_CV_SIZE, tariffa, posizione, remoto)
    if cv_size is None and others_complete:
        return "manca_cv"
    return "scheda"


def candidates_for_state(session: Session, stato: str) -> list[Candidate]:
    if stato == "lead":
        return _leads(session)
    if stato in ("scheda_vuota_nuovi", "scheda_vuota_entrati", "manca_cv", "completo"):
        return _cards(session, stato)
    if stato == "azienda_aperta":
        return _companies(session)
    if stato == "pigro_vuoto":
        raise ValidationFailed(ENTITY, "stato_percorso", PIGRO_LATER)
    raise ValidationFailed(ENTITY, "stato_percorso", f"Stato sconosciuto: {stato}.")


def _leads(session: Session) -> list[Candidate]:
    """A sign-up whose address has no card at all, the same anti-join Talenti runs
    (`talenti._card_emails`): a card deleted by an admin still says «this person is not
    a lead to chase»."""
    card_emails = select(func.lower(User.email)).join(Freelancer, Freelancer.user_id == User.id)
    signup_user = select(User.id).where(func.lower(User.email) == func.lower(Signup.email))
    rows = session.execute(
        select(Signup, signup_user.scalar_subquery())
        .where(func.lower(Signup.email).not_in(card_emails))
        .order_by(Signup.created_at, Signup.id)
    ).all()
    return [
        Candidate(
            email=row.email.lower(),
            nome=display_name(row.nome),
            tipo="lead",
            user_id=user_id,
            signup_id=row.id,
        )
        for row, user_id in rows
    ]


def _cards(session: Session, stato: str) -> list[Candidate]:
    entered = exists(select(Login.id).where(Login.user_id == Freelancer.user_id))
    rows = session.execute(
        select(Freelancer, User, entered.label("entrato"))
        .join(User, User.id == Freelancer.user_id)
        .where(Freelancer.deleted_at.is_(None))
        .order_by(Freelancer.created_at, Freelancer.id)
        .options(defer(Freelancer.cv_bytes))  # completeness reads `cv_size`, never the PDF
    ).all()
    found: list[Candidate] = []
    for card, user, entrato in rows:
        state = card_state(card.cv_size, card.tariffa_giornaliera, card.posizione, card.remoto)
        key = (
            state
            if state != "scheda"
            else ("scheda_vuota_entrati" if entrato else "scheda_vuota_nuovi")
        )
        if key == stato:
            found.append(
                Candidate(
                    email=user.email.lower(),
                    nome=display_name(user.nome),
                    tipo="freelancer",
                    user_id=user.id,
                    freelancer_id=card.id,
                )
            )
    return found


def _companies(session: Session) -> list[Candidate]:
    rows = session.execute(
        select(Company, User)
        .join(User, User.id == Company.user_id)
        .where(Company.deleted_at.is_(None), Company.stato.in_(OPEN_COMPANY_STATES))
        .order_by(Company.created_at, Company.id)
    ).all()
    by_user: dict[UUID, Candidate] = {}
    for request, user in rows:
        known = by_user.get(user.id)
        ids = (*known.company_ids, request.id) if known else (request.id,)
        by_user[user.id] = Candidate(
            email=user.email.lower(),
            nome=display_name(user.nome),
            tipo="azienda",
            user_id=user.id,
            company_ids=ids,
        )
    return list(by_user.values())
