"""What the Talenti tests share (HTEST-10): a freelancer through the wizard, a sign-up, an
admin's draft and the anonymous card written beside a person. `test_talenti.py` and
`test_talenti_card_filters.py` read from here.
"""

from decimal import Decimal
from uuid import UUID

from admins import an_admin
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_core.freelancer_schemas import FreelancerCreate, FreelancerDraft
from rebase_core.freelancers import FreelancerService
from rebase_core.models import FreelancerCard
from rebase_core.service import SignupService
from rebase_core.signup_schemas import SignupCreate, SignupUtm

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _application(email: str = "ada@studio.it", **extra: object) -> FreelancerCreate:
    payload: dict[str, object] = {
        "nome": "Ada",
        "cognome": "Lovelace",
        "email": email,
        "tariffa_giornaliera": Decimal("450.00"),
        "posizione": "Backend developer",
        "remoto": "remoto",
        "links": ["https://github.com/ada"],
    }
    payload.update(extra)
    return FreelancerCreate(**payload)  # type: ignore[arg-type]


def _signup(session: Session, email: str = "lead@studio.it", **extra: object) -> UUID:
    payload: dict[str, object] = {"email": email, "nome": "Nuovo", "cognome": "Arrivato"}
    payload.update(extra)
    return SignupService(session).subscribe(SignupCreate(**payload)).id  # type: ignore[arg-type]


def _draft(**extra: object) -> FreelancerDraft:
    payload: dict[str, object] = {
        "nome": "Ada",
        "cognome": "Lovelace",
        "linkedin_url": "https://www.linkedin.com/in/ada",
        "posizione": "Backend developer",
        "links": ["https://github.com/ada"],
        "fonti": ["https://www.linkedin.com/in/ada"],
    }
    payload.update(extra)
    return FreelancerDraft(**payload)  # type: ignore[arg-type]


def _apply(session: Session, email: str, **extra: object) -> UUID:
    row, _ = FreelancerService(session).apply(
        _application(email, **extra), PDF, "cv.pdf", "application/pdf"
    )
    return row.id


def _with_card(session: Session, freelancer_id: UUID, card: object) -> None:
    session.add(FreelancerCard(freelancer_id=freelancer_id, cv_sha256="0" * 64, card=card))
    session.commit()


# ---- REB-798: `origine` is the value the row shows ------------------------------------


def _three_channels(session: Session) -> dict[str, str]:
    """One wizard card (page `home`), one admin-drafted card (page `pigrocrm`) and one
    lead, by address."""
    FreelancerService(session).apply(
        _application("wizard@studio.it", utm=SignupUtm(origine="home")),
        PDF,
        "cv.pdf",
        "application/pdf",
    )
    ricerca = _signup(session, "admin@studio.it")
    FreelancerService(session).draft_from_signup(ricerca, _draft(), an_admin(session), "web")
    session.execute(
        text("UPDATE freelancers SET origine = 'pigrocrm' WHERE compilata_da = 'admin'")
    )
    session.commit()
    _signup(session, "lead@studio.it")
    return {"wizard": "wizard@studio.it", "admin": "admin@studio.it", "form": "lead@studio.it"}


# ---- REB-801: card filters, sort and the cursor ----------------------------------------


def _profile(
    session: Session,
    email: str,
    *,
    rate: str | None,
    seniority: str | None,
    anni: object = None,
    settori: list[str] | None = None,
    lingue: list[str] | None = None,
    nome: str = "Ada",
) -> UUID:
    extra: dict[str, object] = {"nome": nome}
    if rate is not None:
        extra["tariffa_giornaliera"] = Decimal(rate)
    freelancer = _apply(session, email, **extra)
    if rate is None:
        session.execute(
            text("UPDATE freelancers SET tariffa_giornaliera = NULL WHERE id = :id"),
            {"id": freelancer},
        )
        session.commit()
    card: dict[str, object] = {"ruolo": "Dev", "settori": settori or [], "lingue": lingue or []}
    if seniority is not None:
        card["seniority"] = seniority
    if anni is not None:
        card["anni"] = anni
    _with_card(session, freelancer, card)
    return freelancer


def _emails(listed: object) -> list[str]:
    return [item.email for item in listed.items]  # type: ignore[attr-defined]
