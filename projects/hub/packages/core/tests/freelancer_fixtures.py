"""The freelancers, card writers and database fixtures the card tests share (HTEST-09).

Each of `test_cards.py`, `test_card_persona.py`, `test_card_provenance.py`,
`test_card_staleness.py` and the batch and pool tests used to carry its own copy of the
application that makes a card's person, the writer over a fake model, the switch that
turns the sources on and the fixture that leaves the database empty. They are here once;
a file that needs another default wraps `apply_freelancer`, it does not rewrite it.
"""

import hashlib
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fakes_cards import CARD, card_response, text_pdf
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rebase_core.card_writer import CardWriter
from rebase_core.freelancer_schemas import FreelancerCreate
from rebase_core.freelancers import FreelancerService
from rebase_core.llm import (
    LlmMessage,
    LlmRequest,
    LlmUnavailable,
    RecordingCall,
    _parameters,
)
from rebase_core.models import FreelancerCard, User, UserContactPoint
from rebase_core.sources import SourceService
from rebase_core.team_schemas import Card

CV = text_pdf("Ada Lovelace, backend developer a Torino da nove anni: Python, FastAPI, AWS.")
CV_2026 = text_pdf("Ada Lovelace, backend lead a Torino: Python, Kubernetes, dieci anni.")
# A header on nothing: `extract_text` reads no page and no text, which is a scan's answer.
SCAN = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
# What a card is failed with when it names the person or an address.
IDENTIFYING = "La scheda cita la persona o un indirizzo."
# A card that gives the person's gender away ("sviluppatrice"): the guard asks again once.
SVILUPPATRICE: dict[str, Any] = {
    **CARD,
    "role": "Sviluppatrice backend",
    "profile_summary": "Specializzata in API Python e infrastruttura AWS per il fintech.",
}
LINKS = ["https://github.com/adadev", "https://ada.example/portfolio"]
# The moment `written_cards` writes its cards at, when the caller gives none.
CARDS_NOW = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)


def card_logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """`caplog` on the card writer's loggers. Ask for it after `clean_hub`, on purpose: the
    database fixture runs Alembic's `env.py`, whose `fileConfig` disables every logger
    `alembic.ini` does not list, these among them (the trap `test_llm.py` documents)."""
    for name in ("rebase_core.card_writer", "rebase_core.card_store"):
        logging.getLogger(name).disabled = False
        caplog.set_level(logging.INFO, logger=name)
    return caplog


def llm_down() -> LlmUnavailable:
    """The seam failing the way `AiCall` does on an outage or a run of 429s."""
    return LlmUnavailable("Non riesco a proporre un team adesso: riprova tra poco.")


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def apply_freelancer(
    session: Session,
    email: str = "ada@studio.it",
    cv: bytes | None = CV,
    last_name: str = "Lovelace",
    first_name: str = "Ada",
    *,
    links: list[str] | None = None,
    phone: str | None = None,
) -> UUID:
    """A freelancer through the wizard's own service, with a CV (`None`: without one) and
    no links unless given; `phone` is written on the person afterwards."""
    data = FreelancerCreate(
        first_name=first_name,
        last_name=last_name,
        email=email,
        daily_rate=Decimal("450.00"),
        job_title="Backend developer",
        remote="remote",
        links=links or [],
    )
    service = FreelancerService(session)
    read, _ = service.apply(data) if cv is None else service.apply(data, cv, "cv.pdf", "")
    if phone is not None:
        user_id = session.scalar(select(User.id).where(User.email == email))
        give_phone(session, user_id, phone)
    return read.id


def give_phone(session: Session, user_id: UUID | None, phone: str) -> None:
    """The person's primary phone point, the way a request or an admin writes it."""
    session.add(
        UserContactPoint(user_id=user_id, kind="phone", value=phone, label="work", is_primary=True)
    )
    session.commit()


def apply_numbered(session: Session, n: int = 1, links: list[str] | None = None) -> UUID:
    """The `n`-th person of a crowd: a name, an address and a CV of their own, so that
    each card's prompt says whose it is."""
    return apply_freelancer(
        session,
        f"persona{n}@studio.it",
        text_pdf(f"Persona{n}, backend developer da nove anni: Python, FastAPI."),
        f"Cognome{n}",
        f"Persona{n}",
        links=links,
    )


def enable_sources(session: Session, *kinds: str) -> None:
    """Turns the named sources on (`ai_sources` is seeded off, and `clean` switches it off
    again)."""
    for kind in kinds:
        session.execute(text("UPDATE ai_sources SET enabled = true WHERE kind = :k"), {"k": kind})
    session.commit()


def card_writer(
    session: Session, llm: object, sources: SourceService | None = None, *, now: datetime
) -> CardWriter:
    return CardWriter(session, llm, now=lambda: now, sources=sources)  # type: ignore[arg-type]


def written_cards(session: Session, n: int = 3, *, now: datetime = CARDS_NOW) -> list[UUID]:
    """`n` numbered people, each with the card a canned answer wrote."""
    people = [apply_numbered(session, i) for i in range(1, n + 1)]
    for person in people:
        card_writer(session, RecordingCall([card_response()]), now=now).write(person, force=True)
    return people


def backdate_prompt(session: Session, version: int | None) -> None:
    """Every stored card says it was written by prompt `version`."""
    session.execute(text("UPDATE freelancer_cards SET prompt_version = :v"), {"v": version})
    session.commit()


def stored_card(session: Session, freelancer_id: UUID) -> FreelancerCard | None:
    return session.get(FreelancerCard, freelancer_id, populate_existing=True)


def card_is_sql_null(session: Session, freelancer_id: UUID) -> bool:
    """The column as SQL sees it: JSON `null` reads back as `None` in Python, but it is
    `IS NOT NULL` to the catalogue's filter, which is what a retired card must not be."""
    return bool(
        session.execute(
            text("SELECT card IS NULL FROM freelancer_cards WHERE freelancer_id = :id"),
            {"id": freelancer_id},
        ).scalar_one()
    )


def user_text(request: LlmRequest) -> str:
    return "\n".join(message.text for message in request.messages)


def card_schema() -> dict[str, Any]:
    """The schema `Card` goes out with, as the seam sends it (it never changes: the
    private answer of version 2 wraps it)."""
    request = LlmRequest(system="", messages=[LlmMessage(role="user", text="x")], output_model=Card)
    schema = _parameters(request).output_object
    assert schema is not None
    return schema.json_schema
