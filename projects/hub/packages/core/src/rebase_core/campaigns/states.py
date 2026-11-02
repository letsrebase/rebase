"""Journey states (spec § 2): where a person stopped between a sign-up and a used card.

Each state is a query over the hub's own tables, evaluated when the list is shown and
again when a mail is about to leave. A card's state reads the same four fields
`card_is_complete` reads (`schema_common.py`, the function `is_complete` itself calls): the
CV, the rate, the position and the work mode. Completeness is never re-derived here.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import ColumnElement, and_, exists, func, select
from sqlalchemy.orm import Session

from rebase_core.card_rows import card_emails
from rebase_core.errors import ValidationFailed
from rebase_core.leads import card_by_linkedin
from rebase_core.models import Company, Freelancer, Login, Signup, User
from rebase_core.schema_common import card_is_complete

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
    (`card_rows.card_emails`): a card deleted by an admin still says «this person is not
    a lead to chase»."""
    addresses = select(card_emails().c.email)
    signup_user = select(User.id).where(func.lower(User.email) == func.lower(Signup.email))
    rows = session.execute(
        select(Signup.id, Signup.email, Signup.nome, signup_user.scalar_subquery())
        .where(func.lower(Signup.email).not_in(addresses), ~card_by_linkedin())
        .order_by(Signup.created_at, Signup.id)
    ).all()
    return [
        Candidate(
            email=email.lower(),
            nome=display_name(nome),
            tipo="lead",
            user_id=user_id,
            signup_id=signup_id,
        )
        for signup_id, email, nome, user_id in rows
    ]


def _journey_state(stato: str) -> ColumnElement[bool]:
    """Whether a card is in journey state `stato`, in SQL: `card_state`'s rule over the
    same four columns (`schema_common.card_is_complete`), with the login `entered` splitting the
    cards that are neither complete nor missing only the CV. A test holds the two to the
    same answer for every combination of the four being there or not."""
    others = and_(
        Freelancer.tariffa_giornaliera.is_not(None),
        Freelancer.posizione.is_not(None),
        Freelancer.remoto.is_not(None),
    )
    has_cv = Freelancer.cv_size.is_not(None)
    if stato == "completo":
        return and_(has_cv, others)
    if stato == "manca_cv":
        return and_(~has_cv, others)
    entered = exists(select(Login.id).where(Login.user_id == Freelancer.user_id))
    return and_(~others, entered if stato == "scheda_vuota_entrati" else ~entered)


def _cards(session: Session, stato: str) -> list[Candidate]:
    rows = session.execute(
        select(Freelancer.id, User.id, User.email, User.nome)
        .join(User, User.id == Freelancer.user_id)
        .where(Freelancer.deleted_at.is_(None), _journey_state(stato))
        .order_by(Freelancer.created_at, Freelancer.id)
    ).all()
    return [
        Candidate(
            email=email.lower(),
            nome=display_name(nome),
            tipo="freelancer",
            user_id=user_id,
            freelancer_id=freelancer_id,
        )
        for freelancer_id, user_id, email, nome in rows
    ]


def _companies(session: Session) -> list[Candidate]:
    rows = session.execute(
        select(Company.id, User.id, User.email, User.nome)
        .join(User, User.id == Company.user_id)
        .where(Company.deleted_at.is_(None), Company.stato.in_(OPEN_COMPANY_STATES))
        .order_by(Company.created_at, Company.id)
    ).all()
    by_user: dict[UUID, Candidate] = {}
    for company_id, user_id, email, nome in rows:
        known = by_user.get(user_id)
        ids = (*known.company_ids, company_id) if known else (company_id,)
        by_user[user_id] = Candidate(
            email=email.lower(),
            nome=display_name(nome),
            tipo="azienda",
            user_id=user_id,
            company_ids=ids,
        )
    return list(by_user.values())
