"""Rows the campaign tests build on: people in every state, leads, company requests,
and a clean table after each test. Imported by name, like `fakes_contracts.py`."""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_core.campaigns.states import Candidate
from rebase_core.models import Company, Freelancer, Login, Signup, User

CAMPAIGN_TABLES = ("campaign_optouts", "campaign_recipients", "campaigns")
PEOPLE_TABLES = ("logins", "comments", "freelancers", "companies", "signups", "users")


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    for table in (*CAMPAIGN_TABLES, *PEOPLE_TABLES):
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


def person(
    session: Session,
    email: str,
    *,
    nome: str = "Ada",
    cv: bool = True,
    tariffa: bool = True,
    posizione: bool = True,
    remoto: bool = True,
    deleted: bool = False,
    role: str = "member",
    logins: int = 0,
) -> Freelancer:
    user = User(email=email, nome=nome, cognome="Lovelace", role=role)
    session.add(user)
    session.flush()
    card = Freelancer(
        user_id=user.id,
        cv_bytes=b"%PDF" if cv else None,
        cv_filename="cv.pdf" if cv else None,
        cv_mime="application/pdf" if cv else None,
        cv_size=4 if cv else None,
        tariffa_giornaliera=Decimal("450") if tariffa else None,
        posizione="Backend developer" if posizione else None,
        remoto="remoto" if remoto else None,
        links=[],
    )
    if deleted:
        card.deleted_at = datetime.now(UTC)
    session.add(card)
    session.flush()
    for _ in range(logins):
        session.add(Login(user_id=user.id))
    session.commit()
    return card


def lead(session: Session, email: str, nome: str | None = "giulia") -> Signup:
    row = Signup(email=email, nome=nome, cognome="Branda")
    session.add(row)
    session.commit()
    return row


def company(
    session: Session, email: str, *, stato: str = "nuovo", deleted: bool = False
) -> Company:
    user = session.query(User).filter(User.email == email).one_or_none()
    if user is None:
        user = User(email=email, nome="Ciro", cognome="Aurelio")
        session.add(user)
        session.flush()
    row = Company(
        user_id=user.id,
        nome_azienda="Block Buy SRL",
        figura_richiesta="Developer",
        progetto="ASP.NET Core e React",
        periodo_da=date(2026, 10, 1),
        durata="12 mesi",
        budget_giornaliero=Decimal("320"),
        remoto="remoto",
        numero_risorse=1,
        stato=stato,
    )
    if deleted:
        row.deleted_at = datetime.now(UTC)
    session.add(row)
    session.commit()
    return row


def emails(candidates: list[Candidate]) -> list[str]:
    return [c.email for c in candidates]
