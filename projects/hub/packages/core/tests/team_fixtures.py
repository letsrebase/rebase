"""What the team builder's tests share (HTEST-10): the canned proposals Claude answers with,
the freelancers and cards written straight into the tables, the builder over a fake model
and the proposals it wrote. `test_team_builder.py`, `test_team_catalogue.py`,
`test_team_proposals.py` and `test_team_public_read.py` read from here.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fakes_cards import CARD, MODEL
from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.analytics import Tracker
from rebase_core.config import Settings
from rebase_core.llm import (
    LlmResponse,
)
from rebase_core.models import Freelancer, FreelancerCard, TeamProposal, User
from rebase_core.team_builder import (
    TeamBuilder,
)
from rebase_core.team_catalogue import catalogue_lines
from rebase_core.team_prompt import ProposalAnswer

NOW = datetime(2026, 9, 26, 9, 30, tzinfo=UTC)


DESCRIZIONE = (
    "Acme Logistica rifà il gestionale degli ordini: un backend in Python con FastAPI, "
    "un frontend in React, sei mesi di lavoro, da remoto."
)


RIASSUNTO = (
    "Un'azienda di logistica rifà il gestionale degli ordini: backend Python e frontend "
    "React, sei mesi da remoto."
)


NO_CARD = object()


def make_settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


SETTINGS = make_settings()


class FakeCapture:
    def __init__(self, raises: bool = False) -> None:
        self.raises = raises
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, event: str, *, distinct_id: str, properties: dict[str, Any]) -> None:
        if self.raises:
            raise RuntimeError("la rete non c'e'")
        self.calls.append((event, distinct_id, properties))


def team_member(
    position: str,
    ruolo: str = "Backend developer",
    motivazione: str = "Nove anni di API in Python e FastAPI, quello che serve al gestionale.",
    giorni: int | None = 5,
) -> dict[str, Any]:
    return {"id": position, "ruolo": ruolo, "motivazione": motivazione, "giorni_settimana": giorni}


def proposal_response(
    team: list[dict[str, Any]] | None = None,
    *,
    riassunto: str = RIASSUNTO,
    locale: bool = False,
    dove: str | None = None,
    text: str | None = None,
    stop_reason: str = "end_turn",
    refusal_category: str | None = None,
) -> LlmResponse:
    body = {
        "riassunto": riassunto,
        "luogo": {"locale": locale, "dove": dove},
        "team": team if team is not None else [],
    }
    answer = None if stop_reason == "refusal" else (text if text is not None else json.dumps(body))
    try:
        parsed = ProposalAnswer.model_validate_json(answer) if answer is not None else None
    except ValueError:  # as `AiCall`: a body that is not the shape has no `parsed`
        parsed = None
    return LlmResponse(
        text=answer,
        parsed=parsed,
        stop_reason=stop_reason,
        refusal_category=refusal_category,
        model=MODEL,
        input_tokens=5200,
        output_tokens=640,
        cache_read_tokens=4800,
    )


def a_talent(
    session: Session,
    n: int,
    *,
    card: Any = NO_CARD,
    remoto: str | None = "remoto",
    tariffa: Decimal | None = Decimal("450.00"),
    stato: str = "nuovo",
    deleted: bool = False,
    freelancer_id: UUID | None = None,
) -> UUID:
    """A freelancer with a name, a link and, unless `card` says otherwise, the card of
    `fakes_cards`: everything the catalogue must leave out sits in the row beside it."""
    user = User(email=f"talento{n}@studio.it", nome="Ada", cognome=f"Lovelace{n}")
    session.add(user)
    session.flush()
    row = Freelancer(
        user_id=user.id,
        remoto=remoto,
        tariffa_giornaliera=tariffa,
        stato=stato,
        deleted_at=NOW if deleted else None,
        links=[f"https://github.com/ada{n}"],
        posizione="Backend developer",
    )
    if freelancer_id is not None:
        row.id = freelancer_id
    session.add(row)
    session.flush()
    if card is not None:
        session.add(
            FreelancerCard(
                freelancer_id=row.id,
                cv_sha256="0" * 64,
                card=CARD if card is NO_CARD else card,
                model=MODEL,
                input_tokens=1200,
                output_tokens=180,
                generated_at=NOW,
            )
        )
    session.commit()
    return row.id


def a_user(session: Session, email: str) -> UUID:
    user = User(email=email, nome="Referente", cognome="Azienda")
    session.add(user)
    session.commit()
    return user.id


def catalogue_positions(session: Session) -> dict[UUID, str]:
    _, positions = catalogue_lines(session)
    return {freelancer_id: f"t{index}" for index, freelancer_id in enumerate(positions, start=1)}


def make_builder(
    session: Session,
    llm: Any,
    *,
    settings: Settings = SETTINGS,
    tracker: Tracker | None = None,
    now: datetime = NOW,
) -> TeamBuilder:
    return TeamBuilder(session, llm, settings, tracker=tracker, now=lambda: now)


def proposal_rows(session: Session) -> list[TeamProposal]:
    # `id` breaks the tie on `created_at`: the builder stamps every row with the clock it
    # is handed, by default the fixed `NOW`, so on `created_at` alone the order is the plan's.
    # Ids are UUIDv7, so insertion order holds (REB-661).
    session.expire_all()
    return list(
        session.scalars(select(TeamProposal).order_by(TeamProposal.created_at, TeamProposal.id))
    )
